"""Tests for the local/cloud hybrid runtime lifecycle."""

import wave
import asyncio
import logging
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from collections.abc import Callable, Awaitable

import httpx
import numpy as np
import pytest

from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.streaming import AdditionalOutputs, audio_to_mono_int16
from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector
from reachy_mini_conversation_app.wake_clips import WakeClipRecorder
from reachy_mini_conversation_app.local_actions import LocalActionExecutor
from reachy_mini_conversation_app.hybrid_runtime import (
    RuntimeMode,
    HybridRuntime,
    LocalCommandOutcome,
    _merge_local_items,
    frame_activation_context,
)
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


def framed(transcript: str) -> str:
    """Return ambient transcript as the runtime frames it for an activation."""
    return frame_activation_context(transcript, config.config.LOCAL_CONTEXT_MAX_CHARS)


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

    assert handler.get_initial_context() == framed(
        "We should do something indoors tomorrow. Maybe the aquarium. Reachy, what do you think?"
    )
    assert handler.say_texts == ["what do you think?"]
    assert handler.receive_frames == []
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    # The counter measures the raw ambient transcript, not the activation framing around it.
    assert runtime.status.local_context_chars == len(
        "We should do something indoors tomorrow. Maybe the aquarium. Reachy, what do you think?"
    )
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

    assert handler.get_initial_context() == framed("Earlier dinner context.")
    assert handler.say_texts == ["what do you think?"]
    await runtime.close()


@pytest.mark.asyncio
async def test_transcript_wake_mid_sentence_sends_the_whole_sentence_alongside_acoustic_wake() -> None:
    """With acoustic wake also configured, a mid-sentence wake in the transcript sends the full sentence."""
    clock = FakeClock()
    clock.value = 4.0
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT),
        clock=clock,
    )
    sentence = "I think Real Madrid might play this weekend, Richie, can you check?"
    assert processor.transcript.add(TranscriptSegment("We were talking about football.", 1, 2, 1))
    assert processor.transcript.add(TranscriptSegment(sentence, 3, 4, 2))
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        local_processor=processor,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_detector=TranscriptWakeWordDetector(aliases=("richie",), cooldown_seconds=0),
        acoustic_wake_detector=OpenWakeWordDetector(AcousticScoreModel()),
        clock=clock,
    )

    runtime.observe_local_transcript(TranscriptSegment(sentence, 3, 4, 2))
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()

    assert handler.say_texts == [sentence]
    assert handler.get_initial_context() == framed("We were talking about football.")
    await runtime.close()


def test_transcript_wake_aliases_match_whole_words_only() -> None:
    """Aliases are extra accepted spellings; substrings and unrelated words do not wake."""
    detector = TranscriptWakeWordDetector(aliases=("richie", "ritchie"), cooldown_seconds=0)

    detection = detector.process_transcript("Ritchie, what time is it?", is_final=True)
    assert detection is not None and detection.remaining_text == "what time is it?"
    detector.consume_detection()
    assert detector.process_transcript("that was rich", is_final=True) is None
    assert detector.process_transcript("Richieland is far", is_final=True) is None


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

    assert handler.get_initial_context() == framed("Earlier dinner context.")
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
    assert handler.get_initial_context() == framed("indoors tomorrow, maybe the aquarium")
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
async def test_provider_setup_failure_returns_to_ambient_and_shutdowns_once(caplog: pytest.LogCaptureFixture) -> None:
    """A long-lived provider setup failure does not kill local ambient mode and logs why it failed."""
    handler = FakeHandler(fail_start=True)
    runtime = HybridRuntime(lambda: handler)

    with caplog.at_level(logging.WARNING, logger="reachy_mini_conversation_app.hybrid_runtime"):
        assert await runtime.activate()
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT)
    assert runtime.active_handler is None
    assert handler.shutdown_calls == 1
    assert "Hybrid provider task failed: RuntimeError provider setup sentinel" in caplog.text
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


def _music_session(clock: FakeClock) -> tuple[HybridRuntime, "FakeHandler"]:
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, inactivity_timeout_s=60, clock=clock)
    return runtime, handler


