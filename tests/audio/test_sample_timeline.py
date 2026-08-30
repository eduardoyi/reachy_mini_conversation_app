"""Deterministic sample-clock tests for app-owned native audio."""

from reachy_mini_conversation_app.audio.sample_timeline import SampleTiming, SampleTimeline, apply_sample_timing


class FakeGstBuffer:
    """Minimal buffer surface used to test native metadata without GI."""

    def __init__(self) -> None:
        """Initialize unset timing fields and flags."""
        self.pts = -1
        self.dts = -1
        self.duration = -1
        self.flags: list[object] = []

    def set_flags(self, flags: object) -> None:
        """Record one native flag."""
        self.flags.append(flags)


def test_first_sample_anchors_to_running_time_and_marks_discontinuity() -> None:
    """The first cue uses the supplied running time and exact sample duration."""
    timeline = SampleTimeline(16_000)

    cue = timeline.next_timing(sample_count=320, running_time_ns=123_456_789)

    assert cue.pts_ns == 123_456_789
    assert cue.dts_ns == 123_456_789
    assert cue.duration_ns == 20_000_000
    assert cue.discontinuity


def test_burst_arrivals_preserve_contiguous_sample_clock() -> None:
    """Fast arrival of chunks cannot compress their playback timeline."""
    timeline = SampleTimeline(16_000)

    first = timeline.next_timing(sample_count=320, running_time_ns=1_000_000_000)
    second = timeline.next_timing(sample_count=320, running_time_ns=1_001_000_000)
    third = timeline.next_timing(sample_count=160, running_time_ns=1_002_000_000)

    assert second.pts_ns == first.pts_ns + first.duration_ns
    assert third.pts_ns == second.pts_ns + second.duration_ns
    assert second.duration_ns == 20_000_000
    assert third.duration_ns == 10_000_000
    assert not second.discontinuity
    assert not third.discontinuity


def test_long_buffer_arriving_at_its_end_stays_contiguous() -> None:
    """A one-second buffer arriving at its expected end is not an idle gap."""
    timeline = SampleTimeline(16_000)

    first = timeline.next_timing(sample_count=16_000, running_time_ns=0)
    second = timeline.next_timing(sample_count=320, running_time_ns=1_000_000_000)

    assert first.duration_ns == 1_000_000_000
    assert second.pts_ns == 1_000_000_000
    assert not second.discontinuity


def test_real_gap_reanchors_next_sample_and_marks_discontinuity() -> None:
    """A gap beyond the threshold starts a new running-time segment."""
    timeline = SampleTimeline(16_000)

    first = timeline.next_timing(sample_count=320, running_time_ns=2_000_000_000)
    gap_running_time_ns = 2_020_000_001 + 200_000_000
    second = timeline.next_timing(sample_count=160, running_time_ns=gap_running_time_ns)

    assert second.pts_ns == gap_running_time_ns
    assert second.dts_ns == gap_running_time_ns
    assert second.duration_ns == 10_000_000
    assert second.discontinuity
    assert first.pts_ns + first.duration_ns != second.pts_ns


def test_reset_starts_a_new_discontinuous_segment() -> None:
    """Flush/start reset the pure timeline's prior continuity state."""
    timeline = SampleTimeline(24_000)
    timeline.next_timing(sample_count=480, running_time_ns=3_000_000_000)

    timeline.reset()
    cue = timeline.next_timing(sample_count=240, running_time_ns=4_000_000_000)

    assert cue.pts_ns == 4_000_000_000
    assert cue.duration_ns == 10_000_000
    assert cue.discontinuity


def test_fractional_rate_cumulative_durations_do_not_drift() -> None:
    """One-sample 44.1 kHz chunks sum to exactly one second."""
    timeline = SampleTimeline(44_100)

    cues = [timeline.next_timing(sample_count=1, running_time_ns=0) for _ in range(44_100)]

    assert sum(cue.duration_ns for cue in cues) == 1_000_000_000
    assert cues[-1].pts_ns + cues[-1].duration_ns == 1_000_000_000


def test_gst_buffer_contract_applies_timing_and_discontinuity_without_gi() -> None:
    """The native buffer metadata contract is testable with a tiny fake object."""
    buffer = FakeGstBuffer()
    timing = SampleTiming(pts_ns=5, dts_ns=5, duration_ns=20, discontinuity=True)

    apply_sample_timing(buffer, timing, "DISCONT")

    assert buffer.pts == 5
    assert buffer.dts == 5
    assert buffer.duration == 20
    assert buffer.flags == ["DISCONT"]
