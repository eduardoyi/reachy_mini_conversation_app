"""Bounded timing summaries for supervised local audio comparisons."""

import json
import logging
import threading
from typing import Literal


AudioStage = Literal["capture_pull", "capture_gap", "queue_age", "input_forward", "playback_push", "loop_lag"]
logger = logging.getLogger(__name__)


class AudioDiagnostics:
    """Aggregate timing metadata without retaining media or individual observations."""

    def __init__(self) -> None:
        """Initialize fixed-size per-stage accumulators."""
        self._lock = threading.Lock()
        self._timings: dict[AudioStage, tuple[int, float, float]] = {}

    def observe(self, stage: AudioStage, seconds: float) -> None:
        """Add one nonnegative duration to the current reporting window."""
        milliseconds = max(0.0, seconds * 1000)
        with self._lock:
            count, total, maximum = self._timings.get(stage, (0, 0.0, 0.0))
            self._timings[stage] = (count + 1, total + milliseconds, max(maximum, milliseconds))

    def report(self, *, captured: int, dropped: int) -> None:
        """Log and reset interval timings alongside cumulative capture counters."""
        with self._lock:
            timings = self._timings
            self._timings = {}
        summary = {
            stage: {"count": count, "mean_ms": round(total / count, 2), "max_ms": round(maximum, 2)}
            for stage, (count, total, maximum) in timings.items()
        }
        logger.info("Audio diagnostics captured=%d dropped=%d timings=%s", captured, dropped, json.dumps(summary))
