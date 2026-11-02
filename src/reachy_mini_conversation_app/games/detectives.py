"""Detectives: a mystery party game; Richie narrates and plays the suspects, players investigate on their phones.

Players join by the QR code on the TRMNL. A pre-written case (``detectives_case.py``) is picked for adults or kids;
each phone gets a secret role (a detective, or for adults one detective who also saw something). Turns go round
the players: on your turn your phone lets you question a suspect or witness (you then ask aloud and Richie listens
to you only, or you type it) or examine a clue, which goes up on the TRMNL and every phone. Witness answers come
from a text model held to that character's sheet, with a scripted line when it fails. After the case's rounds, or
earlier when most players vote for it, everyone accuses in secret on the phone (who, and why); Richie reveals the
truth step by step and who solved it. Pause, quit and "¿lo terminamos?" come from the game host.
"""

import json
import time
import random
import asyncio
import logging
from typing import Any, Final, Callable
from pathlib import Path
from dataclasses import field, dataclass

from reachy_mini_conversation_app.games.base import UNCLEAR_ANSWER, Game, GameContext
from reachy_mini_conversation_app.games.catalog import DETECTIVES, words
from reachy_mini_conversation_app.quick_commands import QuickReply
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, Sounds, Speech
from reachy_mini_conversation_app.games.detectives_case import (
    CASES_DIR,
    Case,
    Clue,
    CaseError,
    Character,
    pick_case,
    load_cases,
)
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.detectives_screens import (
    render_card,
    render_join,
    render_board,
    render_solution,
)
from reachy_mini_conversation_app.games.detectives_witness import (
    Witness,
    WitnessError,
    shown,
    spoken,
    split_first,
    witness_instructions,
)


logger = logging.getLogger(__name__)

PAGE: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "detectives" / "index.html"
TURN_S: Final[float] = 60.0
QUESTION_S: Final[float] = 25.0  # from the end of Richie's "what do you ask?" to the question's text
WITNESS_S: Final[float] = 15.0
ACCUSE_S: Final[float] = 90.0
LOBBY_REMINDER_S: Final[float] = 75.0
AFTER_S: Final[float] = 30.0  # the page stays up for the solution
INSIDER_PLAYERS: Final[int] = 3  # an adult game this big gives one detective a secret
QUESTION_MAX: Final[int] = 200
START_WORDS: Final[frozenset[str]] = frozenset(
    {"empezamos", "empecemos", "empieza", "arranca", "listo", "listos", "a jugar", "start", "lets start", "ready"}
)

