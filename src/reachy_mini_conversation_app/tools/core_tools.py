import abc
import sys
import json
import asyncio
import inspect
import logging
import importlib
import threading
import importlib.util
from types import ModuleType
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Callable, ClassVar, Sequence, Awaitable, TypedDict
from pathlib import Path
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from reachy_mini import ReachyMini
from reachy_mini_conversation_app.tools import skills
from reachy_mini_conversation_app.config import config, list_tool_module_names
from reachy_mini_conversation_app.mcp_client import McpToolTimeoutError, McpToolInvocationError
from reachy_mini_conversation_app.tool_spaces import build_remote_client, read_installed_tool_spaces
from reachy_mini_conversation_app.profile_store import DEFAULT_PROFILE_NAME
from reachy_mini_conversation_app.profile_toolsets import read_profile_tool_names, read_profile_tool_override
from reachy_mini_conversation_app.tools.tool_constants import SystemTool


if TYPE_CHECKING:
    from reachy_mini_conversation_app.mcp_client import RemoteMcpToolClient
    from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)


class MissingToolFileError(FileNotFoundError):
    """Raised when a requested tool file is absent on disk."""


@dataclass
class ToolDependencies:
    """External dependencies injected into tools."""

    reachy_mini: ReachyMini
    movement_manager: Any  # MovementManager from moves.py
    # Optional deps
    instance_path: str | Path | None = None
    on_memory_forgotten: Callable[[str], None] | None = None
    memory_mutation_generation: str | None = None
    camera_enabled: bool = False
    motion_duration_s: float = 1.0
    # Sleep pose, then stop the app (also the shutdown path).
    go_to_sleep: Callable[[], dict[str, Any]] | None = None
    # Hybrid runtime only: sleep pose and back to waiting for the wake word, with the app still running.
    standby: Callable[[], dict[str, Any]] | None = None
    # Hybrid runtime only: close the conversation after Reachy's goodbye and wait for the wake word.
    end_conversation: Callable[[], dict[str, Any]] | None = None
    face_recognition: Any = None  # FaceRecognitionService from face_identity.py, when enabled
    home_hub: Any = None  # HomeDevices from home_devices.py, when lights are set up
    tv: Any = None  # AndroidTV from android_tv.py, when a TV is paired
    display: Any = None  # EInkDisplay from eink_display.py, when TRMNL_ENABLED
    now_playing: Any = None  # NowPlaying from now_playing.py, when the display shows Spotify songs
    # Recognized person in front of the camera (household person ID), when face recognition is enabled.
    current_person_id: Callable[[], str | None] | None = None
    maribel: Any = None  # MaribelLink from maribel.py, when Maribel (Grok Bot) is configured
    whatsapp: Any = None  # WhatsAppLink from whatsapp.py, when photos go to a WhatsApp group
    # Hybrid runtime only: beep, then record a voice message until the speaker stops (rate, mono int16) or None.
    # Records a voice message; the argument counts touches on his head, which end it.
    record_message: Callable[[Callable[[], int] | None], Awaitable[tuple[int, NDArray[np.int16]] | None]] | None = None
    # During a quick command: stream a WhatsApp call (whatsapp_calls.CallLink) through Reachy (stream_call).
    stream_call: Callable[[Any], Awaitable[bool]] | None = None
    # Close the conversation after Reachy's reply, then handle this request locally (HybridRuntime.request_after_close).
    request_after_close: Callable[[str], bool] | None = None
    # The local WhatsApp call service (whatsapp_calls.CallManager), when its token is on this computer.
    calls: Any = None
    # Hybrid runtime only: after the current quick reply, listen once without the name and pass what is said on.
    expect_answer: Callable[[Callable[[str], Awaitable[Any]]], None] | None = None
    # Hybrid runtime with the e-ink screen only: writing practice, one letter at a time (writing_mode.py).
    writing: Any = None
    # Hybrid runtime only: the game host (games/host.py), which owns requests, screen and speaker during a game.
    games: Any = None


class ToolSpec(TypedDict):
    """Function-calling spec for a tool, in the OpenAI-compatible shape."""

    type: Literal["function"]
    name: str
    description: str
    parameters: dict[str, Any]  # arbitrary JSON Schema


