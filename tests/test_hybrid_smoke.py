"""Hermetic scenario checks for the local/cloud hybrid runtime."""

import os
import sys
import time
import asyncio
import logging
import threading
import subprocess
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock
from collections.abc import Callable

import numpy as np
import pytest

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector
from reachy_mini_conversation_app.local_actions import LocalActionExecutor
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.ambient_context import TranscriptSegment
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.local_intent_router import LocalIntentRouter
from reachy_mini_conversation_app.audio.speaker_output import GStreamerSpeakerOutput
from reachy_mini_conversation_app.conversation_handler import AudioFrame, HandlerOutput, ConversationHandler


class SmokeClock:
    """Controllable monotonic clock for lifecycle assertions."""

    def __init__(self) -> None:
        """Start at zero seconds."""
        self.value = 0.0

    def __call__(self) -> float:
        """Return the current timestamp."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Advance the clock by a deterministic duration."""
        self.value += seconds


class SmokeHandler(ConversationHandler):
    """Long-lived provider fake exposing the shared handler boundary."""

    def __init__(self) -> None:
        """Initialize lifecycle, input, and output recording."""
        super().__init__()
        self.deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
        self.output_queue: asyncio.Queue[AudioFrame | AdditionalOutputs] = asyncio.Queue()
        self.started = asyncio.Event()
        self.stop_requested = asyncio.Event()
        self.say_called = asyncio.Event()
        self.connected = False
        self.shutdown_calls = 0
        self.receive_frames: list[AudioFrame] = []
        self.say_texts: list[str] = []

    def set_connected(self, connected: bool) -> None:
        """Publish a deterministic connection transition to the runtime."""
        self.connected = connected
        self._emit_connection_state(connected)

    def _is_connected(self) -> bool:
        """Return whether this fake provider is connected."""
        return self.connected

    async def start_up(self) -> None:
        """Stay connected until the runtime requests shutdown."""
        self.started.set()
        self.set_connected(True)
        await self.stop_requested.wait()

    async def shutdown(self) -> None:
        """Close the fake connection exactly once per runtime generation."""
        self.shutdown_calls += 1
        self.stop_requested.set()
        self.set_connected(False)

    async def receive(self, frame: AudioFrame) -> None:
        """Retain a defensive copy of one provider-bound frame."""
        self.receive_frames.append((frame[0], frame[1].copy()))
        self._emit_traffic("audio", int(frame[1].nbytes))

    async def say(self, text: str) -> None:
        """Record one addressed text turn."""
        self.say_texts.append(text)
        self.say_called.set()

    async def emit(self) -> HandlerOutput:
        """Return one queued output without blocking the smoke loop."""
        try:
            return self.output_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def apply_personality(self, profile: str | None) -> str:
        """Satisfy the provider contract for the smoke lifecycle."""
        return profile or ""

    async def get_available_voices(self) -> list[str]:
        """Return the fake provider voice catalog."""
        return ["smoke"]

    def get_current_voice(self) -> str:
        """Return the current fake voice."""
        return "smoke"

    async def change_voice(self, voice: str) -> str:
        """Return the requested fake voice."""
        return voice


class SmokePipeline:
    """Blocking fake native sink for deterministic burst pressure."""

    def __init__(self) -> None:
        """Initialize output recording and release controls."""
        self.pushed: list[np.ndarray] = []
        self.push_started = threading.Event()
        self.release_push = threading.Event()
        self.block_push = False
        self.closed = 0
        self.flushes = 0
        self.events: list[str] = []
        self.flush_completed = threading.Event()
        self.current_frame_pushed = threading.Event()

    def start(self) -> None:
        """Start the fake native sink."""

    def push(self, audio: np.ndarray) -> None:
        """Record one frame, optionally blocking the writer."""
        self.push_started.set()
        if self.block_push:
            self.release_push.wait()
        self.pushed.append(audio.copy())
        self.events.append(f"push:{float(audio[0])}")
        if float(audio[0]) == 2.0:
            self.current_frame_pushed.set()

    def flush(self) -> None:
        """Accept a native flush request."""
        self.flushes += 1
        self.events.append("flush")
        self.flush_completed.set()

    def close(self) -> None:
        """Record native cleanup."""
        self.closed += 1
        self.events.append("close")


