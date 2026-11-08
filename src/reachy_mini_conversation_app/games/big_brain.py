"""Big Brain: a quiz show Richie hosts for teams playing on their phones (docs/plans/party-games.md).

Players join by scanning the QR code on the TRMNL; their phones are buzzers and private inputs, and the TRMNL is
the shared board. Twenty squares (200 to 800 points) in five themed columns: the team in control picks one on a
phone, Richie reads the clue in his host voice, the first buzz wins the right to answer aloud, and only that
player is listened to. Right wins the points and the next pick; wrong loses them (not in kids mode) and the
others may buzz. Four hidden squares: double or nothing, a steal, one everybody answers on the phone, and a bomb
only the picking team may answer (right doubles the points, a miss or silence costs them). Each team holds two
of four once-a-game cards on its phones: Freeze, Shield, Hint and Hijack. A final round with secret bets and
typed answers ends it. The game runs on the shared game host, so pause, quit and "¿lo terminamos?" work as in
every game.
"""

import time
import random
import asyncio
import logging
from typing import Any, Final, Callable
from pathlib import Path
from dataclasses import dataclass

from reachy_mini_conversation_app.games.base import UNCLEAR_ANSWER, Game, GameContext
from reachy_mini_conversation_app.games.catalog import (
    BIG_BRAIN,
    BIG_BRAIN_HP,
    BIG_BRAIN_SW,
    BIG_BRAIN_LOTR,
    BIG_BRAIN_DISNEY,
    GameInfo,
    words,
)
from reachy_mini_conversation_app.quick_commands import QuickReply
from reachy_mini_conversation_app.games.big_brain_audio import (
    RATE,
    Audio,
    Clips,
    Sounds,
    Speech,
    beep,
    join,
    silence,
)
from reachy_mini_conversation_app.games.big_brain_board import (
    POINTS,
    Clue,
    Board,
    BoardError,
    remember,
    load_bank,
    build_board,
    read_history,
)
from reachy_mini_conversation_app.games.big_brain_judge import Judge, Ruling
from reachy_mini_conversation_app.games.big_brain_lines import LINES, TEAMS, shown
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.big_brain_screens import (
    render_clue,
    render_join,
    render_board,
    render_final,
    render_answer,
    render_reveal,
    render_winner,
)


logger = logging.getLogger(__name__)

PICK_S: Final[float] = 40.0
BUZZ_S: Final[float] = 8.0
REBUZZ_S: Final[float] = 6.0
# The buzzing player has this long to start answering aloud; silence is "no answer" at once.
ANSWER_START_S: Final[float] = 6.0
ANSWER_S: Final[float] = 20.0  # at most, from the end of Richie's line to the answer's text
EVERYONE_S: Final[float] = 30.0
BET_S: Final[float] = 30.0
FINAL_S: Final[float] = 30.0
LOBBY_REMINDER_S: Final[float] = 75.0
FINAL_ANSWER_S: Final[float] = 8.0  # the final's answer on the TRMNL before the winner
# The page and its link stay up this long after the game, for the final scores.
AFTER_S: Final[float] = 30.0
STREAK: Final[int] = 3
# Three wrong answers in a row get their own line; a new leader that trailed by COMEBACK_POINTS is a comeback,
# and a lead of RUNAWAY_POINTS over the next team a runaway (2026-11-03: more of the host's banter).
MISS_STREAK: Final[int] = 3
COMEBACK_POINTS: Final[int] = 1500
RUNAWAY_POINTS: Final[int] = 3000
# Each team is dealt HAND of these at the start, each played once from any of its phones (2026-11-08).
CARDS: Final[tuple[str, ...]] = ("freeze", "shield", "hint", "hijack")
HAND: Final[int] = 2
CARD_SOUNDS: Final[dict[str, str]] = {"freeze": "steal", "shield": "hidden", "hint": "pick", "hijack": "steal"}
START_WORDS: Final[frozenset[str]] = frozenset(
    {"empezamos", "empecemos", "empieza", "arranca", "arrancamos", "listo", "listos", "ya estamos", "a jugar",
     "start", "lets start", "start the game", "were ready", "ready", "go"}
)  # fmt: skip


@dataclass(frozen=True)
class Edition:
    """A themed Big Brain: the same show with its own clues, title, opening and closing lines, and intro tune."""

    info: GameInfo
    folder: str  # its bank: trivia-bank/editions/<folder>/bank.jsonl (scripts/edition_bank.py)
    key: str  # its host lines (welcome_<key>, goodbye_<key>) and intro tune (intro_<key>)
    name: dict[str, str]  # under the title on the screens


EDITIONS: Final[dict[str, Edition]] = {
    edition.info.key: edition
    for edition in (
        Edition(BIG_BRAIN_HP, "harry-potter", "hp", {"en": "Hogwarts edition", "es": "Edición Hogwarts"}),
        Edition(
            BIG_BRAIN_LOTR, "lord-of-the-rings", "lotr", {"en": "Middle-earth edition", "es": "Edición Tierra Media"}
        ),
        Edition(BIG_BRAIN_SW, "star-wars", "sw", {"en": "Galactic edition", "es": "Edición Galáctica"}),
        Edition(BIG_BRAIN_DISNEY, "disney", "dis", {"en": "Disney edition", "es": "Edición Disney"}),
    )
}


