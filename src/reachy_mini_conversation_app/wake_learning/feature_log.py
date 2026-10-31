"""A continuous log of what the wake model hears while Reachy listens for his name: its input features, not audio.

openWakeWord turns each 80 ms of sound into 96 numbers; a wake head reads the last 16 of them. Logging them all
lets the nightly job count how often any head would fire over whole days (and learn from every hour of room
sound), which saved clips alone cannot show. A run file holds float16 frames back to back from its start time;
a gap (dropped audio, a conversation) or a new hour starts the next file. About 9 MB an hour of listening.
"""

import os
import time
import logging
import threading
from typing import Final
from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import dataclass
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

FRAME_S: Final[float] = 0.08
FEATURES: Final[int] = 96
WINDOW_FRAMES: Final[int] = 16
SUFFIX: Final[str] = ".f16"
# Frames arriving this much later than expected (dropped audio) start a new run, so windows never span a gap.
GAP_S: Final[float] = 0.5
FLUSH_FRAMES: Final[int] = 25


@dataclass(frozen=True)
class FeatureRun:
    """Consecutive frames from ``start`` (Unix time), one every 80 ms."""

    start: float
    frames: NDArray[np.float16]

    def windows(self, stride: int = 1) -> tuple[NDArray[np.float16], NDArray[np.float64]]:
        """Return each full 16-frame window a head would read (every ``stride`` frames, a view) and when it ends."""
        if len(self.frames) < WINDOW_FRAMES:
            return np.zeros((0, WINDOW_FRAMES, FEATURES), np.float16), np.zeros(0)
        view = np.lib.stride_tricks.sliding_window_view(self.frames, WINDOW_FRAMES, axis=0)[::stride]
        starts = np.arange(len(self.frames) - WINDOW_FRAMES + 1)[::stride]
        return view.transpose(0, 2, 1), (self.start + (starts + WINDOW_FRAMES) * FRAME_S).astype(np.float64)

    def window_at(self, indices: NDArray[np.intp]) -> NDArray[np.float16]:
        """Return the windows (stride 1) at ``indices`` as one array."""
        return (
            np.stack([self.frames[index : index + WINDOW_FRAMES] for index in indices])
            if len(indices)
            else (np.zeros((0, WINDOW_FRAMES, FEATURES), np.float16))
        )


class WakeFeatureLog:
    """Appends the wake model's feature frames to run files, one folder per day, deleted after ``keep_days``."""

    def __init__(self, directory: Path, *, keep_days: int, clock: Callable[[], float] = time.time) -> None:
        """Write under ``directory``."""
        self._directory = directory
        self._keep_days = keep_days
        self._clock = clock
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._run_start = 0.0
        self._frames = 0
        self._hour = -1
        self._buffer: list[bytes] = []
        self._pruned_on: date | None = None

    def add(self, frame: NDArray[np.float32]) -> None:
        """Log one 80 ms frame (96 numbers); a gap or a new hour starts a new run file."""
        if frame.shape != (FEATURES,):
            raise ValueError("Expected 96 features")
        now = self._clock()
        with self._lock:
            expected = self._run_start + (self._frames + 1) * FRAME_S
            hour = datetime.fromtimestamp(now).hour
            if self._path is None or now - expected > GAP_S or hour != self._hour:
                self._flush()
                stamp = datetime.fromtimestamp(now - FRAME_S)
                name = f"{stamp:%H%M%S}{stamp.microsecond // 1000:03d}{SUFFIX}"
                self._path = self._directory / stamp.date().isoformat() / name
                self._run_start, self._frames, self._hour = now - FRAME_S, 0, hour
            self._buffer.append(frame.astype("<f2").tobytes())
            self._frames += 1
            if len(self._buffer) >= FLUSH_FRAMES:
                self._flush()

    def _flush(self) -> None:
        if not self._buffer or self._path is None:
            self._buffer.clear()
            return
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "ab") as file:
                file.write(b"".join(self._buffer))
        except OSError as error:
            logger.warning("Wake features not logged: %s", type(error).__name__)
        self._buffer.clear()
        self._prune(datetime.fromtimestamp(self._run_start).date())

    def _prune(self, today: date) -> None:
        if self._pruned_on == today:
            return
        self._pruned_on = today
        oldest_kept = today - timedelta(days=self._keep_days)
        for folder in self._directory.iterdir():
            try:
                day = date.fromisoformat(folder.name)
            except ValueError:
                continue
            if day >= oldest_kept or not folder.is_dir():
                continue
            for path in folder.glob(f"*{SUFFIX}"):
                path.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError as error:
                logger.warning("Old wake features not deleted: %s", type(error).__name__)


def read_runs(directory: Path, day: str) -> list[FeatureRun]:
    """Return the day's runs in time order."""
    runs = []
    for path in sorted((directory / day).glob(f"*{SUFFIX}")):
        try:
            stamp = datetime.strptime(f"{day} {path.stem}", "%Y-%m-%d %H%M%S%f")
            values = np.fromfile(path, dtype="<f2")
        except (OSError, ValueError) as error:
            logger.warning("Wake features %s unreadable: %s", path.name, type(error).__name__)
            continue
        frames = values[: len(values) // FEATURES * FEATURES].reshape(-1, FEATURES)
        runs.append(FeatureRun(stamp.timestamp(), frames))
    return runs
