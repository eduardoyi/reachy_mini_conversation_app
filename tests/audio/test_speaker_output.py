"""Tests for the app-owned speaker output boundary."""

import sys
import time
import logging
import builtins
import importlib
import threading
from collections.abc import Callable

import numpy as np
import pytest

import reachy_mini_conversation_app.audio.speaker_output as speaker_output


class FakePipeline:
    """Deterministic native-pipeline stand-in."""

    def __init__(self) -> None:
        """Initialize recorded calls and synchronization controls."""
        self.started = False
        self.closed = 0
        self.flushes = 0
        self.pushed: list[np.ndarray] = []
        self.push_started = threading.Event()
        self.release_push = threading.Event()
        self.block_push = False
        self.fail_start = False
        self.fail_push = False
        self.fail_flush = False
        self.events: list[str] = []

    def start(self) -> None:
        """Record that the fake pipeline started."""
        self.events.append("start")
        if self.fail_start:
            raise OSError("native start failed")
        self.started = True

    def push(self, audio: np.ndarray) -> None:
        """Record a frame, optionally waiting to simulate a slow sink."""
        self.push_started.set()
        if self.fail_push:
            raise OSError("native push failed")
        if self.block_push:
            self.release_push.wait(timeout=1.0)
        self.pushed.append(audio.copy())
        self.events.append(f"push:{float(audio[0])}")

    def flush(self) -> None:
        """Record a pipeline flush."""
        self.events.append("flush")
        if self.fail_flush:
            raise OSError("native flush failed")
        self.flushes += 1

    def close(self) -> None:
        """Record a pipeline close."""
        self.events.append("close")
        self.closed += 1


def _factory_for(pipeline: FakePipeline) -> Callable[[int], speaker_output._GStreamerPipeline]:
    """Return a pipeline factory that records the selected sample rate."""
    return lambda _sample_rate: pipeline


def _wait_for(predicate: Callable[[], bool]) -> None:
    """Wait briefly for the output worker without relying on sleeps in assertions."""
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for speaker output worker")


def test_sdk_factory_keeps_output_on_reachy_media() -> None:
    """The safe selector does not construct or intercept an app-owned output."""
    assert speaker_output.create_speaker_output("sdk", 16_000) is None


def test_sdk_output_import_does_not_require_gi(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default output boundary imports without the optional native GI module."""
    module_name = "reachy_mini_conversation_app.audio.speaker_output"
    saved_module = sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def reject_gi(name: str, *args: object, **kwargs: object) -> object:
        if name == "gi" or name.startswith("gi."):
            raise ModuleNotFoundError("GI intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_gi)
    try:
        imported_module = importlib.import_module(module_name)
        assert imported_module.create_speaker_output("sdk", 16_000) is None
    finally:
        sys.modules.pop(module_name, None)
        if saved_module is not None:
            sys.modules[module_name] = saved_module


def test_factory_rejects_unknown_backend() -> None:
    """Only the explicit app-owned backend can change speaker routing."""
    with pytest.raises(ValueError, match="Unsupported audio output backend"):
        speaker_output.create_speaker_output("sounddevice", 16_000)


def test_gstreamer_construction_failure_fails_closed_without_sdk_fallback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unavailable opt-in path raises instead of silently reintroducing the SDK mixer."""
    sentinel = "household-audio-secret"

    def fail(_sample_rate: int) -> speaker_output.GStreamerSpeakerOutput:
        raise OSError(sentinel)

    monkeypatch.setattr(speaker_output, "GStreamerSpeakerOutput", fail)
    with caplog.at_level(logging.ERROR, logger=speaker_output.logger.name):
        with pytest.raises(RuntimeError, match="could not be initialized"):
            speaker_output.create_speaker_output("gstreamer", 16_000)

    assert sentinel not in caplog.text


def test_output_push_is_bounded_nonblocking_and_drops_oldest() -> None:
    """A slow native sink cannot block the realtime loop or grow RAM without bound."""
    pipeline = FakePipeline()
    pipeline.block_push = True
    output = speaker_output.GStreamerSpeakerOutput(16_000, max_queue_frames=2, pipeline_factory=_factory_for(pipeline))
    output.start()
    try:
        first = np.array([0.0], dtype=np.float32)
        output.push(first)
        assert pipeline.push_started.wait(timeout=1.0)

        started = time.monotonic()
        for value in range(10):
            output.push(np.array([float(value + 1)], dtype=np.float32))
        elapsed = time.monotonic() - started

        assert elapsed < 0.1
        assert output.dropped_frames >= 7
    finally:
        pipeline.release_push.set()
        output.close()


def test_push_before_start_and_after_close_is_ignored() -> None:
    """The output accepts frames only while it owns a started pipeline."""
    pipeline = FakePipeline()
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    frame = np.ones(16, dtype=np.float32)

    output.push(frame)
    output.start()
    output.close()
    output.push(frame)

    assert pipeline.pushed == []
    with pytest.raises(RuntimeError, match="closed"):
        output.start()


def test_start_failure_closes_pipeline_once() -> None:
    """A failed native start leaves no output that could silently fall back."""
    pipeline = FakePipeline()
    pipeline.fail_start = True
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))

    with pytest.raises(OSError, match="native start"):
        output.start()
    output.close()

    assert pipeline.closed == 1


