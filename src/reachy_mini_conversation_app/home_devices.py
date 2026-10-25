"""Lights and plugs from every home system (IKEA DIRIGERA, Tuya) behind one interface.

Each provider lists its devices as ``HubDevice`` and applies a provider-neutral ``LightChange``;
``HomeDevices`` merges them so the ``home_lights`` tool needs no system-specific code.
"""

from __future__ import annotations
import json
import asyncio
import logging
import unicodedata
from typing import Protocol
from pathlib import Path
from dataclasses import field, replace, dataclass


logger = logging.getLogger(__name__)


class HomeDeviceError(RuntimeError):
    """A home system could not be reached or rejected the request."""


@dataclass(frozen=True)
class HubDevice:
    """A light or plug as its system reports it."""

    id: str
    kind: str  # "light" or "outlet"
    name: str
    room: str | None
    is_on: bool
    reachable: bool
    brightness: int | None = None  # 1-100
    # Kelvin range, warmest first, when the light can change color temperature.
    kelvin_range: tuple[int, int] | None = None
    colors: bool = False  # can show any color
    source: str = "dirigera"
    areas: tuple[str, ...] = ()  # parts of a room ("Dining"), from the home layout
    # Only shows colors (an RGB strip): its system's white mode leaves it dark, so white is made as a color.
    color_only: bool = False

    @property
    def dims(self) -> bool:
        """Whether the device takes a brightness level."""
        return self.brightness is not None

    @property
    def warmth(self) -> bool:
        """Whether the device can be made warmer or cooler."""
        return self.kelvin_range is not None


@dataclass(frozen=True)
class HubScene:
    """A scene saved in a system's app."""

    id: str
    name: str
    source: str = "dirigera"


@dataclass(frozen=True)
class LightChange:
    """What to change; ``None`` leaves that property alone."""

    on: bool | None = None
    brightness: int | None = None  # 1-100
    warmth: float | None = None  # 0 = warmest, 1 = coolest
    color: tuple[float, float] | None = None  # (hue 0-360, saturation 0-1)

    def for_device(self, device: HubDevice) -> LightChange | None:
        """Keep only what ``device`` supports; a brightness, warmth or color alone also turns it on.

        A light that shows colors but has no white control gets warmth as a near-white color.
        """
        brightness = self.brightness if device.dims else None
        warmth = self.warmth if device.warmth else None
        color = self.color if device.colors else None
        if color is None and warmth is None and self.warmth is not None and device.colors:
            color = white_as_color(self.warmth)
        on = self.on
        if on is None and (brightness is not None or warmth is not None or color is not None):
            on = True
        if on is None and brightness is None and warmth is None and color is None:
            return None
        return LightChange(on=on, brightness=brightness, warmth=warmth, color=color)


class HomeProvider(Protocol):
    """One home system."""

    async def devices(self) -> list[HubDevice]:
        """List the lights and plugs."""
        ...

    async def scenes(self) -> list[HubScene]:
        """List saved scenes."""
        ...

    async def apply(self, device: HubDevice, change: LightChange) -> None:
        """Change one device."""
        ...

    async def trigger_scene(self, scene_id: str) -> None:
        """Run one scene."""
        ...

    async def close(self) -> None:
        """Release connections."""
        ...


def white_as_color(warmth: float) -> tuple[float, float]:
    """Return (hue, saturation) for white on an RGB-only light: warm is a pale amber, cool is plain white."""
    return (30.0, round(0.5 * (1 - max(0.0, min(1.0, warmth))), 3))


def normalize_name(text: str) -> str:
    """Casefold and strip accents so "Salón" matches "salon"."""
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(without_accents.casefold().split())


@dataclass(frozen=True)
class Routine:
    """A named set of lights to turn on and off at once ("movie mode").

    ``on`` and ``off`` hold targets as the ``home_lights`` tool takes them (a device, room or area name,
    or "all"); "all" in ``off`` means every light not in ``on``. ``sleep`` also puts Reachy to sleep,
    ``tv_off`` turns the TV off and ``pause_music`` pauses Spotify (JSON: ``"tv": "off"``, ``"music": "pause"``).
    """

    say: tuple[str, ...]
    on: tuple[str, ...] = ()
    off: tuple[str, ...] = ()
    sleep: bool = False
    tv_off: bool = False
    pause_music: bool = False

    @property
    def name(self) -> str:
        """The first phrase, which names the routine."""
        return self.say[0]


def _routines(raw: object) -> tuple[Routine, ...]:
    routines: list[Routine] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        say = tuple(str(phrase) for phrase in item.get("say") or () if str(phrase).strip())
        on = tuple(str(name) for name in item.get("on") or ())
        off = tuple(str(name) for name in item.get("off") or ())
        tv_off = str(item.get("tv") or "").casefold() == "off"
        pause_music = str(item.get("music") or "").casefold() in ("pause", "stop")
        if say and (on or off or tv_off or pause_music):
            routines.append(
                Routine(
                    say=say,
                    on=on,
                    off=off,
                    sleep=bool(item.get("sleep")),
                    tv_off=tv_off,
                    pause_music=pause_music,
                )
            )
        else:
            logger.warning('Home layout routine ignored: it needs "say" and something to do')
    return tuple(routines)


