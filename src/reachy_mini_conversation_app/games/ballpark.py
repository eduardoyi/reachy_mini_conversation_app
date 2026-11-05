"""Ballpark: a range-guessing game Richie hosts for teams playing on their phones (docs/features/ballpark.md).

Players join by scanning the QR code on the TRMNL, as in Big Brain. Each question has a whole number for an
answer ("How many teeth does a garden snail have?"), and each team writes a range it is sure the answer is in,
not a guess. The smallest range that holds the answer scores 3, every other right range 1, and when every team
is right the widest scores nothing. Each team may, once a game each, copy another team's range, swap two other
teams' ranges, or double its points. One question a game is a surprise: an exact answer scores 5, or everybody's
points count double. The first team to 15 wins.

Richie hosts in Big Brain's host voice: fixed moments are the recorded clips of ``ballpark_lines.py`` (a moment
not recorded yet is said live from one of its takes), and questions, answers, facts, team names and ranges are
said live in the same voice and kept by text. Without an ElevenLabs key, his everyday voice says it all. The
questions ship with the app (``ballpark_questions.jsonl``), written and fact-checked with sources.
"""

import json
import math
import time
import random
import asyncio
import logging
from typing import Any, Final
from pathlib import Path
from dataclasses import dataclass

from reachy_mini_conversation_app.games.base import Game, GameContext
from reachy_mini_conversation_app.games.catalog import BALLPARK, words
from reachy_mini_conversation_app.games.big_brain import START_WORDS, team_name
from reachy_mini_conversation_app.games.ballpark_lines import LINES as HOST_LINES
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, Clips, Sounds, Speech, join
from reachy_mini_conversation_app.games.big_brain_board import remember, read_history
from reachy_mini_conversation_app.games.big_brain_lines import TEAMS, shown
from reachy_mini_conversation_app.games.ballpark_screens import Row, number, render_reveal, render_question
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.big_brain_screens import render_join, render_winner


logger = logging.getLogger(__name__)

PAGE: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "ballpark" / "index.html"
BANK: Final[Path] = Path(__file__).resolve().parent / "ballpark_questions.jsonl"
TARGET: Final[int] = 15
MAX_QUESTIONS: Final[int] = 20  # a game where nobody scores still ends
ANSWER_S: Final[float] = 60.0
HURRY_S: Final[float] = 15.0  # "hurry" is said when this much answering time is left
SURPRISE_FIRST, SURPRISE_LAST = 3, 7  # the surprise is one of these questions (1-based)
LOBBY_REMINDER_S: Final[float] = 75.0
AFTER_S: Final[float] = 30.0  # the page stays up this long after the game, for the final scores
BIGGEST: Final[int] = 10**13  # no range end is larger
NEAR: Final[float] = 0.1  # a wrong range whose nearer end misses by this share of the answer "was close"
FAR: Final[float] = 10.0  # ...and one this many times off (or a tenth) was "way off"
MATCH_POINT: Final[int] = TARGET - 3  # one smallest range from winning
RUNAWAY_POINTS: Final[int] = 7
POWERS: Final[tuple[str, ...]] = ("copy", "swap", "double")
SURPRISES: Final[tuple[str, ...]] = ("nail", "double")

# What Richie says live (names, ranges, answers), and what the screens and phones show.
_LINES: Final[dict[str, dict[str, str]]] = {
    "no_bank": {"es": "No encuentro las preguntas.", "en": "I can't find the questions."},
    "question": {"es": "Pregunta {number}.", "en": "Question {number}."},
    "label": {"es": "Pregunta {number}", "en": "Question {number}"},
    "team": {"es": "¡Equipo {name}!", "en": "{name} team!"},
    "range": {"es": "{name}: de {low} a {high}.", "en": "{name}: from {low} to {high}."},
    "exact": {"es": "{name}: exactamente {value}.", "en": "{name}: exactly {value}."},
    "copied": {"es": "[playful] ¡{name} copió a {other}!", "en": "[playful] {name} copied {other}!"},
    "copied_none": {
        "es": "[mock disappointment] {name} copió a {other}... [pause] ¡pero {other} no mandó nada!",
        "en": "[mock disappointment] {name} copied {other}... [pause] but {other} sent nothing!",
    },
    "copied_loop": {
        "es": "[laughs] ¡{names} se copiaron entre sí! [pause] Nadie tiene rango.",
        "en": "[laughs] {names} copied each other! [pause] Nobody has a range.",
    },
    "swapped": {
        "es": "[dramatic] ¡{name} cambió los rangos de {first} y {second}!",
        "en": "[dramatic] {name} swapped {first} and {second}'s ranges!",
    },
    "doubled": {"es": "[excited] ¡{name} va con puntos dobles!", "en": "[excited] {name} is going for double!"},
    "answer": {"es": "[booming] ¡{said}!", "en": "[booming] {said}!"},
    "fact": {"es": "{fact}", "en": "{fact}"},
    "names": {"es": "¡{names}!", "en": "{names}!"},
    "bravest": {
        "es": "El rango más valiente del juego: {name}, de {low} a {high}.",
        "en": "The bravest range of the game: {name}, from {low} to {high}.",
    },
    "and": {"es": " y ", "en": " and "},
    "note_copy": {"es": "copia a {name}", "en": "copied {name}"},
    "note_swap": {"es": "cambio con {name}", "en": "swapped with {name}"},
}