def test_worker_failure_stops_accepting_audio_and_exposes_only_error_type() -> None:
    """Native push errors stop the worker without retaining or logging PCM."""
    pipeline = FakePipeline()
    pipeline.fail_push = True
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()
    output.push(np.ones(16, dtype=np.float32))

    try:
        _wait_for(lambda: output.failure_type == "OSError")
        output.push(np.full(16, 12345.0, dtype=np.float32))
        assert output.failure_type == "OSError"
    finally:
        output.close()


def test_flush_generation_is_captured_at_enqueue_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame accepted before flush cannot be emitted after the flush race."""
    monkeypatch.setattr(speaker_output, "PIPELINE_LOCK_TIMEOUT_SECONDS", 1.0)
    pipeline = FakePipeline()
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()
    output._pipeline_lock.acquire()
    try:
        output.push(np.ones(16, dtype=np.float32))
        _wait_for(output._queue.empty)
        flush_thread = threading.Thread(target=output.flush)
        flush_thread.start()
        _wait_for(lambda: output._flush_generation == 1)
    finally:
        output._pipeline_lock.release()
    flush_thread.join(timeout=1.0)
    output.close()

    assert not flush_thread.is_alive()
    assert pipeline.pushed == []
    assert pipeline.flushes == 1


def test_flush_is_bounded_and_orders_native_flush_before_new_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """A busy flush returns promptly and orders native flush before later audio."""
    monkeypatch.setattr(speaker_output, "PIPELINE_LOCK_TIMEOUT_SECONDS", 0.01)
    pipeline = FakePipeline()
    pipeline.block_push = True
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()
    output.push(np.array([1.0], dtype=np.float32))
    assert pipeline.push_started.wait(timeout=1.0)

    started = time.monotonic()
    output.flush()
    elapsed = time.monotonic() - started

    try:
        assert elapsed < 0.2
        assert pipeline.flushes == 0
        output.push(np.array([2.0], dtype=np.float32))
        pipeline.release_push.set()
        _wait_for(lambda: pipeline.flushes == 1)
        _wait_for(lambda: len(pipeline.pushed) == 2)
        assert pipeline.events.index("flush") < pipeline.events.index("push:2.0")
    finally:
        pipeline.release_push.set()
        output.close()


def test_flush_failure_is_metadata_only() -> None:
    """A native flush error stops output without escaping into the caller."""
    pipeline = FakePipeline()
    pipeline.fail_flush = True
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()

    output.flush()

    assert output.failure_type == "OSError"
    output.push(np.ones(16, dtype=np.float32))
    output.close()


def test_close_defers_native_close_until_blocked_push_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded shutdown schedules one eventual native close after an in-flight push."""
    monkeypatch.setattr(speaker_output, "PIPELINE_LOCK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(speaker_output, "WORKER_JOIN_TIMEOUT_SECONDS", 0.01)
    pipeline = FakePipeline()
    pipeline.block_push = True
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()
    output.push(np.array([1.0], dtype=np.float32))
    assert pipeline.push_started.wait(timeout=1.0)

    started = time.monotonic()
    output.close()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert pipeline.closed == 0
    pipeline.release_push.set()
    _wait_for(lambda: pipeline.closed == 1)
    output.close()

    assert pipeline.closed == 1


def test_output_flushes_pending_audio_and_close_is_idempotent() -> None:
    """Interruption drops queued frames and repeated shutdown closes native state once."""
    pipeline = FakePipeline()
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()
    output.push(np.ones(16, dtype=np.float32))
    output.flush()
    output.close()
    output.close()

    assert pipeline.flushes == 1
    assert pipeline.closed == 1


def test_output_rejects_invalid_or_nonfinite_frames_without_logging_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The output boundary validates normalized PCM and never logs frame contents."""
    pipeline = FakePipeline()
    output = speaker_output.GStreamerSpeakerOutput(16_000, pipeline_factory=_factory_for(pipeline))
    output.start()
    sentinel = np.array([12345.0], dtype=np.float32)
    output.push(sentinel)
    _wait_for(lambda: len(pipeline.pushed) == 1)
    with pytest.raises(TypeError, match="float32"):
        output.push(np.array([1], dtype=np.int16))
    with pytest.raises(ValueError, match="non-finite"):
        output.push(np.array([np.nan], dtype=np.float32))
    output.close()

    np.testing.assert_array_equal(pipeline.pushed[0], sentinel)
    assert str(sentinel[0]) not in caplog.text
