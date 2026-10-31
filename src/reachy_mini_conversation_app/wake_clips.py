"""Local clips and a record of wake events, kept to measure and retrain the "Reachy" wake word.

Off unless ``WAKE_CLIPS_ENABLED``. While Reachy listens locally (for his name, or for a request after it), the
last 75 s of the microphone audio the wake model hears are kept in memory. Each wake-model fire, near miss, shadow
fire, transcript wake, antenna wake and "missed me" mark adds a line to the day's ``events.jsonl`` and saves a WAV
slice of the buffer around it (fires keep 1.5 s after). What came of a fire (the name check, the length of the
request) is added to the same file under its id. Near-miss clips are rate-limited, and a random background
sample is saved now and then. Clips stay on this computer, are deleted after the keep period, and no transcripts
are saved.
"""

import os
import json
import time
import wave
import random
import shutil
import logging
import threading
from typing import Final
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import deque
from dataclasses import dataclass
from collections.abc import Callable

from reachy_mini_conversation_app.acoustic_wake_word import WakeScoreEvent


logger = logging.getLogger(__name__)

BUFFER_SECONDS: Final[float] = 75.0
EVENTS_FILE: Final[str] = "events.jsonl"
# Seconds kept before each kind of event: a fire keeps 10 s so a call that had to be repeated is in its clip;
# "missed me" keeps the minute before.
SECONDS_BEFORE: Final[dict[str, float]] = {
    "wake": 10.0,
    "shadow": 4.0,
    "near_miss": 4.0,
    "background": 4.0,
    "antenna": 15.0,
    "missed": 60.0,
}
FIRE_SECONDS_AFTER: Final[float] = 1.5
# A name-heard clip is cut from the end of the buffer: the utterance plus the time local STT took to report it.
NAME_HEARD_MARGIN_S: Final[float] = 3.0
NAME_HEARD_MAX_SECONDS: Final[float] = 10.0
# Background samples come at random, on average this often, so the model also learns what is not his name.
BACKGROUND_EVERY_S: Final[float] = 600.0
# A steady noise can make thousands of near misses a day (2026-10-30: 7,990); the event is always logged.
NEAR_MISS_CLIP_GAP_S: Final[float] = 30.0


def wake_clips_directory() -> Path:
    """Return the clip folder beside the household memory file (``$XDG_DATA_HOME`` aware)."""
    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_mini_conversation_app" / "wake-clips"