class Tool(abc.ABC):
    """Base abstraction for tools used in function-calling.

    Each tool must define:
      - name: str
      - description: str
      - parameters_schema: Dict[str, Any]  # JSON Schema

    Tools may override:
      - needs_response: bool = True  # set False to skip the spoken follow-up after this tool runs
    """

    _auto_register: ClassVar[bool] = True
    needs_response: ClassVar[bool] = True

    name: str
    description: str
    parameters_schema: Dict[str, Any]

    def spec(self) -> ToolSpec:
        """Return the function spec for LLM consumption."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    @abc.abstractmethod
    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Async tool execution entrypoint."""
        raise NotImplementedError


ALL_TOOLS: Dict[str, Tool] = {}
_TOOLS_SIGNATURE: tuple[str, str, str | None, bool, str | None, bool, bool, bool, bool, bool, bool, bool] | None = None
_TOOLS_INSTANCE_PATH: str | Path | None = None
_LOADED_TOOL_CLASS_CACHE: Dict[tuple[str, str], List[type[Tool]]] = {}
_REMOTE_TOOL_RETRY_DELAY_S = 0.25
_TOOLS_LOCK = threading.RLock()
_EXTERNAL_TOOL_MODULE_NAMESPACE = "reachy_mini_conversation_app._external_tools"


