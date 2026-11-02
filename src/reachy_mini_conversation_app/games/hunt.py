"""The finding games' shared engine: Reachy names something, a tune speeds up, and the camera looks for it.

Each round Reachy says what to find, the screen shows it, and a tune speeds up while about twice a second a
camera frame that changed (or one every RECHECK_STILL_S if nothing did) goes to Jev with the game's questions:
is someone holding an object up to the camera, and is it what was asked for. Two of the last three frames
finding it win a star; something already in view when the round starts counts only after it has left the view.
A sure "that is something else" is said at most every WRONG_EVERY_S. Five rounds, nobody loses, then
"¿Otra vez?" (``ask_again``, Simón dice's too): a yes plays again and another kids' game named starts it.
The color hunt and the treasure hunt differ only in what they ask for and how Jev is asked.

With recorded audio (games/kids_audio.py) Richie says varied recorded lines with sounds around them, a jingle
opens the game, and a quiet music bed replaces the tune: it switches to a faster piece for the last HURRY_S, when
he also says to hurry, and in long rounds he nudges once halfway if nothing was found yet.
"""

import io
import random
import asyncio
import logging
from typing import Any, Final
from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError
from numpy.typing import NDArray

from reachy_mini_conversation_app.jev import JevError, sized_jpeg
from reachy_mini_conversation_app.games.base import UNCLEAR_ANSWER, Game, GameContext
from reachy_mini_conversation_app.eink_display import render_game_prompt
from reachy_mini_conversation_app.local_speech import SAMPLE_RATE
from reachy_mini_conversation_app.games.catalog import STOP, GAMES, KIDS_GAMES, ANOTHER_GAME, again_answer
from reachy_mini_conversation_app.games.kids_audio import KidsAudio


logger = logging.getLogger(__name__)

ROUNDS: Final[int] = 5
# Frames: one at most this often, and one this often even when nothing changed (someone holds it still).
CHECK_EVERY_S: Final[float] = 0.5
RECHECK_STILL_S: Final[float] = 1.5
# Mean absolute difference (0-255) between small gray thumbnails that counts as a changed frame.
CHANGED_FRAME: Final[float] = 4.0
# Spike thresholds (2026-10-31): held up 0.78-0.98 at arm's length, 0.35-0.71 shoved into the lens, an empty
# hand or someone walking past at most 0.28.
MIN_HELD: Final[float] = 0.5
# "That is something else" is said at most this often.
WRONG_EVERY_S: Final[float] = 5.0
# The answer to "¿Otra vez?" may start up to AGAIN_LISTEN_S after it and is waited for OTHER_ROUND_ANSWER_S
# at most; an answer that says neither yes, no nor a game is asked once more (AGAIN_ASKS in all).
AGAIN_LISTEN_S: Final[float] = 12.0
OTHER_ROUND_ANSWER_S: Final[float] = 30.0
AGAIN_ASKS: Final[int] = 2
# Held for the picture of what to find before the round starts with the word alone.
PICTURE_WAIT_S: Final[float] = 3.0
# With recorded audio: the faster music and "¡Rápido!" for the last HURRY_S of a round of at least HURRY_MIN_S,
# and one nudge at NUDGE_AT of a round of at least NUDGE_MIN_S.
HURRY_S: Final[float] = 10.0
HURRY_MIN_S: Final[float] = 25.0
NUDGE_AT: Final[float] = 0.45
NUDGE_MIN_S: Final[float] = 45.0

HELD_QUESTION: Final[dict[str, Any]] = {
    "type": "noul",
    "instructions": (
        "In `frame`, is someone holding up or showing an object to the camera, close enough to see it clearly?"
    ),
}

STARS: Final[dict[str, tuple[str, ...]]] = {
    "es": ("Esta vez, ninguna estrella.", "¡Una estrella!", "¡Dos estrellas!", "¡Tres estrellas!",
           "¡Cuatro estrellas!", "¡Cinco estrellas!", "¡Seis estrellas!", "¡Siete estrellas!", "¡Ocho estrellas!",
           "¡Nueve estrellas!", "¡Diez estrellas!"),
    "en": ("No stars this time.", "One star!", "Two stars!", "Three stars!", "Four stars!", "Five stars!",
           "Six stars!", "Seven stars!", "Eight stars!", "Nine stars!", "Ten stars!"),
}  # fmt: skip
_AGAIN: Final[dict[str, str]] = {"es": "¿Otra vez?", "en": "Play again?"}
THANKS: Final[dict[str, str]] = {"es": "¡Gracias por jugar!", "en": "Thanks for playing!"}
_WHICH: Final[dict[str, str]] = {"es": "¿A cuál? ¿{titles}?", "en": "Which one? {titles}?"}
WELL_DONE: Final[dict[str, str]] = {"es": "¡Muy bien!", "en": "Well done!"}

