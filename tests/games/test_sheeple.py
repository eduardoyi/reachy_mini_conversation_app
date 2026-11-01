"""Tests for Sheeple: picking boards, judging guesses, the board screen and a whole game with pretend phones."""

import io
import json
import random
import asyncio
from typing import Any
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import sheeple
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.catalog import parse_game_request
from reachy_mini_conversation_app.games.sheeple import Survey, Sheeple, SheepleSetup, survey, pick_surveys
from reachy_mini_conversation_app.games.big_brain_board import read_history
from reachy_mini_conversation_app.games.big_brain_judge import Judge
from reachy_mini_conversation_app.games.sheeple_screens import render_survey
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player


def _entry(index: int, *, kind: str = "board", kids: bool = True, adult: bool = False, us_only: bool = False) -> dict[str, Any]:  # fmt: skip
    """Return a board with its answers out of order (the game sorts them)."""
    return {
        "id": f"s-{index:04d}",
        "kind": kind,
        "question": {"en": f"Name something in a kitchen ({index})", "es": f"Algo que hay en una cocina ({index})"},
        "answers": [
            {"en": "Fridge", "es": "Nevera", "count": 41, "accept": ["refrigerator", "refri", "heladera"]},
            {"en": "Stove", "es": "Cocina", "count": 12, "accept": ["oven", "estufa", "horno"]},
            {"en": "Sink", "es": "Fregadero", "count": 27, "accept": ["lavaplatos"]},
        ][: 2 if kind == "quick" else 3],
        "us_only": us_only,
        "adult": adult,
        "kids": kids,
    }


def _bank() -> list[dict[str, Any]]:
    return [
        *(_entry(index) for index in range(8)),
        _entry(8, kids=False),
        _entry(9, adult=True),
        _entry(10, us_only=True),
        _entry(11, kind="quick"),
    ]


# Picking boards --------------------------------------------------------------------------------------


def test_a_board_reads_one_language_sorts_its_answers_and_accepts_both_languages() -> None:
    """Answers come most popular first, in the game's language, and either language's name counts."""
    board = survey(_entry(1), "es")
    assert board is not None and board.question.startswith("Algo que hay")
    assert [(answer.text, answer.count) for answer in board.answers] == [("Nevera", 41), ("Fregadero", 27), ("Cocina", 12)]  # fmt: skip
    assert {"Fridge", "Nevera", "refri"} <= set(board.answers[0].accept)
    assert survey(_entry(11, kind="quick"), "en") is None  # two answers are too few for a board


def test_kids_mode_leaves_out_adult_us_only_and_untagged_boards_and_quick_ones_never_play() -> None:
    """Kids get only boards tagged for kids; adults get every board; a quick board is never a board."""
    kids = {board.id for board in pick_surveys(_bank(), "es", kids=True, count=20, rng=random.Random(1))}
    adults = {board.id for board in pick_surveys(_bank(), "es", count=20, rng=random.Random(1))}
    assert kids == {f"s-{index:04d}" for index in range(8)}
    assert adults == kids | {"s-0008", "s-0009", "s-0010"}


def test_boards_played_lately_come_last() -> None:
    """A board from the last month plays only when the bank has nothing newer."""
    recent = {f"s-{index:04d}": 1000.0 + index for index in range(6)}
    picked = [board.id for board in pick_surveys(_bank(), "en", kids=True, recent=recent, count=4, rng=random.Random(2))]  # fmt: skip
    assert picked[:2] == ["s-0006", "s-0007"] or picked[:2] == ["s-0007", "s-0006"]
    assert picked[2:] == ["s-0000", "s-0001"]


# Judging --------------------------------------------------------------------------------------------


def _game(tmp_path: Path, jev: Any) -> Sheeple:
    game = Sheeple(SheepleSetup(data_dir=tmp_path, port=0))
    game._judge = Judge(tmp_path / "sheeple" / "rulings.json", jev)
    return game