_POWER_NAMES: Final[dict[str, dict[str, str]]] = {
    "copy": {"es": "Copia", "en": "Copy"},
    "swap": {"es": "Cambio", "en": "Swap"},
    "double": {"es": "×2", "en": "×2"},
}


@dataclass(frozen=True)
class Live:
    """Words Richie says live in the host voice: a question, an answer, a name. Tags shape them, unshown."""

    text: str


@dataclass
class BallparkSetup:
    """Where Ballpark's files are and where phones reach it; main.py fills it from the config."""

    data_dir: Path
    port: int
    public_url: str | None = None  # the phones' https address, as for Big Brain (the same port)
    elevenlabs_key: str | None = None  # the host voice; without it Richie's everyday voice hosts
    bank: Path = BANK

    @property
    def folder(self) -> Path:
        """Return Ballpark's own folder on the Mac mini: the clips, the live lines and the history."""
        return self.data_dir / "ballpark"


@dataclass(frozen=True)
class Question:
    """One question in the game's language: its text, unit, answer, how the answer is said, and a fun fact."""

    id: str
    text: str
    unit: str
    answer: int
    said: str
    fact: str


def load_questions(path: Path) -> list[dict[str, Any]]:
    """Return the bank's entries (one JSON object a line)."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def question(entry: dict[str, Any], language: str) -> Question | None:
    """Return a bank entry in ``language``, or None when it lacks the text or a whole number answer."""

    def text(key: str) -> str:
        return str((entry.get(key) or {}).get(language) or "").strip()

    answer = entry.get("answer")
    if not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer <= BIGGEST:
        return None
    if not text("question") or not text("said"):
        return None
    return Question(str(entry["id"]), text("question"), text("unit"), answer, text("said"), text("fact"))


def pick_questions(
    bank: list[dict[str, Any]],
    language: str,
    *,
    recent: dict[str, float] | None = None,
    count: int = MAX_QUESTIONS,
    rng: random.Random | None = None,
) -> list[Question]:
    """Return ``count`` questions (fewer when the bank is short), new ones first, the themes mixed."""
    rng = rng or random.Random()
    recent = recent or {}
    entries = list(bank)
    rng.shuffle(entries)
    # Questions played lately come last, the longest ago first, so a short bank still fills a game.
    entries.sort(key=lambda entry: recent.get(str(entry.get("id")), 0.0))
    picked: list[Question] = []
    for entry in entries:
        found = question(entry, language)
        if found is not None:
            picked.append(found)
        if len(picked) == count:
            break
    return picked


@dataclass
class Entry:
    """What one team sent for a question: its range (none when it copies), and the powers it used."""

    low: int | None = None
    high: int | None = None
    copy: str | None = None  # the team whose range it takes
    swap: tuple[str, str] | None = None  # two other teams that trade ranges
    double: bool = False
    by: str = ""  # who sent it last, for the team's phones

    def powers(self) -> list[str]:
        """Return the powers this entry uses."""
        return [power for power, used in zip(POWERS, (self.copy, self.swap, self.double)) if used]


@dataclass
class Board:
    """A team's range once the powers are played, and what it scored."""

    team: str
    low: int | None
    high: int | None
    copied: str | None = None  # the team it copied
    swapped: str | None = None  # the team its range came from in a swap
    double: bool = False
    right: bool = False
    best: bool = False
    zeroed: bool = False  # right, but the widest when everyone was right
    points: int = 0

    @property
    def size(self) -> int | None:
        """Return the range's size (high minus low), None with no range."""
        return None if self.low is None or self.high is None else self.high - self.low


@dataclass(frozen=True)
class Played:
    """One power as it played out, for Richie to announce."""

    power: str
    team: str
    others: tuple[str, ...] = ()
    empty: bool = False  # a copy of a team that sent nothing
    loop: bool = False  # teams that copied each other


