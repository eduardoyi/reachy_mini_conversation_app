"""Provider lifecycle and privacy boundary for the local/cloud runtime."""

import math
import time
import asyncio
import inspect
import logging
import weakref
import threading
from enum import Enum
from typing import TypeAlias
from collections import deque
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

from reachy_mini_conversation_app.config import (
    MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
    DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
)
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.streaming import audio_to_mono_int16
from reachy_mini_conversation_app.wake_word import AcousticWakeWordDetector, TranscriptWakeWordDetector
from reachy_mini_conversation_app.local_actions import LocalActionStatus, LocalActionExecutor
from reachy_mini_conversation_app.ambient_context import (
    TranscriptSegment,
    AmbientContextProcessor,
)
from reachy_mini_conversation_app.local_processing import (
    LocalProcessingBackend,
    NullLocalProcessingBackend,
)
from reachy_mini_conversation_app.local_intent_router import LocalIntentRouter, IntentDecisionReason
from reachy_mini_conversation_app.conversation_handler import (
    AudioFrame,
    TrafficKind,
    HandlerOutput,
    ConversationHandler,
)
from reachy_mini_conversation_app.local_audio_backends import RmsEnergyVAD


logger = logging.getLogger(__name__)


class RuntimeMode(str, Enum):
    """Operating modes at the cloud privacy boundary."""

    LOCAL_AMBIENT = "LOCAL_AMBIENT"
    LOCAL_COMMAND = "LOCAL_COMMAND"
    CLOUD_CONVERSATION = "CLOUD_CONVERSATION"
    STOPPING = "STOPPING"


class LocalCommandOutcome(str, Enum):
    """Metadata-only outcome for the most recent local command attempt."""

    LOCAL_EXECUTED = "local_executed"
    ROUTER_REJECTED = "router_rejected"
    FOLLOWUP_TIMEOUT = "followup_timeout"
    LOCAL_EXECUTION_FAILED = "local_execution_failed"


CallbackResult: TypeAlias = None | Awaitable[None]
HandlerFactory: TypeAlias = Callable[[], ConversationHandler | Awaitable[ConversationHandler]]
AcknowledgementCallback: TypeAlias = Callable[[str], CallbackResult]
ActivityObserver: TypeAlias = Callable[[str], None]
TranscriptObserver: TypeAlias = Callable[[str, str, bool], None]
StateObserver: TypeAlias = Callable[[RuntimeMode, str, int], None]
InteractionBusyObserver: TypeAlias = Callable[[bool], None]
CaptureBarrierProvider: TypeAlias = Callable[[], int | None]
Clock: TypeAlias = Callable[[], float]
PostActivationFrame: TypeAlias = tuple[int | None, AudioFrame]
LocalProcessingItem: TypeAlias = tuple[AudioFrame, asyncio.Future[None], int]
LocalProcessingCloseCallback: TypeAlias = Callable[[], CallbackResult]


@dataclass(frozen=True)
class TrafficMetricsSnapshot:
    """Immutable privacy and provider traffic counters."""

    ambient_audio_seconds_buffered: float
    ambient_audio_bytes_sent_to_cloud: int
    cloud_audio_bytes_sent: int
    cloud_video_frames_sent: int
    gemini_session_duration: float


@dataclass(frozen=True)
class RuntimeStatusSnapshot:
    """Immutable state snapshot suitable for status/UI consumers."""

    mode: RuntimeMode
    generation: int
    active: bool
    closed: bool
    provider_task_running: bool
    inactivity_monitor_running: bool
    connected: bool
    interaction_busy: bool = False
    playback_active: bool = False
    shutdown_error: str | None = None
    ambient_audio_seconds_buffered: float = 0.0
    ambient_audio_bytes_sent_to_cloud: int = 0
    cloud_audio_bytes_sent: int = 0
    cloud_video_frames_sent: int = 0
    gemini_session_duration: float = 0.0
    postactivation_frames_dropped: int = 0
    local_context_chars: int = 0
    transcript_segments_accepted: int = 0
    acoustic_wake_enabled: bool = False
    wake_detections: int = 0
    wake_cooldown_suppressed: int = 0
    wake_final_update_count: int = 0
    local_commands_executed: int = 0
    local_command_failures: int = 0
    local_command_escalations: int = 0
    local_command_last_outcome: LocalCommandOutcome | None = None
    local_command_last_router_reason: IntentDecisionReason | None = None
    local_command_router_rejections: int = 0
    local_command_followup_timeouts: int = 0
    local_processing_frames_dropped: int = 0
    local_command_latency_seconds_total: float = 0.0
    local_command_latency_seconds_max: float = 0.0
    context_activation_latency_seconds_total: float = 0.0
    context_activation_latency_seconds_max: float = 0.0
    local_context_strategy: str | None = None
    local_audio_seconds_observed: float = 0.0
    local_speech_frames: int = 0
    local_stt_requests: int = 0
    local_stt_completed: int = 0
    local_stt_failures: int = 0
    local_stt_timeouts: int = 0
    local_stt_dropped: int = 0
    local_stt_latency_seconds_total: float = 0.0
    local_stt_latency_seconds_max: float = 0.0
    local_stt_inflight_peak: int = 0
    local_stt_pending_at_activation: int = 0

    @property
    def cloud_active(self) -> bool:
        """Return whether a cloud conversation is currently selected."""
        return self.mode is RuntimeMode.CLOUD_CONVERSATION

    @property
    def has_active_handler(self) -> bool:
        """Return whether a handler is selected for the current generation."""
        return self.active

    def __call__(self) -> dict[str, object]:
        """Return a metadata-only dictionary for status RPC consumers."""
        return {
            "mode": self.mode.value,
            "state": self.mode.value,
            "generation": self.generation,
            "active": self.active,
            "connected": self.connected,
            "closed": self.closed,
            "provider_task_running": self.provider_task_running,
            "inactivity_monitor_running": self.inactivity_monitor_running,
            "interaction_busy": self.interaction_busy,
            "playback_active": self.playback_active,
            "shutdown_error": self.shutdown_error,
            "ambient_audio_seconds_buffered": self.ambient_audio_seconds_buffered,
            "ambient_audio_bytes_sent_to_cloud": self.ambient_audio_bytes_sent_to_cloud,
            "cloud_audio_bytes_sent": self.cloud_audio_bytes_sent,
            "cloud_video_frames_sent": self.cloud_video_frames_sent,
            "gemini_session_duration": self.gemini_session_duration,
            "postactivation_frames_dropped": self.postactivation_frames_dropped,
            "local_context_chars": self.local_context_chars,
            "transcript_segments_accepted": self.transcript_segments_accepted,
            "acoustic_wake_enabled": self.acoustic_wake_enabled,
            "wake_detections": self.wake_detections,
            "wake_cooldown_suppressed": self.wake_cooldown_suppressed,
            "wake_final_update_count": self.wake_final_update_count,
            "local_commands_executed": self.local_commands_executed,
            "local_command_failures": self.local_command_failures,
            "local_command_escalations": self.local_command_escalations,
            "local_command_last_outcome": (
                self.local_command_last_outcome.value if self.local_command_last_outcome is not None else None
            ),
            "local_command_last_router_reason": (
                self.local_command_last_router_reason.value
                if self.local_command_last_router_reason is not None
                else None
            ),
            "local_command_router_rejections": self.local_command_router_rejections,
            "local_command_followup_timeouts": self.local_command_followup_timeouts,
            "local_processing_frames_dropped": self.local_processing_frames_dropped,
            "local_command_latency_seconds_total": self.local_command_latency_seconds_total,
            "local_command_latency_seconds_max": self.local_command_latency_seconds_max,
            "context_activation_latency_seconds_total": self.context_activation_latency_seconds_total,
            "context_activation_latency_seconds_max": self.context_activation_latency_seconds_max,
            "local_context_strategy": self.local_context_strategy,
            "local_audio_seconds_observed": self.local_audio_seconds_observed,
            "local_speech_frames": self.local_speech_frames,
            "local_stt_requests": self.local_stt_requests,
            "local_stt_completed": self.local_stt_completed,
            "local_stt_failures": self.local_stt_failures,
            "local_stt_timeouts": self.local_stt_timeouts,
            "local_stt_dropped": self.local_stt_dropped,
            "local_stt_latency_seconds_total": self.local_stt_latency_seconds_total,
            "local_stt_latency_seconds_max": self.local_stt_latency_seconds_max,
            "local_stt_inflight_peak": self.local_stt_inflight_peak,
            "local_stt_pending_at_activation": self.local_stt_pending_at_activation,
        }