# Richie's fixed lines; {name} and the like are filled in at the time.
LINES: Final[dict[str, dict[str, str]]] = {
    "welcome": {
        "es": "¡Bienvenidos a Detectives! Escaneen el código de la pantalla con el teléfono, escriban su nombre, y cuando estén todos, toquen Empezar.",  # fmt: skip
        "en": "Welcome to Detectives! Scan the code on the screen with your phone, type your name, and when everyone is in, tap Start.",  # fmt: skip
    },
    "waiting": {
        "es": "Los espero. Escaneen el código y toquen Empezar cuando estén listos.",
        "en": "I'm waiting. Scan the code and tap Start when you're ready.",
    },
    "no_cases": {"es": "No encuentro ningún caso.", "en": "I can't find any cases."},
    "roles": {
        "es": "Miren su teléfono: cada uno tiene un papel secreto. Por turnos, interroguen a los sospechosos o examinen una pista. Cuando tengan una idea, voten para acusar.",  # fmt: skip
        "en": "Look at your phone: each of you has a secret role. Take turns to question the suspects or examine a clue. When you have an idea, vote to accuse.",  # fmt: skip
    },
    "round": {"es": "Ronda {number}.", "en": "Round {number}."},
    "turn": {"es": "{name}, te toca.", "en": "{name}, your turn."},
    "pass": {"es": "{name} pasa.", "en": "{name} passes."},
    "ask": {"es": "{name}, ¿qué le preguntas a {who}?", "en": "{name}, what do you ask {who}?"},
    "no_question": {"es": "No oí la pregunta. Seguimos.", "en": "I didn't catch the question. Let's go on."},
    "clue": {"es": "Nueva pista: {title}. {text}", "en": "New clue: {title}. {text}"},
    "last_round": {"es": "Última ronda.", "en": "Last round."},
    "accuse": {
        "es": "¡Hora de acusar! En su teléfono, elijan en secreto quién fue y por qué.",
        "en": "Time to accuse! On your phone, choose in secret who did it and why.",
    },
    "truth": {"es": "Y ahora... la verdad.", "en": "And now... the truth."},
    "culprit": {"es": "¡El culpable es {who}!", "en": "The culprit is {who}!"},
    "solved": {"es": "¡Lo resolvieron: {names}!", "en": "Solved by: {names}!"},
    "motive": {"es": "Y además acertaron el motivo: {names}.", "en": "And got the motive too: {names}."},
    "unsolved": {
        "es": "Nadie lo resolvió. Esta vez ganó el culpable.",
        "en": "Nobody solved it. This time the culprit wins.",
    },
    "goodbye": {
        "es": "¡Caso cerrado! Gracias por jugar, detectives.",
        "en": "Case closed! Thanks for playing, detectives.",
    },  # fmt: skip
}
ACCUSE_TITLE: Final[dict[str, str]] = {"es": "¡A acusar!", "en": "Accusation!"}
ROLES: Final[dict[str, dict[str, str]]] = {
    "detective": {"es": "Detective", "en": "Detective"},
    "detective_text": {
        "es": "Interroga a los sospechosos y examina las pruebas. Al final, acusa en secreto.",
        "en": "Question the suspects and examine the evidence. At the end, accuse in secret.",
    },
    "insider": {"es": "Detective · testigo secreto", "en": "Detective · secret witness"},
    "insider_text": {
        "es": "{fact} Decide si lo cuentas o te lo guardas.",
        "en": "{fact} Your call: share it or keep it to yourself.",
    },
}


@dataclass
class DetectivesSetup:
    """Where Detectives keeps its history and how witnesses answer; main.py fills it from the config."""

    data_dir: Path
    port: int
    witness: Witness | None
    public_url: str | None = None
    cases_dir: Path = CASES_DIR
    # Characters with a designed voice speak in it through ElevenLabs; without a key Richie says their lines.
    elevenlabs_key: str | None = None

    @property
    def history(self) -> Path:
        """Return the file of cases played, the latest last."""
        return self.data_dir / "detectives" / "played.json"


@dataclass
class Testimony:
    """One question and its answer, in every detective's notebook."""

    asker: str
    character: Character
    question: str
    answer: str


@dataclass
class _Accusation:
    suspect: str
    motive: str | None


@dataclass
class _Result:
    solvers: list[str] = field(default_factory=list)
    motive: list[str] = field(default_factory=list)


def read_played(path: Path) -> list[str]:
    """Return the ids of the cases played, the latest last ([] when none)."""
    try:
        played = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(case_id) for case_id in played] if isinstance(played, list) else []


def remember_played(path: Path, case_id: str) -> None:
    """Note that ``case_id`` was just played."""
    played = [known for known in read_played(path) if known != case_id] + [case_id]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(played), encoding="utf-8")


