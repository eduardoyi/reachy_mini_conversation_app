"""App-owned speaker output implementations."""

import queue
import logging
import importlib
import threading
from typing import Protocol, runtime_checkable
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from reachy_mini_conversation_app.config import AUDIO_OUTPUT_BACKEND_SDK, AUDIO_OUTPUT_BACKEND_GSTREAMER


logger = logging.getLogger(__name__)

AudioFrame = npt.NDArray[np.float32]
PipelineFactory = Callable[[int], "_GStreamerPipeline"]
PIPELINE_LOCK_TIMEOUT_SECONDS = 0.1
WORKER_JOIN_TIMEOUT_SECONDS = 0.1
GSTREAMER_OUTPUT_MODULE = "reachy_mini_conversation_app.audio.gstreamer_output"


class _QueuedAudioFrame:
    """Audio copied with the flush generation active when it was accepted."""

    def __init__(self, audio: AudioFrame, generation: int) -> None:
        """Store one immutable queue item."""
        self.audio = audio
        self.generation = generation


class SpeakerOutput(Protocol):
    """Protocol for the app-owned speaker boundary."""

    @property
    def sample_rate(self) -> int:
        """Return the rate expected by this output."""

    def start(self) -> None:
        """Start the output device."""

    def push(self, audio: AudioFrame) -> None:
        """Queue one normalized mono float32 frame without blocking."""

    def flush(self) -> None:
        """Discard queued and device-side audio."""

    def close(self) -> None:
        """Release output resources; repeated calls are safe."""


class _GStreamerPipeline(Protocol):
    """Small seam around native GStreamer operations for deterministic tests."""

    def start(self) -> None:
        """Set the pipeline to PLAYING."""

    def push(self, audio: AudioFrame) -> None:
        """Push one frame to the pipeline's appsrc."""

    def flush(self) -> None:
        """Flush the pipeline's appsrc."""

    def close(self) -> None:
        """Set the pipeline to NULL and release native resources."""


@runtime_checkable
class _GStreamerBuilder(Protocol):
    """Typed surface of the lazily loaded native pipeline builder."""

    def __call__(self, sample_rate: int) -> _GStreamerPipeline:
        """Build a native GStreamer pipeline."""


