"""Tests for the optional local acoustic wake-word detector."""

from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

import reachy_mini_conversation_app.acoustic_wake_word as acoustic_wake_word


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_280


def _pcm16(samples: NDArray[np.int16]) -> bytes:
    """Encode deterministic little-endian mono PCM16 samples."""
    return samples.astype("<i2", copy=False).tobytes()


class FakeWakeModel:
    """Record model frames and return deterministic scores."""

    def __init__(self, *, default_score: float = 0.0, scores: list[float] | None = None) -> None:
        """Initialize a score script."""
        self.default_score = default_score
        self._scores = list(scores or [])
        self.frames: list[NDArray[np.int16]] = []
        self.reset_count = 0

    def predict(self, audio: NDArray[np.int16]) -> dict[str, float]:
        """Record one frame and return its scripted score."""
        self.frames.append(audio.copy())
        score = self._scores.pop(0) if self._scores else self.default_score
        return {"reachy": score}

    def reset(self) -> None:
        """Record an upstream stream reset."""
        self.reset_count += 1


def test_buffers_partial_frame_and_passes_exact_pcm_values_to_model() -> None:
    """Two 40 ms inputs become one model frame without changing sample values."""
    model = FakeWakeModel(default_score=0.75)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    samples = np.arange(FRAME_SAMPLES, dtype=np.int16)

    assert detector.process_audio(_pcm16(samples[:640]), SAMPLE_RATE, observed_at=12.5) is None
    assert model.frames == []

    detection = detector.process_audio(_pcm16(samples[640:]), SAMPLE_RATE, observed_at=12.5)

    assert detection is not None
    assert detection.wake_word == "Reachy"
    assert detection.remaining_text == ""
    assert detection.source == "acoustic"
    assert detection.detected_at == 12.5
    np.testing.assert_array_equal(model.frames, [samples])


@pytest.mark.parametrize(
    ("score", "detected"),
    [(0.499999, False), (0.5, True)],
)
def test_frozen_score_threshold_is_inclusive(score: float, detected: bool) -> None:
    """Only scores at least 0.5 activate the frozen detector."""
    model = FakeWakeModel(default_score=score)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    frame = _pcm16(np.zeros(FRAME_SAMPLES, dtype=np.int16))

    assert (detector.process_audio(frame, SAMPLE_RATE, observed_at=1.0) is not None) is detected


def test_three_second_cooldown_suppresses_early_frames_then_allows_next_event() -> None:
    """Repeated positive frames inside three seconds are suppressed."""
    model = FakeWakeModel(default_score=0.5)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    frame = _pcm16(np.zeros(FRAME_SAMPLES, dtype=np.int16))

    assert detector.process_audio(frame, SAMPLE_RATE) is not None
    suppressed = [detector.process_audio(frame, SAMPLE_RATE) for _ in range(37)]
    next_detection = detector.process_audio(frame, SAMPLE_RATE)

    assert all(result is None for result in suppressed)
    assert next_detection is not None
    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 2
    assert metrics.cooldown_suppressed_count == 37


def test_reset_discards_partial_frame_and_resets_upstream_model() -> None:
    """Audio captured before reset cannot combine with audio from the next generation."""
    model = FakeWakeModel(default_score=0.75)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    before_reset = np.full(640, 111, dtype=np.int16)
    after_reset = np.full(640, 222, dtype=np.int16)

    assert detector.process_audio(_pcm16(before_reset), SAMPLE_RATE) is None
    detector.reset()

    assert model.reset_count == 1
    assert detector.process_audio(_pcm16(after_reset), SAMPLE_RATE) is None
    detection = detector.process_audio(_pcm16(after_reset), SAMPLE_RATE)

    assert detection is not None
    np.testing.assert_array_equal(model.frames, [np.concatenate((after_reset, after_reset))])


@pytest.mark.parametrize("sample_rate", [0, -1])
def test_rejects_non_positive_sample_rates(sample_rate: int) -> None:
    """A device rate must be positive before audio enters detector state."""
    model = FakeWakeModel(default_score=1.0)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)

    with pytest.raises(ValueError, match="positive sample rate"):
        detector.process_audio(b"\x00\x00", sample_rate)

    assert model.frames == []


def test_rejects_odd_length_pcm16() -> None:
    """A partial PCM16 sample cannot be sent to the model."""
    model = FakeWakeModel(default_score=1.0)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)

    with pytest.raises(ValueError, match="PCM16"):
        detector.process_audio(b"\x00", SAMPLE_RATE)

    assert model.frames == []


def test_rejects_audio_longer_than_five_seconds() -> None:
    """A caller cannot enqueue an unbounded audio payload in one call."""
    model = FakeWakeModel(default_score=1.0)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)

    with pytest.raises(ValueError, match="at most five seconds"):
        detector.process_audio(bytes(SAMPLE_RATE * 10 + 2), SAMPLE_RATE)

    assert model.frames == []


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01])
def test_rejects_non_finite_or_out_of_range_model_scores(score: float) -> None:
    """Invalid model output cannot become an acoustic activation."""
    model = FakeWakeModel(default_score=score)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    frame = _pcm16(np.zeros(FRAME_SAMPLES, dtype=np.int16))

    with pytest.raises(ValueError, match="Invalid wake-word model score"):
        detector.process_audio(frame, SAMPLE_RATE)

    assert detector.metrics_snapshot().detection_count == 0


def test_resamples_eight_kilohertz_pcm_to_one_six_kilohertz_model_frame() -> None:
    """An 80 ms 8 kHz frame reaches the model as 1,280 16 kHz samples."""
    model = FakeWakeModel(default_score=0.75)
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    source = np.arange(640, dtype=np.int16)

    detection = detector.process_audio(_pcm16(source), 8_000)

    assert detection is not None
    assert len(model.frames) == 1
    np.testing.assert_array_equal(model.frames[0], np.repeat(source, 2))


