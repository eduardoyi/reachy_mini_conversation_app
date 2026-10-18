import logging
from typing import Any

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.weather import Place, WeatherError, WeatherService, condition
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


def _service() -> WeatherService:
    home = None
    if config.HOME_LOCATION and config.HOME_LATITUDE is not None and config.HOME_LONGITUDE is not None:
        home = Place(config.HOME_LOCATION, config.HOME_LATITUDE, config.HOME_LONGITUDE)
    return WeatherService(home, home_name=config.HOME_LOCATION)


class GetWeather(Tool):
    """Today's and tomorrow's weather from Open-Meteo (local replacement for the remote weather Space)."""

    name = "get_weather"
    description = (
        "Get the weather: current temperature and conditions, today's and tomorrow's high, low and rain chance. "
        "Call this directly whenever the user asks about the weather, forecast, temperature or rain. Leave "
        "location empty for home; give a place name for anywhere else. Do not just say you'll check."
    )
    parameters_schema = {
        "type": "object",
        "properties": {"location": {"type": "string", "description": "Place name; empty for home."}},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return the forecast in degrees Celsius."""
        location = str(kwargs.get("location") or "").strip()
        logger.info("Tool call: get_weather home=%s", not location)
        service = _service()
        try:
            place = await service.geocode(location) if location else None
            forecast = await service.forecast(place)
        except WeatherError as error:
            return {"error": f"Weather unavailable: {error}"}

        def day(summary: Any) -> dict[str, Any]:
            return {
                "conditions": condition(summary.code, "en"),
                "high_c": round(summary.high),
                "low_c": round(summary.low),
                "rain_chance_percent": summary.rain_chance,
            }

        result: dict[str, Any] = {
            "place": forecast.place,
            "now": {"temperature_c": round(forecast.temperature), "conditions": condition(forecast.code, "en")},
            "today": day(forecast.today),
        }
        if forecast.tomorrow is not None:
            result["tomorrow"] = day(forecast.tomorrow)
        return result
