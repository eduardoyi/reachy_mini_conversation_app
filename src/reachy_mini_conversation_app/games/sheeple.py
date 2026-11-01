"""Sheeple: a survey game Richie hosts for teams playing on their phones (docs/features/sheeple.md).

Players join by scanning the QR code on the TRMNL, as in Big Brain. Each board is a question put to 100 people
in the US, its answers hidden and ranked by how many said them. Flock rounds: every phone types a guess at once,
Richie reveals the answers from the least to the most popular, and each guess scores the people who gave its
answer (for the player's team). Team rounds: the team whose turn it is taps "answer" on a phone and that player
alone says a guess aloud; a hit scores and keeps the turn, a miss passes it, and six misses end the board. Six
boards, flock and team in turn, the last one double, then the winning team. Richie speaks in his own voice.
"""

import time
import random
import asyncio
import logging
from typing import Any, Final
from pathlib import Path
from dataclasses import field, dataclass

from reachy_mini_conversation_app.games.base import UNCLEAR_ANSWER, Game, GameContext
from reachy_mini_conversation_app.games.catalog import SHEEPLE, words
from reachy_mini_conversation_app.quick_commands import QuickReply
from reachy_mini_conversation_app.games.big_brain import START_WORDS, team_name
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Sounds, silence
from reachy_mini_conversation_app.games.big_brain_board import remember, load_bank, read_history
from reachy_mini_conversation_app.games.big_brain_judge import Judge, Ruling, plain, matches
from reachy_mini_conversation_app.games.big_brain_lines import TEAMS
from reachy_mini_conversation_app.games.sheeple_screens import Slot, render_survey
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.big_brain_screens import render_join, render_winner


logger = logging.getLogger(__name__)

PAGE: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "sheeple" / "index.html"
ROUNDS: Final[int] = 6
MISSES: Final[int] = 6
MIN_ANSWERS: Final[int] = 3
FLOCK_S: Final[float] = 30.0
TURN_S: Final[float] = 25.0  # for the team to tap "answer"
ANSWER_START_S: Final[float] = 6.0  # for the player to start speaking; silence is a miss
ANSWER_S: Final[float] = 20.0  # at most, from the prompt to the answer's text
LOBBY_REMINDER_S: Final[float] = 75.0
AFTER_S: Final[float] = 30.0  # the page stays up this long after the game, for the final scores
JEV_AT_ONCE: Final[int] = 6

