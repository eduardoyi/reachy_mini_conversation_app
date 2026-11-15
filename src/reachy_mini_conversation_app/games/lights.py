"""Game lights: the home's color lights glow and flash at a game's big moments, then go back as they were.

Only lights that show any color take part (today the TV strip); a white ceiling light would only blink. The first
effect in a game saves each one as its system reports it (on, brightness and color); every effect ends by putting
them back, and so does a pause or the end of the game. Effects never hold up a game: they run in the background,
one at a time (a newer one replaces one still running once its current step is done), and a light that fails is
logged and skipped.
"""

import asyncio
import logging

from reachy_mini_conversation_app.home_devices import HubDevice, HomeDevices, LightChange, HomeDeviceError
from reachy_mini_conversation_app.tools.home_lights import LIGHT_COLORS


logger = logging.getLogger(__name__)

GLOW_S = 3.0
FLASHES = 3
FLASH_ON_S = 0.5
FLASH_DIM_S = 0.4
FLASH_DIM_BRIGHTNESS = 15  # a flash dims rather than switching off: the strip's on/off is slow and quirky


class GameLights:
    """Glow and flash the color lights for a game, and put them back."""

    def __init__(self, home: HomeDevices) -> None:
        """Use the color lights among ``home``'s devices."""
        self._home = home
        self._saved: list[HubDevice] | None = None
        self._generation = 0
        self._task: asyncio.Task[None] | None = None

    def effect(self, kind: str, color: str) -> None:
        """Start ``kind`` ("glow" for 3 s, or three "flash"es) in ``color`` (a name in ``LIGHT_COLORS``)."""
        if kind not in ("glow", "flash") or color not in LIGHT_COLORS:
            logger.warning("Game lights: no %s effect in %s", kind, color)
            return
        self._start(kind, LIGHT_COLORS[color])

    def restore(self) -> None:
        """Put the lights back as they were before the game's first effect (a pause or the end of the game)."""
        self._start("restore", None)

    def _start(self, kind: str, color: tuple[float, float] | None) -> None:
        self._generation += 1
        self._task = asyncio.get_running_loop().create_task(
            self._run(self._task, self._generation, kind, color), name="game-lights"
        )

    async def _run(
        self, previous: asyncio.Task[None] | None, generation: int, kind: str, color: tuple[float, float] | None
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)  # never two steps on a light at once
        try:
            if kind == "restore":
                if self._saved is not None:
                    await self._put_back()
                    self._saved = None
            elif generation == self._generation and color is not None:
                await self._play(kind, color, generation)
        except (HomeDeviceError, TimeoutError) as error:
            logger.warning("Game lights: %s failed (%s)", kind, type(error).__name__)

    async def _play(self, kind: str, color: tuple[float, float], generation: int) -> None:
        if self._saved is None:
            self._saved = [device for device in await self._home.devices() if device.colors and device.reachable]
        if not self._saved:
            return
        bright = LightChange(on=True, brightness=100, color=color)
        if kind == "glow":
            await self._set(bright)
            await asyncio.sleep(GLOW_S)
        else:
            for _ in range(FLASHES):
                if generation != self._generation:
                    break
                await self._set(bright)
                await asyncio.sleep(FLASH_ON_S)
                await self._set(LightChange(on=True, brightness=FLASH_DIM_BRIGHTNESS, color=color))
                await asyncio.sleep(FLASH_DIM_S)
        if generation == self._generation:  # a newer effect follows straight on, no need to go back first
            await self._put_back()

    async def _set(self, change: LightChange) -> None:
        devices = self._saved or []
        results = await asyncio.gather(*(self._home.apply(d, change) for d in devices), return_exceptions=True)
        for device, result in zip(devices, results):
            if isinstance(result, BaseException):
                logger.warning("Game lights: %s did not change (%s)", device.name, type(result).__name__)

    async def _put_back(self) -> None:
        """Each light as saved: its color first (that also lights it), then off again if it was off."""
        for device in self._saved or []:
            try:
                if device.color is not None:
                    await self._home.apply(device, LightChange(brightness=device.brightness, color=device.color))
                    if not device.is_on:
                        await self._home.apply(device, LightChange(on=False))
                else:
                    on = device.is_on
                    await self._home.apply(device, LightChange(on=on, brightness=device.brightness if on else None))
            except HomeDeviceError as error:
                logger.warning("Game lights: %s not put back (%s)", device.name, type(error).__name__)
