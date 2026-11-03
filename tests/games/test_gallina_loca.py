"""Tests for la gallina loca: names heard at the start, turns passed by a pat or "¡listo!", the hen going off."""

import asyncio
from pathlib import Path
from collections.abc import Callable

import pytest

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import hunt, gallina_loca
from reachy_mini_conversation_app.games.host import GameHost
from reachy_mini_conversation_app.games.catalog import again_answer, parse_game_request
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.kids_lines import LINES
from reachy_mini_conversation_app.games.simon_says import POSES
from reachy_mini_conversation_app.games.gallina_loca import FORFEITS, CHALLENGES, GallinaLoca, heard_names
from reachy_mini_conversation_app.games.big_brain_audio import silence, write_wav
from reachy_mini_conversation_app.games.big_brain_lines import shown


@pytest.fixture(autouse=True)
def fast_hen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the round 1.2 s, the turns short, the answers quick, and never "¡Otra vez tú!"."""
    for name, value in {
        "ROUND_S": (1.2, 1.2),
        "HURRY_AT": (0.5, 0.5),
        "TURN_S": 0.4,
        "MIN_TURN_S": 0.05,
        "FORFEIT_S": 0.2,
        "NAME_ANSWER_S": 0.5,
        "AGAIN_YOU": 0.0,
    }.items():
        monkeypatch.setattr(gallina_loca, name, value)
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


def _host(tmp_path: Path, reachy: FakeReachy, known: list[str]) -> GameHost:
    audio = KidsAudio(tmp_path / "kids", LINES)
    return GameHost(reachy.services, {"gallina": lambda: GallinaLoca(audio, known_names=lambda: known)})


# Names ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "names", "done"),
    [
        ("Enzo", ["Enzo"], False),
        ("Endzo", ["Enzo"], False),  # a slip of the speech model
        ("yo me llamo Sofía", ["Sofía"], False),  # known: said with the family's spelling
        ("Enzo y Sofía", ["Enzo", "Sofía"], False),
        ("Me llamo Valentina", ["Valentina"], False),  # not known: taken as heard
        ("mamá", ["Mamá"], False),
        ("Enzo y ya", ["Enzo"], True),
        ("nadie más", [], True),
        ("ya", [], True),
        ("that's all", [], True),
    ],
)
def test_names_are_heard_against_the_family_and_as_said(said: str, names: list[str], done: bool) -> None:
    """Known names match closely (first names only); others are taken as heard; "ya" says there are no more."""
    assert heard_names(said, ["Enzo Yi", "Sofía", "Eduardo"]) == (names, done)


# A whole game ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_whole_game_with_names_pats_and_listo(tmp_path: Path) -> None:
    """Two names, turns passed by pats and "¡listo!", the hen goes off with a forfeit, again with the same names."""
    reachy = FakeReachy(real_clock=True)
    host = _host(tmp_path, reachy, ["Enzo Yi", "Sofía"])
    host.start("gallina", "es")
    await _until(lambda: len(reachy.asked) == 1)
    assert reachy.asked[0][0] in _takes("gallina_who")
    await reachy.asked[0][1]("Enzo")
    await _until(lambda: len(reachy.asked) == 2)
    assert "¡Hola, Enzo!" in reachy.said and reachy.asked[1][0] in _takes("gallina_more")
    await reachy.asked[1][1]("Sofía y ya")
    await _until(lambda: _count(reachy.said, "gallina_ready") == 1)
    assert "¡Hola, Sofía!" in reachy.said

    async def play() -> None:  # a pat or a "¡listo!" in each turn, in turn
        while True:
            await asyncio.sleep(0.12)
            reachy.pats += 1
            await asyncio.sleep(0.12)
            host.heard("¡Listo!")

    playing = asyncio.get_running_loop().create_task(play())
    try:
        await _until(lambda: len(reachy.asked) == 3)
    finally:
        playing.cancel()
    said = reachy.said
    boom = next(index for index, line in enumerate(said) if line in _takes("gallina_boom"))
    calls = [line for line in said[:boom] if line in ("¡Enzo!", "¡Sofía!")]
    assert said[boom + 1] == calls[-1]  # the forfeit goes to whoever's turn it was
    assert len(calls) >= 2 and all(a != b for a, b in zip(calls, calls[1:]))  # never the same child twice running
    assert _count(said, "gallina_good") >= 1
    assert _count(said, "gallina_boom") == 1 and _count(said, "gallina_caught") == 1
    assert any(line in _takes(f"gallina_forfeit_{key}") for key in FORFEITS for line in said)
    assert _count(said, "gallina_forfeit_done") == 1 and _count(said, "gallina_hurry") <= 1
    assert reachy.asked[2][0] in _takes("again")
    await reachy.asked[2][1]("¡sí, otra vez!")
    await _until(lambda: len(reachy.asked) == 4)
    assert sum(text in _takes("gallina_who") or text in _takes("gallina_more") for text, _ in reachy.asked) == 2
    assert _count(reachy.said, "gallina_boom") == 2  # a second round, with the same names
    await reachy.asked[3][1]("no")
    await _until(lambda: _count(reachy.said, "thanks") == 1)
    assert reachy.emotions == ["excited", "excited"] and reachy.screens
    assert reachy.quiet[0] is True  # the head is held still from the start, so pats count while playing
    assert ["Enzo", "Sofía"] in reachy.hints  # the speech model leans toward the family's names
    await _until(lambda: reachy.hints[-1] == [])  # cleared once the game is over


@pytest.mark.asyncio
async def test_without_names_the_hen_says_next_and_turns_time_out(tmp_path: Path) -> None:
    """Nobody says a name: "¡Siguiente!" calls each turn, an undone turn moves on, and anyone gets the forfeit."""
    reachy = FakeReachy(real_clock=True)
    host = _host(tmp_path, reachy, [])
    host.start("gallina", "es")
    await _until(lambda: len(reachy.asked) == 2)  # "¿Quién juega?" unanswered, then "¿Otra vez?"
    said = reachy.said
    assert _count(said, "gallina_no_names") == 1
    assert _count(said, "gallina_next") >= 2 and _count(said, "gallina_slow") >= 1
    assert _count(said, "gallina_caught_anyone") == 1
    host.end("test over", say=False)


@pytest.mark.asyncio
async def test_listo_counts_only_while_a_turn_is_open(tmp_path: Path) -> None:
    """Outside a turn "¡listo!" is not the game's; a turn's first moments ignore the last child's pat."""
    game = GallinaLoca(KidsAudio(tmp_path, LINES))
    reachy = FakeReachy()
    host = GameHost(reachy.services, {"gallina": lambda: game})
    assert not game.heard(host._context, "¡listo!")  # type: ignore[arg-type]
    game._turn_open = True
    assert game.heard(host._context, "¡listo, listo!")  # type: ignore[arg-type]
    assert not game.heard(host._context, "¿cuál es tu color favorito?")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_recorded_question_listens_after_it(tmp_path: Path) -> None:
    """With "¿Quién juega?" recorded, the clip plays and the name may start up to NAME_LISTEN_S after it."""
    for take in range(len(LINES["gallina_who"]["es"])):
        write_wav(tmp_path / "kids" / "voice" / "gallina_who" / f"es-{take + 1:02d}.wav", silence(0.1))
    reachy = FakeReachy(real_clock=True, recorded=True)
    host = _host(tmp_path, reachy, [])
    host.start("gallina", "es")
    await _until(lambda: bool(reachy.clip_listen))
    assert reachy.clip_listen[0] == gallina_loca.NAME_LISTEN_S
    assert reachy.clips[0][0] in _takes("gallina_who")
    host.end("test over", say=False)


