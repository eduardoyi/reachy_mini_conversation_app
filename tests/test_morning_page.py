from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from reachy_mini_conversation_app import main
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.weather import Forecast, DayForecast, WeatherError
from reachy_mini_conversation_app.eink_display import MAX_PNG_BYTES, DrawingError, render_morning_page
from reachy_mini_conversation_app.morning_page import MORNING_WORDS, MorningPage, word_for


SUNNY = Forecast("Lisboa", 15.0, 0, DayForecast(0, 18.4, 10.6, 0), None)


class _Clock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def __call__(self) -> datetime:
        return self.when


def _page(when: datetime, *, privacy: bool = False, weather: object = None, picture: object = None):
    display = MagicMock()
    display.drawing = AsyncMock(return_value=picture or Image.new("L", (300, 300), 200))
    if weather is None:
        weather = MagicMock()
        weather.forecast = AsyncMock(return_value=SUNNY)
    state = {"privacy": privacy}
    clock = _Clock(when)
    page = MorningPage(display, weather, now=clock, privacy_on=lambda: state["privacy"])
    return page, display, clock, state


def test_each_day_has_its_own_word_and_every_word_has_both_languages() -> None:
    """Consecutive days never share a word; each word names a subject and has Spanish and Portuguese."""
    days = [date(2026, 11, 1).toordinal() + offset for offset in range(len(MORNING_WORDS))]
    words = [word_for(date.fromordinal(day)) for day in days]
    assert len({word.subject for word in words}) == len(MORNING_WORDS)
    assert all(word.subject and word.spanish and word.portuguese for word in MORNING_WORDS)


@pytest.mark.parametrize(
    "sky", ["clear", "partly_cloudy", "cloudy", "fog", "drizzle", "rain", "heavy_rain", "showers", "snow", "storm"]
)
def test_the_page_fits_the_trmnl_for_every_kind_of_weather(sky: str) -> None:
    """Every weather picture renders into an 800x480 PNG the TRMNL can draw."""
    png = render_morning_page(
        "miércoles",
        "18 de noviembre",
        "el tranvía",
        "o elétrico · em português",
        sky=sky,
        temperatures="18° / 11°",
        conditions="Parcialmente nublado",
        picture=Image.new("L", (1024, 1024), 128),
    )
    assert png.startswith(b"\x89PNG") and len(png) <= MAX_PNG_BYTES


@pytest.mark.asyncio
async def test_the_morning_window_shows_the_page_once_a_day_then_his_own_card() -> None:
    """From 7:00 the page is the resting card, drawn once for the day; at 10:00 his own card returns."""
    page, display, clock, _ = _page(datetime(2026, 11, 16, 6, 59))
    await page.check()
    display.set_resting_card.assert_not_called()

    clock.when = datetime(2026, 11, 16, 7, 0)
    await page.check()
    clock.when = datetime(2026, 11, 16, 9, 30)
    await page.check()
    [first, second] = display.set_resting_card.call_args_list
    assert first.args[0] == second.args[0] and first.args[0].startswith(b"\x89PNG")
    display.drawing.assert_awaited_once_with(word_for(date(2026, 11, 16)).subject)

    clock.when = datetime(2026, 11, 16, 10, 0)
    await page.check()
    await page.check()
    assert display.set_resting_card.call_args_list[2:] == [((None,),)]


@pytest.mark.asyncio
async def test_privacy_keeps_its_own_card_and_the_page_returns_after() -> None:
    """With privacy on the page leaves the resting card alone; once off, the next check puts the page back."""
    page, display, _, state = _page(datetime(2026, 11, 16, 8, 0), privacy=True)
    await page.check()
    display.set_resting_card.assert_not_called()

    state["privacy"] = False
    await page.check()
    display.set_resting_card.assert_called_once()

    state["privacy"] = True
    await page.check()
    display.set_resting_card.assert_called_once()  # not reset to his own card over the privacy card


@pytest.mark.asyncio
async def test_no_weather_or_no_drawing_still_shows_the_page() -> None:
    """A failed forecast or drawing leaves that part out instead of skipping the morning."""
    weather = MagicMock()
    weather.forecast = AsyncMock(side_effect=WeatherError("down"))
    page, display, _, _ = _page(datetime(2026, 11, 16, 8, 0), weather=weather)
    display.drawing = AsyncMock(side_effect=DrawingError("out of credits"))

    await page.check()

    assert display.set_resting_card.call_args.args[0].startswith(b"\x89PNG")


def test_the_page_is_built_only_with_a_screen_and_the_switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """No display or DISPLAY_MORNING_PAGE=0 means no morning page watcher."""
    monkeypatch.setattr(config, "DISPLAY_MORNING_PAGE", True)
    monkeypatch.setattr(config, "HOME_LOCATION", None)
    assert main._build_morning_page(None) is None
    assert isinstance(main._build_morning_page(MagicMock()), MorningPage)
    monkeypatch.setattr(config, "DISPLAY_MORNING_PAGE", False)
    assert main._build_morning_page(MagicMock()) is None
