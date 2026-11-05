"""Tests for Ballpark: the question bank, the powers, the scoring, the screens and a whole game with pretend phones."""

import io
import json
import random
import asyncio
from typing import Any
from pathlib import Path

import pytest
from PIL import Image

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import ballpark
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.catalog import parse_game_request
from reachy_mini_conversation_app.games.ballpark import (
    BANK,
    Board,
    Entry,
    Ballpark,
    BallparkSetup,
    score,
    resolve,
    question,
    to_whole,
    load_questions,
    pick_questions,
)
from reachy_mini_conversation_app.games.ballpark_lines import LINES
from reachy_mini_conversation_app.games.big_brain_board import read_history
from reachy_mini_conversation_app.games.big_brain_lines import shown
from reachy_mini_conversation_app.games.ballpark_screens import Row, number, render_reveal, render_question
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player


def _entry(index: int, answer: int = 42) -> dict[str, Any]:
    return {
        "id": f"t-{index:02d}",
        "theme": "animals",
        "question": {
            "es": f"¿Cuántos dientes tiene un perro? ({index})",
            "en": f"How many teeth does a dog have? ({index})",
        },  # fmt: skip
        "answer": answer,
        "unit": {"es": "dientes", "en": "teeth"},
        "said": {"es": "cuarenta y dos dientes", "en": "forty-two teeth"},
        "fact": {"es": "Los cachorros tienen 28.", "en": "Puppies have 28."},
        "sources": ["https://example.org"],
    }


# The bank ------------------------------------------------------------------------------------------


def test_a_question_reads_one_language_and_needs_a_whole_number_answer() -> None:
    """Text, unit, answer and the spoken answer come in the game's language; a fraction or a missing text is no question."""
    asked = question(_entry(1), "es")
    assert asked is not None and asked.text.startswith("¿Cuántos") and (asked.unit, asked.answer) == ("dientes", 42)
    assert question({**_entry(2), "answer": 4.5}, "en") is None
    assert question({**_entry(3), "answer": True}, "en") is None
    assert question({**_entry(4), "question": {"en": "Only English"}}, "es") is None


def test_questions_played_lately_come_last() -> None:
    """A question from the last games plays only when the bank has nothing newer."""
    bank = [_entry(index) for index in range(6)]
    recent = {f"t-{index:02d}": 1000.0 + index for index in range(4)}
    picked = [asked.id for asked in pick_questions(bank, "en", recent=recent, count=6, rng=random.Random(1))]
    assert set(picked[:2]) == {"t-04", "t-05"} and picked[2:] == ["t-00", "t-01", "t-02", "t-03"]


def test_the_shipped_bank_is_whole_and_sourced() -> None:
    """Every shipped question has both languages, a whole number answer, a spoken answer, a fact and a source."""
    bank = load_questions(BANK)
    assert len(bank) >= 120
    assert len({entry["id"] for entry in bank}) == len(bank)
    for entry in bank:
        for language in ("es", "en"):
            asked = question(entry, language)
            assert asked is not None and asked.fact and asked.unit, (entry["id"], language)
        assert entry["sources"] and all(str(url).startswith("http") for url in entry["sources"]), entry["id"]


# Phones' numbers -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "whole"),
    [
        (42, 42),
        ("1.500", 1500),
        ("1,500", 1500),
        ("2 000", 2000),
        (7.0, 7),
        (-1, None),
        ("12a", None),
        (True, None),
        (None, None),
        (10**14, None),
    ],  # fmt: skip
)
def test_a_range_end_is_a_whole_number(sent: object, whole: int | None) -> None:
    """Phones send digits; separators are allowed, and anything negative, huge or not a number is refused."""
    assert to_whole(sent) == whole


# Powers -------------------------------------------------------------------------------------------


