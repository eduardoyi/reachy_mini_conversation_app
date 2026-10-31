"""Tests for the color hunt: rounds, the tune, and how camera frames judged by Jev find a color."""

import asyncio
from typing import Any
from collections.abc import Callable, AsyncIterator

import numpy as np
import pytest
import pytest_asyncio

from .fakes import FakeReachy, seen, frame
from reachy_mini_conversation_app.games import color_hunt
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.color_hunt import TARGETS, ColorHunt, color_hunt_phrases


@pytest.fixture(autouse=True)
def fast_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """One short round, frames every 20 ms, and only red to find unless a test changes it."""
    monkeypatch.setattr(color_hunt, "ROUNDS", 1)
    monkeypatch.setattr(color_hunt, "ROUND_S", 0.8)
    monkeypatch.setattr(color_hunt, "CHECK_EVERY_S", 0.02)
    monkeypatch.setattr(color_hunt, "RECHECK_STILL_S", 0.1)
    monkeypatch.setattr(color_hunt, "WRONG_EVERY_S", 0.3)
    monkeypatch.setattr(color_hunt, "OTHER_ROUND_ANSWER_S", 0.3)
    monkeypatch.setattr(color_hunt, "TARGETS", {"red": TARGETS["red"]})


@pytest_asyncio.fixture
async def game() -> AsyncIterator[tuple[FakeReachy, GameHost]]:
    """Run a color hunt host over a pretend Reachy whose camera shows a new frame each time."""
    reachy = FakeReachy(real_clock=True)
    for seed in range(400):
        reachy.frames.append(frame(seed))
    host = GameHost(reachy.services, {"colors": ColorHunt})
    yield reachy, host
    host.end("test over", say=False)
    await asyncio.sleep(0.1)


def _answers(*script: dict[str, dict[str, Any]]) -> Callable[[], dict[str, dict[str, Any]]]:
    """Jev's answers in order, the last one repeated."""
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
async def test_two_frames_of_the_color_find_it_and_win_a_star(game) -> None:
    """A red toy held up: "¡Sí! ¡Rojo!", a happy move, one star; "no" to another round ends the game."""
    reachy, host = game
    reachy.jev_answer = _answers(seen("none", held=0.05), seen("red"))
    host.start("colors", "es")
    await _until(lambda: "¡Sí! ¡Rojo!" in reachy.said)
    assert reachy.said[:2] == ["¡Vamos a jugar a los colores!", "¡Busca algo ROJO!"]
    assert reachy.emotions == ["happy"]
    await _until(lambda: bool(reachy.asked))
    assert "¡Una estrella!" in reachy.said and reachy.asked[-1][0] == "¿Otra vez?"
    await reachy.asked[-1][1]("no")
    await _until(lambda: host.state is GameState.IDLE)
    assert reachy.said[-1] == "¡Gracias por jugar!" and reachy.restored == 1


@pytest.mark.asyncio
async def test_red_accepts_orange_because_the_camera_shows_red_as_orange(game) -> None:
    """The spike read a red car as orange in nearly every frame; it still counts as red."""
    reachy, host = game
    reachy.jev_answer = _answers(seen("none", held=0.05), seen("orange"))
    host.start("colors", "es")
    await _until(lambda: "¡Sí! ¡Rojo!" in reachy.said)


@pytest.mark.asyncio
async def test_a_sure_wrong_color_is_named_and_time_runs_out(game) -> None:
    """A blue block for red: "Mmm, eso es azul"; nothing red by the end: "Era el rojo"."""
    reachy, host = game
    reachy.jev_answer = _answers(seen("none", held=0.05), seen("blue"))
    host.start("colors", "es")
    await _until(lambda: "¡Se acabó el tiempo! Era el rojo." in reachy.said)
    assert "Mmm, eso es azul. ¡Busca algo rojo!" in reachy.said
    assert reachy.emotions == []