class RemoteMcpTool(Tool):
    """Adapter exposing one remote MCP tool through the local Tool interface."""

    _auto_register: ClassVar[bool] = False

    def __init__(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        client_tool_name: str,
        client: "RemoteMcpToolClient",
    ) -> None:
        """Store the resolved local/remote names and the shared MCP client."""
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self._space_slug = slug
        self._client_tool_name = client_tool_name
        self._client = client
        self._registry_source = f"space:{slug}:{client_tool_name}"

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Invoke the underlying remote MCP tool."""
        try:
            result = await self._client.call_tool(self._client_tool_name, kwargs)
        except McpToolTimeoutError:
            # Timeout subclasses the retryable error, but retrying it would just double the wait.
            raise
        except McpToolInvocationError as exc:
            logger.warning("Remote MCP tool failed once; retrying %s from %s: %s", self.name, self._space_slug, exc)
            await asyncio.sleep(_REMOTE_TOOL_RETRY_DELAY_S)
            result = await self._client.call_tool(self._client_tool_name, kwargs)
        payload = dict(result)
        if payload.get("namespaced_tool_name") == self._client_tool_name:
            payload["namespaced_tool_name"] = self.name
        payload.setdefault("tool_space_slug", self._space_slug)
        return payload


_LOADED_REMOTE_TOOL_CACHE: Dict[tuple[str, str], RemoteMcpTool] = {}


def _load_module_from_file(module_name: str, file_path: Path) -> ModuleType:
    """Load a Python module from a file path."""
    if not file_path.is_file():
        raise MissingToolFileError(f"tool file not found at {file_path}")

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if not (spec and spec.loader):
        raise ModuleNotFoundError(f"Cannot create spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Avoid leaving a partially initialised module registered on failure
        sys.modules.pop(module_name, None)
        raise
    return module


def _format_error(error: Exception) -> str:
    """Format an exception for logging."""
    if isinstance(error, FileNotFoundError):
        return f"Tool file not found: {error}"
    if isinstance(error, ModuleNotFoundError):
        return f"Missing dependency: {error}"
    if isinstance(error, ImportError):
        return f"Import error: {error}"
    return f"{type(error).__name__}: {error}"


def _normalize_signature_path(value: str | Path | None) -> str | None:
    """Normalize a path-like value for registry invalidation and cache keys."""
    if value is None:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return str(value)


def _tool_classes_from_module(module: ModuleType) -> List[type[Tool]]:
    """Return auto-registerable Tool classes defined directly in module."""
    tool_classes: List[type[Tool]] = []
    seen_class_ids: set[int] = set()
    for value in vars(module).values():
        if not inspect.isclass(value) or value.__module__ != module.__name__:
            continue
        try:
            is_tool_class = issubclass(value, Tool)
        except TypeError:
            continue
        if value is Tool or not is_tool_class or inspect.isabstract(value) or not value._auto_register:
            continue
        cls_id = id(value)
        if cls_id in seen_class_ids:
            continue
        seen_class_ids.add(cls_id)
        tool_classes.append(value)
    return tool_classes


def _load_cached_tool_classes(
    cache_key: tuple[str, str],
    load_module: Callable[[], ModuleType],
) -> tuple[List[type[Tool]], bool]:
    """Load tool classes once per source and return whether the cache was reused."""
    cached_classes = _LOADED_TOOL_CLASS_CACHE.get(cache_key)
    if cached_classes is not None:
        return cached_classes, True

    module = load_module()
    tool_classes = _tool_classes_from_module(module)
    _LOADED_TOOL_CLASS_CACHE[cache_key] = tool_classes
    return tool_classes, False


def _try_load_tool_classes(
    tool_name: str,
    module_path: str,
    fallback_directory: Path | None,
    file_subpath: str,
) -> tuple[str, List[type[Tool]], bool]:
    """Try to load tool classes: first via importlib, then from a configured external file."""
    try:
        return (
            "module",
            *_load_cached_tool_classes(
                ("module", module_path),
                lambda: importlib.import_module(module_path),
            ),
        )
    except ModuleNotFoundError:
        if fallback_directory is None:
            raise
        tool_file = fallback_directory / file_subpath
        return (
            "file",
            *_load_cached_tool_classes(
                ("file", _normalize_signature_path(tool_file) or str(tool_file)),
                lambda: _load_module_from_file(f"{_EXTERNAL_TOOL_MODULE_NAMESPACE}.{tool_name}", tool_file),
            ),
        )


def _build_tool_registry(
    tool_classes: List[type[Tool]],
    extra_tools: Sequence[Tool] | None = None,
) -> Dict[str, Tool]:
    """Instantiate tools and fail if duplicate Tool.name values are detected."""
    unique_classes: List[type[Tool]] = []
    seen_class_ids: set[int] = set()
    for cls in tool_classes:
        cls_id = id(cls)
        if cls_id in seen_class_ids:
            continue
        seen_class_ids.add(cls_id)
        unique_classes.append(cls)

    tool_instances: list[Tool] = []
    tool_instances.extend(cls() for cls in unique_classes)
    if extra_tools:
        tool_instances.extend(extra_tools)

    name_to_sources: Dict[str, List[str]] = {}
    for tool in tool_instances:
        source = getattr(tool, "_registry_source", f"{tool.__class__.__module__}.{tool.__class__.__name__}")
        name_to_sources.setdefault(tool.name, []).append(source)

    collisions = {tool_name: sources for tool_name, sources in name_to_sources.items() if len(sources) > 1}
    if collisions:
        details = "; ".join(f"{tool_name}: {sources}" for tool_name, sources in sorted(collisions.items()))
        raise RuntimeError(
            f"Duplicate Tool.name values detected while loading tools. Tool.name must be unique. Conflicts: {details}"
        )

    return {tool.name: tool for tool in tool_instances}


def _tool_registry_signature(
    instance_path: str | Path | None,
) -> tuple[str, str, str | None, bool, str | None, bool, bool, bool, bool, bool, bool, bool]:
    """Return the runtime inputs that determine the active tool registry."""
    return (
        config.REACHY_MINI_CUSTOM_PROFILE or "default",
        _normalize_signature_path(config.PROFILES_DIRECTORY) or "",
        _normalize_signature_path(config.TOOLS_DIRECTORY),
        bool(config.AUTOLOAD_EXTERNAL_TOOLS),
        _normalize_signature_path(instance_path),
        config.HOUSEHOLD_MEMORY_ENABLED,
        bool(config.HYBRID_RUNTIME_ENABLED),
        _lights_configured(),
        _tv_configured(),
        config.TRMNL_ENABLED,
        maribel_configured(),
        whatsapp_photos_configured(),
    )


# Enabled automatically with FACE_RECOGNITION_ENABLED unless the profile's tools were customized.
FACE_TOOLS = ("remember_face", "forget_face", "who_is_here")
# With the hybrid runtime, go_to_sleep means standby; these end a conversation or turn the app off.
# Added next to go_to_sleep unless the profile's tools were customized.
STANDBY_TOOLS = ("end_conversation", "turn_off")
# Enabled automatically once an IKEA hub is paired or Tuya devices have local keys, unless customized.
LIGHT_TOOLS = ("home_lights",)


def _lights_configured() -> bool:
    return bool(config.DIRIGERA_HOST and config.DIRIGERA_TOKEN) or Path(config.TUYA_DEVICES_FILE).is_file()


# Enabled automatically once a TV is paired, unless the profile's tools were customized.
TV_TOOLS = ("tv",)


def _tv_configured() -> bool:
    from reachy_mini_conversation_app.android_tv import is_paired

    return bool(config.ANDROID_TV_HOST) and is_paired(Path(config.ANDROID_TV_CERT_DIR))


# Enabled automatically with TRMNL_ENABLED, unless the profile's tools were customized.
DISPLAY_TOOLS = ("show_on_display",)


# Enabled automatically once Maribel's webhook and Reachy's callback are configured, unless customized.
MARIBEL_TOOLS = ("ask_maribel",)
WHATSAPP_TOOLS = ("take_photo",)
CALL_TOOLS = ("call_person",)
# Enabled automatically with the hybrid runtime and a TypeSafe key (the color hunt's camera checks), unless customized.
GAME_TOOLS = ("play_game",)


def games_configured() -> bool:
    """Return whether games can run: they play locally in the hybrid runtime, judged by Jev."""
    return bool(config.HYBRID_RUNTIME_ENABLED and config.QUICK_COMMANDS_ENABLED and config.TYPESAFE_API_KEY)


def whatsapp_calls_configured() -> bool:
    """Return whether the call service's token and approved list are both on this computer."""
    return Path(config.WHATSAPP_CALLS_TOKEN_FILE).is_file() and Path(config.WHATSAPP_CALLS_APPROVED_FILE).is_file()