def test_48khz_chunking_keeps_model_frames_identical_and_flushes_pending_audio() -> None:
    """Whole, 1,024-sample, and single-sample inputs produce the same 80 ms frames."""
    source = ((np.arange(48_000, dtype=np.int32) % 30_000) - 15_000).astype(np.int16)
    padding = np.zeros(1_920, dtype=np.int16)
    frames_by_chunk_size: dict[int, list[NDArray[np.int16]]] = {}

    for chunk_size in (len(source), 1_024, 1):
        model = FakeWakeModel()
        detector = acoustic_wake_word.OpenWakeWordDetector(model)
        for start in range(0, len(source), chunk_size):
            detector.process_audio(_pcm16(source[start : start + chunk_size]), 48_000)

        assert len(model.frames) == 12
        detector.process_audio(_pcm16(padding), 48_000)

        assert len(model.frames) == 13
        assert all(frame.shape == (FRAME_SAMPLES,) for frame in model.frames)
        frames_by_chunk_size[chunk_size] = model.frames

    baseline_frames = frames_by_chunk_size[len(source)]
    for frames in frames_by_chunk_size.values():
        np.testing.assert_array_equal(np.stack(frames), np.stack(baseline_frames))


def test_processing_continues_after_first_detection_without_dropping_caller_audio() -> None:
    """Frames after a hit in one caller chunk still reach the model and later calls."""
    model = FakeWakeModel(default_score=0.0, scores=[0.75])
    detector = acoustic_wake_word.OpenWakeWordDetector(model)
    first_frame = np.full(FRAME_SAMPLES, -1_000, dtype=np.int16)
    second_frame = np.full(FRAME_SAMPLES, 2_000, dtype=np.int16)
    third_frame = np.full(FRAME_SAMPLES, 3_000, dtype=np.int16)

    detection = detector.process_audio(_pcm16(np.concatenate((first_frame, second_frame))), SAMPLE_RATE)
    assert detection is not None
    assert detector.process_audio(_pcm16(third_frame), SAMPLE_RATE) is None

    np.testing.assert_array_equal(np.stack(model.frames), np.stack((first_frame, second_frame, third_frame)))


def test_missing_asset_fails_before_optional_model_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing local weights cannot trigger an optional dependency import."""
    import_calls: list[str] = []

    def record_import(module_name: str) -> object:
        """Record unexpected optional imports."""
        import_calls.append(module_name)
        return object()

    monkeypatch.setattr(acoustic_wake_word.importlib, "import_module", record_import)

    with pytest.raises(ValueError, match="reachy-official33.onnx"):
        acoustic_wake_word.OpenWakeWordDetector.from_directory(tmp_path)

    assert import_calls == []


def test_mismatched_asset_fails_before_optional_model_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash mismatches cannot trigger optional model construction."""
    for name in acoustic_wake_word.OFFICIAL33_ASSETS:
        (tmp_path / name).write_bytes(b"fixture asset")
    import_calls: list[str] = []

    def record_import(module_name: str) -> object:
        """Record unexpected optional imports."""
        import_calls.append(module_name)
        return object()

    monkeypatch.setattr(acoustic_wake_word.importlib, "import_module", record_import)

    with pytest.raises(ValueError, match="official33 asset"):
        acoustic_wake_word.OpenWakeWordDetector.from_directory(tmp_path)

    assert import_calls == []


def test_loads_verified_assets_with_frozen_model_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verified assets construct the optional model with the pinned settings."""
    asset_payloads = {name.encode(): name for name in acoustic_wake_word.OFFICIAL33_ASSETS}
    for name in acoustic_wake_word.OFFICIAL33_ASSETS:
        (tmp_path / name).write_bytes(name.encode())

    class FakeDigest:
        """Return the expected digest for one fixture asset."""

        def __init__(self, asset_name: str) -> None:
            self.asset_name = asset_name

        def hexdigest(self) -> str:
            """Return the digest configured by the test fixture."""
            return acoustic_wake_word.OFFICIAL33_ASSETS[self.asset_name]

    def fake_sha256(payload: bytes) -> FakeDigest:
        """Map each fixture payload to its expected frozen asset digest."""
        return FakeDigest(asset_payloads[payload])

    model = FakeWakeModel(default_score=0.75)
    model_kwargs: list[dict[str, object]] = []

    def construct_model(**kwargs: object) -> FakeWakeModel:
        """Capture model construction arguments."""
        model_kwargs.append(kwargs)
        return model

    imported_modules: list[str] = []

    def fake_import(module_name: str) -> SimpleNamespace:
        """Provide the optional model module without importing it."""
        imported_modules.append(module_name)
        return SimpleNamespace(Model=construct_model)

    monkeypatch.setattr(acoustic_wake_word.hashlib, "sha256", fake_sha256)
    monkeypatch.setattr(acoustic_wake_word.importlib, "import_module", fake_import)

    detector = acoustic_wake_word.OpenWakeWordDetector.from_directory(tmp_path)

    assert imported_modules == ["openwakeword.model"]
    assert model_kwargs == [
        {
            "wakeword_models": [str(tmp_path / "reachy-official33.onnx")],
            "inference_framework": "onnx",
            "melspec_model_path": str(tmp_path / "melspectrogram.onnx"),
            "embedding_model_path": str(tmp_path / "embedding_model.onnx"),
            "ncpu": 1,
        }
    ]
    assert detector.process_audio(_pcm16(np.zeros(FRAME_SAMPLES, dtype=np.int16)), SAMPLE_RATE) is not None
