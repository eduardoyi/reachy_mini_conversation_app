"""Tests for Big Brain: the board, the judge, the QR code, the phones' server and a whole show with pretend phones."""

import json
import random
import asyncio
from typing import Any
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from .fakes import FakeReachy
from reachy_mini_conversation_app import jev
from reachy_mini_conversation_app.games import big_brain
from reachy_mini_conversation_app.qr_code import qr_matrix
from reachy_mini_conversation_app.games.base import UNCLEAR_ANSWER
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.catalog import parse_game_request
from reachy_mini_conversation_app.games.big_brain import EDITIONS, BigBrain, BigBrainSetup
from reachy_mini_conversation_app.games.big_brain_board import (
    POINTS,
    Clue,
    BoardError,
    imperial,
    remember,
    build_board,
    read_history,
)
from reachy_mini_conversation_app.games.big_brain_judge import Judge, Ruling, plain, matches
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.big_brain_screens import render_join


def _entry(index: int, *, category: str, column: str, hardness: float, kids: bool = False, adult: bool = False) -> dict[str, Any]:  # fmt: skip
    return {
        "id": str(index),
        "category": category,
        "column": {"en": column, "es": f"{column} (es)"},
        "clue": {"en": f"This clue {index}.", "es": f"Esta pista {index}."},
        "answer": {"en": f"Answer {index}", "es": f"Respuesta {index}"},
        "accept": [f"Answer {index}", f"Respuesta {index}"],
        "kids": {"en": f"Kids clue {index}.", "es": f"Pista de niños {index}."} if kids else None,
        "adult": adult,
        "appeal": 3.0,
        "hardness": hardness,
    }


def _bank(size: int = 400) -> list[dict[str, Any]]:
    """Clues in eight themed columns over four categories, hardness spread evenly from 0 to 4."""
    rng = random.Random(7)
    categories = ["history", "science", "screen", "music"]
    return [
        _entry(
            index,
            category=categories[index % 4],
            column=f"Topic {index % 8}",
            hardness=rng.uniform(0, 4),
            kids=index % 3 == 0,
            adult=index % 10 == 0,
        )
        for index in range(size)
    ]


# The board -------------------------------------------------------------------------------------------


def test_a_board_has_five_columns_of_four_clues_from_easy_to_hard() -> None:
    """Each column runs 200 to 800 points with harder clues lower down; three hidden squares, a final apart."""
    board = build_board(_bank(), "es", rng=random.Random(1), now=0)
    assert len(board.titles) == 5 and all(len(column) == 4 for column in board.clues)
    for column in board.clues:
        assert [clue.points for clue in column] == list(POINTS)
        assert [clue.hardness for clue in column] == sorted(clue.hardness for clue in column)
        assert all(clue.text.startswith("Esta pista") for clue in column)
    hidden = [
        (c, r, clue.hidden) for c, column in enumerate(board.clues) for r, clue in enumerate(column) if clue.hidden
    ]
    assert sorted(kind for _, _, kind in hidden) == ["double", "everyone", "steal"]
    assert all(row > 0 for _, row, _ in hidden) and len({column for column, _, _ in hidden}) == 3
    assert board.final.id not in {clue.id for column in board.clues for clue in column}
    assert len(set(board.ids)) == 21


def test_a_column_missing_a_row_borrows_from_the_next_row_and_still_goes_easy_to_hard() -> None:
    """A themed column with no clue in the hardest quarter still plays, its 800 taken from the row above."""
    bank = _bank()
    for entry in bank:
        if entry["column"]["en"] == "Topic 0" and entry["hardness"] >= 3.0:
            entry["hardness"] = 2.0 + entry["hardness"] / 10  # the hardest quarter starts near 3
    titles = set()
    for seed in range(30):
        board = build_board(bank, "en", rng=random.Random(seed), now=0)
        titles |= set(board.titles)
        for column in board.clues:
            assert [clue.hardness for clue in column] == sorted(clue.hardness for clue in column)
    assert "Topic 0" in titles


