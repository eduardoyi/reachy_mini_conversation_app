"""Tests for the guided acoustic wake calibration."""

import numpy as np
import pytest

from reachy_mini_conversation_app import wake_calibration
from reachy_mini_conversation_app.wake_calibration import (
    StepResult,
    CalibrationStep,
    score_events,
    default_steps,
    format_report,
    run_calibration,
)
from reachy_mini_conversation_app.acoustic_wake_word import OpenWakeWordDetector


class ScriptedModel:
    """Return scripted wake scores, one per 80 ms model frame."""

    def __init__(self, scores: list[float]) -> None:
        """Store the score script."""
        self.scores = list(scores)
        self.resets = 0

    def predict(self, audio: np.ndarray) -> dict[str, float]:
        """Return the next scripted score."""
        return {"reachy": self.scores.pop(0) if self.scores else 0.0}

    def reset(self) -> None:
        """Count stream resets."""
        self.resets += 1


class FakeMedia:
    """Deliver 80 ms frames, alternating with empty polls so stale-frame draining stops."""

    def __init__(self, clock: list[float]) -> None:
        """Share a mutable clock with the test."""
        self.clock = clock
        self.calls = 0
        self.recording = False

    def start_recording(self) -> None:
        """Mark recording active."""
        self.recording = True

    def stop_recording(self) -> None:
        """Mark recording stopped."""
        self.recording = False

    def get_input_audio_samplerate(self) -> int:
        """Return the robot microphone rate."""
        return 16_000

    def get_audio_sample(self) -> np.ndarray | None:
        """Return a frame on every other poll, advancing the clock by one frame."""
        self.calls += 1
        if self.calls % 2:
            return None
        self.clock[0] += 0.08
        return np.zeros((1280, 2), dtype=np.float32)


def test_score_events_merge_close_runs_and_split_distant_ones() -> None:
    """Runs above the floor within one second form one event; longer gaps start a new one."""
    gap_short = [0.0] * 5
    gap_long = [0.0] * 20
    scores = [0.3, 0.9, 0.4] + gap_short + [0.6] + gap_long + [0.25, 0.1]

    assert score_events(scores) == (0.9, 0.25)
    assert score_events([0.1, 0.05]) == ()


def test_default_steps_cover_background_similar_words_and_ten_wakes() -> None:
    """The guided sequence has quiet and talk phases, two tries per similar word, and ten wakes."""
    steps = default_steps(talk_seconds=30)

    assert [s.kind for s in steps[:2]] == ["background", "background"]
    assert steps[1].seconds == 30
    assert sum(1 for s in steps if s.kind == "similar") == 2 * len(wake_calibration.SIMILAR_WORDS)
    assert sum(1 for s in steps if s.kind == "wake") == 10


def test_run_calibration_scores_each_step_separately() -> None:
    """Each step resets the detector and records its own peak and events, with recording stopped at the end."""
    clock = [0.0]
    media = FakeMedia(clock)
    warmup = [0.0] * 19  # 1.5 s pre-roll per step at 80 ms frames
    model = ScriptedModel(warmup + [0.0, 0.6, 0.0, 0.0, 0.0] + warmup + [0.1, 0.99, 0.5, 0.0, 0.0])
    steps = [
        CalibrationStep("talk", "talk", "background", 0.4),
        CalibrationStep("Reachy #1", "say it", "wake", 0.4),
    ]
    prompted: list[str] = []
    started: list[str] = []

    results = run_calibration(
        media,
        lambda observer: OpenWakeWordDetector(model, threshold=1.0, score_observer=observer),
        steps=steps,
        before_step=lambda step: prompted.append(step.name),
        on_start=lambda step: started.append(step.name),
        clock=lambda: clock[0],
        sleep=lambda _seconds: None,
    )

    assert prompted == started == ["talk", "Reachy #1"]
    assert all(r.frames == 5 for r in results)
    assert [r.peak for r in results] == [0.6, 0.99]
    assert results[0].event_peaks == (0.6,)
    assert results[1].events_at(0.95) == 1
    assert model.resets == 2
    assert media.recording is False


def test_report_summarizes_recall_similar_hits_and_background_rate() -> None:
    """The summary shows, per threshold, wake recall, similar-word hits and background events per minute."""
    results = [
        StepResult(CalibrationStep("talk", "", "background", 60.0), 0.92, (0.92, 0.3)),
        StepResult(CalibrationStep("peachy #1", "", "similar", 4.0), 0.99, (0.99,)),
        StepResult(CalibrationStep("Reachy #1", "", "wake", 4.0), 0.99, (0.99,)),
        StepResult(CalibrationStep("Reachy #2", "", "wake", 4.0), 0.85, (0.85,)),
    ]

    report = format_report(results)

    assert "talk" in report and "peachy #1" in report
    lines = {line.split()[0]: line.split()[1:] for line in report.splitlines()[-9:-2] if line.strip()}
    assert lines["0.8"] == ["2/2", "1/1", "1.00"]
    assert lines["0.95"] == ["1/2", "1/1", "0.00"]


def test_main_requires_a_model_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a model directory the tool exits with a usage error before touching the robot."""
    monkeypatch.setattr(wake_calibration.config, "LOCAL_ACOUSTIC_WAKE_MODEL_DIR", "", raising=False)
    with pytest.raises(SystemExit):
        wake_calibration.main(["--model-dir", ""])


def test_stops_when_no_microphone_audio_arrives() -> None:
    """A step with no frames at all raises instead of reporting meaningless zero scores."""

    class SilentMedia(FakeMedia):
        def get_audio_sample(self) -> np.ndarray | None:
            """Never deliver a frame, but let time pass."""
            self.clock[0] += 0.08
            return None

    clock = [0.0]
    media = SilentMedia(clock)

    with pytest.raises(wake_calibration.NoMicrophoneAudioError):
        run_calibration(
            media,
            lambda observer: OpenWakeWordDetector(ScriptedModel([]), threshold=1.0, score_observer=observer),
            steps=[CalibrationStep("quiet", "", "background", 0.4)],
            before_step=lambda _step: None,
            clock=lambda: clock[0],
            sleep=lambda _seconds: None,
        )
    assert media.recording is False


def test_report_warns_when_the_microphone_is_silent() -> None:
    """Exact-zero levels on every step are flagged as a blocked or broken microphone."""
    results = [StepResult(CalibrationStep("talk", "", "background", 60.0), 0.0, (), frames=10, peak_level=0.0)]

    assert "WARNING: the microphone delivered only exact silence" in format_report(results)
