"""Story pictures keep up with the narration: every shipped story played through the real story player.

The story player runs on a pretend clock. Each line lasts as long as its text takes to say (the recordings average
about 11 characters a second; they are not in the repository), each sound as long as sounds.yaml says, and the
children answer each question a few seconds after it. Every picture the story asks for must then reach the screen
soon, stay up long enough to be seen, and never be dropped for the next one (Eduardo, 2026-11-07: pictures came
half a minute after the lines they show).
"""

import random
from typing import Any
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pytest

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import story as story_mode
from reachy_mini_conversation_app.games.base import GameContext
from reachy_mini_conversation_app.games.story import StoryGame, StoryVoice
from reachy_mini_conversation_app.games.catalog import GAMES
from reachy_mini_conversation_app.games.story_file import (
    Line,
    Story,
    Picture,
    load_cast,
    load_common,
    load_sounds,
    load_stories,
)
from reachy_mini_conversation_app.games.big_brain_audio import RATE
from reachy_mini_conversation_app.games.big_brain_lines import shown


CHARS_PER_S = 11.0
# A picture asked for goes up within this much story time.
LATE_S = 6.0
# The first picture after the cover may wait longer: the cover stays up through the opening tune and first line.
FIRST_LATE_S = 15.0
# Each picture stays up at least this long before the next one replaces it (the TRMNL takes about 3 s to draw it).
SEEN_S = 7.0
# How long the children take to answer (a noise, a line, a pick), and how long they leave a pat.
ANSWER_S = 3.0

STORY_KEYS = sorted(load_stories())


class PretendClock(GameContext):
    """A game context whose playing time moves only as the story says, plays and waits."""

    async def sleep(self, seconds: float) -> None:
        """Pass ``seconds`` of playing time at once."""
        self.played_s += seconds


class TimedVoice(StoryVoice):
    """Silent clips as long as their text takes to say; every picture made."""

    def clip(self, line: Line, language: str) -> Any:
        """Return silence as long as ``line`` takes to say."""
        seconds = len(shown(line.tagged(language))) / CHARS_PER_S
        return np.zeros(int(seconds * RATE), dtype=np.int16)

    def screen(self, picture: Picture) -> bytes | None:
        """Return the picture's name as its screen."""
        return picture.id.encode()


class TimedSounds:
    """Silent sounds as long as sounds.yaml says; no music, so music is a quiet wait of its length."""

    def __init__(self) -> None:
        """Read each sound's length from sounds.yaml."""
        self._seconds = {name: float(sound.get("seconds", 0.0)) for name, sound in load_sounds().items()}

    def sound(self, name: str) -> Any:
        """Return silence as long as sound ``name``."""
        return np.zeros(int(self._seconds.get(name, 0.0) * RATE), dtype=np.int16)

    def music(self, name: str) -> Any:
        """Return no music, so the story waits quietly instead."""
        return None


@dataclass
class Timeline:
    """When each picture was asked for and put up, and when the story ended."""

    asked: list[tuple[float, str]]  # (story time, picture id) for every picture the story asked for
    shown: list[tuple[float, str]]  # (story time, picture id) for every screen put up
    ended_s: float


async def _play(story: Story, language: str, answer: str) -> Timeline:
    """Tell ``story`` with the children answering ``answer`` to every question (or silence for "")."""
    reachy = FakeReachy(recorded=True)
    cast = load_cast()
    game = StoryGame(
        story,
        GAMES[story.key],
        TimedVoice(Path("/nonexistent"), cast),
        load_common(),
        TimedVoice(Path("/nonexistent"), cast),
        TimedSounds(),  # type: ignore[arg-type]
        random.Random(0),
    )
    context = PretendClock(info=GAMES[story.key], language=language, services=reachy.services)
    context.running.set()
    timeline = Timeline([], [], 0.0)

    async def say_audio(rate: int, samples: Any, text: str, language: str, heard: Any, **_: Any) -> None:
        context.played_s += len(samples) / rate
        if heard is not None:
            context.played_s += ANSWER_S
            # A choice is answered with its first option's word, so its pictures are the first option's.
            await heard(answer)

    def show(screen: bytes) -> None:
        timeline.shown.append((context.played_s, screen.decode(errors="replace")))

    reachy.services.say_audio = say_audio
    reachy.services.show = show
    scene = game._scene

    def watched_scene(context: GameContext, picture: Picture | None) -> None:
        if picture is not None:
            timeline.asked.append((context.played_s, picture.id))
        scene(context, picture)

    game._scene = watched_scene  # type: ignore[method-assign]
    await game.play(context)
    timeline.ended_s = context.played_s
    return timeline


def _answer_for(story: Story) -> str:
    """Words that join in on every noise and line, and pick each choice's first option."""
    words = [step.options[0].words["es"][0] for step in story.steps if step.kind == "choice"]
    return " ".join(["¡sí!", *words])


def _problems(story: Story, timeline: Timeline) -> list[str]:
    problems = []
    shown_at = {}
    for at, picture in timeline.shown:
        shown_at.setdefault(picture, at)
    for number, (asked_at, picture) in enumerate(timeline.asked):
        if picture not in shown_at:
            problems.append(f"{picture} never shows: the next picture replaces it before it goes up")
            continue
        late = shown_at[picture] - asked_at
        allowed = FIRST_LATE_S if number == 1 else LATE_S
        if late > allowed:
            problems.append(f"{picture} goes up {late:.0f} s after the story gets to it (at most {allowed:.0f} s)")
    ends = [at for at, _ in timeline.shown[1:]] + [timeline.ended_s]
    for (at, picture), end in zip(timeline.shown, ends):
        if end - at < SEEN_S:
            problems.append(f"{picture} is up for only {end - at:.0f} s (at least {SEEN_S:.0f} s)")
    return problems


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["es", "it"])
@pytest.mark.parametrize("key", STORY_KEYS)
@pytest.mark.parametrize("joining", [True, False], ids=["children join in", "nobody joins in"])
async def test_every_picture_keeps_up_with_the_story(key: str, language: str, joining: bool) -> None:
    """Each picture goes up within a few seconds of its lines, stays up to be seen, and none is dropped."""
    story = load_stories()[key]
    timeline = await _play(story, language, _answer_for(story) if joining else "")
    assert timeline.asked, "the story asks for no pictures"
    assert _problems(story, timeline) == []


def test_the_timing_check_catches_a_late_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the old 20 s gap, Los tres cerditos fails: its pictures came half a minute late."""
    import asyncio

    monkeypatch.setattr(story_mode, "MIN_SCENE_GAP_S", 20.0)
    story = load_stories()["tres_cerditos"]
    timeline = asyncio.run(_play(story, "es", _answer_for(story)))
    assert any("goes up" in problem for problem in _problems(story, timeline))
