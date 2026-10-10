"""Tests for the robot microphone level probe."""

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_mini_conversation_app import mic_level_probe
from reachy_mini_conversation_app.mic_level_probe import (
    PROBE_PHASES,
    LevelSummary,
    LevelAccumulator,
    run_probe,
    probe_sound,
    frame_levels,
    format_report,
    suggest_threshold,
)


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now


class FakeMedia:
    """Deliver constant 20 ms stereo frames and advance the clock as audio arrives."""

    def __init__(self, clock: FakeClock, level: float = 0.1, fail_after: int | None = None) -> None:
        """Serve frames at a constant float level, optionally failing after a frame count."""
        self.clock = clock
        self.level = level
        self.fail_after = fail_after
        self.frames_served = 0
        self.pending_backlog = 3
        self.recording = False
        self.playing = False
        self.pushed: list[NDArray[np.float32]] = []
        self.stop_calls = 0
        self.play_starts = 0

    def start_recording(self) -> None:
        """Mark capture as started."""
        self.recording = True

    def stop_recording(self) -> None:
        """Mark capture as stopped."""
        self.recording = False
        self.stop_calls += 1

    def get_audio_sample(self) -> NDArray[np.float32] | None:
        """Return stale loud backlog first, then measured frames."""
        if self.pending_backlog:
            self.pending_backlog -= 1
            return np.full((320, 2), 0.9, dtype=np.float32)
        if self.fail_after is not None and self.frames_served >= self.fail_after:
            raise RuntimeError("capture sentinel")
        self.frames_served += 1
        self.clock.now += 0.02
        return np.full((320, 2), self.level, dtype=np.float32)

    def get_input_audio_samplerate(self) -> int:
        """Return the fake microphone rate."""
        return 16_000

    def start_playing(self) -> None:
        """Mark playback as started."""
        self.playing = True
        self.play_starts += 1

    def stop_playing(self) -> None:
        """Mark playback as stopped."""
        self.playing = False

    def push_audio_sample(self, data: NDArray[np.float32]) -> None:
        """Record a pushed playback chunk."""
        self.pushed.append(data)

    def get_output_audio_samplerate(self) -> int:
        """Return the fake speaker rate."""
        return 16_000


def test_frame_levels_match_provider_channel_handling() -> None:
    """Averaged and channel-0 levels use the same int16 scale as the VAD."""
    frame = np.zeros((320, 2), dtype=np.int16)
    frame[:, 0] = 1000

    mixed, channel0, samples = frame_levels(frame)

    assert mixed == pytest.approx(500)
    assert channel0 == pytest.approx(1000)
    assert samples == 320


def test_frame_levels_accept_channels_first_frames() -> None:
    """Channels-first frames are transposed like the Gemini input path."""
    frame = np.zeros((2, 320), dtype=np.int16)
    frame[0, :] = 800

    _, channel0, samples = frame_levels(frame)

    assert channel0 == pytest.approx(800)
    assert samples == 320


def test_accumulator_counts_onsets_with_provider_latch_rule() -> None:
    """Onsets need 120 ms above threshold and rearm only after 400 ms below it."""
    accumulator = LevelAccumulator(threshold=500)

    def frames(level: float, milliseconds: int) -> None:
        for _ in range(milliseconds // 20):
            accumulator.add(level, 0.02)

    frames(600, 100)
    assert accumulator.summary().onsets == 0
    frames(10, 20)
    frames(600, 140)
    assert accumulator.summary().onsets == 1
    frames(10, 200)
    frames(600, 140)
    assert accumulator.summary().onsets == 1
    frames(10, 400)
    frames(600, 140)

    summary = accumulator.summary()
    assert summary.onsets == 2
    assert summary.longest_run_ms == pytest.approx(140)
    assert summary.seconds == pytest.approx(1.14)
    assert summary.fraction_at_threshold == pytest.approx(0.52 / 1.14)


def test_accumulator_treats_zero_level_as_silence_at_zero_threshold() -> None:
    """A zero threshold still ignores digital silence, like RmsEnergyVAD."""
    accumulator = LevelAccumulator(threshold=0)
    for _ in range(10):
        accumulator.add(0.0, 0.02)

    assert accumulator.summary().onsets == 0
    assert accumulator.summary().fraction_at_threshold == 0


def test_empty_accumulator_reports_zeros() -> None:
    """A phase without frames produces an all-zero summary instead of failing."""
    assert LevelAccumulator(threshold=500).summary() == LevelSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)


def _summary(p75: float, p99: float) -> LevelSummary:
    return LevelSummary(10.0, p75 / 2, p75, p99, p99, 0.0, 0.0, 0)


def test_suggest_threshold_sits_between_noise_and_speech() -> None:
    """The suggestion is the geometric mean of the noise ceiling and the weakest speech level."""
    results = {
        "quiet": {"mixed": _summary(p75=50, p99=100)},
        "playback": {"mixed": _summary(p75=150, p99=200)},
        "speech": {"mixed": _summary(p75=1800, p99=4000)},
        "speech_over_playback": {"mixed": _summary(p75=800, p99=4000)},
    }

    assert suggest_threshold(results, "mixed") == pytest.approx(400)