class HybridRuntime:
    """Coordinate local ambient capture with one on-demand cloud handler."""

    _MAX_ADDRESSED_TEXT_CHARS = 512
    _ACOUSTIC_WAKE_ACK_TEXT = "Acknowledge the wake word briefly, then listen."
    # About one second of 10 ms frames, so short event-loop stalls do not drop wake-word audio.
    _LOCAL_PROCESSING_QUEUE_SIZE = 100
    _SHUTDOWN_TIMEOUT_S = 15.0
    # Keep enough live PCM for the bounded provider setup handshake (15 s) and
    # leave a small margin for scheduling jitter, while still bounding memory.
    _POSTACTIVATION_BUFFER_SECONDS = 20.0
    _SPEECH_STARTED_REASONS = frozenset({"user_speech_started"})
    _SPEECH_STOPPED_REASONS = frozenset(
        {
            "user_speech_stopped",
            "user_transcription_completed",
            "user_transcription_complete",
            "user_turn_complete",
            "input_audio_transcription_completed",
            "conversation_turn_complete",
        }
    )

    _VALID_TRANSITIONS: dict[RuntimeMode, frozenset[RuntimeMode]] = {
        RuntimeMode.LOCAL_AMBIENT: frozenset({RuntimeMode.LOCAL_COMMAND}),
        RuntimeMode.LOCAL_COMMAND: frozenset(
            {RuntimeMode.LOCAL_AMBIENT, RuntimeMode.CLOUD_CONVERSATION, RuntimeMode.STOPPING}
        ),
        RuntimeMode.CLOUD_CONVERSATION: frozenset({RuntimeMode.STOPPING}),
        RuntimeMode.STOPPING: frozenset({RuntimeMode.LOCAL_AMBIENT}),
    }

    def __init__(
        self,
        handler_factory: HandlerFactory,
        *,
        ambient_buffer: AmbientAudioBuffer | None = None,
        local_processor: LocalProcessingBackend | None = None,
        context_processor: AmbientContextProcessor | None = None,
        wake_detector: TranscriptWakeWordDetector | None = None,
        acoustic_wake_detector: AcousticWakeWordDetector | None = None,
        intent_router: LocalIntentRouter | None = None,
        action_executor: LocalActionExecutor | None = None,
        local_processing_close_callback: LocalProcessingCloseCallback | None = None,
        acknowledgement_callback: AcknowledgementCallback | None = None,
        state_observer: StateObserver | None = None,
        inactivity_timeout_s: float = 0.0,
        wake_followup_timeout_s: float = DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
        clock: Clock = time.monotonic,
        monitor_interval_s: float = 0.1,
    ) -> None:
        """Initialize an idle runtime without constructing a provider handler."""
        if inactivity_timeout_s < 0:
            raise ValueError("Inactivity timeout must not be negative")
        if (
            wake_followup_timeout_s <= 0
            or not math.isfinite(wake_followup_timeout_s)
            or wake_followup_timeout_s > MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "Wake follow-up timeout must be finite, positive, and no greater than "
                f"{MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS:g} seconds"
            )
        if monitor_interval_s <= 0:
            raise ValueError("Inactivity monitor interval must be positive")

        self._handler_factory = handler_factory
        self._ambient_buffer = ambient_buffer if ambient_buffer is not None else AmbientAudioBuffer()
        self._local_processor = local_processor if local_processor is not None else NullLocalProcessingBackend()
        self._context_processor = context_processor
        if wake_detector is not None and acoustic_wake_detector is not None:
            raise ValueError("Select one wake detector for the supervised trial")
        self._wake_detector = wake_detector
        self._acoustic_wake_detector = acoustic_wake_detector
        self._acoustic_epoch = 0
        self._acoustic_stream_epoch: int | None = None
        self._intent_router = intent_router
        self._action_executor = action_executor
        self._local_processing_close_callback = local_processing_close_callback
        self._acknowledgement_callback = acknowledgement_callback
        self._state_observer = state_observer
        self._activity_observer: ActivityObserver | None = None
        self._transcript_observer: TranscriptObserver | None = None
        self._inactivity_timeout_s = float(inactivity_timeout_s)
        self._wake_followup_timeout_s = float(wake_followup_timeout_s)
        self._clock = clock
        self._monitor_interval_s = monitor_interval_s

        self._state_lock = asyncio.Lock()
        self._provider_call_lock = asyncio.Lock()
        self._lifecycle_lock = threading.RLock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._mode: RuntimeMode = RuntimeMode.LOCAL_AMBIENT
        self._generation = 0
        self._active_handler: ConversationHandler | None = None
        self._provider_task: asyncio.Task[None] | None = None
        self._inactivity_task: asyncio.Task[None] | None = None
        self._activation_task: asyncio.Task[bool] | None = None
        self._wake_followup_future: asyncio.Future[TranscriptSegment] | None = None
        self._wake_followup_after_sequence: int | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False
        self._shutdown_error: str | None = None
        self._stopping_handler: ConversationHandler | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_deadline: float | None = None
        self._close_deadline: float | None = None
        self._last_conversation_activity = self._clock()
        self._user_speaking = False
        self._interaction_busy = False
        self._playback_until = 0.0
        self._connected_since: float | None = None
        self._connection_ready = False
        self._shutdown_handler_tasks: dict[
            int, tuple[weakref.ReferenceType[ConversationHandler], asyncio.Task[None]]
        ] = {}
        self._capture_barrier_provider: CaptureBarrierProvider | None = None
        self._activation_capture_barrier: int | None = None
        self._postactivation_frames: deque[PostActivationFrame] = deque()
        self._postactivation_seconds = 0.0
        self._postactivation_flush_task: asyncio.Task[None] | None = None
        self._activation_text_task: asyncio.Task[None] | None = None
        self._pending_activation_text: tuple[int, str] | None = None
        self._postactivation_frames_dropped = 0
        self._playback_clearer: Callable[[], None] | None = None
        self._local_start_event = asyncio.Event()
        self._output_wakeup_event = asyncio.Event()
        self._local_event_tasks: set[asyncio.Task[bool]] = set()
        self._local_processing_queue: asyncio.Queue[LocalProcessingItem] | None = None
        self._local_processing_task: asyncio.Task[None] | None = None
        self._postactivation_speech_observed = False
        # Per-cloud-session diagnostics, logged once when the session ends (metadata only).
        self._session_audio_bytes_start = 0
        self._local_processing_drop_burst = False
        self._session_video_frames_start = 0
        self._session_queued_frames = 0
        self._session_ack_state = "not_needed"
        self._acoustic_ack_vad = RmsEnergyVAD()
        self._pending_acoustic_ack_generation: int | None = None
        self._acoustic_ack_task: asyncio.Task[None] | None = None

        self._metrics_lock = threading.Lock()
        self._ambient_audio_bytes_sent_to_cloud = 0
        self._cloud_audio_bytes_sent = 0
        self._cloud_video_frames_sent = 0
        self._gemini_session_duration = 0.0
        self._local_context_chars = 0
        self._local_commands_executed = 0
        self._local_command_failures = 0
        self._local_command_escalations = 0
        self._local_command_last_outcome: LocalCommandOutcome | None = None
        self._local_command_last_router_reason: IntentDecisionReason | None = None
        self._local_command_router_rejections = 0
        self._local_command_followup_timeouts = 0
        self._local_processing_frames_dropped = 0
        self._local_command_latency_seconds_total = 0.0
        self._local_command_latency_seconds_max = 0.0
        self._context_activation_latency_seconds_total = 0.0
        self._context_activation_latency_seconds_max = 0.0

    @property
    def mode(self) -> RuntimeMode:
        """Return the current runtime mode."""
        with self._lifecycle_lock:
            return self._mode

    @property
    def active_handler(self) -> ConversationHandler | None:
        """Return the selected cloud handler, if any."""
        with self._lifecycle_lock:
            return self._active_handler

    @property
    def generation(self) -> int:
        """Return the current activation generation."""
        with self._lifecycle_lock:
            return self._generation

    @property
    def ambient_buffer(self) -> AmbientAudioBuffer:
        """Return the bounded local ambient buffer."""
        return self._ambient_buffer

    @property
    def inactivity_timeout_s(self) -> float:
        """Return the configured cloud inactivity threshold."""
        return self._inactivity_timeout_s

    @property
    def interaction_busy(self) -> bool:
        """Return the provider's authoritative interaction busy state."""
        with self._lifecycle_lock:
            return self._interaction_busy

    @property
    def playback_active(self) -> bool:
        """Return whether estimated queued playback is still active."""
        with self._lifecycle_lock:
            return self._playback_is_active_locked(self._clock())

    async def get_transcript_preview(self) -> str | None:
        """Return the bounded local transcript, or ``None`` when unavailable."""
        self._assert_owner_loop()
        with self._lifecycle_lock:
            if self._closed:
                return None
        processor = self._context_processor
        if processor is None:
            return None
        return processor.get_transcript_preview(now=self._clock())

    @property
    def status(self) -> RuntimeStatusSnapshot:
        """Return an immutable runtime status snapshot."""
        return self.status_snapshot()

    @property
    def metrics(self) -> TrafficMetricsSnapshot:
        """Return an immutable traffic and duration snapshot."""
        return self.metrics_snapshot()

    @property
    def postactivation_frames_dropped(self) -> int:
        """Return the number of handshake frames dropped at the queue bound."""
        with self._lifecycle_lock:
            return self._postactivation_frames_dropped

    def status_snapshot(self) -> RuntimeStatusSnapshot:
        """Return an immutable runtime status snapshot."""
        with self._lifecycle_lock:
            mode = self._mode
            generation = self._generation
            active = self._active_handler is not None
            closed = self._closed
            provider_task = self._provider_task
            inactivity_task = self._inactivity_task
            connected = self._connection_ready
            interaction_busy = self._interaction_busy
            playback_active = self._playback_is_active_locked(self._clock())
            shutdown_error = self._shutdown_error
            postactivation_frames_dropped = self._postactivation_frames_dropped
        metrics = self.metrics_snapshot()
        wake_metrics = self._wake_detector.metrics_snapshot() if self._wake_detector is not None else None
        if self._acoustic_wake_detector is not None:
            wake_metrics = self._acoustic_wake_detector.metrics_snapshot()
        context_metrics = self._context_processor.metrics if self._context_processor is not None else None
        with self._metrics_lock:
            local_context_chars = self._local_context_chars
            local_commands_executed = self._local_commands_executed
            local_command_failures = self._local_command_failures
            local_command_escalations = self._local_command_escalations
            local_command_last_outcome = self._local_command_last_outcome
            local_command_last_router_reason = self._local_command_last_router_reason
            local_command_router_rejections = self._local_command_router_rejections
            local_command_followup_timeouts = self._local_command_followup_timeouts
            local_processing_frames_dropped = self._local_processing_frames_dropped
            local_command_latency_seconds_total = self._local_command_latency_seconds_total
            local_command_latency_seconds_max = self._local_command_latency_seconds_max
            context_activation_latency_seconds_total = self._context_activation_latency_seconds_total
            context_activation_latency_seconds_max = self._context_activation_latency_seconds_max
        return RuntimeStatusSnapshot(
            mode=mode,
            generation=generation,
            active=active,
            closed=closed,
            provider_task_running=provider_task is not None and not provider_task.done(),
            inactivity_monitor_running=inactivity_task is not None and not inactivity_task.done(),
            connected=connected,
            interaction_busy=interaction_busy,
            playback_active=playback_active,
            shutdown_error=shutdown_error,
            ambient_audio_seconds_buffered=metrics.ambient_audio_seconds_buffered,
            ambient_audio_bytes_sent_to_cloud=metrics.ambient_audio_bytes_sent_to_cloud,
            cloud_audio_bytes_sent=metrics.cloud_audio_bytes_sent,
            cloud_video_frames_sent=metrics.cloud_video_frames_sent,
            gemini_session_duration=metrics.gemini_session_duration,
            postactivation_frames_dropped=postactivation_frames_dropped,
            local_context_chars=local_context_chars,
            transcript_segments_accepted=(
                context_metrics.transcript_segments_accepted if context_metrics is not None else 0
            ),
            acoustic_wake_enabled=self._acoustic_wake_detector is not None,
            wake_detections=wake_metrics.detection_count if wake_metrics is not None else 0,
            wake_cooldown_suppressed=wake_metrics.cooldown_suppressed_count if wake_metrics is not None else 0,
            wake_final_update_count=wake_metrics.final_update_count if wake_metrics is not None else 0,
            local_commands_executed=local_commands_executed,
            local_command_failures=local_command_failures,
            local_command_escalations=local_command_escalations,
            local_command_last_outcome=local_command_last_outcome,
            local_command_last_router_reason=local_command_last_router_reason,
            local_command_router_rejections=local_command_router_rejections,
            local_command_followup_timeouts=local_command_followup_timeouts,
            local_processing_frames_dropped=local_processing_frames_dropped,
            local_command_latency_seconds_total=local_command_latency_seconds_total,
            local_command_latency_seconds_max=local_command_latency_seconds_max,
            context_activation_latency_seconds_total=context_activation_latency_seconds_total,
            context_activation_latency_seconds_max=context_activation_latency_seconds_max,
            local_context_strategy=(
                self._context_processor.config.strategy.value if self._context_processor is not None else None
            ),
            local_audio_seconds_observed=context_metrics.audio_seconds_observed
            if context_metrics is not None
            else 0.0,
            local_speech_frames=context_metrics.speech_frames if context_metrics is not None else 0,
            local_stt_requests=(
                context_metrics.continuous_stt_requests + context_metrics.activation_stt_requests
                if context_metrics is not None
                else 0
            ),
            local_stt_completed=(
                context_metrics.continuous_stt_completed + context_metrics.activation_stt_completed
                if context_metrics is not None
                else 0
            ),
            local_stt_failures=(
                context_metrics.continuous_stt_failures + context_metrics.activation_stt_failures
                if context_metrics is not None
                else 0
            ),
            local_stt_timeouts=(
                context_metrics.continuous_stt_timeouts + context_metrics.activation_stt_timeouts
                if context_metrics is not None
                else 0
            ),
            local_stt_dropped=context_metrics.continuous_stt_dropped if context_metrics is not None else 0,
            local_stt_latency_seconds_total=(
                context_metrics.continuous_stt_latency_seconds_total
                + context_metrics.activation_stt_latency_seconds_total
                if context_metrics is not None
                else 0.0
            ),
            local_stt_latency_seconds_max=(
                max(
                    context_metrics.continuous_stt_latency_seconds_max,
                    context_metrics.activation_stt_latency_seconds_max,
                )
                if context_metrics is not None
                else 0.0
            ),
            local_stt_inflight_peak=context_metrics.stt_inflight_peak if context_metrics is not None else 0,
            local_stt_pending_at_activation=(
                context_metrics.continuous_stt_pending_at_activation if context_metrics is not None else 0
            ),
        )

    def metrics_snapshot(self) -> TrafficMetricsSnapshot:
        """Return an immutable traffic and duration snapshot."""
        with self._metrics_lock:
            return TrafficMetricsSnapshot(
                ambient_audio_seconds_buffered=self._ambient_buffer.seconds_buffered,
                ambient_audio_bytes_sent_to_cloud=self._ambient_audio_bytes_sent_to_cloud,
                cloud_audio_bytes_sent=self._cloud_audio_bytes_sent,
                cloud_video_frames_sent=self._cloud_video_frames_sent,
                gemini_session_duration=self._gemini_session_duration,
            )

    def set_state_observer(self, observer: StateObserver | None) -> None:
        """Attach an observer for metadata-only runtime transitions."""
        self._state_observer = observer

    def set_capture_barrier_provider(self, provider: CaptureBarrierProvider | None) -> None:
        """Set a capture-sequence source used to fence queued pre-activation frames."""
        self._capture_barrier_provider = provider

    def set_playback_clearer(self, clearer: Callable[[], None] | None) -> None:
        """Set the LocalStream-owned playback flush callback."""
        self._playback_clearer = clearer

    def set_playback_active(self, active: bool) -> None:
        """Set or clear an explicit playback boundary."""
        with self._lifecycle_lock:
            if active:
                self._playback_until = math.inf
            else:
                self._playback_until = 0.0

    def note_playback_queued(self, duration_s: float) -> None:
        """Extend the estimated playback boundary by one queued audio duration."""
        if duration_s <= 0 or not math.isfinite(duration_s):
            return
        with self._lifecycle_lock:
            if self._mode is not RuntimeMode.CLOUD_CONVERSATION or self._active_handler is None:
                return
            now = self._clock()
            self._playback_until = max(self._playback_until, now) + duration_s

    def _playback_is_active_locked(self, now: float) -> bool:
        return self._mode is RuntimeMode.CLOUD_CONVERSATION and self._playback_until > now

    def clear_provider_output(self) -> None:
        """Drain only the active provider output queue; playback remains LocalStream-owned."""
        with self._lifecycle_lock:
            handler = self._active_handler
        if handler is None:
            return
        self._drain_handler_output(handler)

    @staticmethod
    def _drain_handler_output(handler: ConversationHandler) -> None:
        """Drain one provider queue without replacing it."""
        while not handler.output_queue.empty():
            try:
                handler.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _is_connected(self) -> bool:
        """Return whether the selected provider reports a live connection."""
        with self._lifecycle_lock:
            return self._active_handler is not None and self._connection_ready

    def seconds_since_activity(self) -> float:
        """Return seconds since the active cloud handler's last activity."""
        with self._lifecycle_lock:
            last_activity = self._last_conversation_activity
        return max(0.0, self._clock() - last_activity)

    def set_activity_observer(self, observer: ActivityObserver | None) -> None:
        """Attach an observer for active provider conversation activity."""
        self._activity_observer = observer

    def set_transcript_observer(self, observer: TranscriptObserver | None) -> None:
        """Attach an observer for active provider transcript updates."""
        self._transcript_observer = observer

    def record_provider_traffic(self, kind: TrafficKind, byte_count: int) -> None:
        """Record media only after a provider confirms a successful send."""
        if byte_count <= 0:
            return
        if kind not in ("audio", "video"):
            raise ValueError(f"Unsupported provider traffic kind: {kind!r}")
        with self._metrics_lock:
            if kind == "audio":
                self._cloud_audio_bytes_sent += byte_count
            else:
                self._cloud_video_frames_sent += 1

    def _assert_owner_loop(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lifecycle_lock:
            owner_loop = self._owner_loop
            if owner_loop is None:
                self._owner_loop = loop
            elif owner_loop is not loop:
                raise RuntimeError("HybridRuntime async operation called from a foreign event loop")

    def _log_cloud_session_summary(self, reason: str) -> None:
        """Log what one cloud session forwarded, to diagnose sessions that never hear the user."""
        with self._metrics_lock:
            audio_bytes = self._cloud_audio_bytes_sent - self._session_audio_bytes_start
            video_frames = self._cloud_video_frames_sent - self._session_video_frames_start
        with self._lifecycle_lock:
            ack_task = self._acoustic_ack_task
            logger.info(
                "Cloud session summary reason=%s audio_kb_sent=%.0f video_frames=%d queued_frames=%d "
                "still_queued=%d ack=%s ack_task_running=%s connection_ready=%s",
                reason,
                audio_bytes / 1024,
                video_frames,
                self._session_queued_frames,
                len(self._postactivation_frames),
                self._session_ack_state,
                ack_task is not None and not ack_task.done(),
                self._connection_ready,
            )

    def _transition(self, new_mode: RuntimeMode, reason: str) -> None:
        with self._lifecycle_lock:
            old_mode = self._mode
            if new_mode not in self._VALID_TRANSITIONS[old_mode]:
                raise ValueError(f"Invalid runtime transition: {old_mode.value} -> {new_mode.value}")
            self._mode = new_mode
            self._acoustic_epoch += 1
            generation = self._generation
            self._output_wakeup_event.set()
        logger.info(
            "Runtime transition old=%s new=%s reason=%s generation=%d",
            old_mode.value,
            new_mode.value,
            reason,
            generation,
        )
        self._notify_state_observer(new_mode, reason, generation)

    def _notify_state_observer(self, state: RuntimeMode, reason: str, generation: int) -> None:
        observer = self._state_observer
        if observer is not None:
            try:
                observer(state, reason, generation)
            except Exception as error:
                logger.warning("Runtime state observer failed: %s", type(error).__name__)

    async def activate(
        self,
        reason: str = "keyboard",
        *,
        addressed_text: str = "",
        wake_sequence: int | None = None,
    ) -> bool:
        """Acknowledge locally, then create and start one cloud generation."""
        self._assert_owner_loop()
        addressed_text = addressed_text.strip()[: self._MAX_ADDRESSED_TEXT_CHARS]
        with self._lifecycle_lock:
            closed = self._closed
            mode = self._mode
            activation_task = self._activation_task
        if closed or mode in (RuntimeMode.CLOUD_CONVERSATION, RuntimeMode.STOPPING):
            return mode is RuntimeMode.CLOUD_CONVERSATION and not closed

        if activation_task is None or activation_task.done():
            activation_task = asyncio.create_task(
                self._run_activation(
                    reason,
                    addressed_text=addressed_text,
                    wake_sequence=wake_sequence,
                ),
                name="hybrid-activation",
            )
            with self._lifecycle_lock:
                self._activation_task = activation_task
        return await activation_task

    def observe_local_transcript(self, segment: TranscriptSegment) -> None:
        """Route one final local transcript through wake or its bounded follow-up."""
        # The current local STT boundary emits complete final segments. Waiting
        # for one preserves the addressed suffix for local commands; acting on
        # a partial "Reachy" would start cloud setup before the command arrives.
        if not segment.final:
            return
        detector = self._wake_detector
        with self._lifecycle_lock:
            mode = self._mode
            followup = self._wake_followup_future
            if (
                followup is not None
                and not followup.done()
                and not self._closed
                and mode is not RuntimeMode.CLOUD_CONVERSATION
                and (
                    self._wake_followup_after_sequence is None or segment.sequence > self._wake_followup_after_sequence
                )
            ):
                followup.set_result(
                    TranscriptSegment(
                        text=segment.text[: self._MAX_ADDRESSED_TEXT_CHARS],
                        started_at=segment.started_at,
                        ended_at=segment.ended_at,
                        sequence=segment.sequence,
                        final=segment.final,
                    )
                )
                return
            if detector is None or self._closed or mode is not RuntimeMode.LOCAL_AMBIENT:
                return
        try:
            detection = detector.process_transcript(
                segment.text,
                is_final=segment.final,
                speech_started_at=segment.started_at,
                observed_at=segment.ended_at,
            )
        except (TypeError, ValueError) as error:
            logger.warning("Local wake detection failed: %s", type(error).__name__)
            return
        if detection is None:
            return
        detector.consume_detection()
        followup_waiter: asyncio.Future[TranscriptSegment] | None = None
        if not detection.remaining_text:
            try:
                followup_waiter = asyncio.get_running_loop().create_future()
            except RuntimeError:
                logger.warning("Local wake follow-up skipped: runtime loop unavailable")
                return
            with self._lifecycle_lock:
                if self._closed or self._mode is not RuntimeMode.LOCAL_AMBIENT:
                    return
                self._wake_followup_future = followup_waiter
                self._wake_followup_after_sequence = segment.sequence
        try:
            task = asyncio.create_task(
                self.activate(
                    "wake_word",
                    addressed_text=detection.remaining_text,
                    wake_sequence=segment.sequence,
                ),
                name="hybrid-wake-activation",
            )
        except RuntimeError:
            if followup_waiter is not None:
                self._clear_wake_followup_waiter(followup_waiter)
            logger.warning("Local wake activation skipped: runtime loop unavailable")
            return
        self._local_event_tasks.add(task)
        task.add_done_callback(self._local_event_tasks.discard)

    def _clear_wake_followup_waiter(self, waiter: asyncio.Future[TranscriptSegment] | None = None) -> None:
        """Release the pending one-shot wake follow-up without retaining text."""
        with self._lifecycle_lock:
            current = self._wake_followup_future
            if current is None or (waiter is not None and current is not waiter):
                return
            self._wake_followup_future = None
            self._wake_followup_after_sequence = None
            if not current.done():
                current.cancel()

    async def _wait_for_wake_followup(self) -> tuple[TranscriptSegment | None, int | None]:
        """Wait briefly for one final transcript after a wake-only segment."""
        with self._lifecycle_lock:
            waiter = self._wake_followup_future
            wake_sequence = self._wake_followup_after_sequence
        if waiter is None:
            return None, wake_sequence
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout=self._wake_followup_timeout_s), wake_sequence
        except asyncio.TimeoutError:
            return None, wake_sequence
        finally:
            self._clear_wake_followup_waiter(waiter)

    async def start_up(self) -> None:
        """Keep the local ambient runtime alive without opening a provider."""
        self._assert_owner_loop()
        with self._lifecycle_lock:
            closed = self._closed
        if closed:
            return
        self._local_start_event.clear()
        try:
            await self._local_start_event.wait()
        except asyncio.CancelledError:
            raise

    async def shutdown(self) -> None:
        """Close local processing and any active cloud generation."""
        await self.close()

    async def _run_activation(
        self,
        reason: str,
        *,
        addressed_text: str,
        wake_sequence: int | None,
    ) -> bool:
        handler: ConversationHandler | None = None
        generation = 0
        try:
            async with self._state_lock:
                with self._lifecycle_lock:
                    if self._closed or self._mode is not RuntimeMode.LOCAL_AMBIENT:
                        return self._mode is RuntimeMode.CLOUD_CONVERSATION and not self._closed
                    self._generation += 1
                    generation = self._generation
                    barrier_provider = self._capture_barrier_provider
                    self._activation_capture_barrier = barrier_provider() if barrier_provider is not None else None
                    self._clear_postactivation_queue()
                    self._postactivation_speech_observed = False
                    self._pending_acoustic_ack_generation = (
                        generation if reason == "acoustic_wake" and not addressed_text else None
                    )
                self._transition(RuntimeMode.LOCAL_COMMAND, reason)

            await self._invoke_acknowledgement(reason)
            with self._lifecycle_lock:
                closed = self._closed
            if closed:
                raise asyncio.CancelledError

            if reason == "wake_word" and not addressed_text:
                followup_segment, waited_wake_sequence = await self._wait_for_wake_followup()
                if waited_wake_sequence is not None:
                    wake_sequence = waited_wake_sequence
                if followup_segment is not None:
                    addressed_text = followup_segment.text[: self._MAX_ADDRESSED_TEXT_CHARS]
                else:
                    self._record_local_command_outcome(LocalCommandOutcome.FOLLOWUP_TIMEOUT)

            if addressed_text and await self._try_local_command(addressed_text):
                async with self._state_lock:
                    with self._lifecycle_lock:
                        local_action_mode: RuntimeMode = self.mode
                        still_command = (
                            local_action_mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
                        )
                    if still_command:
                        self._clear_postactivation_queue()
                        self._transition(RuntimeMode.LOCAL_AMBIENT, "local_command_completed")
                return True

            initial_context = await self._context_for_activation(before_sequence=wake_sequence)

            produced_handler = self._handler_factory()
            handler = await produced_handler if inspect.isawaitable(produced_handler) else produced_handler
            with self._lifecycle_lock:
                closed = self._closed
            if closed:
                raise asyncio.CancelledError

            async with self._state_lock:
                with self._lifecycle_lock:
                    activation_mode: RuntimeMode = self.mode
                    if (
                        self._closed
                        or activation_mode is not RuntimeMode.LOCAL_COMMAND
                        or generation != self._generation
                    ):
                        raise asyncio.CancelledError
                self._configure_handler(handler, generation)
                handler.set_initial_context(initial_context)
                with self._metrics_lock:
                    self._session_audio_bytes_start = self._cloud_audio_bytes_sent
                    self._session_video_frames_start = self._cloud_video_frames_sent
                with self._lifecycle_lock:
                    self._session_queued_frames = 0
                    self._session_ack_state = "not_needed"
                    self._active_handler = handler
                    self._connection_ready = False
                    self._connected_since = None
                    self._last_conversation_activity = self._clock()
                    self._user_speaking = False
                    self._interaction_busy = False
                    self._playback_until = 0.0
                    self._pending_activation_text = (generation, addressed_text) if addressed_text else None
                self._transition(RuntimeMode.CLOUD_CONVERSATION, "activation_ready")
                provider_task = asyncio.create_task(
                    self._run_provider(handler, generation), name=f"hybrid-provider-{generation}"
                )
                with self._lifecycle_lock:
                    self._provider_task = provider_task
                    if self._inactivity_timeout_s > 0:
                        self._inactivity_task = asyncio.create_task(
                            self._monitor_inactivity(handler, generation),
                            name=f"hybrid-inactivity-{generation}",
                        )

            await asyncio.sleep(0)
            return True
        except asyncio.CancelledError:
            with self._lifecycle_lock:
                active_handler = self._active_handler
            if handler is not None and handler is active_handler:
                await self._deactivate_cloud("activation_cancelled")
            elif handler is not None:
                await self._shutdown_handler_strict(handler, "activation_cancelled")
            with self._lifecycle_lock:
                still_command = self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
            if generation and still_command:
                with self._lifecycle_lock:
                    self._pending_acoustic_ack_generation = None
                    self._postactivation_speech_observed = False
                self._clear_postactivation_queue()
                self._transition(RuntimeMode.LOCAL_AMBIENT, "activation_cancelled")
            raise
        except Exception as error:
            logger.warning("Hybrid cloud activation failed: %s", type(error).__name__)
            with self._lifecycle_lock:
                active_handler = self._active_handler
            if handler is not None and handler is not active_handler:
                await self._contain_uncommitted_handler(handler, "activation_failed", generation)
            with self._lifecycle_lock:
                still_command = self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
            if generation and still_command:
                with self._lifecycle_lock:
                    self._pending_acoustic_ack_generation = None
                    self._postactivation_speech_observed = False
                self._clear_postactivation_queue()
                self._transition(RuntimeMode.LOCAL_AMBIENT, "provider_setup_failed")
            return False
        finally:
            with self._lifecycle_lock:
                activation_is_current = self._activation_task is asyncio.current_task()
            if activation_is_current:
                self._clear_wake_followup_waiter()
            with self._lifecycle_lock:
                if self._activation_task is asyncio.current_task():
                    self._activation_task = None

    async def _invoke_acknowledgement(self, reason: str) -> None:
        callback = self._acknowledgement_callback
        if callback is None:
            return
        try:
            result = callback(reason)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local activation acknowledgement failed: %s", type(error).__name__)

    async def _try_local_command(self, addressed_text: str) -> bool:
        """Execute one exact local command, returning whether cloud can stay closed."""
        router = self._intent_router
        executor = self._action_executor
        if router is None or executor is None:
            with self._metrics_lock:
                self._local_command_escalations += 1
            return False
        decision = router.route_wake_stripped(addressed_text)
        if decision.should_escalate:
            with self._metrics_lock:
                self._local_command_escalations += 1
            self._record_local_command_outcome(LocalCommandOutcome.ROUTER_REJECTED, decision.reason)
            return False

        started_at = self._clock()
        result = await executor.execute(decision)
        duration = max(0.0, self._clock() - started_at)
        executed = result.status is LocalActionStatus.EXECUTED
        with self._metrics_lock:
            self._local_command_latency_seconds_total += duration
            self._local_command_latency_seconds_max = max(self._local_command_latency_seconds_max, duration)
            if executed:
                self._local_commands_executed += 1
            else:
                self._local_command_failures += 1
                self._local_command_escalations += 1
        self._record_local_command_outcome(
            LocalCommandOutcome.LOCAL_EXECUTED if executed else LocalCommandOutcome.LOCAL_EXECUTION_FAILED
        )
        return executed

    def _record_local_command_outcome(
        self,
        outcome: LocalCommandOutcome,
        router_reason: IntentDecisionReason | None = None,
    ) -> None:
        """Record local routing metadata without retaining addressed text."""
        with self._metrics_lock:
            self._local_command_last_outcome = outcome
            self._local_command_last_router_reason = router_reason
            if outcome is LocalCommandOutcome.ROUTER_REJECTED:
                self._local_command_router_rejections += 1
            elif outcome is LocalCommandOutcome.FOLLOWUP_TIMEOUT:
                self._local_command_followup_timeouts += 1

    async def _context_for_activation(self, *, before_sequence: int | None = None) -> str:
        """Build bounded text context while keeping the ambient PCM snapshot local."""
        processor = self._context_processor
        if processor is None:
            return ""
        snapshot = self._ambient_buffer.snapshot()
        started_at = self._clock()
        try:
            context = await processor.context_for_activation(snapshot, before_sequence=before_sequence)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local activation context failed: %s", type(error).__name__)
            return ""
        finally:
            duration = max(0.0, self._clock() - started_at)
            with self._metrics_lock:
                self._context_activation_latency_seconds_total += duration
                self._context_activation_latency_seconds_max = max(
                    self._context_activation_latency_seconds_max,
                    duration,
                )
        with self._metrics_lock:
            self._local_context_chars = len(context.text)
        return context.text

    def _configure_handler(self, handler: ConversationHandler, generation: int) -> None:
        playback_clearer = self._playback_clearer
        if playback_clearer is not None:
            handler._clear_queue = lambda: self._clear_handler_playback(handler, playback_clearer)
        handler.set_activity_observer(lambda reason: self._on_handler_activity(handler, generation, reason))
        handler.set_transcript_observer(
            lambda role, text, final: self._on_handler_transcript(handler, generation, role, text, final)
        )
        handler.set_traffic_observer(
            lambda kind, byte_count: self._on_handler_traffic(handler, generation, kind, byte_count)
        )
        handler.set_connection_observer(lambda connected: self._on_handler_connection(handler, generation, connected))
        busy_setter = getattr(handler, "set_interaction_busy_observer", None)
        if callable(busy_setter):
            busy_setter(lambda busy: self._on_handler_interaction_busy(handler, generation, busy))
        handler.set_startup_greeting_enabled(False)

    def _clear_handler_playback(self, handler: ConversationHandler, clearer: Callable[[], None]) -> None:
        """Flush provider output and delegate robot playback flushing to LocalStream."""
        self._drain_handler_output(handler)
        try:
            clearer()
        except Exception as error:
            logger.warning("Local playback clearer failed: %s", type(error).__name__)

    def _is_current_handler(self, handler: ConversationHandler, generation: int) -> bool:
        with self._lifecycle_lock:
            return (
                self._mode is RuntimeMode.CLOUD_CONVERSATION
                and self._active_handler is handler
                and self._generation == generation
            )

    def _on_handler_activity(self, handler: ConversationHandler, generation: int, reason: str) -> None:
        if not self._is_current_handler(handler, generation):
            return
        with self._lifecycle_lock:
            if reason in self._SPEECH_STARTED_REASONS:
                self._user_speaking = True
            elif reason in self._SPEECH_STOPPED_REASONS:
                self._user_speaking = False
            self._last_conversation_activity = self._clock()
        observer = self._activity_observer
        if observer is not None:
            try:
                observer(reason)
            except Exception as error:
                logger.warning("Runtime activity observer failed: %s", type(error).__name__)

    def _on_handler_transcript(
        self,
        handler: ConversationHandler,
        generation: int,
        role: str,
        text: str,
        final: bool,
    ) -> None:
        if not self._is_current_handler(handler, generation):
            return
        observer = self._transcript_observer
        if observer is not None:
            try:
                observer(role, text, final)
            except Exception as error:
                logger.warning("Runtime transcript observer failed: %s", type(error).__name__)

    def _on_handler_traffic(
        self,
        handler: ConversationHandler,
        generation: int,
        kind: TrafficKind,
        byte_count: int,
    ) -> None:
        if self._is_current_handler(handler, generation):
            self.record_provider_traffic(kind, byte_count)

    def _on_handler_interaction_busy(self, handler: ConversationHandler, generation: int, busy: bool) -> None:
        """Track provider busy state without changing the inactivity clock."""
        if not self._is_current_handler(handler, generation):
            return
        with self._lifecycle_lock:
            self._interaction_busy = bool(busy)

    def _on_handler_connection(self, handler: ConversationHandler, generation: int, connected: bool) -> None:
        if not self._is_current_handler(handler, generation):
            return
        now = self._clock()
        if connected:
            with self._lifecycle_lock:
                if self._connected_since is None:
                    self._connected_since = now
                self._connection_ready = True
                self._last_conversation_activity = now
                self._interaction_busy = False
                should_flush = bool(self._postactivation_frames)
                pending_activation_text = self._pending_activation_text
                pending_acoustic_ack = (
                    self._pending_acoustic_ack_generation == generation and self._acoustic_ack_task is None
                )
                if pending_activation_text is not None and pending_activation_text[0] == generation:
                    self._pending_activation_text = None
                    pending_acoustic_ack = False
                else:
                    pending_activation_text = None
            if pending_activation_text is not None:
                task = asyncio.create_task(
                    self._send_activation_text(handler, generation, pending_activation_text[1]),
                    name=f"hybrid-activation-text-{generation}",
                )
                with self._lifecycle_lock:
                    self._activation_text_task = task
            elif pending_acoustic_ack:
                task = asyncio.create_task(
                    self._send_acoustic_ack(handler, generation),
                    name=f"hybrid-acoustic-ack-{generation}",
                )
                with self._lifecycle_lock:
                    self._acoustic_ack_task = task
            elif should_flush:
                self._schedule_postactivation_flush(handler, generation)
            return
        with self._lifecycle_lock:
            self._connection_ready = False
            self._interaction_busy = False
            self._user_speaking = False
            connected_since = self._connected_since
            self._connected_since = None
        if connected_since is not None:
            self._record_session_segment(now - connected_since)

    async def _send_activation_text(self, handler: ConversationHandler, generation: int, text: str) -> None:
        """Submit the locally transcribed addressed turn after provider readiness."""
        current_task = asyncio.current_task()
        try:
            async with self._provider_call_lock:
                if not self._is_current_handler(handler, generation):
                    return
                try:
                    await handler.say(text)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("Hybrid activation text send failed: %s", type(error).__name__)
                if self._is_current_handler(handler, generation):
                    await self._flush_postactivation_locked(handler, generation)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lifecycle_lock:
                if self._activation_text_task is current_task:
                    self._activation_text_task = None

    async def _send_acoustic_ack(self, handler: ConversationHandler, generation: int) -> None:
        """Acknowledge a bare acoustic wake unless speech already resumed."""
        current_task = asyncio.current_task()
        try:
            async with self._provider_call_lock:
                with self._lifecycle_lock:
                    connection_ready = self._connection_ready
                if not self._is_current_handler(handler, generation) or not connection_ready:
                    with self._lifecycle_lock:
                        self._session_ack_state = "skipped_not_ready"
                    return
                with self._lifecycle_lock:
                    speech_resumed = self._user_speaking or self._postactivation_speech_observed
                    self._session_ack_state = "skipped_speech_resumed" if speech_resumed else "sending"
                if not speech_resumed:
                    try:
                        await handler.say(self._ACOUSTIC_WAKE_ACK_TEXT)
                        with self._lifecycle_lock:
                            self._session_ack_state = "sent"
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        with self._lifecycle_lock:
                            self._session_ack_state = "failed"
                        logger.warning("Acoustic wake acknowledgement failed: %s", type(error).__name__)
                if self._is_current_handler(handler, generation):
                    await self._flush_postactivation_locked(handler, generation)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lifecycle_lock:
                if self._acoustic_ack_task is current_task:
                    self._acoustic_ack_task = None
                if self._pending_acoustic_ack_generation == generation:
                    self._pending_acoustic_ack_generation = None

    def _schedule_postactivation_flush(self, handler: ConversationHandler, generation: int) -> None:
        with self._lifecycle_lock:
            task = self._postactivation_flush_task
            if task is not None and not task.done():
                return
            task = asyncio.create_task(
                self._flush_postactivation(handler, generation),
                name=f"hybrid-postactivation-{generation}",
            )
            self._postactivation_flush_task = task

    async def _flush_postactivation(self, handler: ConversationHandler, generation: int) -> None:
        current_task = asyncio.current_task()
        try:
            async with self._provider_call_lock:
                await self._flush_postactivation_locked(handler, generation)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lifecycle_lock:
                if self._postactivation_flush_task is current_task:
                    self._postactivation_flush_task = None

    async def _flush_postactivation_locked(self, handler: ConversationHandler, generation: int) -> None:
        while True:
            with self._lifecycle_lock:
                if not self._is_current_handler(handler, generation) or not self._connection_ready:
                    return
                if not self._postactivation_frames:
                    return
                _, queued_frame = self._postactivation_frames.popleft()
                self._postactivation_seconds -= self._frame_duration(queued_frame)
            try:
                await handler.receive(queued_frame)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Hybrid queued provider receive failed: %s", type(error).__name__)
                self._clear_postactivation_queue()
                await self._deactivate_cloud("provider_receive_failed")
                return

    def _observe_acoustic_ack_frame(self, frame: AudioFrame, generation: int) -> None:
        """Record local speech evidence while a bare acoustic acknowledgment is pending."""
        with self._lifecycle_lock:
            if self._pending_acoustic_ack_generation != generation or self._closed:
                return
        try:
            is_speech = self._acoustic_ack_vad.is_speech(frame)
        except (TypeError, ValueError) as error:
            logger.warning("Acoustic acknowledgment VAD rejected a frame: %s", type(error).__name__)
            return
        if is_speech:
            with self._lifecycle_lock:
                if self._pending_acoustic_ack_generation == generation and not self._closed:
                    self._postactivation_speech_observed = True

    def _enqueue_postactivation(self, frame: AudioFrame, capture_sequence: int | None) -> None:
        duration = self._frame_duration(frame)
        if duration <= 0:
            return
        with self._lifecycle_lock:
            self._postactivation_frames.append((capture_sequence, frame))
            self._postactivation_seconds += duration
            self._session_queued_frames += 1
            while self._postactivation_seconds > self._POSTACTIVATION_BUFFER_SECONDS:
                _, oldest = self._postactivation_frames.popleft()
                self._postactivation_seconds -= self._frame_duration(oldest)
                self._postactivation_frames_dropped += 1
                logger.warning(
                    "Postactivation audio queue full; dropped oldest frame count=%d",
                    self._postactivation_frames_dropped,
                )

    def _clear_postactivation_queue(self) -> None:
        with self._lifecycle_lock:
            self._postactivation_frames.clear()
            self._postactivation_seconds = 0.0

    @staticmethod
    def _frame_duration(frame: AudioFrame) -> float:
        sample_rate, audio = frame
        if sample_rate <= 0 or audio.size == 0:
            return 0.0
        if audio.ndim == 1:
            sample_count = int(audio.shape[0])
        elif audio.ndim == 2:
            if audio.shape[1] == 1:
                sample_count = int(audio.shape[0])
            elif audio.shape[0] == 1:
                sample_count = int(audio.shape[0])
            else:
                sample_count = max(int(dimension) for dimension in audio.shape)
        else:
            return 0.0
        return sample_count / sample_rate

    async def _run_provider(self, handler: ConversationHandler, generation: int) -> None:
        try:
            await handler.start_up()
        except asyncio.CancelledError:
            with self._lifecycle_lock:
                closed = self._closed
            if not closed and self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_cancelled")
            raise
        except Exception as error:
            logger.warning("Hybrid provider task failed: %s", type(error).__name__)
            if self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_failed")
        else:
            if self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_stopped")

    async def handle_frame(
        self,
        frame: AudioFrame,
        *,
        capture_sequence: int | None = None,
        wait_for_local_processing: bool = True,
    ) -> None:
        """Route one captured frame locally or to the active cloud generation."""
        self._assert_owner_loop()
        copied_frame = self._copy_frame(frame)
        local_frame = False
        postactivation_frame = False
        observe_postactivation_locally = False
        observe_acoustic_ack_speech = False
        handler: ConversationHandler | None = None
        generation = 0
        async with self._state_lock:
            with self._lifecycle_lock:
                active_handler = self._active_handler
                mode = self._mode
                barrier = self._activation_capture_barrier
                generation = self._generation
                if self._closed or mode is RuntimeMode.STOPPING:
                    return
            if mode is RuntimeMode.CLOUD_CONVERSATION and active_handler is not None:
                if barrier is not None and capture_sequence is not None and capture_sequence <= barrier:
                    local_frame = True
                    try:
                        self._ambient_buffer.append(copied_frame)
                    except (TypeError, ValueError) as error:
                        logger.warning("Ambient frame rejected: %s", type(error).__name__)
                else:
                    handler = active_handler
                    observe_acoustic_ack_speech = self._pending_acoustic_ack_generation == generation
            elif mode is RuntimeMode.LOCAL_COMMAND:
                with self._lifecycle_lock:
                    observe_postactivation_locally = (
                        self._wake_followup_future is not None and not self._wake_followup_future.done()
                    )
                    observe_acoustic_ack_speech = self._pending_acoustic_ack_generation == generation
                if barrier is not None and capture_sequence is not None and capture_sequence > barrier:
                    postactivation_frame = True
                else:
                    local_frame = True
                    try:
                        self._ambient_buffer.append(copied_frame)
                    except (TypeError, ValueError) as error:
                        logger.warning("Ambient frame rejected: %s", type(error).__name__)
            else:
                local_frame = True
                try:
                    self._ambient_buffer.append(copied_frame)
                except (TypeError, ValueError) as error:
                    logger.warning("Ambient frame rejected: %s", type(error).__name__)

        if local_frame:
            await self._enqueue_local_frame(copied_frame, wait_for_completion=wait_for_local_processing)
            return
        if postactivation_frame:
            self._enqueue_postactivation(copied_frame, capture_sequence)
            if observe_acoustic_ack_speech:
                self._observe_acoustic_ack_frame(copied_frame, generation)
            if observe_postactivation_locally:
                await self._enqueue_local_frame(copied_frame, wait_for_completion=wait_for_local_processing)
            return
        if handler is None:
            return

        with self._lifecycle_lock:
            activation_text_task = self._activation_text_task
            acoustic_ack_task = self._acoustic_ack_task
            queue_behind_provider_turn = (
                (
                    (activation_text_task is not None and not activation_text_task.done())
                    or (acoustic_ack_task is not None and not acoustic_ack_task.done())
                )
                and self._active_handler is handler
                and self._generation == generation
            )
        if observe_acoustic_ack_speech:
            self._observe_acoustic_ack_frame(copied_frame, generation)

        if queue_behind_provider_turn:
            self._enqueue_postactivation(copied_frame, capture_sequence)
            return

        async with self._provider_call_lock:
            if not self._is_current_handler(handler, generation):
                return
            with self._lifecycle_lock:
                connection_ready = self._connection_ready
            if not connection_ready:
                self._enqueue_postactivation(copied_frame, capture_sequence)
                return
            await self._flush_postactivation_locked(handler, generation)
            if not self._is_current_handler(handler, generation):
                return
            try:
                await handler.receive(copied_frame)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Hybrid provider receive failed: %s", type(error).__name__)
                await self._deactivate_cloud("provider_receive_failed")

    async def _enqueue_local_frame(self, frame: AudioFrame, *, wait_for_completion: bool) -> None:
        """Queue one copied frame for ordered local processing without blocking capture."""
        with self._lifecycle_lock:
            if self._closed or self._mode is RuntimeMode.STOPPING:
                return
            queue = self._local_processing_queue
            task = self._local_processing_task
            if queue is None:
                queue = asyncio.Queue(maxsize=self._LOCAL_PROCESSING_QUEUE_SIZE)
                self._local_processing_queue = queue
            if task is None or task.done():
                self._local_processing_task = asyncio.create_task(
                    self._run_local_processing(queue), name="hybrid-local-processing"
                )
        completed = asyncio.get_running_loop().create_future()
        try:
            queue.put_nowait((frame, completed, self._acoustic_epoch))
        except asyncio.QueueFull:
            # The wake detector keeps its stream state across a dropped frame: a short gap barely changes
            # its score, while a reset would blind it for ~1.3 s and miss a wake word spoken meanwhile.
            completed.cancel()
            with self._metrics_lock:
                self._local_processing_frames_dropped += 1
                dropped_frames = self._local_processing_frames_dropped
                burst_start = not self._local_processing_drop_burst
                self._local_processing_drop_burst = True
            if burst_start:
                logger.warning("Local processing queue full; dropped_frames=%d", dropped_frames)
            return
        with self._metrics_lock:
            self._local_processing_drop_burst = False
        if wait_for_completion:
            await asyncio.shield(completed)

    async def _run_local_processing(self, queue: asyncio.Queue[LocalProcessingItem]) -> None:
        """Run local observation sequentially so VAD/STT retains capture order."""
        while True:
            frame, completed, acoustic_epoch = await queue.get()
            try:
                try:
                    await self._observe_acoustic_frame(frame, acoustic_epoch)
                    result = self._local_processor.observe_frame(frame)
                    if inspect.isawaitable(result):
                        await result
                    if not completed.done():
                        completed.set_result(None)
                except asyncio.CancelledError:
                    if not completed.done():
                        completed.cancel()
                    raise
                except Exception as error:
                    if not completed.done():
                        completed.set_result(None)
                    logger.warning("Local ambient processing failed: %s", type(error).__name__)
            finally:
                queue.task_done()

    async def _observe_acoustic_frame(self, frame: AudioFrame, epoch: int) -> None:
        detector = self._acoustic_wake_detector
        if detector is None or self._closed or self._mode is not RuntimeMode.LOCAL_AMBIENT:
            return
        if epoch != self._acoustic_epoch:
            return
        try:
            if self._acoustic_stream_epoch != epoch:
                await asyncio.to_thread(detector.reset)
                self._acoustic_stream_epoch = epoch
            pcm = audio_to_mono_int16(frame[1]).astype("<i2", copy=False).tobytes()
            detection = await asyncio.to_thread(detector.process_audio, pcm, frame[0])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._acoustic_wake_detector = None
            logger.warning(
                "Acoustic wake disabled after inference failure: %s; manual activation remains available",
                type(error).__name__,
            )
            return
        if (
            detection is not None
            and not self._closed
            and self._mode is RuntimeMode.LOCAL_AMBIENT
            and epoch == self._acoustic_epoch
        ):
            await self.activate("acoustic_wake")

    async def _stop_local_processing(self) -> None:
        """Cancel the local worker and release queued frame waiters during close."""
        with self._lifecycle_lock:
            task = self._local_processing_task
            queue = self._local_processing_queue
            self._local_processing_task = None
            self._local_processing_queue = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            completed_tasks, _ = await asyncio.wait({task}, timeout=self._remaining_cleanup_seconds())
            if completed_tasks:
                await asyncio.gather(*completed_tasks, return_exceptions=True)
            else:
                logger.warning("Hybrid local processing cleanup exceeded the cleanup deadline")
        if queue is None:
            return
        while True:
            try:
                _, completed_future, _ = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            queue.task_done()
            if not completed_future.done():
                completed_future.cancel()

    async def _observe_local(self, frame: AudioFrame) -> None:
        try:
            result = self._local_processor.observe_frame(frame)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local ambient processing failed: %s", type(error).__name__)

    async def emit(self) -> HandlerOutput:
        """Emit output from the currently active handler without a second queue."""
        self._assert_owner_loop()
        async with self._state_lock:
            with self._lifecycle_lock:
                handler = self._active_handler
                generation = self._generation
        if handler is None:
            await self._wait_for_output_wakeup()
            async with self._state_lock:
                with self._lifecycle_lock:
                    handler = self._active_handler
                    generation = self._generation
            if handler is None:
                return None
        try:
            output = await handler.emit()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Hybrid provider output failed: %s", type(error).__name__)
            if self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_emit_failed")
            return None
        if not self._is_current_handler(handler, generation):
            return None
        return output

    async def check_inactivity(self) -> bool:
        """Close an inactive cloud session when its configured threshold is reached."""
        self._assert_owner_loop()
        if self._inactivity_timeout_s <= 0:
            return False
        async with self._state_lock:
            with self._lifecycle_lock:
                if self._mode is not RuntimeMode.CLOUD_CONVERSATION or self._active_handler is None:
                    return False
                if not self._connection_ready or self._user_speaking or self._interaction_busy:
                    return False
                if self._playback_is_active_locked(self._clock()) or not self._active_handler.output_queue.empty():
                    return False
                inactive_for = self._clock() - self._last_conversation_activity
            should_deactivate = inactive_for >= self._inactivity_timeout_s
        if should_deactivate:
            return await self._deactivate_cloud("inactivity_timeout")
        return False

    async def _monitor_inactivity(self, handler: ConversationHandler, generation: int) -> None:
        try:
            while self._is_current_handler(handler, generation):
                await asyncio.sleep(self._monitor_interval_s)
                if self._is_current_handler(handler, generation):
                    await self.check_inactivity()
        except asyncio.CancelledError:
            raise

    async def _wait_for_output_wakeup(self) -> None:
        """Wait until cloud output can exist or shutdown has started."""
        while True:
            with self._lifecycle_lock:
                if self._active_handler is not None or self._closed or self._mode is RuntimeMode.STOPPING:
                    return
            await self._output_wakeup_event.wait()
            self._output_wakeup_event.clear()

    async def deactivate_cloud(self, reason: str = "requested") -> bool:
        """Close the active provider and return to local ambient mode."""
        self._assert_owner_loop()
        return await self._deactivate_cloud(reason)

    async def _contain_uncommitted_handler(self, handler: ConversationHandler, reason: str, generation: int) -> bool:
        """Fence a constructed handler whose activation failed before cloud ownership."""
        shutdown_task: asyncio.Task[None]
        async with self._state_lock:
            with self._lifecycle_lock:
                current_mode = self._mode
                if current_mode is RuntimeMode.LOCAL_AMBIENT:
                    return self._shutdown_error is None
                if current_mode is not RuntimeMode.LOCAL_COMMAND or self._generation != generation:
                    return False
                self._generation += 1
                self._active_handler = None
                self._stopping_handler = handler
                self._provider_task = None
                self._inactivity_task = None
                self._postactivation_flush_task = None
                self._activation_text_task = None
                self._acoustic_ack_task = None
                self._pending_activation_text = None
                self._pending_acoustic_ack_generation = None
                self._postactivation_speech_observed = False
                self._connection_ready = False
                self._user_speaking = False
                self._interaction_busy = False
                self._playback_until = 0.0
                self._clear_wake_followup_waiter()
                self._clear_postactivation_queue()
                self._shutdown_error = None
                shutdown_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                if self._close_deadline is not None:
                    shutdown_deadline = min(shutdown_deadline, self._close_deadline)
                self._shutdown_deadline = shutdown_deadline
                self._transition(RuntimeMode.STOPPING, reason)
                try:
                    handler.stop_background_work()
                except Exception as error:
                    self._shutdown_error = "provider_stop_failed"
                    logger.warning("Provider background-work stop failed: %s", type(error).__name__)
                self._clear_handler_state(handler)
                shutdown_task = asyncio.create_task(
                    self._run_shutdown(handler, reason, ()),
                    name="hybrid-shutdown",
                )
                self._shutdown_task = shutdown_task
                shutdown_task.add_done_callback(self._shutdown_task_done)

        await self._wait_for_shutdown(shutdown_task)
        with self._lifecycle_lock:
            return self._mode is RuntimeMode.LOCAL_AMBIENT and self._shutdown_error is None

    async def _deactivate_cloud(self, reason: str) -> bool:
        current_task = asyncio.current_task()
        shutdown_task: asyncio.Task[None] | None = None
        cleanup_tasks: list[asyncio.Task[bool] | asyncio.Task[None]] = []
        handler: ConversationHandler | None = None

        async with self._state_lock:
            with self._lifecycle_lock:
                mode = self._mode
                active_handler = self._active_handler
                if mode is RuntimeMode.LOCAL_AMBIENT and active_handler is None:
                    self._clear_wake_followup_waiter()
                    return False

                if mode is RuntimeMode.LOCAL_COMMAND:
                    pending_activation = self._activation_task
                    self._generation += 1
                    self._clear_postactivation_queue()
                    self._clear_wake_followup_waiter()
                    if pending_activation is None or pending_activation.done():
                        self._transition(RuntimeMode.LOCAL_AMBIENT, reason)
                    else:
                        self._shutdown_error = None
                        shutdown_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                        if self._close_deadline is not None:
                            shutdown_deadline = min(shutdown_deadline, self._close_deadline)
                        self._shutdown_deadline = shutdown_deadline
                        self._transition(RuntimeMode.STOPPING, reason)
                        if pending_activation is not current_task:
                            pending_activation.cancel()
                            cleanup_tasks.append(pending_activation)
                        shutdown_task = asyncio.create_task(
                            self._run_shutdown(None, reason, tuple(cleanup_tasks)),
                            name="hybrid-shutdown",
                        )
                        self._shutdown_task = shutdown_task
                        shutdown_task.add_done_callback(self._shutdown_task_done)
                elif mode is RuntimeMode.STOPPING:
                    shutdown_task = self._shutdown_task
                elif mode is RuntimeMode.CLOUD_CONVERSATION:
                    handler = active_handler
                    self._log_cloud_session_summary(reason)
                    tasks_to_cancel = (
                        self._activation_task,
                        self._provider_task,
                        self._inactivity_task,
                        self._postactivation_flush_task,
                        self._activation_text_task,
                        self._acoustic_ack_task,
                    )
                    self._generation += 1
                    self._active_handler = None
                    self._stopping_handler = handler
                    self._provider_task = None
                    self._inactivity_task = None
                    self._postactivation_flush_task = None
                    self._activation_text_task = None
                    self._acoustic_ack_task = None
                    self._pending_activation_text = None
                    self._pending_acoustic_ack_generation = None
                    self._postactivation_speech_observed = False
                    self._connection_ready = False
                    self._user_speaking = False
                    self._interaction_busy = False
                    self._playback_until = 0.0
                    self._clear_wake_followup_waiter()
                    self._clear_postactivation_queue()
                    self._accumulate_session_duration(self._clock())
                    self._shutdown_error = None
                    shutdown_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                    if self._close_deadline is not None:
                        shutdown_deadline = min(shutdown_deadline, self._close_deadline)
                    self._shutdown_deadline = shutdown_deadline
                    self._transition(RuntimeMode.STOPPING, reason)
                    if handler is not None and isinstance(handler, ConversationHandler):
                        try:
                            handler.stop_background_work()
                        except Exception as error:
                            self._shutdown_error = "provider_stop_failed"
                            logger.warning("Provider background-work stop failed: %s", type(error).__name__)
                        self._clear_handler_state(handler)
                    for task in tasks_to_cancel:
                        if task is None or task is current_task:
                            continue
                        if task is not self._activation_task and not task.done():
                            task.cancel()
                        cleanup_tasks.append(task)
                    shutdown_task = asyncio.create_task(
                        self._run_shutdown(handler, reason, tuple(cleanup_tasks)),
                        name="hybrid-shutdown",
                    )
                    self._shutdown_task = shutdown_task
                    shutdown_task.add_done_callback(self._shutdown_task_done)
                else:
                    raise ValueError(f"Unsupported runtime mode: {mode.value}")

        if shutdown_task is None:
            return mode is RuntimeMode.LOCAL_COMMAND
        await self._wait_for_shutdown(shutdown_task)
        with self._lifecycle_lock:
            return self._mode is RuntimeMode.LOCAL_AMBIENT and self._shutdown_error is None

    def _clear_handler_state(self, handler: ConversationHandler) -> None:
        """Fence queued provider output and movement state before teardown."""
        self._drain_handler_output(handler)
        if not isinstance(handler, ConversationHandler):
            return
        clear_queue = handler._clear_queue
        if clear_queue is not None:
            try:
                clear_queue()
            except Exception as error:
                logger.warning("Provider playback fence failed: %s", type(error).__name__)
        try:
            movement_manager = handler.deps.movement_manager
            movement_manager.clear_move_queue()
            movement_manager.set_listening(False)
            movement_manager.set_speaking(False)
        except (AttributeError, TypeError) as error:
            logger.debug("Provider movement fence unavailable: %s", type(error).__name__)
        except Exception as error:
            logger.warning("Provider movement fence failed: %s", type(error).__name__)

    async def _run_shutdown(
        self,
        handler: ConversationHandler | None,
        reason: str,
        cleanup_tasks: tuple[asyncio.Task[bool] | asyncio.Task[None], ...],
    ) -> None:
        """Await provider and canceled auxiliary work as one bounded operation."""
        cleanup_operations: list[Awaitable[object]] = [self._await_shutdown_task(task) for task in cleanup_tasks]
        if handler is not None:
            cleanup_operations.insert(0, self._shutdown_handler_strict(handler, reason))
        if not cleanup_operations:
            return
        results = await asyncio.gather(*cleanup_operations, return_exceptions=True)
        shutdown_result = results[0]
        if handler is not None and isinstance(shutdown_result, BaseException):
            raise RuntimeError("provider shutdown did not complete") from shutdown_result
        auxiliary_results = results[1:] if handler is not None else results
        for result in auxiliary_results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise RuntimeError("hybrid cleanup did not complete") from result

    @staticmethod
    async def _await_shutdown_task(task: asyncio.Task[bool] | asyncio.Task[None]) -> object:
        """Await one task so cancellation or failure is included in containment."""
        return await task

    def _remaining_cleanup_seconds(self) -> float:
        """Return the remaining fixed cleanup budget for provider-adjacent work."""
        with self._lifecycle_lock:
            deadlines = tuple(
                deadline for deadline in (self._close_deadline, self._shutdown_deadline) if deadline is not None
            )
        if not deadlines:
            return self._SHUTDOWN_TIMEOUT_S
        return max(0.0, min(deadlines) - asyncio.get_running_loop().time())

    async def _wait_for_shutdown(self, task: asyncio.Task[None]) -> None:
        """Wait for shutdown until its fixed wall-clock containment deadline."""
        if task.done():
            self._finalize_shutdown(task)
            return
        with self._lifecycle_lock:
            deadline = self._shutdown_deadline
        remaining = self._SHUTDOWN_TIMEOUT_S
        if deadline is not None:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        completed, _ = await asyncio.wait({task}, timeout=remaining)
        if completed:
            self._finalize_shutdown(task)
        else:
            self._mark_shutdown_timeout(task)

    def _mark_shutdown_timeout(self, task: asyncio.Task[None]) -> None:
        event: tuple[RuntimeMode, str, int] | None = None
        with self._lifecycle_lock:
            if self._shutdown_task is not task or self._mode is not RuntimeMode.STOPPING:
                return
            if task.done():
                self._finalize_shutdown(task)
                return
            if self._shutdown_error is None:
                self._shutdown_error = "provider_cleanup_timeout"
                event = (RuntimeMode.STOPPING, "shutdown_timeout", self._generation)
        logger.warning("Hybrid provider cleanup exceeded the bounded shutdown deadline")
        if event is not None:
            self._notify_state_observer(*event)

    def _shutdown_task_done(self, task: asyncio.Task[None]) -> None:
        self._finalize_shutdown(task)

    def _finalize_shutdown(self, task: asyncio.Task[None]) -> None:
        event: tuple[RuntimeMode, str, int] | None = None
        failed = False
        try:
            task.result()
        except BaseException as error:
            failed = True
            logger.warning("Hybrid provider cleanup failed: %s", type(error).__name__)
        with self._lifecycle_lock:
            if self._shutdown_task is not task or self._mode is not RuntimeMode.STOPPING:
                return
            if failed:
                if self._shutdown_error is None:
                    self._shutdown_error = "provider_cleanup_failed"
                    event = (RuntimeMode.STOPPING, "shutdown_failed", self._generation)
                self._shutdown_task = None
                self._shutdown_deadline = None
                self._stopping_handler = None
            else:
                self._shutdown_task = None
                self._shutdown_deadline = None
                self._stopping_handler = None
                self._shutdown_error = None
                self._transition(RuntimeMode.LOCAL_AMBIENT, "shutdown_complete")
        if event is not None:
            self._notify_state_observer(*event)

    async def close(self) -> None:
        """Idempotently stop the provider and leave subsequent frames local."""
        self._assert_owner_loop()
        with self._lifecycle_lock:
            if self._closed and self._active_handler is None and self._activation_task is None:
                return
            close_task = self._close_task
            if close_task is None or close_task.done():
                self._closed = True
                self._close_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                close_task = asyncio.create_task(self._finish_close(), name="hybrid-close")
                self._close_task = close_task
        self._local_start_event.set()
        self._output_wakeup_event.set()

        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await asyncio.shield(close_task)
            raise

    async def _finish_close(self) -> None:
        self._clear_wake_followup_waiter()
        await self._deactivate_cloud("runtime_closed")
        current_task = asyncio.current_task()
        local_event_tasks = tuple(task for task in self._local_event_tasks if task is not current_task)
        for task in local_event_tasks:
            task.cancel()
        if local_event_tasks:
            completed, _ = await asyncio.wait(local_event_tasks, timeout=self._remaining_cleanup_seconds())
            if completed:
                await asyncio.gather(*completed, return_exceptions=True)
            if len(completed) != len(local_event_tasks):
                logger.warning("Hybrid local event cleanup exceeded the cleanup deadline")
        self._local_event_tasks.clear()
        with self._lifecycle_lock:
            activation_task = self._activation_task
        if (
            activation_task is not None
            and activation_task is not asyncio.current_task()
            and not activation_task.done()
        ):
            activation_task.cancel()
            completed, _ = await asyncio.wait({activation_task}, timeout=self._remaining_cleanup_seconds())
            if not completed:
                logger.warning("Hybrid activation cleanup exceeded the cleanup deadline")
        await self._stop_local_processing()
        callback = self._local_processing_close_callback
        if callback is not None:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    callback_task: asyncio.Future[None] = asyncio.ensure_future(result)
                    completed_tasks, _ = await asyncio.wait({callback_task}, timeout=self._remaining_cleanup_seconds())
                    if completed_tasks:
                        await asyncio.gather(*completed_tasks, return_exceptions=True)
                    else:
                        callback_task.cancel()
                        logger.warning("Hybrid local processing cleanup exceeded the cleanup deadline")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Local processing shutdown failed: %s", type(error).__name__)

    async def _await_cancelled_task(self, task: asyncio.Task[object]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.warning("Hybrid task cleanup failed: %s", type(error).__name__)

    async def _shutdown_handler(self, handler: ConversationHandler, reason: str) -> None:
        try:
            await self._shutdown_handler_strict(handler, reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Provider shutdown failed reason=%s error=%s", reason, type(error).__name__)

    def _prune_shutdown_handler_tasks(self) -> None:
        """Drop completed shutdown records whose handlers are no longer live."""
        stale_handler_ids = [
            handler_id
            for handler_id, (handler_ref, task) in self._shutdown_handler_tasks.items()
            if handler_ref() is None and task.done()
        ]
        for handler_id in stale_handler_ids:
            self._shutdown_handler_tasks.pop(handler_id, None)

    async def _shutdown_handler_strict(self, handler: ConversationHandler, reason: str) -> None:
        """Shutdown one handler once and preserve failures for containment."""
        del reason
        self._prune_shutdown_handler_tasks()
        handler_id = id(handler)
        tracked = self._shutdown_handler_tasks.get(handler_id)
        if tracked is not None:
            target, task = tracked
            if target() is handler:
                await asyncio.shield(task)
                return
        task = asyncio.create_task(handler.shutdown(), name="hybrid-provider-shutdown")
        self._shutdown_handler_tasks[handler_id] = (weakref.ref(handler), task)
        await asyncio.shield(task)

    def _record_session_segment(self, duration: float) -> None:
        if duration <= 0:
            return
        with self._metrics_lock:
            self._gemini_session_duration += duration

    def _accumulate_session_duration(self, now: float) -> None:
        with self._lifecycle_lock:
            connected_since = self._connected_since
            self._connected_since = None
        if connected_since is not None:
            self._record_session_segment(max(0.0, now - connected_since))

    @staticmethod
    def _copy_frame(frame: AudioFrame) -> AudioFrame:
        sample_rate, audio = frame
        return sample_rate, audio.copy()


__all__ = [
    "AcknowledgementCallback",
    "HandlerFactory",
    "HybridRuntime",
    "InteractionBusyObserver",
    "LocalCommandOutcome",
    "RuntimeMode",
    "RuntimeStatusSnapshot",
    "TrafficMetricsSnapshot",
]
