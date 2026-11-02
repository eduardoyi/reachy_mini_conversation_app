"""Tests for Detectives: the cases and their schema, the witness prompt, the phones' page and a whole game."""

import io
import json
import random
import shutil
import asyncio
from typing import Any
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import detectives
from reachy_mini_conversation_app.qr_code import qr_matrix
from reachy_mini_conversation_app.games.base import GameContext
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.catalog import DETECTIVES, parse_game_request
from reachy_mini_conversation_app.games.detectives import PAGE, Detectives, DetectivesSetup, read_played
from reachy_mini_conversation_app.games.big_brain_audio import Speech
from reachy_mini_conversation_app.games.detectives_case import (
    CASES_DIR,
    LANGUAGES,
    CaseError,
    pick_case,
    load_cases,
    parse_case,
)
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.detectives_screens import render_card, render_join, render_board
from reachy_mini_conversation_app.games.detectives_witness import (
    WitnessError,
    shown,
    spoken,
    split_first,
    witness_instructions,
)


# The cases -------------------------------------------------------------------------------------------


def test_the_cases_follow_the_schema_in_both_languages() -> None:
    """Two or more adult dinner-party cases with 4–5 suspects and the kids' cookie case; the solution names the culprit."""
    cases = load_cases()
    adult = [case for case in cases if not case.kids]
    kids = [case for case in cases if case.kids]
    assert len(adult) >= 2 and all(4 <= len(case.suspects) <= 5 for case in adult)
    assert any(case.title["es"] == "¿Quién se comió las galletas?" for case in kids)
    for case in cases:
        culprit = case.character(case.culprit)
        assert culprit is not None
        for language in LANGUAGES:
            assert any(culprit.name[language].split()[-1] in step[language] for step in case.reveal), (case.id, language)  # fmt: skip
        assert all(clue.round <= case.rounds for clue in case.clues)
        assert all(clue.round == 1 for clue in case.clues[:1])


def test_the_kids_case_has_the_silly_suspects() -> None:
    """The cat, the grandmother and Richie himself are suspects in the cookie case."""
    cookies = next(case for case in load_cases() if case.kids)
    names = {c.name["en"] for c in cookies.suspects}
    assert {"Misifú the cat", "Grandma", "Richie the robot"} <= names


def _raw(case_id: str = "last-toast") -> dict[str, Any]:
    raw: dict[str, Any] = json.loads((CASES_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    return raw


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda raw: raw["intro"].pop("es"), "intro"),
        (lambda raw: raw.update(culprit="rosa"), "culprit"),
        (lambda raw: raw.update(motive="greed"), "motive"),
        (lambda raw: raw["clues"][0].update(round=9), "rounds"),
        (lambda raw: raw["characters"].append(dict(raw["characters"][0])), "unique"),
    ],
)
def test_a_broken_case_says_what_is_wrong(change: Any, problem: str) -> None:
    """A missing language, a witness as culprit, an unknown motive, a clue out of range or a repeated id."""
    raw = _raw()
    change(raw)
    with pytest.raises(CaseError, match=problem):
        parse_case(raw)


def test_kids_mode_picks_the_kids_case_and_fresh_cases_come_first() -> None:
    """Kids get the kids' case; adults get a case not played yet, else the one played longest ago."""
    cases = load_cases()
    rng = random.Random(1)
    assert pick_case(cases, kids=True, played=[], rng=rng).kids  # type: ignore[union-attr]
    adult = [case.id for case in cases if not case.kids]
    fresh = pick_case(cases, kids=False, played=adult[:1], rng=rng)
    assert fresh is not None and fresh.id != adult[0]
    oldest = pick_case(cases, kids=False, played=list(reversed(adult)), rng=rng)
    assert oldest is not None and oldest.id == adult[-1]


# The witness -----------------------------------------------------------------------------------------


def test_a_witness_sees_only_their_own_sheet_and_the_evidence_found() -> None:
    """An innocent suspect's prompt has their secret and the clues found, never the culprit's secret."""
    case = parse_case(_raw())
    valeria, julian = case.character("valeria"), case.character("julian")
    assert valeria is not None and julian is not None and julian.secret is not None
    found = [case.clues[0]]
    prompt = witness_instructions(case, valeria, found, "es")
    assert valeria.secret is not None and valeria.secret["es"] in prompt
    assert julian.secret["es"] not in prompt and julian.secret["en"] not in prompt
    assert case.clues[0].text["es"] in prompt and case.clues[-1].text["es"] not in prompt
    assert "Venezuelan Spanish" in prompt
    assert "young children" in witness_instructions(next(c for c in load_cases() if c.kids), julian, [], "en")