class GStreamerSpeakerOutput:
    """Play app audio through a dedicated pipeline without the SDK mixer."""

    def __init__(
        self,
        sample_rate: int,
        *,
        max_queue_frames: int = 8,
        pipeline_factory: PipelineFactory | None = None,
    ) -> None:
        """Create a bounded appsrc output and its native pipeline."""
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")
        if isinstance(max_queue_frames, bool) or not isinstance(max_queue_frames, int) or max_queue_frames <= 0:
            raise ValueError("max_queue_frames must be a positive integer")

        self._sample_rate = sample_rate
        self._pipeline = (pipeline_factory or _build_gstreamer_pipeline)(sample_rate)
        self._queue: queue.Queue[_QueuedAudioFrame] = queue.Queue(maxsize=max_queue_frames)
        self._state_lock = threading.Lock()
        self._pipeline_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._dropped_frames = 0
        self._flush_generation = 0
        self._pending_native_flush = False
        self._failure_type: str | None = None
        self._pipeline_closed = False

    @property
    def sample_rate(self) -> int:
        """Return the output pipeline sample rate."""
        return self._sample_rate

    @property
    def dropped_frames(self) -> int:
        """Return the number of oldest queued frames dropped under pressure."""
        with self._state_lock:
            return self._dropped_frames

    @property
    def failure_type(self) -> str | None:
        """Return the native push failure type, if the writer stopped."""
        with self._state_lock:
            return self._failure_type

    def start(self) -> None:
        """Start the pipeline and its bounded writer thread once."""
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Speaker output is closed")
            if self._failure_type is not None:
                raise RuntimeError("Speaker output previously failed")
            if self._started:
                return
            try:
                self._pipeline.start()
            except Exception:
                self._closed = True
                self._pipeline_closed = True
                try:
                    self._pipeline.close()
                except Exception as close_error:
                    logger.warning("GStreamer speaker pipeline close failed: %s", type(close_error).__name__)
                raise
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run,
                name="gstreamer-speaker-output",
                daemon=True,
            )
            self._started = True
            self._worker.start()

    def push(self, audio: AudioFrame) -> None:
        """Copy and enqueue one mono float32 frame without waiting on native audio."""
        _validate_audio_frame(audio)
        if audio.size == 0:
            return

        frame = np.array(audio, dtype=np.float32, copy=True)
        with self._state_lock:
            if self._closed or not self._started or self._failure_type is not None:
                return
            queued_frame = _QueuedAudioFrame(frame, self._flush_generation)
            try:
                self._queue.put_nowait(queued_frame)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return
                self._queue.put_nowait(queued_frame)
                self._dropped_frames += 1
                dropped_frames = self._dropped_frames
                if dropped_frames == 1 or dropped_frames % 100 == 0:
                    logger.warning("GStreamer speaker queue full; dropped_frames=%d", dropped_frames)

    def flush(self) -> None:
        """Drop queued frames and flush the dedicated appsrc."""
        with self._state_lock:
            if self._closed or not self._started:
                return
            self._flush_generation += 1
            self._pending_native_flush = True
        self._drain_queue()
        if not self._pipeline_lock.acquire(timeout=PIPELINE_LOCK_TIMEOUT_SECONDS):
            logger.warning("GStreamer speaker flush deferred while pipeline is busy")
            return
        try:
            with self._state_lock:
                if self._closed:
                    return
            self._drain_queue()
            self._flush_pending_native_locked()
        finally:
            self._pipeline_lock.release()

    def close(self) -> None:
        """Stop the writer and release the native pipeline once."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            self._pending_native_flush = False
            self._stop_event.set()
            worker = self._worker
        self._drain_queue()
        if not self._pipeline_lock.acquire(timeout=PIPELINE_LOCK_TIMEOUT_SECONDS):
            logger.warning("GStreamer speaker pipeline close deferred while pipeline is busy")
            threading.Thread(
                target=self._close_pipeline_after_lock,
                name="gstreamer-speaker-close",
                daemon=True,
            ).start()
        else:
            try:
                self._close_pipeline_locked()
            finally:
                self._pipeline_lock.release()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=WORKER_JOIN_TIMEOUT_SECONDS)
            if worker.is_alive():
                logger.warning(
                    "GStreamer speaker writer did not stop within %.2f seconds", WORKER_JOIN_TIMEOUT_SECONDS
                )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                queued_frame = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                with self._pipeline_lock:
                    with self._state_lock:
                        if self._closed:
                            continue
                    if not self._flush_pending_native_locked():
                        continue
                    with self._state_lock:
                        if queued_frame.generation != self._flush_generation or self._closed:
                            continue
                    self._pipeline.push(queued_frame.audio)
                    self._flush_pending_native_locked()
            except Exception as exc:
                with self._state_lock:
                    self._failure_type = type(exc).__name__
                    self._started = False
                self._drain_queue()
                logger.warning("GStreamer speaker output stopped: %s", type(exc).__name__)
                self._stop_event.set()

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _close_pipeline_after_lock(self) -> None:
        """Finish native cleanup after an in-flight push releases the lock."""
        with self._pipeline_lock:
            self._close_pipeline_locked()

    def _flush_pending_native_locked(self) -> bool:
        """Flush a coalesced native request while the pipeline lock is held."""
        with self._state_lock:
            if not self._pending_native_flush:
                return True
            if self._closed:
                self._pending_native_flush = False
                return True
            self._pending_native_flush = False
        try:
            self._pipeline.flush()
        except Exception as error:
            with self._state_lock:
                self._failure_type = type(error).__name__
                self._started = False
            self._drain_queue()
            self._stop_event.set()
            logger.warning("GStreamer speaker output flush failed: %s", type(error).__name__)
            return False
        return True

    def _close_pipeline_locked(self) -> None:
        """Close the native pipeline once while the pipeline lock is held."""
        with self._state_lock:
            if self._pipeline_closed:
                return
            self._pipeline_closed = True
        try:
            self._pipeline.close()
        except Exception as error:
            logger.warning("GStreamer speaker pipeline close failed: %s", type(error).__name__)


def create_speaker_output(
    backend: str,
    sample_rate: int,
    *,
    logger_instance: logging.Logger | None = None,
) -> SpeakerOutput | None:
    """Build the selected speaker output, with no silent SDK fallback."""
    if backend == AUDIO_OUTPUT_BACKEND_SDK:
        return None
    if backend != AUDIO_OUTPUT_BACKEND_GSTREAMER:
        raise ValueError(f"Unsupported audio output backend: {backend!r}")

    try:
        return GStreamerSpeakerOutput(sample_rate)
    except Exception as exc:
        log = logger_instance or logger
        log.error("Configured GStreamer speaker output is unavailable: %s", type(exc).__name__)
        raise RuntimeError("Configured GStreamer speaker output could not be initialized") from exc


def _validate_audio_frame(audio: AudioFrame) -> None:
    """Reject frames that cannot be represented by the appsrc contract."""
    if not isinstance(audio, np.ndarray):
        raise TypeError("Audio must be a NumPy array")
    if audio.dtype != np.float32:
        raise TypeError("Audio must use float32 samples")
    if audio.ndim != 1:
        raise ValueError("Audio must be a one-dimensional frame")
    if not np.all(np.isfinite(audio)):
        raise ValueError("Audio contains non-finite samples")


def _build_gstreamer_pipeline(sample_rate: int) -> _GStreamerPipeline:
    """Build the production GStreamer pipeline."""
    module = importlib.import_module(GSTREAMER_OUTPUT_MODULE)
    builder = getattr(module, "build_gstreamer_pipeline", None)
    if not isinstance(builder, _GStreamerBuilder):
        raise RuntimeError("GStreamer output module does not expose its pipeline builder")
    return builder(sample_rate)


__all__ = ["GStreamerSpeakerOutput", "SpeakerOutput", "create_speaker_output"]
