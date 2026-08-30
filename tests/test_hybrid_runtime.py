"""Tests for the local/cloud hybrid runtime lifecycle."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock
from collections.abc import Callable

import numpy as np
import pytest

from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector
from reachy_mini_conversation_app.local_actions import LocalActionExecutor
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.ambient_context import (
    TranscriptSegment,
    AmbientContextConfig,
    AmbientContextStrategy,
    AmbientContextProcessor,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.local_intent_router import LocalIntentRouter
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
        wake_followup_timeout_s=30,
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
        wake_followup_timeout_s=30,
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


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_wake_followup_timeout_must_be_finite_and_positive(timeout: float) -> None:
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
async def test_after_close_frames_stay_local_and_processor_errors_are_nonfatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Closed runtime keeps local processing alive and logs only error metadata."""
    sentinel = 31415
    processor = FakeLocalProcessor(fail=True)
    runtime = HybridRuntime(lambda: FakeHandler(), local_processor=processor)
    await runtime.close()
    caplog.set_level(logging.WARNING)
    await runtime.handle_frame(frame(sentinel))

    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert "RuntimeError" in caplog.text
    assert str(sentinel) not in caplog.text


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
