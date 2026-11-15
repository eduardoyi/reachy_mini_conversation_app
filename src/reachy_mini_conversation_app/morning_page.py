"""The morning page: from 7:00 to 10:00 the e-ink resting card shows the day, the weather and a word of the day.

The word is in Spanish (spoken at home) with its European Portuguese name (spoken at school) under it, picked by
date so each day has its own. Its drawing is made once and kept with the other drawings. The page replaces
Reachy's own resting card only in that window and only while privacy is off; any screen someone asks for still
shows over it and idles back to it. Logs carry no forecast or words, only failures.
"""

import asyncio
import logging
from typing import Callable
from datetime import date, time, datetime
from dataclasses import dataclass

from reachy_mini_conversation_app.privacy import PRIVACY
from reachy_mini_conversation_app.weather import WeatherError, WeatherService, condition, condition_key
from reachy_mini_conversation_app.eink_display import EInkDisplay, DrawingError, render_morning_page


logger = logging.getLogger(__name__)

MORNING_START = time(7, 0)
MORNING_END = time(10, 0)
CHECK_S = 60.0

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


@dataclass(frozen=True)
class MorningWord:
    """A word of the day: what to draw, its Spanish name and its European Portuguese name, with articles."""

    subject: str
    spanish: str
    portuguese: str


# Things a 2- and 4-year-old know, with the European Portuguese the school uses (comboio, autocarro, sumo).
MORNING_WORDS: tuple[MorningWord, ...] = (
    MorningWord("a dog", "el perro", "o cão"),
    MorningWord("a cat", "el gato", "o gato"),
    MorningWord("a cow", "la vaca", "a vaca"),
    MorningWord("a horse", "el caballo", "o cavalo"),
    MorningWord("a bird", "el pájaro", "o pássaro"),
    MorningWord("a fish", "el pez", "o peixe"),
    MorningWord("a bear", "el oso", "o urso"),
    MorningWord("a rabbit", "el conejo", "o coelho"),
    MorningWord("a butterfly", "la mariposa", "a borboleta"),
    MorningWord("a frog", "la rana", "a rã"),
    MorningWord("a turtle", "la tortuga", "a tartaruga"),
    MorningWord("an elephant", "el elefante", "o elefante"),
    MorningWord("a lion", "el león", "o leão"),
    MorningWord("a giraffe", "la jirafa", "a girafa"),
    MorningWord("a monkey", "el mono", "o macaco"),
    MorningWord("a mouse", "el ratón", "o rato"),
    MorningWord("a duck", "el pato", "o pato"),
    MorningWord("a hen", "la gallina", "a galinha"),
    MorningWord("a pig", "el cerdo", "o porco"),
    MorningWord("a sheep", "la oveja", "a ovelha"),
    MorningWord("an apple", "la manzana", "a maçã"),
    MorningWord("a banana", "el plátano", "a banana"),
    MorningWord("an orange", "la naranja", "a laranja"),
    MorningWord("a strawberry", "la fresa", "o morango"),
    MorningWord("a loaf of bread", "el pan", "o pão"),
    MorningWord("a glass of milk", "la leche", "o leite"),
    MorningWord("a wedge of cheese", "el queso", "o queijo"),
    MorningWord("an egg", "el huevo", "o ovo"),
    MorningWord("a glass of orange juice", "el zumo", "o sumo"),
    MorningWord("an ice cream cone", "el helado", "o gelado"),
    MorningWord("a cookie", "la galleta", "a bolacha"),
    MorningWord("a train", "el tren", "o comboio"),
    MorningWord("a city bus", "el autobús", "o autocarro"),
    MorningWord("a car", "el coche", "o carro"),
    MorningWord("an airplane", "el avión", "o avião"),
    MorningWord("a boat", "el barco", "o barco"),
    MorningWord("a bicycle", "la bicicleta", "a bicicleta"),
    MorningWord("a yellow Lisbon tram", "el tranvía", "o elétrico"),
    MorningWord("the sun", "el sol", "o sol"),
    MorningWord("the moon", "la luna", "a lua"),
    MorningWord("a star", "la estrella", "a estrela"),
    MorningWord("a cloud", "la nube", "a nuvem"),
    MorningWord("a tree", "el árbol", "a árvore"),
    MorningWord("a flower", "la flor", "a flor"),
    MorningWord("a leaf", "la hoja", "a folha"),
    MorningWord("a chestnut", "la castaña", "a castanha"),
    MorningWord("a shoe", "el zapato", "o sapato"),
    MorningWord("a hat", "el sombrero", "o chapéu"),
    MorningWord("a sock", "el calcetín", "a meia"),
    MorningWord("a ball", "la pelota", "a bola"),
    MorningWord("a book", "el libro", "o livro"),
    MorningWord("a bed", "la cama", "a cama"),
    MorningWord("a chair", "la silla", "a cadeira"),
    MorningWord("a spoon", "la cuchara", "a colher"),
    MorningWord("a drinking glass", "el vaso", "o copo"),
    MorningWord("a fridge", "la nevera", "o frigorífico"),
    MorningWord("an umbrella", "el paraguas", "o guarda-chuva"),
    MorningWord("a house", "la casa", "a casa"),
    MorningWord("a key", "la llave", "a chave"),
    MorningWord("a clock", "el reloj", "o relógio"),
)


