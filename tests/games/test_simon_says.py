"""Tests for Simón dice: still poses Jev judges from a frame, a step back for whole-body ones, nobody loses."""

import asyncio
from typing import Any
from collections.abc import Callable, AsyncIterator

import pytest
import pytest_asyncio

from .fakes import FakeReachy, frame
from reachy_mini_conversation_app.games import hunt, simon_says
from reachy_mini_conversation_app.games.host import GameHost
from reachy_mini_conversation_app.games.catalog import parse_game_request
from reachy_mini_conversation_app.games.color_hunt import ColorHunt
from reachy_mini_conversation_app.games.simon_says import POSES, SimonSays, pose_drawings, simon_says_phrases


@pytest.fixture(autouse=True)
def fast_poses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short pose windows and frames every 20 ms."""
    monkeypatch.setattr(simon_says, "POSE_S", 0.6)
    monkeypatch.setattr(simon_says, "CHECK_EVERY_S", 0.02)
    monkeypatch.setattr(simon_says, "RECHECK_STILL_S", 0.1)
    monkeypatch.setattr(hunt, "OTHER_ROUND_ANSWER_S", 0.3)


@pytest_asyncio.fixture
async def game() -> AsyncIterator[tuple[FakeReachy, GameHost]]:
    """Run Simón dice over a pretend Reachy whose camera shows a new frame each time."""
    reachy = FakeReachy(real_clock=True)
    for seed in range(600):
        reachy.frames.append(frame(seed))
    host = GameHost(reachy.services, {"simon": SimonSays})
    yield reachy, host
    host.end("test over", say=False)
    await asyncio.sleep(0.1)


def _pose(probability: float) -> Callable[[], dict[str, dict[str, Any]]]:
    return lambda: {"pose": {"type": "noul", "noul": probability}}


async def _until(predicate: Callable[[], bool], timeout_s: float = 4.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition did not become true"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_pose_done_wins_a_star(game, monkeypatch: pytest.MonkeyPatch) -> None:
    """Saying "Simón dice: ¡tócate la nariz!", Jev sees it twice, a cheer and a happy move."""
    monkeypatch.setattr(simon_says, "POSES_PER_GAME", 1)
    monkeypatch.setattr(SimonSays, "poses", lambda self: ["nose"])
    reachy, host = game
    reachy.jev_answer = _pose(0.9)
    host.start("simon", "es")
    await _until(lambda: "¡Una estrella!" in reachy.said)
    assert reachy.said[:2] == ["¡Vamos a jugar a Simón dice!", "Simón dice: ¡tócate la nariz!"]
    assert reachy.said[2] in ("¡Muy bien!", "¡Eso!", "¡Chévere!", "¡Perfecto!")
    assert "touching their nose" in reachy.questions[-1]["pose"]["instructions"]
    assert reachy.emotions == ["happy"]


@pytest.mark.asyncio
async def test_simon_says_goes_on_to_the_color_hunt_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answering "¿Otra vez?" with "the color game" starts the color hunt in English, without a goodbye."""
    monkeypatch.setattr(simon_says, "POSES_PER_GAME", 1)
    monkeypatch.setattr(SimonSays, "poses", lambda self: ["nose"])
    reachy = FakeReachy(real_clock=True)
    reachy.jev_answer = _pose(0.4)
    host = GameHost(reachy.services, {"simon": SimonSays, "colors": ColorHunt})
    host.start("simon", "en")
    await _until(lambda: bool(reachy.asked))
    await reachy.asked[-1][1]("No, the color game!")
    await _until(lambda: host.status()["game"] == "colors")
    assert "Thanks for playing!" not in reachy.said
    host.end("test over", say=False)


@pytest.mark.asyncio
async def test_a_pose_not_done_shows_how_and_goes_on(game, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nobody does it (or Jev is unsure): "¡Así! Mira la pantalla." and no star; nobody loses."""
    monkeypatch.setattr(simon_says, "POSES_PER_GAME", 1)
    monkeypatch.setattr(SimonSays, "poses", lambda self: ["heart"])
    reachy, host = game
    reachy.jev_answer = _pose(0.4)
    host.start("simon", "en")
    await _until(lambda: "No stars this time." in reachy.said)
    assert "Simon says: make a heart with your hands!" in reachy.said
    assert "Like this! Look at the screen." in reachy.said and reachy.emotions == []


@pytest.mark.asyncio
async def test_whole_body_poses_come_last_after_one_step_back(game, monkeypatch: pytest.MonkeyPatch) -> None:
    """Close poses first; "¡Da un paso hacia atrás!" once before the body ones, "¡Ahora acércate!" after."""
    monkeypatch.setattr(simon_says, "POSES_PER_GAME", 3)
    monkeypatch.setattr(SimonSays, "poses", lambda self: ["crouch", "nose", "star"])
    reachy, host = game
    reachy.jev_answer = _pose(0.9)
    host.start("simon", "es")
    await _until(lambda: bool(reachy.asked))
    said = [line for line in reachy.said if line.startswith(("Simón", "¡Da", "¡Ahora"))]
    assert said[0] == "Simón dice: ¡tócate la nariz!"
    assert said[1] == "¡Da un paso hacia atrás!"
    assert set(said[2:4]) == {"Simón dice: ¡agáchate!", "Simón dice: ¡haz una estrella!"}
    assert said[4] == "¡Ahora acércate!" and said.count("¡Da un paso hacia atrás!") == 1


@pytest.mark.asyncio
async def test_reachy_looks_up_with_them_and_back_after(game, monkeypatch: pytest.MonkeyPatch) -> None:
    """Saying "Mira hacia arriba": his head goes up for the pose and back to the front after."""
    monkeypatch.setattr(simon_says, "POSES_PER_GAME", 1)
    monkeypatch.setattr(SimonSays, "poses", lambda self: ["look_up"])
    reachy, host = game
    reachy.jev_answer = _pose(0.9)
    host.start("simon", "es")
    await _until(lambda: "¡Una estrella!" in reachy.said)
    assert reachy.gestures == ["up", "front"]


@pytest.mark.asyncio
async def test_the_pose_drawings_are_asked_for_once(game) -> None:
    """The game asks for every pose drawing once (made in the background, kept for later games)."""
    reachy, host = game
    reachy.jev_answer = _pose(0.1)
    host.start("simon", "es")
    await _until(lambda: bool(reachy.drawn))
    assert reachy.drawn == [pose_drawings()] and len(pose_drawings()) == len(POSES)


def test_lines_are_cached_and_venezuelan() -> None:
    """Every line is a fixed, cached one; the Spanish is Venezuelan (cachetes, piso, bravo, cachos, lentes)."""
    phrases = simon_says_phrases()
    for line in (
        "Simón dice: ¡pon las manos en los cachetes!",
        "Simón dice: ¡siéntate en el piso!",
        "Simón dice: ¡pon cara de bravo!",
        "Simón dice: ¡haz cachos de toro!",
        "Simón dice: ¡haz unos lentes con los dedos!",
        "Simon says: stand on one foot!",
        "¡Da un paso hacia atrás!",
        "¡Ocho estrellas!",
    ):
        assert line in phrases
    assert "ponte de lado" not in " ".join(phrases) and len(POSES) == 47


@pytest.mark.parametrize("text", ["juguemos a Simón dice", "vamos a jugar Simón dice", "let's play Simon says"])
def test_simon_says_is_found_by_name(text: str) -> None:
    """Its names in both languages start it (after the usual yes)."""
    request = parse_game_request(text)
    assert request is not None and request.game is not None and request.game.key == "simon"