def _frame(value: int, *, sample_rate: int = 100) -> AudioFrame:
    """Return one deterministic local PCM frame."""
    return sample_rate, np.full(sample_rate // 10, value, dtype=np.int16)


def _robot_and_movement() -> tuple[MagicMock, MagicMock]:
    """Return fake robot and movement owners for existing local tools."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4, dtype=np.float32)
    robot.get_current_joint_positions.return_value = (
        [0.0, 0.1, -0.2, 0.3, -0.4, 0.5, -0.6],
        [0.11, -0.22],
    )
    return robot, MagicMock()


def _local_runtime(
    *,
    acknowledgements: list[str],
    silence_calls: list[str],
) -> tuple[HybridRuntime, MagicMock, MagicMock]:
    """Build one runtime using the real wake, router, and action boundaries."""
    robot, movement_manager = _robot_and_movement()
    dependencies = ToolDependencies(reachy_mini=robot, movement_manager=movement_manager)
    executor = LocalActionExecutor(
        dependencies,
        silence_callback=lambda: silence_calls.append("be_quiet"),
    )
    provider_factory = MagicMock(side_effect=AssertionError("exact local command opened cloud"))
    runtime = HybridRuntime(
        provider_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=100),
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=executor,
        acknowledgement_callback=acknowledgements.append,
    )
    return runtime, provider_factory, movement_manager


async def _wait_until(predicate: Callable[[], bool], *, attempts: int = 100) -> None:
    """Yield until a synchronous lifecycle predicate succeeds."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("smoke lifecycle predicate did not become true")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_owner"),
    (
        ("stop", "stop"),
        ("be quiet", "silence"),
        ("look left", "left"),
        ("look right", "right"),
        ("look at me", "tracking"),
    ),
    ids=("stop", "be-quiet", "look-left", "look-right", "look-at-me"),
)
async def test_exact_wake_commands_stay_local_and_cloud_free(
    command: str,
    expected_owner: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every canonical command reaches the local executor without provider construction."""
    acknowledgements: list[str] = []
    silence_calls: list[str] = []
    runtime, provider_factory, movement_manager = _local_runtime(
        acknowledgements=acknowledgements,
        silence_calls=silence_calls,
    )
    try:
        with caplog.at_level(logging.INFO):
            await runtime.handle_frame(_frame(2_000), capture_sequence=1)
            runtime.observe_local_transcript(TranscriptSegment(f"Reachy, {command}", 0.0, 0.1, 1))
            await _wait_until(lambda: runtime.status.local_commands_executed == 1)

        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert acknowledgements == ["wake_word"]
        assert provider_factory.call_count == 0
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
        assert runtime.metrics.cloud_audio_bytes_sent == 0
        assert runtime.metrics.cloud_video_frames_sent == 0
        assert runtime.status.wake_detections == 1
        assert runtime.status.local_command_escalations == 0
        assert runtime.ambient_buffer.seconds_buffered == pytest.approx(0.1)
        assert f"Reachy, {command}" not in caplog.text

        if expected_owner == "stop":
            movement_manager.clear_move_queue.assert_called_once_with()
        elif expected_owner == "silence":
            assert silence_calls == ["be_quiet"]
            movement_manager.clear_move_queue.assert_not_called()
        elif expected_owner == "tracking":
            movement_manager.set_head_tracking.assert_called_once_with(True)
        else:
            movement_manager.queue_move.assert_called_once()
            movement_manager.set_moving_state.assert_called_once()
            expected_yaw = 40 if expected_owner == "left" else -40
            queued_move = movement_manager.queue_move.call_args.args[0]
            np.testing.assert_allclose(
                queued_move.target_head_pose,
                create_head_pose(0, 0, 0, 0, 0, expected_yaw, degrees=True),
            )
        assert runtime.active_handler is None
    finally:
        await runtime.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_split_final_exact_followup_stays_local() -> None:
    """A wake and exact command split across final segments stays cloud-free."""
    acknowledgements: list[str] = []
    silence_calls: list[str] = []
    runtime, provider_factory, movement_manager = _local_runtime(
        acknowledgements=acknowledgements,
        silence_calls=silence_calls,
    )

    try:
        runtime.observe_local_transcript(TranscriptSegment("Reachy", 0.0, 0.1, 1))
        await _wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        runtime.observe_local_transcript(TranscriptSegment("stop", 0.1, 0.2, 2))
        await _wait_until(lambda: runtime.status.local_commands_executed == 1)

        assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        assert provider_factory.call_count == 0
        movement_manager.clear_move_queue.assert_called_once_with()
        assert acknowledgements == ["wake_word"]
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
        assert runtime.metrics.cloud_audio_bytes_sent == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_split_final_delayed_non_exact_followup_escalates_with_suffix() -> None:
    """A delayed uncertain follow-up preserves its suffix for the cloud provider."""
    handler = SmokeHandler()
    factory_calls: list[SmokeHandler] = []

    def factory() -> SmokeHandler:
        """Return the one fake provider owned by this scenario."""
        factory_calls.append(handler)
        return handler

    robot, movement_manager = _robot_and_movement()
    suffix = "private household question"
    runtime = HybridRuntime(
        factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=100),
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=robot, movement_manager=movement_manager)),
        acknowledgement_callback=lambda _reason: None,
        inactivity_timeout_s=5,
    )

    try:
        runtime.observe_local_transcript(TranscriptSegment("Reachy", 0.0, 0.1, 1))
        await _wait_until(lambda: runtime.mode is RuntimeMode.LOCAL_COMMAND)
        await asyncio.sleep(0.01)
        runtime.observe_local_transcript(TranscriptSegment(suffix, 0.2, 0.3, 2))
        await handler.started.wait()
        await handler.say_called.wait()

        assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
        assert factory_calls == [handler]
        assert handler.say_texts == [suffix]
        assert runtime.status.local_command_escalations == 1
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0

        await runtime.handle_frame(_frame(9_999), capture_sequence=1)
        assert [int(audio[0]) for _, audio in handler.receive_frames] == [9_999]
        assert runtime.metrics.cloud_audio_bytes_sent == _frame(9_999)[1].nbytes
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_bare_acoustic_wake_acknowledges_after_ready_without_ambient_upload() -> None:
    """A bare acoustic wake gets one concise cloud acknowledgment after readiness."""
    handler = SmokeHandler()
    acknowledgements: list[str] = []
    runtime = HybridRuntime(
        lambda: handler,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=100),
        acknowledgement_callback=acknowledgements.append,
    )

    try:
        await runtime.handle_frame(_frame(1_000), capture_sequence=1)
        assert await runtime.activate("acoustic_wake")
        await handler.started.wait()
        await handler.say_called.wait()

        assert acknowledgements == ["acoustic_wake"]
        assert handler.say_texts == ["Acknowledge the wake word briefly, then listen."]
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    finally:
        await runtime.close()


def test_blocked_speaker_flush_discards_stale_generation_before_new_audio() -> None:
    """A bounded interruption flushes before any current-generation frame is emitted."""
    pipeline = SmokePipeline()
    pipeline.block_push = True
    output = GStreamerSpeakerOutput(100, max_queue_frames=4, pipeline_factory=lambda _rate: pipeline)
    try:
        output.start()
        output.push(np.full(4, 1, dtype=np.float32))
        assert pipeline.push_started.wait(timeout=1.0)
        output.push(np.full(4, 99, dtype=np.float32))

        started = time.monotonic()
        output.flush()
        elapsed = time.monotonic() - started
        assert elapsed < 0.2
        assert pipeline.flushes == 0

        output.push(np.full(4, 2, dtype=np.float32))
        pipeline.release_push.set()
        assert pipeline.flush_completed.wait(timeout=1.0)
        assert pipeline.current_frame_pushed.wait(timeout=1.0)
        assert pipeline.events[:3] == ["push:1.0", "flush", "push:2.0"]
        assert [int(audio[0]) for audio in pipeline.pushed] == [1, 2]
    finally:
        pipeline.release_push.set()
        output.close()


def test_optional_native_speaker_subprocess_smoke() -> None:
    """Run native app-owned GStreamer cycles only when explicitly requested."""
    if os.environ.get("REACHY_RUN_NATIVE_AUDIO_SMOKE") != "1":
        pytest.skip("native speaker smoke is opt-in: set REACHY_RUN_NATIVE_AUDIO_SMOKE=1")
    if importlib.util.find_spec("gi") is None or importlib.util.find_spec("gi.repository.Gst") is None:
        pytest.skip("native speaker smoke requires the optional gi/GStreamer runtime")

    project_root = Path(__file__).resolve().parents[1]
    child = Path(__file__).with_name("native_speaker_smoke.py")
    child_environment = os.environ.copy()
    child_environment["REACHY_MINI_SKIP_DOTENV"] = "1"
    source_path = str(project_root / "src")
    for variable in (
        "PYTHONPATH",
        "GI_TYPELIB_PATH",
        "GST_PYTHONPATH_1_0",
        "GST_PLUGIN_PATH_1_0",
        "GST_PLUGIN_SYSTEM_PATH_1_0",
        "GST_PLUGIN_SCANNER_1_0",
        "GST_REGISTRY_1_0",
    ):
        child_environment.pop(variable, None)
    child_environment["PYTHONPATH"] = source_path
    process = subprocess.Popen(
        [sys.executable, str(child)],
        cwd=project_root,
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=15.0)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"native speaker smoke timed out; exit={process.returncode}; stderr={stderr[-2000:]}")

    return_code = process.returncode
    if return_code is None:
        pytest.fail("native speaker smoke ended without a child return code")
    status = f"exit {return_code}" if return_code >= 0 else f"signal {-return_code}"
    assert return_code == 0, f"native speaker smoke failed ({status}); stderr={stderr[-2000:]}"
    assert "native speaker smoke completed" in stdout


@pytest.mark.asyncio
async def test_uncertain_wake_suffix_uses_fake_provider_then_inactivity_returns_local(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An uncertain addressed suffix is preserved for cloud, then closes cleanly when idle."""
    monkeypatch.chdir(tmp_path)
    clock = SmokeClock()
    handler = SmokeHandler()
    factory_calls: list[SmokeHandler] = []
    sentinel = "private household question"
    robot, movement_manager = _robot_and_movement()

    def factory() -> SmokeHandler:
        """Return the one fake provider owned by this scenario."""
        factory_calls.append(handler)
        return handler

    runtime = HybridRuntime(
        factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=100),
        wake_detector=TranscriptWakeWordDetector(cooldown_seconds=0),
        intent_router=LocalIntentRouter(),
        action_executor=LocalActionExecutor(ToolDependencies(reachy_mini=robot, movement_manager=movement_manager)),
        acknowledgement_callback=lambda _reason: None,
        inactivity_timeout_s=5,
        clock=clock,
    )

    try:
        with caplog.at_level(logging.INFO):
            await runtime.handle_frame(_frame(4321), capture_sequence=1)
            runtime.observe_local_transcript(TranscriptSegment(f"Reachy, {sentinel}", 0.0, 0.1, 1))
            await handler.started.wait()
            await handler.say_called.wait()

        assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
        assert factory_calls == [handler]
        assert handler.say_texts == [sentinel]
        assert handler.receive_frames == []
        assert runtime.status.local_command_escalations == 1
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
        assert runtime.metrics.cloud_audio_bytes_sent == 0
        assert runtime.ambient_buffer.seconds_buffered == pytest.approx(0.1)
        assert sentinel not in caplog.text

        await handler.output_queue.put(AdditionalOutputs({"role": "assistant", "content": "queued"}))
        runtime.clear_provider_output()
        assert handler.output_queue.empty()

        await runtime.handle_frame(_frame(9_999), capture_sequence=2)
        assert [int(audio[0]) for _, audio in handler.receive_frames] == [9_999]
        assert runtime.metrics.cloud_audio_bytes_sent == _frame(9_999)[1].nbytes
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0

        clock.advance(5)
        assert await runtime.check_inactivity()
        mode_after_inactivity = runtime.mode
        assert mode_after_inactivity.value == RuntimeMode.LOCAL_AMBIENT.value
        assert runtime.active_handler is None
        assert handler.shutdown_calls == 1
        assert tuple(tmp_path.iterdir()) == ()
    finally:
        await runtime.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_provider_lifecycle_reconnects_without_leaking_handlers() -> None:
    """Repeated fake provider generations can be interrupted and shut down exactly once."""
    handlers = [SmokeHandler() for _ in range(8)]
    factory_calls: list[SmokeHandler] = []

    def factory() -> SmokeHandler:
        """Return the next owned fake provider."""
        handler = handlers.pop(0)
        factory_calls.append(handler)
        return handler

    runtime = HybridRuntime(factory)
    try:
        for _ in range(8):
            assert await runtime.activate("smoke")
            handler = factory_calls[-1]
            await handler.started.wait()
            mode_after_activation = runtime.mode
            assert mode_after_activation is RuntimeMode.CLOUD_CONVERSATION
            assert await runtime.deactivate_cloud("smoke_reconnect")
            mode_after_deactivation = runtime.mode
            assert mode_after_deactivation is RuntimeMode.LOCAL_AMBIENT
            assert handler.shutdown_calls == 1

        assert len(factory_calls) == 8
        assert runtime.active_handler is None
        assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    finally:
        await runtime.close()


def test_bursty_provider_audio_uses_bounded_output_and_preserves_order() -> None:
    """The existing fake native seam exposes burst drops, order, duration, and close behavior."""
    pipeline = SmokePipeline()
    pipeline.block_push = True
    output = GStreamerSpeakerOutput(100, max_queue_frames=2, pipeline_factory=lambda _rate: pipeline)
    output.start()
    try:
        output.push(np.zeros(4, dtype=np.float32))
        assert pipeline.push_started.wait(timeout=1.0)
        for value in range(1, 11):
            output.push(np.full(4, value, dtype=np.float32))

        assert output.dropped_frames >= 1
        pipeline.release_push.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(pipeline.pushed) < 3:
            time.sleep(0.005)

        labels = [int(round(float(audio[0]))) for audio in pipeline.pushed]
        assert labels[0] == 0
        assert labels == sorted(labels)
        assert labels[-1] == 10
        assert all(audio.size / output.sample_rate == pytest.approx(0.04) for audio in pipeline.pushed)
    finally:
        pipeline.release_push.set()
        output.close()

    assert pipeline.closed == 1
