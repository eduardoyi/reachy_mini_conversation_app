"""Scribble Rush: teams draw a secret word in a rush and Richie is the only one who guesses (docs/plans/party-games.md).

Players join by scanning the QR code on the TRMNL and pick a team, as for Big Brain (the same phone server serves
``static/scribble/index.html``). Teams take turns; each turn the next player of the team sees a word on their
phone and draws it on paper while a tick-tock speeds up, then holds the drawing up to Richie's camera. One frame
goes to a vision model (Gemini), which gives up to three guesses, least to most confident; Richie says them one
by one, and code then Jev check each against the word's forms in both languages. The earlier the hit, the more
squares the team moves on the TRMNL's track; kids mode moves one square for a miss too. The first team to the
finish wins, or the one furthest along after ROUNDS rounds. Pause, quit and the idle end come from the game host.
"""

import json
import time
import random
import asyncio
import logging
from typing import Any, Final, Callable, Coroutine
from pathlib import Path
from dataclasses import dataclass

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from reachy_mini_conversation_app.jev import JevError, sized_jpeg
from reachy_mini_conversation_app.games.base import Game, JevAsk, GameContext
from reachy_mini_conversation_app.games.hunt import beep_note, thumbnail
from reachy_mini_conversation_app.usage_costs import get_ledger
from reachy_mini_conversation_app.eink_display import render_photo
from reachy_mini_conversation_app.local_speech import SAMPLE_RATE
from reachy_mini_conversation_app.games.catalog import SCRIBBLE_RUSH, words
from reachy_mini_conversation_app.games.big_brain import START_WORDS, team_name
from reachy_mini_conversation_app.games.scribble_words import KIDS_WORDS, ADULT_WORDS, Word, category_name
from reachy_mini_conversation_app.games.big_brain_judge import plain
from reachy_mini_conversation_app.games.big_brain_phones import Phones, Player
from reachy_mini_conversation_app.games.scribble_screens import render_track
from reachy_mini_conversation_app.games.big_brain_screens import render_join, render_winner


logger = logging.getLogger(__name__)

PAGE: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "scribble" / "index.html"
TEAMS: Final[tuple[str, ...]] = ("red", "blue", "green")
GOAL: Final[int] = 24
ROUNDS: Final[int] = 6
# Squares for a hit on Richie's first, second and third guess; kids mode moves KIDS_MISS for a miss.
SQUARES: Final[tuple[int, ...]] = (6, 4, 2)
KIDS_MISS: Final[int] = 1
DRAW_S: Final[float] = 20.0
KIDS_DRAW_S: Final[float] = 30.0
# After "¡muéstramelo!", time to lift the drawing up to the camera before the frame is taken.
HOLD_S: Final[float] = 2.5
VISION_TIMEOUT_S: Final[float] = 15.0
LOBBY_REMINDER_S: Final[float] = 75.0
# The guesses and the word stay on the phones this long before the next turn.
RESULT_S: Final[float] = 3.0
AFTER_S: Final[float] = 30.0
# Jev counts a guess the code did not match at this probability.
JEV_HIT_P: Final[float] = 0.6
# The tick-tock: two notes a beat, from START_BPM to END_BPM over the drawing time.
START_BPM: Final[float] = 90.0
END_BPM: Final[float] = 200.0
_TICK_TOCK: Final[tuple[float, float]] = (1318.5, 987.77)

_VISION_PROMPT: Final[str] = (
    "You are Richie, a friendly robot playing a drawing game with a family. Someone drew a secret word on paper in "
    "about 20 seconds and is holding the drawing up to your camera. The word is a {category}. {audience} Guess "
    'what the drawing shows. Answer in JSON: {{"remark": a short playful comment on the drawing (at most 10 '
    'words, naming none of your guesses), "guesses": [{{"guess": ..., "confidence": 0 to 1}}]}}. Give one '
    "guess when the drawing is clear and up to three different ones when unsure, from least to most confident. "
    "Write in {language}; each guess short, as you would say it aloud, with its article ('un gato', 'el Rey "
    "León'): for a movie its title, for a saying the saying itself. With no drawing in view, give no guesses."
)
_AUDIENCE: Final[dict[bool, str]] = {
    True: "A young child drew it: expect a few simple lines or a scribble of an everyday thing.",
    False: "An adult drew it in a hurry.",
}
_JEV_RULE: Final[str] = (
    "In a drawing guessing game, does Richie's guess `{key}` name the secret word `secret` (any of "
    "`accepted_answers` counts)? Count synonyms, either language, the same thing named more or less precisely (a "
    "kitten for a cat), and for a movie or a saying its title or a description of its main picture (a sleeping "
    "shrimp for 'camarón que se duerme'). Something merely related, of the same kind, or a whole category (a dog "
    "or an animal for a cat) does not count."
)