def test_a_copy_takes_the_range_at_the_end_of_the_chain() -> None:
    """Blue copies green, who copies red: both take red's range."""
    boards, played = resolve(
        {"red": Entry(10, 20), "green": Entry(copy="red"), "blue": Entry(copy="green")}, ["red", "blue", "green"]
    )
    assert (boards["blue"].low, boards["blue"].high) == (10, 20) == (boards["green"].low, boards["green"].high)
    assert boards["blue"].copied == "green" and [p.power for p in played] == ["copy", "copy"]


def test_teams_that_copy_each_other_have_no_range() -> None:
    """Red and blue copy each other: neither has a range, and the loop is announced once."""
    boards, played = resolve({"red": Entry(copy="blue"), "blue": Entry(copy="red")}, ["red", "blue"])
    assert boards["red"].size is None and boards["blue"].size is None
    assert len(played) == 1 and played[0].loop


def test_copying_a_team_that_sent_nothing_leaves_no_range() -> None:
    """A copy of a silent team is announced as empty."""
    boards, played = resolve({"red": Entry(copy="blue")}, ["red", "blue"])
    assert boards["red"].size is None and played[0].empty


def test_a_swap_trades_two_other_teams_ranges_after_the_copies() -> None:
    """Red swaps blue and green; blue had copied red, so green ends with red's range."""
    boards, played = resolve(
        {"red": Entry(1, 2, swap=("blue", "green")), "blue": Entry(copy="red"), "green": Entry(50, 90)},
        ["red", "blue", "green"],
    )
    assert (boards["blue"].low, boards["green"].low) == (50, 1)
    assert boards["blue"].swapped == "green" and [p.power for p in played] == ["copy", "swap"]


# Scoring ------------------------------------------------------------------------------------------


def _boards(*ranges: tuple[int, int] | None, double: str = "") -> dict[str, Board]:
    teams = ("red", "blue", "green", "yellow")
    return {team: Board(team, *(span or (None, None)), double=team == double) for team, span in zip(teams, ranges)}


def test_the_smallest_right_range_scores_three_and_other_right_ones_one() -> None:
    """60–80 and 0–500 hold 70; 10–15 does not."""
    boards = _boards((10, 15), (60, 80), (0, 500))
    score(boards, 70)
    assert [b.points for b in boards.values()] == [0, 3, 1] and not boards["red"].right


def test_when_everyone_is_right_the_widest_scores_nothing() -> None:
    """No "zero to a million": with all right, the widest range is zeroed."""
    boards = _boards((60, 80), (0, 1_000_000))
    score(boards, 70)
    assert [b.points for b in boards.values()] == [3, 0] and boards["blue"].zeroed


def test_equal_smallest_ranges_all_score_three() -> None:
    """Two teams with the same tightest size both score 3, even when everyone is right."""
    boards = _boards((60, 80), (65, 85))
    score(boards, 70)
    assert [b.points for b in boards.values()] == [3, 3]


def test_nail_it_doubles_and_the_surprise_double_multiply() -> None:
    """An exact answer on a nail-it question is 5; a team's double and the everybody-double both count."""
    boards = _boards((70, 70), (60, 80), (0, 1000))
    score(boards, 70, nail=True)
    assert [b.points for b in boards.values()] == [5, 1, 0]
    boards = _boards((60, 80), (0, 1000), (200, 300), double="red")
    score(boards, 70, double_all=True)
    assert [b.points for b in boards.values()] == [12, 2, 0]


# A phone's range ----------------------------------------------------------------------------------


def _game_with_teams(tmp_path: Path, teams: list[str]) -> tuple[Ballpark, list[Player]]:
    game = Ballpark(BallparkSetup(data_dir=tmp_path, port=0))
    game._phones = Phones(0, game.snapshot, game.on_message, lambda: True)
    players = [Player(f"p{i}", f"P{i}", team, float(i)) for i, team in enumerate(teams)]
    for player in players:
        game._phones.players[player.id] = player
    return game, players