def test_a_long_answer_is_cut_to_three_spoken_sentences() -> None:
    """Markdown and quotes go; at most three sentences are said."""
    assert spoken('**I** was there. "Truly." Ask Rosa! She saw. Nobody else.') == "I was there. Truly. Ask Rosa!"


def test_known_audio_tags_stay_for_the_voice_and_never_reach_the_screen() -> None:
    """The voice acts the allowed tags; other bracketed text is dropped; the screen and phones get none."""
    answer = spoken("[nervous] I was in the kitchen. [stage left] Ask Rosa. [laughs] Truly!")
    assert answer == "[nervous] I was in the kitchen. Ask Rosa. [laughs] Truly!"
    assert shown(answer) == "I was in the kitchen. Ask Rosa. Truly!"
    assert "[nervous]" in witness_instructions(parse_case(_raw()), parse_case(_raw()).characters[0], [], "en")


def test_the_rest_of_an_answer_keeps_the_mood_of_its_first_sentence() -> None:
    """The first sentence is voiced on its own; the rest starts with its last tag unless it has its own."""
    assert split_first("[nervous] I was there. Truly, ask Rosa.") == ("[nervous] I was there.", "[nervous] Truly, ask Rosa.")  # fmt: skip
    assert split_first("[sad] I was there. [laughs] Truly.") == ("[sad] I was there.", "[laughs] Truly.")
    assert split_first("Just one sentence.") == ("Just one sentence.", "")


# Voices ----------------------------------------------------------------------------------------------


def test_every_character_but_richie_has_a_designed_voice() -> None:
    """Suspects and witnesses speak in their own ElevenLabs voice; Richie plays himself in his own."""
    for case in load_cases():
        for character in case.characters:
            if character.id == "richie":
                assert character.voice is None
            else:
                assert character.voice is not None and character.voice.id, (case.id, character.id)


@pytest.mark.parametrize(
    ("voice", "problem"),
    [({"id": "abc123"}, "description"), ({"description": "A calm man.", "id": "../voices"}, "letters and digits")],
)
def test_a_broken_voice_says_what_is_wrong(voice: dict[str, str], problem: str) -> None:
    """A voice needs a description, and its id is a plain ElevenLabs id."""
    raw = _raw()
    raw["characters"][0]["voice"] = voice
    with pytest.raises(CaseError, match=problem):
        parse_case(raw)


@pytest.mark.asyncio
async def test_speech_uses_the_voice_it_is_given_and_can_keep_nothing(tmp_path: Path) -> None:
    """A character's voice id goes in the request; with keep=False no clip is written to disk."""
    urls: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, content=b"\x01\x00" * 240)

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    speech = Speech(tmp_path, "key", client, voice="Valeria123", keep=False)
    samples = await speech.say("[annoyed] I was on the terrace.", "en")
    assert samples is not None and len(samples) == 240
    assert "/text-to-speech/Valeria123" in urls[0] and not list(tmp_path.iterdir())
    await client.aclose()


class _FakeSpeech:
    """Stands in for ElevenLabs: one sample per character, or None for text containing ``fail``."""

    made: list[tuple[str, str]] = []

    def __init__(self, folder: Path, api_key: str | None, *, voice: str, keep: bool) -> None:
        self.voice = voice

    async def say(self, text: str, language: str) -> Any:
        _FakeSpeech.made.append((self.voice, text))
        return None if "fail" in text else np.zeros(len(text), dtype=np.int16)


def _voiced_game(tmp_path: Path, reachy: FakeReachy, *, key: str | None) -> tuple[Detectives, Any]:
    game = Detectives(DetectivesSetup(data_dir=tmp_path, port=0, witness=None, elevenlabs_key=key))
    context = GameContext(info=DETECTIVES, language="en", services=reachy.services)
    context.running.set()
    game._context = context
    return game, parse_case(_raw())


@pytest.mark.asyncio
async def test_a_character_speaks_in_their_voice_first_sentence_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both parts are made in Valeria's voice with their tags; each plays with the tags left out of its text."""
    monkeypatch.setattr(detectives, "Speech", _FakeSpeech)
    _FakeSpeech.made = []
    reachy = FakeReachy(recorded=True)
    game, case = _voiced_game(tmp_path, reachy, key="key")
    valeria = case.character("valeria")
    await game._say_as(valeria, "[annoyed] I was on the terrace. With a friend.")
    assert valeria.voice is not None
    assert _FakeSpeech.made == [(valeria.voice.id, "[annoyed] I was on the terrace."), (valeria.voice.id, "[annoyed] With a friend.")]  # fmt: skip
    assert [text for text, _ in reachy.clips] == ["I was on the terrace.", "With a friend."] and not reachy.said