_LINES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "welcome": {
        "es": (
            "¡Bienvenidos a Scribble Rush! Escaneen el código con el teléfono, escojan un equipo y denle a empezar.",
        ),  # fmt: skip
        "en": ("Welcome to Scribble Rush! Scan the code with your phone, pick a team and press start.",),
    },
    "waiting": {
        "es": ("Estoy esperando: escaneen el código y denle a empezar.",),
        "en": ("I'm waiting: scan the code and press start.",),
    },
    "begin": {
        "es": ("¡Empezamos! Yo adivino, ustedes dibujan. ¡Prepárense lápiz y papel!",),
        "en": ("Let's go! You draw, I guess. Get your pencils and paper ready!",),
    },
    "turn": {
        "es": ("¡{player}, del equipo {team}, te toca dibujar! Mira tu teléfono… ¡ya!",),
        "en": ("{player}, team {team}, it's your turn to draw! Look at your phone… go!",),
    },
    "show": {
        "es": ("¡Tiempo! ¡Muéstrame el dibujo!", "¡Se acabó el tiempo! ¡A ver ese dibujo!"),
        "en": ("Time's up! Show me the drawing!", "Time! Let's see that drawing!"),
    },
    "done": {
        "es": ("¡Listo! ¡Muéstramelo!", "¿Ya? ¡A ver, a ver!"),
        "en": ("Done? Show me!", "Ready? Let me see!"),
    },
    "looking": {
        "es": ("Mmm… déjame ver…", "A ver, a ver…", "Mmm, interesante…"),
        "en": ("Hmm… let me see…", "Let's see…", "Hmm, interesting…"),
    },
    "first": {"es": ("¿Es {guess}?",), "en": ("Is it {guess}?",)},
    "middle": {"es": ("¿No? Mmm… ¿{guess}?", "¿Entonces {guess}?"), "en": ("No? Hmm… {guess}?", "Then {guess}?")},
    "last": {
        "es": ("¡Ya sé! ¿{guess}?", "¡Último intento! ¿{guess}?"),
        "en": ("I know! {guess}?", "Last try! {guess}?"),
    },  # fmt: skip
    "hit": {
        "es": (
            "¡Sí! ¡Era {word}! ¡El equipo {team} avanza {squares}!",
            "¡Lo sabía! ¡{word}! ¡{team} avanza {squares}!",
        ),  # fmt: skip
        "en": (
            "Yes! It was {word}! Team {team} moves {squares}!",
            "I knew it! {word}! Team {team} moves {squares}!",
        ),  # fmt: skip
    },
    "miss": {
        "es": ("¡Me rindo! Era {word}.", "¡Ay, no! Era {word}."),
        "en": ("I give up! It was {word}.", "Oh no! It was {word}."),
    },
    "kids_miss": {
        "es": ("¡Qué buen intento! Una casilla por dibujar.",),
        "en": ("Great try! One square for drawing.",),
    },
    "no_drawing": {
        "es": ("¡No veo ningún dibujo! Era {word}.",),
        "en": ("I can't see a drawing! It was {word}.",),
    },
    "blind": {
        "es": ("¡Ay, se me nubló la vista! Este turno no cuenta. Era {word}.",),
        "en": ("Oops, my eyes went blurry! This turn doesn't count. It was {word}.",),
    },
    "finish": {
        "es": ("¡El equipo {team} llegó a la meta!",),
        "en": ("Team {team} reached the finish!",),
    },
    "winner": {"es": ("¡Ganó el equipo {team}!",), "en": ("Team {team} wins!",)},
    "tie": {"es": ("¡Empate! ¡Todos dibujan increíble!",), "en": ("It's a tie! You're all amazing artists!",)},
    "thanks": {"es": ("¡Gracias por jugar Scribble Rush!",), "en": ("Thanks for playing Scribble Rush!",)},
}
_SQUARES: Final[dict[str, tuple[str, str]]] = {"es": ("una casilla", "{n} casillas"), "en": ("one square", "{n} squares")}  # fmt: skip
_SCREEN: Final[dict[str, dict[str, str]]] = {
    "draws": {"es": "{team} DIBUJA", "en": "{team} DRAWS"},
    "turn": {
        "es": "{player} · {category} · Ronda {round} de {rounds}",
        "en": "{player} · {category} · Round {round} of {rounds}",
    },  # fmt: skip
    "looking": {"es": "¿Qué será?", "en": "What could it be?"},
    "was": {"es": "ERA: {word}", "en": "IT WAS: {word}"},
    "moved": {"es": "{team} +{squares}", "en": "{team} +{squares}"},
}