def resolve(entries: dict[str, Entry], teams: list[str]) -> tuple[dict[str, Board], list[Played]]:
    """Play the powers on the teams' ranges: copies (following chains), then swaps in team order, then doubles."""
    own = {
        team: (entry.low, entry.high) if entry.copy is None else (None, None)
        for team, entry in entries.items()
        if team in teams
    }
    boards = {team: Board(team, *own.get(team, (None, None))) for team in teams}
    played: list[Played] = []
    looped: set[str] = set()
    for team in teams:
        entry = entries.get(team)
        if entry is None or entry.copy is None:
            continue
        boards[team].copied = entry.copy
        seen, at = [team], entry.copy
        while at in entries and entries[at].copy is not None and at not in seen:
            seen.append(at)
            at = entries[at].copy or ""
        if at in seen:  # a loop: its teams (and whoever copied into it) have no range
            loop = tuple(seen[seen.index(at) :])
            if not set(loop) <= looped:
                looped |= set(loop)
                played.append(Played("copy", loop[0], loop[1:], loop=True))
            if team not in loop:
                played.append(Played("copy", team, (entry.copy,), empty=True))
            continue
        low, high = own.get(at, (None, None))
        boards[team].low, boards[team].high = low, high
        played.append(Played("copy", team, (entry.copy,), empty=low is None))
    for team in teams:
        entry = entries.get(team)
        if entry is None or entry.swap is None:
            continue
        first, second = entry.swap
        if first not in boards or second not in boards:
            continue
        a, b = boards[first], boards[second]
        a.low, a.high, b.low, b.high = b.low, b.high, a.low, a.high
        a.swapped, b.swapped = second, first
        played.append(Played("swap", team, (first, second)))
    for team in teams:
        entry = entries.get(team)
        if entry is not None and entry.double:
            boards[team].double = True
            played.append(Played("double", team))
    return boards, played


def score(boards: dict[str, Board], answer: int, *, nail: bool = False, double_all: bool = False) -> None:
    """Score each board: 3 for the smallest right range, 1 for other right ones, 0 for the widest when all are right.

    A "nail it" question gives an exact range 5; doubles multiply (a team's own and everybody's both count).
    """
    for board in boards.values():
        board.right = board.low is not None and board.high is not None and board.low <= answer <= board.high
    right = [board for board in boards.values() if board.right]
    if not right:
        return
    smallest = min(board.size or 0 for board in right)
    widest = max(board.size or 0 for board in right)
    everyone = len(boards) > 1 and len(right) == len(boards) and widest > smallest
    for board in right:
        board.best = board.size == smallest
        if everyone and board.size == widest:
            board.zeroed, board.points = True, 0
            continue
        points = 3 if board.best else 1
        if nail and board.size == 0:
            points = 5
        board.points = points * (2 if board.double else 1) * (2 if double_all else 1)