# The tune: a bouncy figure on a C major arpeggio, staccato, sped up from START_BPM to END_BPM over the round.
START_BPM: Final[float] = 100.0
END_BPM: Final[float] = 180.0
_TUNE: Final[tuple[float, ...]] = (523.25, 659.25, 783.99, 659.25, 523.25, 659.25, 783.99, 1046.5)
_TUNE_LEVEL: Final[float] = 0.18


def hunt_phrases() -> frozenset[str]:
    """Return the lines every finding game shares, in both languages, for the speech cache."""
    return frozenset(
        line
        for language in ("es", "en")
        for line in (_AGAIN[language], THANKS[language], _which(language), *STARS[language])
    )


def _which(language: str) -> str:
    """Return "¿A cuál? ¿A los colores, a buscar tesoros o a Simón dice?"."""
    titles = [GAMES[key].title[language] for key in KIDS_GAMES]
    joined = ", ".join(titles[:-1]) + (" o " if language == "es" else " or ") + titles[-1]
    return _WHICH[language].format(titles=joined[:1].upper() + joined[1:])


def beep_note(frequency: float, seconds: float) -> NDArray[np.int16]:
    """Return one staccato note of the tune: the note for 70% of the beat, then silence."""
    samples = int(SAMPLE_RATE * seconds)
    sounding = int(samples * 0.7)
    time_s = np.arange(sounding) / SAMPLE_RATE
    fade = np.minimum(1.0, np.minimum(time_s, time_s[-1] - time_s) / 0.008) if sounding > 1 else np.ones(sounding)
    tone = np.sin(2 * np.pi * frequency * time_s) * fade * _TUNE_LEVEL * 32767
    return np.concatenate([tone, np.zeros(samples - sounding)]).astype(np.int16)


def thumbnail(jpeg: bytes) -> tuple[Image.Image, NDArray[np.float32]] | None:
    """Decode a camera frame: the picture, and a small gray copy to tell whether anything changed."""
    try:
        with Image.open(io.BytesIO(jpeg)) as opened:
            picture = opened.convert("RGB")
    except (UnidentifiedImageError, OSError):
        return None
    small = np.asarray(picture.convert("L").resize((48, 27)), dtype=np.float32)
    return picture, small


async def ask_again(context: GameContext, audio: KidsAudio | None) -> str | None:
    """Ask "¿Otra vez?" (recorded with ``audio``) and return the kids' game to play next, or None to stop.

    A yes anywhere in the answer ("¡Sí, otra, otra!") plays this game again; another kids' game named ("no, ¡el
    tesoro!") plays that one; "otro juego" asks which; a no or silence stops. An answer that says none of these
    (or that the speech model could not read) is asked once more.
    """
    playing = context.info.key
    choice: str | None = None
    for _ in range(AGAIN_ASKS):
        if audio is None:
            text = await _ask(context, _AGAIN[context.language])
        else:
            text = await audio.ask(context, "again", OTHER_ROUND_ANSWER_S, listen_s=AGAIN_LISTEN_S)
        if not text:
            break  # silence
        choice = again_answer(text, playing) if text != UNCLEAR_ANSWER else None
        if choice == ANOTHER_GAME:
            which = await _ask(context, _which(context.language))
            choice = again_answer(which, playing) if which else STOP
            choice = choice if choice in KIDS_GAMES else STOP
        if choice is not None:
            break
    logger.info("%s: play again answer=%s", playing, choice or "none")
    if choice in KIDS_GAMES:
        context.played()
        return choice
    return None


async def _ask(context: GameContext, text: str) -> str | None:
    """Say ``text`` in Richie's own voice and return the answer (None when none came)."""
    answered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def answer(said: str) -> None:
        if not answered.done():
            answered.set_result(said)

    await context.playing()
    context.services.ask(text, context.language, answer)
    try:
        return await asyncio.wait_for(answered, OTHER_ROUND_ANSWER_S)
    except asyncio.TimeoutError:
        return None


@dataclass(frozen=True)
class Check:
    """Jev's verdict on one frame, as the game reads it."""

    held: float  # someone holds an object up to the camera
    seen: str  # what it is, as far as the game tells ("none" when nothing is held up)
    match: bool  # it is what this round asks for
    wrong: bool  # it is surely something else
    probability: float = 0.0


