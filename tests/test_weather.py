"""Tests for the Open-Meteo weather client and its spoken summaries."""

import httpx
import pytest

from reachy_mini_conversation_app.weather import (
    Place,
    Forecast,
    DayForecast,
    WeatherError,
    WeatherService,
    describe,
    condition,
)


FORECAST = Forecast("Lisboa", 18.4, 3, DayForecast(61, 21.5, 15.2, 70), DayForecast(0, 25, 16, 0))


def test_conditions_are_named_in_each_language() -> None:
    """WMO codes group into a handful of spoken conditions."""
    assert condition(0, "en") == "clear"
    assert condition(63, "es") == "lluvia"
    assert condition(95, "it") == "temporali"
    assert condition(3, "pt") == "nublado"


def test_today_summary_mentions_rain_when_likely() -> None:
    """Today's summary: now, high, low, and rain when 20% or more."""
    assert describe(FORECAST, "es") == (
        "En Lisboa hace 18 grados, nublado. Hoy máxima de 22 y mínima de 15, y 70% de probabilidad de lluvia."
    )


def test_tomorrow_and_rain_questions() -> None:
    """Tomorrow uses the second day; a rain question gets a yes or no."""
    assert describe(FORECAST, "en", tomorrow=True) == "Tomorrow in Lisboa: clear, high of 25 and low of 16 degrees."
    assert describe(FORECAST, "en", rain_only=True) == "Yes, rain is likely today in Lisboa, 70% chance."
    assert describe(FORECAST, "pt", tomorrow=True, rain_only=True) == "Não, não deve chover amanhã em Lisboa."


@pytest.mark.asyncio
async def test_forecast_and_home_lookup_through_open_meteo() -> None:
    """The home place is looked up once by name, then its forecast fetched."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "geocoding" in request.url.host:
            name = "Lisboa" if request.url.params["language"] == "es" else "Lisbon"
            return httpx.Response(200, json={"results": [{"name": name, "latitude": 38.7, "longitude": -9.1}]})
        return httpx.Response(
            200,
            json={
                "current": {"temperature_2m": 19.2, "weather_code": 1},
                "daily": {
                    "weather_code": [1, 61],
                    "temperature_2m_max": [24.0, 20.0],
                    "temperature_2m_min": [16.0, 14.0],
                    "precipitation_probability_max": [5, 80],
                },
            },
        )

    service = WeatherService(None, home_name="Lisbon", transport=httpx.MockTransport(respond))
    forecast = await service.forecast()
    assert (forecast.place, forecast.temperature, forecast.tomorrow) == (
        "Lisbon",
        19.2,
        DayForecast(61, 20.0, 14.0, 80),
    )
    assert await service.home_name("es") == "Lisboa"
    assert await service.home_name("es") == "Lisboa"
    assert sum("geocoding" in request.url.host for request in requests) == 2  # home once, Spanish name once
    assert service.home == Place("Lisbon", 38.7, -9.1)


@pytest.mark.asyncio
async def test_unknown_place_and_bad_responses_raise_weather_errors() -> None:
    """Callers get one error type for every failure."""
    service = WeatherService(
        Place("Lisbon", 38.7, -9.1),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []})),
    )
    with pytest.raises(WeatherError, match="not found"):
        await service.geocode("Atlantis")
    with pytest.raises(WeatherError, match="forecast failed"):
        await service.forecast()


def test_rain_question_answers_from_current_conditions() -> None:
    """Asked while it rains, the answer is yes whatever the day's chance says."""
    raining = Forecast("Lisboa", 15.0, 63, DayForecast(63, 17, 12, 30), None)
    assert describe(raining, "en", rain_only=True) == "Yes, it's raining in Lisboa right now."