def test_kids_mode_uses_kids_clues_and_never_adult_ones() -> None:
    """Kids mode reads the kids versions and leaves adult-tagged clues out."""
    bank = _bank()
    board = build_board(bank, "en", kids=True, rng=random.Random(2), now=0)
    by_id = {entry["id"]: entry for entry in bank}
    for clue in [clue for column in board.clues for clue in column] + [board.final]:
        assert clue.text.startswith("Kids clue") and not by_id[clue.id]["adult"]


def test_clues_used_this_month_are_left_out(tmp_path: Path) -> None:
    """A clue played in the last 30 days does not come back; older ones may."""
    bank = _bank(60)
    history = tmp_path / "history.json"
    remember(history, [entry["id"] for entry in bank[:50]], now=1_000_000)
    with pytest.raises(BoardError):
        build_board(bank, "es", recent=read_history(history), now=1_000_000 + 86400)
    assert len(read_history(history)) == 50


def test_imperial_measurements_and_repeated_answers_stay_off_the_board() -> None:
    """Miles and Fahrenheit clues are left out (names like Fahrenheit 451 stay); no answer is on a board twice."""
    bank = _bank()
    for entry in bank[:120]:
        entry["clue"]["en"] = "This peak rises 29,032 feet."
    for index, entry in enumerate(bank[120:200]):
        entry["accept"] = ["Dementor" if index % 2 else "Dementors"]
    bank[200]["clue"]["en"] = "Ray Bradbury wrote Fahrenheit 451."
    board = build_board(bank, "en", rng=random.Random(4), now=0)
    picked = {clue.id for column in board.clues for clue in column} | {board.final.id}
    assert not picked & {str(index) for index in range(120)}
    assert len(picked & {str(index) for index in range(120, 200)}) <= 1
    assert imperial({"clue": {"es": "Mide doce pies de alto."}}) and imperial({"answer": {"en": "1,000 mph"}})
    assert not imperial({"clue": {"en": "Ray Bradbury wrote Fahrenheit 451.", "es": "Murieron miles de personas."}})


# The judge -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "right"),
    [
        ("Greta Garbo", True),
        ("garbo", True),
        ("Creo que es Greta Garbo", True),
        ("greta garbó", True),
        ("Greta Garvo", True),
        ("Marlene Dietrich", False),
        ("Greta", False),
        ("Garbo Garbo Garbo", False),
        ("", False),
    ],
)
def test_code_accepts_forms_slips_and_surnames(said: str, right: bool) -> None:
    """Accents, articles, lead-ins, a surname alone and a small slip count without asking a model."""
    assert matches(said, ["Greta Garbo"]) is right


def test_a_name_counts_without_its_generic_word_but_the_word_alone_does_not() -> None:
    """Pacific counts for the Pacific Ocean, el Amazonas for the río Amazonas; Ocean alone is not an answer."""
    assert matches("Pacific", ["Pacific Ocean"]) and matches("el Amazonas", ["Río Amazonas"])
    assert not matches("Ocean", ["Pacific Ocean"]) and not matches("Atlantic", ["Pacific Ocean"])


@pytest.mark.asyncio
async def test_a_hesitation_is_no_answer_and_never_reaches_jev(tmp_path: Path) -> None:
    """A filler alone is wrong without asking Jev ("Um." once sounded like "Om" to it)."""

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        raise AssertionError("Jev asked")

    assert await Judge(tmp_path / "rulings.json", jev).rule("1", "A sacred syllable.", ["Om"], "Um.") is Ruling.WRONG


def test_answers_count_in_either_language() -> None:
    """Saying "el Amazonas" or "the Amazon" both match the clue's accepted forms."""
    accept = ["Amazon River", "Río Amazonas", "Amazonas", "The Amazon"]
    assert matches("el Amazonas", accept) and matches("the amazon", accept)
    assert plain("¿Es el río Amazonas?") == "rio amazonas"