@pytest.mark.asyncio
async def test_a_range_needs_both_numbers_and_powers_are_once_a_game(tmp_path: Path) -> None:
    """Ends come sorted; a copy of yourself, a swap naming yourself or a spent power is refused."""
    game, (red, _, _) = _game_with_teams(tmp_path, ["red", "blue", "green"])
    entry = game._entry(red, {"low": "80", "high": 60, "double": True})
    assert entry is not None and (entry.low, entry.high, entry.double) == (60, 80, True)
    assert game._entry(red, {"low": 1}) is None
    assert game._entry(red, {"copy": "red"}) is None
    assert game._entry(red, {"low": 1, "high": 2, "swap": ["red", "blue"]}) is None
    swap = game._entry(red, {"low": 1, "high": 2, "swap": ["blue", "green"]})
    assert swap is not None and swap.swap == ("blue", "green")
    game._used["red"] = {"copy", "double"}
    assert game._entry(red, {"copy": "blue"}) is None
    spent = game._entry(red, {"low": 1, "high": 2, "double": True})
    assert spent is not None and not spent.double


@pytest.mark.asyncio
async def test_swap_needs_three_teams(tmp_path: Path) -> None:
    """With two teams, Swap is not offered and copy is."""
    game, (red, _) = _game_with_teams(tmp_path, ["red", "blue"])
    assert game._left("red") == ["copy", "double"]
    assert game._entry(red, {"low": 1, "high": 2, "swap": ["blue", "green"]}) is None


# Screens and starting -----------------------------------------------------------------------------


def test_the_screens_draw_a_question_and_the_ranges() -> None:
    """The question and the number line (log scale for far-apart ranges) are 800×480 PNGs."""
    png = render_question(
        "¿Cuántos dientes tiene un perro adulto?", "dientes", "Pregunta 4", [("Rojo", True)], [("Rojo", 9)], "es", surprise="nail"
    )  # fmt: skip
    assert Image.open(io.BytesIO(png)).size == (800, 480)
    rows = [Row("Rojo", 30, 50, "×2", 6, True), Row("Azul", 0, 10**9, "", 0, True), Row("Verde", None, None, "copia a Azul", None, None)]  # fmt: skip
    for answer in (None, 42):
        png = render_reveal("¿Cuántos dientes?", "dientes", rows, answer, [("Rojo", 15)], "Pregunta 4", "es")
        assert Image.open(io.BytesIO(png)).size == (800, 480)
    assert (number(1500000, "es"), number(1500000, "en")) == ("1.500.000", "1,500,000")


@pytest.mark.parametrize(
    ("said", "language"),
    [
        ("juguemos ballpark", "es"),
        ("let's play ballpark", "en"),
        ("juguemos al más o menos", "es"),
        ("juguemos bolpark", "es"),
    ],  # fmt: skip
)
def test_ballpark_starts_by_its_name_and_its_mishearings(said: str, language: str) -> None:
    """The name, how speech recognition writes it, and "más o menos" start Ballpark."""
    request = parse_game_request(said)
    assert request is not None and request.game is not None
    assert (request.game.key, request.language) == ("ballpark", language)


# A whole game -------------------------------------------------------------------------------------


class _Room:
    """Three pretend teams on a 42 question: red sends 40–45, blue 0–1000, green copies red once, then 41–43."""

    def __init__(self, game: Ballpark) -> None:
        self.game = game
        self.players = [Player("p1", "Ana", "red", 0.0), Player("p2", "Luis", "blue", 1.0), Player("p3", "Eva", "green", 2.0)]  # fmt: skip

    def join(self) -> None:
        phones = self.game._phones
        assert phones is not None
        for player in self.players:
            phones.players[player.id] = player
            self.game.on_message(player, {"type": "joined"})

    async def play(self) -> None:
        game = self.game
        while True:
            await asyncio.sleep(0.02)
            for player in self.players:
                snapshot = game.snapshot(player)
                if "start" in snapshot["can"] and player.team == "red":
                    game.on_message(player, {"type": "start"})
                elif "range" in snapshot["can"] and snapshot["draft"] is None:
                    if player.team == "red":
                        message: dict[str, Any] = {"low": 40, "high": 45}
                    elif player.team == "blue":
                        message = {"low": 0, "high": 1000}
                    elif snapshot["round"]["number"] == 1:
                        message = {"copy": "red"}
                    else:
                        message = {"low": 41, "high": 43}
                    game.on_message(player, {"type": "range", **message})