@pytest.mark.asyncio
async def test_music_wind_down_closes_despite_music_heard_as_speech() -> None:
    """With music playing, the session closes 5 s after Reachy's reply even though the model thinks someone speaks."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime._on_handler_activity(handler, generation, "user_speech_started")  # music heard as speech

    runtime.request_music_wind_down()
    clock.advance(2)
    runtime.note_playback_queued(1.0)  # the reply plays from t=2 to t=3
    clock.advance(4)  # t=6: 3 s after the reply
    assert not await runtime.check_inactivity()
    clock.advance(2.5)  # t=8.5: 5.5 s after the reply
    assert await runtime.check_inactivity()
    assert runtime.mode in (RuntimeMode.STOPPING, RuntimeMode.LOCAL_AMBIENT)
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_waits_for_the_reply_to_a_new_user_turn() -> None:
    """A user turn restarts the wait: the countdown begins only after Reachy answers it."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime.note_playback_queued(1.0)

    clock.advance(3)
    runtime._on_handler_transcript(handler, generation, "user", "and what about tomorrow?", True)
    clock.advance(8)  # Reachy still thinking: no close
    assert not await runtime.check_inactivity()
    runtime.note_playback_queued(2.0)
    clock.advance(6)  # 4 s after the reply ended
    assert not await runtime.check_inactivity()
    clock.advance(1.5)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_gives_ten_seconds_after_a_question() -> None:
    """When Reachy's reply ends in a question, the user has 10 s to answer."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime.note_playback_queued(1.0)
    runtime._on_handler_transcript(handler, generation, "assistant", "Which song, the original or the remix?", True)

    clock.advance(8)  # 7 s after the question
    assert not await runtime.check_inactivity()
    clock.advance(3.5)  # 10.5 s after the question
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_closes_after_twenty_seconds_without_a_reply() -> None:
    """If Reachy never answers while music plays, the conversation still closes."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.request_music_wind_down()

    clock.advance(19)
    assert not await runtime.check_inactivity()
    clock.advance(1.5)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_keeps_a_follow_up_open_from_its_partial_transcript() -> None:
    """A follow-up still being spoken (only partial transcripts so far) is not cut off by the countdown."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime.note_playback_queued(1.0)  # Reachy answers the wake request, t=0..1

    clock.advance(4)  # t=4: the user starts a follow-up
    runtime._on_handler_activity(handler, generation, "user_speech_started")
    runtime._on_handler_transcript(handler, generation, "user", "and what about", False)
    clock.advance(3)  # t=7: 6 s after the reply, still talking
    runtime._on_handler_transcript(handler, generation, "user", "tomorrow in Rome", False)
    assert not await runtime.check_inactivity()
    clock.advance(5)  # t=12: Reachy thinking about the follow-up
    assert not await runtime.check_inactivity()
    runtime.note_playback_queued(1.0)  # answer plays t=12..13
    clock.advance(5.5)  # t=17.5: 4.5 s after the answer
    assert not await runtime.check_inactivity()
    clock.advance(1)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_holds_briefly_when_speech_starts_after_the_reply() -> None:
    """Speech starting near the end of the window holds the close until its transcript can arrive."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime.note_playback_queued(1.0)  # reply ends at t=1

    clock.advance(5.5)  # t=5.5: speech starts just before the 5 s window ends
    runtime._on_handler_activity(handler, generation, "user_speech_started")
    clock.advance(1)  # t=6.5: past the window, but held
    assert not await runtime.check_inactivity()
    clock.advance(2.1)  # t=8.6: no transcript followed (likely music), so close
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_started_mid_conversation_waits_for_reachys_short_reply() -> None:
    """When Reachy starts music, its "Enjoy!" comes first and the countdown follows it."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.note_playback_queued(1.0)  # an earlier reply, t=0..1

    clock.advance(10)  # t=10: the play tool succeeds
    runtime.request_music_wind_down()
    clock.advance(0.5)
    assert not await runtime.check_inactivity()
    runtime.note_playback_queued(1.0)  # "Enjoy!" plays t=10.5..11.5
    clock.advance(5)  # t=15.5: 4 s after "Enjoy!"
    assert not await runtime.check_inactivity()
    clock.advance(1.5)  # 5.5 s after "Enjoy!"
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_waits_for_the_answer_after_let_me_check() -> None:
    """A "let me check" while Gemini is still busy (a tool running) does not start the countdown."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime._on_handler_interaction_busy(handler, generation, True)
    runtime.note_playback_queued(1.0)  # "let me check", t=0..1

    clock.advance(8)  # t=8: the weather tool is still running
    assert not await runtime.check_inactivity()
    runtime.note_playback_queued(2.0)  # the answer, t=8..10
    clock.advance(2)
    runtime._on_handler_interaction_busy(handler, generation, False)
    clock.advance(4)  # t=14: 4 s after the answer
    assert not await runtime.check_inactivity()
    clock.advance(1.5)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_busy_flag_stuck_on_music_only_holds_up_to_the_cap() -> None:
    """If the busy flag never clears (music heard as speech), the close comes 20 s after the reply."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime._on_handler_interaction_busy(handler, generation, True)
    runtime.note_playback_queued(1.0)  # reply ends at t=1

    clock.advance(20)  # t=20: 19 s after the reply
    assert not await runtime.check_inactivity()
    clock.advance(1.5)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_new_song_over_music_already_playing_waits_for_reachys_short_reply() -> None:
    """A song Reachy starts while music was already playing at the wake still gets its "Enjoy!"."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.request_music_wind_down()  # music playing at the wake
    runtime.note_playback_queued(1.0)  # "Sure, putting it on", t=0..1

    clock.advance(4)  # t=4: the new song starts
    runtime.request_music_wind_down()
    clock.advance(3)  # t=7: past 5 s after the first reply, but waiting for "Enjoy!"
    assert not await runtime.check_inactivity()
    runtime.note_playback_queued(1.0)  # "Enjoy!", t=7..8
    clock.advance(5.5)  # t=12.5: 4.5 s after "Enjoy!"
    assert not await runtime.check_inactivity()
    clock.advance(1)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_pausing_the_music_returns_the_conversation_to_the_normal_timeout() -> None:
    """After "pause the music" the user can keep chatting: the 60 s rule applies again."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.request_music_wind_down()
    clock.advance(2)
    runtime.cancel_music_wind_down()
    runtime.note_playback_queued(1.0)  # "Paused." t=2..3

    clock.advance(30)
    assert not await runtime.check_inactivity()
    clock.advance(31)
    assert await runtime.check_inactivity()
    assert runtime.mode in (RuntimeMode.STOPPING, RuntimeMode.LOCAL_AMBIENT)
    await runtime.close()


@pytest.mark.asyncio
async def test_music_wind_down_closes_when_lyrics_keep_arriving_as_user_text() -> None:
    """Endless partial transcripts (lyrics heard as speech) cannot hold the session past the turn cap."""
    clock = FakeClock()
    runtime, handler = _music_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.request_music_wind_down()
    runtime.note_playback_queued(1.0)

    clock.advance(2)
    closed = False
    for _ in range(40):
        runtime._on_handler_transcript(handler, generation, "user", "la la la", False)
        clock.advance(2)
        if await runtime.check_inactivity():
            closed = True
            break
    assert closed
    assert 60 <= clock() <= 64
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
            self.frames.extend(int(sample) for sample in observed_frame[1])
            self.started.set()
            await self.release.wait()

    processor = BlockingLocalProcessor()
    factory = MagicMock(side_effect=AssertionError("cloud must remain closed"))
    runtime = HybridRuntime(factory, local_processor=processor)
    runtime._LOCAL_PROCESSING_QUEUE_SIZE = 8  # small bound keeps the overflow case short

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
async def test_local_processing_backlog_is_merged_into_one_pass() -> None:
    """Frames queued behind a slow pass are observed together, in order, and each caller completes."""

    class SlowLocalProcessor:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.passes: list[list[int]] = []

        async def observe_frame(self, observed_frame: AudioFrame) -> None:
            self.passes.append([int(sample) for sample in observed_frame[1]])
            self.started.set()
            await self.release.wait()

    processor = SlowLocalProcessor()
    runtime = HybridRuntime(
        MagicMock(side_effect=AssertionError("cloud must remain closed")), local_processor=processor
    )
    try:
        await runtime.handle_frame(frame(0), wait_for_local_processing=False)
        await processor.started.wait()
        waiters = [asyncio.create_task(runtime.handle_frame(frame(value))) for value in range(1, 6)]
        await asyncio.sleep(0)
        processor.release.set()
        await asyncio.wait_for(asyncio.gather(*waiters), timeout=1.0)

        assert processor.passes == [[0], [1, 2, 3, 4, 5]]
        assert runtime.status.local_processing_frames_dropped == 0
    finally:
        processor.release.set()
        await runtime.close()


def test_merge_local_items_keeps_lone_frames_and_splits_on_rate_or_epoch() -> None:
    """Only consecutive frames sharing a sample rate and wake epoch are joined, as mono int16."""
    loop = asyncio.new_event_loop()
    try:
        lone = frame(7)
        stereo = (16_000, np.array([[1, 9], [2, 9]], dtype=np.int16))
        items = [
            (lone, loop.create_future(), 1),
            (frame(8, sample_rate=24_000), loop.create_future(), 1),
            (stereo, loop.create_future(), 2),
            (frame(3), loop.create_future(), 2),
        ]
        merged = _merge_local_items(items)
    finally:
        loop.close()

    assert merged[0][0] is lone
    assert [(f[0], f[1].tolist(), epoch, len(done)) for f, epoch, done in merged[1:]] == [
        (24_000, [8], 1, 1),
        (16_000, [*audio_to_mono_int16(stereo[1]).tolist(), 3], 2, 2),
    ]


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


def wave_frames(path) -> bytes:
    """Return a WAV file's PCM bytes."""
    with wave.open(str(path), "rb") as clip:
        return clip.readframes(clip.getnframes())


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
async def test_wake_clips_hold_only_audio_heard_while_listening_locally(tmp_path) -> None:
    """The wake is saved from local audio; what is said in the conversation never reaches a clip."""
    clips = WakeClipRecorder(tmp_path, keep_days=14)
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        acoustic_wake_detector=OpenWakeWordDetector(AcousticScoreModel(), event_observer=clips.save_wake_event),
        wake_clips=clips,
    )
    runtime.set_capture_barrier_provider(lambda: 3)
    await runtime.handle_frame((16000, np.full(1280, 111, dtype=np.int16)), capture_sequence=1)
    await runtime.handle_frame((16000, np.full(1280, 222, dtype=np.int16)), capture_sequence=2)
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()
    await runtime.handle_frame((16000, np.full(1280, 333, dtype=np.int16)), capture_sequence=3)
    clips.save_name_heard(1.0)

    saved = sorted(tmp_path.glob("*/*.wav"))
    assert sorted(path.name.split("-", 1)[1] for path in saved) == ["name_heard.wav", "wake-1.00.wav"]
    for path in saved:
        samples = set(np.frombuffer(wave_frames(path), dtype="<i2").tolist())
        assert samples == {111, 222}
    await runtime.close()


@pytest.mark.asyncio
async def test_a_name_heard_only_by_local_stt_is_saved_as_a_clip(tmp_path) -> None:
    """When the transcript wakes Reachy while the wake model stayed quiet, that utterance is kept for retraining."""
    clips = WakeClipRecorder(tmp_path, keep_days=14)
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        acoustic_wake_detector=OpenWakeWordDetector(AcousticScoreModel()),
        wake_clips=clips,
    )
    runtime.set_capture_barrier_provider(lambda: 1)
    await runtime.handle_frame((16000, np.full(1280, 111, dtype=np.int16)), capture_sequence=1)

    runtime.observe_local_transcript(TranscriptSegment("Reachy, what time is it?", 3, 4, 2))
    await handler.started.wait()

    [clip] = tmp_path.glob("*/*.wav")
    assert clip.name.endswith("-name_heard.wav")
    await runtime.close()


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


