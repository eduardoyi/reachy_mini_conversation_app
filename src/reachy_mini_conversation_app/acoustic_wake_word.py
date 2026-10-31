"""Opt-in local openWakeWord inference for the supervised Reachy trial."""

import math
import time
import hashlib
import logging
import importlib
from typing import Protocol
from pathlib import Path
from dataclasses import dataclass
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


# A wake word lasts well under 3 s; one fire per head per 3 s.
COOLDOWN_SAMPLES = 48000


@dataclass(frozen=True)
class WakeScoreEvent:
    """A near miss of the waking heads, or a fire of a shadow head (which never wakes Reachy)."""

    kind: str  # "near_miss" or "shadow"
    score: float  # the near miss's peak on the main head's scale, or the shadow head's own score
    head: str
    scores: dict[str, float]  # every head's raw score at that moment


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
        event_observer: Callable[[WakeScoreEvent], None] | None = None,
        head_scales: dict[str, float] | None = None,
        shadow_heads: dict[str, float] | None = None,
        frame_source: Callable[[], NDArray[np.float32]] | None = None,
        frame_observer: Callable[[NDArray[np.float32]], None] | None = None,
    ) -> None:
        """Use a prepared model and bounded PCM state; observers get each 80 ms score, near misses and shadow fires.

        With more than one head, ``head_scales`` maps each extra head's name to the factor that puts its score on
        the main head's scale (the main threshold over its own); the strongest scaled score counts.
        ``shadow_heads`` maps heads that never wake Reachy to the threshold at which their fires are reported.
        ``frame_observer`` gets the model's newest 96 input features (from ``frame_source``) every 80 ms.
        """
        if not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("Wake threshold must be in (0, 1]")
        self._head_scales = dict(head_scales or {})
        self._shadow_heads = dict(shadow_heads or {})
        self._model = model
        self._threshold = threshold
        self._score_observer = score_observer
        self._event_observer = event_observer
        self._frame_source = frame_source
        self._frame_observer = frame_observer
        self._event_peak: float | None = None
        self._event_head = ""
        self._event_scores: dict[str, float] = {}
        self._event_detected = False
        self._pending = b""
        self._samples = 0
        self._last_detection_sample = -COOLDOWN_SAMPLES
        self._last_shadow_samples = dict.fromkeys(self._shadow_heads, -COOLDOWN_SAMPLES)
        self._detections = 0
        self._suppressed = 0
        self._sample_rate: int | None = None
        self._extra_heads_muted: Callable[[], bool] | None = None

    def set_extra_heads_muted(self, muted: Callable[[], bool] | None) -> None:
        """Count only the main head while ``muted()`` is true (checked once per audio chunk).

        With the TV on, the "Richie" head fired about 80 times in an hour of film (2026-10-30, on lines like
        "Clarice"); the main head did not.
        """
        self._extra_heads_muted = muted

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        threshold: float = 0.5,
        score_observer: Callable[[float], None] | None = None,
        event_observer: Callable[[WakeScoreEvent], None] | None = None,
        extra_head: Path | None = None,
        extra_threshold: float = 0.8,
        shadow_head: Path | None = None,
        shadow_threshold: float = 0.8,
        frame_observer: Callable[[NDArray[np.float32]], None] | None = None,
    ) -> "OpenWakeWordDetector":
        """Load only the frozen local assets, failing before any inference on mismatch.

        ``extra_head`` adds a second head on the same features (one trained for another pronunciation); it wakes
        at ``extra_threshold`` on its own scale. ``shadow_head`` is scored too but only reports where it would
        have fired at ``shadow_threshold``.
        """
        for name, expected in OFFICIAL33_ASSETS.items():
            path = directory / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Missing or mismatched official33 asset: " + name)
        models = [directory / "reachy-official33.onnx"]
        head_scales: dict[str, float] = {}
        shadow_heads: dict[str, float] = {}
        for role, head, head_threshold in (
            ("Extra", extra_head, extra_threshold),
            ("Shadow", shadow_head, shadow_threshold),
        ):
            if head is None:
                continue
            if not head.is_file():
                raise ValueError(f"Missing {role.lower()} wake head: {head}")
            if not math.isfinite(head_threshold) or not 0 < head_threshold <= 1:
                raise ValueError(f"{role} wake head threshold must be in (0, 1]")
            if head.stem in {loaded.stem for loaded in models}:
                raise ValueError(f"{role} wake head {head.name} is already loaded")
            models.append(head)
            if role == "Extra":
                head_scales[head.stem] = threshold / head_threshold
            else:
                shadow_heads[head.stem] = head_threshold
            logger.info(
                "%s wake head %s sha256=%s threshold=%.2f",
                role,
                head.name,
                hashlib.sha256(head.read_bytes()).hexdigest()[:16],
                head_threshold,
            )
        module = importlib.import_module("openwakeword.model")
        openwakeword_model = module.Model(
            wakeword_models=[str(model) for model in models],
            inference_framework="onnx",
            melspec_model_path=str(directory / "melspectrogram.onnx"),
            embedding_model_path=str(directory / "embedding_model.onnx"),
            ncpu=1,
        )
        frame_source: Callable[[], NDArray[np.float32]] | None = None
        if frame_observer is not None:
            features = openwakeword_model.preprocessor

            def newest_frame() -> NDArray[np.float32]:
                return np.asarray(features.get_features(1), dtype=np.float32).reshape(-1)[-96:]

            frame_source = newest_frame
        return cls(
            openwakeword_model,
            threshold=threshold,
            score_observer=score_observer,
            event_observer=event_observer,
            head_scales=head_scales,
            shadow_heads=shadow_heads,
            frame_source=frame_source,
            frame_observer=frame_observer,
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
        muted = self._extra_heads_muted is not None and self._extra_heads_muted()
        source_block_bytes = sample_rate * 4 // 25
        while len(self._pending) >= source_block_bytes:
            chunk, self._pending = self._pending[:source_block_bytes], self._pending[source_block_bytes:]
            if sample_rate != 16000:
                pcm = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
                chunk = np.clip(resample_audio(pcm, sample_rate, 16000), -32768, 32767).astype("<i2").tobytes()
            prediction = self._model.predict(np.frombuffer(chunk, dtype="<i2"))
            if len(prediction) != 1 + len(self._head_scales) + len(self._shadow_heads):
                raise ValueError("Expected one wake-word score per head")
            score, head = -1.0, ""
            for name, raw in prediction.items():
                if not math.isfinite(raw) or not 0 <= raw <= 1:
                    raise ValueError("Invalid wake-word model score")
                if name in self._shadow_heads or (muted and name in self._head_scales):
                    continue
                scaled = min(1.0, raw * self._head_scales.get(name, 1.0))
                if scaled > score:
                    score, head = scaled, name
            instant = self._samples
            self._samples += 1280
            if self._frame_observer is not None and self._frame_source is not None:
                self._frame_observer(self._frame_source())
            if self._score_observer is not None:
                self._score_observer(score)
            self._track_shadow_fires(prediction, instant)
            self._track_score_event(score, head, prediction)
            if score < self._threshold:
                continue
            if instant - self._last_detection_sample < COOLDOWN_SAMPLES:
                self._suppressed += 1
                continue
            self._last_detection_sample = instant
            self._detections += 1
            self._event_detected = True
            if self._head_scales:
                logger.info("Acoustic wake detected score=%.2f threshold=%.2f head=%s", score, self._threshold, head)
            else:
                logger.info("Acoustic wake detected score=%.2f threshold=%.2f", score, self._threshold)
            detection = detection or WakeWordDetection(
                wake_word="Reachy",
                remaining_text="",
                first_word_retained=False,
                wake_word_is_first_word=False,
                source="acoustic",
                detected_at=time.monotonic() if observed_at is None else observed_at,
                latency_seconds=None,
                score=score,
                head=head,
                head_scores=tuple((name, round(raw, 4)) for name, raw in prediction.items()),
            )
        return detection

    def _track_shadow_fires(self, prediction: dict[str, float], instant: int) -> None:
        """Report each shadow head crossing its threshold, at most once per cooldown; it never wakes Reachy."""
        for name, threshold in self._shadow_heads.items():
            raw = prediction[name]
            if raw < threshold or instant - self._last_shadow_samples[name] < COOLDOWN_SAMPLES:
                continue
            self._last_shadow_samples[name] = instant
            logger.info("Shadow wake score=%.2f threshold=%.2f head=%s", raw, threshold, name)
            if self._event_observer is not None:
                self._event_observer(WakeScoreEvent("shadow", raw, name, dict(prediction)))

    def _track_score_event(self, score: float, head: str, prediction: dict[str, float]) -> None:
        """Log the peak of each above-floor score event that did not wake (metadata only)."""
        floor = min(NEAR_MISS_FLOOR, self._threshold / 2)
        if score >= floor:
            if self._event_peak is None or score > self._event_peak:
                self._event_peak, self._event_head, self._event_scores = score, head, dict(prediction)
            return
        if self._event_peak is not None and not self._event_detected and self._event_peak < self._threshold:
            logger.info("Acoustic wake near miss peak=%.2f threshold=%.2f", self._event_peak, self._threshold)
            if self._event_observer is not None:
                self._event_observer(
                    WakeScoreEvent("near_miss", self._event_peak, self._event_head, self._event_scores)
                )
        self._event_peak = None
        self._event_detected = False

    def rearm(self) -> None:
        """Let the next score over the threshold wake again at once, keeping the stream (after a rejected wake)."""
        self._last_detection_sample = -COOLDOWN_SAMPLES
        self._event_peak = None
        self._event_detected = False

    def reset(self) -> None:
        """Discard stream history between conversation generations without clearing counters."""
        self._event_peak = None
        self._event_detected = False
        self._pending = b""
        self._samples = 0
        self._sample_rate = None
        self._last_detection_sample = -COOLDOWN_SAMPLES
        self._last_shadow_samples = dict.fromkeys(self._shadow_heads, -COOLDOWN_SAMPLES)
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
