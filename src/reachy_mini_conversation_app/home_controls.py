"""The admin app's home controls: lights and plugs, routines, the TV and Spotify, as the phone shows them.

Every change goes through the tools Reachy uses by voice (``home_lights``, ``tv``, ``spotify_control``), so the
phone and his voice behave alike (a game on still holds the lights and music). Run it on the runtime's loop:
the TV's and the IKEA hub's connections belong to it.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Final
from pathlib import Path
from collections.abc import Callable, Awaitable

from reachy_mini_conversation_app.tools.tv import TV
from reachy_mini_conversation_app.now_playing import Playback, spotify_fetcher
from reachy_mini_conversation_app.home_devices import (
    Routine,
    HubDevice,
    HomeLayout,
    HomeDeviceError,
    routine_json,
    save_routines,
    normalize_name,
    routines_version,
)
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
MAX_ROUTINES: Final[int] = 30
MAX_PHRASES: Final[int] = 8
MAX_PHRASE_CHARS: Final[int] = 60
MAX_TARGETS: Final[int] = 40
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
        layout_file: Path | None = None,
    ) -> None:
        """Use ``deps``' home hub and TV, Spotify when it is set up, and the routines in ``layout_file``."""
        from reachy_mini_conversation_app.config import config

        self._deps = deps
        self._layout_file = layout_file or Path(config.HOME_LAYOUT_FILE)
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
            result = await self._lights(self._deps, action="scene", scene=wanted, lights_only=True)
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

    async def _devices_and_scenes(self, home: Any) -> tuple[list[HubDevice], list[str]]:
        try:
            devices, scenes = await asyncio.wait_for(asyncio.gather(home.devices(), home.scenes()), LIGHTS_TIMEOUT_S)
        except (HomeDeviceError, TimeoutError) as error:
            logger.warning("Home controls: lights unavailable for routines (%s)", type(error).__name__)
            return [], []
        return without_offline_duplicates(devices), [scene.name for scene in scenes]

    async def routines(self) -> dict[str, Any]:
        """Return the routines as the layout file holds them, what they can name, and a version to save against."""
        home = self._deps.home_hub
        if home is None:
            return {"error": "No lights are set up."}
        layout = await asyncio.to_thread(HomeLayout.load, self._layout_file)
        devices, scenes = await self._devices_and_scenes(home)
        return {
            "routines": [routine_json(routine) for routine in layout.routines],
            "version": routines_version(layout.routines),
            "targets": {
                "areas": list(layout.areas),
                "rooms": sorted({device.room for device in devices if device.room}),
                "devices": [device.name for device in devices],
            },
            "scenes": scenes,
        }

    async def save_routines(self, payload: dict[str, object]) -> dict[str, Any]:
        """Replace every routine with ``payload["routines"]`` if nobody changed them since ``payload["version"]``.

        The layout file is rewritten (its previous copy kept as ``.bak``) and loaded again, so the voice and the
        phone use the new routines at once.
        """
        home = self._deps.home_hub
        if home is None:
            return {"error": "No lights are set up."}
        current = await asyncio.to_thread(HomeLayout.load, self._layout_file)
        if payload.get("version") != routines_version(current.routines):
            return {"error": "The routines changed since you opened them. Close and open them again."}
        raw = payload.get("routines")
        if not isinstance(raw, list) or len(raw) > MAX_ROUTINES:
            return {"error": f"Send at most {MAX_ROUTINES} routines."}
        devices, _ = await self._devices_and_scenes(home)
        known = {"all", *(normalize_name(area) for area in current.areas)}
        known |= {normalize_name(name) for device in devices for name in (device.name, device.room or "") if name}
        known |= {normalize_name(name) for routine in current.routines for name in (*routine.on, *routine.off)}
        routines: list[Routine] = []
        said: dict[str, str] = {}
        for item in raw:
            routine = _routine(item, known)
            if isinstance(routine, str):
                return {"error": routine}
            for phrase in routine.say:
                key = normalize_name(phrase)
                if key in said:
                    return {"error": f"“{phrase}” starts two routines ({said[key]} and {routine.name})."}
                said[key] = routine.name
            routines.append(routine)
        await asyncio.to_thread(save_routines, self._layout_file, routines)
        layout = await asyncio.to_thread(HomeLayout.load, self._layout_file)
        home.set_layout(layout)
        logger.info("Routines saved from the admin app: %d", len(layout.routines))
        return {"ok": True, "version": routines_version(layout.routines), "count": len(layout.routines)}


def _routine(item: object, known: set[str]) -> Routine | str:
    """Return one routine from the phone, or why it cannot be saved."""
    if not isinstance(item, dict):
        return "A routine is not readable."
    phrases: list[str] = []
    raw_say = item.get("say")
    for phrase in raw_say if isinstance(raw_say, list) else []:
        text = " ".join(phrase.split()) if isinstance(phrase, str) else ""
        if text and normalize_name(text) not in {normalize_name(p) for p in phrases}:
            phrases.append(text)
    if not phrases:
        return "Every routine needs a name."
    name = phrases[0]
    if len(phrases) > MAX_PHRASES or any(len(phrase) > MAX_PHRASE_CHARS for phrase in phrases):
        return f"{name}: at most {MAX_PHRASES} phrases of {MAX_PHRASE_CHARS} characters."
    targets: dict[str, tuple[str, ...]] = {}
    for side in ("on", "off"):
        raw_side = item.get(side)
        values = raw_side if isinstance(raw_side, list) else []
        names = tuple(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
        if len(names) > MAX_TARGETS:
            return f"{name}: too many lights."
        unknown = [target for target in names if normalize_name(target) not in known]
        if unknown:
            return f"{name}: nothing is called {unknown[0]}."
        targets[side] = names
    routine = Routine(
        say=tuple(phrases),
        on=targets["on"],
        off=targets["off"],
        sleep=item.get("sleep") is True,
        tv_off=item.get("tv") == "off",
        pause_music=item.get("music") == "pause",
    )
    if not (routine.on or routine.off or routine.tv_off or routine.pause_music):
        return f"{name} doesn't do anything yet: pick lights, the TV or the music."
    return routine
