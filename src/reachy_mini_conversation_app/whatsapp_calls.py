"""WhatsApp calls through Reachy: the app's side of the local call service (``tools/whatsapp-calls``).

The service (``reachy-calls serve``) is a second linked device on the spare number. One WebSocket is one
call: the app asks for an approved person by name, then sends Reachy's mic and receives the other side,
both as 16 kHz mono 16-bit PCM. The service refuses anyone not on its approved list. See
``docs/plans/whatsapp-calls.md``.
"""

from __future__ import annotations
import json
import asyncio
import logging
from typing import Any, Callable, AsyncIterator
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import resample_audio, audio_to_mono_int16
from reachy_mini_conversation_app.whatsapp_contacts import Contact


logger = logging.getLogger(__name__)

CALL_SAMPLE_RATE = 16_000
# Mic audio waiting to be sent is capped (about 1 s at Reachy's frame size); older audio is dropped.
_SEND_QUEUE_FRAMES = 50
_RECEIVE_QUEUE_FRAMES = 50
# Why a call ended, as the service reports it.
ENDED_NO_ANSWER = "no_answer"
ENDED_DECLINED = "declined"
ENDED_FAILED = "failed"


def load_callees(path: str | Path, fold: Callable[[str], str]) -> list[Contact]:
    """Return the people Reachy may call, from the service's approved list (names and spoken names only).

    Each is a ``Contact`` keyed by its name, so ``find_contact`` can match what was said; the phone
    numbers in the file are left to the service.
    """
    file = Path(path)
    if not file.is_file():
        return []
    try:
        raw = json.loads(file.read_text())
    except (OSError, ValueError) as error:
        logger.warning("Approved callees unreadable: %s", type(error).__name__)
        return []
    entries = raw.get("approved") if isinstance(raw, dict) else None
    callees = []
    for entry in entries if isinstance(entries, list) else []:
        name = str(entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        if not name:
            continue
        names = [fold(str(said)) for said in entry.get("say") or [] if str(said).strip()]
        callees.append(Contact(name, "call:" + name, tuple(dict.fromkeys([*names, fold(name)]))))
    return callees


def call_pcm(frame: tuple[int, Any]) -> bytes:
    """Return one captured mic frame as 16 kHz mono 16-bit PCM bytes."""
    sample_rate, audio = frame
    audio = np.asarray(audio)
    if audio.ndim == 2 and audio.shape[0] >= audio.shape[1] > 1:
        audio = audio[:, 0]  # the first channel, as Gemini Live hears the room while Reachy talks
    mono = audio_to_mono_int16(audio).astype(np.float32) / 32768.0
    resampled = resample_audio(mono, int(sample_rate), CALL_SAMPLE_RATE)
    return (np.clip(resampled, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class CallLink:
    """One call through the service: Reachy's mic out, the other side in, and how it ended."""

    def __init__(self, socket: Any) -> None:
        """Wrap an open WebSocket on which the call request has been sent."""
        self._socket = socket
        self._outgoing: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_SEND_QUEUE_FRAMES)
        self._incoming: asyncio.Queue[NDArray[np.int16] | None] = asyncio.Queue(maxsize=_RECEIVE_QUEUE_FRAMES)
        self.connected = False
        self.reason: str | None = None
        self._tasks = [
            asyncio.ensure_future(self._receive_loop()),
            asyncio.ensure_future(self._send_loop()),
        ]

    @classmethod
    async def open(cls, url: str, token: str, name: str) -> CallLink:
        """Ask the service to call ``name``; raises OSError when the service cannot be reached."""
        import websockets

        try:
            socket = await websockets.connect(
                url, additional_headers={"Authorization": "Bearer " + token}, max_size=1 << 20, open_timeout=5
            )
        except (OSError, websockets.exceptions.WebSocketException, asyncio.TimeoutError) as error:
            raise OSError(f"call service unreachable: {type(error).__name__}") from error
        await socket.send(json.dumps({"op": "call", "name": name}))
        return cls(socket)

    def send_frame(self, frame: tuple[int, Any]) -> None:
        """Queue one captured mic frame for the call (nothing is sent before the other side answers)."""
        if not self.connected or self.reason is not None:
            return
        try:
            data = call_pcm(frame)
        except (TypeError, ValueError):
            return
        if self._outgoing.full():
            self._outgoing.get_nowait()  # the network fell behind: drop the oldest
        self._outgoing.put_nowait(data)

    async def received(self) -> AsyncIterator[NDArray[np.int16]]:
        """Yield the other side's audio until the call ends."""
        while True:
            chunk = await self._incoming.get()
            if chunk is None:
                return
            yield chunk

    async def hang_up(self) -> None:
        """End the call and close the socket."""
        try:
            await self._socket.send(json.dumps({"op": "hangup"}))
        except Exception:
            pass
        await self.close()

    async def close(self) -> None:
        """Close the socket (which hangs up) and stop the link's tasks."""
        try:
            await self._socket.close()
        except Exception:
            pass
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._finish(self.reason or ENDED_FAILED)

    def _finish(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        while True:
            try:
                self._incoming.put_nowait(None)
                return
            except asyncio.QueueFull:
                self._incoming.get_nowait()

    async def _receive_loop(self) -> None:
        try:
            async for message in self._socket:
                if isinstance(message, bytes):
                    if not self.connected:
                        continue
                    chunk = np.frombuffer(message, dtype="<i2").astype(np.int16)
                    if self._incoming.full():
                        self._incoming.get_nowait()  # playback fell behind: drop the oldest
                    self._incoming.put_nowait(chunk)
                    continue
                try:
                    event = json.loads(message)
                except ValueError:
                    continue
                kind = event.get("event") if isinstance(event, dict) else None
                if kind == "connected":
                    self.connected = True
                elif kind == "ended":
                    self._finish(str(event.get("reason") or ENDED_FAILED))
                    return
        except Exception as error:
            logger.info("Call link closed: %s", type(error).__name__)
        self._finish(ENDED_FAILED)

    async def _send_loop(self) -> None:
        try:
            while True:
                await self._socket.send(await self._outgoing.get())
        except Exception:
            return
