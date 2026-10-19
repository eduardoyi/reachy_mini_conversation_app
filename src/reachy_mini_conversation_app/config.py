import os
import re
import sys
import math
import logging
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qsl, urlunsplit
from importlib.resources import files

from dotenv import find_dotenv, load_dotenv

from .wake_word import DEFAULT_LOCAL_WAKE_WORD


# Locked profile: set to a profile name (e.g., "astronomer") to lock the app
# to that profile and disable all profile switching. Leave as None for normal behavior.
LOCKED_PROFILE: str | None = None
PROJECT_ROOT = Path(__file__).parents[2].resolve()


def _is_source_checkout_root(root: Path) -> bool:
    """Return whether the given root looks like this project's source checkout."""
    return (root / "pyproject.toml").is_file() and (root / "src" / "reachy_mini_conversation_app").is_dir()


def _packaged_profiles_directory() -> Path | None:
    """Return the installed wheel's packaged profiles directory when available."""
    try:
        return Path(str(files("reachy_talk_data").joinpath("profiles")))
    except Exception:
        return None


def _resolve_default_profiles_directory() -> Path:
    """Resolve built-in profiles from source checkout or installed package data."""
    source_profiles = PROJECT_ROOT / "profiles"
    if _is_source_checkout_root(PROJECT_ROOT) and source_profiles.is_dir():
        return source_profiles

    packaged_profiles = _packaged_profiles_directory()
    if packaged_profiles is not None and packaged_profiles.is_dir():
        return packaged_profiles

    return source_profiles


DEFAULT_PROFILES_DIRECTORY = _resolve_default_profiles_directory()

USER_PERSONALITIES_DIRNAME = "user_personalities"
TERMINAL_USER_PERSONALITIES_DIRECTORY = Path("external_content") / USER_PERSONALITIES_DIRNAME

# Qwen3-TTS CustomVoice speaker catalog from the deployed Hugging Face backend.
HF_AVAILABLE_VOICES: list[str] = [
    "Aiden",
    "Ryan",
    "Dylan",
    "Eric",
    "Ono_Anna",
    "Serena",
    "Sohee",
    "Uncle_Fu",
    "Vivian",
]

HF_BACKEND = "huggingface"
GEMINI_BACKEND = "gemini"
OPENAI_LIVE_BACKEND = "openai_live"
REALTIME_PROVIDER_ENV = "REALTIME_PROVIDER"
REALTIME_PROVIDER_HUGGINGFACE = HF_BACKEND
REALTIME_PROVIDER_GEMINI = GEMINI_BACKEND
REALTIME_PROVIDER_OPENAI_LIVE = OPENAI_LIVE_BACKEND
DEFAULT_REALTIME_PROVIDER = REALTIME_PROVIDER_HUGGINGFACE
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_LIVE_MODEL_ENV = "GEMINI_LIVE_MODEL"
GEMINI_VOICE_ENV = "GEMINI_VOICE"
GEMINI_THINKING_LEVEL_ENV = "GEMINI_THINKING_LEVEL"
GEMINI_LIVE_LOCAL_BARGE_IN_ENV = "GEMINI_LIVE_LOCAL_BARGE_IN_ENABLED"
GEMINI_LIVE_VIDEO_ENV = "GEMINI_LIVE_VIDEO_ENABLED"
DEFAULT_GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"
GEMINI_EXTENDED_THINKING_MODEL = "gemini-3.8-live-extended-thinking"
# Gemini 3.8 Live without Extended Thinking: same session features (compression, resumption, live video),
# no thinking level and no interaction status.
GEMINI_38_LIVE_MODEL = "gemini-3.8-live"
GEMINI_CONTEXT_TRIGGER_TOKENS_ENV = "GEMINI_CONTEXT_TRIGGER_TOKENS"
# Google bills the whole context on every turn and by default trims it only at 80% of the 131k window
# (about 105k), so a long conversation got dearer each turn. From this size the oldest turns are dropped
# down to half of it; instructions and tool declarations (about 8k) are always kept.
DEFAULT_GEMINI_CONTEXT_TRIGGER_TOKENS = 32_000
MIN_GEMINI_CONTEXT_TRIGGER_TOKENS = 16_000
DEFAULT_GEMINI_VOICE = "Puck"
DEFAULT_GEMINI_THINKING_LEVEL = "medium"
GEMINI_THINKING_LEVELS: tuple[str, ...] = ("low", "medium", "high")
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_LIVE_MODEL_ENV = "OPENAI_LIVE_MODEL"
OPENAI_LIVE_BACKEND_MODEL_ENV = "OPENAI_LIVE_BACKEND_MODEL"
OPENAI_LIVE_VOICE_ENV = "OPENAI_LIVE_VOICE"
DEFAULT_OPENAI_LIVE_MODEL = "gpt-live-1"
DEFAULT_OPENAI_LIVE_BACKEND_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_LIVE_VOICE = "marin"
OPENAI_LIVE_AVAILABLE_VOICES: tuple[str, ...] = (
    "quartz",
    "ripple",
    "vesper",
    "willow",
    "stone",
    "gleam",
    "meridian",
    "bossa",
    "tempo",
    "beacon",
    "delta",
    "cinder",
    "marin",
)
HYBRID_RUNTIME_ENABLED_ENV = "HYBRID_RUNTIME_ENABLED"
HYBRID_CLOUD_INACTIVITY_SECONDS_ENV = "HYBRID_CLOUD_INACTIVITY_SECONDS"
HYBRID_FOLLOW_UP_SECONDS_ENV = "HYBRID_FOLLOW_UP_SECONDS"
HYBRID_ADDRESSEE_CHECK_ENABLED_ENV = "HYBRID_ADDRESSEE_CHECK_ENABLED"
HYBRID_AMBIENT_BUFFER_SECONDS_ENV = "HYBRID_AMBIENT_BUFFER_SECONDS"
LOCAL_CONTEXT_STRATEGY_ENV = "LOCAL_CONTEXT_STRATEGY"
LOCAL_STT_BASE_URL_ENV = "LOCAL_STT_BASE_URL"
LOCAL_STT_MODEL_ENV = "LOCAL_STT_MODEL"
LOCAL_STT_TIMEOUT_SECONDS_ENV = "LOCAL_STT_TIMEOUT_SECONDS"
LOCAL_STT_LATIN_ONLY_ENV = "LOCAL_STT_LATIN_ONLY"
LOCAL_VAD_RMS_THRESHOLD_ENV = "LOCAL_VAD_RMS_THRESHOLD"
LOCAL_VAD_SILENCE_SECONDS_ENV = "LOCAL_VAD_SILENCE_SECONDS"
LOCAL_ACOUSTIC_WAKE_ENABLED_ENV = "LOCAL_ACOUSTIC_WAKE_ENABLED"
LOCAL_ACOUSTIC_WAKE_MODEL_DIR_ENV = "LOCAL_ACOUSTIC_WAKE_MODEL_DIR"
LOCAL_ACOUSTIC_WAKE_THRESHOLD_ENV = "LOCAL_ACOUSTIC_WAKE_THRESHOLD"
DEFAULT_LOCAL_ACOUSTIC_WAKE_THRESHOLD = 0.5
LOCAL_WAKE_WORD_ENV = "LOCAL_WAKE_WORD"
LOCAL_WAKE_COOLDOWN_SECONDS_ENV = "LOCAL_WAKE_COOLDOWN_SECONDS"
LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV = "LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS"
LOCAL_CONTEXT_WINDOW_SECONDS_ENV = "LOCAL_CONTEXT_WINDOW_SECONDS"
LOCAL_CONTEXT_MAX_CHARS_ENV = "LOCAL_CONTEXT_MAX_CHARS"
LOCAL_TRANSCRIPT_PREVIEW_ENABLED_ENV = "LOCAL_TRANSCRIPT_PREVIEW_ENABLED"
HOUSEHOLD_MEMORY_ENABLED_ENV = "HOUSEHOLD_MEMORY_ENABLED"
HOUSEHOLD_MEMORY_CAPTURE_ENABLED_ENV = "HOUSEHOLD_MEMORY_CAPTURE_ENABLED"
HOUSEHOLD_MEMORY_REVIEW_ENABLED_ENV = "HOUSEHOLD_MEMORY_REVIEW_ENABLED"
AUDIO_OUTPUT_BACKEND_ENV = "AUDIO_OUTPUT_BACKEND"
AUDIO_OUTPUT_BACKEND_SDK = "sdk"
AUDIO_OUTPUT_BACKEND_GSTREAMER = "gstreamer"
DEFAULT_AUDIO_OUTPUT_BACKEND = AUDIO_OUTPUT_BACKEND_SDK
DEFAULT_HYBRID_RUNTIME_ENABLED = False
DEFAULT_HYBRID_CLOUD_INACTIVITY_SECONDS = 60.0
# Like Gemini on Google speakers (about 15 s): how long Reachy keeps listening for a follow-up after he replies.
DEFAULT_HYBRID_FOLLOW_UP_SECONDS = 15.0
DEFAULT_HYBRID_AMBIENT_BUFFER_SECONDS = 20.0
MAX_HYBRID_AMBIENT_BUFFER_SECONDS = 20.0
LOCAL_CONTEXT_CONTINUOUS = "continuous"
LOCAL_CONTEXT_ON_ACTIVATION = "activation"
DEFAULT_LOCAL_CONTEXT_STRATEGY = LOCAL_CONTEXT_CONTINUOUS
DEFAULT_LOCAL_STT_MODEL = "whisper-1"
DEFAULT_LOCAL_STT_TIMEOUT_SECONDS = 5.0
DEFAULT_LOCAL_VAD_RMS_THRESHOLD = 500.0
DEFAULT_LOCAL_VAD_SILENCE_SECONDS = 0.6
DEFAULT_LOCAL_WAKE_COOLDOWN_SECONDS = 2.0
DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS = 5.0
MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS = 15.0
DEFAULT_LOCAL_CONTEXT_WINDOW_SECONDS = 60.0
DEFAULT_LOCAL_CONTEXT_MAX_CHARS = 2_000
DEFAULT_LOCAL_TRANSCRIPT_PREVIEW_ENABLED = False
DEFAULT_HOUSEHOLD_MEMORY_ENABLED = False
DEFAULT_HOUSEHOLD_MEMORY_REVIEW_ENABLED = False
HF_REALTIME_CONNECTION_MODE_ENV = "HF_REALTIME_CONNECTION_MODE"
HF_REALTIME_WS_URL_ENV = "HF_REALTIME_WS_URL"
REALTIME_TRANSCRIPTION_LANGUAGE_ENV = "REALTIME_TRANSCRIPTION_LANGUAGE"
HF_LOCAL_CONNECTION_MODE = "local"
HF_DEPLOYED_CONNECTION_MODE = "deployed"
HF_REALTIME_SESSION_PROXY_URL = "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"


