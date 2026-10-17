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
        """Keep only what ``device`` supports; a brightness, warmth or color alone also turns it on."""
        brightness = self.brightness if device.dims else None
        warmth = self.warmth if device.warmth else None
        color = self.color if device.colors else None
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


def normalize_name(text: str) -> str:
    """Casefold and strip accents so "Salón" matches "salon"."""
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(without_accents.casefold().split())


@dataclass(frozen=True)
class HomeLayout:
    """Household-specific grouping the systems do not store: rooms for devices without one, and areas.

    Read from a JSON file such as
    ``{"rooms": {"TV Lights": "Living room"}, "areas": {"Dining": ["Dining light 1", "Dining lamp"]}}``;
    device names match without case or accents.
    """

    rooms: dict[str, str] = field(default_factory=dict)
    areas: dict[str, tuple[str, ...]] = field(default_factory=dict)

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
        rooms = raw.get("rooms") if isinstance(raw, dict) else None
        areas = raw.get("areas") if isinstance(raw, dict) else None
        return cls(
            rooms={normalize_name(str(k)): str(v) for k, v in (rooms or {}).items()},
            areas={str(area): tuple(normalize_name(str(n)) for n in names) for area, names in (areas or {}).items()},
        )

    def apply(self, device: HubDevice) -> HubDevice:
        """Give ``device`` its layout room (if its system has none) and its areas."""
        key = normalize_name(device.name)
        room = device.room or self.rooms.get(key)
        areas = tuple(area for area, names in self.areas.items() if key in names)
        if room == device.room and areas == device.areas:
            return device
        return replace(device, room=room, areas=areas)


class HomeDevices:
    """Every configured home system, merged."""

    def __init__(self, providers: dict[str, HomeProvider], layout: HomeLayout | None = None) -> None:
        """Use ``providers`` keyed by the ``source`` their devices carry, grouped by ``layout``."""
        self._providers = providers
        self._layout = layout or HomeLayout()

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