def _said(lines: list[str], moment: str) -> int:
    takes = {shown(take) for take in LINES[moment]["es"]}
    return sum(line in takes for line in lines)


@pytest.mark.asyncio
async def test_a_whole_game_from_joining_to_the_first_team_at_fifteen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Green copies red on question 1 (both tightest: 3 each), then is tightest alone and wins at 15; blue is widest.

    Copy is spent once; blue's 0–1000 is zeroed every question as everyone is right; the surprise plays once.
    """
    for name, value in {
        "ANSWER_S": 2.0,
        "HURRY_S": 0.5,
        "AFTER_S": 0.0,
        "LOBBY_REMINDER_S": 5.0,
        "SURPRISE_FIRST": 2,
        "SURPRISE_LAST": 2,
    }.items():
        monkeypatch.setattr(ballpark, name, value)

    async def nothing(self: Phones) -> None:
        return None

    monkeypatch.setattr(Phones, "start", nothing)
    monkeypatch.setattr(Phones, "stop", nothing)
    bank = tmp_path / "bank.jsonl"
    bank.write_text("\n".join(json.dumps(_entry(index)) for index in range(12)))
    reachy = FakeReachy(real_clock=True)
    games: list[Ballpark] = []

    def make() -> Ballpark:
        games.append(Ballpark(BallparkSetup(data_dir=tmp_path, port=0, bank=bank), rng=random.Random(5)))
        return games[0]

    host = GameHost(reachy.services, {"ballpark": make})
    host.start("ballpark", "es")
    await asyncio.sleep(0.1)
    room = _Room(games[0])
    room.join()
    playing = asyncio.get_running_loop().create_task(room.play())
    try:
        for _ in range(800):
            if host.state is GameState.IDLE:
                break
            await asyncio.sleep(0.05)
    finally:
        playing.cancel()
    game = games[0]
    assert host.state is GameState.IDLE and game._phase == "over"
    assert game._winners == ["green"] and game._scores["green"] >= ballpark.TARGET
    assert game._scores["blue"] == 0 and game._used["green"] == {"copy"}
    said = reachy.said
    assert "¡Verde copió a Rojo!" in said
    assert _said(said, "surprise") == 1 and _said(said, "powers_used") == 1
    assert _said(said, "everyone_right") == game._number and _said(said, "best_green") == game._number - 1
    assert any(line.startswith("¡Cuarenta y dos dientes!") for line in said)
    assert _said(said, "winner") == _said(said, "winner_green") == 1
    assert len(read_history(tmp_path / "ballpark" / "history.json")) == game._number


def test_every_moment_ballpark_plays_has_takes_in_both_languages() -> None:
    """Each moment the game names (and each team's best and win) has two or more tagged takes in es and en."""
    source = Path(ballpark.__file__).read_text(encoding="utf-8")
    named = {moment for moment in LINES if f'"{moment}"' in source}
    named |= {f"surprise_{kind}" for kind in ballpark.SURPRISES}
    named |= {f"{kind}_{team}" for kind in ("best", "winner") for team in ("red", "blue", "green", "yellow")}
    assert len(named) > 40
    assert named <= set(LINES)
    for moment in named:
        for language in ("es", "en"):
            takes = LINES[moment][language]
            assert len(takes) >= 2 and all(take.startswith("[") and shown(take) for take in takes), moment