def test_activation_context_framing_marks_background_and_keeps_newest_lines() -> None:
    """Ambient text is labelled as background for the addressed request and trimmed from the oldest end."""
    from reachy_mini_conversation_app.hybrid_runtime import (
        ACTIVATION_CUE,
        ACTIVATION_CONTEXT_FOOTER,
        ACTIVATION_CONTEXT_HEADER,
    )

    framed_text = frame_activation_context("I think Real Madrid might play this weekend", 2_000)

    assert framed_text.startswith(ACTIVATION_CONTEXT_HEADER)
    assert "I think Real Madrid might play this weekend" in framed_text
    assert framed_text.endswith(ACTIVATION_CONTEXT_FOOTER)
    assert frame_activation_context("   ", 2_000) == ACTIVATION_CUE

    limit = len(ACTIVATION_CONTEXT_HEADER) + len(ACTIVATION_CONTEXT_FOOTER) + 4 + 10
    trimmed = frame_activation_context("oldest line\nnewest one", limit)
    assert len(trimmed) <= limit
    assert "newest one" in trimmed
    assert "oldest" not in trimmed


def _processor_with_open_wake_segment(wake_segment_text: str) -> AmbientContextProcessor:
    """Return a processor holding earlier context and an open segment that closes with the given text."""
    processor = AmbientContextProcessor(
        vad=SilentVAD(),
        stt=RecordingSTT("unused"),
        config=AmbientContextConfig(strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT),
        clock=lambda: 3.0,
    )
    processor.transcript.add(TranscriptSegment("We were talking about football.", 0.5, 1, 1))
    processor._next_sequence = 3  # segment 2 is open while the wake fires
    processor._speech = object()  # type: ignore[assignment]

    def close_wake_segment() -> None:
        processor._speech = None
        processor.transcript.add(TranscriptSegment(wake_segment_text, 1, 2, 2))

    processor.close_open_segment = close_wake_segment  # type: ignore[method-assign]
    return processor


async def _activate_and_collect(
    runtime: HybridRuntime, handler: "FakeHandler", followup: TranscriptSegment | None = None
) -> None:
    activation = asyncio.create_task(runtime.activate("acoustic_wake"))
    if followup is not None:
        await wait_until(lambda: runtime._wake_followup_future is not None)
        await asyncio.sleep(0.05)
        runtime.observe_local_transcript(followup)
    assert await asyncio.wait_for(activation, timeout=3.0)
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()


@pytest.mark.asyncio
async def test_acoustic_activation_does_not_stall_local_processing() -> None:
    """While an acoustic activation is still setting up, later frames keep reaching the local processor."""
    release_factory = asyncio.Event()

    class CountingProcessor:
        """Count observed frames."""

        def __init__(self) -> None:
            self.frames = 0

        async def observe_frame(self, _frame: AudioFrame) -> None:
            """Record one observed frame."""
            self.frames += 1

    processor = CountingProcessor()

    async def slow_factory() -> FakeHandler:
        await release_factory.wait()
        return FakeHandler()

    runtime = HybridRuntime(
        slow_factory, local_processor=processor, acoustic_wake_detector=OpenWakeWordDetector(AcousticScoreModel())
    )
    try:
        await runtime.handle_frame((16_000, np.full(1280, 222, dtype=np.int16)), wait_for_local_processing=False)
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        frames_at_wake = processor.frames
        for _ in range(5):
            await runtime.handle_frame((16_000, np.zeros(160, dtype=np.int16)), wait_for_local_processing=False)
        await wait_until(lambda: processor.frames >= frames_at_wake + 1)
        assert runtime.mode is RuntimeMode.LOCAL_COMMAND
    finally:
        release_factory.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_sends_the_utterance_with_the_request_at_once() -> None:
    """The utterance containing the wake and its request is sent as text; earlier talk stays context."""
    processor = _processor_with_open_wake_segment(
        "I think Real Madrid might play this weekend. Reachy, can you check?"
    )
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_followup_timeout_s=15.0,
    )
    try:
        await _activate_and_collect(runtime, handler)
        assert handler.say_texts == ["I think Real Madrid might play this weekend. Reachy, can you check?"]
        context = handler.get_initial_context()
        assert "We were talking about football." in context
        assert "Real Madrid" not in context
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_appends_the_followup_when_nothing_followed_the_wake() -> None:
    """A wake at the end of the utterance waits for the next local segment and sends both."""
    processor = _processor_with_open_wake_segment("I think Real Madrid might play this weekend. Reachy")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_followup_timeout_s=2.0,
    )
    try:
        await _activate_and_collect(runtime, handler, TranscriptSegment("can you check?", 2, 3, 3))
        assert handler.say_texts == ["I think Real Madrid might play this weekend. Reachy can you check?"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_treats_a_short_segment_as_the_wake_word_and_waits_for_the_request() -> None:
    """A one- or two-word segment at the wake is not sent alone; the next segment carries the request."""
    processor = _processor_with_open_wake_segment("Re")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_followup_timeout_s=2.0,
    )
    try:
        await _activate_and_collect(runtime, handler, TranscriptSegment("can you check?", 2, 3, 3))
        assert handler.say_texts == ["Re can you check?"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_with_only_a_fragment_and_no_request_falls_back_to_audio() -> None:
    """Without a follow-up, a wake-word fragment is never sent as the request."""
    processor = _processor_with_open_wake_segment("Re")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_followup_timeout_s=0.05,
    )
    try:
        assert await runtime.activate("acoustic_wake")
        await handler.started.wait()
        handler.set_connected(True)
        await handler.say_called.wait()
        assert handler.say_texts == [HybridRuntime._ACOUSTIC_WAKE_ACK_TEXT]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_sends_the_utterance_even_if_stt_misspelled_the_wake() -> None:
    """An unrecognized wake spelling still sends the utterance as the request, without waiting."""
    processor = _processor_with_open_wake_segment("I think Real Madrid might play this weekend. Ritzy, can you check?")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_followup_timeout_s=15.0,
    )
    try:
        await _activate_and_collect(runtime, handler)
        assert handler.say_texts == ["I think Real Madrid might play this weekend. Ritzy, can you check?"]
    finally:
        await runtime.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I think Real Madrid might play this weekend. Reachy, can you check?", "can you check?"),
        ("talking about Reachy", ""),
        ("we were saying Ricci, can you check?", "can you check?"),
        ("Richy what time is it", "what time is it"),
        ("no wake here", ""),
    ],
)
def test_request_after_wake_returns_the_suffix(text: str, expected: str) -> None:
    """Only text after the last wake word counts as the request."""
    from reachy_mini_conversation_app.hybrid_runtime import request_after_wake

    assert request_after_wake(text) == expected