@pytest.mark.asyncio
async def test_jev_rules_on_the_rest_and_its_ruling_is_kept(tmp_path: Path) -> None:
    """A close-but-different answer goes to Jev once; the same answer later gets the kept ruling."""
    calls: list[dict[str, Any]] = []

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        calls.append(state)
        return {"right": {"noul": 0.1}, "too_broad": {"noul": 0.9}, "sounds_like": {"noul": 0.1}}

    judge = Judge(tmp_path / "rulings.json", jev)
    assert await judge.rule("7", "This ship sank in 1912.", ["the Titanic"], "un barco") is Ruling.SPECIFIC
    again = Judge(tmp_path / "rulings.json", jev)
    assert await again.rule("7", "This ship sank in 1912.", ["the Titanic"], "Un barco") is Ruling.SPECIFIC
    assert len(calls) == 1
    again.overrule("7", "un barco")
    assert await Judge(tmp_path / "rulings.json", jev).rule("7", "", ["the Titanic"], "un barco") is Ruling.RIGHT


@pytest.mark.asyncio
async def test_a_spoken_answer_that_sounds_right_counts_but_a_typed_one_must_be_right(tmp_path: Path) -> None:
    """Speech recognition's "Téphanis" for "Tiffany's" counts aloud; typed, only Jev's "right" counts."""
    asked: list[set[str]] = []

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        asked.append(set(questions))
        answers = {"right": {"noul": 0.2}, "too_broad": {"noul": 0.1}, "sounds_like": {"noul": 0.8}}
        return {key: answers[key] for key in questions}

    judge = Judge(tmp_path / "rulings.json", jev)
    accept = ["Tiffany & Co.", "Tiffany's"]
    assert await judge.rule("9", "A jeweler's blue box.", accept, "Téphanis.") is Ruling.RIGHT
    assert await judge.rule("9", "A jeweler's blue box.", accept, "Téphanis", spoken=False) is Ruling.WRONG
    assert asked == [{"right", "too_broad", "sounds_like"}, {"right", "too_broad"}]


@pytest.mark.asyncio
async def test_jev_requests_without_images_send_no_assets() -> None:
    """Jev refuses an empty "assets" object, so a question about text alone leaves it out."""
    bodies: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.9}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        answers = await jev.ask(
            client, api_key="k", model="jev-latest", state={"t": "hi"}, questions={"q": {}}, images={}, feature="x"
        )
    assert answers["q"]["noul"] == 0.9 and "assets" not in bodies[0]


# The QR code ----------------------------------------------------------------------------------------


def test_the_qr_code_has_its_finders_and_timing() -> None:
    """A join link fits a small code with the three finder squares and the timing lines in place."""
    modules = qr_matrix("http://192.168.1.180:7880/?c=ABCD")
    size = len(modules)
    assert size in (21, 25, 29, 33, 37) and all(len(row) == size for row in modules)
    for left, top in ((0, 0), (size - 7, 0), (0, size - 7)):
        assert all(modules[top][left + i] and modules[top + 6][left + i] for i in range(7))
        assert modules[top + 3][left + 3] and not modules[top + 1][left + 1]
    assert [modules[6][x] for x in range(8, size - 8)] == [x % 2 == 0 for x in range(8, size - 8)]


# The phones' server ---------------------------------------------------------------------------------


def test_phones_need_the_code_and_join_with_a_name_and_team() -> None:
    """The page and socket refuse a wrong code; a join gets an id, and later messages reach the game."""
    received: list[tuple[str, dict[str, Any]]] = []
    phones = Phones(
        7999,
        lambda player: {"phase": "lobby", "you": player.name if player else None},
        lambda player, message: received.append((player.name, message)),
        lambda: True,
        code="ABCD",
        clock=lambda: 0.0,
    )
    phones.changed = lambda: None  # type: ignore[method-assign]
    client = TestClient(phones.app)
    assert client.get("/?c=WXYZ").status_code == 404
    with client.websocket_connect("/ws?code=abcd") as socket:
        assert socket.receive_json()["type"] == "state"
        socket.send_json({"type": "join", "name": "  Ana  María ", "team": "blue"})
        welcome = socket.receive_json()
        assert welcome["type"] == "welcome"
        socket.send_json({"type": "buzz"})
        socket.send_json({"type": "join", "player": welcome["player"]})
        assert socket.receive_json() == welcome
    assert [player.name for player in phones.players.values()] == ["Ana María"]
    assert received == [("Ana María", {"type": "joined"}), ("Ana María", {"type": "buzz"})]


