"""Tests for the local/cloud hybrid runtime lifecycle."""

import asyncio
import logging
import threading
from unittest.mock import AsyncMock, MagicMock
from collections.abc import Callable

import numpy as np
import pytest

from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector
from reachy_mini_conversation_app.local_actions import LocalActionExecutor
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime, LocalCommandOutcome
from reachy_mini_conversation_app.ambient_context import (
    TranscriptSegment,
    AmbientContextConfig,
    AmbientContextStrategy,
    AmbientContextProcessor,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.acoustic_wake_word import OpenWakeWordDetector
from reachy_mini_conversation_app.local_intent_router import LocalIntentRouter, IntentDecisionReason
from reachy_mini_conversation_app.conversation_handler import AudioFrame, HandlerOutput, ConversationHandler


class FakeClock:
    """Controllable monotonic clock for deterministic lifecycle tests."""

    def __init__(self) -> None:
        """Start the clock at zero."""
        self.value = 0.0

    def __call__(self) -> float:
        """Return the current fake timestamp."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Advance time by seconds."""
        self.value += seconds


class FakeLocalProcessor:
    """Record local frames while retaining no reference to caller arrays."""

    def __init__(self, *, fail: bool = False) -> None:
        """Initialize the recorder."""
        self.frames: list[AudioFrame] = []
        self.fail = fail

    def observe_frame(self, frame: AudioFrame) -> None:
        """Record a copied local frame or raise a controlled failure."""
        if self.fail:
            raise RuntimeError("local processor sentinel")
        self.frames.append((frame[0], frame[1].copy()))


class TranscriptOnFrameProcessor(FakeLocalProcessor):
    """Emit one deterministic final transcript whenever a frame is observed."""

    def __init__(self) -> None:
        """Initialize the processor without an attached runtime callback."""
        super().__init__()
        self.transcript_callback: Callable[[AudioFrame], None] | None = None

    def observe_frame(self, frame: AudioFrame) -> None:
        """Retain the frame and optionally emit a local final transcript."""
        super().observe_frame(frame)
        if self.transcript_callback is not None:
            self.transcript_callback(frame)


class FakeHandler(ConversationHandler):
    """Long-lived provider fake with observable lifecycle counters."""

    def __init__(
        self,
        *,
        fail_start: bool = False,
        fail_receive: bool = False,
        drop_unready_frames: bool = False,
        block_shutdown: bool = False,
        fail_shutdown: bool = False,
    ) -> None:
        """Initialize a provider fake."""
        super().__init__()
        self.deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
        self.output_queue: asyncio.Queue[AudioFrame | AdditionalOutputs] = asyncio.Queue()
        self.started = asyncio.Event()
        self.shutdown_started = asyncio.Event()
        self.release_shutdown = asyncio.Event()
        self.stop_requested = asyncio.Event()
        self.fail_start = fail_start
        self.fail_receive = fail_receive
        self.drop_unready_frames = drop_unready_frames
        self.block_shutdown = block_shutdown
        self.fail_shutdown = fail_shutdown
        self.connected = False
        self.receive_frames: list[AudioFrame] = []
        self.shutdown_calls = 0
        self.events: list[str] = []
        self.emit_calls = 0
        self.emit_called = asyncio.Event()
        self.say_texts: list[str] = []
        self.say_called = asyncio.Event()

    def set_connected(self, connected: bool) -> None:
        """Set the fake connection state and notify the runtime."""
        self.connected = connected
        self._emit_connection_state(connected)

    def _is_connected(self) -> bool:
        """Return the fake connection state."""
        return self.connected

    async def start_up(self) -> None:
        """Stay alive until the runtime shuts down the fake."""
        self.events.append("start")
        self.started.set()
        if self.fail_start:
            raise RuntimeError("provider setup sentinel")
        await self.stop_requested.wait()

    async def shutdown(self) -> None:
        """Record one provider shutdown and wake startup."""
        self.shutdown_calls += 1
        self.events.append("shutdown")
        self.shutdown_started.set()
        if self.block_shutdown:
            await self.release_shutdown.wait()
        if self.fail_shutdown:
            raise RuntimeError("provider shutdown sentinel")
        self.stop_requested.set()
        self.set_connected(False)

    async def receive(self, frame: AudioFrame) -> None:
        """Record a provider-bound frame or raise a controlled failure."""
        if self.fail_receive:
            raise RuntimeError("provider receive sentinel")
        if self.drop_unready_frames and not self.connected:
            return
        self.receive_frames.append((frame[0], frame[1].copy()))

    async def apply_personality(self, profile: str | None) -> str:
        """Satisfy the provider contract for lifecycle tests."""
        return profile or ""

    async def get_available_voices(self) -> list[str]:
        """Return one fake voice."""
        return ["fake"]

    def get_current_voice(self) -> str:
        """Return the fake voice."""
        return "fake"

    async def change_voice(self, voice: str) -> str:
        """Return the requested fake voice."""
        return voice

    async def say(self, text: str) -> None:
        """Accept an injected fake utterance."""
        self.say_texts.append(text)
        self.say_called.set()

    async def emit(self) -> HandlerOutput:
        """Emit one queued fake output."""
        self.emit_calls += 1
        self.emit_called.set()
        try:
            return self.output_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


async def wait_until(predicate: Callable[[], bool], *, attempts: int = 100) -> None:
    """Yield until a lifecycle predicate is true."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("lifecycle predicate did not become true")


def frame(value: int, *, sample_rate: int = 16_000) -> AudioFrame:
    """Return one deterministic mono frame."""
    return sample_rate, np.array([value], dtype=np.int16)


@pytest.mark.asyncio
async def test_transcript_preview_becomes_unavailable_after_processor_close() -> None:
    """Independent processor shutdown clears preview text before runtime shutdown."""
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(
            strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT,
            transcript_window_seconds=10.0,
            max_transcript_chars=14,
        ),
    )
    processor.transcript.add(TranscriptSegment("old context", 0.0, 1.0, 1))
    processor.transcript.add(TranscriptSegment("newest context", 2.0, 3.0, 2))
    runtime = HybridRuntime(lambda: FakeHandler(), context_processor=processor, clock=lambda: 3.0)

    assert await runtime.get_transcript_preview() == "newest context"

    await processor.close()
    assert processor.transcript.segments == ()
    assert processor.transcript.text(now=3.0) == ""
    assert processor.get_transcript_preview(now=3.0) is None
    assert await runtime.get_transcript_preview() is None

    await runtime.close()
    assert await runtime.get_transcript_preview() is None


@pytest.mark.asyncio
async def test_transcript_preview_returns_empty_for_empty_continuous_context() -> None:
    """An enabled continuous processor with no final segments has empty text."""
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT),
    )
    runtime = HybridRuntime(lambda: FakeHandler(), context_processor=processor)

    assert await runtime.get_transcript_preview() == ""

    await runtime.close()
    await processor.close()


@pytest.mark.asyncio
async def test_transcript_preview_is_unavailable_for_activation_buffer_strategy() -> None:
    """Strategy B does not retain a rolling transcript for diagnostic preview."""
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.ACTIVATION_BUFFER),
    )
    runtime = HybridRuntime(lambda: FakeHandler(), context_processor=processor)

    assert await runtime.get_transcript_preview() is None

    await runtime.close()
    await processor.close()


class SilentVAD:
    """Mark every integration-test frame as silence."""

    def is_speech(self, frame: AudioFrame) -> bool:
        """Return false without retaining the local frame."""
        del frame
        return False


class RecordingSTT:
    """Return deterministic text while retaining a defensive test copy."""

    def __init__(self, text: str, events: list[str] | None = None) -> None:
        """Configure one activation-time transcript."""
        self.text = text
        self.events = events
        self.frames: list[AudioFrame] = []

    def transcribe(self, audio: AudioFrame) -> str:
        """Record only the fake boundary input and return text."""
        self.frames.append((audio[0], audio[1].copy()))
        if self.events is not None:
            self.events.append("stt")
        return self.text


class ScriptedVAD:
    """Return a fixed speech/silence sequence for processor-backed wake tests."""

    def __init__(self, labels: list[bool]) -> None:
        """Initialize the local speech boundary script."""
        self.labels = labels

    def is_speech(self, frame: AudioFrame) -> bool:
        """Return the next scripted local VAD result."""
        del frame
        return self.labels.pop(0)


class DeferredFinalSTT:
    """Complete the wake immediately and defer the next local final until released."""

    def __init__(self) -> None:
        """Initialize per-segment start events and the deferred-final release."""
        self.started: list[asyncio.Event] = []
        self.release_followup = asyncio.Event()

    async def transcribe(self, audio: AudioFrame) -> str:
        """Return a wake, then hold the follow-up final at the local STT boundary."""
        del audio
        started = asyncio.Event()
        self.started.append(started)
        started.set()
        if len(self.started) == 1:
            return "Reachy"
        await self.release_followup.wait()
        return "stop"


@pytest.mark.asyncio
async def test_ambient_soak_stays_local_without_factory_or_cloud_traffic() -> None:
    """A deterministic five-minute-equivalent soak never constructs a handler."""
    factory_calls = 0
    processor = FakeLocalProcessor()

    def factory() -> FakeHandler:
        nonlocal factory_calls
        factory_calls += 1
        return FakeHandler()

    runtime = HybridRuntime(
        factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20, sample_rate=1_000),
        local_processor=processor,
    )
    for index in range(300):
        await runtime.handle_frame((1_000, np.full(1_000, index, dtype=np.int16)))

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert factory_calls == 0
    assert len(processor.frames) == 300
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert runtime.metrics.cloud_audio_bytes_sent == 0
    assert runtime.metrics.cloud_video_frames_sent == 0
    assert runtime.metrics.ambient_audio_seconds_buffered == pytest.approx(20)
    await runtime.close()


@pytest.mark.asyncio
async def test_openai_live_provider_stays_closed_until_activation_and_rebuilds_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI Live remains local until activation and each cloud generation is fresh."""
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", config.REALTIME_PROVIDER_OPENAI_LIVE)
    handlers = [FakeHandler(), FakeHandler()]
    factory_calls = 0

    def factory() -> FakeHandler:
        nonlocal factory_calls
        factory_calls += 1
        return handlers.pop(0)

    runtime = HybridRuntime(factory, ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=1_000))
    await runtime.handle_frame(frame(1_000, sample_rate=1_000))

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert factory_calls == 0
    assert runtime.metrics.cloud_audio_bytes_sent == 0
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0

    assert await runtime.activate("keyboard")
    first = runtime.active_handler
    assert first is not None
    await first.started.wait()
    assert factory_calls == 1

    await runtime.deactivate_cloud("reconnect")
    assert first.shutdown_calls == 1
    assert runtime.active_handler is None

    assert await runtime.activate("keyboard")
    second = runtime.active_handler
    assert second is not None and second is not first
    await second.started.wait()
    assert factory_calls == 2

    await runtime.close()
    await runtime.close()
    assert second.shutdown_calls == 1


