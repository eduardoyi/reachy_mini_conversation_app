"""Tests for the treasure hunt: things around the house, a drawing for the youngest, and "eso no"."""

import io
import asyncio
from typing import Any
from collections.abc import Callable, AsyncIterator

import pytest
import pytest_asyncio
from PIL import Image

from .fakes import FakeReachy, frame
from reachy_mini_conversation_app.games import hunt
from reachy_mini_conversation_app.games.host import GameHost
from reachy_mini_conversation_app.eink_display import render_game_prompt
from reachy_mini_conversation_app.games.catalog import parse_game_request
from reachy_mini_conversation_app.games.treasure_hunt import (
    TREASURES,
    TreasureHunt,
    treasure_drawings,
    treasure_hunt_phrases,
)


@pytest.fixture(autouse=True)
def fast_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """One short round looking for a ball, frames every 20 ms."""
    monkeypatch.setattr(hunt, "ROUNDS", 1)
    monkeypatch.setattr(TreasureHunt, "round_s", 0.8)
    monkeypatch.setattr(hunt, "CHECK_EVERY_S", 0.02)
    monkeypatch.setattr(hunt, "RECHECK_STILL_S", 0.1)
    monkeypatch.setattr(hunt, "WRONG_EVERY_S", 0.3)
    monkeypatch.setattr(hunt, "OTHER_ROUND_ANSWER_S", 0.3)
    monkeypatch.setattr(TreasureHunt, "targets", lambda self: ["ball"])


@pytest_asyncio.fixture
async def game() -> AsyncIterator[tuple[FakeReachy, GameHost]]:
    """Run a treasure hunt host over a pretend Reachy whose camera shows a new frame each time."""
    reachy = FakeReachy(real_clock=True)
    for seed in range(400):
        reachy.frames.append(frame(seed))
    host = GameHost(reachy.services, {"treasure": TreasureHunt})
    yield reachy, host
    host.end("test over", say=False)
    await asyncio.sleep(0.1)


def _shown(held: float, item: float) -> dict[str, dict[str, Any]]:
    return {"held": {"type": "noul", "noul": held}, "item": {"type": "noul", "noul": item}}


def _answers(*script: dict[str, dict[str, Any]]) -> Callable[[], dict[str, dict[str, Any]]]:
    queue = list(script)

    def answer() -> dict[str, dict[str, Any]]:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return answer


async def _until(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition did not become true"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_ball_held_up_is_found(game) -> None:
    """Jev asked "is it a ball?": two sure frames find it, with the found line and a happy move."""
    reachy, host = game
    reachy.jev_answer = _answers(_shown(0.05, 0.0), _shown(0.9, 0.95))
    host.start("treasure", "es")
    await _until(lambda: "¡Sí! ¡Una pelota!" in reachy.said)
    assert reachy.said[:2] == ["¡Vamos a buscar tesoros!", "¡Busca una pelota!"]
    assert "a ball of any kind" in reachy.questions[-1]["item"]["instructions"]
    assert reachy.emotions == ["happy"]


@pytest.mark.asyncio
async def test_something_else_held_up_gets_eso_no(game) -> None:
    """A spoon for a ball: "Mmm, eso no. ¡Busca una pelota!", then the time runs out."""
    reachy, host = game
    reachy.jev_answer = _answers(_shown(0.05, 0.0), _shown(0.9, 0.05))
    host.start("treasure", "es")
    await _until(lambda: "¡Se acabó el tiempo! Era una pelota." in reachy.said)
    assert "Mmm, eso no. ¡Busca una pelota!" in reachy.said


@pytest.mark.asyncio
async def test_an_unsure_frame_neither_counts_nor_is_wrong(game) -> None:
    """Jev in two minds (0.4) about a ball: no find, no "eso no"."""
    reachy, host = game
    reachy.jev_answer = _answers(_shown(0.05, 0.0), _shown(0.9, 0.4))
    host.start("treasure", "en")
    await _until(lambda: "Time's up! It was a ball." in reachy.said)
    assert not any(line.startswith(("Yes!", "Hmm")) for line in reachy.said)


@pytest.mark.asyncio
async def test_the_screen_shows_the_drawing_when_it_is_made_and_missing_ones_are_drawn(game) -> None:
    """With the ball's drawing kept, the screen has it; the game asks for the missing drawings once."""
    reachy, host = game
    reachy.pictures["a ball"] = Image.new("L", (300, 300), 170)
    reachy.jev_answer = _answers(_shown(0.05, 0.0))
    host.start("treasure", "es")
    await _until(lambda: len(reachy.screens) >= 1)
    assert reachy.drawn == [treasure_drawings()]
    with_picture = Image.open(io.BytesIO(reachy.screens[0])).convert("L")
    reachy.pictures.clear()
    host.end("again", say=False)
    host.start("treasure", "es")
    await _until(lambda: len(reachy.screens) >= 2)
    word_only = Image.open(io.BytesIO(reachy.screens[-1])).convert("L")
    # The drawing sits on the left; without it the screen is the word alone, as the color hunt shows it.
    assert with_picture.crop((20, 40, 440, 440)).getextrema()[0] < 255
    assert reachy.screens[-1] == render_game_prompt("UNA PELOTA", 0, 1, "Busca…")
    assert word_only.size == (800, 480)


def test_every_line_is_cached_and_lego_is_named_plainly() -> None:
    """Fixed lines in both languages; the household asked for "un Lego", not "un bloque"."""
    phrases = treasure_hunt_phrases()
    assert {"¡Busca un Lego!", "¡Sí! ¡Un Lego!", "Find something round!", "Hmm, not that one. Find a sock!"} <= phrases
    assert "bloque" not in " ".join(phrases)
    assert TREASURES["lego"].drawing == "a Lego brick"


@pytest.mark.parametrize(
    "text", ["juguemos a buscar tesoros", "vamos a jugar al tesoro", "let's play treasure hunt", "scavenger hunt"]
)
def test_the_treasure_hunt_is_found_by_name(text: str) -> None:
    """Its names in both languages start it (after the usual yes)."""
    request = parse_game_request(text)
    assert request is not None and request.game is not None and request.game.key == "treasure"