def test_the_qr_code_links_to_the_public_address_when_there_is_one() -> None:
    """With a Funnel address, phones skip the Wi-Fi (and Android's permission); codes are six letters."""

    def phones(public_url: str | None) -> Phones:
        return Phones(7880, lambda p: {}, lambda p, m: None, lambda: True, clock=lambda: 0.0, public_url=public_url)

    public = phones("https://mini.example.ts.net/bigbrain/")
    assert public.url() == f"https://mini.example.ts.net/bigbrain/?c={public.code}" and len(public.code) == 6
    assert phones(None).url("192.168.1.180").startswith("http://192.168.1.180:7880/?c=")


# Editions -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "key"),
    [
        ("Let's play Big Brain, Hogwarts.", "bigbrain_hp"),
        ("let's play big brain Harry Potter", "bigbrain_hp"),
        ("Juguemos Big Brain, edición Harry Potter", "bigbrain_hp"),
        ("juguemos la trivia de Harry Potter", "bigbrain_hp"),
        ("let's play Harry Potter trivia", "bigbrain_hp"),
        ("Let's play Big Brain, Lord of the Rings", "bigbrain_lotr"),
        ("Juguemos Big Brain, El Señor de los Anillos", "bigbrain_lotr"),
        ("juguemos la trivia de la Tierra Media", "bigbrain_lotr"),
        ("let's play big brain LOTR", "bigbrain_lotr"),
        ("Let's play Big Brain, Star Wars", "bigbrain_sw"),
        ("Juguemos Big Brain, La guerra de las galaxias", "bigbrain_sw"),
        ("juguemos la trivia de Star Wars", "bigbrain_sw"),
        ("Let's play Big Brain, Disney", "bigbrain_disney"),
        ("Juguemos Big Brain, edición Disney", "bigbrain_disney"),
        ("juguemos la trivia de Pixar", "bigbrain_disney"),
        ("let's play big brain", "bigbrain"),
        ("juguemos trivia", "bigbrain"),
    ],
)
def test_an_edition_starts_by_its_subject_and_plain_big_brain_stays_the_general_quiz(said: str, key: str) -> None:
    """Asking for Big Brain with Hogwarts or Harry Potter starts the edition; Big Brain alone does not."""
    request = parse_game_request(said)
    assert request is not None and request.game is not None and request.game.key == key


def test_an_edition_plays_its_own_bank_title_and_columns(tmp_path: Path) -> None:
    """The Hogwarts edition reads its own bank, names itself, and titles columns from its clues."""
    edition = EDITIONS["bigbrain_hp"]
    setup = BigBrainSetup(data_dir=tmp_path, elevenlabs_key=None, port=0, edition=edition)
    assert setup.bank == tmp_path / "trivia-bank" / "editions" / "harry-potter" / "bank.jsonl"
    game = BigBrain(setup)
    assert game.info.key == "bigbrain_hp" and game.snapshot(None)["title"] == "Big Brain · Edición Hogwarts"
    assert game._own("welcome") == "welcome"  # no clip recorded: the general one
    bank = _bank()
    for entry in bank:  # an edition's categories are its columns
        index = int(entry["id"]) % 6
        entry["category"] = f"key{index}"
        entry["column"] = {"en": f"Column {index}", "es": f"Columna {index}"}
    board = build_board(bank, "es", rng=random.Random(5), now=0)
    assert all(title.startswith("Columna ") for title in board.titles)
    assert render_join("https://x.ts.net/bigbrain/?c=ABCDEF", "ABCDEF", [("Rojo", ["Ana"])], "es", edition="Edición Hogwarts")  # fmt: skip