@pytest.mark.asyncio
async def test_ambient_accepts_sdk_stereo_float_frame_without_rejection_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real SDK-shaped microphone input stays local without repeated warnings."""
    processor = FakeLocalProcessor()
    runtime = HybridRuntime(
        lambda: FakeHandler(),
        local_processor=processor,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=16_000),
    )
    sdk_frame = np.array([[0.25, 0.5], [-0.25, -0.5]], dtype=np.float32)

    with caplog.at_level(logging.WARNING, logger="reachy_mini_conversation_app.hybrid_runtime"):
        await runtime.handle_frame((16_000, sdk_frame))  # type: ignore[arg-type]

    assert "Ambient frame rejected" not in caplog.text
    np.testing.assert_array_equal(runtime.ambient_buffer.snapshot()[1], [12287, -12287])
    assert len(processor.frames) == 1
    np.testing.assert_array_equal(processor.frames[0][1], sdk_frame)
    await runtime.close()


@pytest.mark.asyncio
async def test_activation_acknowledges_before_factory_and_provider_start() -> None:
    """Local acknowledgment precedes provider construction and startup."""
    events: list[str] = []
    handler = FakeHandler()

    def acknowledge(reason: str) -> None:
        events.append(f"ack:{reason}")

    def factory() -> FakeHandler:
        events.append("factory")
        return handler

    handler.events = events
    runtime = HybridRuntime(factory, acknowledgement_callback=acknowledge)

    assert await runtime.activate("keyboard")
    await handler.started.wait()
    assert events[:3] == ["ack:keyboard", "factory", "start"]
    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    await runtime.close()
    assert handler.shutdown_calls == 1


@pytest.mark.asyncio
async def test_strategy_a_injects_completed_dinner_context_without_replaying_audio() -> None:
    """Gemini receives dinner context and the addressed turn while pre-wake PCM stays local."""
    clock = FakeClock()
    clock.value = 4.0
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT),
        clock=clock,
    )
    assert processor.transcript.add(TranscriptSegment("We should do something indoors tomorrow.", 1, 2, 1))
    assert processor.transcript.add(TranscriptSegment("Maybe the aquarium.", 3, 4, 2))
    assert processor.transcript.add(TranscriptSegment("Reachy, what do you think?", 4, 5, 3))
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20, sample_rate=16_000),
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        clock=clock,
    )
    prewake_audio = np.full(1_600, 4321, dtype=np.int16)
    await runtime.handle_frame((16_000, prewake_audio), capture_sequence=1)

    assert await runtime.activate("wake_word", addressed_text="what do you think?")
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()

    assert handler.get_initial_context() == (
        "We should do something indoors tomorrow. Maybe the aquarium. Reachy, what do you think?"
    )
    assert handler.say_texts == ["what do you think?"]
    assert handler.receive_frames == []
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert runtime.status.local_context_chars == len(handler.get_initial_context())
    assert runtime.status.local_context_strategy == "continuous_transcript"
    assert runtime.status.local_audio_seconds_observed == pytest.approx(0.1)
    await runtime.close()


@pytest.mark.asyncio
async def test_strategy_a_excludes_current_wake_question_from_seeded_context() -> None:
    """A live wake question is sent once as the addressed turn, not as background history."""
    clock = FakeClock()
    clock.value = 4.0
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT),
        clock=clock,
    )
    assert processor.transcript.add(TranscriptSegment("Earlier dinner context.", 1, 2, 1))
    assert processor.transcript.add(TranscriptSegment("Reachy, what do you think?", 3, 4, 2))
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        clock=clock,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy, what do you think?", 3, 4, 2))
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()

    assert handler.get_initial_context() == "Earlier dinner context."
    assert handler.say_texts == ["what do you think?"]
    await runtime.close()


@pytest.mark.asyncio
async def test_strategy_a_excludes_split_wake_and_followup_from_seeded_context() -> None:
    """Split-final handoff keeps prior context while omitting both current segments."""
    clock = FakeClock()
    clock.value = 6.0
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT),
        clock=clock,
    )
    assert processor.transcript.add(TranscriptSegment("Earlier dinner context.", 1, 2, 1))
    assert processor.transcript.add(TranscriptSegment("Reachy", 3, 4, 2))
    assert processor.transcript.add(TranscriptSegment("What do you think?", 5, 6, 3))
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        wake_followup_timeout_s=0.2,
        clock=clock,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 3, 4, 2))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
    runtime.observe_local_transcript(TranscriptSegment("What do you think?", 5, 6, 3))
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()

    assert handler.get_initial_context() == "Earlier dinner context."
    assert handler.say_texts == ["What do you think?"]
    await runtime.close()


@pytest.mark.asyncio
async def test_split_followup_ignores_stale_final_segment() -> None:
    """A final segment from before the wake cannot become its follow-up command."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.01,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 2, 3, 2))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
    runtime.observe_local_transcript(TranscriptSegment("stop", 1, 2, 1))
    await asyncio.wait_for(handler.started.wait(), timeout=1.0)

    assert runtime.status.local_commands_executed == 0
    assert handler.say_texts == []
    await runtime.close()