@pytest.mark.asyncio
async def test_a_guess_names_its_answer_by_code_in_either_language(tmp_path: Path) -> None:
    """ "La refri", "the fridge" and "estufa" find their answers without asking Jev; an empty guess finds none."""

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        raise AssertionError("Jev asked")

    game = _game(tmp_path, jev)
    board = survey(_entry(1), "es")
    assert board is not None
    assert await game.find(board, "la refri", spoken=False) == 0
    assert await game.find(board, "The fridge", spoken=True) == 0
    assert await game.find(board, "estufa", spoken=False) == 2
    assert await game.find(board, "  ", spoken=False) is None


@pytest.mark.asyncio
async def test_jev_rules_on_each_answer_and_the_most_popular_hit_wins(tmp_path: Path) -> None:
    """A guess code cannot place goes to Jev once per answer (typed: no "sounds like"); rulings are kept."""
    asked: list[tuple[str, frozenset[str]]] = []

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        asked.append((state["accepted_answers"][0], frozenset(questions)))
        right = 0.9 if state["accepted_answers"][0] in ("refrigerator", "lavaplatos") else 0.1
        return {"right": {"noul": right}, "too_broad": {"noul": 0.1}, "sounds_like": {"noul": 0.1}}

    game = _game(tmp_path, jev)
    board = survey(_entry(1), "es")
    assert board is not None
    assert await game.find(board, "donde guardo la leche", spoken=False) == 0
    assert len(asked) == 3 and all(questions == {"right", "too_broad"} for _, questions in asked)
    assert await _game(tmp_path, jev).find(board, "Donde guardo la leche", spoken=False) == 0
    assert len(asked) == 3  # kept in rulings.json


@pytest.mark.asyncio
async def test_without_jev_an_unplaced_guess_is_a_miss(tmp_path: Path) -> None:
    """With Jev unreachable a guess counts only when code places it."""
    board = survey(_entry(1), "en")
    assert board is not None
    assert await _game(tmp_path, None).find(board, "a toaster", spoken=True) is None


# The screen -----------------------------------------------------------------------------------------


def test_the_board_screen_draws_hidden_and_revealed_answers() -> None:
    """The TRMNL board is an 800×480 greyscale PNG with any number of slots."""
    png = render_survey(
        "Le preguntamos a cien gringos: algo que hay en una cocina y que nadie limpia nunca jamás",
        [("Nevera", 41), None, None, ("Una cosa con un nombre muy largo que no cabe", 3), None, None, None],
        "Ronda 2/6: por equipos",
        [("Rojo", 120), ("Azul", 80)],
        "es",
        turn="Rojo",
        misses=2,
    )
    assert Image.open(io.BytesIO(png)).size == (800, 480)


# Starting --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "language"),
    [
        ("Juguemos Sheeple", "es"),
        ("let's play Sheeple", "en"),
        ("juguemos shipul", "es"),
        ("let's play sheep pull", "en"),
        ("juguemos el juego de las encuestas", "es"),
        ("let's play the survey game", "en"),
    ],
)
def test_sheeple_starts_by_its_name_and_its_mishearings(said: str, language: str) -> None:
    """The name, a few ways speech recognition writes it, and "the survey game" all start Sheeple."""
    request = parse_game_request(said)
    assert request is not None and request.game is not None
    assert (request.game.key, request.language) == ("sheeple", language)


# A whole game ----------------------------------------------------------------------------------------


class _Room:
    """Two pretend phones: Ana (red) always guesses the top answer, Luis (blue) never hits."""

    def __init__(self, game: Sheeple) -> None:
        self.game = game
        self.red = Player("p1", "Ana", "red", 0.0)
        self.blue = Player("p2", "Luis", "blue", 1.0)

    def join(self) -> None:
        phones = self.game._phones
        assert phones is not None
        for player in (self.red, self.blue):
            phones.players[player.id] = player
            self.game.on_message(player, {"type": "joined"})

    async def play(self) -> None:
        game = self.game
        while True:
            await asyncio.sleep(0.02)
            for player in (self.red, self.blue):
                snapshot = game.snapshot(player)
                can = snapshot["can"]
                if "start" in can and player is self.red:
                    game.on_message(player, {"type": "start"})
                elif "answer" in can and snapshot["guess"] is None:
                    text = "the fridge" if player is self.red else "un pingüino"
                    game.on_message(player, {"type": "answer", "text": text})
                elif "speak" in can:
                    game.on_message(player, {"type": "speak"})