def whatsapp_photos_configured() -> bool:
    """Return whether the local WhatsApp server and the photos group are configured."""
    return bool(config.WHATSAPP_API_URL and config.WHATSAPP_API_AUTH and config.WHATSAPP_PHOTOS_CHAT)


def maribel_configured() -> bool:
    """Return whether Maribel's webhook and Reachy's callback are both configured."""
    return bool(
        config.MARIBEL_WEBHOOK_URL
        and config.MARIBEL_WEBHOOK_KEY
        and config.MARIBEL_CALLBACK_URL
        and config.REACHY_CALLBACK_SECRET
    )


# Registry & specs (dynamic)
def _read_profile_tool_names(instance_path: str | Path | None) -> list[str]:
    """Read enabled tool names from the active profile's effective toolset."""
    profile = config.REACHY_MINI_CUSTOM_PROFILE or DEFAULT_PROFILE_NAME
    logger.info("Loading tools for profile: %s", profile)
    try:
        tool_names = read_profile_tool_names(profile, instance_path)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Failed to read tools for profile %r: %s", profile, exc)
        raise RuntimeError(f"Failed to read tools for profile {profile!r}") from exc

    tool_names.extend(tool.value for tool in SystemTool if tool.value not in tool_names)

    if config.AUTOLOAD_EXTERNAL_TOOLS:
        discovered_external_tools = list_tool_module_names(config.TOOLS_DIRECTORY)
        extra_tools = [name for name in discovered_external_tools if name not in tool_names]
        if extra_tools:
            tool_names.extend(extra_tools)
            logger.info(
                "AUTOLOAD_EXTERNAL_TOOLS enabled: added %d external tool(s): %s",
                len(extra_tools),
                extra_tools,
            )

    if not config.FACE_RECOGNITION_ENABLED:
        tool_names = [name for name in tool_names if name not in FACE_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in FACE_TOOLS if name not in tool_names)

    if not config.HYBRID_RUNTIME_ENABLED:
        tool_names = [name for name in tool_names if name != "end_conversation"]
    elif "go_to_sleep" in tool_names and read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in STANDBY_TOOLS if name not in tool_names)

    if not _lights_configured():
        tool_names = [name for name in tool_names if name not in LIGHT_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in LIGHT_TOOLS if name not in tool_names)

    if not _tv_configured():
        tool_names = [name for name in tool_names if name not in TV_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in TV_TOOLS if name not in tool_names)

    if not config.TRMNL_ENABLED:
        tool_names = [name for name in tool_names if name not in DISPLAY_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in DISPLAY_TOOLS if name not in tool_names)

    if not maribel_configured():
        tool_names = [name for name in tool_names if name not in MARIBEL_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in MARIBEL_TOOLS if name not in tool_names)

    if not whatsapp_photos_configured():
        tool_names = [name for name in tool_names if name not in WHATSAPP_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in WHATSAPP_TOOLS if name not in tool_names)

    if not (config.HYBRID_RUNTIME_ENABLED and whatsapp_calls_configured()):
        tool_names = [name for name in tool_names if name not in CALL_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in CALL_TOOLS if name not in tool_names)

    if not games_configured():
        tool_names = [name for name in tool_names if name not in GAME_TOOLS]
    elif read_profile_tool_override(profile, instance_path) is None:
        tool_names.extend(name for name in GAME_TOOLS if name not in tool_names)

    if not config.HOUSEHOLD_MEMORY_ENABLED:
        tool_names = [name for name in tool_names if name != "update_memory"]
    elif (
        "remember" in tool_names
        and "forget" in tool_names
        and "update_memory" not in tool_names
        and read_profile_tool_override(profile, instance_path) is None
    ):
        tool_names.append("update_memory")

    logger.info("Found %d tools to load: %s", len(tool_names), tool_names)
    return tool_names