_LINES: Final[dict[str, dict[str, str]]] = {
    "welcome": {
        "es": "¡Bienvenidos a Sheeple! Aquí no gana el más listo: gana el que piensa como el rebaño. Escaneen el código de la pantalla para jugar.",  # fmt: skip
        "en": "Welcome to Sheeple! The smartest player doesn't win here: whoever thinks like the flock does. Scan the code on the screen to join.",  # fmt: skip
    },
    "waiting": {
        "es": "Cuando estén todos, toquen Empezar en el teléfono.",
        "en": "When everyone's in, tap Start on your phone.",
    },
    "begin": {
        "es": "¡Empezamos! Les preguntamos cosas a cien gringos, y ustedes adivinan qué contestaron. Unas rondas las juegan todos a la vez en el teléfono, y otras por equipos, en voz alta.",  # fmt: skip
        "en": "Let's go! We asked a hundred people some questions, and you guess what they said. Some rounds everyone plays at once on the phone, others go team by team, out loud.",  # fmt: skip
    },
    "no_bank": {"es": "No encuentro las encuestas.", "en": "I can't find the surveys."},
    "flock": {"es": "Ronda {number}: ¡todos responden!", "en": "Round {number}: everybody answers!"},
    "team": {"es": "Ronda {number}: por equipos.", "en": "Round {number}: team by team."},
    "double": {"es": "¡Última ronda, y vale doble!", "en": "Last round, and it counts double!"},
    "type_it": {
        "es": "Escriban en el teléfono lo que creen que dijo la mayoría.",
        "en": "Type on your phone what you think most people said.",
    },
    "time_up": {"es": "¡Tiempo! A ver qué dijo el rebaño.", "en": "Time! Let's see what the flock said."},
    "reveal": {"es": "Número {rank}: {answer}, con {count}.", "en": "Number {rank}: {answer}, with {count}."},
    "said_by": {"es": "¡Lo dijo {names}!", "en": "{names} said it!"},
    "nobody_hit": {"es": "Nadie pensó como el rebaño esta vez.", "en": "Nobody thought like the flock this time."},
    "your_turn": {
        "es": "Equipo {team}, les toca. Toquen Responder en el teléfono y díganmelo.",
        "en": "{team} team, your turn. Tap Answer on your phone and tell me.",
    },
    "listen": {"es": "¿{name}?", "en": "{name}?"},
    "hit": {"es": "¡Está! {answer}, con {count}.", "en": "It's there! {answer}, with {count}."},
    "already": {"es": "Esa ya está en el tablero.", "en": "That one is already on the board."},
    "not_there": {"es": "¡No está!", "en": "Not there!"},
    "miss": {"es": "Van {misses} de seis fallos.", "en": "That's {misses} of six misses."},
    "no_answer": {"es": "No te oí. Cuenta como fallo.", "en": "I didn't hear you. That's a miss."},
    "too_slow": {"es": "Se acabó el tiempo del equipo.", "en": "The team ran out of time."},
    "cleared": {"es": "¡Tablero limpio!", "en": "Board cleared!"},
    "left": {"es": "Faltaban: {answers}.", "en": "Still hidden: {answers}."},
    "winner": {
        "es": "¡Gana el equipo {team}, con {points} puntos!",
        "en": "The {team} team wins, with {points} points!",
    },  # fmt: skip
    "tie": {"es": "¡Empate! Ganan {teams}.", "en": "It's a tie! {teams} win."},
    "sheep": {
        "es": "Y la oveja mayor, la que más pensó como el rebaño: ¡{name}!",
        "en": "And the top sheep, who thought most like the flock: {name}!",
    },
    "goodbye": {"es": "¡Gracias por jugar Sheeple! Bee.", "en": "Thanks for playing Sheeple! Baa."},
}
# Before each question; in Spanish, with some cheek about where the surveys come from.
_ASKED: Final[dict[str, tuple[str, ...]]] = {
    "es": ("Le preguntamos a cien gringos:", "Cien gringos respondieron esto:", "Encuesta a cien gringos:"),
    "en": ("We asked a hundred people:", "A hundred people answered this:", "Survey says, a hundred people:"),
}
_FIXED: Final[tuple[str, ...]] = (
    "welcome", "waiting", "begin", "no_bank", "double", "type_it", "time_up", "nobody_hit", "not_there", "already",
    "no_answer", "too_slow", "cleared", "goodbye",
)  # fmt: skip


@dataclass
class SheepleSetup:
    """Where Sheeple's files are and where phones reach it; main.py fills it from the config."""

    data_dir: Path
    port: int
    public_url: str | None = None  # the phones' https address, as for Big Brain (the same port)

    @property
    def folder(self) -> Path:
        """Return Sheeple's own folder: the bank, the history and the kept rulings."""
        return self.data_dir / "sheeple"

    @property
    def bank(self) -> Path:
        """Return the survey bank."""
        return self.folder / "bank.jsonl"


@dataclass
class Answer:
    """One survey answer: its text in the game's language, how many said it, and every form that counts."""

    text: str
    count: int
    accept: list[str]
    shown: bool = False


@dataclass
class Survey:
    """One board: a question and its answers, the most popular first."""

    id: str
    question: str
    answers: list[Answer] = field(default_factory=list)


def survey(entry: dict[str, Any], language: str) -> Survey | None:
    """Return a bank entry as a board in ``language``, or None when it lacks the text or answers."""
    question = str((entry.get("question") or {}).get(language) or "").strip()
    answers: list[Answer] = []
    for item in entry.get("answers") or []:
        text = str(item.get(language) or "").strip()
        if not text:
            return None
        forms = [str(form) for form in (item.get("accept") or [])]
        forms += [str(item[key]) for key in ("en", "es") if item.get(key)]
        answers.append(Answer(text=text, count=int(item.get("count") or 0), accept=list(dict.fromkeys(forms))))
    if not question or len(answers) < MIN_ANSWERS:
        return None
    answers.sort(key=lambda answer: -answer.count)
    return Survey(id=str(entry["id"]), question=question, answers=answers)