@pytest.mark.asyncio
async def test_a_whole_game_from_joining_to_the_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Six boards, flock and team in turn, the last double; red hits every time and wins; boards are kept.

    Flock rounds score each typed guess (the English "the fridge" in a Spanish game); in team rounds blue's
    miss passes the turn and red clears the board aloud, one listener at a time.
    """
    for name, value in {"FLOCK_S": 1.0, "TURN_S": 1.0, "ANSWER_S": 1.0, "AFTER_S": 0.0, "LOBBY_REMINDER_S": 5.0}.items():  # fmt: skip
        monkeypatch.setattr(sheeple, name, value)

    async def nothing(self: Phones) -> None:
        return None

    monkeypatch.setattr(Phones, "start", nothing)
    monkeypatch.setattr(Phones, "stop", nothing)
    (tmp_path / "sheeple").mkdir()
    (tmp_path / "sheeple" / "bank.jsonl").write_text("\n".join(json.dumps(entry) for entry in _bank()))
    reachy = FakeReachy(real_clock=True)

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        return {"right": {"noul": 0.0}, "too_broad": {"noul": 0.0}, "sounds_like": {"noul": 0.0}}

    reachy.services.jev = jev
    games: list[Sheeple] = []
    listened: list[str] = []

    async def say_audio(
        rate: int, samples: np.ndarray, text: str, language: str, answer: Any, *, listen_s: float | None = None
    ) -> None:
        game = games[0]
        if answer is None or game._speaker is None or game._survey is None:
            return
        listened.append(game._speaker.name)
        hidden = next(a for a in game._survey.answers if not a.shown)
        said = hidden.accept[0] if game._speaker.team == "red" else "un dinosaurio"
        asyncio.get_running_loop().create_task(answer(said))

    reachy.services.say_audio = say_audio

    def make() -> Sheeple:
        games.append(Sheeple(SheepleSetup(data_dir=tmp_path, port=0), rng=random.Random(3)))
        return games[0]

    host = GameHost(reachy.services, {"sheeple": make})
    host.start("sheeple", "es")
    await asyncio.sleep(0.1)
    room = _Room(games[0])
    room.join()
    playing = asyncio.get_running_loop().create_task(room.play())
    try:
        for _ in range(600):
            if host.state is GameState.IDLE:
                break
            await asyncio.sleep(0.05)
    finally:
        playing.cancel()
    game = games[0]
    assert host.state is GameState.IDLE and game._phase == "over"
    assert len(game._surveys) == sheeple.ROUNDS
    # Flock rounds 1, 3 and 5: Ana's "the fridge" scores 41 each; red clears the three team boards, the last doubled.
    assert game._points == {"p1": 41 * 3}
    assert game._scores["red"] == 41 * 3 + 80 * 2 + 80 * 2
    assert game._scores["red"] > game._scores.get("blue", 0) == 0
    assert "Luis" in listened and "Ana" in listened
    assert any("gringos" in line for line in reachy.said)
    assert any(line.startswith("¡Gana el equipo Rojo") for line in reachy.said)
    assert len(read_history(tmp_path / "sheeple" / "history.json")) == sheeple.ROUNDS
    assert game.snapshot(room.red)["sheep"][0]["name"] == "Ana"


def test_every_fixed_line_is_offered_to_the_speech_cache() -> None:
    """The fixed lines (no names or numbers in them) are cached in both languages."""
    phrases = Sheeple.phrases()
    assert "¡Tablero limpio!" in phrases and "Board cleared!" in phrases
    assert all("{" not in phrase for phrase in phrases)


def test_a_survey_needs_its_question_in_the_language() -> None:
    """An entry without Spanish text is not a Spanish board."""
    entry = _entry(1)
    entry["question"] = {"en": "Name a fruit"}
    assert survey(entry, "es") is None and isinstance(survey(entry, "en"), Survey)
