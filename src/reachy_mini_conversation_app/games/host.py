"""The game host: what every game shares, from the question before it starts to putting things back after.

A game starts only after a yes: "¿Jugamos a los colores?" heard as a quick command, answered "sí" (or with a
pat on his head); through Gemini, its own question is the yes. While a game runs it owns the screen and the
speaker (music is paused), other announcements wait, and every request to Reachy reaches the host first:
"pausa" / "seguimos" / "se acabó el juego" heard without the name, his name alone (or an antenna) pauses and
asks "¿Seguimos o terminamos?", and anything else asks whether to end the game, then does it. The screen
before the game comes back when it ends.
"""

import enum
import asyncio
import logging
from typing import Any, Final, Callable, Awaitable

from reachy_mini_conversation_app.games.base import Game, GameContext, GameServices
from reachy_mini_conversation_app.eink_display import render_game_paused
from reachy_mini_conversation_app.games.catalog import (
    GAMES,
    NO_WORDS,
    YES_WORDS,
    QUIT_WORDS,
    PAUSE_WORDS,
    RESUME_WORDS,
    GameInfo,
    GameRequest,
    words,
    find_game,
    parse_game_request,
)
from reachy_mini_conversation_app.quick_commands import QuickReply


logger = logging.getLogger(__name__)

# The start question is open this long for a yes or a pat.
ASK_TIMEOUT_S: Final[float] = 30.0
# "¿Lo terminamos?" with no answer: the game goes on after this long.
END_QUESTION_RESUME_S: Final[float] = 20.0
# A game nobody plays ends after this long; a paused one after PAUSED_END_S.
IDLE_END_S: Final[float] = 300.0
PAUSED_END_S: Final[float] = 600.0
# Holding the head still lapses after moves.HEAD_HOLD_MAX_S (180 s); it is renewed well before.
HOLD_RENEW_S: Final[float] = 60.0
_TICK_S: Final[float] = 0.05

_ASK: Final[dict[str, str]] = {"es": "¿Jugamos {title}? {pitch}", "en": "Shall we play {title}? {pitch}"}
_MENU: Final[dict[str, str]] = {
    "es": "¿A qué jugamos? Puedo jugar {titles}.",
    "en": "What shall we play? I know {titles}.",
}
_NOT_NOW: Final[dict[str, str]] = {"es": "Vale, otro día.", "en": "Okay, another time."}
_PAUSED_ASK: Final[dict[str, str]] = {"es": "Pausa. ¿Seguimos o terminamos?", "en": "Paused. Keep playing, or stop?"}
_PAUSED: Final[dict[str, str]] = {"es": "Pausa.", "en": "Paused."}
_RESUMED: Final[dict[str, str]] = {"es": "¡Seguimos!", "en": "Let's keep going!"}
_ENDED: Final[dict[str, str]] = {"es": "Vale, se acabó el juego.", "en": "Okay, game over."}
_END_FOR: Final[dict[str, str]] = {"es": "Estamos jugando {title}. ¿Lo terminamos?", "en": "We're playing {title}. Shall we stop?"}  # fmt: skip


class GameState(enum.Enum):
    """Where the host is: no game, asking whether to play, playing, or paused."""

    IDLE = "idle"
    ASKING = "asking"
    PLAYING = "playing"
    PAUSED = "paused"


def _titles(language: str) -> str:
    titles = [game.title[language] for game in GAMES.values()]
    joiner = " y " if language == "es" else " and "
    return titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + joiner + titles[-1]


def host_phrases() -> frozenset[str]:
    """Return the host's fixed lines in both languages, for the speech cache."""
    lines: set[str] = set()
    for language in ("es", "en"):
        lines |= {_NOT_NOW[language], _PAUSED_ASK[language], _PAUSED[language], _RESUMED[language], _ENDED[language]}
        lines.add(_MENU[language].format(titles=_titles(language)))
        for game in GAMES.values():
            lines.add(_ASK[language].format(title=game.title[language], pitch=game.pitch[language]))
            lines.add(_END_FOR[language].format(title=game.title[language]))
    return frozenset(lines)


