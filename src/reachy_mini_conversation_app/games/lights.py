"""Game lights: the TV strip glows and flashes at a game's big moments, the room dims to set the mood.

Two lanes that never wait on each other:

- **Color:** lights that show any color (today the TV strip) glow or flash in a color, then go back as they were.
- **Room:** dimmable white lights that are on when the lane first acts (the ceiling lights) dim to a mood held for
  the game ("show", "cozy", "tense") or pulse for a winner ("party"). Lights that are off stay off: the room is
  never lit up by a game.

The first effect in each lane saves its lights as their systems report them (on, brightness and color); a pause and
the end of the game put both lanes back. Effects run in the background, one at a time per lane (a newer one
replaces one still running once its current step is done), and a light that fails is logged and skipped.
"""

import asyncio
import logging
from typing import Callable, Awaitable

from reachy_mini_conversation_app.home_devices import HubDevice, HomeDevices, LightChange, HomeDeviceError
from reachy_mini_conversation_app.tools.home_lights import LIGHT_COLORS


logger = logging.getLogger(__name__)

GLOW_S = 3.0
FLASHES = 3
FLASH_ON_S = 0.5
FLASH_DIM_S = 0.4
FLASH_DIM_BRIGHTNESS = 15  # a flash dims rather than switching off: the strip's on/off is slow and quirky
# Room moods, as a share of each light's own brightness before the game.
MOODS: dict[str, float] = {"show": 0.45, "cozy": 0.3, "tense": 0.15}
PARTY_PULSES = 3
PARTY_BRIGHT_S = 0.45
PARTY_LOW_S = 0.35
PARTY_LOW = 0.25


class _Lane:
    """One group of lights: saved on first use, changed one effect at a time, put back on restore."""

    def __init__(self, home: HomeDevices, name: str, takes_part: Callable[[HubDevice], bool]) -> None:
        self.home = home
        self.name = name
        self._takes_part = takes_part
        self.saved: list[HubDevice] | None = None
        self.generation = 0
        self._task: asyncio.Task[None] | None = None

    def start(self, kind: str, work: Callable[[int], Awaitable[None]] | None) -> None:
        """Run ``work`` after the lane's last effect (None: put the lights back)."""
        self.generation += 1
        self._task = asyncio.get_running_loop().create_task(
            self._run(self._task, self.generation, kind, work), name=f"game-lights-{self.name}"
        )

    async def done(self) -> None:
        """Wait for the lane's effects so far."""
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(
        self,
        previous: asyncio.Task[None] | None,
        generation: int,
        kind: str,
        work: Callable[[int], Awaitable[None]] | None,
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)  # never two steps on a light at once
        try:
            if work is None:
                if self.saved is not None:
                    await self.put_back()
                    self.saved = None
            elif generation == self.generation:
                if self.saved is None:
                    self.saved = [device for device in await self.home.devices() if self._takes_part(device)]
                if not self.saved:
                    logger.info("Game lights: no %s light to %s", self.name, kind)
                    return
                logger.info("Game lights: %s on %d %s light(s)", kind, len(self.saved), self.name)
                await work(generation)
        except (HomeDeviceError, TimeoutError) as error:
            logger.warning("Game lights: %s failed (%s)", kind, type(error).__name__)

    async def set(self, change: Callable[[HubDevice], LightChange]) -> None:
        devices = self.saved or []
        results = await asyncio.gather(*(self.home.apply(d, change(d)) for d in devices), return_exceptions=True)
        for device, result in zip(devices, results):
            if isinstance(result, BaseException):
                logger.warning("Game lights: %s did not change (%s)", device.name, type(result).__name__)

    async def put_back(self) -> None:
        """Each light as saved: its color first (that also lights it), then off again if it was off."""
        for device in self.saved or []:
            try:
                if device.color is not None:
                    await self.home.apply(device, LightChange(brightness=device.brightness, color=device.color))
                    if not device.is_on:
                        await self.home.apply(device, LightChange(on=False))
                else:
                    on = device.is_on
                    await self.home.apply(device, LightChange(on=on, brightness=device.brightness if on else None))
            except HomeDeviceError as error:
                logger.warning("Game lights: %s not put back (%s)", device.name, type(error).__name__)


def _share(device: HubDevice, share: float) -> LightChange:
    """``device`` at ``share`` of the brightness it had before the game (at least 1%)."""
    return LightChange(brightness=max(1, round((device.brightness or 100) * share)))


class GameLights:
    """Glow and flash the color lights, set the room's mood, and put everything back."""

    def __init__(self, home: HomeDevices) -> None:
        """Use ``home``'s color lights, and its dimmable white lights that are on."""
        self._color = _Lane(home, "color", lambda d: d.colors and d.reachable)
        self._room = _Lane(
            home, "room", lambda d: d.dims and not d.colors and d.reachable and d.is_on and d.kind == "light"
        )
        self._mood: float | None = None  # the room's held mood, which a party pulse returns to

    def effect(self, kind: str, color: str) -> None:
        """Start ``kind`` ("glow" for 3 s, or three "flash"es) in ``color`` (a name in ``LIGHT_COLORS``)."""
        if kind not in ("glow", "flash") or color not in LIGHT_COLORS:
            logger.warning("Game lights: no %s effect in %s", kind, color)
            return
        hue = LIGHT_COLORS[color]
        lane = self._color

        async def play(generation: int) -> None:
            bright = LightChange(on=True, brightness=100, color=hue)
            if kind == "glow":
                await lane.set(lambda d: bright)
                await asyncio.sleep(GLOW_S)
            else:
                for _ in range(FLASHES):
                    if generation != lane.generation:
                        break
                    await lane.set(lambda d: bright)
                    await asyncio.sleep(FLASH_ON_S)
                    await lane.set(lambda d: LightChange(on=True, brightness=FLASH_DIM_BRIGHTNESS, color=hue))
                    await asyncio.sleep(FLASH_DIM_S)
            if generation == lane.generation:  # a newer effect follows straight on, no need to go back first
                await lane.put_back()

        lane.start(kind, play)

    def mood(self, name: str) -> None:
        """Dim the room to a held mood ("show", "cozy", "tense"), or pulse it for a winner ("party")."""
        if name != "party" and name not in MOODS:
            logger.warning("Game lights: no room mood %s", name)
            return
        lane = self._room
        if name in MOODS:
            self._mood = MOODS[name]
        held = self._mood

        async def play(generation: int) -> None:
            if name == "party":
                for _ in range(PARTY_PULSES):
                    if generation != lane.generation:
                        return
                    await lane.set(lambda d: LightChange(brightness=100))
                    await asyncio.sleep(PARTY_BRIGHT_S)
                    await lane.set(lambda d: _share(d, PARTY_LOW))
                    await asyncio.sleep(PARTY_LOW_S)
                if generation != lane.generation:
                    return
                if held is None:
                    await lane.put_back()
                    return
            await lane.set(lambda d: _share(d, held if held is not None else 1.0))

        lane.start(name, play)

    def restore(self) -> None:
        """Put every light back as it was before the game's first effect (a pause or the end of the game)."""
        self._mood = None
        self._color.start("restore", None)
        self._room.start("restore", None)

    async def settled(self) -> None:
        """Wait for every effect started so far (tests and shutdown)."""
        await asyncio.gather(self._color.done(), self._room.done())
