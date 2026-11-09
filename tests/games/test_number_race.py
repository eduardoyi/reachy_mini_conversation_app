"""Tests for the number race: spins by a pat or "¡gira!", counts cheered or done together, Flecha's turns, the end."""

import random
import asyncio
from pathlib import Path
from collections.abc import Callable, Sequence

import pytest

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import hunt, number_race
from reachy_mini_conversation_app.games.host import GameHost
from reachy_mini_conversation_app.games.catalog import again_answer, parse_game_request
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.kids_lines import LINES
from reachy_mini_conversation_app.games.number_race import NUMBER_WORDS, NumberRace, heard_numbers
from reachy_mini_conversation_app.games.big_brain_lines import shown
from reachy_mini_conversation_app.games.number_race_screens import render_number_race


class _Spinner(random.Random):
    """A wheel that always lands on ``value``."""

    def __init__(self, value: int) -> None:
        super().__init__()
        self.value = value

    def choice(self, seq: Sequence[int]) -> int:  # type: ignore[override]
        return self.value


@pytest.fixture(autouse=True)
def fast_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the waits for a spin and a count short."""
    for name, value in {
        "SPIN_NUDGE_S": 0.3,
        "SPIN_WAIT_S": 0.6,
        "SPIN_SETTLE_S": 0.05,
        "COUNT_ANSWER_S": 0.4,
    }.items():
        monkeypatch.setattr(number_race, name, value)
    monkeypatch.setattr(hunt, "OTHER_ROUND_ANSWER_S", 0.5)


def _takes(moment: str, language: str = "es") -> set[str]:
    return {shown(take) for take in LINES[moment][language]}


def _count(said: list[str], moment: str) -> int:
    takes = _takes(moment)
    return sum(line in takes for line in said)


async def _until(predicate: Callable[[], bool], timeout_s: float = 6.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition did not become true"
        await asyncio.sleep(0.01)


def _host(tmp_path: Path, reachy: FakeReachy, spins: int) -> GameHost:
    audio = KidsAudio(tmp_path / "kids", LINES)
    return GameHost(reachy.services, {"numbers": lambda: NumberRace(audio, rng=_Spinner(spins))})


@pytest.mark.parametrize(
    ("said", "numbers"),
    [
        ("cinco, seis", {5, 6}),
        ("¡Seis!", {6}),
        ("five six", {5, 6}),
        ("5, 6", {5, 6}),
        ("uno dos tres", {1, 2, 3}),
        ("diez", {10}),
        ("once", set()),  # not on the track
        ("¡vamos Tito!", set()),
        ("", set()),
    ],
)
def test_numbers_are_heard_in_spanish_english_and_digits(said: str, numbers: set[int]) -> None:
    """Number words in either language and digits count, up to 10."""
    assert heard_numbers(said) == numbers


@pytest.mark.asyncio
async def test_a_whole_race_with_pats_and_right_counts(tmp_path: Path) -> None:
    """Twos all the way: five pats and right counts take Tito to 10 first; Flecha counts his own; then thanks."""
    reachy = FakeReachy(real_clock=True)
    host = _host(tmp_path, reachy, spins=2)
    host.start("numbers", "es")

    async def pat() -> None:
        while True:
            await asyncio.sleep(0.12)
            reachy.pats += 1

    patting = asyncio.get_running_loop().create_task(pat())
    try:
        for turn in range(5):
            await _until(lambda: len(reachy.asked) == turn + 1)
            assert reachy.asked[turn][0] in _takes("race_count_2")
            words = NUMBER_WORDS["es"]
            await reachy.asked[turn][1](f"{words[2 * turn]}, {words[2 * turn + 1]}")  # counting on from Tito
        await _until(lambda: len(reachy.asked) == 6)
    finally:
        patting.cancel()
    said = reachy.said
    assert _count(said, "race_start") == 1 and _count(said, "race_turn_first") == 1
    assert _count(said, "race_right") == 5 and _count(said, "race_together") == 0
    cheers = [said[index + 1] for index, line in enumerate(said) if line in _takes("race_right")]
    assert cheers == [shown(LINES[f"num_{n}"]["es"][0]) for n in (2, 4, 6, 8, 10)]  # the number reached, again
    assert _count(said, "race_flecha_2") == 4  # Flecha reaches 8 and Tito wins
    assert _count(said, "race_tito_wins") == 1 and _count(said, "race_flecha_wins") == 0
    assert reachy.asked[5][0] in _takes("again")
    await reachy.asked[5][1]("no")
    await _until(lambda: _count(reachy.said, "thanks") == 1)
    assert reachy.quiet[0] is True  # the head is held still, so pats spin the wheel
    assert list(NUMBER_WORDS["es"]) in reachy.hints  # the speech model leans toward the number words
    assert len(reachy.screens) >= 10


@pytest.mark.asyncio
async def test_nobody_spins_or_counts_so_richie_does(tmp_path: Path) -> None:
    """With no pat Richie reminds once, then spins; with no count he counts the squares with them."""
    reachy = FakeReachy(real_clock=True)
    host = _host(tmp_path, reachy, spins=2)
    host.start("numbers", "es")
    await _until(lambda: _count(reachy.said, "race_together") == 1)
    said = reachy.said
    assert _count(said, "race_spin_nudge") == 1
    together = next(index for index, line in enumerate(said) if line in _takes("race_together"))
    assert said[together + 1 : together + 3] == [shown(LINES[f"num_{n}"]["es"][0]) for n in (1, 2)]
    host.end("test over", say=False)


@pytest.mark.asyncio
async def test_a_wrong_count_is_counted_together(tmp_path: Path) -> None:
    """A count that misses the number Tito reaches gets "¡Contemos juntos!", never a correction."""
    reachy = FakeReachy(real_clock=True)
    host = _host(tmp_path, reachy, spins=1)
    host.start("numbers", "es")
    await _until(lambda: len(reachy.asked) == 1)
    assert reachy.asked[0][0] in _takes("race_count_1")
    await reachy.asked[0][1]("tres")
    await _until(lambda: _count(reachy.said, "race_together") == 1)
    assert _count(reachy.said, "race_right") == 0
    host.end("test over", say=False)


@pytest.mark.asyncio
async def test_gira_spins_without_a_pat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A "¡gira!" heard without the name spins the wheel; outside the wait it is not the game's."""
    monkeypatch.setattr(number_race, "SPIN_NUDGE_S", 5.0)
    monkeypatch.setattr(number_race, "SPIN_WAIT_S", 5.0)
    reachy = FakeReachy(real_clock=True)
    host = _host(tmp_path, reachy, spins=2)
    host.start("numbers", "es")
    await _until(lambda: _count(reachy.said, "race_turn_first") == 1)
    await asyncio.sleep(0.2)
    assert host.heard("¡Gira!")
    await _until(lambda: len(reachy.asked) == 1, timeout_s=2.0)
    assert not host.heard("¡Gira!")  # counting now, not spinning
    host.end("test over", say=False)