@pytest.mark.asyncio
async def test_an_unsure_or_not_held_color_finds_nothing(game) -> None:
    """Red that nobody holds up (held 0.2), or another color Jev is unsure of, neither counts nor is named."""
    reachy, host = game
    reachy.jev_answer = _answers(seen("none", held=0.05), seen("red", held=0.2), seen("blue", probability=0.5))
    host.start("colors", "es")
    await _until(lambda: "¡Se acabó el tiempo! Era el rojo." in reachy.said)
    assert not any(line.startswith(("¡Sí!", "Mmm")) for line in reachy.said)


@pytest.mark.asyncio
async def test_a_color_already_in_view_counts_only_after_it_left(game, monkeypatch: pytest.MonkeyPatch) -> None:
    """A red bottle in front of the camera from the start is not a find, until it goes and comes back."""
    monkeypatch.setattr(color_hunt, "ROUND_S", 3.0)
    reachy, host = game
    reachy.jev_answer = _answers(*[seen("red")] * 6, seen("none", held=0.05), seen("red"))
    host.start("colors", "es")
    await _until(lambda: reachy.jev_calls >= 6)
    assert "¡Sí! ¡Rojo!" not in reachy.said
    await _until(lambda: "¡Sí! ¡Rojo!" in reachy.said)


@pytest.mark.asyncio
async def test_an_unchanged_frame_is_checked_only_now_and_then(game) -> None:
    """An empty room (the same frame) is sent to Jev every RECHECK_STILL_S, not every frame."""
    reachy, host = game
    reachy.frames.clear()
    reachy.frames.append(frame(1))
    reachy.jev_answer = _answers(seen("none", held=0.05))
    host.start("colors", "es")
    await _until(lambda: "¡Se acabó el tiempo! Era el rojo." in reachy.said)
    assert 3 <= reachy.jev_calls <= 12  # 0.8 s at 0.1 s rechecks, not 40 frames


@pytest.mark.asyncio
async def test_the_tune_plays_while_looking_and_speeds_up(game) -> None:
    """Notes play through the round, each shorter than the last few as the tempo rises."""
    reachy, host = game
    reachy.jev_answer = _answers(seen("none", held=0.05))
    host.start("colors", "es")
    await _until(lambda: "¡Se acabó el tiempo! Era el rojo." in reachy.said)
    lengths = [samples.size for samples in reachy.audio]
    assert len(lengths) >= 4 and lengths[-1] < lengths[0]
    assert all(samples.dtype == np.int16 for samples in reachy.audio)


@pytest.mark.asyncio
async def test_a_pause_stops_the_tune_and_the_clock(game) -> None:
    """Paused, no notes play and the round's time does not run out."""
    reachy, host = game
    reachy.jev_answer = _answers(seen("none", held=0.05))
    host.start("colors", "es")
    await _until(lambda: len(reachy.audio) >= 2)
    host.pause()
    await asyncio.sleep(0.05)
    notes = len(reachy.audio)
    await asyncio.sleep(1.0)  # longer than the round
    assert len(reachy.audio) == notes and "¡Se acabó el tiempo! Era el rojo." not in reachy.said
    host.resume()
    await _until(lambda: "¡Se acabó el tiempo! Era el rojo." in reachy.said)


def test_every_line_is_cached_and_no_wrong_line_names_an_accepted_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """All lines are fixed (cached); red's lines never call orange wrong."""
    monkeypatch.setattr(color_hunt, "TARGETS", TARGETS)
    phrases = color_hunt_phrases()
    assert {"¡Busca algo ROJO!", "Find something red!", "¡Sí! ¡Rosa!", "Time's up! It was black."} <= phrases
    assert "Mmm, eso es azul. ¡Busca algo rojo!" in phrases
    assert "Mmm, eso es naranja. ¡Busca algo rojo!" not in phrases
    assert "Mmm, eso es naranja. ¡Busca algo azul!" in phrases
