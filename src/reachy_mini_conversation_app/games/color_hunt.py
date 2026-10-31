"""The color hunt ("juguemos a los colores"): find something of the color Reachy names and show it to him.

Each round Reachy says "¡Busca algo ROJO!", the screen shows ROJO, and a tune speeds up for 30 seconds. About
twice a second a camera frame that changed goes to Jev with two questions: is someone holding an object up to
the camera, and what is its main color. Two of the last three frames agreeing on the color finds it. Reachy's
camera shows red and hot pink as orange-ish (spike, 2026-10-31), so red accepts orange; a color already in view
when the round starts (a bottle on the table) counts only after it has left the view. Five rounds, a star per
find; nobody loses.
"""

import io
import random
import asyncio
import logging
from typing import Any, Final
from collections import deque

import numpy as np
from PIL import Image, UnidentifiedImageError
from numpy.typing import NDArray

from reachy_mini_conversation_app.jev import JevError, sized_jpeg
from reachy_mini_conversation_app.games.base import Game, GameContext
from reachy_mini_conversation_app.eink_display import render_game_prompt
from reachy_mini_conversation_app.local_speech import SAMPLE_RATE
from reachy_mini_conversation_app.games.catalog import YES_WORDS, COLOR_HUNT, words


logger = logging.getLogger(__name__)

ROUNDS: Final[int] = 5
ROUND_S: Final[float] = 30.0
# Frames: one at most this often, and one this often even when nothing changed (someone holds it still).
CHECK_EVERY_S: Final[float] = 0.5
RECHECK_STILL_S: Final[float] = 1.5
# Mean absolute difference (0-255) between small gray thumbnails that counts as a changed frame.
CHANGED_FRAME: Final[float] = 4.0
# Spike thresholds (2026-10-31): held up 0.78-0.98 at arm's length, 0.35-0.71 shoved into the lens, an empty
# hand or someone walking past at most 0.28.
MIN_HELD: Final[float] = 0.5
# Naming a wrong color needs a sure answer, and not more than once in this long.
MIN_WRONG_P: Final[float] = 0.7
WRONG_EVERY_S: Final[float] = 5.0
OTHER_ROUND_ANSWER_S: Final[float] = 20.0

# The colors Reachy asks for: Spanish and English names, and the Jev answers that count as each.
TARGETS: Final[dict[str, tuple[str, str, frozenset[str]]]] = {
    "red": ("rojo", "red", frozenset({"red", "orange"})),
    "blue": ("azul", "blue", frozenset({"blue"})),
    "yellow": ("amarillo", "yellow", frozenset({"yellow"})),
    "green": ("verde", "green", frozenset({"green"})),
    "pink": ("rosa", "pink", frozenset({"pink"})),
    "white": ("blanco", "white", frozenset({"white"})),
    "black": ("negro", "black", frozenset({"black"})),
}
# Every answer Jev may give, with its names for "eso es naranja".
_COLOR_NAMES: Final[dict[str, tuple[str, str]]] = {
    **{key: (spanish, english) for key, (spanish, english, _) in TARGETS.items()},
    "orange": ("naranja", "orange"),
    "purple": ("morado", "purple"),
    "brown": ("marrón", "brown"),
}
_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    "held": {
        "type": "noul",
        "instructions": (
            "In `frame`, is someone holding up or showing an object to the camera, close enough to see it clearly?"
        ),
    },
    "color": {
        "type": "choice",
        "instructions": (
            "What is the main color of the object someone is holding up or showing to the camera in `frame`? "
            "Ignore the background, the person's skin and clothes."
        ),
        "criteria": {
            **{key: "purple or violet" if key == "purple" else key for key in _COLOR_NAMES},
            "none": "no object is being held up or shown to the camera",
        },
    },
}

_START: Final[dict[str, str]] = {"es": "¡Vamos a jugar a los colores!", "en": "Let's play color hunt!"}
_FIND: Final[dict[str, str]] = {"es": "¡Busca algo {color}!", "en": "Find something {color}!"}
_FOUND: Final[dict[str, str]] = {"es": "¡Sí! ¡{color}!", "en": "Yes! {color}!"}
_WRONG: Final[dict[str, str]] = {"es": "Mmm, eso es {seen}. ¡Busca algo {color}!", "en": "Hmm, that's {seen}. Find something {color}!"}  # fmt: skip
_TIME_UP: Final[dict[str, str]] = {"es": "¡Se acabó el tiempo! Era el {color}.", "en": "Time's up! It was {color}."}
_STARS: Final[dict[str, tuple[str, ...]]] = {
    "es": ("Esta vez, ninguna estrella.", "¡Una estrella!", "¡Dos estrellas!", "¡Tres estrellas!",
           "¡Cuatro estrellas!", "¡Cinco estrellas!"),
    "en": ("No stars this time.", "One star!", "Two stars!", "Three stars!", "Four stars!", "Five stars!"),
}  # fmt: skip
_AGAIN: Final[dict[str, str]] = {"es": "¿Otra vez?", "en": "Play again?"}
_THANKS: Final[dict[str, str]] = {"es": "¡Gracias por jugar!", "en": "Thanks for playing!"}
_SCREEN_CAPTION: Final[dict[str, str]] = {"es": "Busca algo…", "en": "Find something…"}
_WELL_DONE: Final[dict[str, str]] = {"es": "¡Muy bien!", "en": "Well done!"}

