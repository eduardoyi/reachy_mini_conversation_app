"""The admin app's home controls: lights and plugs, routines, the TV and Spotify, as the phone shows them.

Every change goes through the tools Reachy uses by voice (``home_lights``, ``tv``, ``spotify_control``), so the
phone and his voice behave alike (a game on still holds the lights and music). Run it on the runtime's loop:
the TV's and the IKEA hub's connections belong to it.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Final
from collections.abc import Callable, Awaitable

from reachy_mini_conversation_app.tools.tv import TV
from reachy_mini_conversation_app.now_playing import Playback, spotify_fetcher
from reachy_mini_conversation_app.home_devices import HubDevice, HomeDeviceError
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.home_lights import HomeLights, find_routine, without_offline_duplicates
from reachy_mini_conversation_app.tools.spotify_control import SpotifyDucker, SpotifyControl


logger = logging.getLogger(__name__)

# A Tuya device that is unplugged takes up to 6 s to give up on (tuya_devices.REACH_DEADLINE_S).
LIGHTS_TIMEOUT_S: Final[float] = 8.0
TV_TIMEOUT_S: Final[float] = 3.0
MUSIC_TIMEOUT_S: Final[float] = 4.0
# The apps the phone offers as buttons (names ``tools.tv`` opens).
TV_APPS: Final[tuple[str, ...]] = ("YouTube", "YouTube Kids", "Netflix", "Disney Plus", "Prime Video", "Spotify")
TV_ACTIONS: Final[frozenset[str]] = frozenset({"power", "volume", "mute", "media", "navigate", "open", "youtube"})
MUSIC_ACTIONS: Final[frozenset[str]] = frozenset({"resume", "pause", "next", "volume"})
_WARMTHS: Final[frozenset[str]] = frozenset({"warm", "neutral", "cool"})
_MAX_TEXT: Final[int] = 100

Tool = Callable[..., Awaitable[dict[str, Any]]]


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: object) -> str | None:
    return value.strip()[:_MAX_TEXT] if isinstance(value, str) and value.strip() else None


def _device(device: HubDevice) -> dict[str, Any]:
    return {
        "name": device.name,
        "kind": "plug" if device.kind == "outlet" else "light",
        "room": device.room,
        "areas": list(device.areas),
        "on": device.is_on,
        "reachable": device.reachable,
        "brightness": device.brightness,
        "warmth": device.warmth,
        "colors": device.colors,
    }


class HomeControls:
    """Read and change the home's devices for the phone; ``lights``, ``tv``, ``spotify`` and ``playback`` are fakes in tests."""

    def __init__(
        self,
        deps: ToolDependencies,
        *,
        lights: Tool | None = None,
        tv: Tool | None = None,
        spotify: Tool | None = None,
        playback: Callable[[], Awaitable[Playback | None]] | None = None,
    ) -> None:
        """Use ``deps``' home hub and TV, and Spotify when it is set up."""
        self._deps = deps
        self._lights: Tool = lights or HomeLights()
        self._tv: Tool = tv or TV()
        self._spotify: Tool = spotify or SpotifyControl()
        self._playback = playback

    def _music_set_up(self) -> bool:
        return self._playback is not None or SpotifyDucker._configuration() is not None

    async def status(self) -> dict[str, Any]:
        """Return each part's state (None when it is not set up, ``{"error"}`` when it did not answer)."""
        lights, tv, music = await asyncio.gather(self._lights_status(), self._tv_status(), self._music_status())
        return {"lights": lights, "tv": tv, "music": music}

    async def _lights_status(self) -> dict[str, Any] | None:
        home = self._deps.home_hub
        if home is None:
            return None
        try:
            devices, scenes = await asyncio.wait_for(asyncio.gather(home.devices(), home.scenes()), LIGHTS_TIMEOUT_S)
        except (HomeDeviceError, TimeoutError) as error:
            logger.warning("Home controls: lights unavailable (%s)", type(error).__name__)
            return {"error": "The lights didn't answer."}
        layout = home.layout
        return {
            "devices": [_device(device) for device in without_offline_duplicates(devices)],
            "areas": list(layout.areas),
            "routines": [routine.name for routine in layout.routines] + [scene.name for scene in scenes],
        }

    async def _tv_status(self) -> dict[str, Any] | None:
        if self._deps.tv is None:
            return None
        try:
            state = await asyncio.wait_for(self._tv(self._deps, action="status"), TV_TIMEOUT_S)
        except TimeoutError:
            state = {"error": "timeout"}
        if "error" in state:
            logger.info("Home controls: TV unavailable")
            return {"error": "The TV didn't answer.", "apps": list(TV_APPS)}
        return {**state, "apps": list(TV_APPS)}

    async def _music_status(self) -> dict[str, Any] | None:
        if not self._music_set_up():
            return None
        fetch = self._playback
        if fetch is None:
            configuration = SpotifyDucker._configuration()
            assert configuration is not None
            fetch = spotify_fetcher(*configuration)
        try:
            playback = await asyncio.wait_for(fetch(), MUSIC_TIMEOUT_S)
        except Exception as error:
            logger.warning("Home controls: Spotify unavailable (%s)", type(error).__name__)
            return {"error": "Spotify didn't answer."}
        if playback is None:
            return {"playing": False, "title": None}
        return {
            "playing": playback.playing,
            "title": playback.title,
            "artist": playback.artist,
            "art": playback.art_url,
            "volume": playback.volume,
        }

    async def light(self, payload: dict[str, object]) -> dict[str, Any]:
        """Switch, dim, warm or color a light, plug, area or room (``target``), as the voice tool does."""
        target = _text(payload.get("target"))
        if target is None:
            return {"error": "Say which light."}
        change: dict[str, Any] = {}
        if isinstance(payload.get("on"), bool):
            change["on"] = payload["on"]
        brightness = _int(payload.get("brightness"))
        if brightness is not None:
            change["brightness"] = max(1, min(100, brightness))
        if payload.get("warmth") in _WARMTHS:
            change["warmth"] = payload["warmth"]
        color = _text(payload.get("color"))
        if color is not None:
            change["color"] = color
        if not change:
            return {"error": "Nothing to change."}
        return await self._lights(self._deps, action="set", target=target, **change)

    async def all_off(self) -> dict[str, Any]:
        """Turn every light and plug off."""
        return await self._lights(self._deps, action="set", target="all", on=False)

    async def routine(self, name: object) -> dict[str, Any]:
        """Run a household routine or a saved scene; ``"sleep": True`` in the answer when he should sleep too."""
        home = self._deps.home_hub
        wanted = _text(name)
        if home is None or wanted is None:
            return {"error": "No such routine."}
        routine = find_routine(home.layout, wanted)
        if routine is not None and not routine.on and not routine.off:
            result: dict[str, Any] = {"ok": True, "routine": routine.name}
        else:
            result = await self._lights(self._deps, action="scene", scene=wanted)
        if routine is None or "error" in result:
            return result
        if routine.tv_off and self._deps.tv is not None:
            answer = await self._tv(self._deps, action="power", on=False)
            if "error" in answer:
                logger.info("Routine %r: TV not turned off", routine.name)
        if routine.pause_music and self._music_set_up():
            playing = await self._spotify(self._deps, action="status")
            if playing.get("status") == "playing":
                await self._spotify(self._deps, action="pause")
        return {**result, "sleep": routine.sleep}

    async def tv(self, payload: dict[str, object]) -> dict[str, Any]:
        """Send one remote command (power, volume, mute, media, navigate, open, youtube)."""
        action = payload.get("action")
        if action not in TV_ACTIONS:
            return {"error": "Unknown TV command."}
        command: dict[str, Any] = {"action": action}
        for name in ("on", "muted"):
            if isinstance(payload.get(name), bool):
                command[name] = payload[name]
        for name in ("level", "steps", "repeat"):
            if (number := _int(payload.get(name))) is not None:
                command[name] = number
        for name in ("change", "key", "app", "query"):
            if (text := _text(payload.get(name))) is not None:
                command[name] = text
        answer = await self._tv(self._deps, **command)
        answer.pop("instruction", None)
        return answer

    async def music(self, payload: dict[str, object]) -> dict[str, Any]:
        """Resume, pause, skip or set the volume of Spotify on Reachy's speaker."""
        action = payload.get("action")
        if action not in MUSIC_ACTIONS or not self._music_set_up():
            return {"error": "Unknown music command." if action not in MUSIC_ACTIONS else "Spotify isn't set up."}
        command: dict[str, Any] = {"action": action}
        if action == "volume":
            command["volume_percent"] = _int(payload.get("volume"))
        return await self._spotify(self._deps, **command)