@pytest.mark.asyncio
async def test_richie_says_the_line_when_there_is_no_voice_or_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key: Richie says it all; a failed second part: the first plays in the voice and Richie says the rest."""
    monkeypatch.setattr(detectives, "Speech", _FakeSpeech)
    reachy = FakeReachy(recorded=True)
    game, case = _voiced_game(tmp_path, reachy, key=None)
    await game._say_as(case.character("valeria"), "[sad] I loved him. Truly.")
    assert reachy.said == ["I loved him. Truly."] and not reachy.clips

    reachy = FakeReachy(recorded=True)
    game, case = _voiced_game(tmp_path, reachy, key="key")
    await game._say_as(case.character("rosa"), "[sad] I saw it all. Then it failed.")
    assert [text for text, _ in reachy.clips] == ["I saw it all."] and reachy.said == ["Then it failed."]


# Phones and screens ----------------------------------------------------------------------------------


def test_the_phones_get_the_detectives_page() -> None:
    """The shared phone server serves Detectives' own page with the game's code."""
    phones = Phones(0, lambda player: {}, lambda player, message: None, lambda: True, code="ABCDEF", clock=lambda: 0.0, page=PAGE)  # fmt: skip
    client = TestClient(phones.app)
    page = client.get("/?c=abcdef")
    assert page.status_code == 200 and "DETECTIVES" in page.text
    assert client.get("/?c=XXXXXX").status_code == 404


def test_the_screens_render() -> None:
    """Join, board and card screens are PNGs (2-bit, 800×480)."""
    for png in (
        render_join("http://192.168.1.2:7880/?c=ABCDEF", "ABCDEF", ["Ana", "Luis"], "es", kids=True),
        render_board(
            "El último brindis", [("Valeria Montoro", False), ("Rosa", True)], ["La copa"], 1, 4, "Ana", "es"
        ),
        render_card("Rosa", "Don Aurelio tomó la primera copa. " * 6, footer="Ana"),
    ):
        assert png.startswith(b"\x89PNG")


def test_the_join_qr_code_keeps_its_size_and_a_white_quiet_zone() -> None:
    """Phones scan the code across the room: up to 400 px, with two white modules all around it."""
    url = "https://reachy.tail1234.ts.net/detectives/?c=ABCDEF"
    screen = Image.open(io.BytesIO(render_join(url, "ABCDEF", ["Ana"] * 12, "es", kids=True))).convert("L")
    modules = len(qr_matrix(url))
    scale = min(400 // (modules + 4), 14)
    size = (modules + 4) * scale
    left, top = 30, (480 - size) // 2
    zone = screen.crop((left, top, left + size, top + size))
    inside = screen.crop((left + 2 * scale, top + 2 * scale, left + size - 2 * scale, top + size - 2 * scale))
    assert size >= 370
    assert zone.histogram()[0] == inside.histogram()[0]  # every black pixel is inside the code
    assert set(zone.get_flattened_data()) == {0, 255}  # crisp: no greys


@pytest.mark.parametrize(
    "said",
    ["juguemos Detectives", "let's play Detectives", "el juego de detectives", "el caso", "juguemos a los detectives",
     "let's play a mystery game", "juguemos detective"],
)  # fmt: skip
def test_detectives_starts_by_its_names(said: str) -> None:
    """Its names in both languages, and a close mishearing."""
    request = parse_game_request(said)
    assert request is not None and request.game is not None and request.game.key == "detectives"


# A whole game ----------------------------------------------------------------------------------------


class _Room:
    """Three pretend phones. Ana types a question, then asks one aloud; Luis examines clues; Eva never plays.

    Ana and Luis vote to accuse once two statements are in; Ana accuses the doctor for the right motive, Luis Valeria.
    """

    def __init__(self, game: Detectives, reachy: FakeReachy) -> None:
        self.game = game
        self.reachy = reachy
        self.ana = Player("p1", "Ana", "red", 0.0)
        self.luis = Player("p2", "Luis", "red", 1.0)
        self.eva = Player("p3", "Eva", "red", 2.0)

    def join(self) -> None:
        phones = self.game._phones
        assert phones is not None
        for player in (self.ana, self.luis, self.eva):
            phones.players[player.id] = player
            self.game.on_message(player, {"type": "joined"})

    async def play(self) -> None:
        game = self.game
        while True:
            await asyncio.sleep(0.02)
            while self.reachy.asked:  # Richie asked Ana for her question: she says it aloud
                _, answer = self.reachy.asked.pop(0)
                asyncio.get_running_loop().create_task(answer("Did you put anything in his glass?"))
            for player in (self.ana, self.luis):
                can = game.snapshot(player)["can"]
                if "start" in can and player is self.ana:
                    game.on_message(player, {"type": "start"})
                elif "vote" in can and len(game._notes) >= 2:
                    game.on_message(player, {"type": "vote"})
                elif "act" in can and player is self.ana:
                    if not game._notes:
                        game.on_message(player, {"type": "ask", "id": "valeria", "text": "Where were you at 9:10?"})
                    else:
                        game.on_message(player, {"type": "ask", "id": "julian"})
                elif "act" in can:
                    clue = game.snapshot(player)["available"][0]["id"]
                    game.on_message(player, {"type": "examine", "id": clue})
                elif "accuse" in can:
                    suspect = "julian" if player is self.ana else "valeria"
                    game.on_message(player, {"type": "accuse", "suspect": suspect, "motive": "secret"})


@pytest.mark.asyncio
async def test_a_whole_game_from_joining_to_the_solution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three detectives play the adult case: statements from the model and the scripted line, a clue, the vote, the truth.

    The model answers the typed question and fails on the spoken one (the doctor's scripted line is said instead);
    Eva lets her turn pass; the room votes to accuse in round 2; only Ana solves it, motive included.
    """
    for name, value in {"TURN_S": 0.3, "QUESTION_S": 1.0, "ACCUSE_S": 0.5, "AFTER_S": 0.0, "LOBBY_REMINDER_S": 5.0}.items():  # fmt: skip
        monkeypatch.setattr(detectives, name, value)

    async def nothing(self: Phones) -> None:
        return None

    monkeypatch.setattr(Phones, "start", nothing)
    monkeypatch.setattr(Phones, "stop", nothing)
    cases = tmp_path / "cases"
    cases.mkdir()
    shutil.copy(CASES_DIR / "last-toast.json", cases)
    asked: list[tuple[str, str]] = []

    async def witness(instructions: str, question: str) -> str:
        asked.append((instructions, question))
        if len(asked) > 1:
            raise WitnessError("busy")
        return "I was on the terrace. With a friend. Why do you ask? I loved my father. Truly."

    reachy = FakeReachy(real_clock=True)
    games: list[Detectives] = []

    def make() -> Detectives:
        setup = DetectivesSetup(data_dir=tmp_path, port=0, witness=witness, cases_dir=cases)
        games.append(Detectives(setup, rng=random.Random(4)))
        return games[0]

    host = GameHost(reachy.services, {"detectives": make})
    host.start("detectives", "en")
    await asyncio.sleep(0.1)
    room = _Room(games[0], reachy)
    room.join()
    playing = asyncio.get_running_loop().create_task(room.play())
    roles: set[str] = set()
    try:
        for _ in range(600):
            if host.state is GameState.IDLE:
                break
            if games[0]._case is not None and not roles:
                roles = {games[0].snapshot(p)["you"]["role"]["name"] for p in (room.ana, room.luis, room.eva)}
            await asyncio.sleep(0.05)
    finally:
        playing.cancel()
    game = games[0]
    case = game._case
    assert host.state is GameState.IDLE and game._phase == "over" and case is not None
    assert roles == {"Detective", "Detective · secret witness"}  # three players: one also saw something
    assert [(note.asker, note.character.id) for note in game._notes] == [("Ana", "valeria"), ("Ana", "julian")]
    assert game._notes[0].answer == "I was on the terrace. With a friend. Why do you ask?"
    assert game._notes[1].question == "Did you put anything in his glass?"
    assert game._notes[1].answer == case.character("julian").fallback["en"]  # type: ignore[union-attr]
    assert "Your secret: The winery owes the bank" in asked[0][0] and "digitalis drops" not in asked[0][0]
    assert len(game._found) == 1 and game._round == 2
    assert "Eva passes." in reachy.said
    result = game.snapshot(room.ana)["result"]
    assert result["solvers"] == ["Ana"] and result["motive_right"] == ["Ana"] and result["you_right"]
    assert not game.snapshot(room.luis)["result"]["you_right"]
    assert all(step["en"] in reachy.said for step in case.reveal)
    assert "The culprit is Doctor Julián Ferrer!" in reachy.said
    assert read_played(tmp_path / "detectives" / "played.json") == ["last-toast"]