def to_whole(value: object) -> int | None:
    """Return a range end a phone sent as a whole number from 0 to BIGGEST, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):  # "1.500", "1,500" and "1 500" are all fifteen hundred
        digits = value.replace(".", "").replace(",", "").replace(" ", "").strip()
        value = int(digits) if digits.isdigit() else None
    if isinstance(value, float):
        value = round(value) if math.isfinite(value) else None
    if not isinstance(value, int):
        return None
    return value if 0 <= value <= BIGGEST else None


class Ballpark(Game):
    """One Ballpark game, from joining to the winner."""

    info = BALLPARK

    def __init__(self, setup: BallparkSetup, *, rng: random.Random | None = None) -> None:
        """Get ready to host; nothing is loaded until the game plays."""
        self._setup = setup
        self._rng = rng or random.Random()
        self._sounds = Sounds(setup.data_dir / "big-brain" / "sounds")
        self._clips = Clips(setup.folder / "voice", self._rng, lines=HOST_LINES)
        self._speech = Speech(setup.folder / "speech", setup.elevenlabs_key)
        self._context: GameContext | None = None
        self._phones: Phones | None = None
        self._phase = "lobby"
        self._started = False
        self._questions: list[Question] = []
        self._question: Question | None = None
        self._number = 0
        self._surprise_at = 0  # the surprise question's number
        self._surprise: str = ""  # its kind: "nail" or "double"
        self._this_surprise = ""  # the kind when this question is the surprise
        self._scores: dict[str, int] = {}
        self._used: dict[str, set[str]] = {}  # team -> powers spent
        self._entries: dict[str, Entry] = {}  # team -> this question's entry
        self._boards: dict[str, Board] = {}  # team -> its board, after the reveal
        self._revealed = False  # the answer is out
        self._message: str | None = None
        self._deadline: float | None = None  # in playing seconds
        self._event = asyncio.Event()
        self._led: str | None = None
        self._match_point: set[str] = set()  # teams Richie has called one step away
        self._ran: str | None = None
        self._bravest: tuple[str, int, int] | None = None  # the game's smallest right range: team, low, high
        self._winners: list[str] = []

    # What phones see ------------------------------------------------------------------------------

    @property
    def _language(self) -> str:
        return self._context.language if self._context is not None else "es"

    def _line(self, key: str, **values: object) -> str:
        return _LINES[key][self._language].format(**values)

    def _name(self, team: str) -> str:
        return team_name(team, self._language)

    def _teams_playing(self) -> list[str]:
        phones = self._phones
        return [team for team in TEAMS if phones is not None and phones.team_players(team)]

    def _scoreboard(self) -> list[tuple[str, int]]:
        return [(self._name(team), self._scores.get(team, 0)) for team in self._teams_playing()]

    def _left(self, team: str) -> list[str]:
        """Return the powers ``team`` may still use (swap only with three teams or more)."""
        teams = len(self._teams_playing())
        used = self._used.get(team, set())
        return [p for p in POWERS if p not in used and not (p == "copy" and teams < 2) and not (p == "swap" and teams < 3)]  # fmt: skip

    def snapshot(self, player: Player | None) -> dict[str, Any]:
        """Return the game as phone ``player`` should see it (None: a phone that has not joined)."""
        context, phones, asked = self._context, self._phones, self._question
        language = self._language
        paused = context is not None and not context.running.is_set() and self._phase != "over"
        playing = self._teams_playing()
        teams = [
            {
                "key": team,
                "name": self._name(team),
                "score": self._scores.get(team, 0),
                "players": [p.name for p in phones.team_players(team)] if phones else [],
                "sent": team in self._entries,
                "powers": self._left(team) if team in playing else [],
            }
            for team in (TEAMS if self._phase == "lobby" else playing)
        ]
        can: list[str] = []
        if player is None:
            if self._phase == "lobby":
                can.append("join")
        elif paused:
            can.append("resume")
        elif self._phase == "lobby":
            can.append("start")
        elif self._phase == "answer":
            can.append("range")
        deadline = None
        if self._deadline is not None and context is not None and not paused:
            deadline = int((time.time() + max(0.0, self._deadline - context.played_s)) * 1000)
        mine = self._entries.get(player.team) if player is not None else None
        draft = None
        if mine is not None:
            draft = {
                "low": mine.low,
                "high": mine.high,
                "copy": mine.copy,
                "swap": list(mine.swap) if mine.swap else None,
                "double": mine.double,
                "by": mine.by,
            }
        boards = [
            {
                "team": board.team,
                "name": self._name(board.team),
                "low": board.low,
                "high": board.high,
                "copied": board.copied,
                "swapped": board.swapped,
                "double": board.double,
                "right": board.right if self._revealed else None,
                "best": board.best if self._revealed else None,
                "zeroed": board.zeroed if self._revealed else None,
                "points": board.points if self._revealed else None,
            }
            for board in self._boards.values()
        ]
        return {
            "phase": "paused" if paused else self._phase,
            "language": language,
            "title": "Ballpark",
            "target": TARGET,
            "you": {"player": player.id, "name": player.name, "team": player.team} if player else None,
            "teams": teams,
            "round": {"number": self._number, "surprise": self._this_surprise or None} if asked else None,
            "question": asked.text if asked else None,
            "unit": asked.unit if asked else None,
            "draft": draft,
            "boards": boards,
            "answer": (
                {"value": asked.answer, "said": shown(asked.said), "fact": asked.fact}
                if asked and self._revealed
                else None
            ),
            "winners": [self._name(team) for team in self._winners] if self._phase == "over" else [],
            "deadline": deadline,
            "can": can,
            "message": self._message,
        }

    def _changed(self) -> None:
        if self._phones is not None:
            self._phones.changed()

    # What phones send -----------------------------------------------------------------------------

    def on_message(self, player: Player, message: dict[str, Any]) -> None:
        """Take a phone's message (the server already checked the player)."""
        context = self._context
        if context is None:
            return
        context.played()
        kind = message.get("type")
        if kind == "resume" and not context.running.is_set():
            context.resume()
        elif kind == "joined":
            self._scores.setdefault(player.team, 0)
        elif kind == "start" and self._phase == "lobby":
            self._started = True
            self._event.set()
        elif kind == "range" and self._phase == "answer":
            entry = self._entry(player, message)
            if entry is not None:
                self._entries[player.team] = entry
                self._event.set()
        self._changed()

    def _entry(self, player: Player, message: dict[str, Any]) -> Entry | None:
        """Return the team's entry from a phone's "range" message, or None when it is not a valid one."""
        team, playing, left = player.team, self._teams_playing(), self._left(player.team)
        copy = message.get("copy")
        swap = message.get("swap")
        entry = Entry(double=bool(message.get("double")) and "double" in left, by=player.name)
        if copy:
            if "copy" not in left or copy == team or copy not in playing:
                return None
            entry.copy = str(copy)
        else:
            low, high = to_whole(message.get("low")), to_whole(message.get("high"))
            if low is None or high is None:
                return None
            entry.low, entry.high = min(low, high), max(low, high)
        if swap:
            pair = [str(other) for other in swap] if isinstance(swap, list) else []
            ok = len(set(pair)) == 2 and team not in pair and set(pair) <= set(playing) and "swap" in left
            if not ok:
                return None
            entry.swap = (pair[0], pair[1])
        return entry

    def heard(self, context: GameContext, text: str) -> bool:
        """In the lobby, "¡empezamos!" heard in the room starts the game."""
        if self._phase == "lobby" and words(text) in START_WORDS and self._phones and self._phones.players:
            self._started = True
            self._event.set()
            return True
        return False

    def paused(self, context: GameContext) -> None:
        """Show the pause on the phones (the host shows it on the TRMNL)."""
        self._changed()

    # Speaking and waiting -------------------------------------------------------------------------

    async def _say(self, text: str) -> None:
        if self._context is not None:
            await self._context.say(text)

    def _live(self, key: str, **values: object) -> Live:
        return Live(self._line(key, **values))

    def _prepare(self, *texts: str) -> None:
        """Start making live lines now, so they are ready when Richie says them."""
        for text in texts:
            self._speech.prepare(text, self._language)

    async def _piece(self, part: str | Live) -> tuple[str, Audio | None]:
        """Return a part's shown text and its host-voice audio (None: say the text in the everyday voice)."""
        language = self._language
        if isinstance(part, Live):
            return shown(part.text), await self._speech.say(part.text, language)
        line = self._clips.line(part, language)
        if line is not None:
            return line
        takes = HOST_LINES.get(part, {}).get(language, ())
        if not takes:
            return "", None
        take = self._rng.choice(takes)  # not recorded yet: the take is said live, its tags and all
        return shown(take), await self._speech.say(take, language)

    async def _host(self, *parts: str | Live, sounds: tuple[str, ...] = ()) -> None:
        """Say ``parts`` (moments of ballpark_lines, or live words) after ``sounds``, in order, as few pieces as can be."""
        context = self._context
        if context is None:
            return
        pieces: list[tuple[str, Audio | None]] = [
            ("", s) for name in sounds if (s := self._sounds.get(name)) is not None
        ]
        pieces += await asyncio.gather(*(self._piece(part) for part in parts))
        say_audio = context.services.say_audio
        run: list[tuple[str, Audio]] = []

        async def flush() -> None:
            if run and say_audio is not None:
                texts = [text for text, _ in run if text]
                await context.playing()
                await say_audio(RATE, join(*(samples for _, samples in run)), " ".join(texts), context.language, None)
            run.clear()

        for text, samples in pieces:
            if samples is not None and say_audio is not None:
                run.append((text, samples))
            elif text:
                await flush()
                await context.say(text)
        await flush()

    async def _wait(self, seconds: float) -> bool:
        """Wait for the next phone event, up to ``seconds`` of playing time; True when one came."""
        context = self._context
        assert context is not None
        self._event.clear()
        self._deadline = context.played_s + seconds
        self._changed()
        try:
            while not self._event.is_set():
                await context.playing()
                if context.played_s >= self._deadline:
                    return False
                try:
                    await asyncio.wait_for(self._event.wait(), 0.2)
                except asyncio.TimeoutError:
                    pass
            return True
        finally:
            self._deadline = None

    async def _watch_pause(self) -> None:
        context = self._context
        assert context is not None
        running = context.running.is_set()
        while True:
            await asyncio.sleep(0.5)
            if context.running.is_set() != running:
                running = context.running.is_set()
                self._changed()

    # The game -------------------------------------------------------------------------------------

    async def play(self, context: GameContext) -> None:
        """Host one game."""
        self._context = context
        setup = self._setup
        try:
            bank = await asyncio.to_thread(load_questions, setup.bank)
        except (OSError, ValueError):
            logger.warning("Ballpark: no question bank at %s", setup.bank)
            await self._say(self._line("no_bank"))
            return
        self._phones = Phones(
            setup.port,
            self.snapshot,
            self.on_message,
            lambda: self._phase == "lobby",
            public_url=setup.public_url,
            page=PAGE,
        )
        await self._phones.start()
        context.join_url = self._phones.url()
        watcher = asyncio.get_running_loop().create_task(self._watch_pause(), name="ballpark-pause")
        history = setup.folder / "history.json"
        try:
            if not await self._lobby():
                return
            self._questions = pick_questions(bank, context.language, recent=read_history(history), rng=self._rng)
            if not self._questions:
                logger.warning("Ballpark: no question fits (%s)", context.language)
                await self._say(self._line("no_bank"))
                return
            self._surprise_at = self._rng.randint(SURPRISE_FIRST, SURPRISE_LAST)
            self._surprise = self._rng.choice(SURPRISES)
            self._prepare(self._line("question", number=1), self._questions[0].text)
            for index, asked in enumerate(self._questions):
                if index + 1 < len(self._questions):  # the next one is made while this one plays
                    self._prepare(self._line("question", number=index + 2), self._questions[index + 1].text)
                await self._play_question(index, asked)
                if self._winners:
                    break
                await self._standings()
            await self._ending()
            await asyncio.sleep(AFTER_S)
        finally:
            watcher.cancel()
            played = [asked.id for asked in self._questions[: self._number]]
            if played:
                await asyncio.to_thread(remember, history, played)
            await self._phones.stop()

    def _show(self, png: bytes) -> None:
        if self._context is not None:
            self._context.show(png)

    def _show_join(self) -> None:
        phones = self._phones
        if phones is None:
            return
        teams = [(self._name(team), [p.name for p in phones.team_players(team)]) for team in TEAMS]
        self._show(render_join(phones.url(), phones.code, teams, self._language, title="BALLPARK"))

    def _label(self) -> str:
        return self._line("label", number=self._number)

    def _show_question(self) -> None:
        asked = self._question
        assert asked is not None
        sent = [(self._name(team), team in self._entries) for team in self._teams_playing()]
        self._show(
            render_question(
                asked.text,
                asked.unit,
                self._label(),
                sent,
                self._scoreboard(),
                self._language,
                surprise=self._this_surprise or None,
            )
        )

    def _show_reveal(self, *, before_powers: bool = False) -> None:
        asked = self._question
        assert asked is not None
        rows: list[Row] = []
        for team in self._teams_playing():
            entry = self._entries.get(team)
            if before_powers:
                low, high = (entry.low, entry.high) if entry else (None, None)
                note = (
                    self._line("note_copy", name=self._name(entry.copy))
                    if entry and entry.copy
                    else " ".join(self._power_names(entry))
                )
                rows.append(Row(self._name(team), low, high, note, None, None))
                continue
            board = self._boards[team]
            notes = []
            if board.copied:
                notes.append(self._line("note_copy", name=self._name(board.copied)))
            if board.swapped:
                notes.append(self._line("note_swap", name=self._name(board.swapped)))
            if board.double:
                notes.append("×2")
            right = board.right if self._revealed else None
            points = board.points if self._revealed else None
            rows.append(Row(self._name(team), board.low, board.high, " ".join(notes), points, right))
        self._show(
            render_reveal(
                asked.text,
                asked.unit,
                rows,
                asked.answer if self._revealed else None,
                self._scoreboard(),
                self._label(),
                self._language,
            )
        )

    def _power_names(self, entry: Entry | None) -> list[str]:
        return [_POWER_NAMES[power][self._language] for power in entry.powers()] if entry else []

    async def _lobby(self) -> bool:
        """Show the join screen until someone starts the game."""
        context = self._context
        assert context is not None and self._phones is not None
        self._phase = "lobby"
        self._show_join()
        self._changed()
        await self._host("welcome", sounds=("intro",))
        shown_players = -1
        while not (self._started and self._phones.players):
            self._started = False
            came = await self._wait(LOBBY_REMINDER_S)
            if len(self._phones.players) != shown_players:
                shown_players = len(self._phones.players)
                self._show_join()
            if not came and not self._started:
                await self._host("waiting")
        for team in self._teams_playing():
            self._scores.setdefault(team, 0)
        logger.info("Ballpark started: %d players, %d teams", len(self._phones.players), len(self._teams_playing()))
        self._phase = "intro"
        self._changed()
        teams = len(self._teams_playing())
        await self._host(
            "begin", "powers_rules" if teams > 2 else "powers_rules_two" if teams == 2 else "double_rules"
        )
        return True

    async def _play_question(self, index: int, asked: Question) -> None:
        context = self._context
        assert context is not None
        self._question, self._number = asked, index + 1
        self._entries, self._boards, self._revealed, self._message = {}, {}, False, None
        self._this_surprise = self._surprise if self._number == self._surprise_at else ""
        self._phase = "intro"
        self._prepare(self._line("answer", said=_capital(asked.said)), *([asked.fact] if asked.fact else []))
        self._show_question()
        self._changed()
        if self._this_surprise:
            await self._host("surprise", f"surprise_{self._this_surprise}", sounds=("hidden",))
        await self._host(self._live("question", number=self._number), "asked", Live(asked.text), sounds=("board",))
        await self._answering()
        await self._reveal(asked)

    async def _answering(self) -> None:
        """Teams send their ranges for up to ANSWER_S, sooner once every team has sent."""
        context = self._context
        assert context is not None
        self._phase = "answer"
        self._changed()
        await self._host("type_first" if self._number == 1 else "type_it")
        self._deadline = context.played_s + ANSWER_S
        self._changed()
        hurried, sent = False, -1
        teams = self._teams_playing()
        while context.played_s < self._deadline and not (teams and set(teams) <= set(self._entries)):
            await context.playing()
            if len(self._entries) != sent:
                sent = len(self._entries)
                self._show_question()
            if not hurried and self._deadline - context.played_s <= HURRY_S:
                hurried = True
                await self._host("hurry")
                continue
            await asyncio.sleep(0.2)
        everyone = bool(teams) and set(teams) <= set(self._entries)
        self._deadline = None
        self._phase = "reveal"
        self._changed()
        await self._host("all_sent" if everyone else "time_up", sounds=("time_up",))

    async def _reveal(self, asked: Question) -> None:
        """Show the ranges, play the powers, roast the widest, then the answer, the fact and the points."""
        context = self._context
        assert context is not None
        teams = self._teams_playing()
        entries = {team: entry for team, entry in self._entries.items() if team in teams}
        for team, entry in entries.items():
            self._used.setdefault(team, set()).update(entry.powers())
        if not entries:
            self._boards = {team: Board(team, None, None) for team in teams}
            self._show_reveal()
            await self._host("nobody_sent")
        else:
            self._boards, played = resolve(entries, teams)
            if played:
                self._show_reveal(before_powers=True)
                await self._host("powers_used", sounds=("steal",))
                for power in played:
                    await self._host(self._played_line(power))
            self._show_reveal()
            self._changed()
            await self._roast()
        self._revealed = True
        score(
            self._boards, asked.answer, nail=self._this_surprise == "nail", double_all=self._this_surprise == "double"
        )
        await self._host("suspense", sounds=("drumroll",))
        for board in self._boards.values():
            self._scores[board.team] = self._scores.get(board.team, 0) + board.points
        self._message = (
            " · ".join(f"{self._name(b.team)} +{b.points}" for b in self._boards.values() if b.points) or None
        )
        self._show_reveal()
        self._changed()
        await self._host(self._live("answer", said=_capital(asked.said)), *([Live(asked.fact)] if asked.fact else []))
        await self._results(asked)
        self._phase = "revealed"
        self._changed()

    def _played_line(self, power: Played) -> Live:
        name = self._name(power.team)
        others = [self._name(team) for team in power.others]
        if power.power == "copy" and power.loop:
            return self._live("copied_loop", names=_joined([name, *others], self._language))
        if power.power == "copy":
            return self._live("copied_none" if power.empty else "copied", name=name, other=others[0])
        if power.power == "swap":
            return self._live("swapped", name=name, first=others[0], second=others[1])
        return self._live("doubled", name=name)

    def _range_line(self, board: Board) -> Live:
        assert board.low is not None and board.high is not None
        name, language = self._name(board.team), self._language
        if board.low == board.high:
            return self._live("exact", name=name, value=number(board.low, language))
        return self._live("range", name=name, low=number(board.low, language), high=number(board.high, language))

    async def _roast(self) -> None:
        """Before the answer: the widest range is teased, and the narrowest is called brave."""
        ranged = [board for board in self._boards.values() if board.size is not None]
        if not ranged:
            return
        if len(ranged) == 1:
            await self._host(self._range_line(ranged[0]), "alone")
            return
        widest = max(ranged, key=lambda board: board.size or 0)
        narrowest = min(ranged, key=lambda board: board.size or 0)
        if widest.size == narrowest.size:
            await self._host("same_size")
            return
        huge = (widest.size or 0) > 100 * max(narrowest.size or 0, 1)
        await self._host(self._range_line(widest), "widest_huge" if huge else "widest")
        await self._host(self._range_line(narrowest), "exact_bet" if narrowest.size == 0 else "narrowest")

    async def _results(self, asked: Question) -> None:
        """Say what the answer did to the ranges: the best, a bullseye, near misses, powers that paid or not."""
        context = self._context
        assert context is not None
        boards = list(self._boards.values())
        right = [board for board in boards if board.right]
        best = [board for board in right if board.best and not board.zeroed]
        bullseye = [board for board in right if board.size == 0]
        if bullseye:
            context.services.emotion("excited")
            names = _joined([self._name(board.team) for board in bullseye], self._language)
            await self._host(self._live("names", names=names), "bullseye", sounds=("applause",))
        elif len(best) == 1:
            context.services.emotion("happy")
            await self._host(f"best_{best[0].team}", sounds=("correct",))
        elif len(best) > 1:
            await self._host("best_tie", sounds=("correct",))
        elif any(board.size is not None for board in boards):
            context.services.emotion("surprised")
            await self._host("nobody_right", sounds=("wrong",))
        zeroed = [board for board in right if board.zeroed]
        if zeroed:
            await self._host("everyone_right", self._live("team", name=self._name(zeroed[0].team)), "widest_zero")
        # One remark more at most, so the reveal keeps moving: a power that paid off or flopped, else a near miss.
        remarks: list[tuple[str, str]] = []
        for board in boards:
            if board.copied and board.size is not None:
                remarks.append((board.team, "copy_paid" if board.points else "copy_flop"))
            if board.double:
                remarks.append((board.team, "double_paid" if board.points else "double_flop"))
        near = [board for board in boards if not board.right and _near(board, asked.answer)]
        far = [board for board in boards if not board.right and _far(board, asked.answer)]
        if near:
            remarks.append((near[0].team, "near_miss"))
        elif far and right:
            remarks.append((far[0].team, "way_off"))
        if remarks:
            team, moment = remarks[0]
            await self._host(self._live("team", name=self._name(team)), moment)
        if self._this_surprise == "double" and right:
            await self._host("double_all_paid")
        for board in best:
            if board.low is None or board.high is None:
                continue
            if self._bravest is None or board.high - board.low < self._bravest[2] - self._bravest[1]:
                self._bravest = (board.team, board.low, board.high)
        winners = [team for team, points in self._scores.items() if points >= TARGET and team in self._boards]
        if winners:
            top = max(self._scores[team] for team in winners)
            winners = [team for team in winners if self._scores[team] == top]
            sizes = {team: self._boards[team].size for team in winners}
            smallest = min((size for size in sizes.values() if size is not None), default=None)
            if len(winners) > 1 and smallest is not None:
                winners = [team for team in winners if sizes[team] == smallest]
            self._winners = winners

    def _leader(self) -> tuple[str | None, int]:
        """Return the team alone in first place (None for a tie) and its lead over the next team."""
        ranked = sorted((self._scores.get(team, 0) for team in self._teams_playing()), reverse=True)
        if len(ranked) < 2 or ranked[0] == ranked[1]:
            return None, 0
        best = ranked[0]
        return next(team for team in self._teams_playing() if self._scores.get(team, 0) == best), best - ranked[1]

    async def _standings(self) -> None:
        """Between questions, Richie remarks on the race: one step from winning, a new leader, a runaway."""
        leader, lead = self._leader()
        close = [
            team
            for team in self._teams_playing()
            if MATCH_POINT <= self._scores.get(team, 0) < TARGET and team not in self._match_point
        ]
        if close:
            self._match_point |= set(close)
            names = _joined([self._name(team) for team in close], self._language)
            await self._host(self._live("names", names=names), "match_point")
        elif leader is not None and self._led is not None and leader != self._led:
            await self._host("lead_change")
        elif leader is not None and lead >= RUNAWAY_POINTS and self._ran != leader:
            self._ran = leader
            await self._host("runaway")
        if leader is not None:
            self._led = leader

    async def _ending(self) -> None:
        language = self._language
        if not self._winners:  # out of questions: the most points wins
            best = max(self._scores.values(), default=0)
            self._winners = [team for team in self._teams_playing() if self._scores.get(team, 0) == best]
        self._phase, self._message, self._question = "over", None, None
        self._show(render_winner(self._scoreboard(), language))
        self._changed()
        if self._context is not None:
            self._context.services.emotion("excited")
        if len(self._winners) == 1:
            await self._host("winner", f"winner_{self._winners[0]}", sounds=("drumroll",))
        else:
            names = _joined([self._name(team) for team in self._winners], language)
            await self._host("tie", self._live("names", names=names), sounds=("drumroll",))
        if self._bravest is not None:
            team, low, high = self._bravest
            await self._host(
                self._live("bravest", name=self._name(team), low=number(low, language), high=number(high, language))
            )
        await self._host("goodbye", sounds=("fanfare", "applause"))
        logger.info("Ballpark over after %d questions: %s", self._number, ", ".join(f"{t} {p}" for t, p in self._scores.items()))  # fmt: skip


def _near(board: Board, answer: int) -> bool:
    """Return whether a wrong range missed ``answer`` by a little (a tenth of it, at least 1)."""
    if board.low is None or board.high is None:
        return False
    miss = board.low - answer if answer < board.low else answer - board.high
    return 0 < miss <= max(1, answer * NEAR)


def _far(board: Board, answer: int) -> bool:
    """Return whether a wrong range was off by ten times or more."""
    if board.low is None or board.high is None:
        return False
    if answer < board.low:
        return board.low >= max(answer, 1) * FAR
    return board.high > 0 and answer >= board.high * FAR


def _capital(text: str) -> str:
    """Return ``text`` with its first letter capitalized ("¡Cuarenta y dos dientes!")."""
    return text[:1].upper() + text[1:]


def _joined(names: list[str], language: str) -> str:
    """Return "Rojo, Azul y Verde" (or "and")."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + _LINES["and"][language] + names[-1]
