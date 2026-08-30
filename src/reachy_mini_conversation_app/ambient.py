"""RAM-only ambient audio boundaries used by the hybrid runtime."""

import threading
from collections import deque

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import AudioArray, audio_to_mono_int16
from reachy_mini_conversation_app.conversation_handler import AudioFrame


MAX_BUFFER_SECONDS = 20.0
DEFAULT_SAMPLE_RATE = 16_000


class AmbientAudioBuffer:
    """Bounded mono-int16 PCM storage that never writes audio to disk."""

    def __init__(self, *, max_seconds: float = 20.0, sample_rate: int | None = None) -> None:
        """Initialize a buffer with an optional fixed sample-rate policy."""
        if max_seconds <= 0:
            raise ValueError("Ambient buffer duration must be positive")
        if sample_rate is not None and sample_rate <= 0:
            raise ValueError("Ambient buffer sample rate must be positive")

        self.max_seconds = min(float(max_seconds), MAX_BUFFER_SECONDS)
        self._configured_sample_rate = int(sample_rate) if sample_rate is not None else None
        self._sample_rate = self._configured_sample_rate
        self._max_samples = self._capacity_for(self._sample_rate)
        self._chunks: deque[NDArray[np.int16]] = deque()
        self._sample_count = 0
        self._lock = threading.RLock()

    @property
    def sample_rate(self) -> int | None:
        """Return the fixed rate or the first accepted frame's rate."""
        with self._lock:
            return self._sample_rate

    @property
    def max_samples(self) -> int:
        """Return the maximum number of PCM samples retained."""
        with self._lock:
            return self._max_samples

    @property
    def seconds_buffered(self) -> float:
        """Return current PCM occupancy in seconds."""
        with self._lock:
            if self._sample_rate is None:
                return 0.0
            return self._sample_count / self._sample_rate

    @property
    def occupancy_seconds(self) -> float:
        """Return current PCM occupancy in seconds."""
        return self.seconds_buffered

    @property
    def byte_count(self) -> int:
        """Return the current retained PCM byte count."""
        with self._lock:
            return self._sample_count * np.dtype(np.int16).itemsize

    @property
    def sample_count(self) -> int:
        """Return the current retained sample count."""
        with self._lock:
            return self._sample_count

    def __len__(self) -> int:
        """Return the number of retained samples."""
        return self.sample_count

    def append(self, frame: AudioFrame | tuple[int, AudioArray]) -> None:
        """Append one frame, adapting once then rejecting mixed rates."""
        input_sample_rate, input_audio = frame
        if input_sample_rate <= 0:
            raise ValueError("Ambient audio sample rate must be positive")
        normalized = audio_to_mono_int16(input_audio)
        if normalized.size == 0:
            return

        with self._lock:
            if self._sample_rate is None:
                self._sample_rate = input_sample_rate
                self._max_samples = self._capacity_for(input_sample_rate)
            elif input_sample_rate != self._sample_rate:
                raise ValueError(
                    f"Ambient buffer accepts {self._sample_rate} Hz frames; received {input_sample_rate} Hz"
                )
            self._append_normalized(normalized)

    def snapshot(self) -> AudioFrame:
        """Return a defensive chronological copy tagged with the sample rate."""
        with self._lock:
            sample_rate = self._sample_rate or DEFAULT_SAMPLE_RATE
            if not self._chunks:
                return sample_rate, np.empty(0, dtype=np.int16)
            return sample_rate, np.concatenate(tuple(self._chunks)).astype(np.int16, copy=True)

    def clear(self) -> None:
        """Forget retained samples and reset an adaptive rate for the next frame."""
        with self._lock:
            self._chunks.clear()
            self._sample_count = 0
            self._sample_rate = self._configured_sample_rate
            self._max_samples = self._capacity_for(self._sample_rate)

    def _capacity_for(self, sample_rate: int | None) -> int:
        if sample_rate is None:
            return 0
        return max(1, round(self.max_seconds * sample_rate))

    def _append_normalized(self, normalized: NDArray[np.int16]) -> None:
        if normalized.size > self._max_samples:
            normalized = normalized[-self._max_samples :].copy()

        self._chunks.append(normalized)
        self._sample_count += normalized.size
        while self._sample_count > self._max_samples:
            oldest = self._chunks[0]
            excess = self._sample_count - self._max_samples
            if oldest.size <= excess:
                self._chunks.popleft()
                self._sample_count -= oldest.size
            else:
                self._chunks[0] = oldest[excess:].copy()
                self._sample_count -= excess


__all__ = ["AmbientAudioBuffer", "MAX_BUFFER_SECONDS"]