# Starting and switching ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["juguemos a la gallina loca", "vamos a jugar la gallina", "let's play crazy hen", "juguemos papa caliente"],
)
def test_the_hen_is_found_by_name(text: str) -> None:
    """Its names, in Spanish and English (and the hot potato it is like)."""
    request = parse_game_request(text)
    assert request is not None and request.game is not None and request.game.key == "gallina"


def test_another_kids_game_can_switch_to_and_from_the_hen() -> None:
    """Answering "¿Otra vez?" in Simón dice can go to the hen, and in the hen to Simón dice."""
    assert again_answer("no, ¡la gallina loca!", "simon") == "gallina"
    assert again_answer("¡Simón dice!", "gallina") == "simon"


def test_every_challenge_and_forfeit_has_a_line_and_a_drawing() -> None:
    """Each challenge, pose and forfeit is recorded in both languages and has something to draw."""
    keys = [f"gallina_do_{k}" for k in CHALLENGES] + [f"gallina_pose_{k}" for k in POSES]
    keys += [f"gallina_forfeit_{k}" for k in FORFEITS]
    for key in keys:
        assert LINES[key]["es"] and LINES[key]["en"], key
    assert all(c.drawing for c in (*CHALLENGES.values(), *FORFEITS.values()))
    assert len(CHALLENGES) >= 20 and len(FORFEITS) >= 5
