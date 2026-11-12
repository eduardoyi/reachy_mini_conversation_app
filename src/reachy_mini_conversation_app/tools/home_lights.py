import asyncio
import logging
from typing import Any
from pathlib import Path
from collections.abc import Callable, Awaitable

from reachy_mini_conversation_app.home_devices import (
    Routine,
    HubDevice,
    HomeLayout,
    LightChange,
    HomeDeviceError,
    normalize_name,
)
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolSpec, ToolDependencies


logger = logging.getLogger(__name__)

# A routine's "TV off" runs in the background: the TV can take seconds to confirm, and the reply need not wait.
_BACKGROUND: set[asyncio.Task[Any]] = set()

_WARMTH_POSITION = {"warm": 0.0, "neutral": 0.5, "cool": 1.0}
# (hue in degrees, saturation 0-1)
_COLORS: dict[str, tuple[float, float]] = {
    "red": (0.0, 1.0),
    "orange": (25.0, 1.0),
    "yellow": (55.0, 1.0),
    "green": (120.0, 1.0),
    "cyan": (180.0, 1.0),
    "blue": (230.0, 1.0),
    "purple": (275.0, 1.0),
    "pink": (320.0, 0.7),
}
# "Turn on the lights" means every light and plug, in any of the household's languages.
_EVERYTHING = frozenset(
    {
        "all", "everything", "house", "lights", "the lights", "all lights", "all the lights",
        "todo", "todas", "todos", "casa", "luces", "las luces", "todas las luces",
        "tutto", "tutte", "luci", "le luci", "tutte le luci",
        "tudo", "luzes", "as luzes", "todas as luzes",
    }
)  # fmt: skip


def without_offline_duplicates(devices: list[HubDevice]) -> list[HubDevice]:
    """Drop an unreachable device when a reachable one has the same name (an old bulb left in an app)."""
    online = {normalize_name(device.name) for device in devices if device.reachable}
    return [device for device in devices if device.reachable or normalize_name(device.name) not in online]


# Words that only say "lights" or "area" around a group's name: "the dining lights" -> "dining".
_FILLER_WORDS = frozenset(
    {
        "the", "light", "lights", "lamp", "lamps", "area", "zone", "part", "side",
        "el", "la", "los", "las", "luz", "luces", "zona", "parte", "lampara", "lamparas",
        "il", "le", "luce", "luci", "o", "a", "os", "as", "luzes",
    }
)  # fmt: skip


def _core(text: str) -> str:
    """Return the name without filler words, so "the dining lights" and "Dining" match."""
    words = normalize_name(text).split()
    return " ".join(word for word in words if word not in _FILLER_WORDS) or " ".join(words)


def _match_targets(devices: list[HubDevice], target: str) -> list[HubDevice] | None:
    """Return the devices a spoken target names: everything, a room, an area, a device, or a partial name."""
    devices = without_offline_duplicates(devices)
    wanted = normalize_name(target)
    if wanted in _EVERYTHING:
        return devices
    # A device's own name wins: "Living lamp" is the lamp, not the whole "Living" area.
    exact = [device for device in devices if normalize_name(device.name) == wanted]
    if exact:
        return exact
    rooms = [device for device in devices if device.room and normalize_name(device.room) == wanted]
    if rooms:
        return rooms
    areas = [device for device in devices if any(_core(area) == _core(target) for area in device.areas)]
    if areas:
        return areas
    partial = [
        device
        for device in devices
        if wanted in normalize_name(device.name) or (device.room and wanted in normalize_name(device.room))
    ]
    if partial:
        return partial
    # Every word of a device's name was said, among others: "living room lamp" -> "Living lamp".
    said = set(wanted.split())
    named = [device for device in devices if set(normalize_name(device.name).split()) <= said]
    return named or None


def _layout(home: Any) -> HomeLayout:
    layout = getattr(home, "layout", None)
    return layout if isinstance(layout, HomeLayout) else HomeLayout()


def _left_out(devices: list[HubDevice], layout: HomeLayout) -> list[HubDevice]:
    """Drop the lights "turn on the lights" skips (the TV backlight)."""
    return [device for device in devices if normalize_name(device.name) not in layout.left_out_of_all_on]


def find_routine(layout: HomeLayout, name: str) -> Routine | None:
    """Return the routine one of whose phrases is ``name`` (without case or accents)."""
    wanted = normalize_name(name)
    for routine in layout.routines:
        if any(normalize_name(phrase) == wanted for phrase in routine.say):
            return routine
    return None


