import asyncio
from dataclasses import replace

import pytest

from reachy_mini_conversation_app.games import lights as lights_module
from reachy_mini_conversation_app.games.lights import GameLights
from reachy_mini_conversation_app.home_devices import HubDevice, LightChange, HomeDeviceError


STRIP = HubDevice("strip", "light", "TV Lights", "Living room", False, True, 60, colors=True, color=(30.0, 0.5))
CEILING = HubDevice("ceiling", "light", "Living light 1", "Living room", True, True, 80)
DARK = HubDevice("dark", "light", "Dining light 1", "Living room", False, True, 100)
LAMP = HubDevice("lamp", "outlet", "Living lamp", "Living room", True, True)


class _Home:
    """Home devices that record each change; ``fail`` names devices whose changes raise."""

    def __init__(self, *devices: HubDevice, fail: frozenset[str] = frozenset()) -> None:
        self._devices = list(devices)
        self.fail = fail
        self.changes: list[tuple[str, LightChange]] = []

    async def devices(self) -> list[HubDevice]:
        return self._devices

    async def apply(self, device: HubDevice, change: LightChange) -> None:
        await asyncio.sleep(0)
        if device.id in self.fail:
            raise HomeDeviceError("unreachable")
        self.changes.append((device.id, change))


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every glow and flash step instant."""
    for name in ("GLOW_S", "FLASH_ON_S", "FLASH_DIM_S", "PARTY_BRIGHT_S", "PARTY_LOW_S"):
        monkeypatch.setattr(lights_module, name, 0.0)


async def _settle(lights: GameLights) -> None:
    await lights.settled()


@pytest.mark.asyncio
async def test_a_flash_uses_only_color_lights_and_puts_the_strip_back_as_it_was() -> None:
    """Three bright-dim flashes on the strip, never the white ceiling light; then its color, then off again."""
    home = _Home(STRIP, CEILING)
    lights = GameLights(home)  # type: ignore[arg-type]

    lights.effect("flash", "green")
    await _settle(lights)

    assert {device for device, _ in home.changes} == {"strip"}
    bright, dim = LightChange(on=True, brightness=100, color=(120.0, 1.0)), LightChange(on=True, brightness=15, color=(120.0, 1.0))  # fmt: skip
    assert [change for _, change in home.changes] == [bright, dim] * 3 + [
        LightChange(brightness=60, color=(30.0, 0.5)),
        LightChange(on=False),
    ]


@pytest.mark.asyncio
async def test_a_light_that_reports_no_color_goes_back_on_or_off_only() -> None:
    """Without a reported color, putting back restores on/off and brightness."""
    home = _Home(replace(STRIP, is_on=True, color=None))
    lights = GameLights(home)  # type: ignore[arg-type]
    lights.effect("glow", "red")
    await _settle(lights)
    assert home.changes[-1] == ("strip", LightChange(on=True, brightness=60))


@pytest.mark.asyncio
async def test_a_newer_effect_replaces_one_still_running_and_restore_comes_last() -> None:
    """The flash stops after its current step, a glow replaced before it starts never plays, restore puts back."""
    home = _Home(STRIP)
    lights = GameLights(home)  # type: ignore[arg-type]
    lights.effect("flash", "blue")
    while not home.changes:  # the flash has started
        await asyncio.sleep(0)
    lights.effect("glow", "yellow")
    lights.restore()
    await _settle(lights)

    colors = [change.color for _, change in home.changes]
    assert colors.count((230.0, 1.0)) == 2  # one bright-dim flash of three, then it stopped
    assert (55.0, 1.0) not in colors  # the restore superseded the glow before it started
    assert home.changes[-2:] == [
        ("strip", LightChange(brightness=60, color=(30.0, 0.5))),
        ("strip", LightChange(on=False)),
    ]
    assert lights._color.saved is None  # the next game saves the lights afresh


@pytest.mark.asyncio
async def test_unknown_colors_no_lights_and_failing_lights_never_break_a_game() -> None:
    """An unknown color does nothing; a home without color lights changes nothing; a failing light is skipped."""
    home = _Home(CEILING)
    lights = GameLights(home)  # type: ignore[arg-type]
    lights.effect("flash", "chartreuse")
    lights.effect("glow", "red")
    lights.restore()
    await _settle(lights)
    assert home.changes == []

    failing = _Home(STRIP, fail=frozenset({"strip"}))
    lights = GameLights(failing)  # type: ignore[arg-type]
    lights.effect("flash", "red")
    lights.restore()
    await _settle(lights)
    assert failing.changes == []


@pytest.mark.asyncio
async def test_the_room_dims_to_a_mood_and_goes_back_exactly() -> None:
    """Only dimmable white lights that are on take part; moods are shares of their own level; restore is exact."""
    home = _Home(STRIP, CEILING, DARK, LAMP)
    lights = GameLights(home)  # type: ignore[arg-type]

    lights.mood("show")
    await _settle(lights)
    lights.mood("tense")
    await _settle(lights)
    assert home.changes == [("ceiling", LightChange(brightness=36)), ("ceiling", LightChange(brightness=12))]

    lights.restore()
    await _settle(lights)
    assert home.changes[-1] == ("ceiling", LightChange(on=True, brightness=80))
    assert {device for device, _ in home.changes} == {"ceiling"}  # the strip, the dark light and the plug untouched


@pytest.mark.asyncio
async def test_a_party_pulses_and_returns_to_the_held_mood_or_to_how_it_was() -> None:
    """Three bright-low pulses, then the game's mood again; with no mood held, back to the saved level."""
    home = _Home(CEILING)
    lights = GameLights(home)  # type: ignore[arg-type]
    lights.mood("show")
    await _settle(lights)
    lights.mood("party")
    await _settle(lights)
    levels = [change.brightness for _, change in home.changes]
    assert levels == [36, 100, 20, 100, 20, 100, 20, 36]

    alone = _Home(CEILING)
    lights = GameLights(alone)  # type: ignore[arg-type]
    lights.mood("party")
    await _settle(lights)
    assert alone.changes[-1] == ("ceiling", LightChange(on=True, brightness=80))
    lights.mood("disco")
    await _settle(lights)
    assert len(alone.changes) == 7
