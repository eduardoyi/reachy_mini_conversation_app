"""Bounded timing and level summaries for supervised local audio comparisons."""

import json
import logging
import threading
from typing import Literal


AudioStage = Literal["capture_pull", "capture_gap", "queue_age", "input_forward", "playback_push", "loop_lag"]
logger = logging.getLogger(__name__)


class AudioDiagnostics:
    """Aggregate timing and level metadata without retaining media or individual observations."""

    def __init__(self, loud_rms_threshold: float = 500.0) -> None:
        """Initialize fixed-size per-stage accumulators."""
        self._lock = threading.Lock()
        self._timings: dict[AudioStage, tuple[int, float, float]] = {}
        self._loud_rms_threshold = loud_rms_threshold
        self._levels: tuple[int, int, float] = (0, 0, 0.0)

    def observe(self, stage: AudioStage, seconds: float) -> None:
        """Add one nonnegative duration to the current reporting window."""
        milliseconds = max(0.0, seconds * 1000)
        with self._lock:
            count, total, maximum = self._timings.get(stage, (0, 0.0, 0.0))
            self._timings[stage] = (count + 1, total + milliseconds, max(maximum, milliseconds))

    def observe_input_level(self, rms: float) -> None:
        """Add one forwarded microphone frame's int16 RMS to the current window."""
        with self._lock:
            count, loud, maximum = self._levels
            self._levels = (count + 1, loud + int(rms >= self._loud_rms_threshold), max(maximum, rms))

    def report(self, *, captured: int, dropped: int, server_idle_s: float | None = None) -> None:
        """Log and reset interval summaries alongside cumulative capture counters."""
        with self._lock:
            timings = self._timings
            self._timings = {}
            frames, loud, max_rms = self._levels
            self._levels = (0, 0, 0.0)
        summary = {
            stage: {"count": count, "mean_ms": round(total / count, 2), "max_ms": round(maximum, 2)}
            for stage, (count, total, maximum) in timings.items()
        }
        levels = {"frames": frames, "loud": loud, "max_rms": round(max_rms)}
        idle = "n/a" if server_idle_s is None else f"{server_idle_s:.1f}"
        logger.info(
            "Audio diagnostics captured=%d dropped=%d timings=%s input_level=%s server_idle_s=%s",
            captured,
            dropped,
            json.dumps(summary),
            json.dumps(levels),
            idle,
        )