class GameHost:
    """Runs one game at a time and everything around it."""

    def __init__(self, services: GameServices, games: dict[str, Callable[[], Game]]) -> None:
        """Build games from ``games`` (key to factory) and drive Reachy through ``services``."""
        self._services = services
        self._factories = games
        self._state = GameState.IDLE
        self._offered: GameInfo | None = None
        self._language = "es"
        self._asked_at = 0.0
        self._game: Game | None = None
        self._context: GameContext | None = None
        self._task: asyncio.Task[None] | None = None
        self._watch: asyncio.Task[None] | None = None
        self._restore_screen: Callable[[], None] | None = None
        self._head_held = False
        self._paused_at = 0.0
        self._resume_at: float | None = None
        self._background: set[asyncio.Task[Any]] = set()

    # State ---------------------------------------------------------------------------------------

    @property
    def state(self) -> GameState:
        """The host's state now."""
        return self._state

    @property
    def active(self) -> bool:
        """Whether a game is playing or paused (it owns the screen, speaker and requests)."""
        return self._state in (GameState.PLAYING, GameState.PAUSED)

    def holding(self) -> bool:
        """Whether other announcements should wait: a game is playing."""
        return self._state is GameState.PLAYING

    def status(self) -> dict[str, Any]:
        """Return what the admin app shows: the state, the game and its progress, and the games there are."""
        context = self._context
        return {
            "state": self._state.value,
            "game": context.info.key if context is not None and self.active else None,
            "progress": context.progress if context is not None and self.active else "",
            "games": [
                {"key": game.key, "title": game.title} for game in GAMES.values() if game.key in self._factories
            ],
        }

    # Starting ------------------------------------------------------------------------------------

    def offer(self, request: GameRequest) -> QuickReply:
        """Ask whether to play the requested game (or which one); the yes, a pat or a name starts it."""
        language = request.language
        game = request.game if request.game is not None and request.game.key in self._factories else None
        self._offered, self._language, self._asked_at = game, language, self._services.clock()
        self._set_state(GameState.ASKING)
        if game is None:
            text = _MENU[language].format(titles=_titles(language))
        else:
            text = _ASK[language].format(title=game.title[language], pitch=game.pitch[language])
        logger.info("Game offered: %s", game.key if game is not None else "which game")

        async def listen() -> str | None:
            self._services.expect_answer(self._start_answer)
            return None

        return QuickReply(text, language, "game", after=listen)

    async def _start_answer(self, text: str) -> QuickReply:
        """Take the answer to the start question: yes starts it, a game's name starts that one, no drops it."""
        return self._answer_start(words(text))

    def _answer_start(self, said: str) -> QuickReply:
        language = self._language
        if self._state is not GameState.ASKING:
            return QuickReply(None, language, "game")
        request = parse_game_request(said)
        named = request.game if request is not None else find_game(said)
        if named is not None and named.key in self._factories:
            self.start(named.key, language)
        elif said in YES_WORDS and self._offered is not None:
            self.start(self._offered.key, language)
        elif said in NO_WORDS:
            self._set_state(GameState.IDLE)
            return QuickReply(_NOT_NOW[language], language, "game")
        else:
            logger.info("Game not started: the answer was neither yes nor a game")
            self._set_state(GameState.IDLE)
        return QuickReply(None, language, "game")

    def start(self, key: str, language: str) -> str | None:
        """Start game ``key`` now (a yes, Gemini or the admin app); return why it cannot start instead."""
        factory = self._factories.get(key)
        if factory is None:
            return f"There is no game {key!r}."
        if self.active:
            self.end("another game", say=False)
        game = factory()
        services = self._services
        context = GameContext(info=game.info, language=language, services=services)
        context.active_at = services.clock()
        self._game, self._context, self._language = game, context, language
        self._restore_screen = services.save_screen()
        self._spawn(services.pause_music(), "game-pause-music")
        self._set_state(GameState.PLAYING)
        self._task = asyncio.get_running_loop().create_task(self._play(game, context), name=f"game-{key}")
        logger.info("Game started: %s (%s)", key, language)
        return None

    async def _play(self, game: Game, context: GameContext) -> None:
        try:
            await game.play(context)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Game %s failed", context.info.key)
        if self._context is context:
            logger.info("Game over: %s", context.info.key)
            self._finish()

    # While playing -------------------------------------------------------------------------------

    def heard(self, text: str) -> bool:
        """Offer a room transcript (said without the name); True when the game or the host acted on it."""
        said = words(text)
        if self._state is GameState.ASKING:
            if said in YES_WORDS or said in NO_WORDS or find_game(said) is not None:
                reply = self._answer_start(said)
                if reply.text:
                    self._say(reply.text)
                return True
            return False
        context, game = self._context, self._game
        if context is None or game is None or not self.active:
            return False
        if said in QUIT_WORDS:
            self.end("asked")
        elif said in PAUSE_WORDS:
            if self._state is GameState.PLAYING:
                self.pause()
                self._say(_PAUSED[self._language])
        elif said in RESUME_WORDS:
            if self._state is GameState.PAUSED:
                self.resume(say=True)
        elif self._state is GameState.PLAYING and game.heard(context, text):
            context.played()
        else:
            return False
        return True

    async def addressed(self, text: str) -> QuickReply | None:
        """Answer a request to Reachy by name (or his name alone) while a game runs; None when no game does."""
        said = words(text)
        language = self._language
        if self._state is GameState.ASKING:
            return self._answer_start(said)
        if not self.active:
            return None
        if self._state is GameState.PLAYING:
            self.pause()
        if said in QUIT_WORDS:
            self.end("asked", say=False)
            return QuickReply(_ENDED[language], language, "game")
        if said in RESUME_WORDS or said in YES_WORDS:
            self.resume()
            return QuickReply(_RESUMED[language], language, "game")
        if said in PAUSE_WORDS:
            return QuickReply(_PAUSED[language], language, "game")
        if not said or said in NO_WORDS:
            return self._question(_PAUSED_ASK[language], self._pause_answer)
        assert self._context is not None
        title = self._context.info.title[language]
        self._resume_at = self._services.clock() + END_QUESTION_RESUME_S

        async def end_for(answer: str) -> QuickReply:
            return self._end_for(words(answer), text)

        return self._question(_END_FOR[language].format(title=title), end_for)

    def _question(self, text: str, answer: Callable[[str], Awaitable[object]]) -> QuickReply:
        async def listen() -> str | None:
            self._services.expect_answer(answer)
            return None

        return QuickReply(text, self._language, "game", after=listen)

    async def _pause_answer(self, text: str) -> QuickReply:
        """Take the answer to "¿Seguimos o terminamos?"; anything else leaves the game paused."""
        said, language = words(text), self._language
        if self._state is not GameState.PAUSED:
            return QuickReply(None, language, "game")
        if said in RESUME_WORDS or said in YES_WORDS:
            self.resume()
            return QuickReply(_RESUMED[language], language, "game")
        if said in QUIT_WORDS or said in ("terminamos", "terminar", "stop", "para", "acabamos", "we stop"):
            self.end("asked", say=False)
            return QuickReply(_ENDED[language], language, "game")
        return QuickReply(None, language, "game")

    def _end_for(self, said: str, request: str) -> QuickReply:
        """Take the answer to "¿Lo terminamos?": yes ends the game and carries out ``request``; no plays on."""
        language = self._language
        self._resume_at = None
        if self._state is not GameState.PAUSED:
            return QuickReply(None, language, "game")
        if said in YES_WORDS or said in QUIT_WORDS:
            self.end("another request", say=False)
            self._services.hand_over(request)
            return QuickReply(None, language, "game")
        self.resume()
        return QuickReply(_RESUMED[language], language, "game")

    def pause(self) -> None:
        """Pause the game: its sound stops, the screen says so, timers stop, the head waits for a pat."""
        if self._state is not GameState.PLAYING or self._context is None or self._game is None:
            return
        self._paused_at = self._services.clock()
        self._set_state(GameState.PAUSED)
        self._game.paused(self._context)
        self._services.show(render_game_paused(self._context.info.title[self._language], self._language))
        logger.info("Game paused: %s", self._context.info.key)

    def resume(self, *, say: bool = False) -> None:
        """Play on from where the game was paused."""
        if self._state is not GameState.PAUSED or self._context is None:
            return
        self._resume_at = None
        self._context.played()
        self._set_state(GameState.PLAYING)
        if self._context.screen:
            self._services.show(self._context.screen)
        if say:
            self._say(_RESUMED[self._language])
        logger.info("Game resumed: %s", self._context.info.key)

    def end(self, reason: str, *, say: bool = True) -> None:
        """End the game now; the screen goes back to what it was, and Reachy listens as usual."""
        if not self.active or self._context is None:
            if self._state is GameState.ASKING:
                self._set_state(GameState.IDLE)
            return
        logger.info("Game ended: %s (%s)", self._context.info.key, reason)
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        language = self._language
        self._finish()
        if say:
            self._say(_ENDED[language])

    def _finish(self) -> None:
        """Put back what the game took and go idle."""
        restore, self._restore_screen = self._restore_screen, None
        self._set_state(GameState.IDLE)
        self._game = None
        if restore is not None:
            try:
                restore()
            except Exception as error:
                logger.warning("Screen not restored after the game: %s", type(error).__name__)

    # Plumbing ------------------------------------------------------------------------------------

    def _set_state(self, state: GameState) -> None:
        self._state = state
        context = self._context
        if context is not None:
            if state is GameState.PLAYING:
                context.running.set()
            else:
                context.running.clear()
        # The head is held still (no breathing, no speech wobble) only while a pat means something.
        self._hold_head(state in (GameState.ASKING, GameState.PAUSED))
        if state is not GameState.IDLE and (self._watch is None or self._watch.done()):
            self._watch = asyncio.get_running_loop().create_task(self._watch_loop(), name="game-watch")

    def _hold_head(self, hold: bool) -> None:
        if hold != self._head_held:
            self._head_held = hold
            self._services.quiet_head(hold)

    def _say(self, text: str) -> None:
        self._spawn(self._services.speak(text, self._language), "game-say")

    def _spawn(self, work: Awaitable[Any], name: str) -> None:
        async def run() -> None:
            try:
                await work
            except Exception as error:
                logger.warning("%s failed: %s", name, type(error).__name__)

        task = asyncio.get_running_loop().create_task(run(), name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _watch_loop(self) -> None:
        """Pats, playing time, a ringing call, and the quiet that ends a game, every tick until idle."""
        services = self._services
        seen = services.touches()
        held_at = services.clock()
        last = services.clock()
        while self._state is not GameState.IDLE:
            await asyncio.sleep(_TICK_S)
            now = services.clock()
            context = self._context
            if self._state is GameState.PLAYING and context is not None:
                context.played_s += now - last
            last = now
            touches = services.touches()
            patted = touches > seen
            seen = touches
            if self._head_held and now - held_at >= HOLD_RENEW_S:
                services.quiet_head(True)
                held_at = now
            if self._state is GameState.ASKING:
                if patted and self._offered is not None:
                    logger.info("Game started by a pat")
                    self.start(self._offered.key, self._language)
                elif now - self._asked_at >= ASK_TIMEOUT_S:
                    logger.info("Game not started: no answer")
                    self._set_state(GameState.IDLE)
                continue
            if context is None:
                continue
            if self._state is GameState.PLAYING:
                if services.call_busy():
                    self.pause()
                elif now - context.active_at >= IDLE_END_S:
                    self.end("nobody playing")
            elif self._state is GameState.PAUSED:
                if patted and not services.call_busy():
                    self.resume(say=True)
                elif self._resume_at is not None and now >= self._resume_at:
                    self.resume()
                elif now - self._paused_at >= PAUSED_END_S:
                    self.end("paused too long", say=False)
