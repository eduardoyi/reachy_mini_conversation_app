"""Scribble Rush: the word lists, judging Richie's guesses, the vision call and whole games on a pretend Reachy."""

import io
import random
import asyncio
from typing import Any
from collections import defaultdict

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from .fakes import FakeReachy, frame
from reachy_mini_conversation_app.games import scribble_rush
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.catalog import parse_game_request
from reachy_mini_conversation_app.games.scribble_rush import (
    PAGE,
    Guesses,
    VisionError,
    ScribbleRush,
    GeminiGuesser,
    ScribbleSetup,
    guessed,
    read_guesses,
    judge_guesses,
)
from reachy_mini_conversation_app.games.scribble_words import CATEGORIES, KIDS_WORDS, ADULT_WORDS, Word
from reachy_mini_conversation_app.games.big_brain_judge import plain
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.scribble_screens import render_track


def _word(es: str) -> Word:
    return next(word for word in (*KIDS_WORDS, *ADULT_WORDS) if word.es == es)


def test_the_word_lists_are_big_enough_and_every_word_has_both_languages() -> None:
    """At least 120 kids' and 200 adults' words, each in both languages, with no form shared by two words."""
    assert len(KIDS_WORDS) >= 120 and len(ADULT_WORDS) >= 200
    for words in (KIDS_WORDS, ADULT_WORDS):
        owners: dict[str, set[str]] = defaultdict(set)
        for word in words:
            assert word.es and word.en and word.category in CATEGORIES
            assert word.es in word.accept and word.en in word.accept
            for form in word.accept:
                owners[plain(form)].add(word.es)
        assert not {form: names for form, names in owners.items() if len(names) > 1}
        assert len({word.es for word in words}) == len(words)
    assert not {word.category for word in KIDS_WORDS} & {"movie", "saying", "action"}


@pytest.mark.parametrize(
    ("guess", "es", "hit"),
    [
        ("un gato", "gato", True),
        ("Unos gatitos", "gato", True),
        ("a kitten", "gato", True),
        ("¿Un gato negro?", "gato", True),
        ("un perro", "gato", False),
        ("leones", "león", True),
        ("el Rey León", "El Rey León", True),
        ("The Lion King", "El Rey León", True),
        ("un león", "El Rey León", False),
        ("un camarón durmiendo en el río", "Camarón que se duerme se lo lleva la corriente", True),
        ("", "gato", False),
    ],
)
def test_code_counts_a_guess_in_either_language_with_articles_and_plurals(guess: str, es: str, hit: bool) -> None:
    """A guess counts when it is one of the word's forms in either language, plural or with a few more words."""
    assert guessed(guess, _word(es)) is hit


