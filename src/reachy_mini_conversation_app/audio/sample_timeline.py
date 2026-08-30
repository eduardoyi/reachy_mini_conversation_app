"""Sample-derived timing for realtime audio buffers."""

from typing import Protocol
from dataclasses import dataclass


NANOSECONDS_PER_SECOND = 1_000_000_000
DEFAULT_GAP_THRESHOLD_NS = 200_000_000


@dataclass(frozen=True, slots=True)
class SampleTiming:
    """Describe one contiguous or discontinuous sample-buffer timeline cue."""

    pts_ns: int
    dts_ns: int
    duration_ns: int
    discontinuity: bool


class _TimestampedBuffer(Protocol):
    pts: int
    dts: int
    duration: int

    def set_flags(self, flags: object) -> None:
        """Set native buffer flags."""


def apply_sample_timing(buffer: _TimestampedBuffer, timing: SampleTiming, discontinuity_flag: object) -> None:
    """Apply sample timing metadata to a native buffer-like object."""
    buffer.pts = timing.pts_ns
    buffer.dts = timing.dts_ns
    buffer.duration = timing.duration_ns
    if timing.discontinuity:
        buffer.set_flags(discontinuity_flag)


class SampleTimeline:
    """Generate contiguous sample timestamps while honoring real idle gaps."""

    def __init__(self, sample_rate: int, *, gap_threshold_ns: int = DEFAULT_GAP_THRESHOLD_NS) -> None:
        """Create a timeline for one fixed-rate audio stream."""
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")
        if isinstance(gap_threshold_ns, bool) or not isinstance(gap_threshold_ns, int) or gap_threshold_ns <= 0:
            raise ValueError("gap_threshold_ns must be a positive integer")
        self._sample_rate = sample_rate
        self._gap_threshold_ns = gap_threshold_ns
        self._anchor_pts_ns: int | None = None
        self._sample_count = 0

    @property
    def sample_rate(self) -> int:
        """Return the fixed sample rate used by this timeline."""
        return self._sample_rate

    def next_timing(self, *, sample_count: int, running_time_ns: int) -> SampleTiming:
        """Return timing whose duration and continuity derive from sample count."""
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
            raise ValueError("sample_count must be a positive integer")
        if isinstance(running_time_ns, bool) or not isinstance(running_time_ns, int) or running_time_ns < 0:
            raise ValueError("running_time_ns must be a non-negative integer")

        next_pts_ns = self._next_pts_ns
        discontinuity = self._anchor_pts_ns is None or (
            next_pts_ns is not None and running_time_ns > next_pts_ns + self._gap_threshold_ns
        )
        if discontinuity:
            self._anchor_pts_ns = running_time_ns
            self._sample_count = 0
        anchor_pts_ns = self._anchor_pts_ns
        if anchor_pts_ns is None:
            raise RuntimeError("sample timeline has no timestamp anchor")
        start_sample_count = self._sample_count
        end_sample_count = start_sample_count + sample_count
        pts_ns = anchor_pts_ns + self._scale_samples(start_sample_count)
        duration_ns = self._scale_samples(end_sample_count) - self._scale_samples(start_sample_count)
        self._sample_count = end_sample_count
        return SampleTiming(
            pts_ns=pts_ns,
            dts_ns=pts_ns,
            duration_ns=duration_ns,
            discontinuity=discontinuity,
        )

    def reset(self) -> None:
        """Reset continuity so the next buffer becomes a new first cue."""
        self._anchor_pts_ns = None
        self._sample_count = 0

    @property
    def _next_pts_ns(self) -> int | None:
        """Return the expected PTS for the next contiguous buffer."""
        if self._anchor_pts_ns is None:
            return None
        return self._anchor_pts_ns + self._scale_samples(self._sample_count)

    def _scale_samples(self, sample_count: int) -> int:
        """Convert a cumulative sample position to nanoseconds without drift."""
        return sample_count * NANOSECONDS_PER_SECOND // self._sample_rate


__all__ = ["SampleTiming", "SampleTimeline", "apply_sample_timing"]