@dataclass(frozen=True)
class HFBackendDefaults:
    """Defaults for the Hugging Face realtime backend."""

    connection_mode: str = HF_DEPLOYED_CONNECTION_MODE
    # App-managed Hugging Face Space proxy. The Space forwards to the current
    # session allocator, so allocator changes do not require app releases.
    # Users who need a custom target should use HF_REALTIME_CONNECTION_MODE=local
    # with HF_REALTIME_WS_URL.
    session_url: str = HF_REALTIME_SESSION_PROXY_URL
    voice: str = "Aiden"
    direct_port: int = 8765


HF_DEFAULTS = HFBackendDefaults()

logger = logging.getLogger(__name__)

# Removed backend selectors kept in stale robot .env files: warn but ignore them.
_OBSOLETE_BACKEND_ENV_NAMES = ("BACKEND_PROVIDER", "MODEL_NAME")


def _warn_on_obsolete_backend_env() -> None:
    """Warn when removed multi-backend selectors are still set; Hugging Face is the only backend."""
    present = [name for name in _OBSOLETE_BACKEND_ENV_NAMES if (os.getenv(name) or "").strip()]
    if present:
        logger.warning(
            "Ignoring obsolete backend environment variable(s): %s. This app now uses the Hugging Face backend only.",
            ", ".join(present),
        )


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag.

    Accepted truthy values: 1, true, yes, on
    Accepted falsy values: 0, false, no, off
    """
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    logger.warning("Invalid boolean value for %s=%r, using default=%s", name, raw, default)
    return default


APP_TIMEOUT_MINUTES_ENV = "REACHY_MINI_APP_TIMEOUT_MINUTES"
DEFAULT_APP_TIMEOUT_MINUTES = 1440.0


def resolve_app_timeout_minutes() -> float | None:
    """Read the app inactivity timeout (minutes) from the environment; None means disabled."""
    raw_value = os.getenv(APP_TIMEOUT_MINUTES_ENV, "").strip()
    if not raw_value:
        return DEFAULT_APP_TIMEOUT_MINUTES
    try:
        timeout_minutes = float(raw_value)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default.", APP_TIMEOUT_MINUTES_ENV, raw_value)
        return DEFAULT_APP_TIMEOUT_MINUTES
    return timeout_minutes if timeout_minutes > 0 else None


def _normalize_hf_connection_mode(value: str | None) -> str | None:
    """Normalize the Hugging Face connection mode, if explicitly configured."""
    candidate = (value or "").strip().lower()
    if not candidate:
        return None

    if candidate not in {HF_LOCAL_CONNECTION_MODE, HF_DEPLOYED_CONNECTION_MODE}:
        logger.warning(
            "Invalid %s=%r. Expected local or deployed.",
            HF_REALTIME_CONNECTION_MODE_ENV,
            value,
        )
        return None
    return candidate


def _normalize_realtime_provider(value: str | None) -> str:
    """Normalize the realtime provider, rejecting invalid cloud selections."""
    candidate = (value or "").strip().lower()
    if not candidate:
        return DEFAULT_REALTIME_PROVIDER

    if candidate == "openai-live":
        candidate = REALTIME_PROVIDER_OPENAI_LIVE

    if candidate not in {
        REALTIME_PROVIDER_HUGGINGFACE,
        REALTIME_PROVIDER_GEMINI,
        REALTIME_PROVIDER_OPENAI_LIVE,
    }:
        raise ValueError(
            f"Invalid {REALTIME_PROVIDER_ENV}={value!r}. Expected "
            f"{REALTIME_PROVIDER_HUGGINGFACE!r}, {REALTIME_PROVIDER_GEMINI!r}, or "
            f"{REALTIME_PROVIDER_OPENAI_LIVE!r}."
        )
    return candidate


def is_gemini_extended_thinking_model(model: str | None = None) -> bool:
    """Return whether a model ID is the supported Extended Thinking model."""
    configured_model = model
    if configured_model is None:
        configured_model = (os.getenv(GEMINI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_GEMINI_LIVE_MODEL
    return configured_model.strip().removeprefix("models/") == GEMINI_EXTENDED_THINKING_MODEL


def is_gemini_38_live_model(model: str | None = None) -> bool:
    """Return whether a model ID is a Gemini 3.8 Live model, with or without Extended Thinking."""
    configured_model = model
    if configured_model is None:
        configured_model = (os.getenv(GEMINI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_GEMINI_LIVE_MODEL
    return configured_model.strip().removeprefix("models/") in (GEMINI_38_LIVE_MODEL, GEMINI_EXTENDED_THINKING_MODEL)


def resolve_gemini_context_trigger_tokens() -> int:
    """Return the context size at which Gemini 3.8 Live drops the oldest turns."""
    raw_value = os.getenv(GEMINI_CONTEXT_TRIGGER_TOKENS_ENV, "").strip()
    if not raw_value:
        return DEFAULT_GEMINI_CONTEXT_TRIGGER_TOKENS
    try:
        value = int(raw_value)
    except ValueError:
        value = 0
    if value < MIN_GEMINI_CONTEXT_TRIGGER_TOKENS:
        logger.warning("Ignoring invalid %s=%r; using default.", GEMINI_CONTEXT_TRIGGER_TOKENS_ENV, raw_value)
        return DEFAULT_GEMINI_CONTEXT_TRIGGER_TOKENS
    return value


def resolve_gemini_thinking_level(model: str | None = None, value: str | None = None) -> str | None:
    """Validate the optional thinking level for the selected Gemini model."""
    configured_model = model
    if configured_model is None:
        configured_model = (os.getenv(GEMINI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_GEMINI_LIVE_MODEL
    configured_value = value if value is not None else os.getenv(GEMINI_THINKING_LEVEL_ENV)
    candidate = (configured_value or "").strip().lower()
    if not candidate:
        return DEFAULT_GEMINI_THINKING_LEVEL if is_gemini_extended_thinking_model(configured_model) else None
    if not is_gemini_extended_thinking_model(configured_model):
        raise ValueError(f"{GEMINI_THINKING_LEVEL_ENV} is supported only for {GEMINI_EXTENDED_THINKING_MODEL!r}.")
    if candidate not in GEMINI_THINKING_LEVELS:
        expected = ", ".join(GEMINI_THINKING_LEVELS)
        raise ValueError(f"Invalid {GEMINI_THINKING_LEVEL_ENV}={configured_value!r}. Expected {expected}.")
    return candidate


def _follow_up_by_person(value: str | None) -> dict[str, float]:
    """Parse "enzo=45, mara=45" into person IDs and follow-up seconds; bad entries are skipped."""
    windows: dict[str, float] = {}
    for item in (value or "").split(","):
        name, _, seconds = item.partition("=")
        try:
            parsed = float(seconds)
        except ValueError:
            continue
        if name.strip() and 0 < parsed <= 300:
            windows[name.strip().casefold()] = parsed
    return windows


def _optional_float(value: str | None) -> float | None:
    """Parse an optional number; empty or invalid is None."""
    try:
        return float(value) if value and value.strip() else None
    except ValueError:
        return None


def _normalize_transcription_language(value: str | None) -> str:
    """Return the configured transcription language; "auto" returns "" (no hint, detect per utterance)."""
    candidate = (value or "").strip()
    if candidate.lower() == "auto":
        return ""
    return candidate or "en"


def _resolve_non_negative_seconds(
    env_name: str,
    default: float,
    *,
    allow_zero: bool = True,
) -> float | None:
    """Parse a non-negative seconds setting, returning None only for disabled values."""
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return default
    try:
        seconds = float(raw_value)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default.", env_name, raw_value)
        return default
    if not math.isfinite(seconds) or seconds < 0 or (seconds == 0 and not allow_zero):
        logger.warning("Ignoring invalid %s=%r; using default.", env_name, raw_value)
        return default
    return seconds


def resolve_hybrid_cloud_inactivity_seconds() -> float | None:
    """Read the cloud conversational inactivity timeout; zero disables it."""
    value = _resolve_non_negative_seconds(
        HYBRID_CLOUD_INACTIVITY_SECONDS_ENV,
        DEFAULT_HYBRID_CLOUD_INACTIVITY_SECONDS,
    )
    return None if value == 0 else value


def resolve_hybrid_follow_up_seconds() -> float:
    """Read how long a conversation stays open after Reachy's last reply; zero disables the window."""
    return _resolve_non_negative_seconds(HYBRID_FOLLOW_UP_SECONDS_ENV, DEFAULT_HYBRID_FOLLOW_UP_SECONDS) or 0.0


