"""Sheeple: a survey game Richie hosts for teams playing on their phones (docs/features/sheeple.md).

Players join by scanning the QR code on the TRMNL, as in Big Brain. Each board is a question put to 100 people
in the US, its answers hidden and ranked by how many said them. Flock rounds: every phone types a guess at once,
Richie reveals the answers from the least to the most popular, and each guess scores the people who gave its
answer (for the player's team). Team rounds: the team whose turn it is taps "answer" on a phone and that player
alone says a guess aloud; a hit scores and keeps the turn, a miss passes it, and three misses end the board. Six
boards, flock and team in turn, the last one double, then the winning team.

Richie hosts in Big Brain's host voice: fixed moments are the recorded clips of ``sheeple_lines.py`` (a moment
not recorded yet is said live from one of its takes), and questions, answers and names are said live in the same
voice and kept by text. Without an ElevenLabs key, his everyday voice says it all. He riffs on the answers the
way a survey-show host does (2026-11-03: the household found the game flat next to Big Brain's banter).
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
from reachy_mini_conversation_app.games.sheeple_lines import LINES as HOST_LINES
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, Clips, Sounds, Speech, beep, join, silence
from reachy_mini_conversation_app.games.big_brain_board import remember, load_bank, read_history
from reachy_mini_conversation_app.games.big_brain_judge import Judge, Ruling, plain, matches
from reachy_mini_conversation_app.games.big_brain_lines import TEAMS, shown
from reachy_mini_conversation_app.games.sheeple_screens import Slot, render_survey
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.big_brain_screens import render_join, render_winner


logger = logging.getLogger(__name__)

PAGE: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "sheeple" / "index.html"
ROUNDS: Final[int] = 6
# Six made a team board drag on (2026-11-02); sheeple_screens draws one box each.
MISSES: Final[int] = 3
MIN_ANSWERS: Final[int] = 3
FLOCK_S: Final[float] = 30.0
TURN_S: Final[float] = 25.0  # for the team to tap "answer"
ANSWER_START_S: Final[float] = 6.0  # for the player to start speaking; silence is a miss
ANSWER_S: Final[float] = 20.0  # at most, from the prompt to the answer's text
LOBBY_REMINDER_S: Final[float] = 75.0
AFTER_S: Final[float] = 30.0  # the page stays up this long after the game, for the final scores
JEV_AT_ONCE: Final[int] = 6
LOW_COUNT: Final[int] = 5  # an answer this few people gave "barely counts"
ECHO_CHARS: Final[int] = 40  # a missed spoken guess this short is said back ("¿Un dinosaurio?")
# After a board: a new leader that was this far behind is a comeback; a lead this big is a runaway; a gap this
# small between the top two is a close game.
COMEBACK_POINTS: Final[int] = 60
RUNAWAY_POINTS: Final[int] = 150
CLOSE_POINTS: Final[int] = 10

# What Richie says live (names, answers, counts), and what the screens and phones show.
_LINES: Final[dict[str, dict[str, str]]] = {
    "no_bank": {"es": "No encuentro las encuestas.", "en": "I can't find the surveys."},
    "flock": {"es": "Ronda {number}: ¡todos responden!", "en": "Round {number}: everybody answers!"},
    "team": {"es": "Ronda {number}: por equipos.", "en": "Round {number}: team by team."},
    "reveal": {"es": "Número {rank}: {answer}, con {count}.", "en": "Number {rank}: {answer}, with {count}."},
    "said_by": {"es": "¡Lo dijo {names}!", "en": "{names} said it!"},
    "typed": {"es": "{name} puso: {guess}.", "en": "{name} typed: {guess}."},
    "listen": {"es": "¿{name}?", "en": "{name}?"},
    "echo": {"es": "[disbelief] ¿{said}?", "en": "[disbelief] {said}?"},
    "too_slow": {"es": "Se acabó el tiempo del equipo.", "en": "The team ran out of time."},
    "list": {"es": "{answers}.", "en": "{answers}."},
    "name": {"es": "¡{name}!", "en": "{name}!"},
}


@dataclass(frozen=True)
class Live:
    """Words Richie says live in the host voice: a question, an answer, a name. Tags shape them, unshown."""

    text: str


@dataclass
class SheepleSetup:
    """Where Sheeple's files are and where phones reach it; main.py fills it from the config."""

    data_dir: Path
    port: int
    public_url: str | None = None  # the phones' https address, as for Big Brain (the same port)
    elevenlabs_key: str | None = None  # the host voice; without it Richie's everyday voice hosts

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
        self._clips = Clips(setup.folder / "voice", self._rng, lines=HOST_LINES)
        self._speech = Speech(setup.folder / "speech", setup.elevenlabs_key)
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
        self._listening = False  # the speaker heard the beep and may talk now
        self._misses = 0
        self._message: str | None = None
        self._deadline: float | None = None  # in playing seconds
        self._event = asyncio.Event()
        self._led: str | None = None  # the team that led after the last board
        self._behind: dict[str, int] = {}  # team -> the most it trailed the leader by since the lead last changed
        self._ran: str | None = None  # the team Richie last called a runaway
        self._close_said = False  # "close game" was said after the last board

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
            "listening": self._listening,
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
        """Say ``parts`` (moments of sheeple_lines, or live words) after ``sounds``, in order, as few pieces as can be."""
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
            # A beep opens the listening with its own short wait: players spoke over the name before (2026-11-02).
            await self._host(Live(prompt))
            self._listening = True
            self._changed()
            await say_audio(
                RATE, join(silence(0.05), beep()), prompt, context.language, answer, listen_s=ANSWER_START_S
            )
        else:
            self._listening = True
            self._changed()
            context.services.ask(prompt, context.language, answer)
        try:
            said = await asyncio.wait_for(heard, ANSWER_S)
        except asyncio.TimeoutError:
            return ""
        finally:
            self._listening = False
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
        self._kids = context.kids
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
        context.join_url = self._phones.url()
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
            self._prepare(*(board.question for board in self._surveys))
            self._prepare(*(self._line("listen", name=player.name) for player in self._phones.players.values()))
            for index, board in enumerate(self._surveys):
                await self._play_board(index, board)
                if index + 1 < len(self._surveys):
                    await self._standings()
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
        self._first_turn = self._rng.randrange(len(self._teams_playing()))
        logger.info("Sheeple started: %d players, %d teams", len(self._phones.players), len(self._teams_playing()))
        self._phase = "intro"
        self._changed()
        await self._host("begin")
        return True

    def _reveal(self, rank: int, answer: Answer) -> str:
        return self._line("reveal", rank=rank + 1, answer=answer.text, count=answer.count)

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
        self._prepare(*(self._reveal(rank, answer) for rank, answer in enumerate(board.answers)))
        if self._context is not None:  # the speech model leans toward this board's answers, in both languages
            self._context.services.hint_answers(
                [a.text for a in board.answers] + [f for a in board.answers for f in a.accept]
            )
        self._show_board()
        self._changed()
        intro = [f"{self._kind}_round", *(["double"] if double else []), "asked"]
        await self._host(*intro, Live(board.question), sounds=("board",))
        if self._kind == "flock":
            await self._flock(board, 2 if double else 1)
        else:
            await self._team(board, 2 if double else 1)

    async def _flock(self, board: Survey, times: int) -> None:
        """Everybody types a guess; the answers are revealed from the least popular up, with Richie's comments."""
        assert self._phones is not None
        self._phase = "flock"
        self._changed()
        await self._host("type_it")
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
        guesses = {plain(text): text for text in self._typed.values() if plain(text)}
        found, _ = await asyncio.gather(
            asyncio.gather(*(self.find(board, text, spoken=False) for text in guesses.values())),
            self._host("time_up"),
        )
        hit_by_guess = dict(zip(guesses, found))
        self._hits = {
            player_id: hit_by_guess.get(plain(text)) for player_id, text in self._typed.items() if player_id in players
        }
        typers = [pid for pid, text in self._typed.items() if plain(text) and pid in players]
        winners_at = {
            rank: [players[pid] for pid, hit in self._hits.items() if hit == rank]
            for rank in range(len(board.answers))
        }
        language = self._language
        # The survey host's favourite moment, after the reveals: a guess not one of the hundred gave, read out.
        strays = [pid for pid in typers if self._hits.get(pid) is None]
        stray = players[self._rng.choice(strays)] if strays else None
        typed = self._live("typed", name=stray.name, guess=self._typed[stray.id]) if stray else None
        self._prepare(
            *(
                self._line("said_by", names=_joined([p.name for p in winners], language))
                for winners in winners_at.values()
                if winners
            ),
            *([typed.text] if typed else []),
        )
        anybody = False
        for rank in reversed(range(len(board.answers))):
            answer = board.answers[rank]
            winners = winners_at[rank]
            if rank == 0:
                await self._host("top_suspense")
            answer.shown = True
            for player in winners:
                self._points[player.id] = self._points.get(player.id, 0) + answer.count * times
                self._scores[player.team] = self._scores.get(player.team, 0) + answer.count * times
            names = _joined([player.name for player in winners], language)
            self._message = f"{answer.text}: {names} +{answer.count * times}" if winners else None
            self._show_board()
            self._changed()
            sounds = ("drumroll",) if rank == 0 else ()
            if not winners:
                missed_top = rank == 0 and bool(typers)
                await self._host(Live(self._reveal(rank, answer)), *(["top_missed"] if missed_top else []), sounds=sounds)  # fmt: skip
                continue
            anybody = True
            if len(typers) > 1 and len(winners) == len(typers):
                comment = "whole_flock"
            elif rank == 0 and len(winners) == 1 and len(typers) > 1:
                comment = "lone_top"
            elif answer.count <= LOW_COUNT:
                comment = "low_hit"
            else:
                comment = "flock_hit"
            said_by = self._live("said_by", names=names)
            await self._host(Live(self._reveal(rank, answer)), said_by, comment, sounds=(*sounds, "correct"))
        self._phase = "revealed"
        self._changed()
        if not anybody:
            await self._host("nobody_hit")
            return
        if typed is not None:
            await self._host(typed, "black_sheep")

    async def _team(self, board: Survey, times: int) -> None:
        """Teams take turns saying a guess aloud until the board is clear or three misses."""
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
                await self._host(f"turn_{team}")
            # A tap during the turn's line already counts (it was taken for "too slow" before, 2026-11-03).
            came = self._speaker is not None or await self._wait(TURN_S)
            speaker = self._speaker
            if not came or speaker is None:
                self._message = self._line("too_slow")
                await self._miss("too_slow")
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
                reveal = Live(self._reveal(index, answer))
                if index == 0:
                    parts: tuple[str | Live, ...] = ("team_top", reveal)
                elif answer.count <= LOW_COUNT:
                    parts = ("team_hit", reveal, "team_low")
                else:
                    parts = ("team_hit", reveal)
                await self._host(*parts, sounds=("correct",))
                continue
            if not said:
                self._message = None
                await self._miss("no_answer")
            elif index is not None:
                self._message = f"{speaker.name}: «{said}»"
                await self._miss("already")
            else:
                self._message = f"{speaker.name}: «{said}» ✗"
                # Said back first, as a survey host would ("¿Un dinosaurio?"), when it is short enough.
                echo = [self._live("echo", said=said.strip(" .!?¿¡"))] if len(said) <= ECHO_CHARS else []
                await self._miss(*echo, "not_there")
        self._phase, self._speaker = "reveal", None
        hidden = [answer for answer in board.answers if not answer.shown]
        for answer in hidden:
            answer.shown = True
        self._show_board()
        self._changed()
        if hidden:
            await self._host("left", self._live("list", answers=_joined([a.text for a in hidden], self._language)))
        else:
            await self._host("cleared")

    async def _miss(self, *why: str | Live) -> None:
        """Count a miss, say why and how many, and pass the turn to the next team."""
        self._misses += 1
        self._phase = "miss"
        teams = self._teams_playing()
        if self._turn in teams:
            self._turn = teams[(teams.index(self._turn) + 1) % len(teams)]
        self._show_board()
        self._changed()
        await self._host(*why, f"miss_{min(self._misses, MISSES)}", sounds=("wrong",))

    def _leader(self) -> tuple[str | None, int]:
        """Return the team alone in first place (None for a tie) and its lead over the next team."""
        ranked = sorted((self._scores.get(team, 0) for team in self._teams_playing()), reverse=True)
        if len(ranked) < 2 or ranked[0] == ranked[1]:
            return None, 0
        best = ranked[0]
        return next(team for team in self._teams_playing() if self._scores.get(team, 0) == best), best - ranked[1]

    async def _standings(self) -> None:
        """Between boards, Richie remarks on the race: a new leader, a comeback, a runaway, a close game."""
        leader, lead = self._leader()
        moment: str | None = None
        if leader is not None and self._led is not None and leader != self._led:
            moment = "comeback" if self._behind.get(leader, 0) >= COMEBACK_POINTS else "lead_change"
            self._behind, self._ran = {}, None
        elif leader is not None and lead >= RUNAWAY_POINTS and self._ran != leader:
            moment, self._ran = "runaway", leader
        elif (
            self._round > 1 and lead <= CLOSE_POINTS and not self._close_said and max(self._scores.values(), default=0)
        ):
            moment = "close_game"
        self._close_said = moment == "close_game"
        if leader is not None:
            self._led = leader
        top = max((self._scores.get(team, 0) for team in self._teams_playing()), default=0)
        for team in self._teams_playing():
            self._behind[team] = max(self._behind.get(team, 0), top - self._scores.get(team, 0))
        if moment is not None:
            await self._host(moment)

    async def _ending(self) -> None:
        language = self._language
        self._phase, self._message, self._survey = "over", None, None
        self._show(render_winner(self._scoreboard(), language))
        self._changed()
        scores = {team: self._scores.get(team, 0) for team in self._teams_playing()}
        best = max(scores.values(), default=0)
        winners = [team for team, points in scores.items() if points == best]
        if self._context is not None:
            self._context.services.emotion("excited")
        if len(winners) == 1:
            await self._host("winner", f"winner_{winners[0]}", sounds=("drumroll",))
        else:
            await self._host("tie", sounds=("drumroll",))
        phones = self._phones
        if phones is not None and len(phones.players) > 1 and self._points:
            top = max(phones.players.values(), key=lambda p: self._points.get(p.id, 0))
            if self._points.get(top.id, 0) > 0:
                await self._host("top_sheep", self._live("name", name=top.name))
        await self._host("goodbye", sounds=("fanfare", "applause"))
        logger.info("Sheeple over: %s", ", ".join(f"{team} {points}" for team, points in scores.items()))


def _joined(names: list[str], language: str) -> str:
    """Return "Ana, Luis y Eva" (or "and")."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + (" y " if language == "es" else " and ") + names[-1]