def test_suggest_threshold_refuses_overlapping_levels() -> None:
    """Speech that is not clearly louder than noise yields no suggestion."""
    results = {
        "quiet": {"mixed": _summary(p75=300, p99=900)},
        "speech": {"mixed": _summary(p75=1000, p99=3000)},
    }

    assert suggest_threshold(results, "mixed") is None
    assert suggest_threshold({"quiet": results["quiet"]}, "mixed") is None


def test_probe_sound_is_bounded_and_phase_continuous() -> None:
    """Consecutive chunks join without a discontinuity and stay within the requested peak."""
    whole = probe_sound(16_000, 0.0, 0.2, 0.3)
    joined = np.concatenate([probe_sound(16_000, 0.0, 0.1, 0.3), probe_sound(16_000, 0.1, 0.1, 0.3)])

    assert whole.dtype == np.float32
    assert np.max(np.abs(whole)) <= 0.3
    np.testing.assert_allclose(joined, whole, atol=1e-5)


def test_run_probe_measures_each_phase_and_plays_only_in_playback_phases() -> None:
    """Every phase is summarized, stale frames are drained, and speaker use is bounded to playback phases."""
    clock = FakeClock()
    media = FakeMedia(clock, level=0.1)
    seen_phases: list[str] = []
    pushes_before_phase: list[int] = []

    def before_phase(phase: mic_level_probe.ProbePhase) -> None:
        seen_phases.append(phase.name)
        pushes_before_phase.append(len(media.pushed))
        media.pending_backlog = 3

    results = run_probe(
        media,
        phases=PROBE_PHASES,
        seconds=1.0,
        threshold=500,
        playback_level=0.3,
        before_phase=before_phase,
        clock=clock,
        sleep=lambda seconds: None,
    )

    assert seen_phases == [phase.name for phase in PROBE_PHASES]
    assert list(results) == seen_phases
    for by_signal in results.values():
        assert by_signal["mixed"].p50 == pytest.approx(0.1 * 32767, rel=1e-3)
        assert by_signal["mixed"].peak < 0.9 * 32767
        assert by_signal["mixed"].seconds == pytest.approx(1.0, abs=0.021)
    assert pushes_before_phase[0] == pushes_before_phase[1] == 0
    assert len(media.pushed) > pushes_before_phase[2]
    assert not media.recording and not media.playing


def test_run_probe_skips_speaker_without_playback_phases() -> None:
    """A capture-only run never starts or feeds the speaker."""
    clock = FakeClock()
    media = FakeMedia(clock)

    run_probe(
        media,
        phases=[phase for phase in PROBE_PHASES if not phase.playback],
        seconds=0.2,
        threshold=500,
        playback_level=0.3,
        before_phase=lambda phase: None,
        clock=clock,
        sleep=lambda seconds: None,
    )

    assert media.play_starts == 0
    assert not media.pushed


def test_run_probe_stops_media_when_capture_fails() -> None:
    """Recording and playback are stopped even when the SDK raises mid-phase."""
    clock = FakeClock()
    media = FakeMedia(clock, fail_after=5)

    with pytest.raises(RuntimeError, match="capture sentinel"):
        run_probe(
            media,
            phases=PROBE_PHASES,
            seconds=1.0,
            threshold=500,
            playback_level=0.3,
            before_phase=lambda phase: None,
            clock=clock,
            sleep=lambda seconds: None,
        )

    assert media.stop_calls == 1
    assert not media.playing


def test_format_report_lists_phases_and_suggestions() -> None:
    """The report includes every phase/signal row and a line per signal."""
    results = {
        "quiet": {"mixed": _summary(p75=50, p99=100), "channel0": _summary(p75=50, p99=100)},
        "speech": {"mixed": _summary(p75=1600, p99=3000), "channel0": _summary(p75=120, p99=300)},
    }

    report = format_report(results, 500)

    assert "Threshold 500" in report
    assert report.count("quiet ") == 2
    assert "mixed: candidate threshold about 400." in report
    assert "channel0: no clear separation" in report


def test_main_reports_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing daemon exits with status 2 instead of a traceback."""

    def refuse_connection(**kwargs: object) -> None:
        raise ConnectionError("no daemon")

    monkeypatch.setattr(mic_level_probe, "ReachyMini", refuse_connection)

    assert mic_level_probe.main(["--seconds", "1"]) == 2


def test_main_exits_cleanly_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C during a prompt returns the conventional interrupt status."""

    def cancel(**kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(mic_level_probe, "ReachyMini", cancel)

    assert mic_level_probe.main([]) == 130


def test_main_rejects_invalid_arguments() -> None:
    """Out-of-range playback levels are rejected before connecting."""
    with pytest.raises(SystemExit):
        mic_level_probe.main(["--playback-level", "2"])