@pytest.mark.asyncio
async def test_split_followup_allows_local_stt_finalization_delay_without_cloud_audio() -> None:
    """A delayed next final still reaches the exact local route without uploading ambient PCM."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20, sample_rate=16_000),
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.05,
    )
    runtime.set_capture_barrier_provider(lambda: 1)
    await runtime.handle_frame((16_000, np.array([123], dtype=np.int16)), capture_sequence=1)
    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 2))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)

    await asyncio.sleep(0.01)
    runtime.observe_local_transcript(TranscriptSegment("stop", 3, 4, 3))
    await wait_until(lambda: runtime.status.local_commands_executed > 0 or handler.started.is_set(), attempts=500)

    assert runtime.status.local_commands_executed == 1
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert handler.shutdown_calls == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_processor_backed_split_followup_waits_for_deferred_local_final() -> None:
    """A deferred local STT final can arrive within the five-second waiter without cloud."""
    handler_factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    stt = DeferredFinalSTT()
    processor = AmbientContextProcessor(
        vad=ScriptedVAD([True, False, True, False]),
        stt=stt,
        config=AmbientContextConfig(
            strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT,
            min_speech_seconds=0.0,
            min_silence_seconds=0.0,
            vad_timeout_seconds=0.2,
            stt_timeout_seconds=1.0,
        ),
    )
    runtime = HybridRuntime(
        handler_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20, sample_rate=16_000),
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
    )
    processor.set_transcript_observer(runtime.observe_local_transcript)

    try:
        await runtime.handle_frame(frame(1))
        await runtime.handle_frame(frame(2))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)

        await runtime.handle_frame(frame(3))
        await runtime.handle_frame(frame(4))
        await wait_until(lambda: len(stt.started) >= 2)
        await stt.started[1].wait()
        assert runtime.status.local_commands_executed == 0
        assert handler_factory.call_count == 0

        stt.release_followup.set()
        await wait_until(lambda: runtime.status.local_commands_executed == 1)

        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
        handler_factory.assert_not_called()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_processor_backed_split_followup_after_deadline_is_not_local_command() -> None:
    """A local final completing after the waiter deadline cannot execute a command."""
    handler = FakeHandler()
    stt = DeferredFinalSTT()
    processor = AmbientContextProcessor(
        vad=ScriptedVAD([True, False, True, False]),
        stt=stt,
        config=AmbientContextConfig(
            strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT,
            min_speech_seconds=0.0,
            min_silence_seconds=0.0,
            vad_timeout_seconds=0.2,
            stt_timeout_seconds=1.0,
        ),
    )
    runtime = HybridRuntime(
        lambda: handler,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20, sample_rate=16_000),
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.01,
    )
    processor.set_transcript_observer(runtime.observe_local_transcript)

    try:
        await runtime.handle_frame(frame(1))
        await runtime.handle_frame(frame(2))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)

        await runtime.handle_frame(frame(3))
        await runtime.handle_frame(frame(4))
        await wait_until(lambda: len(stt.started) >= 2)
        await stt.started[1].wait()
        await handler.started.wait()

        stt.release_followup.set()
        await processor.drain()
        await asyncio.sleep(0)

        assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
        assert runtime.status.local_commands_executed == 0
        assert handler.say_texts == []
        assert runtime.status.local_command_followup_timeouts == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_strategy_b_acknowledges_then_transcribes_ram_before_factory() -> None:
    """Activation-time STT stays local and runs only after physical acknowledgment."""
    events: list[str] = []
    stt = RecordingSTT("indoors tomorrow, maybe the aquarium", events)
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=stt,
        config=AmbientContextConfig(strategy=AmbientContextStrategy.ACTIVATION_BUFFER),
    )
    handler = FakeHandler()

    def factory() -> FakeHandler:
        events.append("factory")
        return handler

    runtime = HybridRuntime(
        factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20, sample_rate=100),
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        acknowledgement_callback=lambda _reason: events.append("ack"),
    )
    await runtime.handle_frame((100, np.arange(100, dtype=np.int16)), capture_sequence=1)

    assert await runtime.activate("keyboard")
    await handler.started.wait()

    assert events[:3] == ["ack", "stt", "factory"]
    assert handler.get_initial_context() == "indoors tomorrow, maybe the aquarium"
    assert len(stt.frames) == 1
    np.testing.assert_array_equal(stt.frames[0][1], np.arange(100, dtype=np.int16))
    assert handler.receive_frames == []
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert processor.metrics.activation_stt_completed == 1
    status = runtime.status()
    assert status["local_context_strategy"] == "activation_buffer"
    assert status["local_stt_requests"] == 1
    assert status["local_stt_completed"] == 1
    assert status["local_stt_failures"] == 0
    assert status["local_stt_timeouts"] == 0
    assert status["local_stt_inflight_peak"] == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_wake_local_stop_executes_without_constructing_cloud_handler() -> None:
    """An exact addressed stop command acknowledges and remains fully local."""
    factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    movement_manager = MagicMock()
    executor = LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=movement_manager))
    acknowledgements: list[str] = []
    runtime = HybridRuntime(
        factory,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=executor,
        acknowledgement_callback=acknowledgements.append,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy, stop", 1, 2, 1))
    await wait_until(lambda: runtime.status.local_commands_executed == 1)

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert acknowledgements == ["wake_word"]
    movement_manager.clear_move_queue.assert_called_once_with()
    factory.assert_not_called()
    assert runtime.metrics.cloud_audio_bytes_sent == 0
    assert runtime.status.wake_detections == 1
    assert runtime.status.wake_final_update_count == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_partial_wake_defers_to_final_local_command_and_next_wake() -> None:
    """Partial wake text cannot open cloud before its final local command arrives."""
    factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = (
        [0.73, 0.1, -0.2, 0.3, -0.4, 0.5, -0.6],
        [0.11, -0.22],
    )
    movement_manager = MagicMock()
    executor = LocalActionExecutor(ToolDependencies(reachy_mini=robot, movement_manager=movement_manager))
    detector = TranscriptWakeWordDetector(cooldown_seconds=0)
    runtime = HybridRuntime(
        factory,
        wake_detector=detector,
        intent_router=LocalIntentRouter(),
        action_executor=executor,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1, final=False))
    factory.assert_not_called()
    assert runtime.status.wake_detections == 0

    runtime.observe_local_transcript(TranscriptSegment("Reachy, look left", 1, 2, 1, final=True))
    await wait_until(lambda: runtime.status.local_commands_executed == 1)
    runtime.observe_local_transcript(TranscriptSegment("Reachy, look right", 3, 4, 2, final=True))
    await wait_until(lambda: runtime.status.local_commands_executed == 2)

    factory.assert_not_called()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert detector.metrics_snapshot().detection_count == 2
    assert movement_manager.queue_move.call_count == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_executes_exact_command_without_cloud() -> None:
    """A wake-only final and immediate exact follow-up stay entirely local."""
    factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    movement_manager = MagicMock()
    executor = LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=movement_manager))
    acknowledgements: list[str] = []
    runtime = HybridRuntime(
        factory,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=executor,
        acknowledgement_callback=acknowledgements.append,
        wake_followup_timeout_s=0.2,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    runtime.observe_local_transcript(TranscriptSegment("stop", 2, 3, 2))
    await wait_until(lambda: runtime.status.local_commands_executed == 1)

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert acknowledgements == ["wake_word"]
    assert movement_manager.clear_move_queue.call_count == 1
    assert runtime.status.local_command_escalations == 0
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    factory.assert_not_called()
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_uncertain_text_escalates_with_text_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An uncertain split follow-up opens cloud with that text, without logging it."""
    sentinel = "private household follow-up"
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.2,
    )

    with caplog.at_level(logging.INFO):
        runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        runtime.observe_local_transcript(TranscriptSegment(sentinel, 2, 3, 2))
        await handler.started.wait()
        handler.set_connected(True)
        await handler.say_called.wait()

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert handler.say_texts == [sentinel]
    assert runtime.status.local_command_escalations == 1
    assert sentinel not in caplog.text
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_timeout_uses_normal_cloud_activation() -> None:
    """A missing split command times out and opens cloud without an injected turn."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.01,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    await handler.started.wait()

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert handler.say_texts == []
    assert runtime.status.local_command_escalations == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_timeout_preserves_observed_fifo_for_cloud() -> None:
    """A timeout keeps the locally observed post-barrier frame for the provider."""
    processor = FakeLocalProcessor()
    handler = FakeHandler(drop_unready_frames=True)
    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.05,
    )
    runtime.set_capture_barrier_provider(lambda: 0)

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
    await runtime.handle_frame(frame(9), capture_sequence=1)
    await handler.started.wait()
    handler.set_connected(True)
    await wait_until(lambda: len(handler.receive_frames) == 1)

    assert len(processor.frames) == 1
    assert int(processor.frames[0][1][0]) == 9
    assert int(handler.receive_frames[0][1][0]) == 9
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_cancellation_releases_waiter() -> None:
    """Deactivation cancels a pending split-follow-up waiter and returns ambient."""
    runtime = HybridRuntime(
        lambda: FakeHandler(),
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=15,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
    waiter = runtime._wake_followup_future
    local_event_tasks = tuple(runtime._local_event_tasks)
    assert waiter is not None and not waiter.done()

    assert await runtime.deactivate_cloud("test_cancel")
    await asyncio.gather(*local_event_tasks, return_exceptions=True)

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime._wake_followup_future is None
    assert waiter.cancelled()
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_close_before_activation_starts_releases_waiter() -> None:
    """Shutdown releases a waiter even when it cancels its not-yet-started task."""
    runtime = HybridRuntime(
        lambda: FakeHandler(),
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=15,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    waiter = runtime._wake_followup_future
    assert waiter is not None and not waiter.done()

    await runtime.close()

    assert runtime._wake_followup_future is None
    assert waiter.cancelled()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT


@pytest.mark.asyncio
async def test_split_wake_followup_dual_frame_path_observes_and_preserves_fifo_on_local_success() -> None:
    """A post-barrier follow-up frame feeds local STT and is cleared after local success."""
    processor = TranscriptOnFrameProcessor()
    factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = (
        [0.73, 0.1, -0.2, 0.3, -0.4, 0.5, -0.6],
        [0.11, -0.22],
    )
    movement_manager = MagicMock()
    runtime = HybridRuntime(
        factory,
        local_processor=processor,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=robot, movement_manager=movement_manager)),
        wake_followup_timeout_s=0.2,
    )
    runtime.set_capture_barrier_provider(lambda: 0)
    processor.transcript_callback = lambda _frame: runtime.observe_local_transcript(
        TranscriptSegment("look left", 2, 3, 2)
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
    await runtime.handle_frame(frame(7), capture_sequence=1)
    await wait_until(lambda: runtime.status.local_commands_executed == 1)

    assert len(processor.frames) == 1
    assert int(processor.frames[0][1][0]) == 7
    assert len(runtime._postactivation_frames) == 0
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    factory.assert_not_called()
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_dual_frame_path_preserves_fifo_for_cloud_escalation() -> None:
    """An uncertain post-barrier follow-up keeps queued audio for provider readiness."""
    sentinel = "private cloud follow-up"
    processor = TranscriptOnFrameProcessor()
    handler = FakeHandler(drop_unready_frames=True)
    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.2,
    )
    runtime.set_capture_barrier_provider(lambda: 0)
    processor.transcript_callback = lambda _frame: runtime.observe_local_transcript(
        TranscriptSegment(sentinel, 2, 3, 2)
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
    await runtime.handle_frame(frame(8), capture_sequence=1)
    await handler.started.wait()
    handler.set_connected(True)
    await wait_until(lambda: len(handler.receive_frames) == 1)
    await handler.say_called.wait()

    assert int(handler.receive_frames[0][1][0]) == 8
    assert handler.say_texts == [sentinel]
    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    await runtime.close()


@pytest.mark.asyncio
async def test_split_wake_followup_late_segment_after_timeout_is_not_reused() -> None:
    """A final arriving after timeout cannot become a second local command."""
    handler = FakeHandler()
    movement_manager = MagicMock()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(
            ToolDependencies(reachy_mini=MagicMock(), movement_manager=movement_manager)
        ),
        wake_followup_timeout_s=0.01,
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
    await handler.started.wait()
    runtime.observe_local_transcript(TranscriptSegment("stop", 2, 3, 2))
    await asyncio.sleep(0)

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert movement_manager.clear_move_queue.call_count == 0
    assert handler.say_texts == []
    await runtime.close()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf"), 15.1])
def test_wake_followup_timeout_must_be_finite_positive_and_bounded(timeout: float) -> None:
    """Wake follow-up waits cannot be disabled or made unbounded accidentally."""
    with pytest.raises(ValueError, match="Wake follow-up timeout"):
        HybridRuntime(lambda: FakeHandler(), wake_followup_timeout_s=timeout)


@pytest.mark.asyncio
async def test_uncertain_wake_utterance_escalates_to_cloud() -> None:
    """Anything outside the exact local allowlist opens the ordinary provider path."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
    )

    runtime.observe_local_transcript(TranscriptSegment("Reachy, what do you think?", 1, 2, 1))
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert runtime.status.local_command_escalations == 1
    assert runtime.status.local_commands_executed == 0
    assert handler.say_texts == ["what do you think?"]
    await runtime.close()