def pick_surveys(
    bank: list[dict[str, Any]],
    language: str,
    *,
    kids: bool = False,
    recent: dict[str, float] | None = None,
    count: int = ROUNDS,
    rng: random.Random | None = None,
) -> list[Survey]:
    """Return ``count`` boards (fewer when the bank is short), new ones first; kids mode leaves adult and US-only out."""
    rng = rng or random.Random()
    recent = recent or {}
    fitting = [
        entry
        for entry in bank
        if entry.get("kind", "board") == "board"
        and (not kids or (entry.get("kids") and not entry.get("adult") and not entry.get("us_only")))
    ]
    rng.shuffle(fitting)
    # Boards played lately come last, the longest ago first, so a short bank still fills a game.
    fitting.sort(key=lambda entry: recent.get(str(entry.get("id")), 0.0))
    surveys: list[Survey] = []
    for entry in fitting:
        board = survey(entry, language)
        if board is not None:
            surveys.append(board)
        if len(surveys) == count:
            break
    return surveys


class Sheeple(Game):
    """One Sheeple game, from joining to the winner."""

    info = SHEEPLE

    def __init__(self, setup: SheepleSetup, *, rng: random.Random | None = None) -> None:
        """Get ready to host; nothing is loaded until the game plays."""
        self._setup = setup
        self._rng = rng or random.Random()
        self._sounds = Sounds(setup.data_dir / "big-brain" / "sounds")
        self._context: GameContext | None = None
        self._judge: Judge | None = None
        self._jev_gate = asyncio.Semaphore(JEV_AT_ONCE)
        self._phones: Phones | None = None
        self._phase = "lobby"
        self._kids = False
        self._started = False
        self._surveys: list[Survey] = []
        self._survey: Survey | None = None
        self._round = 0
        self._kind = ""
        self._scores: dict[str, int] = {}  # team -> points
        self._points: dict[str, int] = {}  # player id -> points from flock rounds
        self._typed: dict[str, str] = {}  # player id -> this flock round's guess
        self._hits: dict[str, int | None] = {}  # player id -> the answer their guess hit
        self._turn: str | None = None
        self._first_turn = 0
        self._speaker: Player | None = None
        self._misses = 0
        self._message: str | None = None
        self._deadline: float | None = None  # in playing seconds
        self._event = asyncio.Event()

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line, in both languages, for the speech cache."""
        return frozenset(_LINES[key][language] for key in _FIXED for language in ("es", "en"))

    # What phones see ------------------------------------------------------------------------------

    @property
    def _language(self) -> str:
        return self._context.language if self._context is not None else "es"

    def _line(self, key: str, **values: object) -> str:
        return _LINES[key][self._language].format(**values)

    def _teams_playing(self) -> list[str]:
        phones = self._phones
        return [team for team in TEAMS if phones is not None and phones.team_players(team)]

    def _scoreboard(self) -> list[tuple[str, int]]:
        return [(team_name(team, self._language), self._scores.get(team, 0)) for team in self._teams_playing()]

    def snapshot(self, player: Player | None) -> dict[str, Any]:
        """Return the game as phone ``player`` should see it (None: a phone that has not joined)."""
        context, phones, board = self._context, self._phones, self._survey
        language = self._language
        paused = context is not None and not context.running.is_set() and self._phase != "over"
        teams = [
            {
                "key": team,
                "name": team_name(team, language),
                "score": self._scores.get(team, 0),
                "players": [p.name for p in phones.team_players(team)] if phones else [],
            }
            for team in (TEAMS if self._phase == "lobby" else self._teams_playing())
        ]
        can: list[str] = []
        if player is None:
            if self._phase == "lobby":
                can.append("join")
        elif paused:
            can.append("resume")
        elif self._phase == "lobby":
            can += ["start", "settings"]
        elif self._phase == "flock":
            can.append("answer")
        elif self._phase == "team" and player.team == self._turn and self._speaker is None:
            can.append("speak")
        deadline = None
        if self._deadline is not None and context is not None and not paused:
            deadline = int((time.time() + max(0.0, self._deadline - context.played_s)) * 1000)
        guess = None
        if player is not None and player.id in self._typed:
            hit = self._hits.get(player.id)
            shown = hit is not None and board is not None and board.answers[hit].shown
            guess = {"text": self._typed[player.id], "rank": hit + 1 if shown and hit is not None else None}
        ranked = sorted(phones.players.values(), key=lambda p: -self._points.get(p.id, 0)) if phones else []
        sheep = [{"name": p.name, "team": p.team, "points": self._points.get(p.id, 0)} for p in ranked]
        return {
            "phase": "paused" if paused else self._phase,
            "language": language,
            "title": "Sheeple",
            "kids": self._kids,
            "you": {"player": player.id, "name": player.name, "team": player.team} if player else None,
            "teams": teams,
            "round": {"number": self._round, "of": len(self._surveys), "kind": self._kind} if board else None,
            "question": board.question if board else None,
            "answers": (
                [
                    {"rank": rank + 1, "text": a.text if a.shown else None, "count": a.count if a.shown else None}
                    for rank, a in enumerate(board.answers)
                ]
                if board
                else []
            ),
            "turn": self._turn if self._kind == "team" else None,
            "speaker": {"name": self._speaker.name, "team": self._speaker.team} if self._speaker else None,
            "misses": self._misses if self._kind == "team" else None,
            "guess": guess,
            "sheep": sheep if self._phase == "over" else [],
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
        elif kind == "kids" and self._phase == "lobby":
            self._kids = bool(message.get("on"))
            self._show_join()
        elif kind == "answer" and self._phase == "flock":
            self._typed[player.id] = " ".join(str(message.get("text") or "").split())[:60]
        elif kind == "speak" and self._phase == "team" and player.team == self._turn and self._speaker is None:
            self._speaker = player
            self._event.set()
        self._changed()

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

    async def _sound(self, name: str) -> None:
        """Play one of Big Brain's sounds when it was made (and recorded audio can play)."""
        context = self._context
        samples = self._sounds.get(name)
        if context is None or samples is None or context.services.say_audio is None:
            return
        await context.playing()
        await context.services.say_audio(RATE, samples, "", context.language, None)

    async def _listen(self, prompt: str) -> str:
        """Say ``prompt`` and return what the named player then says ("" for nothing)."""
        context = self._context
        assert context is not None
        heard: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def answer(said: str) -> QuickReply:
            if not heard.done():
                heard.set_result(said)
            return QuickReply(None, context.language, "game")

        await context.playing()
        say_audio = context.services.say_audio
        if say_audio is not None:
            # A moment of silence opens the listening with its own short wait, as Big Brain does.
            await context.say(prompt)
            await say_audio(RATE, silence(0.1), prompt, context.language, answer, listen_s=ANSWER_START_S)
        else:
            context.services.ask(prompt, context.language, answer)
        try:
            said = await asyncio.wait_for(heard, ANSWER_S)
        except asyncio.TimeoutError:
            return ""
        return "" if said == UNCLEAR_ANSWER else said

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

    # Judging --------------------------------------------------------------------------------------

    async def find(self, board: Survey, said: str, *, spoken: bool) -> int | None:
        """Return which answer ``said`` names (the most popular when several), or None."""
        if not plain(said):
            return None
        for index, answer in enumerate(board.answers):
            if matches(said, answer.accept):
                return index
        judge = self._judge
        if judge is None:
            return None

        async def rule(index: int) -> Ruling:
            async with self._jev_gate:
                answer = board.answers[index]
                return await judge.rule(f"{board.id}#{index}", board.question, answer.accept, said, spoken=spoken)

        rulings = await asyncio.gather(*(rule(index) for index in range(len(board.answers))))
        return next((index for index, ruling in enumerate(rulings) if ruling is Ruling.RIGHT), None)

    # The game -------------------------------------------------------------------------------------

    async def play(self, context: GameContext) -> None:
        """Host one game."""
        self._context = context
        setup = self._setup
        self._judge = Judge(setup.folder / "rulings.json", context.services.jev)
        try:
            bank = await asyncio.to_thread(load_bank, setup.bank)
        except OSError:
            logger.warning("Sheeple: no survey bank at %s", setup.bank)
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
        watcher = asyncio.get_running_loop().create_task(self._watch_pause(), name="sheeple-pause")
        history = setup.folder / "history.json"
        try:
            if not await self._lobby():
                return
            self._surveys = pick_surveys(
                bank, context.language, kids=self._kids, recent=read_history(history), rng=self._rng
            )
            if not self._surveys:
                logger.warning("Sheeple: no board fits (%s, kids=%s)", context.language, self._kids)
                await self._say(self._line("no_bank"))
                return
            for index, board in enumerate(self._surveys):
                await self._play_board(index, board)
            await self._ending()
            await asyncio.sleep(AFTER_S)
        finally:
            watcher.cancel()
            played = [board.id for board in self._surveys[: self._round]]
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
        teams = [(team_name(team, self._language), [p.name for p in phones.team_players(team)]) for team in TEAMS]
        self._show(render_join(phones.url(), phones.code, teams, self._language, kids=self._kids, title="SHEEPLE"))

    def _show_board(self) -> None:
        board = self._survey
        assert board is not None
        slots: list[Slot] = [(a.text, a.count) if a.shown else None for a in board.answers]
        label = self._line(self._kind, number=f"{self._round}/{len(self._surveys)}").rstrip(".!")
        team = self._kind == "team"
        self._show(
            render_survey(
                board.question,
                slots,
                label,
                self._scoreboard(),
                self._language,
                turn=team_name(self._turn, self._language) if team and self._turn else None,
                misses=self._misses if team else None,
            )
        )

    async def _lobby(self) -> bool:
        """Show the join screen until someone starts the game."""
        context = self._context
        assert context is not None and self._phones is not None
        self._phase = "lobby"
        self._show_join()
        self._changed()
        await self._say(self._line("welcome"))
        shown_players = -1
        while not (self._started and self._phones.players):
            self._started = False
            came = await self._wait(LOBBY_REMINDER_S)
            if len(self._phones.players) != shown_players:
                shown_players = len(self._phones.players)
                self._show_join()
            if not came and not self._started:
                await self._say(self._line("waiting"))
        for team in self._teams_playing():
            self._scores.setdefault(team, 0)
        self._first_turn = self._rng.randrange(len(self._teams_playing()))
        logger.info("Sheeple started: %d players, %d teams", len(self._phones.players), len(self._teams_playing()))
        self._phase = "intro"
        self._changed()
        await self._say(self._line("begin"))
        return True

    async def _play_board(self, index: int, board: Survey) -> None:
        self._survey, self._round = board, index + 1
        self._kind = "flock" if index % 2 == 0 else "team"
        self._misses, self._typed, self._hits, self._speaker, self._message = 0, {}, {}, None, None
        double = self._round == len(self._surveys) and self._round > 1
        self._phase = "intro"
        if self._kind == "team":
            teams = self._teams_playing()
            self._turn = teams[self._first_turn % len(teams)]
            self._first_turn += 1
        self._show_board()
        self._changed()
        await self._sound("board")
        intro = self._line(self._kind, number=self._round) + (" " + self._line("double") if double else "")
        asked = self._rng.choice(_ASKED[self._language])
        await self._say(f"{intro} {asked} {board.question}")
        if self._kind == "flock":
            await self._flock(board, 2 if double else 1)
        else:
            await self._team(board, 2 if double else 1)

    async def _flock(self, board: Survey, times: int) -> None:
        """Everybody types a guess; the answers are revealed from the least popular up."""
        assert self._phones is not None
        self._phase = "flock"
        self._changed()
        await self._say(self._line("type_it"))
        context = self._context
        assert context is not None
        self._deadline = context.played_s + FLOCK_S
        self._changed()
        players = self._phones.players
        while context.played_s < self._deadline and not (players and set(players) <= set(self._typed)):
            await context.playing()
            await asyncio.sleep(0.2)
        self._deadline = None
        self._phase = "reveal"
        self._changed()
        await self._say(self._line("time_up"))
        guesses = {plain(text): text for text in self._typed.values() if plain(text)}
        found = await asyncio.gather(*(self.find(board, text, spoken=False) for text in guesses.values()))
        hit_by_guess = dict(zip(guesses, found))
        self._hits = {player_id: hit_by_guess.get(plain(text)) for player_id, text in self._typed.items()}
        anybody = False
        for rank in reversed(range(len(board.answers))):
            answer = board.answers[rank]
            answer.shown = True
            winners = [players[pid] for pid, hit in self._hits.items() if hit == rank and pid in players]
            for player in winners:
                self._points[player.id] = self._points.get(player.id, 0) + answer.count * times
                self._scores[player.team] = self._scores.get(player.team, 0) + answer.count * times
            names = _joined([player.name for player in winners], self._language)
            self._message = f"{answer.text}: {names} +{answer.count * times}" if winners else None
            self._show_board()
            self._changed()
            if rank == 0:
                await self._sound("drumroll")
            line = self._line("reveal", rank=rank + 1, answer=answer.text, count=answer.count)
            if winners:
                anybody = True
                await self._sound("correct")
                line += " " + self._line("said_by", names=names)
            await self._say(line)
        self._phase = "revealed"
        self._changed()
        if not anybody:
            await self._say(self._line("nobody_hit"))

    async def _team(self, board: Survey, times: int) -> None:
        """Teams take turns saying a guess aloud until the board is clear or six misses."""
        context = self._context
        assert context is not None
        announced: str | None = None
        while self._misses < MISSES and any(not answer.shown for answer in board.answers):
            team = self._turn
            assert team is not None
            self._phase, self._speaker = "team", None
            self._show_board()
            if team != announced:
                announced = team
                await self._say(self._line("your_turn", team=team_name(team, self._language)))
            came = await self._wait(TURN_S)
            speaker = self._speaker
            if not came or speaker is None:
                self._message = self._line("too_slow")
                await self._miss(self._line("too_slow"))
                continue
            self._phase = "answering"
            self._changed()
            said = await self._listen(self._line("listen", name=speaker.name))
            context.played()
            index = await self.find(board, said, spoken=True) if said else None
            if index is not None and not board.answers[index].shown:
                answer = board.answers[index]
                answer.shown = True
                self._scores[team] = self._scores.get(team, 0) + answer.count * times
                self._message = f"{speaker.name}: {answer.text} +{answer.count * times}"
                self._phase = "hit"
                self._show_board()
                self._changed()
                context.services.emotion("happy")
                await self._sound("correct")
                await self._say(self._line("hit", answer=answer.text, count=answer.count))
                continue
            if not said:
                self._message = None
                await self._miss(self._line("no_answer"))
            elif index is not None:
                self._message = f"{speaker.name}: «{said}»"
                await self._miss(self._line("already"))
            else:
                self._message = f"{speaker.name}: «{said}» ✗"
                await self._miss(self._line("not_there"))
        self._phase, self._speaker = "reveal", None
        hidden = [answer for answer in board.answers if not answer.shown]
        for answer in hidden:
            answer.shown = True
        self._show_board()
        self._changed()
        if hidden:
            await self._say(self._line("left", answers=_joined([a.text for a in hidden], self._language)))
        else:
            await self._say(self._line("cleared"))

    async def _miss(self, why: str) -> None:
        """Count a miss, say so, and pass the turn to the next team."""
        self._misses += 1
        self._phase = "miss"
        teams = self._teams_playing()
        if self._turn in teams:
            self._turn = teams[(teams.index(self._turn) + 1) % len(teams)]
        self._show_board()
        self._changed()
        await self._sound("wrong")
        await self._say(f"{why} {self._line('miss', misses=self._misses)}".strip())

    async def _ending(self) -> None:
        language = self._language
        self._phase, self._message, self._survey = "over", None, None
        self._show(render_winner(self._scoreboard(), language))
        self._changed()
        scores = {team: self._scores.get(team, 0) for team in self._teams_playing()}
        best = max(scores.values(), default=0)
        winners = [team_name(team, language) for team, points in scores.items() if points == best]
        if self._context is not None:
            self._context.services.emotion("excited")
        await self._sound("drumroll")
        if len(winners) == 1:
            await self._say(self._line("winner", team=winners[0], points=best))
        else:
            await self._say(self._line("tie", teams=_joined(winners, language)))
        phones = self._phones
        if phones is not None and len(phones.players) > 1 and self._points:
            top = max(phones.players.values(), key=lambda p: self._points.get(p.id, 0))
            if self._points.get(top.id, 0) > 0:
                await self._say(self._line("sheep", name=top.name))
        await self._sound("fanfare")
        await self._sound("applause")
        await self._say(self._line("goodbye"))
        logger.info("Sheeple over: %s", ", ".join(f"{team} {points}" for team, points in scores.items()))


def _joined(names: list[str], language: str) -> str:
    """Return "Ana, Luis y Eva" (or "and")."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + (" y " if language == "es" else " and ") + names[-1]