@pytest.mark.asyncio
async def test_acoustic_wake_without_context_still_marks_the_request_as_addressed() -> None:
    """With no ambient transcript, the provider still learns the following request is addressed to it."""
    from reachy_mini_conversation_app.hybrid_runtime import ACTIVATION_CUE

    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    try:
        assert await runtime.activate("acoustic_wake")
        await handler.started.wait()
        assert handler.get_initial_context() == ACTIVATION_CUE
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cloud_session_observer_is_told_start_and_end_once() -> None:
    """The observer hears True when a cloud conversation opens and False after it has ended."""
    events: list[bool] = []

    async def observe(active: bool) -> None:
        events.append(active)

    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, cloud_session_observer=observe)
    try:
        assert await runtime.activate("keyboard")
        await handler.started.wait()
        await wait_until(lambda: events == [True])
        assert await runtime.deactivate_cloud("test_end")
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT)
        await wait_until(lambda: events == [True, False])
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cloud_session_summary_logs_ack_and_forwarding(caplog: pytest.LogCaptureFixture) -> None:
    """Ending a cloud session logs whether the wake ack was sent and how many frames were queued."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    runtime.set_capture_barrier_provider(lambda: 0)

    assert await runtime.activate("acoustic_wake")
    await handler.started.wait()
    await runtime.handle_frame(frame(0), capture_sequence=1)
    handler.set_connected(True)
    await handler.say_called.wait()
    await wait_until(lambda: len(handler.receive_frames) == 1)

    with caplog.at_level(logging.INFO, logger="reachy_mini_conversation_app.hybrid_runtime"):
        await runtime.deactivate_cloud("test_end")

    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Cloud session summary")]
    assert len(summary) == 1
    assert "reason=test_end" in summary[0]
    assert "queued_frames=1" in summary[0]
    assert "ack=sent" in summary[0]
    assert "ack_task_running=False" in summary[0]
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
async def test_dropped_local_frame_does_not_reset_the_acoustic_detector(caplog: pytest.LogCaptureFixture) -> None:
    """A local-queue overflow mid-stream keeps the wake detector's state and logs once per drop burst."""
    gate = asyncio.Event()
    gate.set()

    class GatedLocalProcessor:
        """Let the test stall the local worker so its queue overflows."""

        def __init__(self) -> None:
            self.frames = 0

        async def observe_frame(self, _frame: AudioFrame) -> None:
            """Wait at the gate, then count the frame."""
            await gate.wait()
            self.frames += 1

    processor = GatedLocalProcessor()
    model = AcousticScoreModel()
    runtime = HybridRuntime(
        MagicMock(side_effect=AssertionError("cloud must remain closed")),
        local_processor=processor,
        acoustic_wake_detector=OpenWakeWordDetector(model),
    )
    runtime._LOCAL_PROCESSING_QUEUE_SIZE = 2
    silence = (16000, np.zeros(160, dtype=np.int16))
    try:
        for _ in range(3):
            await runtime.handle_frame(silence, wait_for_local_processing=False)
            await asyncio.sleep(0.02)
        await wait_until(lambda: processor.frames == 3)
        resets_while_listening = model.resets

        gate.clear()
        with caplog.at_level(logging.WARNING, logger="reachy_mini_conversation_app.hybrid_runtime"):
            for _ in range(8):
                await runtime.handle_frame(silence, wait_for_local_processing=False)
        assert runtime.status.local_processing_frames_dropped >= 2
        assert sum("Local processing queue full" in r.getMessage() for r in caplog.records) == 1

        gate.set()
        await asyncio.sleep(0.1)
        frames_after_drain = processor.frames
        for _ in range(3):
            await runtime.handle_frame(silence, wait_for_local_processing=False)
            await asyncio.sleep(0.02)
        await wait_until(lambda: processor.frames == frames_after_drain + 3)

        assert model.resets == resets_while_listening
    finally:
        gate.set()
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


def _follow_up_session(clock: FakeClock, addressee_check: object = None) -> tuple[HybridRuntime, "FakeHandler"]:
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        inactivity_timeout_s=60,
        follow_up_window_s=15,
        addressee_check=addressee_check,  # type: ignore[arg-type]
        clock=clock,
    )
    return runtime, handler


@pytest.mark.asyncio
async def test_follow_up_window_closes_15_s_after_reachys_reply() -> None:
    """Without a follow-up, the conversation closes 15 s after the reply instead of after 60 s of silence."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.note_playback_queued(1.0)  # reply ends at t=1

    clock.advance(15.5)  # t=15.5
    assert not await runtime.check_inactivity()
    clock.advance(1)  # t=16.5: 15.5 s after the reply
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_side_conversation_closes_the_follow_up_window_at_a_pause() -> None:
    """People talking on without Reachy answering close it after the window, once they pause."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.note_playback_queued(1.0)  # reply ends at t=1

    for _ in range(16):  # t=3..18: people keep talking, one transcript piece a second
        clock.advance(1)
        runtime._on_handler_transcript(handler, generation, "user", "and then she said", False)
    assert not await runtime.check_inactivity()  # mid-sentence
    clock.advance(2.5)  # a pause
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_follow_up_window_waits_for_a_long_sentence_whose_transcript_has_not_arrived() -> None:
    """Someone mid-sentence is never cut off: Gemini hears speech, though no transcript has come yet."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.note_playback_queued(1.0)  # reply ends at t=1

    clock.advance(10)  # t=10
    runtime._on_handler_activity(handler, generation, "user_speech_started")
    clock.advance(8)  # t=18: 17 s after the reply, still talking, nothing transcribed yet
    assert not await runtime.check_inactivity()
    clock.advance(2)  # t=20
    runtime._on_handler_activity(handler, generation, "user_speech_stopped")
    runtime._on_handler_transcript(handler, generation, "user", "and can you also show me the bridge", True)
    clock.advance(5)  # t=25: her turn waits for Reachy's answer like any other
    assert not await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_speech_holds_the_follow_up_close_at_most_twice_the_window() -> None:
    """A speech flag that never clears (a TV, a stuck provider) still closes 30 s after the reply."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.note_playback_queued(1.0)
    clock.advance(5)
    runtime._on_handler_activity(handler, generation, "user_speech_started")

    clock.advance(25)  # t=30: 29 s after the reply
    assert not await runtime.check_inactivity()
    clock.advance(1.5)  # t=31.5
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_an_unanswered_turn_does_not_close_while_someone_is_still_speaking() -> None:
    """After the window, the close waits for a pause in speech, not only a pause in transcripts."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.note_playback_queued(1.0)
    clock.advance(2)  # t=2
    runtime._on_handler_transcript(handler, generation, "user", "so what I was thinking is", False)
    runtime._on_handler_activity(handler, generation, "user_speech_started")

    clock.advance(17)  # t=19: unanswered for 17 s, transcripts quiet, still speaking
    assert not await runtime.check_inactivity()
    runtime._on_handler_activity(handler, generation, "user_speech_stopped")
    clock.advance(2.5)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_follow_up_window_waits_while_reachy_is_working_and_restarts_after_his_answer() -> None:
    """A busy provider (thinking, a tool) holds the close, and each answer starts a new window."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime.note_playback_queued(1.0)
    clock.advance(2)
    runtime._on_handler_transcript(handler, generation, "user", "what's the weather tomorrow?", True)
    runtime._on_handler_interaction_busy(handler, generation, True)

    clock.advance(16)  # t=18: still searching
    assert not await runtime.check_inactivity()
    runtime._on_handler_interaction_busy(handler, generation, False)
    runtime.note_playback_queued(2.0)  # the answer, t=18..20
    clock.advance(14)  # t=32: 12 s after the answer
    assert not await runtime.check_inactivity()
    clock.advance(4)
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_a_requested_close_waits_for_the_goodbye_to_play() -> None:
    """end_conversation and go_to_sleep close only after Reachy's reply has finished playing."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)

    assert runtime.request_close("go_to_sleep")
    assert not await runtime.check_inactivity()  # within the grace period
    runtime.note_playback_queued(2.0)  # "Good night!" t=0..2
    clock.advance(1.5)
    assert not await runtime.check_inactivity()
    clock.advance(1)  # t=2.5: played
    assert await runtime.check_inactivity()
    assert not runtime.request_close("go_to_sleep")  # nothing left to close
    await runtime.close()


@pytest.mark.asyncio
async def test_after_his_goodbye_gemini_hears_silence() -> None:
    """Once Reachy ends the conversation, the room saying "bye!" back cannot start another goodbye."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    await runtime.handle_frame(frame(7))
    await wait_until(lambda: len(handler.receive_frames) == 1)

    assert runtime.request_close("assistant_ended")
    await runtime.handle_frame(frame(9))
    await wait_until(lambda: len(handler.receive_frames) == 2)

    assert handler.receive_frames[0][1].tolist() == [7]
    assert handler.receive_frames[1][1].tolist() == [0]
    await runtime.close()


