import asyncio
import logging
from typing import Any

from reachy_mini_conversation_app.home_devices import HubDevice, LightChange, HomeDeviceError, normalize_name
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

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


def _without_offline_duplicates(devices: list[HubDevice]) -> list[HubDevice]:
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
    devices = _without_offline_duplicates(devices)
    wanted = normalize_name(target)
    if wanted in _EVERYTHING:
        return devices
    rooms = [device for device in devices if device.room and normalize_name(device.room) == wanted]
    if rooms:
        return rooms
    areas = [device for device in devices if any(_core(area) == _core(target) for area in device.areas)]
    if areas:
        return areas
    exact = [device for device in devices if normalize_name(device.name) == wanted]
    if exact:
        return exact
    partial = [
        device
        for device in devices
        if wanted in normalize_name(device.name) or (device.room and wanted in normalize_name(device.room))
    ]
    return partial or None


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
        "or 'all' (also for plain \"the lights\"). Give on=true/false, "
        "brightness 1-100, warmth (warm, neutral, cool) for white light, and/or color (red, orange, yellow, "
        "green, cyan, blue, purple, pink, white, or #rrggbb) for lights that show colors. Brightness, warmth "
        "or color alone also turns the light on. action=scene runs a saved scene by name. Use the names as "
        "status returns them, and say briefly what you changed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "set", "scene"]},
            "target": {"type": "string", "description": "Room, light or plug name, or 'all' (for set)."},
            "on": {"type": "boolean", "description": "Turn on (true) or off (false)."},
            "brightness": {"type": "integer", "minimum": 1, "maximum": 100},
            "warmth": {"type": "string", "enum": ["warm", "neutral", "cool"]},
            "color": {"type": "string", "description": "A color name, 'white', or #rrggbb."},
            "scene": {"type": "string", "description": "Scene name (for scene)."},
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Read or change lights."""
        home = deps.home_hub
        if home is None:
            return {"error": "No lights are set up."}
        action = kwargs.get("action")
        logger.info("Tool call: home_lights action=%s", action)
        try:
            if action == "status":
                return await self._status(home)
            if action == "scene":
                return await self._scene(home, str(kwargs.get("scene") or kwargs.get("target") or ""))
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
            "devices": [_describe(device) for device in _without_offline_duplicates(devices)],
            "scenes": [scene.name for scene in scenes],
        }

    async def _scene(self, home: Any, name: str) -> dict[str, Any]:
        scenes = await home.scenes()
        wanted = normalize_name(name)
        matches = [scene for scene in scenes if normalize_name(scene.name) == wanted] or [
            scene for scene in scenes if wanted and wanted in normalize_name(scene.name)
        ]
        if len(matches) != 1:
            return {"error": f"No single scene called {name!r}.", "scenes": [scene.name for scene in scenes]}
        await home.trigger_scene(matches[0])
        return {"ok": True, "scene": matches[0].name}

    async def _set(self, home: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        target = str(kwargs.get("target") or "").strip()
        on = kwargs.get("on")
        brightness = kwargs.get("brightness")
        warmth = kwargs.get("warmth")
        color_name = kwargs.get("color")
        if not target:
            return {"error": "set needs a target: a room, a light or plug name, or 'all'."}
        if on is None and brightness is None and warmth is None and not color_name:
            return {"error": "set needs on, brightness, warmth or color."}
        if warmth is not None and warmth not in _WARMTH_POSITION:
            return {"error": "warmth must be warm, neutral or cool."}
        color = None
        if color_name:
            if str(color_name).strip().lower() == "white":
                warmth = warmth or "neutral"  # white light, not a color
            else:
                color = _parse_color(str(color_name))
                if color is None:
                    return {"error": f"Unknown color {color_name!r}; use a color name or #rrggbb."}
        change = LightChange(
            on=None if on is None else bool(on),
            brightness=None if brightness is None else max(1, min(100, int(brightness))),
            warmth=None if warmth is None else _WARMTH_POSITION[warmth],
            color=color,
        )

        devices = await home.devices()
        targets = _match_targets(devices, target)
        if targets is None:
            return {
                "error": f"Nothing called {target!r}.",
                "rooms": sorted({device.room for device in devices if device.room}),
                "names": [device.name for device in devices],
            }
        changes = [
            (device, device_change)
            for device in targets
            if device.reachable and (device_change := change.for_device(device)) is not None
        ]
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
        if failed or (unreachable and not changed):
            # A device that failed once fails again; retrying only loops (seen live with a color bug).
            response["instruction"] = "Tell the person which lights did not respond. Do not retry this change."
        notes = []
        if brightness is not None and any(not device.dims for device in targets):
            notes.append("Plugs only switch on and off.")
        if color is not None and any(device.dims and not device.colors for device in targets):
            notes.append("Some of these lights cannot show colors.")
        if warmth is not None and any(device.dims and not device.warmth for device in targets):
            notes.append("Some of these lights have fixed white.")
        if notes:
            response["note"] = " ".join(notes)
        return response
