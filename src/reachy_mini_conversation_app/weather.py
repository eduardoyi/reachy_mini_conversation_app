"""Today's and tomorrow's weather from Open-Meteo (free, no key, about 0.2 s), with spoken summaries.

Used by quick commands so "what's the weather?" is answered without the cloud conversation; the
home place comes from ``HOME_LOCATION`` (and optionally ``HOME_LATITUDE``/``HOME_LONGITUDE``).
"""

from __future__ import annotations
import logging
from typing import Any, Final
from dataclasses import dataclass

import httpx


logger = logging.getLogger(__name__)

FORECAST_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL: Final[str] = "https://geocoding-api.open-meteo.com/v1/search"
REQUEST_TIMEOUT_S: Final[float] = 4.0


class WeatherError(RuntimeError):
    """The forecast or the place could not be fetched."""


@dataclass(frozen=True)
class Place:
    """A named point."""

    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class DayForecast:
    """One day's summary."""

    code: int
    high: float
    low: float
    rain_chance: int | None


@dataclass(frozen=True)
class Forecast:
    """Current conditions plus today and tomorrow."""

    place: str
    temperature: float
    code: int
    today: DayForecast
    tomorrow: DayForecast | None


# WMO weather interpretation codes, grouped, per language.
_CONDITIONS: Final[dict[str, dict[str, str]]] = {
    "clear": {"en": "clear", "es": "despejado", "it": "sereno", "pt": "céu limpo"},
    "mostly_clear": {"en": "mostly sunny", "es": "casi despejado", "it": "quasi sereno", "pt": "pouco nublado"},
    "partly_cloudy": {
        "en": "partly cloudy",
        "es": "parcialmente nublado",
        "it": "parzialmente nuvoloso",
        "pt": "parcialmente nublado",
    },
    "cloudy": {"en": "cloudy", "es": "nublado", "it": "nuvoloso", "pt": "nublado"},
    "fog": {"en": "foggy", "es": "con niebla", "it": "nebbia", "pt": "nevoeiro"},
    "drizzle": {"en": "drizzly", "es": "llovizna", "it": "pioviggine", "pt": "chuvisco"},
    "rain": {"en": "rainy", "es": "lluvia", "it": "pioggia", "pt": "chuva"},
    "heavy_rain": {"en": "heavy rain", "es": "lluvia fuerte", "it": "pioggia forte", "pt": "chuva forte"},
    "showers": {"en": "showers", "es": "chubascos", "it": "rovesci", "pt": "aguaceiros"},
    "snow": {"en": "snow", "es": "nieve", "it": "neve", "pt": "neve"},
    "storm": {"en": "thunderstorms", "es": "tormentas", "it": "temporali", "pt": "trovoada"},
}


_RAINY: Final[frozenset[str]] = frozenset({"drizzle", "rain", "heavy_rain", "showers", "storm"})


def condition_key(code: int) -> str:
    """Group a WMO weather code."""
    if code == 0:
        return "clear"
    if code == 1:
        return "mostly_clear"
    if code == 2:
        return "partly_cloudy"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if code in (65, 67, 82):
        return "heavy_rain"
    if 61 <= code <= 67:
        return "rain"
    if 80 <= code <= 82:
        return "showers"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloudy"


def condition(code: int, language: str) -> str:
    """Name a WMO weather code in ``language`` (en, es, it, pt)."""
    names = _CONDITIONS[condition_key(code)]
    return names.get(language, names["en"])


def _degrees(value: float) -> int:
    return round(value)


