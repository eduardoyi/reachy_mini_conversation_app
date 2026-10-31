"""What a game sees of Reachy while it plays, and what every game provides.

A game is one coroutine (``Game.play``) that runs while the host says it may: ``GameContext.sleep`` and
``GameContext.playing`` hold it while the game is paused, so timers count playing time only. The host owns
starting, pausing, ending and putting things back; a game only plays.
"""

import time
import asyncio
from typing import Any, Callable, Optional, Awaitable
from dataclasses import field, dataclass

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.games.catalog import GameInfo


# How long a game waits for one of its lines to be heard before going on without it.
SPEAK_TIMEOUT_S = 20.0
_TICK_S = 0.05

JevAsk = Callable[[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes]], Awaitable[dict[str, dict[str, Any]]]]


@dataclass
class GameServices:
    """Reachy's parts a game host uses; main.py wires them, tests fake them."""

    # Say a line once Reachy is free and return once it has been heard (or SPEAK_TIMEOUT_S passed).
    speak: Callable[[str, str], Awaitable[None]]
    # Say a line, then hand the next thing heard (without the name) to the answer handler.
    ask: Callable[[str, str, Callable[[str], Awaitable[object]]], None]
    expect_answer: Callable[[Callable[[str], Awaitable[object]]], None]
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