@dataclass
class BigBrainSetup:
    """Where Big Brain's files are and how it speaks; main.py fills it from the config."""

    data_dir: Path
    elevenlabs_key: str | None
    port: int
    public_url: str | None = None  # the phones' https address, when one reaches ``port`` from the internet
    edition: Edition | None = None  # None: the general quiz

    @property
    def bank(self) -> Path:
        """Return the clue bank this show plays from."""
        folder = self.data_dir / "trivia-bank"
        return folder / "editions" / self.edition.folder / "bank.jsonl" if self.edition else folder / "bank.jsonl"


def answer_hint(answer: str) -> dict[str, str | int]:
    """Return what a Hint card shows of ``answer``: its first letter, how many letters and words, and "P_____ ____"."""
    text = " ".join(answer.split())
    first = next((char.upper() for char in text if char.isalnum()), "")
    blanks = "".join("_" if char.isalnum() else char for char in text)
    at = next((index for index, char in enumerate(text) if char.isalnum()), 0)
    pattern = blanks[:at] + first + blanks[at + 1 :] if first else blanks
    return {
        "first": first,
        "letters": sum(char.isalnum() for char in text),
        "words": len(text.split()),
        "pattern": pattern,
    }


def team_name(team: str, language: str) -> str:
    """Return a team's name in the game's language: "Azul", "Blue"."""
    english, spanish = TEAMS[team]
    return (spanish if language == "es" else english).capitalize()


@dataclass
class _Vote:
    """The room may overrule a wrong ruling until the next square is picked."""

    player: Player
    said: str
    clue: Clue
    restore: int  # the points to give back and award
    voters: set[str]