# A whole show ----------------------------------------------------------------------------------------


class _Room:
    """Two pretend phones: red buzzes and types the answer, blue never buzzes but counts a ruling; both bet."""

    def __init__(self, game: BigBrain) -> None:
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
                can = game.snapshot(player)["can"]
                board = game._board
                if "vote" in can and player is self.blue:
                    game.on_message(player, {"type": "vote"})
                elif "start" in can and player is self.red:
                    game.on_message(player, {"type": "start"})
                elif "pick" in can and board is not None:
                    column, row = next((c, r) for c, col in enumerate(board.clues) for r, clue in enumerate(col) if not clue.used)  # fmt: skip
                    game.on_message(player, {"type": "pick", "column": column, "row": row})
                elif "buzz" in can and player is self.red:
                    game.on_message(player, {"type": "buzz"})
                elif "answer" in can and game._clue is not None:
                    text = game._clue.accept[0] if player is self.red else "no sé"
                    game.on_message(player, {"type": "answer", "text": text})
                elif "bet" in can:
                    game.on_message(player, {"type": "bet", "points": 100})


@pytest.mark.asyncio
async def test_a_whole_show_from_joining_to_the_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two teams play the board and the final; red wins; clues are kept.

    A phone plays on after a pause; red's first answer cannot be read, so Richie asks again; the second is ruled
    wrong and blue's vote ends that square at once; the bets end as soon as both teams bet. Each spoken answer
    opens with a beep after Richie's line, and only then does the phone say "answer now"; "¡Correcto!" goes when
    the next pick starts, and nobody is asked to pick once the board is empty (2026-11-02).
    """
    for name, value in {"PICK_S": 1.0, "BUZZ_S": 0.5, "REBUZZ_S": 0.3, "ANSWER_S": 1.0, "EVERYONE_S": 0.3,
                        "BET_S": 30.0, "FINAL_S": 0.3, "AFTER_S": 0.0, "FINAL_ANSWER_S": 0.0, "LOBBY_REMINDER_S": 5.0}.items():  # fmt: skip
        monkeypatch.setattr(big_brain, name, value)

    async def nothing(self: Phones) -> None:
        return None

    monkeypatch.setattr(Phones, "start", nothing)
    monkeypatch.setattr(Phones, "stop", nothing)
    bank_dir = tmp_path / "trivia-bank"
    bank_dir.mkdir()
    (bank_dir / "bank.jsonl").write_text("\n".join(json.dumps(entry) for entry in _bank()))
    reachy = FakeReachy(real_clock=True)
    games: list[BigBrain] = []
    played: list[tuple[str, bool]] = []
    given: list[str] = []
    beeps: list[tuple[int, bool]] = []  # each listening clip: its loudest sample, and the phone saying "answer now"

    async def say_audio(
        rate: int, samples: np.ndarray, text: str, language: str, answer: Any, *, listen_s: float | None = None
    ) -> None:
        played.append((text, answer is not None))
        game = games[0]
        if answer is not None and game._buzz is not None:
            beeps.append((int(np.abs(samples).max()), game.snapshot(game._buzz)["listening"]))
        if answer is not None and game._buzz is not None and game._clue is not None:
            said = [UNCLEAR_ANSWER, "Algo raro"][len(given)] if len(given) < 2 else game._clue.accept[0]
            given.append(said)
            asyncio.get_running_loop().create_task(answer(said))

    reachy.services.say_audio = say_audio

    def make() -> BigBrain:
        games.append(BigBrain(BigBrainSetup(data_dir=tmp_path, elevenlabs_key=None, port=0), rng=random.Random(3)))
        return games[0]

    host = GameHost(reachy.services, {"bigbrain": make})
    host.start("bigbrain", "es")
    await asyncio.sleep(0.1)
    room = _Room(games[0])
    room.join()
    host.pause()
    assert "resume" in games[0].snapshot(room.red)["can"]
    games[0].on_message(room.red, {"type": "resume"})
    assert host.state is GameState.PLAYING
    counted: list[str] = []
    counted_now = games[0]._counted_now

    async def spy(clue: Any) -> None:
        counted.append(clue.id)
        await counted_now(clue)

    monkeypatch.setattr(games[0], "_counted_now", spy)
    playing = asyncio.get_running_loop().create_task(room.play())
    seen: list[dict[str, Any]] = []  # what red's phone showed, every 10 ms

    async def watch() -> None:
        while True:
            seen.append({**games[0].snapshot(room.red), "left": games[0]._board.left if games[0]._board else None})
            await asyncio.sleep(0.01)

    watching = asyncio.get_running_loop().create_task(watch())
    try:
        for _ in range(900):
            if host.state is GameState.IDLE:
                break
            await asyncio.sleep(0.05)
    finally:
        playing.cancel()
        watching.cancel()
    game = games[0]
    assert host.state is GameState.IDLE
    assert game._phase == "over"
    assert beeps and all(peak > 5000 and talk_now for peak, talk_now in beeps)
    assert not any(s["listening"] for s in seen if s["phase"] != "answering")
    picking = [s for s in seen if "pick" in s["can"]]
    assert picking and not any(s["left"] == 0 for s in picking)
    assert not any((s["message"] or "").startswith("¡Correcto") for s in picking)
    assert not any(s["message"] for s in seen if s["phase"].startswith("final"))
    assert (
        given[:3] == [UNCLEAR_ANSWER, "Algo raro", given[2]]
        and len(counted) == 1
        and game.snapshot(room.red)["final"]["answer"] == game._board.final.answer
    )  # type: ignore[union-attr]
    assert game._scores["red"] > 0 >= game._scores.get("blue", 0)
    assert game._board is not None and game._board.left == 0
    assert [entry["team"] for entry in game._reveal] == ["blue", "red"]
    assert len(read_history(tmp_path / "big-brain" / "history.json")) == 21
    assert any(listening for _, listening in played)
    assert parse_game_request("juguemos big brain") is not None


@pytest.mark.asyncio
async def test_misses_in_a_row_a_drop_below_zero_a_comeback_and_a_runaway_get_their_own_lines(tmp_path: Path) -> None:
    """More of the host's banter (2026-11-03): each new moment plays once recorded, the old one until then."""
    game = BigBrain(BigBrainSetup(data_dir=tmp_path, elevenlabs_key=None, port=0))
    said: list[tuple[str, ...]] = []

    async def say(*moments: str, sounds: tuple[str, ...] = (), after: Any = None) -> None:
        said.append(moments)

    game._say = say  # type: ignore[method-assign]
    game._teams_playing = lambda: ["red", "blue"]  # type: ignore[method-assign]
    recorded = {"wrong_streak", "comeback", "below_zero", "runaway"}
    game._clips.has = lambda moment, language: moment in recorded  # type: ignore[method-assign]
    red = Player("p1", "Ana", "red", 0.0)
    clue = Clue("c1", "Cosas", 400, "¿Qué?", "Esto", ["esto"], 0.5)
    game._scores = {"red": 600, "blue": 0}
    for _ in range(3):
        await game._wrong(red, clue, "otra cosa")
    assert said == [("wrong",), ("wrong", "below_zero"), ("wrong_streak",)]
    recorded.discard("wrong_streak")
    for _ in range(3):
        await game._wrong(red, clue, "otra cosa")
    assert said[-1] == ("wrong",)

    said.clear()
    game._scores = {"red": 3000, "blue": 0}
    await game._standings("red", "red")
    assert said == [("runaway",)]
    await game._standings("red", "red")  # said once per leader
    game._scores = {"red": 3000, "blue": 3200}
    await game._standings("red", "blue")
    assert said == [("runaway",), ("comeback",)]
    game._scores = {"red": 3400, "blue": 3200}
    await game._standings("blue", "red")
    assert said[-1] == ("lead_change",)