# The tune: a bouncy figure on a C major arpeggio, staccato, sped up from START_BPM to END_BPM over the round.
START_BPM: Final[float] = 100.0
END_BPM: Final[float] = 180.0
_TUNE: Final[tuple[float, ...]] = (523.25, 659.25, 783.99, 659.25, 523.25, 659.25, 783.99, 1046.5)
_TUNE_LEVEL: Final[float] = 0.18


def _name(color: str, language: str, *, capital: bool = False) -> str:
    spanish, english = _COLOR_NAMES[color]
    name = spanish if language == "es" else english
    return name[:1].upper() + name[1:] if capital else name


def color_hunt_phrases() -> frozenset[str]:
    """Every line the color hunt says, in both languages, for the speech cache."""
    lines: set[str] = set()
    for language in ("es", "en"):
        lines |= {_START[language], _AGAIN[language], _THANKS[language], *_STARS[language]}
        for target in TARGETS:
            name = _name(target, language)
            lines.add(_FIND[language].format(color=name.upper() if language == "es" else name))
            lines.add(_FOUND[language].format(color=_name(target, language, capital=True)))
            lines.add(_TIME_UP[language].format(color=name))
            for seen in _COLOR_NAMES:
                if seen not in TARGETS[target][2]:
                    lines.add(_WRONG[language].format(seen=_name(seen, language), color=name))
    return frozenset(lines)


def beep_note(frequency: float, seconds: float) -> NDArray[np.int16]:
    """One staccato note of the tune: the note for 70% of the beat, then silence."""
    samples = int(SAMPLE_RATE * seconds)
    sounding = int(samples * 0.7)
    time_s = np.arange(sounding) / SAMPLE_RATE
    fade = np.minimum(1.0, np.minimum(time_s, time_s[-1] - time_s) / 0.008) if sounding > 1 else np.ones(sounding)
    tone = np.sin(2 * np.pi * frequency * time_s) * fade * _TUNE_LEVEL * 32767
    return np.concatenate([tone, np.zeros(samples - sounding)]).astype(np.int16)


def _thumbnail(jpeg: bytes) -> tuple[Image.Image, NDArray[np.float32]] | None:
    """Decode a camera frame: the picture, and a small gray copy to tell whether anything changed."""
    try:
        with Image.open(io.BytesIO(jpeg)) as opened:
            picture = opened.convert("RGB")
    except (UnidentifiedImageError, OSError):
        return None
    small = np.asarray(picture.convert("L").resize((48, 27)), dtype=np.float32)
    return picture, small


class Check:
    """Jev's verdict on one frame: whether something is held up, and its likeliest color and probability."""

    def __init__(self, held: float, color: str, probability: float) -> None:
        """Keep the answers."""
        self.held, self.color, self.probability = held, color, probability

    def __repr__(self) -> str:
        """Show the answers in the log."""
        return f"Check(held={self.held:.2f}, color={self.color}, p={self.probability:.2f})"