@pytest.mark.asyncio
async def test_jev_rules_only_on_guesses_code_did_not_match_and_a_failure_is_a_miss() -> None:
    """One Jev call for the unmatched guesses; when Jev fails they are misses and nothing raises."""
    asked: list[dict[str, Any]] = []

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        asked.append(state)
        assert not images
        return {key: {"noul": 0.9 if state[key] == "un minino" else 0.1} for key in questions}

    cat = _word("gato")
    assert await judge_guesses(["un perro", "un minino tierno", "un gato"], cat, jev) == [False, True, True]
    assert await judge_guesses(["un perro", "un tigrito"], cat, jev) == [False, False]
    assert [set(state) - {"secret", "accepted_answers"} for state in asked] == [{"guess_1"}, {"guess_1", "guess_2"}]

    async def broken(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        return {}

    assert await judge_guesses(["un perro"], cat, broken) == [False]
    assert await judge_guesses(["un perro"], cat, None) == [False]


def test_the_vision_answer_is_read_least_to_most_confident_and_at_most_three() -> None:
    """Guesses are sorted by confidence (most confident last) and cut to three; nonsense raises VisionError."""
    answer = (
        '{"remark": "¡Qué obra!", "guesses": [{"guess": "un gato", "confidence": 0.9}, {"guess": "un perro", '
        '"confidence": 0.2}, {"guess": "un zorro", "confidence": 0.5}, {"guess": "un oso", "confidence": 0.1}, '
        '{"guess": "  ", "confidence": 1}]}'
    )
    assert read_guesses(answer) == Guesses("¡Qué obra!", ["un perro", "un zorro", "un gato"])
    assert read_guesses('{"guesses": []}') == Guesses("", [])
    for bad in ("not json", "[1, 2]"):
        with pytest.raises(VisionError):
            read_guesses(bad)


@pytest.mark.asyncio
async def test_the_vision_model_sees_the_category_never_the_word_and_its_use_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One Gemini call per drawing, with the image and the category only; its tokens go to the usage ledger."""
    sent: list[Any] = []
    recorded: list[tuple[str, str, dict[str, int]]] = []

    class _Usage:
        prompt_token_count, candidates_token_count, thoughts_token_count = 300, 40, 20

    class _Response:
        text = '{"remark": "Mmm", "guesses": [{"guess": "un gato", "confidence": 0.8}]}'
        usage_metadata = _Usage()

    class _Models:
        async def generate_content(self, **request: Any) -> _Response:
            sent.append(request)
            return _Response()

    class _Ledger:
        def record(self, feature: str, model: str, tokens: dict[str, int]) -> None:
            recorded.append((feature, model, tokens))

    monkeypatch.setattr(scribble_rush, "get_ledger", lambda: _Ledger())
    guesser = GeminiGuesser("key", "gemini-3.8-flash")
    guesser._client = type("Client", (), {"aio": type("Aio", (), {"models": _Models()})()})()  # type: ignore[assignment]
    word = _word("Titanic")
    result = await guesser(b"jpeg", word, "es", False)
    assert result == Guesses("Mmm", ["un gato"])
    prompt = sent[0]["contents"][0]
    assert "movie" in prompt and "Spanish" in prompt and "titanic" not in prompt.lower()
    assert recorded == [("scribble_guess", "gemini-3.8-flash", {"input_text": 300, "output_text": 40, "thinking": 20})]


def test_the_track_shows_the_category_as_a_big_label() -> None:
    """The category is a black label in front of the drawer and round (players missed it in the caption, 2026-11-02)."""
    lanes = [("Rojo", 3), ("Azul", 0), ("Verde", 7)]
    tagged = render_track(
        "ROJO DIBUJA", "Sofía · Ronda 6 de 6", lanes, 24, turn="Rojo", tag="CATEGORÍA: PERSONAJE U OFICIO"
    )
    untagged = render_track("ROJO DIBUJA", "Sofía · Ronda 6 de 6", lanes, 24, turn="Rojo")

    def black(png: bytes) -> int:
        """Black pixels on the caption's band."""
        image = Image.open(io.BytesIO(png)).convert("L")
        assert image.size == (800, 480)
        return int((np.asarray(image.crop((0, 100, 800, 170))) < 40).sum())

    assert black(tagged) > black(untagged) + 5000


def test_the_phones_get_the_scribble_page_and_the_start_phrases_name_the_game() -> None:
    """The shared phone server serves Scribble Rush's own page; every start phrase starts the game."""
    phones = Phones(0, lambda player: {}, lambda player, message: None, lambda: True, clock=lambda: 0.0, page=PAGE)
    response = TestClient(phones.app).get(f"/?c={phones.code}")
    assert response.status_code == 200 and "Scribble" in response.text
    for said in (
        "juguemos Scribble Rush",
        "let's play Scribble Rush",
        "el juego de dibujar",
        "dibujo relámpago",
        "drawing game",
        "juguemos escribel rush",
    ):
        request = parse_game_request(said)
        assert request is not None and request.game is not None and request.game.key == "scribble", said


class _Room:
    """Two pretend phones on two teams; the drawer always presses "done", red presses start."""

    def __init__(self, game: ScribbleRush, *, kids: bool = False) -> None:
        self.game = game
        self.kids = kids
        self.red = Player("p1", "Ana", "red", 0.0)
        self.blue = Player("p2", "Luis", "blue", 1.0)
        self.seen_words: list[tuple[str, str | None]] = []

    def join(self, *players: Player) -> None:
        phones = self.game._phones
        assert phones is not None
        for player in players:
            phones.players[player.id] = player
            self.game.on_message(player, {"type": "joined"})

    async def play(self) -> None:
        game = self.game
        while True:
            await asyncio.sleep(0.02)
            for player in (self.red, self.blue):
                if game._phones is None or player.id not in game._phones.players:
                    continue
                state = game.snapshot(player)
                if "start" in state["can"] and player is self.red:
                    if self.kids:
                        game.on_message(player, {"type": "kids", "on": True})
                    game.on_message(player, {"type": "start"})
                if state["phase"] == "drawing":
                    self.seen_words.append((player.id, state["word"]))
                if "done" in state["can"]:
                    game.on_message(player, {"type": "done"})


def _fast(monkeypatch: pytest.MonkeyPatch, rounds: int) -> None:
    for name, value in {"DRAW_S": 0.3, "KIDS_DRAW_S": 0.3, "HOLD_S": 0.0, "RESULT_S": 0.0, "AFTER_S": 0.0,
                        "LOBBY_REMINDER_S": 5.0, "ROUNDS": rounds}.items():  # fmt: skip
        monkeypatch.setattr(scribble_rush, name, value)

    async def nothing(self: Phones) -> None:
        return None

    monkeypatch.setattr(Phones, "start", nothing)
    monkeypatch.setattr(Phones, "stop", nothing)


async def _run(host: GameHost) -> None:
    for _ in range(400):
        if host.state is GameState.IDLE:
            return
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_whole_game_from_joining_to_the_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two teams, two rounds: a first-guess hit, a miss, a failed vision call and a second-guess hit.

    Only the drawer's phone shows the word; Richie says each guess until the hit; the team with most squares wins.
    """
    _fast(monkeypatch, rounds=2)
    reachy = FakeReachy(real_clock=True)
    reachy.frames.append(frame(1))
    turns: list[Word] = []

    async def guess(jpeg: bytes, word: Word, language: str, kids: bool) -> Guesses:
        turns.append(word)
        assert jpeg and language == "es" and not kids
        if len(turns) == 1:
            return Guesses("¡Qué obra de arte!", [word.es])
        if len(turns) == 2:
            return Guesses("", ["un ornitorrinco", "una tetera", "un submarino amarillo"])
        if len(turns) == 3:
            raise VisionError("APIError")
        return Guesses("", ["un ornitorrinco", word.en])

    async def jev(state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        return {key: {"noul": 0.0} for key in questions}

    reachy.services.jev = jev
    games: list[ScribbleRush] = []

    def make() -> ScribbleRush:
        games.append(ScribbleRush(ScribbleSetup(port=0, guess=guess), rng=random.Random(5)))
        return games[0]

    host = GameHost(reachy.services, {"scribble": make})
    host.start("scribble", "es")
    await asyncio.sleep(0.1)
    room = _Room(games[0])
    room.join(room.red, room.blue)
    playing = asyncio.get_running_loop().create_task(room.play())
    try:
        await _run(host)
    finally:
        playing.cancel()
    game = games[0]
    assert host.state is GameState.IDLE and game._phase == "over"
    assert len(turns) == 4 and len({word.es for word in turns}) == 4
    squares = sorted(game._squares.values())
    assert squares == [4, 6]
    winner = max(game._squares, key=lambda team: game._squares[team])
    assert f"¡Ganó el equipo {'Rojo' if winner == 'red' else 'Azul'}!" in reachy.said
    assert "¡Qué obra de arte!" in reachy.said
    assert any("ornitorrinco" in line for line in reachy.said) and any("submarino" in line for line in reachy.said)
    assert any(line.startswith("¡Ay, se me nubló la vista!") for line in reachy.said)
    shown = {(player, word) for player, word in room.seen_words}
    drawers = {player for player, word in shown if word}
    assert drawers == {"p1", "p2"} and all(word in {w.es for w in turns} for _, word in shown if word)
    assert not any(word for player, word in room.seen_words if game._phones and player not in drawers)
    assert reachy.audio, "the tick-tock played while drawing"


@pytest.mark.asyncio
async def test_kids_mode_draws_kids_words_and_a_miss_still_moves_a_square(monkeypatch: pytest.MonkeyPatch) -> None:
    """In kids mode the words are the kids' list, a miss moves one square, and no camera frame means no squares."""
    _fast(monkeypatch, rounds=2)
    reachy = FakeReachy(real_clock=True)
    reachy.services.jev = None
    words: list[Word] = []

    async def guess(jpeg: bytes, word: Word, language: str, kids: bool) -> Guesses:
        words.append(word)
        assert kids
        return Guesses("", ["un ornitorrinco"])

    games: list[ScribbleRush] = []

    def make() -> ScribbleRush:
        games.append(ScribbleRush(ScribbleSetup(port=0, guess=guess), rng=random.Random(2)))
        return games[-1]

    host = GameHost(reachy.services, {"scribble": make})
    host.start("scribble", "en")
    await asyncio.sleep(0.1)
    room = _Room(games[0], kids=True)
    room.join(room.red)
    playing = asyncio.get_running_loop().create_task(room.play())
    try:
        await _run(host)
    finally:
        playing.cancel()
    game = games[0]
    assert host.state is GameState.IDLE
    assert not words, "no camera frame: the vision model is never asked"
    assert game._squares == {"red": 0}
    assert sum(line.startswith("Oops, my eyes went blurry!") for line in reachy.said) == 2

    reachy.frames.append(frame(2))
    host.start("scribble", "en")
    await asyncio.sleep(0.1)
    room = _Room(games[1], kids=True)
    room.join(room.red)
    playing = asyncio.get_running_loop().create_task(room.play())
    try:
        await _run(host)
    finally:
        playing.cancel()
    assert len(words) == 2 and all(word in KIDS_WORDS for word in words)
    assert games[1]._squares == {"red": 2}
    assert reachy.said.count("Great try! One square for drawing.") == 2
