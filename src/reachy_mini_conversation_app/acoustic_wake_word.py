"""Opt-in local openWakeWord inference for the supervised Reachy trial."""

import math
import time
import hashlib
import importlib
from typing import Protocol
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import resample_audio
from reachy_mini_conversation_app.wake_word import WakeWordDetection, WakeWordMetricsSnapshot


OFFICIAL33_ASSETS = {
    "reachy-official33.onnx": "f94693ce16d970d8ab0524f36854491d7f6067ae0f46b23cea624e86e5928427",
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
}


class WakeModel(Protocol):
    """Public inference operations needed from the optional dependency."""

    def predict(self, audio: NDArray[np.int16]) -> dict[str, float]:
        """Score one 80-millisecond PCM frame."""

    def reset(self) -> None:
        """Clear the upstream stream state."""


class OpenWakeWordDetector:
    """Detect official33 at its frozen operating point without owning a microphone."""

    def __init__(self, model: WakeModel) -> None:
        """Use a prepared model and bounded PCM state."""
        self._model = model
        self._pending = b""
        self._samples = 0
        self._last_detection_sample = -48000
        self._detections = 0
        self._suppressed = 0
        self._sample_rate: int | None = None

    @classmethod
    def from_directory(cls, directory: Path) -> "OpenWakeWordDetector":
        """Load only the frozen local assets, failing before any inference on mismatch."""
        for name, expected in OFFICIAL33_ASSETS.items():
            path = directory / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Missing or mismatched official33 asset: " + name)
        module = importlib.import_module("openwakeword.model")
        model: WakeModel = module.Model(
            wakeword_models=[str(directory / "reachy-official33.onnx")],
            inference_framework="onnx",
            melspec_model_path=str(directory / "melspectrogram.onnx"),
            embedding_model_path=str(directory / "embedding_model.onnx"),
            ncpu=1,
        )
        return cls(model)

    def process_audio(
        self, audio: bytes, sample_rate: int, *, observed_at: float | None = None
    ) -> WakeWordDetection | None:
        """Consume mono PCM16 in bounded 80ms windows at threshold 0.5 and cooldown 3s."""
        if sample_rate <= 0 or sample_rate % 25 or len(audio) % 2 or len(audio) > sample_rate * 10:
            raise ValueError("Expected at most five seconds of mono PCM16 at a positive sample rate divisible by 25")
        if observed_at is not None and not math.isfinite(observed_at):
            raise ValueError("Observation timestamp must be finite")
        if self._sample_rate is not None and self._sample_rate != sample_rate:
            self.reset()
        self._sample_rate = sample_rate
        self._pending += audio
        detection = None
        source_block_bytes = sample_rate * 4 // 25
        while len(self._pending) >= source_block_bytes:
            chunk, self._pending = self._pending[:source_block_bytes], self._pending[source_block_bytes:]
            if sample_rate != 16000:
                pcm = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
                chunk = np.clip(resample_audio(pcm, sample_rate, 16000), -32768, 32767).astype("<i2").tobytes()
            prediction = self._model.predict(np.frombuffer(chunk, dtype="<i2"))
            if len(prediction) != 1:
                raise ValueError("Expected one wake-word model output")
            score = next(iter(prediction.values()))
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Invalid wake-word model score")
            instant = self._samples
            self._samples += 1280
            if score < 0.5:
                continue
            if instant - self._last_detection_sample < 48000:
                self._suppressed += 1
                continue
            self._last_detection_sample = instant
            self._detections += 1
            detection = detection or WakeWordDetection(
                wake_word="Reachy",
                remaining_text="",
                first_word_retained=False,
                wake_word_is_first_word=False,
                source="acoustic",
                detected_at=time.monotonic() if observed_at is None else observed_at,
                latency_seconds=None,
            )
        return detection

    def reset(self) -> None:
        """Discard stream history between conversation generations without clearing counters."""
        self._pending = b""
        self._samples = 0
        self._sample_rate = None
        self._last_detection_sample = -48000
        self._model.reset()

    def metrics_snapshot(self) -> WakeWordMetricsSnapshot:
        """Return counters without audio or guessed acoustic latency."""
        return WakeWordMetricsSnapshot(
            detection_count=self._detections,
            false_positive_count=0,
            missed_activation_count=0,
            cooldown_suppressed_count=self._suppressed,
            partial_update_count=0,
            final_update_count=0,
            latency_sample_count=0,
            total_latency_seconds=0,
            average_latency_seconds=None,
        )