@pytest.mark.asyncio
async def test_addressee_check_closes_early_when_people_talk_among_themselves() -> None:
    """Unanswered speech judged not for Reachy closes the conversation before the window ends."""
    clock = FakeClock()
    calls: list[tuple[str, str]] = []

    async def judge(reachy_said: str, people_said: str) -> bool | None:
        calls.append((reachy_said, people_said))
        return False

    runtime, handler = _follow_up_session(clock, judge)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime._on_handler_transcript(handler, generation, "assistant", "It will be sunny tomorrow.", False)
    runtime.note_playback_queued(1.0)
    clock.advance(2)
    runtime._on_handler_transcript(handler, generation, "user", "ok so what should we", False)
    runtime._on_handler_transcript(handler, generation, "user", "have for dinner tonight", False)
    clock.advance(2)  # a pause

    assert not await runtime.check_inactivity()  # starts the check
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == [("It will be sunny tomorrow.", "ok so what should we have for dinner tonight")]
    assert await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.asyncio
async def test_addressee_verdicts_that_keep_him_or_come_too_late_do_not_close() -> None:
    """A "for Reachy" verdict leaves the window alone, and a stale "not for him" is dropped once he answers."""
    clock = FakeClock()
    verdicts = [True, False]
    release = asyncio.Event()

    async def judge(_reachy_said: str, _people_said: str) -> bool | None:
        verdict = verdicts.pop(0)
        if verdict is False:
            await release.wait()
        return verdict

    runtime, handler = _follow_up_session(clock, judge)
    assert await runtime.activate()
    handler.set_connected(True)
    generation = runtime.generation
    runtime._on_handler_transcript(handler, generation, "assistant", "Want another song?", False)
    runtime.note_playback_queued(1.0)
    clock.advance(2)
    runtime._on_handler_transcript(handler, generation, "user", "yes please play something", False)
    clock.advance(2)
    assert not await runtime.check_inactivity()  # judged: for Reachy
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not await runtime.check_inactivity()

    runtime._on_handler_transcript(handler, generation, "user", "maybe some jazz or blues", False)
    clock.advance(2)
    assert not await runtime.check_inactivity()  # second check starts and waits
    runtime._on_handler_transcript(handler, generation, "assistant", "Jazz it is!", False)
    runtime.note_playback_queued(1.0)  # he answered meanwhile
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not await runtime.check_inactivity()
    await runtime.close()


@pytest.mark.parametrize(
    ("text", "leads"),
    [
        ("Reachy, wake up", True),
        ("hey Reachy what's up", True),
        ("ok vale. Reachy, despierta", True),
        ("¿Reachy, estás?", True),
        ("I told Reachy that he was asleep", False),
        ("this weekend, Reachy, can you check?", False),
        ("", False),
    ],
)
def test_name_leads_only_when_the_name_opens_a_sentence(text: str, leads: bool) -> None:
    """While asleep, a name that opens the utterance or a sentence wakes him; one mid-sentence does not."""
    from reachy_mini_conversation_app.hybrid_runtime import _name_leads

    assert _name_leads(text) is leads


@pytest.mark.asyncio
async def test_asleep_an_acoustic_wake_mid_sentence_stays_asleep_without_moving() -> None:
    """After "go to sleep", an acoustic hit on talk about Reachy neither acknowledges nor opens the cloud."""
    processor = _processor_with_open_wake_segment("I told Reachy that")
    handler = FakeHandler()
    acknowledged: list[str] = []
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        acknowledgement_callback=acknowledged.append,
        wake_followup_timeout_s=0.1,
    )
    runtime._asleep = True
    try:
        assert not await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert acknowledged == [] and not handler.started.is_set()
        assert runtime._asleep
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_started_asleep_an_acoustic_wake_without_the_name_stays_asleep() -> None:
    """Restarted in the sleep pose: an acoustic hit on ordinary talk does not open a conversation."""
    processor = _processor_with_open_wake_segment("people are over there")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_followup_timeout_s=0.1,
        start_asleep=True,
    )
    try:
        assert not await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert not handler.started.is_set()
        assert runtime._asleep
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_asleep_the_name_first_wakes_him_and_he_stays_awake() -> None:
    """A wake that starts with his name acknowledges (raising the head) and ends sleep mode."""
    processor = _processor_with_open_wake_segment("Reachy")
    handler = FakeHandler()
    acknowledged: list[str] = []
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        acknowledgement_callback=acknowledged.append,
        wake_followup_timeout_s=2.0,
    )
    runtime._asleep = True
    try:
        await _activate_and_collect(runtime, handler, TranscriptSegment("are you awake?", 2, 3, 3))
        assert acknowledged == ["acoustic_wake"]
        assert not runtime._asleep
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_go_to_sleep_close_puts_the_runtime_to_sleep() -> None:
    """Only the go_to_sleep close turns on the stricter wake; ending a chat does not."""
    clock = FakeClock()
    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.request_close("assistant_ended")
    assert not runtime._asleep
    await runtime.close()

    runtime, handler = _follow_up_session(clock)
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.request_close("go_to_sleep")
    assert runtime._asleep
    await runtime.close()


@pytest.mark.asyncio
async def test_asleep_a_transcript_mention_mid_sentence_does_not_wake_him() -> None:
    """Talking about Reachy while he sleeps leaves him asleep; starting with his name wakes him."""
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        wake_detector=TranscriptWakeWordDetector("Reachy", cooldown_seconds=0),
        wake_followup_timeout_s=0.1,
    )
    runtime._asleep = True
    try:
        runtime.observe_local_transcript(TranscriptSegment("look, I think Reachy is sleeping", 1, 2, 1))
        await asyncio.sleep(0.05)
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT

        runtime.observe_local_transcript(TranscriptSegment("Reachy, wake up", 3, 4, 2))
        await wait_until(lambda: runtime.mode is not RuntimeMode.LOCAL_AMBIENT)
        assert not runtime._asleep
    finally:
        await runtime.close()


class _QuickReply:
    def __init__(self, text: str | None, *, sleep: bool = False, after: Callable[[], Any] | None = None) -> None:
        self.text = text
        self.language = "en"
        self.kind = "lights"
        self.sleep = sleep
        self.after = after


def _quick_runtime(
    reply: _QuickReply | None, handler: FakeHandler, spoken: list[str], seen: list[str]
) -> HybridRuntime:
    async def quick(text: str) -> _QuickReply | None:
        seen.append(text)
        return reply

    async def speak(text: str, _language: str):  # type: ignore[no-untyped-def]
        spoken.append(text)
        yield 24_000, np.zeros(24, dtype=np.int16)

    return HybridRuntime(lambda: handler, quick_commands=quick, reply_speaker=speak)


@pytest.mark.asyncio
async def test_quick_command_answers_locally_without_opening_the_cloud() -> None:
    """A recognized command runs, its reply is queued for playback, and Gemini never starts."""
    handler = FakeHandler()
    spoken: list[str] = []
    seen: list[str] = []
    runtime = _quick_runtime(_QuickReply("Okay."), handler, spoken, seen)

    assert await runtime.activate("acoustic_wake", addressed_text="Reachy, turn off the lights")

    assert seen == ["turn off the lights"]  # the request after the wake word
    assert spoken == ["Okay."]
    assert not handler.started.is_set()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.status.local_commands_executed == 1
    outputs = [await runtime.emit() for _ in range(3)]
    assert isinstance(outputs[0], AdditionalOutputs) and outputs[0].args[0]["role"] == "user"
    assert isinstance(outputs[1], AdditionalOutputs) and outputs[1].args[0]["content"] == "Okay."
    assert isinstance(outputs[2], tuple) and outputs[2][0] == 24_000
    await runtime.close()


@pytest.mark.asyncio
async def test_unrecognized_request_still_opens_the_conversation() -> None:
    """When quick commands decline, the request goes to Gemini as before."""
    handler = FakeHandler()
    runtime = _quick_runtime(None, handler, [], [])

    assert await runtime.activate("acoustic_wake", addressed_text="tell me a story")
    await handler.started.wait()
    handler.set_connected(True)
    await handler.say_called.wait()
    assert handler.say_texts == ["tell me a story"]
    await runtime.close()