def _resolve_remote_tools(tool_names: list[str], instance_path: str | Path | None) -> list[RemoteMcpTool]:
    """Build Space tools enabled by the active profile from the cached install manifest, without any network calls."""
    remote_tools: list[RemoteMcpTool] = []
    for installed_space in read_installed_tool_spaces(instance_path).spaces:
        enabled_tool_names = {name for name in tool_names if name.startswith(f"{installed_space.alias}__")}
        if not enabled_tool_names:
            continue

        discovered_tool_names = {tool.local_name for tool in installed_space.tools}
        missing_tool_names = sorted(enabled_tool_names - discovered_tool_names)
        if missing_tool_names:
            logger.warning(
                "Tools enabled from '%s' are missing from the install manifest and will be skipped: %s. "
                "Re-run 'tool-spaces add %s' to refresh.",
                installed_space.slug,
                ", ".join(missing_tool_names),
                installed_space.slug,
            )

        client = build_remote_client(
            installed_space.alias,
            installed_space.mcp_url,
            private=installed_space.private,
            cached_tools=installed_space.tools,
        )
        for remote_tool in installed_space.tools:
            if remote_tool.local_name not in enabled_tool_names:
                continue
            cache_key = ("remote", f"{installed_space.slug}:{remote_tool.local_name}:{remote_tool.client_tool_name}")
            cached_tool = _LOADED_REMOTE_TOOL_CACHE.get(cache_key)
            if cached_tool is None:
                cached_tool = RemoteMcpTool(
                    slug=installed_space.slug,
                    name=remote_tool.local_name,
                    description=remote_tool.description,
                    parameters_schema=remote_tool.parameters_schema,
                    client_tool_name=remote_tool.client_tool_name,
                    client=client,
                )
                _LOADED_REMOTE_TOOL_CACHE[cache_key] = cached_tool
            remote_tools.append(cached_tool)

    return remote_tools


def _load_enabled_tools(tool_names: list[str], remote_tool_names: set[str]) -> List[type[Tool]]:
    """Load shared and external tools while skipping resolved remote tool IDs."""
    loaded_tool_classes: List[type[Tool]] = []

    for tool_name in tool_names:
        if tool_name in remote_tool_names:
            logger.info("✓ Registered remote tool: %s", tool_name)
            continue

        shared_module_path = f"reachy_mini_conversation_app.tools.{tool_name}"
        try:
            source, tool_classes, reused_cache = _try_load_tool_classes(
                tool_name,
                module_path=shared_module_path,
                fallback_directory=config.TOOLS_DIRECTORY,
                file_subpath=f"{tool_name}.py",
            )
            loaded_tool_classes.extend(tool_classes)
            action = "Reused" if reused_cache else "Loaded"
            if source == "file":
                logger.info("✓ %s external tool: %s", action, tool_name)
            else:
                logger.info("✓ %s core tool: %s", action, tool_name)
        except (ModuleNotFoundError, FileNotFoundError):
            logger.warning("⚠️ Tool '%s' not found in shared or external tools", tool_name)
        except Exception as e:
            logger.error("❌ Failed to load tool '%s': %s", tool_name, _format_error(e))
            logger.error("  Module path: %s", shared_module_path)

    return loaded_tool_classes


