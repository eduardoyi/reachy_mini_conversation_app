"""A pretend Reachy for game tests: it records what games say and show, and answers as the test sets up."""

import io
import time
import asyncio
from typing import Any, Callable, Awaitable
from collections import deque

import numpy as np
from PIL import Image
from numpy.typing import NDArray

from reachy_mini_conversation_app.games.base import GameServices


Answer = Callable[[str], Awaitable[object]]


class FakeReachy:
    """Game services that record everything; ``now`` is the clock unless ``real_clock``."""

    def __init__(self, *, real_clock: bool = False) -> None:
        """Start with nothing said or shown, at a fixed time unless ``real_clock``."""
        self.now = 1000.0
        self.said: list[str] = []
        self.asked: list[tuple[str, Answer]] = []
        self.expected: list[Answer] = []
        self.audio: list[NDArray[np.int16]] = []
        self.screens: list[bytes] = []
        self.quiet: list[bool] = []
        self.pats = 0
        self.music_paused = 0
        self.handed: list[str] = []
        self.emotions: list[str] = []
        self.restored = 0
        self.busy = False
        self.frames: deque[bytes] = deque()
        self.last_frame: bytes | None = None
        self.jev_calls = 0
        self.jev_answer: Callable[[], dict[str, dict[str, Any]]] | None = None
        self.services = GameServices(
            speak=self._speak,
            ask=lambda text, language, answer: (self.said.append(text), self.asked.append((text, answer))),
            expect_answer=self.expected.append,
            play_audio=lambda rate, samples: self.audio.append(samples) is None,
            show=self.screens.append,
            save_screen=self._save_screen,
            quiet_head=self.quiet.append,
            touches=lambda: self.pats,
            pause_music=self._pause_music,
            hand_over=self.handed.append,
            camera=self._camera,
            jev=self._jev,
            emotion=self.emotions.append,
            call_busy=lambda: self.busy,
            clock=time.monotonic if real_clock else (lambda: self.now),
        )

    async def _speak(self, text: str, language: str) -> None:
        self.said.append(text)

    def _save_screen(self) -> Callable[[], None]:
        def restore() -> None:
            self.restored += 1

        return restore

    async def _pause_music(self) -> None:
        self.music_paused += 1

    def _camera(self) -> bytes | None:
        if self.frames:
            self.last_frame = self.frames.popleft()
        return self.last_frame

    async def _jev(self, state: dict[str, Any], questions: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        self.jev_calls += 1
        assert "frame" in images and set(questions) == {"held", "color"}
        assert self.jev_answer is not None
        return self.jev_answer()


def frame(seed: int) -> bytes:
    """Return a camera frame of noise, different for each ``seed``."""
    pixels = np.random.default_rng(seed).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    data = io.BytesIO()
    Image.fromarray(pixels).save(data, format="JPEG")
    return data.getvalue()


def seen(color: str, held: float = 0.9, probability: float = 0.9) -> dict[str, dict[str, Any]]:
    """Jev's answers for a frame showing ``color`` held up."""
    return {
        "held": {"type": "noul", "noul": held},
        "color": {"type": "choice", "choice": color, "probabilities": {color: probability}},
    }


async def ticks(seconds: float = 0.15) -> None:
    """Let the host's watch loop (every 0.05 s) run a few times."""
    await asyncio.sleep(seconds)