@pytest.mark.asyncio
async def test_quick_reply_follow_up_runs_after_speech_and_sleep_is_remembered() -> None:
    """The follow-up (start music, lower the head) runs after the reply; "go to sleep" leaves Reachy asleep."""
    handler = FakeHandler()
    spoken: list[str] = []
    events: list[str] = []

    async def after() -> str | None:
        events.append(f"after:{len(spoken)}")
        return "Sorry, that didn't work."

    runtime = _quick_runtime(_QuickReply("Good night!", sleep=True, after=after), handler, spoken, [])
    assert await runtime.activate("acoustic_wake", addressed_text="go to sleep")

    assert events == ["after:1"]
    assert spoken == ["Good night!", "Sorry, that didn't work."]
    assert runtime._asleep is True
    assert not handler.started.is_set()
    await runtime.close()


@pytest.mark.asyncio
async def test_a_failing_quick_command_falls_back_to_the_conversation() -> None:
    """An exception in the quick path never loses the request."""
    handler = FakeHandler()

    async def quick(_text: str) -> None:
        raise RuntimeError("bug")

    runtime = HybridRuntime(lambda: handler, quick_commands=quick)
    assert await runtime.activate("acoustic_wake", addressed_text="turn off the lights")
    await handler.started.wait()
    await runtime.close()


@pytest.mark.asyncio
async def test_a_slow_quick_command_is_not_repeated_by_the_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command that times out may have half run, so the cloud does not get it again."""
    handler = FakeHandler()

    async def quick(_text: str) -> None:
        await asyncio.sleep(10)

    runtime = HybridRuntime(lambda: handler, quick_commands=quick)
    monkeypatch.setattr(runtime, "_QUICK_COMMAND_TIMEOUT_S", 0.01)
    assert await runtime.activate("acoustic_wake", addressed_text="next song")
    assert not handler.started.is_set()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_a_stalled_reply_stream_does_not_block_the_wake_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speech that stops arriving is abandoned and Reachy listens again."""
    handler = FakeHandler()

    async def quick(_text: str) -> _QuickReply:
        return _QuickReply("Okay.")

    async def stalled(_text: str, _language: str):  # type: ignore[no-untyped-def]
        yield 24_000, np.zeros(24, dtype=np.int16)
        await asyncio.sleep(10)

    runtime = HybridRuntime(lambda: handler, quick_commands=quick, reply_speaker=stalled)
    monkeypatch.setattr(runtime, "_QUICK_REPLY_BASE_S", 0.05)
    monkeypatch.setattr(runtime, "_QUICK_REPLY_PER_CHAR_S", 0.0)
    assert await asyncio.wait_for(runtime.activate("acoustic_wake", addressed_text="lights off"), 2)
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_quick_commands_only_get_words_after_the_name() -> None:
    """Overheard speech never runs a quick command: only what follows "Reachy" does."""
    seen: list[str] = []

    async def quick(text: str) -> _QuickReply | None:
        seen.append(text)
        return _QuickReply("Okay.")

    async def speak(_text: str, _language: str):  # type: ignore[no-untyped-def]
        yield 24_000, np.zeros(24, dtype=np.int16)

    for sentence, expected in (
        ("we were talking. Reachy, turn off the lights", ["turn off the lights"]),
        ("we should turn off the lights", []),  # acoustic wake, but no name in the transcript
    ):
        seen.clear()
        processor = _processor_with_open_wake_segment(sentence)
        handler = FakeHandler()
        runtime = HybridRuntime(
            lambda: handler,
            context_processor=processor,
            local_processing_close_callback=processor.close,
            quick_commands=quick,
            reply_speaker=speak,
            wake_followup_timeout_s=0.05,
        )
        try:
            await runtime.activate("acoustic_wake")
            await wait_until(lambda: runtime.mode is not RuntimeMode.LOCAL_COMMAND, attempts=2000)
            assert seen == expected
            assert handler.started.is_set() == (not expected)
        finally:
            await runtime.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Reach, lights on.", "lights on."),
        ("Rich turn off the lights", "turn off the lights"),
        ("We should turn off the lights", ""),
        ("Mom said turn off the TV", ""),
        ("Turn off the lights", ""),
    ],
)
def test_request_after_misheard_wake_needs_a_name_like_first_word(text: str, expected: str) -> None:
    """Only a first word that sounds like "Reachy" counts as the name."""
    from reachy_mini_conversation_app.hybrid_runtime import request_after_misheard_wake

    assert request_after_misheard_wake(text) == expected