@pytest.mark.asyncio
async def test_same_final_router_rejection_reports_metadata_reason() -> None:
    """A same-final non-allowlisted suffix records its router reason without text."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
    )

    try:
        runtime.observe_local_transcript(TranscriptSegment("Reachy, look left please", 1, 2, 1))
        await handler.started.wait()
        handler.set_connected(True)
        await handler.say_called.wait()

        status = runtime.status
        assert status.local_command_last_outcome is LocalCommandOutcome.ROUTER_REJECTED
        assert status.local_command_last_router_reason is IntentDecisionReason.UNSUPPORTED_COMMAND
        assert status.local_command_router_rejections == 1
        assert status.local_command_followup_timeouts == 0
        assert status.local_command_failures == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wake_followup_timeout_reports_metadata_reason() -> None:
    """A wake-only final records timeout separately from router rejection."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
        wake_followup_timeout_s=0.01,
    )

    try:
        runtime.observe_local_transcript(TranscriptSegment("Reachy", 1, 2, 1))
        await handler.started.wait()
        handler.set_connected(True)
        await wait_until(lambda: runtime.status.local_command_followup_timeouts == 1)

        status = runtime.status
        assert status.local_command_last_outcome is LocalCommandOutcome.FOLLOWUP_TIMEOUT
        assert status.local_command_last_router_reason is None
        assert status.local_command_router_rejections == 0
        assert status.local_command_failures == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_local_execution_failure_reports_metadata_reason() -> None:
    """An exact route whose local executor fails records execution failure metadata."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
    )

    try:
        runtime.observe_local_transcript(TranscriptSegment("Reachy, be quiet", 1, 2, 1))
        await handler.started.wait()

        status = runtime.status
        assert status.local_command_last_outcome is LocalCommandOutcome.LOCAL_EXECUTION_FAILED
        assert status.local_command_last_router_reason is None
        assert status.local_command_router_rejections == 0
        assert status.local_command_followup_timeouts == 0
        assert status.local_command_failures == 1
        assert status.local_command_escalations == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_activation_text_failure_is_private_and_keeps_session_usable(caplog) -> None:
    """A failed addressed-turn send logs only error metadata and leaves cloud active."""
    sentinel = "private addressed household question"
    handler = FakeHandler()
    handler.say = AsyncMock(side_effect=RuntimeError(sentinel))
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate("wake_word", addressed_text=sentinel)
    await handler.started.wait()
    with caplog.at_level(logging.WARNING):
        handler.set_connected(True)
        await wait_until(lambda: handler.say.await_count == 1)

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text
    await runtime.close()


@pytest.mark.asyncio
async def test_addressed_text_completes_before_queued_postactivation_audio() -> None:
    """A post-connected frame queues promptly until the addressed turn completes."""
    handler = FakeHandler(drop_unready_frames=True)
    say_started = asyncio.Event()
    release_say = asyncio.Event()
    events: list[str] = []

    async def blocked_say(text: str) -> None:
        events.append(f"say_start:{text}")
        say_started.set()
        await release_say.wait()
        events.append("say_done")

    handler.say = blocked_say
    runtime = HybridRuntime(lambda: handler)
    runtime.set_capture_barrier_provider(lambda: 0)

    assert await runtime.activate("wake_word", addressed_text="what do you think?")
    await handler.started.wait()
    handler.set_connected(True)
    await say_started.wait()

    await asyncio.wait_for(runtime.handle_frame(frame(7), capture_sequence=1), timeout=0.1)
    assert handler.receive_frames == []

    release_say.set()
    await wait_until(lambda: len(handler.receive_frames) == 1)
    assert events == ["say_start:what do you think?", "say_done"]
    assert int(handler.receive_frames[0][1][0]) == 7
    await runtime.close()


@pytest.mark.asyncio
async def test_deactivation_cancels_blocked_activation_text_send() -> None:
    """Session teardown cannot leave a locally derived text-send task behind."""
    handler = FakeHandler()
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def blocked_say(_text: str) -> None:
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise

    handler.say = blocked_say
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate("wake_word", addressed_text="what do you think?")
    await handler.started.wait()
    handler.set_connected(True)
    await send_started.wait()

    assert await runtime.deactivate_cloud("test")
    assert send_cancelled.is_set()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_local_action_escalates_instead_of_silently_guessing() -> None:
    """An unavailable local callback hands the addressed request to Gemini."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())),
    )

    assert await runtime.activate("wake_word", addressed_text="be quiet")
    await handler.started.wait()

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert runtime.status.local_command_failures == 1
    assert runtime.status.local_command_escalations == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_concurrent_activation_creates_exactly_one_generation() -> None:
    """Concurrent keyboard requests share one acknowledgment and handler."""
    acknowledgement_started = asyncio.Event()
    release_acknowledgement = asyncio.Event()
    acknowledgement_calls = 0
    factory_calls = 0
    handler = FakeHandler()

    async def acknowledge(reason: str) -> None:
        nonlocal acknowledgement_calls
        acknowledgement_calls += 1
        acknowledgement_started.set()
        await release_acknowledgement.wait()

    def factory() -> FakeHandler:
        nonlocal factory_calls
        factory_calls += 1
        return handler

    runtime = HybridRuntime(factory, acknowledgement_callback=acknowledge)
    first = asyncio.create_task(runtime.activate())
    await acknowledgement_started.wait()
    second = asyncio.create_task(runtime.activate())
    release_acknowledgement.set()

    assert await first
    assert await second
    assert acknowledgement_calls == 1
    assert factory_calls == 1
    assert runtime.generation == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_capture_barrier_excludes_queued_preactivation_frames() -> None:
    """Frames at or before the activation capture barrier never reach a provider."""
    processor = FakeLocalProcessor()
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
    )
    runtime.set_capture_barrier_provider(lambda: 10)
    await runtime.handle_frame(frame(1), capture_sequence=1)
    assert await runtime.activate()
    handler.set_connected(True)
    await runtime.handle_frame(frame(10), capture_sequence=10)
    await runtime.handle_frame(frame(11), capture_sequence=11)

    assert [int(audio[0]) for _, audio in handler.receive_frames] == [11]
    assert len(processor.frames) == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_cloud_forwards_only_postactivation_frames_and_not_ring_snapshot() -> None:
    """Activation does not replay ambient ring contents into the provider."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    await runtime.handle_frame(frame(7))
    assert await runtime.activate()
    handler.set_connected(True)
    await runtime.handle_frame(frame(8))

    assert [int(audio[0]) for _, audio in handler.receive_frames] == [8]
    assert int(runtime.ambient_buffer.snapshot()[1][0]) == 7
    await runtime.close()


@pytest.mark.asyncio
async def test_local_command_buffers_only_postactivation_audio_during_acknowledgement() -> None:
    """The activation seam keeps the capture barrier while retaining the first new utterance."""
    acknowledgement_started = asyncio.Event()
    release_acknowledgement = asyncio.Event()
    processor = FakeLocalProcessor()
    handler = FakeHandler(drop_unready_frames=True)

    async def acknowledge(_reason: str) -> None:
        acknowledgement_started.set()
        await release_acknowledgement.wait()

    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
        acknowledgement_callback=acknowledge,
    )
    runtime.set_capture_barrier_provider(lambda: 10)
    await runtime.handle_frame(frame(1), capture_sequence=10)

    activation = asyncio.create_task(runtime.activate("keyboard"))
    await acknowledgement_started.wait()
    await runtime.handle_frame(frame(2), capture_sequence=10)
    await runtime.handle_frame(frame(3), capture_sequence=11)
    release_acknowledgement.set()

    assert await activation
    handler.set_connected(True)
    await runtime.handle_frame(frame(4), capture_sequence=12)

    assert [int(audio[0]) for _, audio in handler.receive_frames] == [3, 4]
    assert [int(audio[0]) for _, audio in processor.frames] == [1, 2]
    await runtime.close()


@pytest.mark.asyncio
async def test_provider_traffic_hook_counts_only_explicit_successes() -> None:
    """Frame observation alone never increments cloud traffic counters."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    assert await runtime.activate()
    await runtime.handle_frame(frame(1))
    assert runtime.metrics.cloud_audio_bytes_sent == 0

    runtime.record_provider_traffic("audio", 12)
    runtime.record_provider_traffic("video", 999)
    snapshot = runtime.metrics_snapshot()
    assert snapshot.cloud_audio_bytes_sent == 12
    assert snapshot.cloud_video_frames_sent == 1
    with pytest.raises(Exception):
        snapshot.cloud_audio_bytes_sent = 4  # type: ignore[misc]
    await runtime.close()