class BigBrain(Game):
    """One Big Brain show, from joining to the winner."""

    info = BIG_BRAIN

    def __init__(self, setup: BigBrainSetup, *, rng: random.Random | None = None) -> None:
        """Get ready to host; nothing is loaded until the game plays."""
        self._setup = setup
        self.info = setup.edition.info if setup.edition else BIG_BRAIN
        self._rng = rng or random.Random()
        folder = setup.data_dir / "big-brain"
        self._clips = Clips(folder / "voice", self._rng)
        self._sounds = Sounds(folder / "sounds")
        self._speech = Speech(folder / "speech", setup.elevenlabs_key)
        self._history = folder / "history.json"
        self._context: GameContext | None = None
        self._judge: Judge | None = None
        self._phones: Phones | None = None
        self._board: Board | None = None
        self._phase = "lobby"
        self._kids = False
        self._scores: dict[str, int] = {}
        self._streak: dict[str, int] = {}
        self._wrong_row: dict[str, int] = {}  # team -> wrong answers in a row
        self._behind: dict[str, int] = {}  # team -> the most it trailed the leader by since the lead last changed
        self._ran: str | None = None  # the team Richie last called a runaway
        self._control: str | None = None
        self._clue: Clue | None = None
        self._clue_shown = False
        self._answer_shown = False  # the answer is on the TRMNL, so the phones show it too
        self._locked: set[str] = set()
        self._buzz: Player | None = None
        self._listening = False  # the beep played: the player who buzzed may answer now
        self._pick: tuple[int, int] | None = None
        self._typed: dict[str, str] = {}  # player id (everyone) or team (final) -> text
        self._bets: dict[str, int] = {}
        self._reveal: list[dict[str, Any]] = []
        self._vote: _Vote | None = None
        self._overruled = False
        self._counted: Clue | None = None  # the square the room just counted, while it is still being played
        self._cards: dict[str, list[str]] = {}  # team -> the cards still in its hand
        self._frozen: dict[str, Clue | None] = {}  # team -> the clue it cannot buzz on (None: the next one)
        self._shields: dict[str, Clue | None] = {}  # team -> the clue a miss costs it nothing (None: the next)
        self._hinted: set[str] = set()  # teams whose phones show the current clue's hint
        self._calls: list[tuple[str, str, str | None]] = []  # cards played for Richie to announce: card, team, target
        self._opened = False  # the buzzers unlocked on this clue, so a Freeze now waits for the next one
        self._message: str | None = None
        self._deadline: float | None = None  # in playing seconds
        self._event = asyncio.Event()
        self._started = False

    # What phones see ------------------------------------------------------------------------------

    def _teams_playing(self) -> list[str]:
        phones = self._phones
        return [team for team in TEAMS if phones is not None and phones.team_players(team)]

    def _scoreboard(self) -> list[tuple[str, int]]:
        language = self._language
        return [(team_name(team, language), self._scores.get(team, 0)) for team in self._teams_playing()]

    @property
    def _language(self) -> str:
        return self._context.language if self._context is not None else "es"

    def snapshot(self, player: Player | None) -> dict[str, Any]:
        """Return the game as phone ``player`` should see it (None: a phone that has not joined)."""
        context, phones, board = self._context, self._phones, self._board
        language = self._language
        paused = context is not None and not context.running.is_set() and self._phase not in ("over",)
        phase = "paused" if paused else self._phase
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
        else:
            if self._phase == "lobby":
                can += ["start", "settings"]
            elif self._phase == "board" and player.team == self._control and self._board and self._board.left:
                can.append("pick")
            elif self._phase == "buzzing" and player.team not in self._locked and self._buzz is None:
                can.append("buzz")
            elif self._phase in ("everyone", "final_answer"):
                can.append("answer")
            elif self._phase == "final_bet":
                can.append("bet")
        vote = None
        if self._vote is not None:
            needed = self._votes_needed()
            vote = {
                "open": True,
                "for": self._vote.player.name,
                "said": self._vote.said,
                "yes": len(self._vote.voters),
                "needed": needed,
                "voted": player is not None and player.id in self._vote.voters,
            }
            if player is not None and player.id != self._vote.player.id and player.id not in self._vote.voters:
                can.append("vote")
        clue = None
        if self._clue is not None and self._phase not in ("board", "lobby"):
            clue = {
                "column": self._clue.column,
                "points": self._clue.points,
                "text": self._clue.text if self._clue_shown else None,
                "hidden": self._clue.hidden,
                "answer": self._clue.answer if self._answer_shown else None,
            }
        deadline = None
        if self._deadline is not None and context is not None and not paused:
            deadline = int((time.time() + max(0.0, self._deadline - context.played_s)) * 1000)
        max_bet = None
        if player is not None and self._phase == "final_bet":
            max_bet = max(self._scores.get(player.team, 0), POINTS[-1])
        final = None
        if board is not None and self._phase in ("final_bet", "final_clue", "final_answer", "reveal", "over"):
            final = {
                "category": board.final_category,
                "answers": self._reveal,
                "answer": board.final.answer if self._answer_shown and self._clue is board.final else None,
            }
        return {
            "phase": phase,
            "language": language,
            "title": self._title,
            "kids": self._kids,
            "you": {"player": player.id, "name": player.name, "team": player.team} if player else None,
            "teams": teams,
            "board": (
                {
                    "columns": [
                        {"title": title, "cells": [{"points": c.points, "used": c.used} for c in column]}
                        for title, column in zip(board.titles, board.clues)
                    ]
                }
                if board is not None
                else None
            ),
            "control": self._control,
            "clue": clue,
            "buzzed": {"name": self._buzz.name, "team": self._buzz.team} if self._buzz else None,
            "listening": self._listening,
            "deadline": deadline,
            "can": can,
            "max_bet": max_bet,
            "vote": vote,
            "message": self._message,
            "final": final,
            "cards": self._cards.get(player.team, []) if player else [],
            "playable": self._playable(player.team) if player and not paused else [],
            "frozen": [team for team in TEAMS if team in self._frozen],
            "shielded": [team for team in TEAMS if team in self._shields],
            "hint": answer_hint(self._clue.answer) if player and player.team in self._hinted and self._clue else None,
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
            # A false call or a bumped antenna paused the show; any phone can play on.
            context.resume()
        elif kind == "joined":
            self._scores.setdefault(player.team, 0)
            if self._started:
                self._changed()
        elif kind == "start" and self._phase == "lobby":
            self._started = True
            self._event.set()
        elif kind == "kids" and self._phase == "lobby":
            self._kids = bool(message.get("on"))
            self._show_join()
        elif kind == "pick" and self._phase == "board" and player.team == self._control and self._pick is None:
            column, row = int(message.get("column", -1)), int(message.get("row", -1))
            if self._board is not None and self._board.clue(column, row) is not None:
                self._pick = (column, row)
                self._event.set()
        elif kind == "buzz" and self._phase == "buzzing" and self._buzz is None and player.team not in self._locked:
            self._buzz = player
            self._event.set()
        elif kind == "answer" and self._phase in ("everyone", "final_answer"):
            text = " ".join(str(message.get("text") or "").split())[:100]
            self._typed[player.id if self._phase == "everyone" else player.team] = text
        elif kind == "bet" and self._phase == "final_bet":
            limit = max(self._scores.get(player.team, 0), POINTS[-1])
            self._bets[player.team] = max(0, min(int(message.get("points") or 0), limit))
        elif kind == "card":
            target = message.get("team")
            self._play_card(player, str(message.get("card") or ""), target if isinstance(target, str) else None)
        elif kind == "vote" and self._vote is not None and player.id != self._vote.player.id:
            self._vote.voters.add(player.id)
            if len(self._vote.voters) >= self._votes_needed():
                self._overrule()
        self._changed()

    def _costly(self, clue: Clue) -> bool:
        """Whether a miss on ``clue`` costs points (never in kids mode, on a double or when everyone answers)."""
        return not self._kids and clue.hidden not in ("double", "everyone")

    def _playable(self, team: str) -> list[str]:
        """Return the cards ``team`` may play right now."""
        phase, clue, board = self._phase, self._clue, self._board
        others = [other for other in self._teams_playing() if other != team]
        picking = phase == "board" and board is not None and board.left > 0 and self._pick is None
        live = clue is not None and phase in ("reading", "buzzing", "everyone") and not self._answer_shown
        buzzable = live and clue is not None and clue.hidden != "everyone"
        allowed = {
            # Before the buzzers unlock; it then holds for that clue.
            "freeze": (picking or (buzzable and phase == "reading" and not self._opened))
            and any(other not in self._frozen for other in others),
            "shield": team not in self._shields and (picking or (buzzable and team not in self._locked)),
            "hint": live and self._clue_shown and team not in self._hinted,
            "hijack": picking and self._control in others,
        }
        return [card for card in self._cards.get(team, []) if allowed[card]]

    def _play_card(self, player: Player, card: str, target: str | None) -> None:
        """Play one of ``player``'s team's cards, when it may be played now; otherwise nothing happens."""
        team = player.team
        if card not in self._playable(team):
            logger.info("Big Brain: %s cannot play %s now", team, card or "a card")
            return
        clue = self._clue if self._phase != "board" else None
        if card == "freeze":
            if target is None or target == team or target in self._frozen or target not in self._teams_playing():
                logger.info("Big Brain: %s cannot freeze %s", team, target)
                return
            self._frozen[target] = clue
        elif card == "shield":
            self._shields[team] = clue if clue is not None and self._costly(clue) else None
        elif card == "hint":
            self._hinted.add(team)
        else:
            target, self._control = self._control, team
        self._cards[team].remove(card)
        self._calls.append((card, team, target if card in ("freeze", "hijack") else None))
        logger.info("Big Brain: %s played %s", team, card)
        if self._phase == "board":
            self._event.set()  # Richie announces it, and a hijack hands the pick over

    def _votes_needed(self) -> int:
        others = len(self._phones.players) - 1 if self._phones is not None else 0
        return max(1, others // 2 + 1)

    def _overrule(self) -> None:
        vote, self._vote = self._vote, None
        if vote is None:
            return
        team = vote.player.team
        self._scores[team] = self._scores.get(team, 0) + vote.restore
        self._control = team
        if self._judge is not None:
            self._judge.overrule(vote.clue.id, vote.said)
        points = self._points(vote.clue)
        self._message = f"¡{vote.player.name}: vale! +{points}" if self._language == "es" else f"{vote.player.name}: it counts! +{points}"  # fmt: skip
        self._overruled = True
        # During its own square the square ends now; later, Richie says so before the next pick.
        self._counted = vote.clue if vote.clue is self._clue else None
        self._event.set()
        logger.info("Big Brain: the room overruled a ruling")

    def heard(self, context: GameContext, text: str) -> bool:
        """In the lobby, "¡empezamos!" heard in the room starts the show."""
        if self._phase == "lobby" and words(text) in START_WORDS and self._phones and self._phones.players:
            self._started = True
            self._event.set()
            return True
        return False

    # Sound ----------------------------------------------------------------------------------------

    async def _play(self, samples: Audio | None, text: str = "", *, listen: bool = False) -> str | None:
        """Play ``samples`` as Richie's line; with ``listen``, return what the player then says (or None)."""
        context = self._context
        if context is None or samples is None or not len(samples):
            return None
        await context.playing()
        say_audio = context.services.say_audio
        if say_audio is None:
            return None
        if not listen:
            await say_audio(RATE, samples, text, context.language, None)
            return None
        heard: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def answer(said: str) -> QuickReply:
            if not heard.done():
                heard.set_result(said)
            return QuickReply(None, context.language, "game")

        # Players answered over Richie's line (2026-11-02): it plays first, then a beep opens the listening and
        # the player's phone says "answer now".
        await say_audio(RATE, samples, text, context.language, None)
        self._listening = True
        self._changed()
        try:
            await say_audio(RATE, join(silence(0.05), beep()), "", context.language, answer, listen_s=ANSWER_START_S)
            return await asyncio.wait_for(heard, ANSWER_S) or None
        except asyncio.TimeoutError:
            return None
        finally:
            self._listening = False
            self._changed()

    @property
    def _title(self) -> str:
        edition = self._setup.edition
        return f"Big Brain · {edition.name[self._language]}" if edition else "Big Brain"

    def _own(self, moment: str) -> str:
        """Return the edition's own version of ``moment`` ("welcome_hp") when it was recorded."""
        edition = self._setup.edition
        own = f"{moment}_{edition.key}" if edition else moment
        return own if self._clips.has(own, self._language) else moment

    def _own_sound(self, name: str) -> str:
        """Return the edition's own version of sound ``name`` ("intro_hp") when it was made."""
        edition = self._setup.edition
        own = f"{name}_{edition.key}" if edition else name
        return own if self._sounds.get(own) is not None else name

    def _line(self, moment: str) -> tuple[str, Audio] | None:
        return self._clips.line(moment, self._language)

    async def _say(self, *moments: str, sounds: tuple[str, ...] = (), after: Audio | None = None) -> None:
        """Play ``sounds`` then a take of each moment (and ``after``) as one piece."""
        pieces, texts = await self._pieces(sounds, moments)
        if after is not None:
            pieces.append(after)
        if pieces:
            await self._play(join(*pieces), " ".join(texts))

    async def _pieces(self, sounds: tuple[str, ...], moments: tuple[str, ...]) -> tuple[list[Audio], list[str]]:
        pieces: list[Audio] = []
        texts: list[str] = []
        for name in sounds:
            sound = self._sounds.get(name)
            if sound is not None:
                pieces.append(sound)
        for moment in moments:
            line = self._line(moment)
            takes = LINES.get(moment, {}).get(self._language, ())
            if line is None and takes and self._setup.elevenlabs_key:
                # Not recorded yet: a take is said live in the host voice.
                take = self._rng.choice(takes)
                samples = await self._speech.say(take, self._language)
                line = (shown(take), samples) if samples is not None else None
            if line is not None:
                texts.append(line[0])
                pieces.append(line[1])
        return pieces, texts

    async def _speak(self, text: str) -> Audio | None:
        """Return ``text`` in the host voice, or None (then Richie's own voice says it)."""
        samples = await self._speech.say(text, self._language)
        if samples is None and self._context is not None:
            await self._context.say(text)
        return samples

    # Waiting --------------------------------------------------------------------------------------

    async def _wait(self, seconds: float | None) -> bool:
        """Wait for the next phone event, up to ``seconds`` of playing time; True when one came."""
        context = self._context
        assert context is not None
        self._event.clear()
        self._deadline = context.played_s + seconds if seconds is not None else None
        self._changed()
        try:
            while not self._event.is_set():
                await context.playing()
                if self._deadline is not None and context.played_s >= self._deadline:
                    return False
                try:
                    await asyncio.wait_for(self._event.wait(), 0.2)
                except asyncio.TimeoutError:
                    pass
            return True
        finally:
            self._deadline = None

    async def _countdown(
        self, seconds: float, music: str | None = None, *, done: Callable[[], bool] | None = None
    ) -> None:
        """Let ``seconds`` of playing time pass (answers keep arriving), with ``music``; sooner once ``done()``."""
        context = self._context
        assert context is not None
        self._deadline = context.played_s + seconds
        self._changed()
        sound = self._sounds.get(music) if music else None
        if sound is not None:
            await self._play(sound[: int(RATE * seconds)])
        while context.played_s < self._deadline and not (done is not None and done()):
            await context.playing()
            await asyncio.sleep(0.2)
        self._deadline = None

    # The show -------------------------------------------------------------------------------------

    async def play(self, context: GameContext) -> None:
        """Host one show."""
        self._context = context
        self._kids = context.kids
        self._judge = Judge(self._setup.data_dir / "big-brain" / "rulings.json", context.services.jev)
        bank_path = self._setup.bank
        try:
            bank = await asyncio.to_thread(load_bank, bank_path)
        except OSError:
            logger.warning("Big Brain: no clue bank at %s", bank_path)
            await context.say("No encuentro las preguntas." if context.language == "es" else "I can't find the questions.")  # fmt: skip
            return
        self._phones = Phones(
            self._setup.port,
            self.snapshot,
            self.on_message,
            lambda: self._phase == "lobby",
            public_url=self._setup.public_url,
        )
        await self._phones.start()
        context.join_url = self._phones.url()
        watcher = asyncio.get_running_loop().create_task(self._watch_pause(), name="big-brain-pause")
        try:
            if not await self._lobby():
                return
            try:
                self._board = await asyncio.to_thread(
                    build_board, bank, context.language, kids=self._kids, recent=read_history(self._history)
                )
            except BoardError as error:
                logger.warning("Big Brain: no board: %s", error)
                return
            self._prepare_speech()
            context.services.hint_answers(self._hints())
            await self._rounds()
            await self._final()
            await self._ending()
            await asyncio.sleep(AFTER_S)
        finally:
            watcher.cancel()
            if self._board is not None:
                await asyncio.to_thread(remember, self._history, self._board.ids)
            await self._phones.stop()

    def paused(self, context: GameContext) -> None:
        """Show the pause on the phones (the host shows it on the TRMNL)."""
        self._changed()

    async def _watch_pause(self) -> None:
        context = self._context
        assert context is not None
        running = context.running.is_set()
        while True:
            await asyncio.sleep(0.5)
            if context.running.is_set() != running:
                running = context.running.is_set()
                self._changed()

    def _prepare_speech(self) -> None:
        board = self._board
        assert board is not None
        language = self._language
        for column in board.clues:
            for clue in column:
                self._speech.prepare(clue.text, language)
        self._speech.prepare(board.final.text, language)

    def _hints(self) -> list[str]:
        """Return the board's answers (each clue's own first, then the other forms it accepts) for the speech model.

        The whole board, never one clue's answer alone, so a wrong answer is not pulled toward the right one.
        """
        board = self._board
        assert board is not None
        clues = [clue for column in board.clues for clue in column]
        return [clue.answer for clue in clues] + [form for clue in clues for form in clue.accept]

    def _show(self, png: bytes) -> None:
        if self._context is not None:
            self._context.show(png)

    def _show_join(self) -> None:
        phones = self._phones
        if phones is None:
            return
        teams = [(team_name(team, self._language), [p.name for p in phones.team_players(team)]) for team in TEAMS]
        edition = self._setup.edition
        self._show(
            render_join(
                phones.url(),
                phones.code,
                teams,
                self._language,
                kids=self._kids,
                edition=edition.name[self._language] if edition else None,
            )
        )

    async def _lobby(self) -> bool:
        """Show the join screen until someone starts the show; False when it never starts."""
        context = self._context
        assert context is not None and self._phones is not None
        self._phase = "lobby"
        self._show_join()
        self._changed()
        await self._say(self._own("welcome"), sounds=(self._own_sound("intro"),))
        await self._say("join")
        shown_players = -1
        while True:
            if self._started and self._phones.players:
                break
            self._started = False
            came = await self._wait(LOBBY_REMINDER_S)
            if len(self._phones.players) != shown_players:
                shown_players = len(self._phones.players)
                self._show_join()
            if not came and not self._started:
                await self._say("waiting")
        self._control = self._rng.choice(self._teams_playing())
        # Kids mode costs no points, so it has no use for a Shield.
        deck = [card for card in CARDS if not (self._kids and card == "shield")]
        for team in self._teams_playing():
            self._scores.setdefault(team, 0)
            self._cards[team] = sorted(self._rng.sample(deck, HAND), key=CARDS.index)
        logger.info("Big Brain started: %d players, %d teams", len(self._phones.players), len(self._teams_playing()))
        await self._say("begin", "cards_rules_kids" if self._kids else "cards_rules")
        return True

    def _show_board(self) -> None:
        board = self._board
        assert board is not None
        used = [[clue.used for clue in column] for column in board.clues]
        control = team_name(self._control, self._language) if self._control else None
        self._show(render_board(board.titles, used, POINTS, self._scoreboard(), control=control))

    async def _rounds(self) -> None:
        board = self._board
        assert board is not None
        first = True
        leader: str | None = None
        while board.left:
            self._phase, self._clue, self._pick = "board", None, None
            if not self._overruled:
                self._message = None  # the last square's "¡Correcto!" ends with it (2026-11-02)
            self._clue_shown, self._buzz, self._locked = False, None, set()
            self._show_board()
            if first:
                await self._say("board", sounds=("board",))
                first = False
            if self._overruled:
                self._overruled = False
                await self._say("overruled", sounds=("correct",))
                self._message = None
                self._changed()
            await self._picking()
            if self._pick is None:
                free = [
                    (c, r) for c, column in enumerate(board.clues) for r, clue in enumerate(column) if not clue.used
                ]
                self._pick = self._rng.choice(free)
            self._vote = None
            column, row = self._pick
            clue = board.clues[column][row]
            await self._square(clue)
            # Cards held for this clue end with it; a hint not yet teased stays secret.
            self._frozen = {team: on for team, on in self._frozen.items() if on is not clue}
            self._shields = {team: on for team, on in self._shields.items() if on is not clue}
            self._hinted = set()
            self._calls = [call for call in self._calls if call[0] != "hint"]
            await self._call_cards()
            new_leader = self._leader()
            if board.left:
                await self._standings(leader, new_leader)
            leader = new_leader or leader
        self._phase, self._message = "board", None
        self._show_board()
        self._changed()
        await self._say("board_done")

    async def _picking(self) -> None:
        """Ask the team in control to pick, until it does or PICK_S passes; a hijack asks the new team afresh."""
        context = self._context
        assert context is not None
        asked: str | None = None
        end = 0.0
        # Cards played or a pick made while Richie talks are seen on the next turn of the loop.
        while self._pick is None:
            if self._calls:
                await self._call_cards()
            elif self._control != asked:
                asked = self._control
                # The team is named, so the room knows whose phone the board is on.
                team_pick = f"pick_{asked}"
                await self._say(team_pick if self._clips.has(team_pick, self._language) else "pick")
                end = context.played_s + PICK_S
            elif not await self._wait(end - context.played_s):
                return

    async def _call_cards(self) -> None:
        """Announce the cards played since the last call: the team, then the card (never a Hint's content)."""
        while self._calls:
            card, team, target = self._calls.pop(0)
            await self._say(f"buzz_{team}", f"{card}_{target}" if target else card, sounds=(CARD_SOUNDS[card],))

    def _either(self, moment: str, fallback: str) -> str:
        """Return ``moment`` when it was recorded, else ``fallback`` (new moments play once their clips are made)."""
        return moment if self._clips.has(moment, self._language) else fallback

    async def _standings(self, leader: str | None, new_leader: str | None) -> None:
        """After a square, remark on the race: a new leader (a comeback when it trailed far), or a runaway."""
        scores = sorted((self._scores.get(team, 0) for team in self._teams_playing()), reverse=True)
        lead = scores[0] - scores[1] if len(scores) > 1 else 0
        moment: str | None = None
        if leader is not None and new_leader is not None and new_leader != leader:
            far = self._behind.get(new_leader, 0) >= COMEBACK_POINTS
            moment = self._either("comeback", "lead_change") if far else "lead_change"
            self._behind, self._ran = {}, None
        elif new_leader is not None and lead >= RUNAWAY_POINTS and self._ran != new_leader:
            moment, self._ran = "runaway", new_leader
        top = scores[0] if scores else 0
        for team in self._teams_playing():
            self._behind[team] = max(self._behind.get(team, 0), top - self._scores.get(team, 0))
        if moment is not None:
            await self._say(moment)

    def _leader(self) -> str | None:
        scores = {team: self._scores.get(team, 0) for team in self._teams_playing()}
        if not scores:
            return None
        best = max(scores.values())
        leaders = [team for team, points in scores.items() if points == best]
        return leaders[0] if len(leaders) == 1 else None

    async def _square(self, clue: Clue) -> None:
        """Play one square: the clue, the buzzes, the answers, and what Richie says about them."""
        language = self._language
        clue.used = True
        picker = self._control
        self._clue, self._phase, self._message, self._answer_shown = clue, "reading", None, False
        self._opened = False
        # Cards played for "the next clue" hold for this one (a Shield waits for a clue that can cost points).
        self._frozen = {team: clue if on is None and clue.hidden != "everyone" else on for team, on in self._frozen.items()}  # fmt: skip
        self._shields = {team: clue if on is None and self._costly(clue) else on for team, on in self._shields.items()}  # fmt: skip
        self._changed()
        if clue.hidden == "bomb":
            await self._say("bomb_kids" if self._kids else "bomb", sounds=("pick", "drumroll"))
        elif clue.hidden:
            await self._say(clue.hidden, sounds=("pick", "steal" if clue.hidden == "steal" else "hidden"))
        else:
            sound = self._sounds.get("pick")
            if sound is not None:
                await self._play(sound)
        self._show(render_clue(clue.column, clue.points, clue.text, language, hidden=clue.hidden))
        reading = await self._speech.say(clue.text, language)
        self._clue_shown = True
        self._changed()
        if reading is not None:
            await self._play(reading, clue.text)
        else:
            assert self._context is not None
            await self._context.say(clue.text)
        if clue.hidden == "everyone":
            await self._everyone(clue)
            return
        await self._call_cards()
        self._opened = True
        self._locked |= {team for team, on in self._frozen.items() if on is clue}
        if clue.hidden == "bomb":  # only the team that picked it may answer
            self._locked |= {team for team in self._teams_playing() if team != picker}
        asked_specific = missed = False
        seconds = BUZZ_S
        while True:
            if self._counted is clue:  # counted while Richie was still talking
                await self._counted_now(clue)
                return
            if all(team in self._locked for team in self._teams_playing()):
                break  # everyone is frozen or locked out
            self._phase, self._buzz = "buzzing", None
            came = await self._wait(seconds)
            if self._counted is clue:
                await self._counted_now(clue)
                return
            if not came or self._buzz is None:
                break
            player = self._buzz
            self._phase = "answering"
            self._changed()
            pieces, texts = await self._pieces((f"buzz_{player.team}",), (f"buzz_{player.team}", "answer"))
            # With no clip made, a moment of silence still opens the listening.
            spoken = join(*pieces) if pieces else silence(0.1)
            said = await self._play(spoken, " ".join(texts), listen=True) or ""
            if said == UNCLEAR_ANSWER:
                # Someone spoke but the speech model wrote nothing: one more try, then it is no answer.
                again = self._line("say_again")
                said = await self._play(again[1] if again else silence(0.1), again[0] if again else "", listen=True) or ""  # fmt: skip
                said = "" if said == UNCLEAR_ANSWER else said
            assert self._judge is not None
            ruling = await self._judge.rule(clue.id, clue.text, clue.accept, said)
            if ruling is Ruling.SPECIFIC and not asked_specific:
                asked_specific = True
                more = self._line("more_specific")
                again = more[1] if more else silence(0.1)
                more_said = await self._play(again, more[0] if more else "", listen=True)
                said = more_said if more_said and more_said != UNCLEAR_ANSWER else said
                ruling = await self._judge.rule(clue.id, clue.text, clue.accept, said)
            if ruling is Ruling.RIGHT:
                await self._right(player, clue)
                return
            await self._wrong(player, clue, said)
            missed = True
            if self._counted is clue:
                await self._counted_now(clue)
                return
            seconds = REBUZZ_S
            if all(team in self._locked for team in self._teams_playing()):
                break
            await self._call_cards()
            await self._say("anyone_else")
        if clue.hidden == "bomb" and not missed and picker is not None:
            # Silence on a bomb blows it up too.
            _, saved = self._lose(picker, clue)
            self._phase = "reading"
            self._changed()
            await self._say("bomb_boom", *(("shield_saved",) if saved else ()), sounds=("wrong",))
        self._phase = "reading"
        self._buzz = None
        self._answer_shown = True
        self._show(render_answer(clue.column, clue.answer, language))
        self._changed()
        answer = await self._speech.say(clue.answer, language)
        await self._say("answer_was", sounds=("time_up",), after=answer)

    async def _counted_now(self, clue: Clue) -> None:
        """End the square the room just counted (while it was still open) with that team's points."""
        self._counted, self._overruled = None, False
        self._phase, self._buzz = "reading", None
        self._answer_shown = True
        self._show(render_answer(clue.column, clue.answer, self._language))
        self._changed()
        await self._say("overruled", sounds=("correct",))

    def _points(self, clue: Clue) -> int:
        return clue.points * (2 if clue.hidden in ("double", "bomb") else 1)

    def _lose(self, team: str, clue: Clue) -> tuple[int, bool]:
        """Take a miss's points from ``team``; return them, and whether its Shield saved them (and is spent)."""
        if not self._costly(clue):
            return 0, False
        if team in self._shields and self._shields[team] is clue:
            del self._shields[team]
            return 0, True
        self._scores[team] = self._scores.get(team, 0) - clue.points
        return clue.points, False

    async def _right(self, player: Player, clue: Clue) -> None:
        team, language = player.team, self._language
        points = self._points(clue)
        self._scores[team] = self._scores.get(team, 0) + points
        if clue.hidden == "steal":
            others = [t for t in self._teams_playing() if t != team and self._scores.get(t, 0) > 0]
            if others:
                robbed = max(others, key=lambda t: self._scores.get(t, 0))
                taken = min(clue.points, self._scores[robbed])
                self._scores[robbed] -= taken
                self._scores[team] += taken
        self._streak = {team: self._streak.get(team, 0) + 1}
        self._wrong_row[team] = 0
        self._control = team
        self._message = (f"¡Correcto, {player.name}! +{points}" if language == "es" else f"Correct, {player.name}! +{points}")  # fmt: skip
        self._phase, self._answer_shown = "reading", True
        self._changed()
        if self._context is not None:
            self._context.services.emotion("happy")
        moment = "bomb_defused" if clue.hidden == "bomb" else "correct_hard" if clue.points == POINTS[-1] else "correct_streak" if self._streak[team] >= STREAK else "correct"  # fmt: skip
        await self._say(moment, sounds=("correct",))

    async def _wrong(self, player: Player, clue: Clue, said: str) -> None:
        team, language = player.team, self._language
        before = self._scores.get(team, 0)
        lost, saved = self._lose(team, clue)
        self._streak[team] = 0
        self._wrong_row[team] = self._wrong_row.get(team, 0) + 1
        self._locked.add(team)
        self._buzz = None
        if said:
            self._vote = _Vote(player=player, said=said, clue=clue, restore=lost + self._points(clue), voters=set())
        self._message = (f"{player.name}: «{said}» — no" if language == "es" else f"{player.name}: “{said}” — no") if said else None  # fmt: skip
        self._phase = "reading"
        self._changed()
        shield = ("shield_saved",) if saved else ()
        if clue.hidden == "bomb":
            await self._say("bomb_boom", *shield, sounds=("wrong",))
        elif self._wrong_row[team] >= MISS_STREAK:
            self._wrong_row[team] = 0
            await self._say(self._either("wrong_streak", "wrong"), *shield, sounds=("wrong",))
        elif before >= 0 > self._scores[team]:
            await self._say("wrong", "below_zero", sounds=("wrong",))
        else:
            await self._say("wrong", *shield, sounds=("wrong",))

    async def _everyone(self, clue: Clue) -> None:
        """Everybody types an answer; every right one scores for its team."""
        assert self._judge is not None and self._phones is not None
        self._phase, self._typed = "everyone", {}
        await self._countdown(EVERYONE_S)
        self._phase = "reading"
        self._changed()
        players = self._phones.players
        typed = [(players[player_id], text) for player_id, text in self._typed.items() if player_id in players]
        rulings = await asyncio.gather(
            *(self._judge.rule(clue.id, clue.text, clue.accept, text, spoken=False) for _, text in typed)
        )
        right = [player for (player, _), ruling in zip(typed, rulings) if ruling is Ruling.RIGHT]
        for player in right:
            self._scores[player.team] = self._scores.get(player.team, 0) + clue.points
        names = ", ".join(player.name for player in right)
        if self._language == "es":
            self._message = f"Acertaron: {names}" if right else "Nadie acertó"
        else:
            self._message = f"Got it: {names}" if right else "Nobody got it"
        self._answer_shown = True
        self._changed()
        self._show(render_answer(clue.column, clue.answer, self._language))
        answer = await self._speech.say(clue.answer, self._language)
        if right:
            await self._say("correct", "answer_was", sounds=("correct",), after=answer)
        else:
            await self._say("answer_was", sounds=("time_up",), after=answer)

    async def _final(self) -> None:
        """Secret bets, the final clue, typed answers, and the reveal from the lowest score up."""
        board, language = self._board, self._language
        assert board is not None and self._judge is not None
        clue = board.final
        self._clue, self._clue_shown, self._vote, self._answer_shown = clue, False, None, False
        self._phase, self._bets, self._typed, self._reveal = "final_bet", {}, {}, []
        self._message = None
        self._show(render_final(board.final_category, self._scoreboard(), language, step="bet"))
        self._changed()
        await self._say("final_intro", sounds=("final",))
        await self._say("place_bets")
        await self._countdown(BET_S, done=lambda: all(team in self._bets for team in self._teams_playing()))
        self._phase = "final_clue"
        self._changed()
        self._show(render_clue(board.final_category, 0, clue.text, language))
        reading = await self._speech.say(clue.text, language)
        line = self._line("final_clue")
        intro = [line[1]] if line else []
        self._clue_shown = True
        self._changed()
        await self._play(join(*intro, *([reading] if reading is not None else [])), clue.text)
        self._phase = "final_answer"
        await self._say("thinking")
        await self._countdown(FINAL_S, music="thinking")
        self._phase = "reveal"
        self._changed()
        await self._say("reveal", sounds=("drumroll",))
        for team in sorted(self._teams_playing(), key=lambda t: self._scores.get(t, 0)):
            text, bet = self._typed.get(team, ""), self._bets.get(team, 0)
            entry: dict[str, Any] = {"team": team, "text": text, "bet": bet, "right": None}
            self._reveal.append(entry)
            self._show(render_reveal(team_name(team, language), text, bet, None, language))
            self._changed()
            spoken = await self._speech.say(text, language) if text else None
            buzz = self._sounds.get(f"buzz_{team}")
            await self._play(join(*([buzz] if buzz is not None else []), *([spoken] if spoken is not None else [silence(0.5)])), text)  # fmt: skip
            right = await self._judge.rule(clue.id, clue.text, clue.accept, text, spoken=False) is Ruling.RIGHT
            entry["right"] = right
            self._scores[team] = self._scores.get(team, 0) + (bet if right else -bet)
            self._show(render_reveal(team_name(team, language), text, bet, right, language))
            self._changed()
            await self._say("correct" if right else "wrong", sounds=("correct" if right else "wrong",))
        self._answer_shown = True
        self._show(render_answer(board.final_category, clue.answer, language))
        self._changed()
        answer = await self._speech.say(clue.answer, language)
        await self._say("answer_was", after=answer)
        # The e-ink screen takes a few seconds to draw; the answer stays up before the winner covers it.
        if self._context is not None:
            await self._context.sleep(FINAL_ANSWER_S)

    async def _ending(self) -> None:
        language = self._language
        self._phase, self._message = "over", None
        self._show(render_winner(self._scoreboard(), language))
        self._changed()
        scores = {team: self._scores.get(team, 0) for team in self._teams_playing()}
        best = max(scores.values(), default=0)
        winners = [team for team, points in scores.items() if points == best]
        if self._context is not None:
            self._context.services.emotion("excited")
        if len(winners) == 1:
            await self._say("winner", f"winner_{winners[0]}", sounds=("drumroll",))
        else:
            await self._say("tie")
        await self._say(sounds=("fanfare", "applause"))
        await self._say(self._own("goodbye"))
        outro = self._sounds.get("outro")
        if outro is not None:
            await self._play(outro)
        logger.info("Big Brain over: %s", ", ".join(f"{team} {points}" for team, points in scores.items()))
