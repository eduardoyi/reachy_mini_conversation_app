"""The kids' games' audio: Richie's recorded lines, short sounds and a quiet music bed.

Lines are the clips ``scripts/big_brain_voice.py --set kids`` made from ``games/kids_lines.py`` (``<folder>/voice/<moment>/
<language>-NN.wav``), a different take each time; sounds and music are the takes ``scripts/big_brain_sounds.py
--set kids`` made (``<folder>/sounds``, ``chosen.json``). A moment with no clip is said in Richie's everyday voice
instead, so the games work before (or without) any recording. Music plays as Reachy's ambient audio, a little
ahead of time, under everything else: it stops by itself while he speaks or listens and goes on afterwards.
Every sound is levelled by its loudness (the takes ElevenLabs makes differ by 15 dB or more) for a living room
with adults in it: the voice's clips are about -20 dB RMS; sounds sit a little under them, jingles about level
and the music bed well under.
"""

import random
import asyncio
from typing import Final
from pathlib import Path
from collections.abc import Mapping, Callable, Sequence

import numpy as np

from reachy_mini_conversation_app.games.base import GameContext
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, Clips, Sounds, join
from reachy_mini_conversation_app.games.big_brain_lines import shown


# Loudness (RMS, dB below full scale) of sounds, of the jingles that open and end a game, and of the music bed.
SOUND_DB: Final[float] = -24.0
JINGLE_DB: Final[float] = -22.0
MUSIC_DB: Final[float] = -33.0
JINGLES: Final[frozenset[str]] = frozenset({"intro_colors", "intro_treasure", "intro_simon", "celebrate"})
# No sample louder than this (dB below full scale) after levelling.
PEAK_DB: Final[float] = -1.0
# Music goes out in pieces of CHUNK_S, at most AHEAD_S ahead of what is playing, so it stops soon after a find.
CHUNK_S: Final[float] = 0.5
AHEAD_S: Final[float] = 0.8
# The end of a music take blends into its start over LOOP_FADE_S, so it loops without a jump.
LOOP_FADE_S: Final[float] = 0.4


def leveled(samples: Audio, target_db: float) -> Audio:
    """Return ``samples`` scaled to ``target_db`` RMS, less when a peak would pass PEAK_DB; silence unchanged."""
    floats = samples.astype(np.float32)
    rms = float(np.sqrt(np.mean(floats**2))) if len(floats) else 0.0
    peak = float(np.abs(floats).max()) if len(floats) else 0.0
    if rms < 1.0:
        return samples
    gain = min(10 ** (target_db / 20) * 32768 / rms, 10 ** (PEAK_DB / 20) * 32768 / peak)
    scaled: Audio = np.clip(floats * gain, -32768, 32767).astype(np.int16)
    return scaled


def loopable(samples: Audio) -> Audio:
    """Return ``samples`` with its last LOOP_FADE_S faded into its first, to play round and round."""
    fade = int(RATE * LOOP_FADE_S)
    if len(samples) <= 2 * fade:
        return samples
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    head = samples[:fade].astype(np.float32) * ramp + samples[-fade:].astype(np.float32) * (1 - ramp)
    looped: Audio = np.concatenate([head.astype(np.int16), samples[fade:-fade]])
    return looped


