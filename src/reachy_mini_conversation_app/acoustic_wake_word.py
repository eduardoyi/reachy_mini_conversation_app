"""Opt-in local openWakeWord inference for the supervised Reachy trial."""

import math
import time
import hashlib
import logging
import importlib
from typing import Protocol
from pathlib import Path
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import resample_audio
from reachy_mini_conversation_app.wake_word import WakeWordDetection, WakeWordMetricsSnapshot


logger = logging.getLogger(__name__)

# Score events that rise above this floor but stay under the threshold are logged as near misses,
# so the threshold can be calibrated on new microphones from scores alone (no audio kept).
NEAR_MISS_FLOOR = 0.2

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

    def __init__(
        self,
        model: WakeModel,
        *,
        threshold: float = 0.5,
        score_observer: Callable[[float], None] | None = None,
        event_observer: Callable[[float, bool], None] | None = None,
        head_scales: dict[str, float] | None = None,
    ) -> None:
        """Use a prepared model and bounded PCM state; observers get each 80 ms score and each wake or near miss.

        With more than one head, ``head_scales`` maps each extra head's name to the factor that puts its score on
        the main head's scale (the main threshold over its own); the strongest scaled score counts.
        """
        if not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("Wake threshold must be in (0, 1]")
        self._head_scales = dict(head_scales or {})
        self._model = model
        self._threshold = threshold
        self._score_observer = score_observer
        self._event_observer = event_observer
        self._event_peak: float | None = None
        self._event_detected = False
        self._pending = b""
        self._samples = 0
        self._last_detection_sample = -48000
        self._detections = 0
        self._suppressed = 0
        self._sample_rate: int | None = None

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        threshold: float = 0.5,
        score_observer: Callable[[float], None] | None = None,
        event_observer: Callable[[float, bool], None] | None = None,
        extra_head: Path | None = None,
        extra_threshold: float = 0.8,
    ) -> "OpenWakeWordDetector":
        """Load only the frozen local assets, failing before any inference on mismatch.

        ``extra_head`` adds a second head on the same features (one trained for another pronunciation); it wakes
        at ``extra_threshold`` on its own scale.
        """
        for name, expected in OFFICIAL33_ASSETS.items():
            path = directory / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Missing or mismatched official33 asset: " + name)
        models = [str(directory / "reachy-official33.onnx")]
        head_scales: dict[str, float] = {}
        if extra_head is not None:
            if not extra_head.is_file():
                raise ValueError("Missing extra wake head: " + str(extra_head))
            if not math.isfinite(extra_threshold) or not 0 < extra_threshold <= 1:
                raise ValueError("Extra wake head threshold must be in (0, 1]")
            models.append(str(extra_head))
            head_scales[extra_head.stem] = threshold / extra_threshold
            logger.info(
                "Extra wake head %s sha256=%s threshold=%.2f",
                extra_head.name,
                hashlib.sha256(extra_head.read_bytes()).hexdigest()[:16],
                extra_threshold,
            )
        module = importlib.import_module("openwakeword.model")
        model: WakeModel = module.Model(
            wakeword_models=models,
            inference_framework="onnx",
            melspec_model_path=str(directory / "melspectrogram.onnx"),
            embedding_model_path=str(directory / "embedding_model.onnx"),
            ncpu=1,
        )
        return cls(
            model,
            threshold=threshold,
            score_observer=score_observer,
            event_observer=event_observer,
            head_scales=head_scales,
        )

    def process_audio(
        self, audio: bytes, sample_rate: int, *, observed_at: float | None = None
    ) -> WakeWordDetection | None:
        """Consume mono PCM16 in bounded 80ms windows at the configured threshold and a 3s cooldown."""
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
            if len(prediction) != 1 + len(self._head_scales):
                raise ValueError("Expected one wake-word score per head")
            score, head = -1.0, ""
            for name, raw in prediction.items():
                if not math.isfinite(raw) or not 0 <= raw <= 1:
                    raise ValueError("Invalid wake-word model score")
                scaled = min(1.0, raw * self._head_scales.get(name, 1.0))
                if scaled > score:
                    score, head = scaled, name
            instant = self._samples
            self._samples += 1280
            if self._score_observer is not None:
                self._score_observer(score)
            self._track_score_event(score)
            if score < self._threshold:
                continue
            if instant - self._last_detection_sample < 48000:
                self._suppressed += 1
                continue
            self._last_detection_sample = instant
            self._detections += 1
            self._event_detected = True
            if self._head_scales:
                logger.info("Acoustic wake detected score=%.2f threshold=%.2f head=%s", score, self._threshold, head)
            else:
                logger.info("Acoustic wake detected score=%.2f threshold=%.2f", score, self._threshold)
            if self._event_observer is not None:
                self._event_observer(score, True)
            detection = detection or WakeWordDetection(
                wake_word="Reachy",
                remaining_text="",
                first_word_retained=False,
                wake_word_is_first_word=False,
                source="acoustic",
                detected_at=time.monotonic() if observed_at is None else observed_at,
                latency_seconds=None,
                score=score,
            )
        return detection

    def _track_score_event(self, score: float) -> None:
        """Log the peak of each above-floor score event that did not wake (metadata only)."""
        floor = min(NEAR_MISS_FLOOR, self._threshold / 2)
        if score >= floor:
            self._event_peak = score if self._event_peak is None else max(self._event_peak, score)
            return
        if self._event_peak is not None and not self._event_detected and self._event_peak < self._threshold:
            logger.info("Acoustic wake near miss peak=%.2f threshold=%.2f", self._event_peak, self._threshold)
            if self._event_observer is not None:
                self._event_observer(self._event_peak, False)
        self._event_peak = None
        self._event_detected = False

    def reset(self) -> None:
        """Discard stream history between conversation generations without clearing counters."""
        self._event_peak = None
        self._event_detected = False
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