@dataclass(frozen=True)
class Guesses:
    """What the vision model made of a drawing: a remark, and its guesses from least to most confident."""

    remark: str
    guesses: list[str]


Guesser = Callable[[bytes, Word, str, bool], Coroutine[Any, Any, Guesses]]


class VisionError(RuntimeError):
    """The vision model could not guess."""


class GeminiGuesser:
    """Guesses drawings with a Gemini Flash model: one call per drawing, recorded in the usage ledger."""

    def __init__(self, api_key: str, model: str) -> None:
        """Use ``model`` with ``api_key``."""
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def __call__(self, jpeg: bytes, word: Word, language: str, kids: bool) -> Guesses:
        """Return the guesses for the drawing in ``jpeg`` (the secret ``word`` gives only its category)."""
        prompt = _VISION_PROMPT.format(
            category=category_name(word.category, "en"),
            audience=_AUDIENCE[kids],
            language="Spanish" if language == "es" else "English",
        )
        contents: list[types.PartUnionDict] = [prompt, types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")]
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                    ),
                ),
                VISION_TIMEOUT_S,
            )
        except (genai_errors.APIError, asyncio.TimeoutError) as error:
            raise VisionError(type(error).__name__) from error
        usage = response.usage_metadata
        if usage is not None:
            tokens = {
                "input_text": int(usage.prompt_token_count or 0),
                "output_text": int(usage.candidates_token_count or 0),
                "thinking": int(usage.thoughts_token_count or 0),
            }
            get_ledger().record("scribble_guess", self._model, tokens)
        return read_guesses(response.text or "")


def read_guesses(text: str) -> Guesses:
    """Read the vision model's JSON answer: at most three guesses, ordered from least to most confident."""
    try:
        answer = json.loads(text)
    except ValueError as error:
        raise VisionError("unreadable answer") from error
    if not isinstance(answer, dict):
        raise VisionError("unreadable answer")
    ranked: list[tuple[float, str]] = []
    for item in answer.get("guesses") or []:
        if isinstance(item, dict) and isinstance(item.get("guess"), str) and item["guess"].strip():
            try:
                confidence = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            ranked.append((confidence, " ".join(item["guess"].split())[:60]))
    ranked.sort(key=lambda pair: pair[0])  # stable: equal confidences keep the model's order
    remark = answer.get("remark")
    return Guesses(" ".join(remark.split())[:100] if isinstance(remark, str) else "", [g for _, g in ranked[-3:]])


def _forms(form: str) -> set[str]:
    """Return ``form`` made plain, with its plurals: "gato", "gatos"; "leon", "leones"."""
    base = plain(form)
    return {base, f"{base}s", f"{base}es"} if base else set()


def guessed(guess: str, word: Word) -> bool:
    """Whether ``guess`` names ``word`` by code alone: one of its forms (or a plural), maybe with a few more words."""
    heard = plain(guess)
    if not heard:
        return False
    for form in word.accept:
        for target in _forms(form):
            if heard == target or f" {target} " in f" {heard} " and len(heard.split()) <= len(target.split()) + 3:
                return True
    return False


async def judge_guesses(guesses: list[str], word: Word, jev: JevAsk | None) -> list[bool]:
    """Return whether each guess names ``word``: code first, then one Jev call for the rest (when there is Jev)."""
    hits = [guessed(guess, word) for guess in guesses]
    unsure = [index for index, hit in enumerate(hits) if not hit]
    if not unsure or jev is None:
        return hits
    state: dict[str, Any] = {"secret": f"{word.es} / {word.en}", "accepted_answers": list(word.accept)}
    questions: dict[str, dict[str, Any]] = {}
    for index in unsure:
        key = f"guess_{index + 1}"
        state[key] = guesses[index]
        questions[key] = {"type": "noul", "instructions": _JEV_RULE.format(key=key)}
    try:
        answers = await jev(state, questions, {})
        for index in unsure:
            hits[index] = float(answers[f"guess_{index + 1}"]["noul"]) >= JEV_HIT_P
    except (JevError, KeyError, TypeError, ValueError) as error:
        logger.warning("Scribble Rush guesses not judged by Jev: %s", type(error).__name__)
    return hits