def make_private_dir(path: Path) -> None:
    """Create ``path`` and any missing parent folders, each readable only by its owner."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700, exist_ok=True)


@dataclass
class _PendingClip:
    path: Path
    start: int
    end: int


class WakeClipRecorder:
    """Keeps a rolling buffer of mono PCM16, saves WAV slices of it and logs wake events, one folder per day."""

    def __init__(
        self,
        directory: Path,
        *,
        keep_days: int,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        """Save into ``directory`` and delete day folders older than ``keep_days``."""
        self._directory = directory
        self._keep_days = keep_days
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._chunks: deque[bytes] = deque()
        self._buffered_bytes = 0
        self._fed_bytes = 0  # since the buffer last started over; the buffer holds the newest _buffered_bytes
        self._sample_rate: int | None = None
        self._pending: list[_PendingClip] = []
        self._capture: list[bytes] | None = None
        self._next_background_at = clock() + self._background_gap()
        self._last_near_miss_clip = float("-inf")
        self._pruned_on: date | None = None

    def feed(self, pcm: bytes, sample_rate: int, *, ambient: bool = True) -> None:
        """Add mono PCM16 to the rolling buffer; a background sample is saved when one is due and ``ambient``."""
        with self._lock:
            ready = []
            if sample_rate != self._sample_rate:
                ready = self._take_pending(everything=True)
                self._restart(sample_rate)
            self._chunks.append(pcm)
            self._buffered_bytes += len(pcm)
            self._fed_bytes += len(pcm)
            if self._capture is not None:
                self._capture.append(pcm)
            limit = int(BUFFER_SECONDS * sample_rate) * 2
            while self._buffered_bytes - len(self._chunks[0]) >= limit:
                self._buffered_bytes -= len(self._chunks.popleft())
            ready += self._take_pending(everything=False)
        self._write(ready)
        now = self._clock()
        if ambient and now >= self._next_background_at:
            self._next_background_at = now + self._background_gap()
            self.record("background")

    def flush(self) -> None:
        """Save clips still waiting for their last seconds with what has arrived (a conversation starts)."""
        with self._lock:
            ready = self._take_pending(everything=True)
        self._write(ready)

    def clear(self) -> None:
        """Save clips still waiting for their last seconds, then forget the buffer (listening starts over)."""
        with self._lock:
            ready = self._take_pending(everything=True)
            self._restart(self._sample_rate)
        self._write(ready)

    def record(
        self, kind: str, *, seconds_before: float | None = None, ended_ago_s: float = 0.0, **details: object
    ) -> str:
        """Log a ``kind`` event that happened ``ended_ago_s`` ago and save its clip; return the event's id.

        ``details`` are scores, heads and flags only, never words. Fires and shadow fires keep 1.5 s after the
        event, so their clip is written once that audio has arrived.
        """
        now = self._clock()
        stamp = datetime.fromtimestamp(now - ended_ago_s)
        day = stamp.date().isoformat()
        event_id = f"{day}/{stamp:%H%M%S}{stamp.microsecond // 1000:03d}-{kind}"
        before = SECONDS_BEFORE.get(kind, 4.0) if seconds_before is None else seconds_before
        after = FIRE_SECONDS_AFTER if kind in ("wake", "shadow") else 0.0
        clip: str | None = None
        event_at: float | None = None
        ready: list[tuple[Path, bytes, int]] = []
        with self._lock:
            rate = self._sample_rate
            throttled = kind == "near_miss" and now - self._last_near_miss_clip < NEAR_MISS_CLIP_GAP_S
            if rate is not None and self._buffered_bytes and not throttled:
                if kind == "near_miss":
                    self._last_near_miss_clip = now
                event_pos = self._fed_bytes - _bytes(ended_ago_s, rate)
                start = max(self._fed_bytes - self._buffered_bytes, event_pos - _bytes(before, rate))
                if event_pos > start:
                    path = self._directory / f"{event_id}.wav"
                    clip, event_at = f"{event_id}.wav", (event_pos - start) / 2 / rate
                    self._pending.append(_PendingClip(path, start, event_pos + _bytes(after, rate)))
                    ready = self._take_pending(everything=False)
        self._write(ready)
        self._append(day, {"id": event_id, "t": round(now - ended_ago_s, 3), "kind": kind, "clip": clip,
                           "event_at": event_at, **details})  # fmt: skip
        return event_id

    def save_score_event(self, event: WakeScoreEvent) -> None:
        """Log a near miss or a shadow head's fire (the wake detector's event observer) with its clip."""
        scores = {name: round(raw, 4) for name, raw in event.scores.items()}
        self.record(event.kind, score=round(event.score, 4), head=event.head, scores=scores)

    def note(self, event_id: str, **details: object) -> None:
        """Add what came of an event (a name check, a request's length) under its id; flags and counts only."""
        self._append(event_id.split("/", 1)[0], {"id": event_id, "t": round(self._clock(), 3), **details})

    def save_name_heard(self, speech_seconds: float) -> str:
        """Log an utterance in which local STT heard the name but the wake model did not wake, with its clip."""
        return self.record(
            "name_heard",
            seconds_before=min(NAME_HEARD_MAX_SECONDS, speech_seconds + NAME_HEARD_MARGIN_S),
            speech_seconds=round(speech_seconds, 2),
        )

    def begin_capture(self) -> None:
        """Start collecting every chunk fed from now on (a recording of the name for the household's voices)."""
        with self._lock:
            self._capture = []

    def captured(self) -> tuple[int | None, bytes]:
        """Return the sample rate and the audio fed since ``begin_capture``."""
        with self._lock:
            return self._sample_rate, b"".join(self._capture or ())

    def end_capture(self) -> None:
        """Stop collecting."""
        with self._lock:
            self._capture = None

    def _restart(self, sample_rate: int | None) -> None:
        self._chunks.clear()
        self._buffered_bytes = 0
        self._fed_bytes = 0
        self._sample_rate = sample_rate
        if self._capture is not None:
            self._capture.clear()

    def _take_pending(self, *, everything: bool) -> list[tuple[Path, bytes, int]]:
        """Slice out the clips whose audio has all arrived (or every clip, cut short), with their sample rate."""
        rate = self._sample_rate
        due = [clip for clip in self._pending if everything or clip.end <= self._fed_bytes]
        if not due or rate is None:
            return []
        self._pending = [clip for clip in self._pending if not (everything or clip.end <= self._fed_bytes)]
        audio = b"".join(self._chunks)
        offset = self._fed_bytes - len(audio)
        return [
            (clip.path, audio[max(0, clip.start - offset) : min(clip.end, self._fed_bytes) - offset], rate)
            for clip in due
        ]

    def _write(self, ready: list[tuple[Path, bytes, int]]) -> None:
        for path, audio, rate in ready:
            if not audio:
                continue
            try:
                make_private_dir(path.parent)
                with wave.open(str(path), "wb") as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(rate)
                    out.writeframes(audio)
                path.chmod(0o600)
            except OSError as error:
                logger.warning("Wake clip not saved: %s", type(error).__name__)
                continue
            logger.info("Wake clip saved %s seconds=%.1f", path.stem, len(audio) / 2 / rate)
        if ready:
            self._prune(datetime.fromtimestamp(self._clock()).date())

    def _append(self, day: str, entry: dict[str, object]) -> None:
        path = self._directory / day / EVENTS_FILE
        try:
            make_private_dir(path.parent)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")
        except OSError as error:
            logger.warning("Wake event not logged: %s", type(error).__name__)

    def _background_gap(self) -> float:
        return self._rng.uniform(0.5, 1.5) * BACKGROUND_EVERY_S

    def _prune(self, today: date) -> None:
        """Delete day folders past the keep period, at most once a day."""
        if self._pruned_on == today:
            return
        self._pruned_on = today
        oldest_kept = today - timedelta(days=self._keep_days)
        for folder in self._directory.iterdir():
            try:
                day = date.fromisoformat(folder.name)
            except ValueError:
                continue
            if not folder.is_dir() or day >= oldest_kept:
                continue
            try:
                shutil.rmtree(folder)
            except OSError as error:
                logger.warning("Old wake clips not deleted: %s", type(error).__name__)
                continue
            logger.info("Deleted wake clips from %s", folder.name)


def _bytes(seconds: float, sample_rate: int) -> int:
    return int(seconds * sample_rate) * 2
