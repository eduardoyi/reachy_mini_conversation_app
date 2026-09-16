from __future__ import annotations
import math
import time
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Literal, ClassVar, TypeAlias
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import AdditionalOutputs, AsyncStreamHandler, wait_for_item
from reachy_mini_conversation_app.idle_policy import start_idle_tool_call
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies, get_tool_specs
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)


AudioFrame: TypeAlias = tuple[int, NDArray[np.int16]]
HandlerOutput: TypeAlias = AudioFrame | AdditionalOutputs | None
QueueItem: TypeAlias = AudioFrame | AdditionalOutputs
TrafficKind: TypeAlias = Literal["audio", "video"]
TrafficObserver: TypeAlias = Callable[[TrafficKind, int], None]
ConnectionObserver: TypeAlias = Callable[[bool], None]
InteractionBusyObserver: TypeAlias = Callable[[bool], None]


class ConversationHandler(AsyncStreamHandler, ABC):
    """Shared app handler contract and idle behavior for realtime conversation backends."""

    IDLE_BEHAVIOR_THRESHOLD_S: ClassVar[float] = 180.0

    deps: ToolDependencies
    tool_manager: BackgroundToolManager
    output_queue: asyncio.Queue[QueueItem]
    last_activity_time: float
    last_idle_behavior_time: float
    _activity_observer: Callable[[str], None] | None = None
    _transcript_observer: Callable[[str, str, bool], None] | None = None
    _traffic_observer: TrafficObserver | None = None
    _connection_observer: ConnectionObserver | None = None
    _interaction_busy_observer: InteractionBusyObserver | None = None
    _startup_greeting_enabled: bool = True
    _initial_context: str = ""
    _interaction_busy: bool = False
    _playback_until: float = 0.0

    def __init__(self) -> None:
        """Initialize the stream handler and shared idle/activity tracking."""
        super().__init__()
        self.last_activity_time = time.monotonic()
        self.last_idle_behavior_time = self.last_activity_time
        self._initial_context = ""
        self._interaction_busy = False
        self._playback_until = 0.0

    def set_activity_observer(self, observer: Callable[[str], None] | None) -> None:
        """Attach or detach an activity observer. Pass None to clear."""
        self._activity_observer = observer

    def set_transcript_observer(self, observer: Callable[[str, str, bool], None] | None) -> None:
        """Attach/detach a transcript observer, called (role, text, final)."""
        self._transcript_observer = observer

    def set_traffic_observer(self, observer: TrafficObserver | None) -> None:
        """Attach/detach a provider traffic observer for successful media sends."""
        self._traffic_observer = observer

    def set_connection_observer(self, observer: ConnectionObserver | None) -> None:
        """Attach/detach an observer for provider session connection changes."""
        self._connection_observer = observer

    def set_interaction_busy_observer(self, observer: InteractionBusyObserver | None) -> None:
        """Attach/detach an observer for authoritative provider busy state."""
        self._interaction_busy_observer = observer

    @property
    def interaction_busy(self) -> bool:
        """Return the provider's authoritative interaction busy state."""
        return self._interaction_busy

    @property
    def playback_active(self) -> bool:
        """Return whether local playback is currently consuming provider output."""
        return self._playback_until > time.monotonic()

    def set_playback_active(self, active: bool) -> None:
        """Set or clear an explicit local playback boundary."""
        self._playback_until = math.inf if active else 0.0

    def note_playback_queued(self, duration_s: float) -> None:
        """Extend the estimate of queued local playback by one audio duration."""
        if duration_s <= 0 or not math.isfinite(duration_s):
            return
        now = time.monotonic()
        self._playback_until = max(self._playback_until, now) + duration_s

    def _emit_interaction_busy(self, busy: bool) -> None:
        """Publish authoritative provider busy state without changing deadlines."""
        normalized_busy = bool(busy)
        if self._interaction_busy == normalized_busy:
            return
        self._interaction_busy = normalized_busy
        observer = self._interaction_busy_observer
        if observer is None:
            return
        try:
            observer(normalized_busy)
        except Exception as error:
            logger.warning("interaction busy observer failed: %s", type(error).__name__)

    def set_startup_greeting_enabled(self, enabled: bool) -> None:
        """Enable or suppress a provider's automatic startup greeting."""
        self._startup_greeting_enabled = bool(enabled)

    def set_initial_context(self, context: str) -> None:
        """Set bounded locally derived context before provider startup."""
        self._initial_context = context.strip()

    def get_initial_context(self) -> str:
        """Return the locally derived context configured for startup."""
        return self._initial_context

    def _emit_traffic(self, kind: TrafficKind, byte_count: int) -> None:
        """Notify observers after a provider media send succeeds."""
        observer = self._traffic_observer
        if observer is None or byte_count <= 0:
            return
        try:
            observer(kind, byte_count)
        except Exception:
            logger.debug("traffic observer raised (ignored)", exc_info=True)

    def _emit_connection_state(self, connected: bool) -> None:
        """Notify observers when a provider session becomes connected or closed."""
        observer = self._connection_observer
        if observer is None:
            return
        try:
            observer(connected)
        except Exception:
            logger.debug("connection observer raised (ignored)", exc_info=True)

    def _emit_transcript(self, role: str, text: str, final: bool = True) -> None:
        """Forward one transcript chunk to the observer, if attached."""
        observer = self._transcript_observer
        if observer is not None and text:
            try:
                observer(role, text, final)
            except Exception:
                logger.debug("transcript observer raised (ignored)", exc_info=True)

    def _mark_activity(self, reason: str) -> None:
        """Record non-idle conversation activity for the idle timer."""
        self.last_activity_time = time.monotonic()
        logger.debug("last activity time updated to %s (%s)", self.last_activity_time, reason)
        if self._activity_observer is not None:
            try:
                self._activity_observer(reason)
            except Exception:
                logger.debug("activity observer raised (ignored)", exc_info=True)

    def _idle_behavior_ready(self) -> bool:
        """Return whether idle behavior may run now. Backends can add guards."""
        return True

    async def emit(self) -> HandlerOutput:
        """Emit the next queued output, triggering local idle behavior when due."""
        now = time.monotonic()
        idle_duration = now - self.last_activity_time
        idle_behavior_duration = now - self.last_idle_behavior_time
        if (
            idle_duration > self.IDLE_BEHAVIOR_THRESHOLD_S
            and idle_behavior_duration > self.IDLE_BEHAVIOR_THRESHOLD_S
            and self._idle_behavior_ready()
            and not self._interaction_busy
            and not self.playback_active
            and self.output_queue.empty()
            and self.deps.movement_manager.is_idle()
        ):
            try:
                await self.send_idle_signal(idle_duration)
            except Exception as e:
                logger.warning("Idle tool skipped (connection closed?): %s", e)
                return None
            self.last_idle_behavior_time = now
        handler_output = await wait_for_item(self.output_queue)
        return handler_output

    async def send_idle_signal(self, idle_duration: float) -> None:
        """Run a locally selected idle tool without sending an idle turn to the model."""
        if not self._is_connected():
            logger.debug("No active session; cannot run idle tool")
            return

        available_tool_names = {spec["name"] for spec in get_tool_specs()}
        await start_idle_tool_call(
            deps=self.deps,
            tool_manager=self.tool_manager,
            output_queue=self.output_queue,
            available_tool_names=available_tool_names,
            idle_duration=idle_duration,
        )

    @abstractmethod
    def _is_connected(self) -> bool:
        """Return whether the backend session/connection is currently open."""
        ...

    @abstractmethod
    async def start_up(self) -> None:
        """Start the realtime handler."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Shut down the realtime handler."""
        ...

    def stop_background_work(self) -> None:
        """Stop provider work before the runtime leaves cloud mode."""
        return

    @abstractmethod
    async def receive(self, frame: AudioFrame) -> None:
        """Receive an input audio frame."""
        ...

    @abstractmethod
    async def apply_personality(self, profile: str | None) -> str:
        """Apply a personality profile."""
        ...

    @abstractmethod
    async def get_available_voices(self) -> list[str]:
        """Return voices available for the active backend."""
        ...

    @abstractmethod
    def get_current_voice(self) -> str:
        """Return the current voice."""
        ...

    @abstractmethod
    async def change_voice(self, voice: str) -> str:
        """Change the current voice."""
        ...

    @abstractmethod
    async def say(self, text: str) -> None:
        """Make the robot speak ``text`` now (injected turn; not verbatim TTS).

        The backend is speech-to-speech, so ``text`` is an instruction the
        model voices, not a guaranteed-literal string. Raises if no session is
        open.
        """
        ...
