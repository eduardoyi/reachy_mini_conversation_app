"""A local record of each cloud conversation, to find out why Reachy sometimes answers nonsense.

Off unless ``CONVERSATION_RECORD_ENABLED``. Each conversation gets two files in the day's folder: a
``<time>.jsonl`` of what happened and a ``<time>.wav`` of the microphone audio the provider was sent. The
record says how the conversation started (the wake, the request as local speech recognition heard it, the
request clip, the room context the provider was given), each user turn as the provider transcribed it, each
reply with whether it was cut off and after how long, the tools it called and how it ended. Files are
readable only by their owner, stay on this computer and are deleted after the keep period
(docs/features/conversation-record.md).
"""

import os
import json
import time
import wave
import shutil
import logging
import threading
from typing import Any, Final
from pathlib import Path
from datetime import date, datetime, timedelta
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.wake_clips import make_private_dir


logger = logging.getLogger(__name__)

RECORD_SUFFIX: Final[str] = ".jsonl"
AUDIO_SUFFIX: Final[str] = ".wav"


def conversation_record_directory() -> Path:
    """Return the record folder beside the wake clips (``$XDG_DATA_HOME`` aware)."""
    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_mini_conversation_app" / "conversations"


class ConversationRecorder:
    """Opens one record per conversation, one folder per day, and deletes days past the keep period."""

    def __init__(
        self,
        directory: Path,
        *,
        keep_days: int,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        """Save into ``directory`` and delete day folders older than ``keep_days``."""
        self._directory = directory
        self._keep_days = keep_days
        self._clock = clock
        self._now = now
        self._pruned_on: date | None = None

    def start(self, **details: object) -> "ConversationRecord | None":
        """Open a record for a conversation starting now; ``details`` describe how it started.

        Returns None when the folder cannot be written: a record must never stop a conversation.
        """
        stamp = self._now()
        folder = self._directory / stamp.date().isoformat()
        try:
            make_private_dir(folder)
            self._prune(stamp.date())
            base = folder / stamp.strftime("%H%M%S")
            suffix = 1
            while base.with_suffix(RECORD_SUFFIX).exists():
                suffix += 1
                base = folder / f"{stamp.strftime('%H%M%S')}-{suffix}"
            record = ConversationRecord(base, clock=self._clock)
        except OSError as error:
            logger.warning("Conversation record not started: %s", type(error).__name__)
            return None
        record.write("start", at=stamp.isoformat(timespec="seconds"), **details)
        return record

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
                logger.warning("Old conversation records not deleted: %s", type(error).__name__)
                continue
            logger.info("Deleted conversation records from %s", folder.name)


class ConversationRecord:
    """One conversation's events and audio; every method is safe to call after ``close``."""

    def __init__(self, base: Path, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Create ``base``.jsonl (owner-only); the audio file is made with the first frame."""
        self._base = base
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        descriptor = os.open(base.with_suffix(RECORD_SUFFIX), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._events = os.fdopen(descriptor, "a", encoding="utf-8")
        self._audio: wave.Wave_write | None = None
        self._audio_file: Any = None
        self._audio_rate: int | None = None
        self._audio_samples = 0
        self._audio_failed = False
        self._closed = False
        self._heard: list[str] = []
        self._heard_at = 0.0
        self._said: list[str] = []
        self._said_at = 0.0

    @property
    def closed(self) -> bool:
        """Return whether the record has been closed."""
        return self._closed

    def write(self, event: str, **details: object) -> None:
        """Add one event line, stamped with the seconds since the conversation started."""
        with self._lock:
            self._write_locked(event, details)

    def heard(self, piece: str) -> None:
        """Add a piece of what the provider heard the user say (pieces carry their own spacing)."""
        if not piece:
            return
        with self._lock:
            if not self._heard:
                self._heard_at = self._elapsed()
            self._heard.append(piece)

    def said(self, piece: str) -> None:
        """Add a piece of the reply; its first piece ends what the user said."""
        if not piece:
            return
        with self._lock:
            self._flush_heard_locked()
            if not self._said:
                self._said_at = self._elapsed()
            self._said.append(piece)

    def cut_off(self) -> None:
        """Record the reply so far as cut off: the provider stopped it because it heard speech."""
        with self._lock:
            reply_s = round(self._elapsed() - self._said_at, 1) if self._said else None
            self._flush_said_locked(cut_off=True)
            self._write_locked("cut_off", {"into_reply_s": reply_s})

    def tool(self, name: str) -> None:
        """Record a tool call by name (its arguments are not kept)."""
        with self._lock:
            self._flush_heard_locked()
            self._write_locked("tool", {"name": name})

    def turn_complete(self) -> None:
        """End the provider's turn: write what the user said, then the reply."""
        with self._lock:
            self._flush_heard_locked()
            self._flush_said_locked(cut_off=False)

    def audio(self, sample_rate: int, samples: NDArray[np.int16]) -> None:
        """Append mono PCM16 the provider was sent; frames at another rate than the first are skipped."""
        with self._lock:
            if self._closed or self._audio_failed or not samples.size:
                return
            try:
                if self._audio is None:
                    descriptor = os.open(
                        self._base.with_suffix(AUDIO_SUFFIX), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                    )
                    self._audio_file = os.fdopen(descriptor, "wb")
                    audio = wave.open(self._audio_file, "wb")
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(sample_rate)
                    self._audio, self._audio_rate = audio, sample_rate
                if sample_rate != self._audio_rate:
                    return
                self._audio.writeframes(samples.astype("<i2", copy=False).tobytes())
                self._audio_samples += samples.size
            except (OSError, wave.Error) as error:
                logger.warning("Conversation audio not recorded: %s", type(error).__name__)
                self._audio_failed = True  # once: the rest of the conversation keeps its text record
                self._close_audio_locked()

    def close(self, reason: str) -> None:
        """Write what is still pending and how the conversation ended, then close the files."""
        with self._lock:
            if self._closed:
                return
            self._flush_heard_locked()
            self._flush_said_locked(cut_off=False, finished=False)
            audio_s = round(self._audio_samples / self._audio_rate, 1) if self._audio_rate else 0.0
            self._write_locked("end", {"reason": reason, "audio_s": audio_s})
            self._closed = True
            self._close_audio_locked()
            try:
                self._events.close()
            except OSError as error:
                logger.warning("Conversation record not closed: %s", type(error).__name__)

    def _elapsed(self) -> float:
        return self._clock() - self._started

    def _flush_heard_locked(self) -> None:
        if self._heard:
            text = "".join(self._heard).strip()
            self._heard = []
            if text:
                self._write_locked("heard", {"text": text}, at=self._heard_at)

    def _flush_said_locked(self, *, cut_off: bool, finished: bool = True) -> None:
        if self._said:
            text = "".join(self._said).strip()
            self._said = []
            if text:
                details: dict[str, object] = {"text": text, "cut_off": cut_off}
                if not finished:
                    details["unfinished"] = True
                self._write_locked("said", details, at=self._said_at)

    def _write_locked(self, event: str, details: dict[str, Any], *, at: float | None = None) -> None:
        if self._closed:
            return
        elapsed = self._elapsed() if at is None else at
        line = json.dumps({"t": round(elapsed, 1), "event": event, **details}, ensure_ascii=False, default=str)
        try:
            self._events.write(line + "\n")
            self._events.flush()
        except (OSError, ValueError) as error:
            logger.warning("Conversation event not recorded: %s", type(error).__name__)

    def _close_audio_locked(self) -> None:
        audio, self._audio = self._audio, None
        audio_file, self._audio_file = self._audio_file, None
        try:
            if audio is not None:
                audio.close()  # writes the final header; the file it was given stays open
        except (OSError, wave.Error) as error:
            logger.warning("Conversation audio not closed: %s", type(error).__name__)
        finally:
            if audio_file is not None:
                audio_file.close()