@dataclass(frozen=True)
class HomeLayout:
    """Household-specific grouping the systems do not store: rooms for devices without one, and areas.

    Read from a JSON file such as
    ``{"rooms": {"TV Lights": "Living room"}, "areas": {"Dining": ["Dining light 1", "Dining lamp"]}}``;
    device names match without case or accents. Optional: ``"left_out_of_all_on": ["TV Lights"]``
    (lights that "turn on the lights" skips; turning everything off still includes them),
    ``"color_only": ["TV Lights"]`` (RGB lights whose white mode is dark; see ``HubDevice.color_only``) and
    ``"routines"`` (see ``Routine``), e.g.
    ``[{"say": ["movie mode", "modo cine"], "on": ["TV Lights"], "off": ["all"]}]``.
    """

    rooms: dict[str, str] = field(default_factory=dict)
    areas: dict[str, tuple[str, ...]] = field(default_factory=dict)
    left_out_of_all_on: frozenset[str] = frozenset()
    color_only: frozenset[str] = frozenset()
    routines: tuple[Routine, ...] = ()

    @classmethod
    def load(cls, path: Path) -> HomeLayout:
        """Read the layout file; a missing or invalid file is an empty layout."""
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as error:
            logger.warning("Home layout unreadable: %s", type(error).__name__)
            return cls()
        if not isinstance(raw, dict):
            return cls()
        rooms = raw.get("rooms")
        areas = raw.get("areas")
        return cls(
            rooms={normalize_name(str(k)): str(v) for k, v in (rooms or {}).items()},
            areas={str(area): tuple(normalize_name(str(n)) for n in names) for area, names in (areas or {}).items()},
            left_out_of_all_on=frozenset(normalize_name(str(n)) for n in raw.get("left_out_of_all_on") or ()),
            color_only=frozenset(normalize_name(str(n)) for n in raw.get("color_only") or ()),
            routines=_routines(raw.get("routines")),
        )

    def apply(self, device: HubDevice) -> HubDevice:
        """Give ``device`` its layout room (if its system has none), its areas, and mark it color-only."""
        key = normalize_name(device.name)
        room = device.room or self.rooms.get(key)
        areas = tuple(area for area, names in self.areas.items() if key in names)
        color_only = device.color_only or (key in self.color_only and device.colors)
        if room == device.room and areas == device.areas and color_only == device.color_only:
            return device
        if color_only:
            # Its system may claim a white mode (the TV strip reports a color temperature); offering it would go dark.
            return replace(device, room=room, areas=areas, color_only=True, kelvin_range=None)
        return replace(device, room=room, areas=areas)


class HomeDevices:
    """Every configured home system, merged."""

    def __init__(self, providers: dict[str, HomeProvider], layout: HomeLayout | None = None) -> None:
        """Use ``providers`` keyed by the ``source`` their devices carry, grouped by ``layout``."""
        self._providers = providers
        self._layout = layout or HomeLayout()

    @property
    def layout(self) -> HomeLayout:
        """The household layout (areas, routines, lights left out of "all on")."""
        return self._layout

    async def devices(self) -> list[HubDevice]:
        """List every system's devices; a system that fails is skipped unless all fail."""
        results = await asyncio.gather(*(p.devices() for p in self._providers.values()), return_exceptions=True)
        devices: list[HubDevice] = []
        errors: list[BaseException] = []
        for source, result in zip(self._providers, results):
            if isinstance(result, BaseException):
                logger.warning("Home devices from %s unavailable: %s", source, result)
                errors.append(result)
            else:
                devices.extend(result)
        if errors and not devices:
            raise HomeDeviceError(str(errors[0]))
        return [self._layout.apply(device) for device in devices]

    async def scenes(self) -> list[HubScene]:
        """List every system's scenes, skipping systems that fail."""
        results = await asyncio.gather(*(p.scenes() for p in self._providers.values()), return_exceptions=True)
        return [scene for result in results if not isinstance(result, BaseException) for scene in result]

    async def apply(self, device: HubDevice, change: LightChange) -> None:
        """Change one device through its own system."""
        await self._providers[device.source].apply(device, change)

    async def trigger_scene(self, scene: HubScene) -> None:
        """Run a scene in its own system."""
        await self._providers[scene.source].trigger_scene(scene.id)

    async def close(self) -> None:
        """Release every system's connections."""
        await asyncio.gather(*(p.close() for p in self._providers.values()), return_exceptions=True)