class KidsAudio:
    """Recorded lines, sounds and music for the color hunt, the treasure hunt and Simón dice."""

    def __init__(
        self, folder: Path, lines: Mapping[str, Mapping[str, Sequence[str]]], rng: random.Random | None = None
    ) -> None:
        """Use the clips and sounds under ``folder`` for ``lines`` (games/kids_lines.py)."""
        self._lines = lines
        self._clips = Clips(folder / "voice", rng, lines=lines)
        self._sounds = Sounds(folder / "sounds")
        self._levelled: dict[str, Audio | None] = {}

    def text(self, moment: str, language: str) -> str:
        """Return what ``moment`` says, without tags (its first take)."""
        takes = self._lines.get(moment, {}).get(language, ())
        return shown(takes[0]) if takes else ""

    def sound(self, name: str) -> Audio | None:
        """Return sound (or jingle) ``name`` at its level, or None when it was not made."""
        if name not in self._levelled:
            samples = self._sounds.get(name)
            target = JINGLE_DB if name in JINGLES else SOUND_DB
            self._levelled[name] = leveled(samples, target) if samples is not None else None
        return self._levelled[name]

    def music(self, name: str) -> Audio | None:
        """Return music ``name`` ready to loop at its level, or None when it was not made."""
        key = f"music:{name}"
        if key not in self._levelled:
            samples = self._sounds.get(name)
            self._levelled[key] = loopable(leveled(samples, MUSIC_DB)) if samples is not None else None
        return self._levelled[key]

    def _pieces(
        self, language: str, moments: Sequence[str], sounds: Sequence[str], after: Sequence[str]
    ) -> tuple[Audio, str] | None:
        """Join the sounds, a take of each moment and the ``after`` sounds; None when a moment has no clip."""
        lines = [self._clips.line(moment, language) for moment in moments]
        if any(line is None for line in lines):
            return None
        pieces = [sound for name in sounds if (sound := self.sound(name)) is not None]
        pieces += [line[1] for line in lines if line is not None]
        pieces += [sound for name in after if (sound := self.sound(name)) is not None]
        text = " ".join(line[0] for line in lines if line is not None)
        return (join(*pieces), text) if pieces else None

    async def say(
        self, context: GameContext, *moments: str, sounds: Sequence[str] = (), after: Sequence[str] = ()
    ) -> None:
        """Play ``sounds``, a take of each moment, then ``after``, as one line; Richie's own voice without clips."""
        await context.playing()
        say_audio = context.services.say_audio
        joined = self._pieces(context.language, moments, sounds, after) if say_audio is not None else None
        if say_audio is None or joined is None:
            for moment in moments:
                if text := self.text(moment, context.language):
                    await context.say(text)
            return
        samples, text = joined
        await say_audio(RATE, samples, text, context.language, None)

    async def ask(
        self, context: GameContext, moment: str, seconds: float, *, listen_s: float | None = None
    ) -> str | None:
        """Say ``moment`` and return the answer, waited for ``seconds`` at most (None when none came).

        With a recorded take, the answer may start up to ``listen_s`` after it: silence then answers "" and speech
        the model could not read UNCLEAR_ANSWER.
        """
        answered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def answer(text: str) -> None:
            if not answered.done():
                answered.set_result(text)

        await context.playing()
        say_audio = context.services.say_audio
        joined = self._pieces(context.language, (moment,), (), ()) if say_audio is not None else None
        if say_audio is None or joined is None:
            context.services.ask(self.text(moment, context.language), context.language, answer)
        else:
            await say_audio(RATE, joined[0], joined[1], context.language, answer, listen_s=listen_s)
        try:
            return await asyncio.wait_for(answered, seconds)
        except asyncio.TimeoutError:
            return None

    async def play_music(self, context: GameContext, pick: Callable[[float], str], until: Callable[[], bool]) -> None:
        """Play music under the game until ``until()``; ``pick(seconds playing)`` names the piece (it may change).

        Pieces go out a little ahead of time; while Reachy speaks or listens they are refused and the music waits.
        """
        started = context.played_s
        queued = 0.0  # seconds of music handed over, counted from ``started``
        name, position = "", 0
        chunk = int(RATE * CHUNK_S)
        while not until():
            await context.playing()
            elapsed = context.played_s - started
            if queued - elapsed < AHEAD_S:
                wanted = pick(elapsed)
                samples = self.music(wanted)
                if samples is None:
                    return
                if wanted != name:
                    name, position = wanted, 0
                piece = np.take(samples, np.arange(position, position + chunk), mode="wrap")
                if context.services.play_audio(RATE, piece):
                    position = (position + chunk) % len(samples)
                    queued = max(queued, elapsed) + CHUNK_S
                else:
                    queued = elapsed  # he is speaking: nothing is waiting to play
            await asyncio.sleep(0.05)