def resolve_hybrid_ambient_buffer_seconds() -> float:
    """Read the RAM ring duration, capped at the privacy-bound 20-second maximum."""
    value = _resolve_non_negative_seconds(
        HYBRID_AMBIENT_BUFFER_SECONDS_ENV,
        DEFAULT_HYBRID_AMBIENT_BUFFER_SECONDS,
        allow_zero=False,
    )
    if value is None or value > MAX_HYBRID_AMBIENT_BUFFER_SECONDS:
        logger.warning(
            "Ignoring %s above the %.1f-second bound; using default.",
            HYBRID_AMBIENT_BUFFER_SECONDS_ENV,
            MAX_HYBRID_AMBIENT_BUFFER_SECONDS,
        )
        return DEFAULT_HYBRID_AMBIENT_BUFFER_SECONDS
    return value


def is_hybrid_runtime_enabled() -> bool:
    """Return whether the opt-in local ambient runtime is enabled."""
    return _env_flag(HYBRID_RUNTIME_ENABLED_ENV, default=DEFAULT_HYBRID_RUNTIME_ENABLED)


def is_local_transcript_preview_enabled() -> bool:
    """Return whether the opt-in local transcript preview is enabled."""
    return _env_flag(LOCAL_TRANSCRIPT_PREVIEW_ENABLED_ENV, default=DEFAULT_LOCAL_TRANSCRIPT_PREVIEW_ENABLED)


def is_household_memory_enabled() -> bool:
    """Return whether the opt-in household memory integration is enabled."""
    return _env_flag(HOUSEHOLD_MEMORY_ENABLED_ENV, default=DEFAULT_HOUSEHOLD_MEMORY_ENABLED)