@pytest.mark.asyncio
async def test_provider_setup_failure_returns_to_ambient_and_shutdowns_once() -> None:
    """A long-lived provider setup failure does not kill local ambient mode."""
    handler = FakeHandler(fail_start=True)
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate()
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT)
    assert runtime.active_handler is None
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_activation_cleanup_keeps_runtime_stopping() -> None:
    """A constructed provider that cannot close remains fenced from ambient mode."""

    class FailingConfigurationHandler(FakeHandler):
        def set_initial_context(self, _context: str) -> None:
            raise RuntimeError("provider configuration sentinel")

    handler = FailingConfigurationHandler(fail_shutdown=True)
    runtime = HybridRuntime(lambda: handler)

    assert not await runtime.activate("failed_configuration")
    assert runtime.mode is RuntimeMode.STOPPING
    assert runtime.status.shutdown_error == "provider_cleanup_failed"
    assert handler.shutdown_calls == 1
    assert not await runtime.activate("blocked_failed_configuration")
    await runtime.close()


@pytest.mark.asyncio
async def test_provider_receive_failure_returns_to_ambient() -> None:
    """A provider receive error closes the generation and resumes local capture."""
    handler = FakeHandler(fail_receive=True)
    runtime = HybridRuntime(lambda: handler)
    assert await runtime.activate()
    handler.set_connected(True)

    await runtime.handle_frame(frame(3))
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_cloud_frames_do_not_refresh_inactivity_without_provider_activity() -> None:
    """Continuous microphone frames cannot keep an otherwise idle session alive."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=5, clock=clock)
    assert await runtime.activate()
    handler.set_connected(True)

    for value in range(5):
        clock.advance(1)
        await runtime.handle_frame(frame(value))

    assert await runtime.check_inactivity()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_inactivity_waits_for_connection_and_resets_after_reconnect() -> None:
    """Setup/disconnected time is excluded and each reconnect starts a new baseline."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=5, clock=clock)
    assert await runtime.activate()

    clock.advance(100)
    assert not await runtime.check_inactivity()

    handler.set_connected(True)
    clock.advance(4)
    assert not await runtime.check_inactivity()
    handler.set_connected(False)
    clock.advance(100)
    assert not await runtime.check_inactivity()

    handler.set_connected(True)
    clock.advance(4)
    assert not await runtime.check_inactivity()
    clock.advance(1)
    assert await runtime.check_inactivity()
    assert runtime.metrics.gemini_session_duration == pytest.approx(9)
    await runtime.close()


@pytest.mark.asyncio
async def test_inactivity_pauses_during_long_user_utterance_then_counts_from_stop() -> None:
    """Semantic speech boundaries prevent timeout during speech and reset its clock."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=5, clock=clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation

    runtime._on_handler_activity(handler, generation, "user_speech_started")
    clock.advance(100)
    assert not await runtime.check_inactivity()

    runtime._on_handler_activity(handler, generation, "user_speech_stopped")
    clock.advance(4)
    assert not await runtime.check_inactivity()
    runtime._on_handler_activity(handler, generation, "user_transcription_completed")
    clock.advance(4)
    assert not await runtime.check_inactivity()
    clock.advance(1)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_disconnect_mid_utterance_does_not_block_inactivity_after_reconnect() -> None:
    """A speech start without its stop cannot outlive the provider connection."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=5, clock=clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime._on_handler_activity(handler, runtime.generation, "user_speech_started")

    handler.set_connected(False)
    handler.set_connected(True)
    clock.advance(5)
    assert await runtime.check_inactivity()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_postactivation_queue_survives_handshake_and_flushes_before_new_frames() -> None:
    """Frames after the activation barrier wait for readiness and preserve order."""
    handler = FakeHandler(drop_unready_frames=True)
    runtime = HybridRuntime(lambda: handler)
    runtime.set_capture_barrier_provider(lambda: 10)
    assert await runtime.activate()

    for value in (11, 12):
        await runtime.handle_frame(
            (16_000, np.full(12_000, value, dtype=np.int16)),
            capture_sequence=value,
        )
    assert handler.receive_frames == []

    handler.set_connected(True)
    await runtime.handle_frame(
        (16_000, np.full(12_000, 13, dtype=np.int16)),
        capture_sequence=13,
    )

    assert [int(audio[0]) for _, audio in handler.receive_frames] == [11, 12, 13]
    await runtime.close()