def word_for(day: date) -> MorningWord:
    """Return the word of the day for ``day``; consecutive days never repeat."""
    return MORNING_WORDS[day.toordinal() % len(MORNING_WORDS)]


class MorningPage:
    """Put the morning page up as the resting card each morning and take it down after."""

    def __init__(
        self,
        display: EInkDisplay,
        weather: WeatherService | None,
        *,
        now: Callable[[], datetime] = datetime.now,
        privacy_on: Callable[[], bool] = lambda: PRIVACY.on,
    ) -> None:
        """Use ``weather`` for the forecast (None: the page has no weather); ``now`` is local time."""
        self._display = display
        self._weather = weather
        self._now = now
        self._privacy_on = privacy_on
        self._page: tuple[date, bytes] | None = None
        self._showing = False

    async def run(self) -> None:
        """Check every minute until cancelled."""
        logger.info("Morning page on the e-ink screen enabled (%s-%s)", MORNING_START, MORNING_END)
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # the watcher must outlive any one bad morning
                logger.warning("Morning page check failed: %s", type(error).__name__)
            await asyncio.sleep(CHECK_S)

    async def check(self) -> None:
        """Make the morning page the resting card inside the window, and Reachy's own card again after it."""
        now = self._now()
        if not MORNING_START <= now.time() < MORNING_END or self._privacy_on():
            if self._showing:
                self._showing = False
                if not self._privacy_on():  # privacy keeps its own card
                    self._display.set_resting_card(None)
            return
        if self._page is None or self._page[0] != now.date():
            self._page = (now.date(), await self._render(now.date()))
        # Every minute, so the page comes back after privacy is switched off; unchanged, it is not redrawn.
        self._display.set_resting_card(self._page[1])
        self._showing = True

    async def _render(self, day: date) -> bytes:
        """Draw ``day``'s page, leaving out the weather or the picture when either is unavailable."""
        word = word_for(day)
        sky, temperatures, conditions = None, "", ""
        if self._weather is not None:
            try:
                forecast = await self._weather.forecast()
            except WeatherError as error:
                logger.warning("Morning page has no weather: %s", error)
            else:
                today = forecast.today
                sky = condition_key(today.code)
                temperatures = f"{round(today.high)}° / {round(today.low)}°"
                conditions = condition(today.code, "es").capitalize()
        try:
            picture = await self._display.drawing(word.subject)
        except DrawingError as error:
            logger.warning("Morning page has no picture: %s", error)
            picture = None
        return render_morning_page(
            WEEKDAYS[day.weekday()],
            f"{day.day} de {MONTHS[day.month - 1]}",
            word.spanish,
            f"{word.portuguese} · em português",
            sky=sky,
            temperatures=temperatures,
            conditions=conditions,
            picture=picture,
        )