def is_household_memory_review_enabled() -> bool:
    """Return whether daily review is enabled for household memory."""
    return is_household_memory_enabled() and _env_flag(
        HOUSEHOLD_MEMORY_REVIEW_ENABLED_ENV,
        default=DEFAULT_HOUSEHOLD_MEMORY_REVIEW_ENABLED,
    )


def resolve_local_context_strategy() -> str:
    """Return the configured ambient transcription strategy."""
    candidate = (os.getenv(LOCAL_CONTEXT_STRATEGY_ENV) or "").strip().lower()
    if not candidate:
        return DEFAULT_LOCAL_CONTEXT_STRATEGY
    if candidate not in {LOCAL_CONTEXT_CONTINUOUS, LOCAL_CONTEXT_ON_ACTIVATION}:
        logger.warning("Ignoring invalid %s=%r; using default.", LOCAL_CONTEXT_STRATEGY_ENV, candidate)
        return DEFAULT_LOCAL_CONTEXT_STRATEGY
    return candidate


def resolve_local_stt_base_url() -> str | None:
    """Return an explicitly configured local STT endpoint."""
    return (os.getenv(LOCAL_STT_BASE_URL_ENV) or "").strip() or None


def resolve_local_stt_model() -> str:
    """Return the configured OpenAI-compatible local STT model name."""
    return (os.getenv(LOCAL_STT_MODEL_ENV) or "").strip() or DEFAULT_LOCAL_STT_MODEL


def resolve_local_context_max_chars() -> int:
    """Return the bounded number of ambient transcript characters retained."""
    raw_value = os.getenv(LOCAL_CONTEXT_MAX_CHARS_ENV, "").strip()
    if not raw_value:
        return DEFAULT_LOCAL_CONTEXT_MAX_CHARS
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default.", LOCAL_CONTEXT_MAX_CHARS_ENV, raw_value)
        return DEFAULT_LOCAL_CONTEXT_MAX_CHARS
    if value < 128 or value > 8_000:
        logger.warning("Ignoring invalid %s=%r; using default.", LOCAL_CONTEXT_MAX_CHARS_ENV, raw_value)
        return DEFAULT_LOCAL_CONTEXT_MAX_CHARS
    return value


def resolve_local_processing_seconds(env_name: str, default: float) -> float:
    """Return a positive finite local-processing duration setting."""
    value = _resolve_non_negative_seconds(env_name, default, allow_zero=False)
    return default if value is None else value


def resolve_local_wake_followup_timeout_seconds() -> float:
    """Return the bounded wait for a split wake-word follow-up segment."""
    value = resolve_local_processing_seconds(
        LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV,
        DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
    )
    if value > MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS:
        logger.warning(
            "Ignoring %s above the %.1f-second bound; using default.",
            LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV,
            MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
        )
        return DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS
    return value


def resolve_local_acoustic_wake_threshold() -> float:
    """Return the acoustic wake score threshold in (0, 1], defaulting to the frozen 0.5."""
    raw_value = os.getenv(LOCAL_ACOUSTIC_WAKE_THRESHOLD_ENV, "").strip()
    if not raw_value:
        return DEFAULT_LOCAL_ACOUSTIC_WAKE_THRESHOLD
    try:
        value = float(raw_value)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or not 0 < value <= 1:
        logger.warning("Ignoring invalid %s=%r; using default.", LOCAL_ACOUSTIC_WAKE_THRESHOLD_ENV, raw_value)
        return DEFAULT_LOCAL_ACOUSTIC_WAKE_THRESHOLD
    return value


def resolve_local_vad_rms_threshold() -> float:
    """Return the non-negative PCM RMS threshold for the baseline local VAD."""
    value = _resolve_non_negative_seconds(LOCAL_VAD_RMS_THRESHOLD_ENV, DEFAULT_LOCAL_VAD_RMS_THRESHOLD)
    return DEFAULT_LOCAL_VAD_RMS_THRESHOLD if value is None else value


def resolve_audio_output_backend() -> str:
    """Return the selected speaker output backend, defaulting to the SDK path."""
    candidate = (os.getenv(AUDIO_OUTPUT_BACKEND_ENV) or "").strip().lower()
    if not candidate:
        return DEFAULT_AUDIO_OUTPUT_BACKEND
    if candidate not in {AUDIO_OUTPUT_BACKEND_SDK, AUDIO_OUTPUT_BACKEND_GSTREAMER}:
        raise ValueError(
            f"Invalid {AUDIO_OUTPUT_BACKEND_ENV}={candidate!r}. Expected "
            f"{AUDIO_OUTPUT_BACKEND_SDK!r} or {AUDIO_OUTPUT_BACKEND_GSTREAMER!r}."
        )
    return candidate


@dataclass(frozen=True)
class HFConnectionSelection:
    """Resolved Hugging Face connection mode and target availability."""

    mode: str
    has_target: bool
    session_url: str | None = None
    direct_ws_url: str | None = None


@dataclass(frozen=True)
class HFRealtimeURLParts:
    """Parsed Hugging Face realtime URL components used by UI and client setup."""

    base_url: str
    websocket_base_url: str
    connect_query: dict[str, str]
    host: str | None
    port: int | None
    has_realtime_path: bool


def parse_hf_realtime_url(realtime_url: str) -> HFRealtimeURLParts:
    """Parse a Hugging Face realtime URL into OpenAI-compatible client endpoints."""
    parsed = urlsplit(realtime_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss", "http", "https"}:
        raise ValueError(
            "Expected Hugging Face realtime URL to start with ws://, wss://, http://, or https://, "
            f"got: {realtime_url}"
        )

    path = parsed.path.rstrip("/")
    has_realtime_path = path.endswith("/realtime")
    if has_realtime_path:
        base_path = path[: -len("/realtime")]
    else:
        base_path = path

    connect_query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "model"}
    http_scheme = "https" if scheme in {"wss", "https"} else "http"
    websocket_scheme = "wss" if scheme in {"wss", "https"} else "ws"
    base_url = urlunsplit((http_scheme, parsed.netloc, base_path, "", ""))
    websocket_base_url = urlunsplit((websocket_scheme, parsed.netloc, base_path, "", ""))
    return HFRealtimeURLParts(
        base_url=base_url,
        websocket_base_url=websocket_base_url,
        connect_query=connect_query,
        host=parsed.hostname,
        port=parsed.port or HF_DEFAULTS.direct_port,
        has_realtime_path=has_realtime_path,
    )


def parse_hf_direct_target(ws_url: str | None) -> tuple[str | None, int | None]:
    """Extract host and port from a direct Hugging Face realtime URL."""
    if not ws_url:
        return None, None
    try:
        parsed = parse_hf_realtime_url(ws_url)
        return parsed.host, parsed.port
    except Exception:
        return None, None


def build_hf_direct_ws_url(host: str, port: int) -> str:
    """Build the direct Hugging Face realtime websocket URL used by the app."""
    return f"ws://{host}:{port}/v1/realtime"


def _collect_profile_names(profiles_root: Path) -> set[str]:
    """Return declarative profile names from a profiles root directory."""
    if not profiles_root.exists() or not profiles_root.is_dir():
        return set()
    return {path.name for path in profiles_root.iterdir() if path.is_dir() and (path / "profile.md").is_file()}