@pytest.mark.asyncio
async def test_a_misheard_name_cut_mid_request_waits_for_the_rest() -> None:
    """Whisper turned "Reachy, can..." into "which you can't"; the person kept talking, so the rest is the request."""
    seen: list[str] = []

    async def quick(text: str) -> _QuickReply | None:
        seen.append(text)
        return _QuickReply("It's 2:08 PM.")

    async def speak(_text: str, _language: str):  # type: ignore[no-untyped-def]
        yield 24_000, np.zeros(24, dtype=np.int16)

    processor = _processor_with_open_wake_segment("which you can't")
    close_wake = processor.close_open_segment

    def close_and_keep_talking() -> None:
        close_wake()
        # The person keeps talking: the next segment opens right after the wake.
        asyncio.get_running_loop().call_soon(setattr, processor, "_speech", object())
        asyncio.get_running_loop().call_soon(setattr, processor, "_next_sequence", 4)

    processor.close_open_segment = close_and_keep_talking  # type: ignore[method-assign]
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        quick_commands=quick,
        reply_speaker=speak,
        wake_followup_timeout_s=2.0,
    )
    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await wait_until(lambda: runtime._wake_followup_future is not None, attempts=2000)
        await asyncio.sleep(0.05)
        runtime.observe_local_transcript(TranscriptSegment("tell me the time", 2, 3, 3))
        assert await asyncio.wait_for(activation, timeout=3.0)
        assert seen == ["tell me the time"]
        assert not handler.started.is_set()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_acoustic_wake_transcribes_the_whole_request_as_one_clip() -> None:
    """Everything said after the name, until a pause, is one accurately transcribed clip; music is lowered meanwhile."""
    seen: list[str] = []
    clips: list[float] = []
    listening: list[bool] = []

    async def quick(text: str) -> _QuickReply | None:
        seen.append(text)
        return _QuickReply(None)

    async def transcribe_request(frame: AudioFrame) -> str:
        clips.append(frame[1].size / frame[0])
        return "what time is it"

    async def on_listening(active: bool) -> None:
        listening.append(active)

    processor = _processor_with_open_wake_segment("Reachy")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        quick_commands=quick,
        request_transcriber=transcribe_request,
        listening_observer=on_listening,
        wake_followup_timeout_s=0.2,
    )
    runtime.set_capture_barrier_provider(lambda: 1)
    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        speech = np.full(1_600, 3_000, dtype=np.int16)
        silence = np.zeros(1_600, dtype=np.int16)
        for sequence, audio in enumerate([speech] * 10 + [silence] * 10, start=2):
            await runtime.handle_frame((16_000, audio), capture_sequence=sequence)
        assert await asyncio.wait_for(activation, timeout=5.0)
        await asyncio.sleep(0)
        assert seen == ["what time is it"]
        assert clips == [pytest.approx(2.0)]
        assert not handler.started.is_set()
        assert listening == [True, False]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_a_request_for_the_conversation_reaches_gemini_as_audio_with_the_text_as_a_hint() -> None:
    """Gemini hears the request itself; a misheard local text ("Start" for "stop") is only a hint."""

    async def quick(_text: str) -> _QuickReply | None:
        return None  # not a quick command: it goes to the conversation

    async def transcribe_request(_frame: AudioFrame) -> str:
        return "Start the music."

    processor = _processor_with_open_wake_segment("Reachy")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        quick_commands=quick,
        request_transcriber=transcribe_request,
        wake_followup_timeout_s=0.2,
    )
    runtime.set_capture_barrier_provider(lambda: 3)  # frames 2-3 were captured before activation
    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        speech = np.full(1_600, 3_000, dtype=np.int16)
        silence = np.zeros(1_600, dtype=np.int16)
        for sequence, audio in enumerate([speech] * 10 + [silence] * 10, start=2):
            await runtime.handle_frame((16_000, audio), capture_sequence=sequence)
        assert await asyncio.wait_for(activation, timeout=5.0)
        await handler.started.wait()
        handler.set_connected(True)
        await wait_until(lambda: len(handler.receive_frames) == 20)

        assert handler.say_texts == []
        assert 'heard the request as: "Start the music."' in handler.get_initial_context()
        assert sum(int(frame[1][0] != 0) for frame in handler.receive_frames) == 10  # all of the request
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_request_clip_keeps_audio_captured_before_activation_caught_up() -> None:
    """On a busy Mac the wake is detected late; audio up to the barrier still belongs to the request ("Prende ...")."""
    clips: list[float] = []

    async def quick(_text: str) -> _QuickReply | None:
        return _QuickReply(None)

    async def transcribe_request(frame: AudioFrame) -> str:
        clips.append(frame[1].size / frame[0])
        return "prende la luz"

    processor = _processor_with_open_wake_segment("Reachy")
    runtime = HybridRuntime(
        FakeHandler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        quick_commands=quick,
        request_transcriber=transcribe_request,
        wake_followup_timeout_s=0.2,
    )
    runtime.set_capture_barrier_provider(lambda: 5)  # frames 2-5 were captured before activation
    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        speech = np.full(1_600, 3_000, dtype=np.int16)
        silence = np.zeros(1_600, dtype=np.int16)
        for sequence, audio in enumerate([speech] * 10 + [silence] * 10, start=2):
            await runtime.handle_frame((16_000, audio), capture_sequence=sequence)
        assert await asyncio.wait_for(activation, timeout=5.0)
        assert clips == [pytest.approx(2.0)]  # all ten speech frames, not only the six after the barrier
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_a_clip_that_lost_the_verb_falls_back_to_the_fast_transcript() -> None:
    """The clip heard only "la luz"; the fast transcript had "Reachy, prende la luz", which runs locally."""
    seen: list[str] = []

    async def quick(text: str) -> _QuickReply | None:
        seen.append(text)
        return _QuickReply(None) if text.startswith("prende") else None

    async def transcribe_request(_frame: AudioFrame) -> str:
        return "la luz"

    processor = _processor_with_open_wake_segment("Reachy, prende la luz")
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        quick_commands=quick,
        request_transcriber=transcribe_request,
        wake_followup_timeout_s=0.2,
    )
    runtime.set_capture_barrier_provider(lambda: 1)
    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        speech = np.full(1_600, 3_000, dtype=np.int16)
        silence = np.zeros(1_600, dtype=np.int16)
        for sequence, audio in enumerate([speech] * 5 + [silence] * 10, start=2):
            await runtime.handle_frame((16_000, audio), capture_sequence=sequence)
        assert await asyncio.wait_for(activation, timeout=5.0)
        assert seen == ["la luz", "prende la luz"]
        assert not handler.started.is_set()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_a_misheard_transcript_wake_request_is_heard_again_accurately() -> None:
    """whisper-small wrote "prendela los"; the accurate model hears the same audio as "prende las luces"."""
    seen: list[str] = []
    heard_again: list[int] = []

    async def quick(text: str) -> _QuickReply | None:
        seen.append(text)
        return _QuickReply(None) if text == "prende las luces." else None

    async def transcribe_request(frame: AudioFrame) -> str:
        heard_again.append(frame[1].size)
        return "Reachy, prende las luces."

    processor = _processor_with_open_wake_segment("unused")
    processor.segment_audio = lambda sequence: (16_000, np.ones(160, dtype=np.int16)) if sequence == 5 else None  # type: ignore[method-assign]
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        quick_commands=quick,
        request_transcriber=transcribe_request,
    )
    try:
        runtime.observe_local_transcript(TranscriptSegment("Reachy, prendela los.", 1, 2, 5))
        await wait_until(lambda: len(seen) == 2 and runtime.mode is RuntimeMode.LOCAL_AMBIENT, attempts=2000)
        assert seen == ["prendela los.", "prende las luces."]
        assert heard_again == [160]
        assert not handler.started.is_set()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_follow_up_window_is_longer_while_a_child_who_needs_time_is_talking() -> None:
    """With Enzo in the conversation, Reachy waits 45 s instead of 15 s before standby."""
    clock = FakeClock()
    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        inactivity_timeout_s=60,
        follow_up_window_s=15,
        follow_up_window_for=lambda: 45.0,
        clock=clock,
    )
    assert await runtime.activate()
    handler.set_connected(True)
    runtime.note_playback_queued(1.0)  # reply ends at t=1

    clock.advance(30)  # t=31: past 15 s, Enzo is still thinking
    assert not await runtime.check_inactivity()
    clock.advance(16)  # t=47: 46 s after the reply
    assert await runtime.check_inactivity()
    await runtime.close()


def test_follow_up_by_person_parses_names_and_seconds() -> None:
    """Bad entries are skipped; names match without case."""
    from reachy_mini_conversation_app.config import _follow_up_by_person

    assert _follow_up_by_person("Enzo=45, mara=40, bad, x=0, y=999") == {"enzo": 45.0, "mara": 40.0}
    assert _follow_up_by_person(None) == {}


@pytest.mark.asyncio
async def test_a_conversation_that_cannot_connect_says_so_out_loud() -> None:
    """No credits or no internet: instead of silence, Reachy says it cannot connect, then keeps listening."""
    from reachy_mini_conversation_app.quick_commands import CANNOT_CONNECT

    spoken: list[tuple[str, str]] = []

    async def speak(text: str, language: str):  # type: ignore[no-untyped-def]
        spoken.append((text, language))
        yield 24_000, np.zeros(24, dtype=np.int16)

    runtime = HybridRuntime(lambda: FakeHandler(fail_start=True), reply_speaker=speak)

    assert await runtime.activate()
    await wait_until(lambda: bool(spoken))
    assert spoken == [(CANNOT_CONNECT["es"], "es")]  # no request text: the household's language
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await runtime.close()


@pytest.mark.asyncio
async def test_a_conversation_that_drops_after_connecting_does_not_apologise() -> None:
    """Only a conversation that never connected gets the apology; a dropped one closes as before."""

    class DropsAfterConnecting(FakeHandler):
        async def start_up(self) -> None:
            self.events.append("start")
            self.started.set()
            self.set_connected(True)
            await asyncio.sleep(0.05)
            raise RuntimeError("dropped sentinel")

    spoken: list[str] = []

    async def speak(text: str, _language: str):  # type: ignore[no-untyped-def]
        spoken.append(text)
        yield 24_000, np.zeros(24, dtype=np.int16)

    runtime = HybridRuntime(lambda: DropsAfterConnecting(), reply_speaker=speak)

    assert await runtime.activate()
    for _ in range(200):
        if runtime.mode is RuntimeMode.LOCAL_AMBIENT:
            break
        await asyncio.sleep(0.01)
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    await asyncio.sleep(0.1)
    assert spoken == []
    await runtime.close()


def _asleep_runtime_with_wake_audio(heard: str) -> tuple[HybridRuntime, FakeHandler, list[str], list[Any]]:
    """Return an asleep runtime whose fast transcript has no name; the accurate model hears ``heard``."""
    processor = _processor_with_open_wake_segment("people are over there")
    handler = FakeHandler()
    acknowledged: list[str] = []
    asked: list[Any] = []
    wake_audio = (16000, np.full(16000, 300, dtype=np.int16))

    async def accurate(audio: Any) -> str:
        asked.append(audio)
        return heard if audio is wake_audio else ""

    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        acknowledgement_callback=acknowledged.append,
        request_transcriber=accurate,
        wake_followup_timeout_s=0.1,
    )
    runtime._REQUEST_CLIP_START_S = 0.1
    runtime._asleep = True
    runtime._wake_name_audio = wake_audio
    return runtime, handler, acknowledged, asked