def initialize_tools(instance_path: str | Path | None = None, *, force: bool = False) -> None:
    """Populate or refresh the active-profile tool registry.

    When ``force`` is true, file-backed tools are re-executed, while importable
    tool modules still follow normal ``importlib``/``sys.modules`` caching.
    """
    global ALL_TOOLS, _TOOLS_SIGNATURE, _TOOLS_INSTANCE_PATH

    with _TOOLS_LOCK:
        if force:
            _LOADED_TOOL_CLASS_CACHE.clear()
            _LOADED_REMOTE_TOOL_CACHE.clear()

        if instance_path is not None:
            _TOOLS_INSTANCE_PATH = instance_path
        effective_instance_path = _TOOLS_INSTANCE_PATH
        signature = _tool_registry_signature(effective_instance_path)

        if _TOOLS_SIGNATURE is not None and not force and signature == _TOOLS_SIGNATURE:
            logger.debug("Tools already initialized for active profile; skipping reinitialization.")
            return
        if _TOOLS_SIGNATURE is not None:
            logger.info("Reloading tool registry for active profile/configuration change.")

        tool_names = _read_profile_tool_names(effective_instance_path)
        remote_tools = _resolve_remote_tools(tool_names, effective_instance_path)
        remote_tool_names = {tool.name for tool in remote_tools}
        loaded_tool_classes = _load_enabled_tools(tool_names, remote_tool_names)
        tools = _build_tool_registry(
            loaded_tool_classes,
            extra_tools=remote_tools,
        )
        ALL_TOOLS = tools
        _TOOLS_SIGNATURE = signature

        for tool_name, tool in tools.items():
            logger.info("tool registered: %s - %s", tool_name, tool.description)


def get_tool_specs(exclusion_list: list[str] | None = None) -> list[ToolSpec]:
    """Get the tool specs to offer the model, optionally excluding some tools.

    With SKILL_TOOLS_ENABLED, rarely used tools are folded into ``use_skill`` and ``skill_help``.
    """
    initialize_tools()
    exclusion_list = exclusion_list or []
    with _TOOLS_LOCK:
        specs = [tool.spec() for tool in ALL_TOOLS.values() if tool.name not in exclusion_list]
    return skills.fold_into_skills(specs) if config.SKILL_TOOLS_ENABLED else specs


def get_tools() -> dict[str, Tool]:
    """Return a shallow snapshot of the active tool registry."""
    initialize_tools()
    with _TOOLS_LOCK:
        return dict(ALL_TOOLS)


# Dispatcher
def _safe_load_obj(args_json: str) -> Dict[str, Any]:
    try:
        parsed_args = json.loads(args_json or "{}")
        return parsed_args if isinstance(parsed_args, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Tool arguments are not valid JSON")
        return {}


async def _dispatch_tool_call(tool_name: str, args: Dict[str, Any], deps: ToolDependencies) -> Dict[str, Any]:
    if tool_name == skills.SKILL_HELP:
        return skills.skill_help(get_tools(), args)
    if tool_name == skills.USE_SKILL:
        skill_tool, arguments = skills.check_arguments(get_tools(), args)
        if skill_tool is None:
            return arguments
        logger.info("Using skill: %s", skill_tool.name)
        tool_name, args = skill_tool.name, arguments
    tool = get_tools().get(tool_name)
    if not tool:
        return {"error": f"unknown tool: {tool_name}"}
    try:
        return await tool(deps, **args)
    except asyncio.CancelledError:
        logger.info("Tool cancelled: %s", tool_name)
        return {"error": "Tool cancelled"}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        logger.exception("Tool error in %s: %s", tool_name, msg)
        return {"error": msg}


async def dispatch_tool_call(tool_name: str, args_json: str, deps: ToolDependencies) -> Dict[str, Any]:
    """Dispatch a tool call by name with JSON args and dependencies."""
    return await _dispatch_tool_call(tool_name, _safe_load_obj(args_json), deps)


async def dispatch_tool_call_with_manager(
    tool_name: str, args_json: str, deps: ToolDependencies, tool_manager: "BackgroundToolManager"
) -> Dict[str, Any]:
    """Dispatch a tool call, injecting a BackgroundToolManager into the args."""
    args = _safe_load_obj(args_json)
    args["tool_manager"] = tool_manager
    return await _dispatch_tool_call(tool_name, args, deps)
