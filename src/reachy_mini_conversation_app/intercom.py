"""The admin app's intercom: listen to the room through Reachy's mic, and talk through his speaker.

One phone at a time. While it is connected the room goes only to that phone (no wake word, room transcript or
cloud), and a chime plays before each time someone talks. Nothing is recorded.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Final
from collections.abc import AsyncIterator

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.whatsapp_calls import CALL_SAMPLE_RATE, call_pcm


logger = logging.getLogger(__name__)

INTERCOM_SAMPLE_RATE: Final[int] = CALL_SAMPLE_RATE
# Frames waiting for a slow phone; the oldest are dropped so what it hears stays live.
HEARD_QUEUE_FRAMES: Final[int] = 25
# The phone sends its voice in small pieces; a talk ends if none comes for this long (a dropped connection).
TALK_GAP_S: Final[float] = 2.0
# One piece of the phone's voice at most (16-bit samples): a second.
MAX_TALK_CHUNK_BYTES: Final[int] = INTERCOM_SAMPLE_RATE * 2


def chime() -> NDArray[np.int16]:
    """Return a soft falling two-note chime ("someone is about to talk") at ``INTERCOM_SAMPLE_RATE``."""
    notes = []
    for frequency, seconds in ((1046.5, 0.16), (784.0, 0.32)):
        time_s = np.arange(int(INTERCOM_SAMPLE_RATE * seconds)) / INTERCOM_SAMPLE_RATE
        envelope = np.minimum(1.0, np.minimum(time_s, time_s[-1] - time_s) / 0.01) * np.exp(-time_s * 6.0)
        notes.append(np.sin(2 * np.pi * frequency * time_s) * envelope * 0.3 * 32767)
    return np.concatenate(notes).astype(np.int16)


def talk_samples(chunk: bytes) -> NDArray[np.int16] | None:
    """Return one piece of the phone's voice (16 kHz mono 16-bit PCM) as samples, or None when malformed."""
    if not chunk or len(chunk) % 2 or len(chunk) > MAX_TALK_CHUNK_BYTES:
        return None
    return np.frombuffer(chunk, dtype="<i2").astype(np.int16)


class IntercomSession:
    """One phone's link: Reachy's mic to it, and its voice to his speaker while someone holds to talk.

    ``loop`` is the loop the phone's connection runs on; the runtime calls ``hear`` from its own loop.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bridge the runtime's loop to the phone connection's ``loop``."""
        self._loop = loop
        self._heard: asyncio.Queue[tuple[int, Any]] = asyncio.Queue(maxsize=HEARD_QUEUE_FRAMES)

    def hear(self, frame: tuple[int, Any]) -> None:
        """Take one captured mic frame for the phone; thread-safe."""
        try:
            self._loop.call_soon_threadsafe(self._keep, frame)
        except RuntimeError:  # the phone's loop has closed
            logger.debug("Intercom frame dropped: connection loop closed")

    def _keep(self, frame: tuple[int, Any]) -> None:
        if self._heard.full():
            self._heard.get_nowait()
        self._heard.put_nowait(frame)

    async def heard(self) -> AsyncIterator[bytes]:
        """Yield what Reachy hears as 16 kHz mono 16-bit PCM, live, until cancelled."""
        while True:
            frame = await self._heard.get()
            yield call_pcm(frame)


class Talk:
    """The phone's voice for one hold of the talk button, played on the runtime's ``loop``."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Queue pieces onto ``loop``, where the runtime plays them."""
        self._loop = loop
        self._pieces: asyncio.Queue[NDArray[np.int16] | None] = asyncio.Queue()

    def say(self, samples: NDArray[np.int16]) -> None:
        """Queue one piece of the voice; thread-safe."""
        self._loop.call_soon_threadsafe(self._pieces.put_nowait, samples)

    def end(self) -> None:
        """End this talk once what was queued has played; thread-safe."""
        try:
            self._loop.call_soon_threadsafe(self._pieces.put_nowait, None)
        except RuntimeError:
            logger.debug("Intercom talk end dropped: runtime loop closed")

    async def pieces(self) -> AsyncIterator[NDArray[np.int16]]:
        """Yield the voice until the talk ends or goes quiet for ``TALK_GAP_S`` (runtime loop)."""
        while True:
            try:
                piece = await asyncio.wait_for(self._pieces.get(), TALK_GAP_S)
            except TimeoutError:
                logger.info("Intercom talk ended: nothing came for %.0fs", TALK_GAP_S)
                return
            if piece is None:
                return
            yield piece
