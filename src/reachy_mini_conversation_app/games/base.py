"""What a game sees of Reachy while it plays, and what every game provides.

A game is one coroutine (``Game.play``) that runs while the host says it may: ``GameContext.sleep`` and
``GameContext.playing`` hold it while the game is paused, so timers count playing time only. The host owns
starting, pausing, ending and putting things back; a game only plays.
"""

import time
import asyncio
from typing import Any, Callable, Optional, Protocol, Awaitable, Coroutine
from dataclasses import field, dataclass

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.games.catalog import GameInfo


# How long a game waits for one of its lines to be heard before going on without it.
SPEAK_TIMEOUT_S = 20.0
# What a question with its own short wait (``listen_s``) gets when someone spoke but the speech model wrote nothing
# (2026-11-01: "Pacific", 1.6 s, no text); silence gets "".
UNCLEAR_ANSWER = "…"
_TICK_S = 0.05


class SayAudio(Protocol):
    """Play recorded speech (rate, samples) as Reachy's line, then hand the next thing heard to ``answer``."""

    def __call__(
        self,
        rate: int,
        samples: NDArray[np.int16],
        text: str,
        language: str,
        answer: Optional[Callable[[str], Awaitable[object]]],
        *,
        listen_s: Optional[float] = None,
    ) -> Coroutine[Any, Any, None]:
        """Play the line; ``listen_s`` is how long the answer may take to start (Reachy's default when None)."""
        ...


JevAsk = Callable[[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes]], Awaitable[dict[str, dict[str, Any]]]]


@dataclass
class GameServices:
    """Reachy's parts a game host uses; main.py wires them, tests fake them."""

    # Say a line once Reachy is free and return once it has been heard (or SPEAK_TIMEOUT_S passed).
    speak: Callable[[str, str], Awaitable[None]]
    # Say a line, then hand the next thing heard (without the name) to the answer handler.
    ask: Callable[[str, str, Callable[[str], Awaitable[object]]], None]
    expect_answer: Callable[[Callable[[str], Awaitable[object]], str], None]
    # Play sound while Reachy waits for his name; False while he is busy.
    play_audio: Callable[[int, NDArray[np.int16]], bool]
    show: Callable[[bytes], None]
    # The screen before the game, and putting it back.
    save_screen: Callable[[], Callable[[], None]]
    quiet_head: Callable[[bool], None]
    touches: Callable[[], int]
    pause_music: Callable[[], Awaitable[None]]
    # Carry out a request as if just asked, after the game ended for it.
    hand_over: Callable[[str], None]
    camera: Callable[[], Optional[bytes]]
    jev: Optional[JevAsk]
    emotion: Callable[[str], None]
    # The kept drawing of a subject for a game's screen, or None when it is not made yet (never waits for one).
    picture: Optional[Callable[[str], Any]] = None
    # Make the drawings of these subjects that are not made yet (in the background, a few cents each, once).
    draw_pictures: Optional[Callable[[list[str]], Coroutine[Any, Any, None]]] = None
    # Reachy does a pose too: "up" or "left" with his head, "front" back to looking ahead.
    gesture: Optional[Callable[[str], Awaitable[None]]] = None
    # Play recorded speech (sample rate, samples, its text for the transcript, language) as Reachy's line once he
    # is free, and return once it has been heard; with an answer handler, listen for the next thing said.
    say_audio: Optional[SayAudio] = None
    # A call rings or is on.
    call_busy: Callable[[], bool] = lambda: False
    clock: Callable[[], float] = time.monotonic


@dataclass
class GameContext:
    """One game's view of Reachy; built by the host for each game."""

    info: GameInfo
    language: str
    services: GameServices
    # Set while the game may play; cleared while it is paused.
    running: asyncio.Event = field(default_factory=asyncio.Event)
    # Playing seconds so far: time spent paused is not counted.
    played_s: float = 0.0
    # The game's last screen, shown again when a pause ends.
    screen: bytes = b""
    # What the admin app shows ("Round 2 of 5, 1 star").
    progress: str = ""
    # When the game last saw someone play (a find, an answer); the host ends it after a long quiet.
    active_at: float = 0.0
    # Play on after a pause, as "sigue" would (a phone's Continue button); the host sets it.
    resume: Callable[[], None] = lambda: None
    # Start in kids mode (chosen in the admin app); the phone lobby can still change it.
    kids: bool = False
    # The address phones open to join (the QR code), for the admin app; empty for games without phones.
    join_url: str = ""
    # The game to start as this one ends ("no, ¡el tesoro!" after "¿Otra vez?"); the game sets it, the host starts it.
    next_game: str | None = None

    async def playing(self) -> None:
        """Return once the game is not paused."""
        await self.running.wait()

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` of playing time."""
        target = self.played_s + seconds
        while self.played_s < target:
            await self.playing()
            await asyncio.sleep(min(_TICK_S, target - self.played_s))

    async def say(self, text: str) -> None:
        """Say ``text`` and return once it has been heard."""
        await self.playing()
        await self.services.speak(text, self.language)

    def show(self, png: bytes) -> None:
        """Put the game's screen up (and remember it for after a pause)."""
        self.screen = png
        self.services.show(png)

    def played(self) -> None:
        """Note that someone is playing, so the game does not end for quiet."""
        self.active_at = self.services.clock()


class Game:
    """One game: its info, its fixed lines (cached by the reply voice), and how it plays."""

    info: GameInfo

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line the game says, in both languages, for the speech cache."""
        return frozenset()

    async def play(self, context: GameContext) -> None:
        """Play until the game is over; the host cancels it when it ends early."""
        raise NotImplementedError

    def heard(self, context: GameContext, text: str) -> bool:
        """Act on a room transcript (no name) meant for the game; False when it is not."""
        return False

    def paused(self, context: GameContext) -> None:
        """Stop anything playing now: the game was paused."""