class Detectives(Game):
    """One Detectives game, from joining to the solution."""

    info = DETECTIVES

    def __init__(self, setup: DetectivesSetup, *, rng: random.Random | None = None) -> None:
        """Get ready; cases are loaded when the game plays."""
        self._setup = setup
        self._rng = rng or random.Random()
        self._sounds = Sounds(setup.data_dir / "big-brain" / "sounds")
        self._voices: dict[str, Speech] = {}  # by ElevenLabs voice id
        self._context: GameContext | None = None
        self._phones: Phones | None = None
        self._case: Case | None = None
        self._phase = "lobby"
        self._kids = False
        self._started = False
        self._order: list[Player] = []
        self._insider: str | None = None
        self._round = 0
        self._turn: Player | None = None
        self._action: dict[str, Any] | None = None
        self._found: list[Clue] = []
        self._notes: list[Testimony] = []
        self._votes: set[str] = set()
        self._accusations: dict[str, _Accusation] = {}
        self._result: _Result | None = None
        self._message: str | None = None
        self._deadline: float | None = None
        self._event = asyncio.Event()

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line without a name in it, both languages."""
        return frozenset(text for line in LINES.values() for text in line.values() if "{" not in text)

    @property
    def _language(self) -> str:
        return self._context.language if self._context is not None else "es"

    def _line(self, key: str, **values: str) -> str:
        return LINES[key][self._language].format(**values)

    # What phones see ------------------------------------------------------------------------------

    def snapshot(self, player: Player | None) -> dict[str, Any]:
        """Return the game as phone ``player`` should see it (None: a phone that has not joined)."""
        context, phones, case = self._context, self._phones, self._case
        language = self._language
        paused = context is not None and not context.running.is_set() and self._phase != "over"
        players = self._order or (sorted(phones.players.values(), key=lambda p: p.joined_at) if phones else [])
        can: list[str] = []
        if player is None:
            if self._phase == "lobby":
                can.append("join")
        elif paused:
            can.append("resume")
        elif self._phase == "lobby":
            can += ["start", "settings"]
        elif self._phase == "accuse" and player.id not in self._accusations:
            can.append("accuse")
        elif self._phase in ("turn", "asking", "answering", "clue"):
            if self._phase == "turn" and self._turn is not None and player.id == self._turn.id:
                can.append("act")
            if player.id not in self._votes:
                can.append("vote")
        deadline = None
        if self._deadline is not None and context is not None and not paused:
            deadline = int((time.time() + max(0.0, self._deadline - context.played_s)) * 1000)
        role = None
        if player is not None and case is not None:
            if player.id == self._insider and case.insider is not None:
                text = ROLES["insider_text"][language].format(fact=case.insider[language])
                role = {"name": ROLES["insider"][language], "text": text}
            else:
                role = {"name": ROLES["detective"][language], "text": ROLES["detective_text"][language]}
        mine = self._accusations.get(player.id) if player is not None else None
        return {
            "phase": "paused" if paused else self._phase,
            "language": language,
            "title": case.title[language] if case else "Detectives",
            "intro": case.intro[language] if case else None,
            "kids": self._kids,
            "you": {"player": player.id, "name": player.name, "role": role} if player else None,
            "players": [
                {
                    "name": p.name,
                    "turn": self._turn is not None and p.id == self._turn.id,
                    "accused": p.id in self._accusations,
                }  # fmt: skip
                for p in players
            ],
            "round": self._round,
            "rounds": case.rounds if case else 0,
            "turn": self._turn.name if self._turn else None,
            "characters": (
                [
                    {"id": c.id, "name": c.name[language], "about": c.about[language], "kind": c.kind}
                    for c in case.characters
                ]  # fmt: skip
                if case
                else []
            ),
            "clues": [{"id": c.id, "title": c.title[language], "text": c.text[language]} for c in self._found],
            "available": (
                [{"id": c.id, "title": c.title[language]} for c in self._available()] if "act" in can else []
            ),
            "notes": [
                {"asker": n.asker, "who": n.character.name[language], "question": n.question, "answer": n.answer}
                for n in self._notes
            ],
            "votes": {"yes": len(self._votes), "needed": self._votes_needed()},
            "motives": [{"id": m.id, "text": m.text[language]} for m in case.motives] if case else [],
            "accused": {"suspect": mine.suspect, "motive": mine.motive} if mine else None,
            "deadline": deadline,
            "can": can,
            "message": self._message,
            "result": self._solution(player) if self._result is not None else None,
        }

    def _solution(self, player: Player | None) -> dict[str, Any]:
        case, result = self._case, self._result
        assert case is not None and result is not None
        language = self._language
        culprit = case.character(case.culprit)
        motive = next(m for m in case.motives if m.id == case.motive)
        mine = self._accusations.get(player.id) if player is not None else None
        return {
            "culprit": culprit.name[language] if culprit else case.culprit,
            "motive": motive.text[language],
            "solvers": result.solvers,
            "motive_right": result.motive,
            "you_right": mine is not None and mine.suspect == case.culprit,
        }

    def _available(self) -> list[Clue]:
        case = self._case
        if case is None:
            return []
        found = {clue.id for clue in self._found}
        return [clue for clue in case.clues if clue.round <= self._round and clue.id not in found]

    def _votes_needed(self) -> int:
        players = len(self._phones.players) if self._phones is not None else 0
        return max(1, players // 2 + 1)

    def _changed(self) -> None:
        if self._phones is not None:
            self._phones.changed()

    # What phones send -----------------------------------------------------------------------------

    def on_message(self, player: Player, message: dict[str, Any]) -> None:
        """Take a phone's message (the server already checked the player)."""
        context, case = self._context, self._case
        if context is None:
            return
        context.played()
        kind = message.get("type")
        if kind == "resume" and not context.running.is_set():
            context.resume()
        elif kind == "start" and self._phase == "lobby":
            self._started = True
            self._event.set()
        elif kind == "kids" and self._phase == "lobby":
            self._kids = bool(message.get("on"))
            self._show_join()
        elif kind == "joined" and self._phase == "lobby":
            self._show_join()
        elif kind in ("ask", "examine") and self._phase == "turn" and self._turn is not None and player.id == self._turn.id:  # fmt: skip
            target = str(message.get("id") or "")
            known = (
                case is not None and case.character(target) is not None
                if kind == "ask"
                else target in {clue.id for clue in self._available()}
            )
            if known and self._action is None:
                self._action = {"type": kind, "id": target, "text": " ".join(str(message.get("text") or "").split())[:QUESTION_MAX]}  # fmt: skip
                self._event.set()
        elif kind == "vote" and self._phase in ("turn", "asking", "answering", "clue"):
            self._votes.add(player.id)
            if len(self._votes) >= self._votes_needed():
                self._event.set()
        elif kind == "accuse" and self._phase == "accuse" and case is not None:
            suspect = str(message.get("suspect") or "")
            motive = str(message.get("motive") or "") or None
            if suspect in {c.id for c in case.suspects}:
                motive = motive if motive in {m.id for m in case.motives} else None
                self._accusations[player.id] = _Accusation(suspect, motive)
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
        """Play one of Big Brain's sounds when it was made; nothing otherwise."""
        context = self._context
        sound: Audio | None = self._sounds.get(name)
        if context is None or sound is None or context.services.say_audio is None:
            return
        await context.playing()
        await context.services.say_audio(RATE, sound, "", context.language, None)

    async def _wait(self, seconds: float, *, done: Callable[[], bool] | None = None) -> bool:
        """Wait for a phone event (or ``done()``), up to ``seconds`` of playing time; True when one came."""
        context = self._context
        assert context is not None
        self._event.clear()
        self._deadline = context.played_s + seconds
        self._changed()
        try:
            while not self._event.is_set() and not (done is not None and done()):
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

    async def _listen(self, text: str) -> str:
        """Say ``text``, then return what the player on turn says next ("" for nothing)."""
        context = self._context
        assert context is not None
        await context.playing()
        heard: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def answer(said: str) -> QuickReply:
            if not heard.done():
                heard.set_result(said)
            return QuickReply(None, context.language, "game")

        context.services.ask(text, context.language, answer)
        try:
            said = await asyncio.wait_for(heard, QUESTION_S)
        except asyncio.TimeoutError:
            return ""
        return "" if said == UNCLEAR_ANSWER else said.strip()

    def _show(self, png: bytes) -> None:
        if self._context is not None:
            self._context.show(png)

    def _show_join(self) -> None:
        phones = self._phones
        if phones is None:
            return
        names = [p.name for p in sorted(phones.players.values(), key=lambda p: p.joined_at)]
        self._show(render_join(phones.url(), phones.code, names, self._language, kids=self._kids))

    def _show_board(self) -> None:
        case = self._case
        assert case is not None
        language = self._language
        people = [(c.name[language], c.kind == "witness") for c in case.characters]
        evidence = [clue.title[language] for clue in self._found]
        turn = self._turn.name if self._turn else None
        self._show(render_board(case.title[language], people, evidence, self._round, case.rounds, turn, language))

    # The game -------------------------------------------------------------------------------------

    async def play(self, context: GameContext) -> None:
        """Run one game."""
        self._context = context
        self._kids = context.kids
        try:
            cases = await asyncio.to_thread(load_cases, self._setup.cases_dir)
        except (CaseError, OSError) as error:
            logger.warning("Detectives: cases not loaded: %s", error)
            cases = []
        if not cases:
            await self._say(self._line("no_cases"))
            return
        self._phones = Phones(
            self._setup.port,
            self.snapshot,
            self.on_message,
            lambda: self._phase == "lobby",
            public_url=self._setup.public_url,
            page=PAGE,
        )
        await self._phones.start()
        context.join_url = self._phones.url()
        watcher = asyncio.get_running_loop().create_task(self._watch_pause(), name="detectives-pause")
        try:
            await self._lobby()
            played = await asyncio.to_thread(read_played, self._setup.history)
            case = pick_case(cases, kids=self._kids, played=played, rng=self._rng)
            if case is None:
                await self._say(self._line("no_cases"))
                return
            self._case = case
            await asyncio.to_thread(remember_played, self._setup.history, case.id)
            logger.info("Detectives: case %s, %d players", case.id, len(self._order))
            await self._introduce()
            await self._rounds()
            await self._accuse()
            await self._reveal()
            await asyncio.sleep(AFTER_S)
        finally:
            watcher.cancel()
            await self._phones.stop()

    async def _watch_pause(self) -> None:
        context = self._context
        assert context is not None
        running = context.running.is_set()
        while True:
            await asyncio.sleep(0.5)
            if context.running.is_set() != running:
                running = context.running.is_set()
                self._changed()

    async def _lobby(self) -> None:
        """Show the join screen until someone starts with at least one player."""
        assert self._phones is not None
        self._phase = "lobby"
        self._show_join()
        self._changed()
        await self._say(self._line("welcome"))
        while not (self._started and self._phones.players):
            self._started = False
            if not await self._wait(LOBBY_REMINDER_S) and not self._started:
                await self._say(self._line("waiting"))
        self._order = sorted(self._phones.players.values(), key=lambda p: p.joined_at)
        case_has_insider = len(self._order) >= INSIDER_PLAYERS and not self._kids
        self._insider = self._rng.choice(self._order).id if case_has_insider else None

    async def _introduce(self) -> None:
        case = self._case
        assert case is not None
        language = self._language
        self._phase = "intro"
        self._show(render_card(case.title[language], case.intro[language]))
        self._changed()
        await self._say(case.intro[language])
        await self._say(self._line("roles"))

    async def _rounds(self) -> None:
        case = self._case
        assert case is not None
        for number in range(1, case.rounds + 1):
            self._round = number
            await self._say(self._line("last_round" if number == case.rounds > 1 else "round", number=str(number)))
            for player in self._order:
                if self._accusing():
                    return
                await self._play_turn(player)
            if self._accusing():
                return

    def _accusing(self) -> bool:
        return len(self._votes) >= self._votes_needed()

    async def _play_turn(self, player: Player) -> None:
        case = self._case
        assert case is not None
        language = self._language
        self._phase, self._turn, self._action, self._message = "turn", player, None, None
        self._show_board()
        self._changed()
        await self._say(self._line("turn", name=player.name))
        await self._wait(TURN_S, done=lambda: self._action is not None)
        action = self._action
        if action is None:
            if not self._accusing():
                await self._say(self._line("pass", name=player.name))
            return
        if action["type"] == "examine":
            clue = next(c for c in case.clues if c.id == action["id"])
            self._phase = "clue"
            self._found.append(clue)
            self._message = clue.title[language]
            self._show(render_card(clue.title[language], clue.text[language], footer=player.name))
            self._changed()
            await self._sound("correct")
            await self._say(self._line("clue", title=clue.title[language], text=clue.text[language]))
            return
        character = case.character(action["id"])
        assert character is not None
        self._phase = "asking"
        self._changed()
        question = action["text"] or await self._listen(self._line("ask", name=player.name, who=character.name[language]))  # fmt: skip
        if not question:
            await self._say(self._line("no_question"))
            return
        self._phase = "answering"
        self._show(render_card(character.name[language], f"«{question}»", footer=player.name))
        self._changed()
        answer = await self._witness(character, question)
        self._notes.append(Testimony(player.name, character, question, shown(answer)))
        self._show(render_card(character.name[language], shown(answer), footer=f"{player.name}: «{question}»"))
        self._changed()
        await self._say_as(character, answer)

    async def _say_as(self, character: Character, text: str) -> None:
        """Say ``text`` in the character's own voice when it has one, else in Richie's (tags dropped).

        The first sentence and the rest are made at once and the first plays as soon as it is ready: a whole
        answer took 2 to 5 s to make (2026-11-02), its first sentence about a second.
        """
        context = self._context
        assert context is not None
        say_audio = context.services.say_audio
        voice = character.voice.id if character.voice is not None else None
        if not voice or not self._setup.elevenlabs_key or say_audio is None:
            await self._say(shown(text))
            return
        speech = self._voices.get(voice)
        if speech is None:
            # Answers are new every time, so nothing is kept on disk.
            folder = self._setup.data_dir / "detectives" / "speech"
            speech = self._voices[voice] = Speech(folder, self._setup.elevenlabs_key, voice=voice, keep=False)
        language = context.language
        parts = [part for part in split_first(text) if part]
        made = [asyncio.create_task(speech.say(part, language)) for part in parts]
        for index, (part, task) in enumerate(zip(parts, made)):
            samples = await task
            if samples is None:
                logger.warning("Detectives: %s's voice failed; Richie says the line", character.id)
                for later in made[index + 1 :]:
                    later.cancel()
                await self._say(shown(" ".join(parts[index:])))
                return
            await context.playing()
            await say_audio(RATE, samples, shown(part), language, None)

    async def _witness(self, character: Character, question: str) -> str:
        """Return ``character``'s answer to ``question``: the model's, or their scripted line when it fails."""
        case = self._case
        assert case is not None
        language = self._language
        witness = self._setup.witness
        if witness is not None:
            instructions = witness_instructions(case, character, self._found, language)
            try:
                answer = spoken(await asyncio.wait_for(witness(instructions, question), WITNESS_S))
                if answer:
                    return answer
            except (WitnessError, asyncio.TimeoutError) as error:
                logger.warning("Detectives: witness model failed (%s); scripted line", type(error).__name__)
        return character.fallback[language]

    async def _accuse(self) -> None:
        case = self._case
        assert case is not None and self._phones is not None
        language = self._language
        self._phase, self._turn, self._message = "accuse", None, None
        self._show(render_card(ACCUSE_TITLE[language], self._line("accuse")))
        self._changed()
        await self._sound("drumroll")
        await self._say(self._line("accuse"))
        players = self._phones.players
        await self._wait(ACCUSE_S, done=lambda: all(player_id in self._accusations for player_id in players))

    async def _reveal(self) -> None:
        case = self._case
        assert case is not None and self._phones is not None
        language = self._language
        self._phase = "reveal"
        self._changed()
        await self._say(self._line("truth"))
        for index, step in enumerate(case.reveal, start=1):
            self._show(render_card(f"{case.title[language]} · {index}/{len(case.reveal)}", step[language]))
            await self._say(step[language])
        culprit = case.character(case.culprit)
        assert culprit is not None
        motive = next(m for m in case.motives if m.id == case.motive)
        players = {p.id: p for p in self._order}
        right = [players[pid].name for pid, a in self._accusations.items() if pid in players and a.suspect == case.culprit]  # fmt: skip
        both = [players[pid].name for pid, a in self._accusations.items() if pid in players and a.suspect == case.culprit and a.motive == case.motive]  # fmt: skip
        self._result = _Result(solvers=right, motive=both)
        self._phase = "over"
        self._show(render_solution(culprit.name[language], motive.text[language], right, language))
        self._changed()
        await self._sound("drumroll")
        await self._say(self._line("culprit", who=culprit.name[language]))
        if right:
            if self._context is not None:
                self._context.services.emotion("excited")
            await self._sound("fanfare")
            await self._say(self._line("solved", names=_names(right, language)))
            if both:
                await self._say(self._line("motive", names=_names(both, language)))
        else:
            await self._sound("wrong")
            await self._say(self._line("unsolved"))
        await self._say(self._line("goodbye"))
        logger.info("Detectives over: %d of %d solved it", len(right), len(self._order))


def _names(names: list[str], language: str) -> str:
    """Return "Ana, Luis y Eva" / "Ana, Luis and Eva"."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} {'y' if language == 'es' else 'and'} {names[-1]}"