@pytest.mark.asyncio
async def test_postactivation_queue_drops_oldest_at_bounded_handshake_capacity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SDK stereo frames use sample-axis duration at the 20-second FIFO boundary."""
    handler = FakeHandler(drop_unready_frames=True)
    runtime = HybridRuntime(lambda: handler)
    runtime.set_capture_barrier_provider(lambda: 0)
    assert await runtime.activate()
    caplog.set_level(logging.WARNING)

    values = tuple(range(1000, 1021))
    for value in values:
        await runtime.handle_frame(
            (16_000, np.full((16_000, 2), value, dtype=np.float32)),
            capture_sequence=value + 1,
        )

    assert runtime.postactivation_frames_dropped == 1
    assert "Postactivation audio queue full" in caplog.text
    assert all(str(value) not in caplog.text for value in values)
    handler.set_connected(True)
    await wait_until(lambda: len(handler.receive_frames) == 20)
    assert [int(audio[0, 0]) for _, audio in handler.receive_frames] == list(values[1:])
    assert runtime.status().get("postactivation_frames_dropped") == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_deactivation_clears_handshake_queue_before_fresh_generation() -> None:
    """Queued frames from one generation cannot cross a reconnect boundary."""
    first = FakeHandler(drop_unready_frames=True)
    second = FakeHandler(drop_unready_frames=True)
    handlers = [first, second]
    runtime = HybridRuntime(lambda: handlers.pop(0))
    runtime.set_capture_barrier_provider(lambda: 0)

    assert await runtime.activate()
    await runtime.handle_frame(frame(101), capture_sequence=1)
    assert await runtime.deactivate_cloud("reconnect")
    assert first.receive_frames == []

    assert await runtime.activate()
    second.set_connected(True)
    await runtime.handle_frame(frame(202), capture_sequence=2)
    assert [int(audio[0]) for _, audio in second.receive_frames] == [202]
    await runtime.close()


@pytest.mark.asyncio
async def test_provider_cancellation_clears_unflushed_postactivation_audio() -> None:
    """A cancelled provider generation drops its handshake queue before shutdown."""
    handler = FakeHandler(drop_unready_frames=True)
    runtime = HybridRuntime(lambda: handler)
    runtime.set_capture_barrier_provider(lambda: 0)
    assert await runtime.activate()
    await handler.started.wait()
    await runtime.handle_frame(frame(404), capture_sequence=1)

    provider_task = runtime._provider_task
    assert provider_task is not None
    provider_task.cancel()
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT)

    assert runtime.active_handler is None
    assert handler.receive_frames == []
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_inactivity_uses_conversation_activity_and_counts_connected_duration() -> None:
    """Ambient frames do not refresh the cloud timer; connected time is cumulative."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=5, clock=clock)
    await runtime.handle_frame(frame(0))
    assert await runtime.activate()
    handler.set_connected(True)
    clock.advance(3)
    clock.advance(2)

    assert await runtime.check_inactivity()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.metrics.gemini_session_duration == pytest.approx(5)
    await runtime.close()


@pytest.mark.asyncio
async def test_never_connected_setup_does_not_report_session_duration() -> None:
    """Provider setup time before a connection is ready is not Gemini session time."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, clock=clock)
    assert await runtime.activate()
    clock.advance(10)
    await runtime.deactivate_cloud("test")

    assert runtime.metrics.gemini_session_duration == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_cancellation_during_activation_closes_without_factory_leak() -> None:
    """Cancelling an activation waiting for acknowledgment returns to ambient."""
    acknowledgement_started = asyncio.Event()
    factory_calls = 0

    async def acknowledge(reason: str) -> None:
        acknowledgement_started.set()
        await asyncio.Event().wait()

    def factory() -> FakeHandler:
        nonlocal factory_calls
        factory_calls += 1
        return FakeHandler()

    runtime = HybridRuntime(factory, acknowledgement_callback=acknowledge)
    activation = asyncio.create_task(runtime.activate())
    await acknowledgement_started.wait()
    activation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await activation

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert factory_calls == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_stale_generation_callbacks_cannot_reset_new_cloud_generation() -> None:
    """Late activity or traffic from an old generation is ignored."""
    handlers = [FakeHandler(), FakeHandler()]
    runtime = HybridRuntime(lambda: handlers.pop(0))
    assert await runtime.activate()
    old_handler = runtime.active_handler
    assert old_handler is not None
    old_generation = runtime.generation
    await runtime.deactivate_cloud("reconnect")
    assert await runtime.activate()
    new_handler = runtime.active_handler
    assert new_handler is not None and new_handler is not old_handler
    runtime._on_handler_activity(old_handler, old_generation, "stale")
    runtime._on_handler_traffic(old_handler, old_generation, "audio", 99)

    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert runtime.active_handler is new_handler
    assert runtime.metrics.cloud_audio_bytes_sent == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_fresh_activation_uses_fresh_handler_and_idempotent_close() -> None:
    """Reconnects create a fresh handler and shutdown remains exactly once."""
    handlers = [FakeHandler(), FakeHandler()]
    first, second = handlers
    runtime = HybridRuntime(lambda: handlers.pop(0))
    assert await runtime.activate()
    await runtime.deactivate_cloud("reconnect")
    assert first.shutdown_calls == 1
    assert await runtime.activate()
    assert runtime.active_handler is second

    await runtime.close()
    await runtime.close()
    assert first.shutdown_calls == 1
    assert second.shutdown_calls == 1


@pytest.mark.asyncio
async def test_shutdown_tracking_does_not_skip_fresh_objects_after_id_reuse() -> None:
    """Every fresh handler is shut down even when prior objects become unreachable."""
    shutdown_count = 0

    def factory() -> FakeHandler:
        handler = FakeHandler()

        async def shutdown() -> None:
            nonlocal shutdown_count
            shutdown_count += 1
            await FakeHandler.shutdown(handler)

        handler.shutdown = shutdown  # type: ignore[method-assign]
        return handler

    runtime = HybridRuntime(factory)
    for _ in range(100):
        assert await runtime.activate()
        await runtime.deactivate_cloud("next")

    assert shutdown_count == 100
    await runtime.close()


@pytest.mark.asyncio
async def test_status_is_metadata_only_and_async_calls_reject_foreign_loop() -> None:
    """UI snapshots omit handler objects and runtime mutations stay owner-loop-bound."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    assert await runtime.activate()

    snapshot = runtime.status
    status_payload = snapshot()
    assert "active_handler" not in status_payload
    assert status_payload["active"] is True
    with pytest.raises(RuntimeError, match="foreign event loop"):
        await asyncio.to_thread(lambda: asyncio.run(runtime.emit()))
    await runtime.close()


