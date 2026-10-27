"""Short local clips of what the wake model hears, saved to retrain the "Reachy" wake word.

Off unless ``WAKE_CLIPS_ENABLED``. While Reachy listens locally for his name, the last few seconds of the
microphone audio the wake model hears are kept in memory. A WAV slice of it is written for a wake, a near
miss, a name that local STT heard but the wake model missed, and an occasional random background sample.
Clips stay on this computer, are deleted after the keep period, and no transcripts are saved.
"""

import os
import time
import wave
import random
import shutil
import logging
import threading
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import deque
from collections.abc import Callable


logger = logging.getLogger(__name__)

BUFFER_SECONDS = 12.0
EVENT_CLIP_SECONDS = 4.0
# A name-heard clip is cut from the end of the buffer: the utterance plus the time local STT took to report it.
NAME_HEARD_MARGIN_S = 3.0
NAME_HEARD_MAX_SECONDS = 10.0
# Background samples come at random, on average this often, so the model also learns what is not his name.
BACKGROUND_EVERY_S = 600.0


def wake_clips_directory() -> Path:
    """Return the clip folder beside the household memory file (``$XDG_DATA_HOME`` aware)."""
    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_mini_conversation_app" / "wake-clips"


class WakeClipRecorder:
    """Keeps a rolling buffer of mono PCM16 and saves WAV slices of it into one folder per day."""

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
        self._sample_rate: int | None = None
        self._next_background_at = clock() + self._background_gap()
        self._pruned_on: date | None = None

    def feed(self, pcm: bytes, sample_rate: int) -> None:
        """Add mono PCM16 to the rolling buffer; saves a background sample when one is due."""
        with self._lock:
            if sample_rate != self._sample_rate:
                self._chunks.clear()
                self._buffered_bytes = 0
                self._sample_rate = sample_rate
            self._chunks.append(pcm)
            self._buffered_bytes += len(pcm)
            limit = int(BUFFER_SECONDS * sample_rate) * 2
            while self._buffered_bytes - len(self._chunks[0]) >= limit:
                self._buffered_bytes -= len(self._chunks.popleft())
        now = self._clock()
        if now >= self._next_background_at:
            self._next_background_at = now + self._background_gap()
            self._save("background", EVENT_CLIP_SECONDS)

    def clear(self) -> None:
        """Forget the buffered audio, e.g. when local listening starts over after a conversation."""
        with self._lock:
            self._chunks.clear()
            self._buffered_bytes = 0

    def save_wake_event(self, score: float, detected: bool) -> None:
        """Save the seconds around an acoustic wake or near miss (the detector's event observer)."""
        self._save("wake" if detected else "near_miss", EVENT_CLIP_SECONDS, score)

    def save_name_heard(self, speech_seconds: float) -> None:
        """Save an utterance in which local STT heard the name but the wake model did not wake."""
        self._save("name_heard", min(NAME_HEARD_MAX_SECONDS, speech_seconds + NAME_HEARD_MARGIN_S))

    def _background_gap(self) -> float:
        return self._rng.uniform(0.5, 1.5) * BACKGROUND_EVERY_S

    def _save(self, kind: str, seconds: float, score: float | None = None) -> None:
        with self._lock:
            sample_rate = self._sample_rate
            audio = b"".join(self._chunks)
        if sample_rate is None or not audio:
            return
        audio = audio[-int(seconds * sample_rate) * 2 :]
        now = datetime.fromtimestamp(self._clock())
        folder = self._directory / now.date().isoformat()
        name = f"{now:%H%M%S}{now.microsecond // 1000:03d}-{kind}" + (f"-{score:.2f}" if score is not None else "")
        path = folder / f"{name}.wav"
        try:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as clip:
                clip.setnchannels(1)
                clip.setsampwidth(2)
                clip.setframerate(sample_rate)
                clip.writeframes(audio)
            path.chmod(0o600)
        except OSError as error:
            logger.warning("Wake clip not saved: %s", type(error).__name__)
            return
        logger.info(
            "Wake clip saved kind=%s score=%s seconds=%.1f",
            kind,
            "-" if score is None else f"{score:.2f}",
            len(audio) / 2 / sample_rate,
        )
        self._prune(now.date())

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