def list_tool_module_names(tools_root: Path | None) -> list[str]:
    """Return valid importable tool module names from a directory."""
    if tools_root is None or not tools_root.is_dir():
        return []

    ignored = {"__init__", "core_tools"}
    tool_names: list[str] = []
    try:
        for tool_file in tools_root.glob("*.py"):
            if not tool_file.is_file() or tool_file.stem in ignored or tool_file.name.startswith("_"):
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool_file.stem) is None:
                logger.warning("Skipping tool module with invalid name: %s", tool_file)
                continue
            tool_names.append(tool_file.stem)
    except OSError as exc:
        logger.warning("Failed to list tool modules in %s: %s", tools_root, exc)
    return sorted(tool_names)


def _raise_on_name_collisions(
    *,
    label: str,
    external_root: Path,
    internal_root: Path,
    external_names: set[str],
    internal_names: set[str],
) -> None:
    """Raise with a clear message when external/internal names collide."""
    collisions = sorted(external_names & internal_names)
    if not collisions:
        return

    raise RuntimeError(
        f"Config.__init__(): Ambiguous {label} names found in both external and built-in libraries: {collisions}. "
        f"External {label} root: {external_root}. Built-in {label} root: {internal_root}. "
        f"Please rename the conflicting external {label}(s) to continue."
    )


def _load_dotenv_configuration() -> None:
    """Load local configuration without overriding explicit process values."""
    if _env_flag("REACHY_MINI_SKIP_DOTENV", default=False):
        logger.info("Skipping .env loading because REACHY_MINI_SKIP_DOTENV is set")
        return

    # Explicit process/terminal variables must win over local defaults.
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)
        logger.info("Configuration loaded from %s", dotenv_path)
    else:
        logger.warning("No .env file found, using environment variables")


# Validate LOCKED_PROFILE at startup
if LOCKED_PROFILE is not None:
    _profiles_dir = DEFAULT_PROFILES_DIRECTORY
    _profile_path = _profiles_dir / LOCKED_PROFILE
    _profile_file = _profile_path / "profile.md"
    if not _profile_path.is_dir():
        logger.critical("LOCKED_PROFILE %r does not exist in %s", LOCKED_PROFILE, _profiles_dir)
        sys.exit(1)
    if not _profile_file.is_file():
        logger.critical("LOCKED_PROFILE %r has no profile definition", LOCKED_PROFILE)
        sys.exit(1)

_load_dotenv_configuration()

_warn_on_obsolete_backend_env()