class ColorHunt(Game):
    """Five rounds of finding a color."""

    info = COLOR_HUNT

    def __init__(self, rng: random.Random | None = None) -> None:
        """Pick colors with ``rng`` (seeded in tests)."""
        self._rng = rng or random.Random()
        self._stars = 0
        self._round = 0
        self._tune: asyncio.Task[None] | None = None

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line, for the speech cache."""
        return color_hunt_phrases()

    def paused(self, context: GameContext) -> None:
        """Nothing to stop: the tune waits for play to resume, and a wake already cut what was queued."""

    async def play(self, context: GameContext) -> None:
        """Play rounds of five until nobody wants another."""
        language = context.language
        await context.say(_START[language])
        previous = ""
        while True:
            self._stars = 0
            for self._round in range(1, ROUNDS + 1):
                color = self._rng.choice([key for key in TARGETS if key != previous])
                previous = color
                if await self._play_round(context, color):
                    self._stars += 1
                context.progress = f"Round {self._round} of {ROUNDS}, {self._stars} star{'s' * (self._stars != 1)}"
            context.show(render_game_prompt(_WELL_DONE[language], self._stars, ROUNDS))
            await context.say(_STARS[language][self._stars])
            if not await self._again(context):
                await context.say(_THANKS[language])
                return

    async def _again(self, context: GameContext) -> bool:
        """Ask "¿Otra vez?" and wait for a yes."""
        answered: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        async def answer(text: str) -> None:
            if not answered.done():
                answered.set_result(words(text) in YES_WORDS)

        await context.playing()
        context.services.ask(_AGAIN[context.language], context.language, answer)
        try:
            again = await asyncio.wait_for(answered, OTHER_ROUND_ANSWER_S)
        except asyncio.TimeoutError:
            return False
        if again:
            context.played()
        return again

    async def _play_round(self, context: GameContext, color: str) -> bool:
        """One round: name ``color``, play the tune and look until it is found or time is up."""
        language = context.language
        name = _name(color, language)
        context.progress = f"Round {self._round} of {ROUNDS}: {_name(color, 'en')}"
        context.show(render_game_prompt(name.upper(), self._stars, ROUNDS, _SCREEN_CAPTION[language]))
        await context.say(_FIND[language].format(color=name.upper() if language == "es" else name))
        started = context.played_s
        tune = asyncio.get_running_loop().create_task(self._play_tune(context, started), name="color-hunt-tune")
        try:
            found = await self._look(context, color, started)
        finally:
            tune.cancel()
        if found:
            context.played()
            context.services.emotion("happy")
            await context.say(_FOUND[language].format(color=_name(color, language, capital=True)))
            context.show(render_game_prompt(name.upper(), self._stars + 1, ROUNDS, _SCREEN_CAPTION[language]))
            await context.sleep(1.0)
            return True
        await context.say(_TIME_UP[language].format(color=name))
        return False

    async def _play_tune(self, context: GameContext, started: float) -> None:
        """Play the tune, faster as the round goes on, while the game is not paused and Reachy is free."""
        beat = 0
        while context.played_s - started < ROUND_S:
            await context.playing()
            progress = min(1.0, (context.played_s - started) / ROUND_S)
            seconds = 60.0 / (START_BPM + (END_BPM - START_BPM) * progress) / 2  # two notes per beat
            if context.services.play_audio(SAMPLE_RATE, beep_note(_TUNE[beat % len(_TUNE)], seconds)):
                beat += 1
            await context.sleep(seconds)

    async def _look(self, context: GameContext, color: str, started: float) -> bool:
        """Check camera frames until two of the last three find ``color``; False when the round's time is up."""
        family = TARGETS[color][2]
        recent: deque[bool] = deque(maxlen=3)
        last_small: NDArray[np.float32] | None = None
        last_checked_at = -RECHECK_STILL_S
        already_there: str | None = None  # a color in view as the round starts
        first = True
        wrong_said_at = -WRONG_EVERY_S
        while context.played_s - started < ROUND_S:
            await context.sleep(CHECK_EVERY_S)
            frame = await asyncio.to_thread(context.services.camera)
            decoded = await asyncio.to_thread(_thumbnail, frame) if frame else None
            if decoded is None:
                continue
            picture, small = decoded
            still = last_small is not None and float(np.abs(small - last_small).mean()) < CHANGED_FRAME
            if still and context.played_s - last_checked_at < RECHECK_STILL_S:
                continue
            last_small, last_checked_at = small, context.played_s
            check = await self._check(context, picture)
            if check is None:
                continue
            logger.debug("Color hunt %s: %r", color, check)
            held = check.held >= MIN_HELD and check.color != "none"
            if first:
                first = False
                already_there = check.color if held else None
            elif already_there is not None and (not held or check.color != already_there):
                already_there = None  # it left the view (or something else came): it counts from now on
            fresh = held and check.color != already_there
            recent.append(fresh and check.color in family)
            if sum(recent) >= 2:
                logger.info("Color hunt: %s found (%r)", color, check)
                return True
            if fresh:
                context.played()
                sure_other = check.color not in family and check.probability >= MIN_WRONG_P
                if sure_other and context.played_s - wrong_said_at >= WRONG_EVERY_S:
                    wrong_said_at = context.played_s
                    language = context.language
                    await context.say(
                        _WRONG[language].format(seen=_name(check.color, language), color=_name(color, language))
                    )
                    recent.clear()
        return False

    async def _check(self, context: GameContext, picture: Image.Image) -> Check | None:
        """Ask Jev about one frame; None when it cannot answer."""
        jev = context.services.jev
        if jev is None:
            return None
        jpeg = await asyncio.to_thread(sized_jpeg, picture)
        try:
            answers = await jev({}, _QUESTIONS, {"frame": jpeg})
            held = float(answers["held"]["noul"])
            choice = answers["color"]
            color = str(choice["choice"])
            probability = float(choice["probabilities"][color])
        except (JevError, KeyError, TypeError, ValueError) as error:
            logger.info("Color check failed: %s", type(error).__name__)
            return None
        return Check(held, color if color in _COLOR_NAMES or color == "none" else "none", probability)