def test_the_race_is_asked_for_by_name_and_is_a_kids_game() -> None:
    """Its names start it; "la gran carrera" stays the story; "¿Otra vez?" can switch to and from it."""
    for text in ("juguemos a la carrera de números", "vamos a jugar a los números", "let's play the number race"):
        request = parse_game_request(text)
        assert request is not None and request.game is not None and request.game.key == "numbers"
    story = parse_game_request("cuéntanos la gran carrera")
    assert story is not None and story.game is not None and story.game.key == "la_gran_carrera"
    assert again_answer("no, ¡la carrera de números!", "colors") == "numbers"
    assert again_answer("¡la gallina loca!", "numbers") == "gallina"


def test_the_screen_is_a_two_bit_png() -> None:
    """The board renders for the TRMNL at every position, the finish included."""
    for tito, flecha in ((0, 0), (4, 3), (10, 9)):
        png = render_number_race(tito, flecha, "La carrera de números", ("Tito", range(tito + 1, tito + 3)))
        assert png.startswith(b"\x89PNG") and len(png) < 90_000


def test_every_count_has_a_line_in_both_languages() -> None:
    """Each number, both spins and Flecha's spins are written in Spanish and English."""
    moments = [f"num_{n}" for n in range(1, 11)] + [f"race_count_{n}" for n in (1, 2)]
    moments += [f"race_flecha_{n}" for n in (1, 2)]
    for moment in moments:
        assert LINES[moment]["es"] and LINES[moment]["en"]


def test_the_cars_are_the_storys_and_fit_over_their_roads() -> None:
    """Tito and Flecha are the story's pictures (scripts/race_cars.py), transparent around them, under the frame."""
    from reachy_mini_conversation_app.games import number_race_screens as screens

    for car, above in ((screens.TITO, 66), (screens.FLECHA, 262)):  # the caption's tab, the line between roads
        picture = screens._car(car)
        ground, _ = screens._LANES[car]
        assert picture.mode == "LA" and picture.getchannel("A").getextrema() == (0, 255)
        assert ground - picture.height > above
        assert picture.width < 2 * screens._SQUARE + 20
