"""Tests for the continuous log of the wake model's input features."""

from pathlib import Path
from datetime import datetime

import numpy as np

from reachy_mini_conversation_app.wake_learning.feature_log import FRAME_S, WakeFeatureLog, read_runs


START = datetime(2026, 10, 30, 10, 0, 0).timestamp()


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        """Start mid-morning."""
        self.now = START

    def __call__(self) -> float:
        """Return the time."""
        return self.now


def _frame(value: float) -> np.ndarray:
    return np.full(96, value, dtype=np.float32)


def test_frames_are_kept_as_runs_that_a_gap_splits(tmp_path: Path) -> None:
    """Frames 80 ms apart join one run; a pause (a conversation) starts the next, with its own start time."""
    clock = Clock()
    log = WakeFeatureLog(tmp_path, keep_days=30, clock=clock)
    for index in range(40):
        clock.now = START + (index + 1) * FRAME_S
        log.add(_frame(index))
    clock.now += 5.0
    for index in range(25):
        clock.now += FRAME_S
        log.add(_frame(100 + index))

    runs = read_runs(tmp_path, "2026-10-30")

    assert [len(run.frames) for run in runs] == [40, 25]
    assert abs(runs[1].start - (START + 40 * FRAME_S + 5.0)) < 1e-3
    first = runs[0]
    assert abs(first.start - START) < 1e-3
    assert first.frames.dtype == np.float16 and first.frames[3, 0] == 3
    windows, ends = first.windows()
    assert windows.shape == (25, 16, 96) and windows[0, -1, 0] == 15
    assert abs(ends[0] - (START + 16 * FRAME_S)) < 1e-3
    assert np.array_equal(first.window_at(np.array([0, 24])), windows[[0, 24]])
    assert (tmp_path / "2026-10-30").stat().st_mode & 0o777 == 0o700


def test_old_days_are_deleted(tmp_path: Path) -> None:
    """Days past the keep period go when the log next writes."""
    (tmp_path / "2026-09-01").mkdir()
    (tmp_path / "2026-09-01" / "100000000.f16").write_bytes(b"\x00" * 192)
    (tmp_path / "2026-10-29").mkdir()
    clock = Clock()
    log = WakeFeatureLog(tmp_path, keep_days=30, clock=clock)
    for _ in range(25):
        clock.now += FRAME_S
        log.add(_frame(1.0))

    assert sorted(path.name for path in tmp_path.iterdir()) == ["2026-10-29", "2026-10-30"]