def scribble_phrases() -> frozenset[str]:
    """Every fixed line Richie says (no name, word or guess in it), in both languages, for the speech cache."""
    return frozenset(
        line
        for moments in _LINES.values()
        for language in ("es", "en")
        for line in moments[language]
        if "{" not in line
    )


@dataclass
class ScribbleSetup:
    """How Scribble Rush reaches phones and the vision model; main.py fills it from the config."""

    port: int
    guess: Guesser
    public_url: str | None = None


class ScribbleRush(Game):
    """One game of Scribble Rush, from joining to the winner."""

    info = SCRIBBLE_RUSH

    def __init__(self, setup: ScribbleSetup, *, rng: random.Random | None = None) -> None:
        """Get ready to play; the phone server starts with the game."""
        self._setup = setup
        self._rng = rng or random.Random()
        self._context: GameContext | None = None
        self._phones: Phones | None = None
        self._phase = "lobby"
        self._kids = False
        self._started = False
        self._squares: dict[str, int] = {}
        self._next_drawer: dict[str, int] = {}
        self._round = 0
        self._team: str | None = None
        self._drawer: Player | None = None
        self._word: Word | None = None
        self._skipped = False
        self._done = False
        self._used: set[str] = set()
        self._said: list[dict[str, Any]] = []  # the guesses said so far this turn, for the phones
        self._message: str | None = None
        self._deadline: float | None = None  # in playing seconds
        self._event = asyncio.Event()

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line, for the speech cache."""
        return scribble_phrases()

    # What phones see and send ---------------------------------------------------------------------

    @property
    def _language(self) -> str:
        return self._context.language if self._context is not None else "es"

    def _teams_playing(self) -> list[str]:
        phones = self._phones
        return [team for team in TEAMS if phones is not None and phones.team_players(team)]

    def snapshot(self, player: Player | None) -> dict[str, Any]:
        """Return the game as phone ``player`` sees it; only the drawer's phone gets the word."""
        context, phones = self._context, self._phones
        language = self._language
        paused = context is not None and not context.running.is_set() and self._phase != "over"
        drawing = player is not None and self._drawer is not None and player.id == self._drawer.id
        can: list[str] = []
        if player is None:
            if self._phase == "lobby":
                can.append("join")
        elif paused:
            can.append("resume")
        elif self._phase == "lobby":
            can += ["start", "settings"]
        elif self._phase == "drawing" and drawing:
            can += ["done"] + ([] if self._skipped else ["skip"])
        word = self._word
        deadline = None
        if self._deadline is not None and context is not None and not paused:
            deadline = int((time.time() + max(0.0, self._deadline - context.played_s)) * 1000)
        show_word = word is not None and (drawing or self._phase in ("result", "over"))
        return {
            "phase": "paused" if paused else self._phase,
            "language": language,
            "kids": self._kids,
            "you": {"player": player.id, "name": player.name, "team": player.team} if player else None,
            "teams": [
                {
                    "key": team,
                    "name": team_name(team, language),
                    "squares": self._squares.get(team, 0),
                    "players": [p.name for p in phones.team_players(team)] if phones else [],
                }
                for team in (TEAMS if self._phase == "lobby" else self._teams_playing())
            ],
            "goal": GOAL,
            "round": self._round,
            "rounds": ROUNDS,
            "turn": self._team,
            "drawer": {"name": self._drawer.name, "team": self._drawer.team} if self._drawer else None,
            "drawing": drawing,
            "category": category_name(word.category, language) if word else None,
            "word": word.name(language) if show_word and word else None,
            "guesses": self._said,
            "deadline": deadline,
            "can": can,
            "message": self._message,
        }

    def _changed(self) -> None:
        if self._phones is not None:
            self._phones.changed()

    def on_message(self, player: Player, message: dict[str, Any]) -> None:
        """Take a phone's message (the server already checked the player)."""
        context = self._context
        if context is None:
            return
        context.played()
        kind = message.get("type")
        drawing = self._phase == "drawing" and self._drawer is not None and player.id == self._drawer.id
        if kind == "resume" and not context.running.is_set():
            context.resume()
        elif kind == "joined" and self._phase == "lobby":
            self._show_join()
        elif kind == "start" and self._phase == "lobby":
            self._started = True
            self._event.set()
        elif kind == "kids" and self._phase == "lobby":
            self._kids = bool(message.get("on"))
            self._show_join()
        elif kind == "done" and drawing:
            self._done = True
            self._event.set()
        elif kind == "skip" and drawing and not self._skipped:
            self._skipped = True
            self._word = self._pick_word()
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

    # Playing --------------------------------------------------------------------------------------

    def _line(self, moment: str, **values: Any) -> str:
        return self._rng.choice(_LINES[moment][self._language]).format(**values)

    async def _say(self, moment: str, **values: Any) -> None:
        if self._context is not None:
            await self._context.say(self._line(moment, **values))

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

    async def play(self, context: GameContext) -> None:
        """Play one game."""
        self._context = context
        self._kids = context.kids
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
        watcher = asyncio.get_running_loop().create_task(self._watch_pause(), name="scribble-pause")
        try:
            if not await self._lobby():
                return
            winner = await self._rounds()
            await self._ending(winner)
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

    def _show_join(self) -> None:
        phones, context = self._phones, self._context
        if phones is None or context is None:
            return
        teams = [(team_name(team, self._language), [p.name for p in phones.team_players(team)]) for team in TEAMS]
        context.show(render_join(phones.url(), phones.code, teams, self._language, kids=self._kids, title="SCRIBBLE RUSH"))  # fmt: skip

    def _lanes(self) -> list[tuple[str, int]]:
        return [(team_name(team, self._language), self._squares.get(team, 0)) for team in self._teams_playing()]

    def _show_track(self, title: str, caption: str) -> None:
        if self._context is not None:
            turn = team_name(self._team, self._language) if self._team else None
            self._context.show(render_track(title, caption, self._lanes(), GOAL, turn=turn))

    async def _lobby(self) -> bool:
        """Show the join screen until someone starts the game."""
        context, phones = self._context, self._phones
        assert context is not None and phones is not None
        self._show_join()
        self._changed()
        await self._say("welcome")
        while not (self._started and phones.players):
            self._started = False
            if not await self._wait(LOBBY_REMINDER_S) and not self._started:
                await self._say("waiting")
        for team in self._teams_playing():
            self._squares[team] = 0
        logger.info("Scribble Rush started: %d players, %d teams", len(phones.players), len(self._teams_playing()))
        await self._say("begin")
        return True

    def _pick_word(self) -> Word:
        pool = KIDS_WORDS if self._kids else ADULT_WORDS
        left = [word for word in pool if word.es not in self._used] or list(pool)
        word = self._rng.choice(left)
        self._used.add(word.es)
        return word

    async def _rounds(self) -> str | None:
        """Play turns until a team finishes or ROUNDS rounds are over; return the team that finished first."""
        context, phones = self._context, self._phones
        assert context is not None and phones is not None
        teams = self._teams_playing()
        first = self._rng.randrange(len(teams))
        order = teams[first:] + teams[:first]
        for self._round in range(1, ROUNDS + 1):
            for team in order:
                players = phones.team_players(team)
                if not players:
                    continue
                drawer = players[self._next_drawer.get(team, 0) % len(players)]
                self._next_drawer[team] = self._next_drawer.get(team, 0) + 1
                await self._turn(team, drawer)
                if self._squares[team] >= GOAL:
                    return team
        return None

    async def _turn(self, team: str, drawer: Player) -> None:
        """One drawing: the word to the drawer's phone, the drawing time, the frame, Richie's guesses."""
        context = self._context
        assert context is not None
        language = self._language
        self._team, self._drawer, self._word = team, drawer, self._pick_word()
        self._skipped = self._done = False
        self._said, self._message = [], None
        self._phase = "drawing"
        name = team_name(team, language)
        context.progress = f"Round {self._round} of {ROUNDS}: {team} draws"
        category = category_name(self._word.category, language)
        caption = _SCREEN["turn"][language].format(player=drawer.name, category=category, round=self._round, rounds=ROUNDS)  # fmt: skip
        self._show_track(_SCREEN["draws"][language].format(team=name.upper()), caption)
        self._changed()
        await self._say("turn", player=drawer.name, team=name)
        context.played()
        started = context.played_s
        seconds = KIDS_DRAW_S if self._kids else DRAW_S
        tune = asyncio.get_running_loop().create_task(self._tick_tock(started, seconds), name="scribble-tune")
        try:
            await self._wait(seconds)
        finally:
            tune.cancel()
        self._phase = "showing"
        self._changed()
        await self._say("done" if self._done else "show")
        await context.sleep(HOLD_S)
        await self._guess(team)

    async def _tick_tock(self, started: float, seconds: float) -> None:
        """Tick-tock faster and faster while the drawing time runs (and Richie is free to play it)."""
        context = self._context
        assert context is not None
        beat = 0
        while context.played_s - started < seconds:
            await context.playing()
            progress = min(1.0, (context.played_s - started) / seconds)
            note_s = 60.0 / (START_BPM + (END_BPM - START_BPM) * progress) / 2
            if context.services.play_audio(SAMPLE_RATE, beep_note(_TICK_TOCK[beat % 2], note_s)):
                beat += 1
            await context.sleep(note_s)

    async def _guess(self, team: str) -> None:
        """Look at the drawing, say the guesses one by one, and move the team."""
        context, word, guess = self._context, self._word, self._setup.guess
        assert context is not None and word is not None
        language = self._language
        self._phase = "guessing"
        self._changed()
        frame = await asyncio.to_thread(context.services.camera)
        decoded = await asyncio.to_thread(thumbnail, frame) if frame else None
        picture = decoded[0] if decoded else None
        guesses: Guesses | None = None
        if picture is not None:
            context.show(render_photo(picture, _SCREEN["looking"][language]))
            context.services.emotion("thinking")
            jpeg = await asyncio.to_thread(sized_jpeg, picture)
            asking = asyncio.get_running_loop().create_task(guess(jpeg, word, language, self._kids))
            await self._say("looking")
            try:
                guesses = await asking
            except VisionError as error:
                logger.warning("Scribble Rush vision model failed: %s", error)
        else:
            logger.warning("Scribble Rush: no camera frame")
        name = word.name(language)
        context.played()
        if guesses is None:
            await self._result(team, 0, "blind", name)
            return
        if guesses.remark:
            await context.say(guesses.remark)
        hits = await judge_guesses(guesses.guesses, word, context.services.jev)
        logger.info("Scribble Rush: %d guesses, hits %s", len(guesses.guesses), hits)
        for index, (said, hit) in enumerate(zip(guesses.guesses, hits)):
            last = index == len(guesses.guesses) - 1
            moment = "first" if index == 0 else "last" if last else "middle"
            self._said.append({"text": said, "hit": None})
            self._changed()
            await self._say(moment, guess=said)
            self._said[-1]["hit"] = hit
            if hit:
                await self._result(team, SQUARES[index], "hit", name)
                return
        await self._result(team, KIDS_MISS if self._kids else 0, "miss" if guesses.guesses else "no_drawing", name)

    async def _result(self, team: str, squares: int, moment: str, word: str) -> None:
        """Move ``team`` by ``squares``, show the track and say how it went."""
        context = self._context
        assert context is not None
        language = self._language
        name = team_name(team, language)
        self._squares[team] = min(GOAL, self._squares.get(team, 0) + squares)
        self._phase = "result"
        self._message = _SCREEN["moved"][language].format(team=name, squares=squares) if squares else None
        caption = _SCREEN["moved"][language].format(team=name, squares=squares)
        self._show_track(_SCREEN["was"][language].format(word=word.upper()), caption)
        self._changed()
        if moment == "hit":
            context.services.emotion("happy")
            one, many = _SQUARES[language]
            await self._say("hit", word=word, team=name, squares=one if squares == 1 else many.format(n=squares))
        else:
            context.services.emotion("sad")
            await self._say(moment, word=word)
            if squares:
                await self._say("kids_miss")
        if self._squares[team] >= GOAL:
            await self._say("finish", team=name)
        await context.sleep(RESULT_S)

    async def _ending(self, finished: str | None) -> None:
        context = self._context
        assert context is not None
        language = self._language
        self._phase, self._team, self._drawer, self._word = "over", None, None, None
        scores = {team: self._squares.get(team, 0) for team in self._teams_playing()}
        context.show(render_winner([(team_name(team, language), squares) for team, squares in scores.items()], language))  # fmt: skip
        self._changed()
        best = max(scores.values(), default=0)
        winners = [finished] if finished else [team for team, squares in scores.items() if squares == best]
        context.services.emotion("excited")
        if len(winners) == 1:
            await self._say("winner", team=team_name(winners[0], language))
        else:
            await self._say("tie")
        await self._say("thanks")
        logger.info("Scribble Rush over: %s", ", ".join(f"{team} {squares}" for team, squares in scores.items()))
