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
# Outgoing: while the room is quiet, Reachy's mic carries a faint hiss (his motors, or the mic's own noise) that
# the other phone heard as rustling (2026-10-30, about 0.01 RMS). Below GATE_OPEN_RMS it is turned down to
# GATE_FLOOR; it opens at once for anything louder and stays open GATE_HOLD_S after.
GATE_OPEN_RMS = 0.02
GATE_FLOOR = 0.1
GATE_HOLD_S = 0.4
# Incoming: shouting on the phone cut Reachy's speaker out (2026-10-30) though the audio arrived whole, so the
# other side plays at most LIMIT_PEAK; the level comes back up by LIMIT_RELEASE per chunk.
LIMIT_PEAK = 0.35
LIMIT_RELEASE = 0.05
# While it rings, Reachy plays a ringback tone (Portugal's: 425 Hz, 1 s on, 4 s off) so the room knows it is
# ringing and hears when it is answered (the tone stops). Made in RINGBACK_CHUNK_S pieces, played in real time.
RINGBACK_HZ = 425.0
RINGBACK_ON_S = 1.0
RINGBACK_PERIOD_S = 5.0
RINGBACK_LEVEL = 0.12
RINGBACK_CHUNK_S = 0.2
_RINGBACK_LEAD_CHUNKS = 2
_RINGBACK_EDGE_S = 0.01
# The request a call becomes when it is handed to the call command, in the conversation's language.
_CALL_REQUEST = {"en": "call {name}", "es": "llama a {name}", "it": "chiama {name}", "pt": "liga para {name}"}


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


def call_request(language: str, name: str) -> str:
    """Return the "call x" request (after his name) that the call command handles, in ``language``."""
    return "Reachy, " + _CALL_REQUEST.get(language, _CALL_REQUEST["en"]).format(name=name)


async def parent_in_front(service: Any, allowed: frozenset[str], look_s: float, interval_s: float) -> bool:
    """Return whether face recognition sees one of ``allowed`` (household person ids) within ``look_s``.

    One frame is not enough: just after waking his head is still rising, and a first look can be unsure
    (2026-10-30: 0.41, below the bar, then 0.51 a few seconds later).
    """
    if service is None or not allowed:
        return False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + look_s
    while True:
        try:
            matches = await asyncio.to_thread(service.identify_now)
        except RuntimeError:
            matches = []
        if any(match is not None and match.person_id in allowed for match in matches):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval_s)


def ringback(index: int) -> NDArray[np.int16]:
    """Return the ``index``-th RINGBACK_CHUNK_S piece of the ringback tone (16 kHz), soft at its edges."""
    size = round(RINGBACK_CHUNK_S * CALL_SAMPLE_RATE)
    t = (index * size + np.arange(size)) / CALL_SAMPLE_RATE
    phase = t % RINGBACK_PERIOD_S
    envelope = np.clip(np.minimum(phase, RINGBACK_ON_S - phase) / _RINGBACK_EDGE_S, 0.0, 1.0)
    tone = np.sin(2 * np.pi * RINGBACK_HZ * t) * envelope * RINGBACK_LEVEL
    chunk: NDArray[np.int16] = (tone * 32767).astype(np.int16)
    return chunk


class NoiseGate:
    """Turn the mic down while the room is quiet, so the hiss of an idle room is not sent."""

    def __init__(self) -> None:
        """Start closed."""
        self._gain = GATE_FLOOR
        self._open_for_s = 0.0

    def process(self, samples: NDArray[np.float32]) -> NDArray[np.float32]:
        """Return ``samples`` (16 kHz) with the gate applied, ramping the level across the chunk."""
        if samples.size == 0:
            return samples
        rms = float(np.sqrt(np.mean(samples**2)))
        if rms >= GATE_OPEN_RMS:
            self._open_for_s = GATE_HOLD_S
        else:
            self._open_for_s = max(0.0, self._open_for_s - samples.size / CALL_SAMPLE_RATE)
        target = 1.0 if self._open_for_s > 0 else GATE_FLOOR
        ramp = np.linspace(self._gain, target, samples.size, dtype=np.float32)
        self._gain = target
        return samples * ramp


class Limiter:
    """Keep the other side below LIMIT_PEAK, easing back up after a loud moment."""

    def __init__(self) -> None:
        """Start at full level."""
        self._gain = 1.0

    def process(self, chunk: NDArray[np.int16]) -> NDArray[np.int16]:
        """Return ``chunk`` turned down as needed."""
        if chunk.size == 0:
            return chunk
        peak = float(np.abs(chunk.astype(np.float32)).max()) / 32768.0
        wanted = min(1.0, LIMIT_PEAK / peak) if peak > 0 else 1.0
        gain = min(wanted, self._gain + LIMIT_RELEASE)
        ramp = np.linspace(self._gain, gain, chunk.size, dtype=np.float32)
        self._gain = gain
        if gain >= 1.0 and float(ramp.min()) >= 1.0:
            return chunk
        limited: NDArray[np.int16] = np.clip(chunk.astype(np.float32) * ramp, -32768, 32767).astype(np.int16)
        return limited


def call_pcm(frame: tuple[int, Any], gate: NoiseGate | None = None) -> bytes:
    """Return one captured mic frame as 16 kHz mono 16-bit PCM bytes (through ``gate`` when given)."""
    sample_rate, audio = frame
    audio = np.asarray(audio)
    if audio.ndim == 2 and audio.shape[0] >= audio.shape[1] > 1:
        audio = audio[:, 0]  # the first channel, as Gemini Live hears the room while Reachy talks
    mono = audio_to_mono_int16(audio).astype(np.float32) / 32768.0
    resampled = resample_audio(mono, int(sample_rate), CALL_SAMPLE_RATE)
    if gate is not None:
        resampled = gate.process(resampled)
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
        self._gate = NoiseGate()
        self._answered = asyncio.Event()  # answered, or ended before that: the ringback stops
        self._limiter = Limiter()
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
            data = call_pcm(frame, self._gate)
        except (TypeError, ValueError):
            return
        if self._outgoing.full():
            self._outgoing.get_nowait()  # the network fell behind: drop the oldest
        self._outgoing.put_nowait(data)

    async def received(self) -> AsyncIterator[NDArray[np.int16]]:
        """Yield a ringback tone until the other side answers, then their audio until the call ends."""
        index = 0
        while not self.connected and self.reason is None:
            yield ringback(index)
            index += 1
            if index < _RINGBACK_LEAD_CHUNKS:
                continue  # a little queued ahead, so the tone has no gaps
            try:
                await asyncio.wait_for(self._answered.wait(), RINGBACK_CHUNK_S)
            except asyncio.TimeoutError:
                pass
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
        self._answered.set()
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
                    chunk = self._limiter.process(np.frombuffer(message, dtype="<i2").astype(np.int16))
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
                    self._answered.set()
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