@pytest.mark.asyncio
async def test_cancellation_during_close_finishes_cleanup_before_propagating() -> None:
    """Close shields provider teardown so cancellation cannot leak a live handler."""
    handler = FakeHandler(block_shutdown=True)
    runtime = HybridRuntime(lambda: handler)
    assert await runtime.activate()

    closing = asyncio.create_task(runtime.close())
    await handler.shutdown_started.wait()
    closing.cancel()
    handler.release_shutdown.set()

    with pytest.raises(asyncio.CancelledError):
        await closing
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.active_handler is None
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_busy_and_estimated_playback_gate_inactivity() -> None:
    """Provider busy state and queued playback suppress ordinary inactivity."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=5, clock=clock)

    assert await runtime.activate()
    handler.set_connected(True)
    handler._emit_interaction_busy(True)
    clock.advance(10)
    assert runtime.status.interaction_busy is True
    assert not await runtime.check_inactivity()

    handler._emit_interaction_busy(False)
    runtime.note_playback_queued(4)
    clock.advance(3)
    assert runtime.status.playback_active is True
    assert not await runtime.check_inactivity()
    clock.advance(1)
    assert runtime.status.playback_active is False
    assert await runtime.check_inactivity()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_shutdown_timeout_fences_media_and_denies_reactivation_until_late_cleanup() -> None:
    """A resistant provider keeps the runtime fenced until it confirms closure."""
    processor = FakeLocalProcessor()
    handler = FakeHandler(block_shutdown=True)
    handler.output_queue.put_nowait(frame(99))
    runtime = HybridRuntime(lambda: handler, local_processor=processor)
    runtime._SHUTDOWN_TIMEOUT_S = 0.01

    assert await runtime.activate()
    handler.set_connected(True)
    previous_generation = runtime.generation

    assert not await runtime.deactivate_cloud("timeout_test")
    assert runtime.mode is RuntimeMode.STOPPING
    assert runtime.active_handler is None
    assert runtime.generation == previous_generation + 1
    assert runtime.status.shutdown_error == "provider_cleanup_timeout"
    assert handler.output_queue.empty()
    assert handler.deps.movement_manager.clear_move_queue.called
    assert handler.deps.movement_manager.set_listening.called
    assert handler.deps.movement_manager.set_speaking.called

    await runtime.handle_frame(frame(1))
    assert processor.frames == []
    assert not await runtime.activate("blocked_while_stopping")

    handler.release_shutdown.set()
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT)
    assert runtime.status.shutdown_error is None
    assert await runtime.activate("after_confirmed_cleanup")
    await runtime.close()


@pytest.mark.asyncio
async def test_shutdown_error_keeps_runtime_stopping_and_does_not_retry() -> None:
    """A failed provider close is visible as a permanent reactivation fence."""
    handler = FakeHandler(fail_shutdown=True)
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate()
    assert not await runtime.deactivate_cloud("shutdown_error_test")
    assert runtime.mode is RuntimeMode.STOPPING
    assert runtime.status.shutdown_error == "provider_cleanup_failed"
    assert not await runtime.activate("retry_after_shutdown_error")
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_resistant_activation_factory_stays_stopping_until_orphan_handler_closes() -> None:
    """A factory that ignores cancellation cannot reopen ambient mode early."""
    release_factory = asyncio.Event()
    handler = FakeHandler()

    async def factory() -> FakeHandler:
        try:
            await release_factory.wait()
        except asyncio.CancelledError:
            await release_factory.wait()
        return handler

    runtime = HybridRuntime(factory)
    runtime._SHUTDOWN_TIMEOUT_S = 0.01
    activation = asyncio.create_task(runtime.activate("factory_race"))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)

    assert not await runtime.deactivate_cloud("factory_race_stop")
    assert runtime.mode is RuntimeMode.STOPPING
    assert not await runtime.activate("blocked_factory_race")

    release_factory.set()
    with pytest.raises(asyncio.CancelledError):
        await activation
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT)
    assert handler.shutdown_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_orphan_handler_shutdown_failure_keeps_activation_fence() -> None:
    """An orphan provider that fails shutdown leaves STOPPING visible."""
    release_factory = asyncio.Event()
    handler = FakeHandler(fail_shutdown=True)

    async def factory() -> FakeHandler:
        try:
            await release_factory.wait()
        except asyncio.CancelledError:
            await release_factory.wait()
        return handler

    runtime = HybridRuntime(factory)
    activation = asyncio.create_task(runtime.activate("orphan_failure"))
    await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)

    stop_task = asyncio.create_task(runtime.deactivate_cloud("orphan_failure_stop"))
    await asyncio.sleep(0)
    release_factory.set()
    with pytest.raises(RuntimeError, match="provider shutdown sentinel"):
        await activation
    assert not await stop_task
    assert runtime.mode is RuntimeMode.STOPPING
    assert runtime.status.shutdown_error == "provider_cleanup_failed"
    assert not await runtime.activate("blocked_orphan_failure")
    await runtime.close()


@pytest.mark.asyncio
async def test_local_processor_errors_are_nonfatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An open runtime keeps processor errors local and logs only error metadata."""
    sentinel = 31415
    processor = FakeLocalProcessor(fail=True)
    runtime = HybridRuntime(lambda: FakeHandler(), local_processor=processor)
    caplog.set_level(logging.WARNING)
    await runtime.handle_frame(frame(sentinel))

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert "RuntimeError" in caplog.text
    assert str(sentinel) not in caplog.text
    await runtime.close()


@pytest.mark.asyncio
async def test_after_close_frames_are_dropped_without_local_observation() -> None:
    """A frame racing after close cannot reach the local processor."""
    processor = FakeLocalProcessor()
    runtime = HybridRuntime(lambda: FakeHandler(), local_processor=processor)

    await runtime.close()
    await runtime.handle_frame(frame(2718))

    assert processor.frames == []


@pytest.mark.asyncio
async def test_local_processing_backpressure_is_bounded_and_private() -> None:
    """A blocked local processor drops only bounded local work, never cloud audio."""

    class BlockingLocalProcessor:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.frames: list[int] = []

        async def observe_frame(self, observed_frame: AudioFrame) -> None:
            self.frames.append(int(observed_frame[1][0]))
            self.started.set()
            await self.release.wait()

    processor = BlockingLocalProcessor()
    factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    runtime = HybridRuntime(factory, local_processor=processor)

    try:
        await runtime.handle_frame(frame(0), wait_for_local_processing=False)
        await processor.started.wait()
        for value in range(1, 12):
            await runtime.handle_frame(frame(value), wait_for_local_processing=False)

        status = runtime.status
        assert status.local_processing_frames_dropped >= 1
        assert status.local_processing_frames_dropped <= 3
        assert status.local_command_last_outcome is None
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
        factory.assert_not_called()

        processor.release.set()
        await wait_until(lambda: len(processor.frames) == 9)
        assert processor.frames == list(range(9))
    finally:
        processor.release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_handle_frame_waits_for_successful_async_local_processing() -> None:
    """The default direct runtime call completes after async local observation."""

    class CompletingLocalProcessor:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.completed = asyncio.Event()

        async def observe_frame(self, _frame: AudioFrame) -> None:
            self.started.set()
            await asyncio.sleep(0)
            self.completed.set()

    processor = CompletingLocalProcessor()
    runtime = HybridRuntime(lambda: FakeHandler(), local_processor=processor)

    try:
        await runtime.handle_frame(frame(0))
        assert processor.started.is_set()
        assert processor.completed.is_set()
        assert runtime.status.local_processing_frames_dropped == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_close_cancels_blocked_local_processing_and_queued_waiter() -> None:
    """Runtime close cancels both the active local observation and queued caller."""

    class CancellationLocalProcessor:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.never = asyncio.Event()

        async def observe_frame(self, _frame: AudioFrame) -> None:
            self.started.set()
            try:
                await self.never.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    processor = CancellationLocalProcessor()
    runtime = HybridRuntime(lambda: FakeHandler(), local_processor=processor)

    await runtime.handle_frame(frame(0), wait_for_local_processing=False)
    await processor.started.wait()
    queued = asyncio.create_task(runtime.handle_frame(frame(1)))
    await asyncio.sleep(0)

    await runtime.close()

    with pytest.raises(asyncio.CancelledError):
        await queued
    assert processor.cancelled.is_set()
    assert runtime.status.closed
    assert runtime.status.local_processing_frames_dropped == 0


@pytest.mark.asyncio
async def test_emit_consumes_one_active_handler_queue_without_duplicate_queue() -> None:
    """Runtime delegates output to the selected handler's existing queue."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    assert await runtime.activate()
    output = AdditionalOutputs({"role": "assistant", "content": "hello"})
    await handler.output_queue.put(output)

    assert await runtime.emit() is output
    assert await runtime.emit() is None
    await runtime.close()


@pytest.mark.asyncio
async def test_ambient_emit_waits_without_spinning_and_wakes_on_activation() -> None:
    """Ambient playback waits once, then wakes as soon as a provider is selected."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    emit_task = asyncio.create_task(runtime.emit())

    await asyncio.sleep(0.02)
    assert not emit_task.done()
    assert handler.emit_calls == 0

    assert await runtime.activate("keyboard")
    await asyncio.wait_for(handler.emit_called.wait(), timeout=0.1)
    assert handler.emit_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_ambient_emit_wakes_for_shutdown() -> None:
    """Closing an ambient runtime releases the playback wait promptly."""
    runtime = HybridRuntime(lambda: FakeHandler())
    emit_task = asyncio.create_task(runtime.emit())

    await asyncio.sleep(0.02)
    assert not emit_task.done()
    await runtime.close()

    assert await asyncio.wait_for(emit_task, timeout=0.1) is None


@pytest.mark.asyncio
async def test_runtime_startup_waits_locally_and_does_not_open_provider() -> None:
    """The LocalStream startup seam keeps ambient mode alive until shutdown."""
    factory_calls = 0

    def factory() -> FakeHandler:
        nonlocal factory_calls
        factory_calls += 1
        return FakeHandler()

    runtime = HybridRuntime(factory)
    startup = asyncio.create_task(runtime.start_up())
    await asyncio.sleep(0)
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert factory_calls == 0
    await runtime.close()
    await startup