def describe(forecast: Forecast, language: str, *, tomorrow: bool = False, rain_only: bool = False) -> str:
    """Return a one- or two-sentence spoken summary."""
    day = forecast.tomorrow if tomorrow and forecast.tomorrow is not None else forecast.today
    chance = day.rain_chance
    place = forecast.place
    if rain_only:
        if not tomorrow and condition_key(forecast.code) in _RAINY:
            return {"en": f"Yes, it's raining in {place} right now.", "es": f"Sí, está lloviendo en {place} ahora mismo.",
                    "it": f"Sì, sta piovendo a {place} adesso.", "pt": f"Sim, está a chover em {place} agora."}[language]  # fmt: skip
        likely = chance is not None and chance >= 50
        possible = chance is not None and chance >= 20
        when = {"en": "tomorrow" if tomorrow else "today", "es": "mañana" if tomorrow else "hoy",
                "it": "domani" if tomorrow else "oggi", "pt": "amanhã" if tomorrow else "hoje"}[language]  # fmt: skip
        percent = f"{chance}%" if chance is not None and chance >= 10 else ""
        if language == "es":
            verdict = (
                "Sí, probablemente llueva"
                if likely
                else ("Puede que llueva" if possible else "No, no parece que vaya a llover")
            )
            return f"{verdict} {when} en {place}" + (f": {percent} de probabilidad." if percent else ".")
        if language == "it":
            verdict = (
                "Sì, probabilmente pioverà"
                if likely
                else ("Potrebbe piovere" if possible else "No, non dovrebbe piovere")
            )
            return f"{verdict} {when} a {place}" + (f": {percent} di probabilità." if percent else ".")
        if language == "pt":
            verdict = "Sim, deve chover" if likely else ("Pode chover" if possible else "Não, não deve chover")
            return f"{verdict} {when} em {place}" + (f": {percent} de probabilidade." if percent else ".")
        verdict = (
            "Yes, rain is likely" if likely else ("It might rain" if possible else "No, it doesn't look like rain")
        )
        return f"{verdict} {when} in {place}" + (f", {percent} chance." if percent else ".")
    high, low = _degrees(day.high), _degrees(day.low)
    sky = condition(day.code, language)
    if tomorrow:
        rain = ""
        if chance is not None and chance >= 20:
            rain = {"en": f", {chance}% chance of rain", "es": f", {chance}% de probabilidad de lluvia",
                    "it": f", {chance}% di probabilità di pioggia", "pt": f", {chance}% de probabilidade de chuva"}[language]  # fmt: skip
        return {
            "en": f"Tomorrow in {place}: {sky}, high of {high} and low of {low} degrees{rain}.",
            "es": f"Mañana en {place}: {sky}, máxima de {high} y mínima de {low} grados{rain}.",
            "it": f"Domani a {place}: {sky}, massima di {high} e minima di {low} gradi{rain}.",
            "pt": f"Amanhã em {place}: {sky}, máxima de {high} e mínima de {low} graus{rain}.",
        }[language]
    now = _degrees(forecast.temperature)
    current = condition(forecast.code, language)
    rain = ""
    if chance is not None and chance >= 20:
        rain = {"en": f", and a {chance}% chance of rain", "es": f", y {chance}% de probabilidad de lluvia",
                "it": f", e {chance}% di probabilità di pioggia", "pt": f", e {chance}% de probabilidade de chuva"}[language]  # fmt: skip
    return {
        "en": f"In {place} it's {now} degrees and {current}. Today's high is {high}, low {low}{rain}.",
        "es": f"En {place} hace {now} grados, {current}. Hoy máxima de {high} y mínima de {low}{rain}.",
        "it": f"A {place} ci sono {now} gradi, {current}. Oggi massima di {high} e minima di {low}{rain}.",
        "pt": f"Em {place} estão {now} graus, {current}. Hoje máxima de {high} e mínima de {low}{rain}.",
    }[language]


class WeatherService:
    """Fetch forecasts from Open-Meteo."""

    def __init__(
        self,
        home: Place | None,
        *,
        home_name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Use ``home`` when no place is named, else look ``home_name`` up once; ``transport`` is for tests."""
        self.home = home
        self._home_name = home_name
        self._transport = transport
        self._local_names: dict[str, str] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, transport=self._transport)

    async def geocode(self, name: str, language: str = "en") -> Place:
        """Find a place by name."""
        try:
            async with self._client() as client:
                response = await client.get(
                    GEOCODING_URL, params={"name": name, "count": 1, "language": language, "format": "json"}
                )
                response.raise_for_status()
                results = response.json().get("results") or []
        except (httpx.HTTPError, ValueError) as error:
            raise WeatherError(f"place lookup failed: {type(error).__name__}") from error
        if not results:
            raise WeatherError("place not found")
        best = results[0]
        return Place(str(best.get("name") or name), float(best["latitude"]), float(best["longitude"]))

    async def home_name(self, language: str) -> str | None:
        """Return the home place's name in ``language`` ("Lisboa" for Spanish), looked up once per language."""
        if self.home is None:
            return None
        if language not in self._local_names:
            try:
                self._local_names[language] = (await self.geocode(self.home.name, language)).name
            except WeatherError:
                return self.home.name
        return self._local_names[language]

    async def forecast(self, place: Place | None = None) -> Forecast:
        """Return current conditions, today and tomorrow for ``place`` (default: home)."""
        if place is None and self.home is None and self._home_name:
            self.home = await self.geocode(self._home_name)
        place = place or self.home
        if place is None:
            raise WeatherError("no home place is set")
        params: dict[str, Any] = {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 2,
        }
        try:
            async with self._client() as client:
                response = await client.get(FORECAST_URL, params=params)
                response.raise_for_status()
                data = response.json()
            current, daily = data["current"], data["daily"]

            def day(index: int) -> DayForecast:
                chances = daily.get("precipitation_probability_max") or []
                chance = chances[index] if index < len(chances) else None
                return DayForecast(
                    code=int(daily["weather_code"][index]),
                    high=float(daily["temperature_2m_max"][index]),
                    low=float(daily["temperature_2m_min"][index]),
                    rain_chance=None if chance is None else int(chance),
                )

            return Forecast(
                place=place.name,
                temperature=float(current["temperature_2m"]),
                code=int(current["weather_code"]),
                today=day(0),
                tomorrow=day(1) if len(daily.get("weather_code") or []) > 1 else None,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as error:
            raise WeatherError(f"forecast failed: {type(error).__name__}") from error