class Hunt(Game):
    """A finding game: subclasses say what to find, how Jev is asked, and what Reachy says."""

    round_s: float = 30.0
    # The game's recorded moments start with this ("colors_find_red"); see games/kids_lines.py.
    prefix: str = ""

    def __init__(self, rng: random.Random | None = None, audio: KidsAudio | None = None) -> None:
        """Pick what to find with ``rng`` (seeded in tests); speak with ``audio`` when it is given."""
        self._rng = rng or random.Random()
        self._audio = audio
        self._stars = 0
        self._round = 0

    # What each game provides -----------------------------------------------------------------------

    def targets(self) -> list[str]:
        """Every target key the game can ask for."""
        raise NotImplementedError

    def questions(self, target: str) -> dict[str, dict[str, Any]]:
        """Jev's questions about a frame while ``target`` is asked for (HELD_QUESTION as "held" included)."""
        raise NotImplementedError

    def judge(self, answers: dict[str, dict[str, Any]], target: str) -> Check:
        """Read Jev's ``answers`` (may raise KeyError, TypeError or ValueError on a malformed answer)."""
        raise NotImplementedError

    def line(self, kind: str, target: str, language: str, seen: str = "") -> str:
        """Return the line for ``kind``: "start", "find", "found", "wrong" (``seen`` is what Jev saw) or "time_up"."""
        raise NotImplementedError

    def name(self, target: str, language: str) -> str:
        """Return the target's name as the screen shows it."""
        raise NotImplementedError

    def moments(self, kind: str, target: str, seen: str = "") -> tuple[str, ...]:
        """Return the recorded moments for ``kind`` (as ``line``); "wrong" names the target again."""
        prefix = self.prefix
        return {
            "start": (f"{prefix}_start",),
            "find": (f"{prefix}_find_{target}",),
            "found": (f"{prefix}_found_{target}",),
            "wrong": ("wrong", f"{prefix}_again_{target}"),
            "time_up": ("time_up",),
        }[kind]

    def prepare(self, context: GameContext) -> None:
        """Get ready as the game starts (the treasure hunt draws its missing pictures)."""

    async def screen(self, context: GameContext, target: str, stars: int) -> bytes:
        """Return the round's screen: the target's name (and picture) above ``stars`` stars."""
        caption = "Busca…" if context.language == "es" else "Find…"
        return render_game_prompt(self.name(target, context.language).upper(), stars, ROUNDS, caption)

    # Playing ---------------------------------------------------------------------------------------

    def paused(self, context: GameContext) -> None:
        """Nothing to stop: the tune waits for play to resume, and a wake already cut what was queued."""

    async def _say(
        self, context: GameContext, kind: str, target: str = "", seen: str = "", *, sounds: tuple[str, ...] = (),
        before: tuple[str, ...] = (), after: tuple[str, ...] = (),
    ) -> None:  # fmt: skip
        """Say the line for ``kind``: recorded (with ``before`` moments and the sounds) when there is audio."""
        if self._audio is None:
            await context.say(self.line(kind, target, context.language, seen))
            return
        await self._audio.say(context, *before, *self.moments(kind, target, seen), sounds=sounds, after=after)

    async def play(self, context: GameContext) -> None:
        """Play rounds of five until nobody wants another (or another kids' game is asked for)."""
        language = context.language
        audio = self._audio
        self.prepare(context)
        await self._say(context, "start", sounds=(f"intro_{self.prefix}",))
        previous = ""
        while True:
            self._stars = 0
            left = [key for key in self.targets() if key != previous]
            self._rng.shuffle(left)
            for self._round in range(1, ROUNDS + 1):
                if not left:
                    left = [key for key in self.targets() if key != previous]
                    self._rng.shuffle(left)
                target = previous = left.pop()
                if await self._play_round(context, target):
                    self._stars += 1
                context.progress = f"Round {self._round} of {ROUNDS}, {self._stars} star{'s' * (self._stars != 1)}"
            context.show(render_game_prompt(WELL_DONE[language], self._stars, ROUNDS))
            if audio is None:
                await context.say(STARS[language][self._stars])
            else:
                await audio.say(context, f"stars_{self._stars}", sounds=("celebrate",) if self._stars else ())
            following = await ask_again(context, audio)
            if following == self.info.key:
                continue
            context.next_game = following
            if following is None:
                if audio is None:
                    await context.say(THANKS[language])
                else:
                    await audio.say(context, "thanks")
            return

    async def _play_round(self, context: GameContext, target: str) -> bool:
        """One round: name ``target``, play the tune and look until it is found or time is up."""
        context.progress = f"Round {self._round} of {ROUNDS}: {self.name(target, 'en')}"
        context.show(await self.screen(context, target, self._stars))
        opening = "round_first" if self._round == 1 else "round_last" if self._round == ROUNDS else "round_next"
        await self._say(context, "find", target, sounds=("round",), before=(opening,))
        started = context.played_s
        loop = asyncio.get_running_loop()
        tunes = [loop.create_task(self._play_tune(context, started), name="hunt-tune")]
        if self._audio is not None:
            tunes.append(loop.create_task(self._coach(context, started), name="hunt-coach"))
        try:
            found = await self._look(context, target, started)
        finally:
            for task in tunes:
                task.cancel()
        if found:
            context.played()
            context.services.emotion("happy")
            await self._say(context, "found", target, sounds=("found",), after=("star",))
            context.show(await self.screen(context, target, self._stars + 1))
            await context.sleep(1.0)
            return True
        await self._say(context, "time_up", target, sounds=("time_up",))
        return False

    async def _coach(self, context: GameContext, started: float) -> None:
        """With recorded audio: one nudge halfway through a long round, and "¡Rápido!" near the end."""
        assert self._audio is not None
        cues = []
        if self.round_s >= NUDGE_MIN_S:
            cues.append((self.round_s * NUDGE_AT, "nudge"))
        if self.round_s >= HURRY_MIN_S:
            cues.append((self.round_s - HURRY_S, "hurry"))
        for at, moment in cues:
            await context.sleep(max(0.0, at - (context.played_s - started)))
            await self._audio.say(context, moment)

    async def _play_tune(self, context: GameContext, started: float) -> None:
        """Play the tune, faster as the round goes on, while the game is not paused and Reachy is free.

        With recorded music, the music bed instead: the faster piece for the last HURRY_S.
        """
        if self._audio is not None and self._audio.music("hunt") is not None:
            hurry_from = self.round_s - HURRY_S if self.round_s >= HURRY_MIN_S else self.round_s
            await self._audio.play_music(
                context,
                lambda elapsed: "hunt_hurry" if elapsed >= hurry_from else "hunt",
                lambda: context.played_s - started >= self.round_s,
            )
            return
        beat = 0
        while context.played_s - started < self.round_s:
            await context.playing()
            progress = min(1.0, (context.played_s - started) / self.round_s)
            seconds = 60.0 / (START_BPM + (END_BPM - START_BPM) * progress) / 2  # two notes per beat
            if context.services.play_audio(SAMPLE_RATE, beep_note(_TUNE[beat % len(_TUNE)], seconds)):
                beat += 1
            await context.sleep(seconds)

    async def _look(self, context: GameContext, target: str, started: float) -> bool:
        """Check camera frames until two of the last three find ``target``; False when the round's time is up."""
        recent: deque[bool] = deque(maxlen=3)
        last_small: NDArray[np.float32] | None = None
        last_checked_at = -RECHECK_STILL_S
        already_there: str | None = None  # what was in view as the round started
        first = True
        wrong_said_at = -WRONG_EVERY_S
        while context.played_s - started < self.round_s:
            await context.sleep(CHECK_EVERY_S)
            frame = await asyncio.to_thread(context.services.camera)
            decoded = await asyncio.to_thread(thumbnail, frame) if frame else None
            if decoded is None:
                continue
            picture, small = decoded
            still = last_small is not None and float(np.abs(small - last_small).mean()) < CHANGED_FRAME
            if still and context.played_s - last_checked_at < RECHECK_STILL_S:
                continue
            last_small, last_checked_at = small, context.played_s
            check = await self._check(context, picture, target)
            if check is None:
                continue
            logger.debug("%s, looking for %s: %r", self.info.key, target, check)
            held = check.held >= MIN_HELD and check.seen != "none"
            if first:
                first = False
                already_there = check.seen if held else None
            elif already_there is not None and (not held or check.seen != already_there):
                already_there = None  # it left the view (or something else came): it counts from now on
            fresh = held and check.seen != already_there
            recent.append(fresh and check.match)
            if sum(recent) >= 2:
                logger.info("%s: %s found (%r)", self.info.key, target, check)
                return True
            if fresh:
                context.played()
                if check.wrong and not check.match and context.played_s - wrong_said_at >= WRONG_EVERY_S:
                    wrong_said_at = context.played_s
                    await self._say(context, "wrong", target, check.seen, sounds=("boing",))
                    recent.clear()
        return False

    async def _check(self, context: GameContext, picture: Image.Image, target: str) -> Check | None:
        """Ask Jev about one frame; None when it cannot answer."""
        jev = context.services.jev
        if jev is None:
            return None
        jpeg = await asyncio.to_thread(sized_jpeg, picture)
        try:
            return self.judge(await jev({}, self.questions(target), {"frame": jpeg}), target)
        except (JevError, KeyError, TypeError, ValueError) as error:
            logger.info("%s check failed: %s", self.info.key, type(error).__name__)
            return None