def test_invalid_transition_is_rejected_and_logs_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    """The state machine rejects direct ambient-to-cloud transitions."""
    runtime = HybridRuntime(lambda: FakeHandler())
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError, match="Invalid runtime transition"):
        runtime._transition(RuntimeMode.CLOUD_CONVERSATION, "invalid")
    runtime._transition(RuntimeMode.LOCAL_COMMAND, "keyboard")
    runtime._transition(RuntimeMode.LOCAL_AMBIENT, "cancelled")
    assert "LOCAL_AMBIENT" in caplog.text
    assert "LOCAL_COMMAND" in caplog.text
    assert "CLOUD_CONVERSATION" not in caplog.text


class AcousticScoreModel:
    """Recognize one synthetic marker without accessing a model or microphone."""

    def __init__(self) -> None:
        """Initialize observed inference counts."""
        self.resets = 0
        self.frames = 0

    def reset(self) -> None:
        """Count each new listening stream."""
        self.resets += 1

    def predict(self, audio):
        """Score the test marker."""
        self.frames += 1
        return {"reachy": float(np.max(audio) == 222)}


@pytest.mark.asyncio
async def test_acoustic_wake_uses_existing_barrier_and_returns_to_listening() -> None:
    """Acoustic activation keeps captured wake audio local and rearms after timeout."""
    model = AcousticScoreModel()
    detector = OpenWakeWordDetector(model)
    handlers = []

    def factory():
        handler = FakeHandler()
        handlers.append(handler)
        return handler

    clock = FakeClock()
    acknowledgements = []
    runtime = HybridRuntime(
        factory,
        acoustic_wake_detector=detector,
        clock=clock,
        inactivity_timeout_s=5,
        acknowledgement_callback=acknowledgements.append,
    )
    runtime.set_capture_barrier_provider(lambda: 2)
    await runtime.handle_frame((16000, np.zeros(1280, dtype=np.int16)), capture_sequence=1)
    assert not handlers
    await runtime.handle_frame((16000, np.full(1280, 222, dtype=np.int16)), capture_sequence=2)
    await handlers[0].started.wait()
    handlers[0].set_connected(True)
    assert acknowledgements == ["acoustic_wake"]
    await handlers[0].say_called.wait()
    assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
    assert not handlers[0].receive_frames
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    await runtime.handle_frame(frame(333), capture_sequence=3)
    assert handlers[0].receive_frames[0][1].tolist() == [333]
    assert model.frames == 2
    clock.advance(5)
    assert await runtime.check_inactivity()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.handle_frame((16000, np.full(1280, 222, dtype=np.int16)), capture_sequence=4)
    assert len(handlers) == 2 and model.resets == 2
    assert runtime.status_snapshot().acoustic_wake_enabled
    assert runtime.status_snapshot().wake_detections == 2
    await runtime.close()
    await runtime.handle_frame((16000, np.full(1280, 222, dtype=np.int16)))
    assert len(handlers) == 2


@pytest.mark.asyncio
async def test_bare_acoustic_wake_acknowledges_once_after_provider_readiness() -> None:
    """A bare acoustic activation speaks only after its provider is ready."""
    handler = FakeHandler()
    acknowledgements: list[str] = []
    runtime = HybridRuntime(
        lambda: handler,
        acknowledgement_callback=acknowledgements.append,
    )
    runtime.set_capture_barrier_provider(lambda: 0)

    assert await runtime.activate("acoustic_wake")
    await handler.started.wait()
    assert handler.say_texts == []
    await runtime.handle_frame(frame(0), capture_sequence=1)

    handler.set_connected(True)
    handler.set_connected(True)
    await handler.say_called.wait()
    await wait_until(lambda: len(handler.receive_frames) == 1)

    assert acknowledgements == ["acoustic_wake"]
    assert handler.say_texts == ["Acknowledge the wake word briefly, then listen."]
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_activation_with_addressed_text_does_not_add_ack() -> None:
    """An addressed acoustic activation submits its text once without a second ack."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate("acoustic_wake", addressed_text="what do you think?")
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()

    assert handler.say_texts == ["what do you think?"]
    await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_ack_skips_when_postactivation_local_vad_hears_speech() -> None:
    """Speech during the local activation seam suppresses the bare acknowledgment."""
    acknowledgement_started = asyncio.Event()
    release_acknowledgement = asyncio.Event()
    handler = FakeHandler()

    async def acknowledge(_reason: str) -> None:
        acknowledgement_started.set()
        await release_acknowledgement.wait()

    runtime = HybridRuntime(
        lambda: handler,
        acknowledgement_callback=acknowledge,
    )
    runtime.set_capture_barrier_provider(lambda: 10)

    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await acknowledgement_started.wait()
        await runtime.handle_frame(frame(2_000), capture_sequence=11)
        release_acknowledgement.set()

        assert await activation
        await handler.started.wait()
        handler.set_connected(True)
        await wait_until(lambda: bool(handler.receive_frames))

        assert handler.say_texts == []
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_canceled_acoustic_activation_cannot_ack_after_standby() -> None:
    """A canceled acoustic activation cannot speak after returning to ambient mode."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate("acoustic_wake")
    await handler.started.wait()
    assert await runtime.deactivate_cloud("test_cancel")

    handler.set_connected(True)
    await asyncio.sleep(0)

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert handler.say_texts == []
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_deactivation_cancels_blocked_acoustic_ack() -> None:
    """Deactivation cancels an acoustic acknowledgment waiting in the provider."""
    handler = FakeHandler()
    ack_started = asyncio.Event()
    ack_cancelled = asyncio.Event()

    async def blocked_say(_text: str) -> None:
        ack_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            ack_cancelled.set()
            raise

    handler.say = blocked_say
    runtime = HybridRuntime(lambda: handler)
    runtime.set_capture_barrier_provider(lambda: 0)

    assert await runtime.activate("acoustic_wake")
    await handler.started.wait()
    handler.set_connected(True)
    await ack_started.wait()
    await asyncio.wait_for(runtime.handle_frame(frame(2_000), capture_sequence=1), timeout=0.1)
    assert handler.receive_frames == []

    assert await runtime.deactivate_cloud("test_cancel")
    assert ack_cancelled.is_set()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_failure_keeps_manual_activation_available(caplog) -> None:
    """Inference failure disables acoustic wake without disabling manual activation."""
    detector = OpenWakeWordDetector(AcousticScoreModel())
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, acoustic_wake_detector=detector)
    with caplog.at_level(logging.WARNING):
        await runtime.handle_frame((0, np.zeros(1280, dtype=np.int16)))
    assert not runtime.status_snapshot().acoustic_wake_enabled
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert "manual activation remains available" in caplog.text
    assert await runtime.activate()
    await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_result_after_manual_activation_cannot_start_another_session() -> None:
    """A result from an old ambient epoch cannot duplicate manual activation."""
    entered = threading.Event()
    release = threading.Event()

    class SlowModel(AcousticScoreModel):
        def predict(self, audio):
            entered.set()
            assert release.wait(2)
            return {"reachy": 1.0}

    factory = MagicMock(side_effect=lambda: FakeHandler())
    runtime = HybridRuntime(factory, acoustic_wake_detector=OpenWakeWordDetector(SlowModel()))
    pending = asyncio.create_task(runtime.handle_frame((16000, np.zeros(1280, dtype=np.int16))))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert await runtime.activate()
        assert await runtime.deactivate_cloud()
        release.set()
        await pending
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert factory.call_count == 1
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_close_discards_late_acoustic_detection() -> None:
    """Closing never waits indefinitely for inference or acts on its late result."""
    entered = threading.Event()
    release = threading.Event()

    class SlowModel(AcousticScoreModel):
        def predict(self, audio):
            entered.set()
            assert release.wait(2)
            return {"reachy": 1.0}

    factory = MagicMock()
    runtime = HybridRuntime(factory, acoustic_wake_detector=OpenWakeWordDetector(SlowModel()))
    pending = asyncio.create_task(runtime.handle_frame((16000, np.zeros(1280, dtype=np.int16))))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(runtime.close(), 1)
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        assert runtime.status_snapshot().closed
        factory.assert_not_called()
    finally:
        release.set()
        await runtime.close()