class Config:
    """Configuration class for the conversation app."""

    REALTIME_PROVIDER = _normalize_realtime_provider(os.getenv(REALTIME_PROVIDER_ENV))
    GEMINI_API_KEY = os.getenv(GEMINI_API_KEY_ENV)
    GEMINI_LIVE_MODEL = (os.getenv(GEMINI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_GEMINI_LIVE_MODEL
    GEMINI_VOICE = (os.getenv(GEMINI_VOICE_ENV) or "").strip() or DEFAULT_GEMINI_VOICE
    GEMINI_THINKING_LEVEL = resolve_gemini_thinking_level(
        GEMINI_LIVE_MODEL,
        os.getenv(GEMINI_THINKING_LEVEL_ENV),
    )
    GEMINI_CONTEXT_TRIGGER_TOKENS = resolve_gemini_context_trigger_tokens()
    OPENAI_API_KEY = os.getenv(OPENAI_API_KEY_ENV)
    OPENAI_LIVE_MODEL = (os.getenv(OPENAI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_OPENAI_LIVE_MODEL
    OPENAI_LIVE_BACKEND_MODEL = (
        os.getenv(OPENAI_LIVE_BACKEND_MODEL_ENV) or ""
    ).strip() or DEFAULT_OPENAI_LIVE_BACKEND_MODEL
    OPENAI_LIVE_VOICE = (os.getenv(OPENAI_LIVE_VOICE_ENV) or "").strip() or DEFAULT_OPENAI_LIVE_VOICE
    HYBRID_RUNTIME_ENABLED = is_hybrid_runtime_enabled()
    HYBRID_CLOUD_INACTIVITY_SECONDS = resolve_hybrid_cloud_inactivity_seconds()
    HYBRID_FOLLOW_UP_SECONDS = resolve_hybrid_follow_up_seconds()
    HYBRID_ADDRESSEE_CHECK_ENABLED = _env_flag(HYBRID_ADDRESSEE_CHECK_ENABLED_ENV, default=True)
    HYBRID_AMBIENT_BUFFER_SECONDS = resolve_hybrid_ambient_buffer_seconds()
    LOCAL_CONTEXT_STRATEGY = resolve_local_context_strategy()
    LOCAL_STT_BASE_URL = resolve_local_stt_base_url()
    LOCAL_STT_MODEL = resolve_local_stt_model()
    # Optional more accurate model for requests made to Reachy (e.g. openai/whisper-large-v3-turbo).
    LOCAL_STT_REQUEST_MODEL = (os.getenv("LOCAL_STT_REQUEST_MODEL") or "").strip() or None
    LOCAL_STT_TIMEOUT_SECONDS = resolve_local_processing_seconds(
        LOCAL_STT_TIMEOUT_SECONDS_ENV, DEFAULT_LOCAL_STT_TIMEOUT_SECONDS
    )
    LOCAL_STT_LATIN_ONLY = _env_flag(LOCAL_STT_LATIN_ONLY_ENV, default=False)
    LOCAL_VAD_RMS_THRESHOLD = resolve_local_vad_rms_threshold()
    LOCAL_VAD_SILENCE_SECONDS = resolve_local_processing_seconds(
        LOCAL_VAD_SILENCE_SECONDS_ENV, DEFAULT_LOCAL_VAD_SILENCE_SECONDS
    )
    LOCAL_ACOUSTIC_WAKE_ENABLED = _env_flag(LOCAL_ACOUSTIC_WAKE_ENABLED_ENV, default=False)
    LOCAL_ACOUSTIC_WAKE_MODEL_DIR = (os.getenv(LOCAL_ACOUSTIC_WAKE_MODEL_DIR_ENV) or "").strip()
    LOCAL_ACOUSTIC_WAKE_THRESHOLD = resolve_local_acoustic_wake_threshold()
    LOCAL_WAKE_WORD = (os.getenv(LOCAL_WAKE_WORD_ENV) or "").strip() or DEFAULT_LOCAL_WAKE_WORD
    LOCAL_WAKE_COOLDOWN_SECONDS = resolve_local_processing_seconds(
        LOCAL_WAKE_COOLDOWN_SECONDS_ENV, DEFAULT_LOCAL_WAKE_COOLDOWN_SECONDS
    )
    LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS = resolve_local_wake_followup_timeout_seconds()
    LOCAL_CONTEXT_WINDOW_SECONDS = resolve_local_processing_seconds(
        LOCAL_CONTEXT_WINDOW_SECONDS_ENV, DEFAULT_LOCAL_CONTEXT_WINDOW_SECONDS
    )
    LOCAL_CONTEXT_MAX_CHARS = resolve_local_context_max_chars()
    LOCAL_TRANSCRIPT_PREVIEW_ENABLED = is_local_transcript_preview_enabled()
    HOUSEHOLD_MEMORY_ENABLED = is_household_memory_enabled()
    HOUSEHOLD_MEMORY_CAPTURE_ENABLED = _env_flag(HOUSEHOLD_MEMORY_CAPTURE_ENABLED_ENV, default=False)
    HOUSEHOLD_MEMORY_REVIEW_ENABLED = is_household_memory_review_enabled()
    AUDIO_DIAGNOSTICS_ENABLED = _env_flag("AUDIO_DIAGNOSTICS_ENABLED", default=False)
    GEMINI_LIVE_LOCAL_BARGE_IN_ENABLED = _env_flag(GEMINI_LIVE_LOCAL_BARGE_IN_ENV, default=False)
    GEMINI_LIVE_VIDEO_ENABLED = _env_flag(GEMINI_LIVE_VIDEO_ENV, default=False)
    OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED = _env_flag("OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED", default=False)
    MACOS_LOCAL_AUDIO_FLUSH_ENABLED = _env_flag("MACOS_LOCAL_AUDIO_FLUSH_ENABLED", default=False)
    SPEECH_WOBBLE_ENABLED = _env_flag("SPEECH_WOBBLE_ENABLED", default=True)
    SPEAKER_FOLLOWING_ENABLED = _env_flag("SPEAKER_FOLLOWING_ENABLED", default=True)
    FACE_RECOGNITION_ENABLED = _env_flag("FACE_RECOGNITION_ENABLED", default=False)
    # IKEA DIRIGERA hub for lights and plugs; set by `python -m reachy_mini_conversation_app.dirigera_hub pair`.
    DIRIGERA_HOST = (os.getenv("DIRIGERA_HOST") or "").strip() or None
    DIRIGERA_TOKEN = (os.getenv("DIRIGERA_TOKEN") or "").strip() or None
    # Smart Life devices with local keys, from TinyTuya's wizard (kept outside the repository).
    TUYA_DEVICES_FILE = os.path.expanduser(os.getenv("TUYA_DEVICES_FILE") or "~/.config/reachy-tuya/devices.json")
    # Rooms for devices whose system has none, and areas within rooms ("Dining"), for home_lights.
    HOME_LAYOUT_FILE = os.path.expanduser(os.getenv("HOME_LAYOUT_FILE") or "~/.config/reachy-home/layout.json")
    # Everyday commands (lights, music, TV, time, weather) answered locally, without the cloud conversation.
    QUICK_COMMANDS_ENABLED = _env_flag("QUICK_COMMANDS_ENABLED", default=True)
    # Longer follow-up windows for people who need more time to answer (face-recognized), e.g. "enzo=45".
    HYBRID_FOLLOW_UP_BY_PERSON = _follow_up_by_person(os.getenv("HYBRID_FOLLOW_UP_BY_PERSON"))
    # Debugging only: print the words of requests quick commands passed to the conversation (off by default).
    QUICK_COMMANDS_LOG_TEXT = _env_flag("QUICK_COMMANDS_LOG_TEXT", default=False)
    # Gemini text-to-speech model for quick replies (in GEMINI_VOICE).
    LOCAL_SPEECH_MODEL = (os.getenv("LOCAL_SPEECH_MODEL") or "").strip() or "gemini-3.8-flash-lite-tts"
    # Home place for weather; coordinates skip the lookup.
    HOME_LOCATION = (os.getenv("HOME_LOCATION") or "").strip() or None
    HOME_LATITUDE = _optional_float(os.getenv("HOME_LATITUDE"))
    HOME_LONGITUDE = _optional_float(os.getenv("HOME_LONGITUDE"))
    # Android / Google TV; set by `python -m reachy_mini_conversation_app.android_tv pair`.
    ANDROID_TV_HOST = (os.getenv("ANDROID_TV_HOST") or "").strip() or None
    ANDROID_TV_CERT_DIR = os.path.expanduser(os.getenv("ANDROID_TV_CERT_DIR") or "~/.config/reachy-tv")
    AUDIO_OUTPUT_BACKEND = resolve_audio_output_backend()
    HF_REALTIME_CONNECTION_MODE = (
        _normalize_hf_connection_mode(os.getenv(HF_REALTIME_CONNECTION_MODE_ENV)) or HF_DEFAULTS.connection_mode
    )
    # Deliberately ignore HF_REALTIME_SESSION_URL from the environment; the app-managed proxy is HF_DEFAULTS.session_url.
    HF_REALTIME_SESSION_URL = HF_DEFAULTS.session_url
    HF_REALTIME_WS_URL = os.getenv(HF_REALTIME_WS_URL_ENV)
    REALTIME_TRANSCRIPTION_LANGUAGE = _normalize_transcription_language(os.getenv(REALTIME_TRANSCRIPTION_LANGUAGE_ENV))
    HF_TOKEN = os.getenv("HF_TOKEN")  # Optional, falls back to hf auth login if not set

    logger.debug(
        "HF mode: %s, HF session URL set: %s, HF direct URL set: %s",
        HF_REALTIME_CONNECTION_MODE,
        bool(HF_REALTIME_SESSION_URL and HF_REALTIME_SESSION_URL.strip()),
        bool(HF_REALTIME_WS_URL and HF_REALTIME_WS_URL.strip()),
    )

    # Filesystem root containing profile directories, not a Python import path.
    _profiles_directory_env = os.getenv("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY")
    PROFILES_DIRECTORY = Path(_profiles_directory_env) if _profiles_directory_env else DEFAULT_PROFILES_DIRECTORY
    INSTANCE_PATH: Path | None = None  # set at startup; writable home for UI-created profiles
    _tools_directory_env = os.getenv("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY")
    TOOLS_DIRECTORY = Path(_tools_directory_env) if _tools_directory_env else None
    AUTOLOAD_EXTERNAL_TOOLS = _env_flag("AUTOLOAD_EXTERNAL_TOOLS", default=False)
    REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE")

    logger.debug(f"Custom Profile: {REACHY_MINI_CUSTOM_PROFILE}")

    def __init__(self) -> None:
        """Initialize the configuration."""
        if (
            self.REACHY_MINI_CUSTOM_PROFILE
            and self.REACHY_MINI_CUSTOM_PROFILE != "default"
            and self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY
        ):
            selected_profile_path = self.PROFILES_DIRECTORY / self.REACHY_MINI_CUSTOM_PROFILE
            if not (selected_profile_path / "profile.md").is_file():
                available_profiles = sorted(_collect_profile_names(self.PROFILES_DIRECTORY))
                raise RuntimeError(
                    "Config.__init__(): Selected profile "
                    f"'{self.REACHY_MINI_CUSTOM_PROFILE}' was not found in external profiles root "
                    f"{self.PROFILES_DIRECTORY}. "
                    f"Available external profiles: {available_profiles}. "
                    "Either set 'REACHY_MINI_CUSTOM_PROFILE' to one of the available external profiles "
                    "or unset 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' to use built-in profiles."
                )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            external_profiles = _collect_profile_names(self.PROFILES_DIRECTORY)
            internal_profiles = _collect_profile_names(DEFAULT_PROFILES_DIRECTORY)
            _raise_on_name_collisions(
                label="profile",
                external_root=self.PROFILES_DIRECTORY,
                internal_root=DEFAULT_PROFILES_DIRECTORY,
                external_names=external_profiles,
                internal_names=internal_profiles,
            )

        if self.TOOLS_DIRECTORY is not None:
            builtin_tools_root = Path(__file__).parent / "tools"
            external_tools = set(list_tool_module_names(self.TOOLS_DIRECTORY))
            internal_tools = set(list_tool_module_names(builtin_tools_root))
            _raise_on_name_collisions(
                label="tool",
                external_root=self.TOOLS_DIRECTORY,
                internal_root=builtin_tools_root,
                external_names=external_tools,
                internal_names=internal_tools,
            )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is set. "
                "Profiles will be loaded from %s.",
                self.PROFILES_DIRECTORY,
            )
        else:
            logger.info(
                "'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is not set. Using built-in profiles from %s.",
                DEFAULT_PROFILES_DIRECTORY,
            )

        if self.TOOLS_DIRECTORY is not None:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is set. "
                "External tools will be loaded from %s.",
                self.TOOLS_DIRECTORY,
            )
        else:
            logger.info("'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is not set. Using built-in shared tools only.")

    def user_personalities_root(self) -> Path:
        """Return the writable root for user-created profiles."""
        if self.INSTANCE_PATH is None:
            return TERMINAL_USER_PERSONALITIES_DIRECTORY
        return self.INSTANCE_PATH / USER_PERSONALITIES_DIRNAME

    def resolve_profile_dir(self, profile: str) -> Path:
        """On-disk directory for a profile selection."""
        head, _, tail = profile.partition("/")
        if head == USER_PERSONALITIES_DIRNAME and tail:
            return self.user_personalities_root() / tail
        return self.PROFILES_DIRECTORY / profile


config = Config()


def refresh_runtime_config_from_env() -> None:
    """Refresh mutable runtime config fields from the current environment."""
    _warn_on_obsolete_backend_env()
    config.REALTIME_PROVIDER = _normalize_realtime_provider(os.getenv(REALTIME_PROVIDER_ENV))
    config.GEMINI_API_KEY = os.getenv(GEMINI_API_KEY_ENV)
    gemini_model = (os.getenv(GEMINI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_GEMINI_LIVE_MODEL
    gemini_thinking_level = resolve_gemini_thinking_level(
        gemini_model,
        os.getenv(GEMINI_THINKING_LEVEL_ENV),
    )
    config.GEMINI_LIVE_MODEL = gemini_model
    config.GEMINI_VOICE = (os.getenv(GEMINI_VOICE_ENV) or "").strip() or DEFAULT_GEMINI_VOICE
    config.GEMINI_THINKING_LEVEL = gemini_thinking_level
    config.GEMINI_CONTEXT_TRIGGER_TOKENS = resolve_gemini_context_trigger_tokens()
    config.OPENAI_API_KEY = os.getenv(OPENAI_API_KEY_ENV)
    config.OPENAI_LIVE_MODEL = (os.getenv(OPENAI_LIVE_MODEL_ENV) or "").strip() or DEFAULT_OPENAI_LIVE_MODEL
    config.OPENAI_LIVE_BACKEND_MODEL = (
        os.getenv(OPENAI_LIVE_BACKEND_MODEL_ENV) or ""
    ).strip() or DEFAULT_OPENAI_LIVE_BACKEND_MODEL
    config.OPENAI_LIVE_VOICE = (os.getenv(OPENAI_LIVE_VOICE_ENV) or "").strip() or DEFAULT_OPENAI_LIVE_VOICE
    config.HYBRID_RUNTIME_ENABLED = is_hybrid_runtime_enabled()
    config.HYBRID_CLOUD_INACTIVITY_SECONDS = resolve_hybrid_cloud_inactivity_seconds()
    config.HYBRID_FOLLOW_UP_SECONDS = resolve_hybrid_follow_up_seconds()
    config.HYBRID_ADDRESSEE_CHECK_ENABLED = _env_flag(HYBRID_ADDRESSEE_CHECK_ENABLED_ENV, default=True)
    config.HYBRID_AMBIENT_BUFFER_SECONDS = resolve_hybrid_ambient_buffer_seconds()
    config.LOCAL_CONTEXT_STRATEGY = resolve_local_context_strategy()
    config.LOCAL_STT_BASE_URL = resolve_local_stt_base_url()
    config.LOCAL_STT_MODEL = resolve_local_stt_model()
    config.LOCAL_STT_REQUEST_MODEL = (os.getenv("LOCAL_STT_REQUEST_MODEL") or "").strip() or None
    config.LOCAL_STT_TIMEOUT_SECONDS = resolve_local_processing_seconds(
        LOCAL_STT_TIMEOUT_SECONDS_ENV, DEFAULT_LOCAL_STT_TIMEOUT_SECONDS
    )
    config.LOCAL_VAD_RMS_THRESHOLD = resolve_local_vad_rms_threshold()
    config.LOCAL_VAD_SILENCE_SECONDS = resolve_local_processing_seconds(
        LOCAL_VAD_SILENCE_SECONDS_ENV, DEFAULT_LOCAL_VAD_SILENCE_SECONDS
    )
    config.LOCAL_STT_LATIN_ONLY = _env_flag(LOCAL_STT_LATIN_ONLY_ENV, default=False)
    config.LOCAL_ACOUSTIC_WAKE_ENABLED = _env_flag(LOCAL_ACOUSTIC_WAKE_ENABLED_ENV, default=False)
    config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR = (os.getenv(LOCAL_ACOUSTIC_WAKE_MODEL_DIR_ENV) or "").strip()
    config.LOCAL_ACOUSTIC_WAKE_THRESHOLD = resolve_local_acoustic_wake_threshold()
    config.LOCAL_WAKE_WORD = (os.getenv(LOCAL_WAKE_WORD_ENV) or "").strip() or DEFAULT_LOCAL_WAKE_WORD
    config.LOCAL_WAKE_COOLDOWN_SECONDS = resolve_local_processing_seconds(
        LOCAL_WAKE_COOLDOWN_SECONDS_ENV, DEFAULT_LOCAL_WAKE_COOLDOWN_SECONDS
    )
    config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS = resolve_local_wake_followup_timeout_seconds()
    config.LOCAL_CONTEXT_WINDOW_SECONDS = resolve_local_processing_seconds(
        LOCAL_CONTEXT_WINDOW_SECONDS_ENV, DEFAULT_LOCAL_CONTEXT_WINDOW_SECONDS
    )
    config.LOCAL_CONTEXT_MAX_CHARS = resolve_local_context_max_chars()
    config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED = is_local_transcript_preview_enabled()
    config.HOUSEHOLD_MEMORY_ENABLED = is_household_memory_enabled()
    config.HOUSEHOLD_MEMORY_CAPTURE_ENABLED = _env_flag(HOUSEHOLD_MEMORY_CAPTURE_ENABLED_ENV, default=False)
    config.HOUSEHOLD_MEMORY_REVIEW_ENABLED = is_household_memory_review_enabled()
    config.AUDIO_DIAGNOSTICS_ENABLED = _env_flag("AUDIO_DIAGNOSTICS_ENABLED", default=False)
    config.GEMINI_LIVE_LOCAL_BARGE_IN_ENABLED = _env_flag(GEMINI_LIVE_LOCAL_BARGE_IN_ENV, default=False)
    config.GEMINI_LIVE_VIDEO_ENABLED = _env_flag(GEMINI_LIVE_VIDEO_ENV, default=False)
    config.OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED = _env_flag("OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED", default=False)
    config.MACOS_LOCAL_AUDIO_FLUSH_ENABLED = _env_flag("MACOS_LOCAL_AUDIO_FLUSH_ENABLED", default=False)
    config.SPEECH_WOBBLE_ENABLED = _env_flag("SPEECH_WOBBLE_ENABLED", default=True)
    config.SPEAKER_FOLLOWING_ENABLED = _env_flag("SPEAKER_FOLLOWING_ENABLED", default=True)
    config.FACE_RECOGNITION_ENABLED = _env_flag("FACE_RECOGNITION_ENABLED", default=False)
    config.DIRIGERA_HOST = (os.getenv("DIRIGERA_HOST") or "").strip() or None
    config.DIRIGERA_TOKEN = (os.getenv("DIRIGERA_TOKEN") or "").strip() or None
    config.TUYA_DEVICES_FILE = os.path.expanduser(
        os.getenv("TUYA_DEVICES_FILE") or "~/.config/reachy-tuya/devices.json"
    )
    config.HOME_LAYOUT_FILE = os.path.expanduser(os.getenv("HOME_LAYOUT_FILE") or "~/.config/reachy-home/layout.json")
    config.QUICK_COMMANDS_ENABLED = _env_flag("QUICK_COMMANDS_ENABLED", default=True)
    config.HYBRID_FOLLOW_UP_BY_PERSON = _follow_up_by_person(os.getenv("HYBRID_FOLLOW_UP_BY_PERSON"))
    config.QUICK_COMMANDS_LOG_TEXT = _env_flag("QUICK_COMMANDS_LOG_TEXT", default=False)
    config.LOCAL_SPEECH_MODEL = (os.getenv("LOCAL_SPEECH_MODEL") or "").strip() or "gemini-3.8-flash-lite-tts"
    config.HOME_LOCATION = (os.getenv("HOME_LOCATION") or "").strip() or None
    config.HOME_LATITUDE = _optional_float(os.getenv("HOME_LATITUDE"))
    config.HOME_LONGITUDE = _optional_float(os.getenv("HOME_LONGITUDE"))
    config.ANDROID_TV_HOST = (os.getenv("ANDROID_TV_HOST") or "").strip() or None
    config.ANDROID_TV_CERT_DIR = os.path.expanduser(os.getenv("ANDROID_TV_CERT_DIR") or "~/.config/reachy-tv")
    config.AUDIO_OUTPUT_BACKEND = resolve_audio_output_backend()
    config.HF_REALTIME_CONNECTION_MODE = (
        _normalize_hf_connection_mode(os.getenv(HF_REALTIME_CONNECTION_MODE_ENV)) or HF_DEFAULTS.connection_mode
    )
    # Deliberately ignore HF_REALTIME_SESSION_URL from the environment; the app-managed proxy is HF_DEFAULTS.session_url.
    config.HF_REALTIME_SESSION_URL = HF_DEFAULTS.session_url
    config.HF_REALTIME_WS_URL = os.getenv(HF_REALTIME_WS_URL_ENV)
    config.REALTIME_TRANSCRIPTION_LANGUAGE = _normalize_transcription_language(
        os.getenv(REALTIME_TRANSCRIPTION_LANGUAGE_ENV)
    )
    config.HF_TOKEN = os.getenv("HF_TOKEN")
    config.REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE")


def get_available_voices() -> list[str]:
    """Return the curated Hugging Face voice list."""
    return list(HF_AVAILABLE_VOICES)


def get_default_voice() -> str:
    """Return the default Hugging Face voice."""
    return HF_DEFAULTS.voice


def get_realtime_provider() -> str:
    """Return the selected realtime provider."""
    return config.REALTIME_PROVIDER


def get_audio_output_backend() -> str:
    """Return the selected application speaker output backend."""
    return config.AUDIO_OUTPUT_BACKEND


def get_hf_session_url() -> str | None:
    """Return the built-in Hugging Face session proxy URL, if any."""
    value = (getattr(config, "HF_REALTIME_SESSION_URL", None) or "").strip()
    return value or None


def get_hf_direct_ws_url() -> str | None:
    """Return the configured direct Hugging Face realtime URL, if any."""
    value = (getattr(config, "HF_REALTIME_WS_URL", None) or "").strip()
    return value or None


def get_hf_connection_selection() -> HFConnectionSelection:
    """Resolve the selected Hugging Face connection mode and whether it is usable."""
    session_url = get_hf_session_url()
    direct_ws_url = get_hf_direct_ws_url()
    mode = _normalize_hf_connection_mode(getattr(config, "HF_REALTIME_CONNECTION_MODE", None))
    if mode is None:
        raise RuntimeError(f"{HF_REALTIME_CONNECTION_MODE_ENV} must be set to local or deployed.")

    target = direct_ws_url if mode == HF_LOCAL_CONNECTION_MODE else session_url

    return HFConnectionSelection(
        mode=mode,
        has_target=bool(target),
        session_url=session_url,
        direct_ws_url=direct_ws_url,
    )


def has_hf_realtime_target() -> bool:
    """Return whether Hugging Face has a target for the selected mode."""
    return get_hf_connection_selection().has_target


def has_realtime_target() -> bool:
    """Return whether the selected provider has its required connection target."""
    if get_realtime_provider() == GEMINI_BACKEND:
        return bool((config.GEMINI_API_KEY or "").strip())
    if get_realtime_provider() == OPENAI_LIVE_BACKEND:
        return bool((config.OPENAI_API_KEY or "").strip())
    return has_hf_realtime_target()


def get_realtime_missing_config_message() -> str:
    """Return the configuration message for the selected provider's missing target."""
    if get_realtime_provider() == GEMINI_BACKEND:
        return f"{GEMINI_API_KEY_ENV} is not configured."
    if get_realtime_provider() == OPENAI_LIVE_BACKEND:
        return f"{OPENAI_API_KEY_ENV} is not configured."
    return f"{HF_REALTIME_WS_URL_ENV} is not configured."


def set_instance_path(instance_path: str | Path | None) -> None:
    """Record the app instance dir so UI-created profiles persist outside package data."""
    config.INSTANCE_PATH = Path(instance_path) if instance_path else None


def set_custom_profile(profile: str | None) -> None:
    """Update the selected profile in runtime config and the environment."""
    if LOCKED_PROFILE is not None:
        return
    if profile:
        os.environ["REACHY_MINI_CUSTOM_PROFILE"] = profile
    else:
        os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
    config.REACHY_MINI_CUSTOM_PROFILE = profile