@pytest.mark.asyncio
async def test_asleep_the_accurate_model_can_confirm_a_name_the_fast_transcript_missed() -> None:
    """The wake audio heard as "Richie!" by the accurate model wakes him, as his name first would."""
    runtime, handler, acknowledged, asked = _asleep_runtime_with_wake_audio("Richie!")
    try:
        await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert acknowledged == ["acoustic_wake"]
        assert not runtime._asleep
        assert any(audio[0] == 16000 and audio[1].size == 16000 for audio in asked)
        assert runtime._wake_name_audio is None  # used once, not kept
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_asleep_the_accurate_model_without_the_name_keeps_him_asleep() -> None:
    """Ordinary words in the wake audio ("Thank you.") still leave him asleep and the cloud closed."""
    runtime, handler, acknowledged, _asked = _asleep_runtime_with_wake_audio("Thank you.")
    try:
        assert not await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert acknowledged == [] and not handler.started.is_set()
        assert runtime._asleep
    finally:
        await runtime.close()


def test_the_wake_window_keeps_only_the_last_seconds_at_the_current_rate() -> None:
    """The in-memory window holds at most 2.5 s, and a new sample rate starts it over."""
    runtime = HybridRuntime(lambda: FakeHandler())
    second = np.ones(16000, dtype="<i2").tobytes()
    for _ in range(4):
        runtime._keep_wake_window(second, 16000)
    assert len(runtime._wake_window) == int(16000 * runtime._WAKE_NAME_WINDOW_S) * 2

    runtime._keep_wake_window(np.ones(100, dtype="<i2").tobytes(), 24000)
    assert (len(runtime._wake_window), runtime._wake_window_rate) == (200, 24000)


def _runtime_with_wake_audio(
    fast: str | Exception, accurate: str | Exception, *, asleep: bool = False
) -> tuple[HybridRuntime, FakeHandler, list[str], list[bool], list[str]]:
    """Return a runtime holding wake audio; the fast and accurate models hear ``fast`` and ``accurate`` in it."""
    processor = _processor_with_open_wake_segment("people are over there")
    handler = FakeHandler()
    acknowledged: list[str] = []
    listening: list[bool] = []
    asked: list[str] = []
    wake_audio = (16000, np.full(16000, 300, dtype=np.int16))

    def hearing(label: str, heard: str | Exception) -> Callable[[Any], Awaitable[str]]:
        async def transcribe(audio: Any) -> str:
            if audio is not wake_audio:
                return ""
            asked.append(label)
            if isinstance(heard, Exception):
                raise heard
            return heard

        return transcribe

    async def on_listening(active: bool) -> None:
        listening.append(active)

    runtime = HybridRuntime(
        lambda: handler,
        context_processor=processor,
        local_processing_close_callback=processor.close,
        acknowledgement_callback=acknowledged.append,
        request_transcriber=hearing("accurate", accurate),
        name_check_transcriber=hearing("fast", fast),
        listening_observer=on_listening,
        wake_followup_timeout_s=0.1,
    )
    runtime._REQUEST_CLIP_START_S = 0.1
    runtime._asleep = asleep
    runtime._wake_name_audio = wake_audio
    return runtime, handler, acknowledged, listening, asked


@pytest.mark.asyncio
async def test_the_fast_model_hearing_the_name_wakes_him_without_asking_the_accurate_one() -> None:
    """Awake, "Richie." from whisper-small is enough: he reacts and the conversation opens."""
    runtime, handler, acknowledged, listening, asked = _runtime_with_wake_audio("Richie.", "unused")
    try:
        assert await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert asked == ["fast"]
        assert acknowledged == ["acoustic_wake"] and listening[0] is True
        await handler.started.wait()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_the_accurate_model_gets_a_second_try_when_the_fast_one_misses_the_name() -> None:
    """Whisper-small heard "Pretty!" but turbo hears "Ritchie.": he wakes (2026-10-28, 09:53)."""
    runtime, handler, acknowledged, _listening, asked = _runtime_with_wake_audio("Pretty!", "Ritchie.")
    try:
        assert await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert asked == ["fast", "accurate"]
        assert acknowledged == ["acoustic_wake"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_a_wake_with_no_name_in_its_audio_goes_back_to_listening_without_reacting() -> None:
    """Breakfast clinks scored 0.98 but neither model hears a name: no cue, no conversation, still listening."""
    runtime, handler, acknowledged, listening, asked = _runtime_with_wake_audio("so", "I")
    try:
        assert not await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert asked == ["fast", "accurate"]
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert acknowledged == [] and listening == []
        assert not handler.started.is_set()
        assert runtime._request_clip is None and runtime._wake_name_audio is None
        assert not runtime._asleep
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_when_whisper_is_down_an_awake_reachy_still_wakes_on_the_wake_model() -> None:
    """Both checks failing is not a reason to ignore him: awake, he wakes as before the check."""
    runtime, handler, acknowledged, _listening, asked = _runtime_with_wake_audio(
        httpx.ConnectError("down"), httpx.ConnectError("down")
    )
    try:
        assert await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert asked == ["fast", "accurate"]
        assert acknowledged == ["acoustic_wake"]
    finally:
        await runtime.close()


@pytest.mark.parametrize(
    ("fast", "accurate"),
    [
        ("Háblale a Richi, salúdalo.", "Háblale a Richi, salúdalo."),
        (httpx.ConnectError("down"), httpx.ConnectError("down")),
    ],
)
@pytest.mark.asyncio
async def test_asleep_only_the_name_leading_the_wake_audio_wakes_him(
    fast: str | Exception, accurate: str | Exception
) -> None:
    """Asleep, the name mid-sentence or a failed check keeps him asleep and still."""
    runtime, handler, acknowledged, listening, _asked = _runtime_with_wake_audio(fast, accurate, asleep=True)
    try:
        assert not await asyncio.wait_for(runtime.activate("acoustic_wake"), timeout=3.0)
        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert acknowledged == [] and listening == []
        assert not handler.started.is_set()
        assert runtime._asleep
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_no_cue_until_the_name_check_passes() -> None:
    """The antennas, the head turn and music lowering wait for Whisper to hear the name."""
    release = asyncio.Event()
    runtime, handler, acknowledged, listening, _asked = _runtime_with_wake_audio("unused", "unused")
    wake_audio = runtime._wake_name_audio

    async def slow_fast(audio: Any) -> str:
        if audio is not wake_audio:
            return ""
        await release.wait()
        return "Reachy!"

    runtime._name_check_transcriber = slow_fast
    try:
        activation = asyncio.create_task(runtime.activate("acoustic_wake"))
        await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        await asyncio.sleep(0.05)
        assert acknowledged == [] and listening == []
        release.set()
        assert await asyncio.wait_for(activation, timeout=3.0)
        assert acknowledged == ["acoustic_wake"]
    finally:
        await runtime.close()


@pytest.mark.parametrize(("heard", "opens"), [("Richie.", True), ("so", False)])
@pytest.mark.asyncio
async def test_an_acoustic_wake_sends_the_audio_it_fired_on_to_the_name_check(heard: str, opens: bool) -> None:
    """Awake, the wake model's own audio (in memory) is what Whisper checks; only a heard name opens the cloud."""
    checked: list[set[int]] = []

    async def fast(audio: AudioFrame) -> str:
        checked.append(set(audio[1].tolist()))
        return heard

    async def accurate(_audio: AudioFrame) -> str:
        return ""

    handler = FakeHandler()
    runtime = HybridRuntime(
        lambda: handler,
        acoustic_wake_detector=OpenWakeWordDetector(AcousticScoreModel()),
        request_transcriber=accurate,
        name_check_transcriber=fast,
    )
    runtime._REQUEST_CLIP_START_S = 0.1
    runtime.set_capture_barrier_provider(lambda: 2)
    try:
        await runtime.handle_frame((16000, np.full(1280, 111, dtype=np.int16)), capture_sequence=1)
        await runtime.handle_frame((16000, np.full(1280, 222, dtype=np.int16)), capture_sequence=2)
        await wait_until(lambda: bool(checked))
        assert checked[0] == {111, 222}
        if opens:
            await asyncio.wait_for(handler.started.wait(), timeout=3.0)
        else:
            await wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_AMBIENT and not runtime._local_event_tasks)
            assert not handler.started.is_set()
        assert runtime._wake_name_audio is None
    finally:
        await runtime.close()