def routine_line(routine: Routine) -> str:
    """Describe a routine for the model: its name, other phrases, and what it does."""

    def named(target: str, other: str) -> str:
        return other if normalize_name(target) in _EVERYTHING else target

    does = []
    if routine.on:
        does.append("turns on " + ", ".join(named(target, "all the lights") for target in routine.on))
    if routine.off:
        rest = "every other light" if routine.on else "all the lights"
        does.append("turns off " + ", ".join(named(target, rest) for target in routine.off))
    if routine.tv_off:
        does.append("turns the TV off")
    if routine.pause_music:
        does.append("pauses the music")
    if routine.sleep:
        does.append("then you go to sleep")
    also = f" (also {', '.join(repr(phrase) for phrase in routine.say[1:])})" if len(routine.say) > 1 else ""
    return f"{routine.name!r}{also}: {', '.join(does)}"


def _log_tv_off(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("Routine TV off failed: %s", type(error).__name__)
    elif isinstance(task.result(), dict) and "error" in task.result():
        logger.info("Routine TV off: the TV did not respond")


def _describe(device: HubDevice) -> dict[str, Any]:
    state: dict[str, Any] = {"name": device.name, "on": device.is_on}
    if device.room:
        state["room"] = device.room
    if device.areas:
        state["area"] = ", ".join(device.areas)
    if device.kind == "outlet":
        state["plug"] = True
    if device.colors:
        state["colors"] = True
    if device.brightness is not None and device.is_on:
        state["brightness"] = device.brightness
    if not device.reachable:
        state["unreachable"] = True
    return state


def _parse_color(value: str) -> tuple[float, float] | None:
    """Return (hue, saturation) for a color name or #rrggbb."""
    value = value.strip().lower()
    if value in _COLORS:
        return _COLORS[value]
    hex_digits = value.removeprefix("#")
    if len(hex_digits) == 6:
        try:
            red, green, blue = (int(hex_digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
        except ValueError:
            return None
        high, low = max(red, green, blue), min(red, green, blue)
        if high == 0:
            return None
        delta = high - low
        if delta == 0:
            hue = 0.0
        elif high == red:
            hue = 60 * (((green - blue) / delta) % 6)
        elif high == green:
            hue = 60 * ((blue - red) / delta + 2)
        else:
            hue = 60 * ((red - green) / delta + 4)
        return round(hue, 1), round(delta / high, 3)
    return None


class HomeLights(Tool):
    """Switch, dim and color the home's lights and smart plugs."""

    name = "home_lights"
    description = (
        "Control the home's lights and smart plugs (IKEA hub and Smart Life devices). Anyone in the household "
        "may use it, kids included. action=status lists rooms, lights, plugs and scenes with their state and "
        "which lights can show colors: use it first when you do not know the names, or when asked what is on. "
        "action=set changes a target: a room, an area within a room (e.g. the dining area), a light or plug name, "
        "or 'all' (also for plain \"the lights\"; no target means all). Give on=true/false, "
        "brightness 1-100, warmth (warm, neutral, cool) for white light, and/or color (red, orange, yellow, "
        "green, cyan, blue, purple, pink, white, or #rrggbb) for lights that show colors. Brightness, warmth "
        "or color alone also turns the light on. 'all' with on=true leaves out accent lights such as the TV "
        'backlight unless they are named. action=scene runs a saved scene or a household routine ("movie mode") '
        "by name. Use the names as status returns them, and say briefly what you changed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "set", "scene"]},
            "target": {"type": "string", "description": "Room, light or plug name, or 'all' (for set; the default)."},
            "on": {"type": "boolean", "description": "Turn on (true) or off (false)."},
            "brightness": {"type": "integer", "minimum": 1, "maximum": 100},
            "warmth": {"type": "string", "enum": ["warm", "neutral", "cool"]},
            "color": {"type": "string", "description": "A color name, 'white', or #rrggbb."},
            "scene": {"type": "string", "description": "Scene name (for scene)."},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        *,
        tv: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        spotify: Callable[..., Awaitable[Any]] | None = None,
        layout_file: Path | None = None,
    ) -> None:
        """Use the ``tv`` and ``spotify`` tools for routines (the real ones unless given) and ``layout_file``."""
        self._tv_tool = tv
        self._spotify_tool = spotify
        self._layout_file = layout_file

    def spec(self) -> ToolSpec:
        """Offer the model the household routines as they are now: each conversation reads them afresh."""
        spec = super().spec()
        from reachy_mini_conversation_app.config import config

        layout = HomeLayout.load(self._layout_file or Path(config.HOME_LAYOUT_FILE))
        if layout.routines:
            listed = "; ".join(routine_line(routine) for routine in layout.routines)
            spec = {
                **spec,
                "description": f"{spec['description']} Household routines (run one with action=scene and its name, "
                f"when someone asks for it in any words): {listed}.",
            }
        return spec

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Read or change lights.

        ``lights_only=True`` (not offered to the model) runs only a routine's lights: the quick commands and the
        admin app turn the TV off, pause the music and put Reachy to sleep themselves.
        """
        home = deps.home_hub
        if home is None:
            return {"error": "No lights are set up."}
        action = kwargs.get("action")
        logger.info("Tool call: home_lights action=%s", action)
        games = getattr(deps, "games", None)
        if action != "status" and getattr(games, "active", False) is True:
            return {"error": "A game is on; the lights wait until it ends ('se acabó el juego')."}
        try:
            if action == "status":
                return await self._status(home)
            if action == "scene":
                return await self._scene(
                    deps,
                    home,
                    str(kwargs.get("scene") or kwargs.get("target") or ""),
                    lights_only=kwargs.get("lights_only") is True,
                )
            if action == "set":
                return await self._set(home, kwargs)
        except HomeDeviceError as error:
            logger.warning("home_lights failed: %s", error)
            return {"error": f"The lights did not respond: {error}"}
        return {"error": "action must be status, set or scene"}

    async def _status(self, home: Any) -> dict[str, Any]:
        devices, scenes = await asyncio.gather(home.devices(), home.scenes())
        rooms = sorted({device.room for device in devices if device.room})
        areas = sorted({area for device in devices for area in device.areas})
        return {
            "rooms": rooms,
            "areas": areas,
            "devices": [_describe(device) for device in without_offline_duplicates(devices)],
            "scenes": [scene.name for scene in scenes] + [routine.name for routine in _layout(home).routines],
        }

    async def _scene(self, deps: ToolDependencies, home: Any, name: str, *, lights_only: bool) -> dict[str, Any]:
        routine = find_routine(_layout(home), name)
        if routine is not None:
            if routine.on or routine.off:
                result = await self._routine(home, routine)
            else:
                result = {"ok": True, "routine": routine.name}
            if not lights_only and "error" not in result:
                await self._routine_extras(deps, routine, result)
            return result
        scenes = await home.scenes()
        wanted = normalize_name(name)
        matches = [scene for scene in scenes if normalize_name(scene.name) == wanted] or [
            scene for scene in scenes if wanted and wanted in normalize_name(scene.name)
        ]
        if len(matches) != 1:
            return {
                "error": f"No single scene or routine called {name!r}.",
                "scenes": [scene.name for scene in scenes] + [routine.name for routine in _layout(home).routines],
            }
        await home.trigger_scene(matches[0])
        return {"ok": True, "scene": matches[0].name}

    async def _set(self, home: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        # No target is plain "the lights", as the quick command reads it (2026-10-30: "turn the light off"
        # through Gemini came without one and was refused).
        target = str(kwargs.get("target") or "").strip() or "all"
        on = kwargs.get("on")
        brightness = kwargs.get("brightness")
        warmth = kwargs.get("warmth")
        color_name = kwargs.get("color")
        if on is None and brightness is None and warmth is None and not color_name:
            logger.info("home_lights set refused: no change given")
            return {"error": "set needs on, brightness, warmth or color."}
        if warmth is not None and warmth not in _WARMTH_POSITION:
            logger.info("home_lights set refused: unknown warmth")
            return {"error": "warmth must be warm, neutral or cool."}
        color = None
        if color_name:
            if str(color_name).strip().lower() == "white":
                warmth = warmth or "neutral"  # white light, not a color
            else:
                color = _parse_color(str(color_name))
                if color is None:
                    logger.info("home_lights set refused: unknown color")
                    return {"error": f"Unknown color {color_name!r}; use a color name or #rrggbb."}
        change = LightChange(
            on=None if on is None else bool(on),
            brightness=None if brightness is None else max(1, min(100, int(brightness))),
            warmth=None if warmth is None else _WARMTH_POSITION[warmth],
            color=color,
        )

        devices = await home.devices()
        targets = _match_targets(devices, target)
        # Device names and the kind of change only: the target as said is the person's words.
        fields = ",".join(
            name
            for name, value in (("on", on), ("brightness", brightness), ("warmth", warmth), ("color", color_name))
            if value is not None and value != ""
        )
        if targets is None:
            logger.info("home_lights set matched nothing change=%s", fields)
            return {
                "error": f"Nothing called {target!r}.",
                "rooms": sorted({device.room for device in devices if device.room}),
                "names": [device.name for device in devices],
            }
        if normalize_name(target) in _EVERYTHING and change.on is not False:
            targets = _left_out(targets, _layout(home))  # "turn on the lights" skips the TV backlight
        changes = [
            (device, device_change)
            for device in targets
            if device.reachable and (device_change := change.for_device(device)) is not None
        ]
        response = await self._apply(home, changes, targets)
        notes = []
        if brightness is not None and any(not device.dims for device in targets):
            notes.append("Plugs only switch on and off.")
        if color is not None and any(device.dims and not device.colors for device in targets):
            notes.append("Some of these lights cannot show colors.")
        if warmth is not None and any(device.dims and not device.warmth and not device.colors for device in targets):
            notes.append("Some of these lights have fixed white.")
        if notes:
            response["note"] = " ".join(notes)
        logger.info(
            "home_lights set change=%s on=%s matched=%s changed=%s failed=%s unreachable=%s",
            fields,
            change.on,
            [device.name for device in targets],
            response.get("changed", []),
            response.get("failed", []),
            response.get("unreachable", []),
        )
        return response

    async def _routine(self, home: Any, routine: Routine) -> dict[str, Any]:
        """Turn the routine's lights on and the rest of its lights off, in one step."""
        devices = await home.devices()
        layout = _layout(home)

        def resolve(names: tuple[str, ...]) -> list[HubDevice]:
            found: dict[str, HubDevice] = {}
            for name in names:
                matched = _match_targets(devices, name)
                if matched is None:
                    logger.warning("Routine %r names nothing called %r", routine.name, name)
                    continue
                found.update((device.id, device) for device in matched)
            return list(found.values())

        on_names = tuple(name for name in routine.on if normalize_name(name) not in _EVERYTHING)
        turn_on = resolve(on_names)
        if len(on_names) < len(routine.on):  # "all" on leaves out the accent lights
            named = {device.id for device in turn_on}
            turn_on += [device for device in _left_out(resolve(("all",)), layout) if device.id not in named]
        on_ids = {device.id for device in turn_on}
        turn_off = [device for device in resolve(routine.off) if device.id not in on_ids]
        targets = turn_on + turn_off
        changes = [(device, LightChange(on=True)) for device in turn_on if device.reachable] + [
            (device, LightChange(on=False)) for device in turn_off if device.reachable
        ]
        response = await self._apply(home, changes, targets)
        response["routine"] = routine.name
        logger.info(
            "Routine %r: on=%d off=%d failed=%d",
            routine.name,
            sum(1 for _, change in changes if change.on),
            sum(1 for _, change in changes if change.on is False),
            len(response.get("failed", [])),
        )
        return response

    async def _routine_extras(self, deps: ToolDependencies, routine: Routine, result: dict[str, Any]) -> None:
        """Do the rest of a routine run from a conversation: the TV off, the music paused, then sleep."""
        if routine.tv_off and deps.tv is not None:
            from reachy_mini_conversation_app.tools.tv import TV

            tv = self._tv_tool or TV()

            async def tv_off() -> dict[str, Any]:
                return await tv(deps, action="power", on=False)

            task = asyncio.create_task(tv_off(), name="routine-tv-off")
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)
            task.add_done_callback(_log_tv_off)
            result["tv"] = "turning off"
        if routine.pause_music:
            from reachy_mini_conversation_app.tools.spotify_control import SpotifyDucker, SpotifyControl

            if self._spotify_tool is not None or SpotifyDucker._configuration() is not None:
                spotify = self._spotify_tool or SpotifyControl()
                playing = await spotify(deps, action="status")
                if playing.get("status") == "playing":
                    await spotify(deps, action="pause")
                    result["music"] = "paused"
        if routine.sleep:
            sleep = deps.standby or deps.go_to_sleep
            if sleep is not None:
                await asyncio.to_thread(sleep)
                result["sleep"] = True
                result["instruction"] = "This routine puts you to sleep: say a very short good night."
        logger.info("Routine %r run from a conversation", routine.name)

    async def _apply(
        self, home: Any, changes: list[tuple[HubDevice, LightChange]], targets: list[HubDevice]
    ) -> dict[str, Any]:
        results = await asyncio.gather(
            *(home.apply(device, device_change) for device, device_change in changes), return_exceptions=True
        )
        changed = [device.name for (device, _), result in zip(changes, results) if not isinstance(result, Exception)]
        failed = [device.name for (device, _), result in zip(changes, results) if isinstance(result, Exception)]
        response: dict[str, Any] = {"ok": bool(changed), "changed": changed}
        unreachable = [device.name for device in targets if not device.reachable]
        if unreachable:
            response["unreachable"] = unreachable
        for (device, _), result in zip(changes, results):
            if isinstance(result, Exception):
                logger.warning("home_lights: %s failed: %s: %s", device.name, type(result).__name__, result)
        if failed:
            response["failed"] = failed
        if failed or unreachable:
            # A device that failed once fails again; retrying only loops (seen live with a color bug). Name the
            # ones left out even when others changed: on 2026-10-28 "lights" lit the hub lights, skipped both
            # plugs, and Reachy said it was done.
            response["instruction"] = "Tell the person which lights did not respond. Do not retry this change."
        return response
