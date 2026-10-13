"""Bidirectional local audio stream with an optional provider settings UI.

Provider credentials remain in the app environment; the UI reports readiness,
selects the provider, and edits only the Hugging Face connection target.
"""

import os
import sys
import json
import math
import time
import queue
import asyncio
import logging
import ipaddress
import threading
from typing import Any, List, TypeVar, Optional, Protocol
from pathlib import Path
from collections import deque
from collections.abc import Callable, Iterator, Coroutine

import numpy as np
from numpy.typing import NDArray

from reachy_mini import ReachyMini
from reachy_mini.io.jsonrpc import JsonRpcError
from reachy_mini.apps.jsonrpc_server import JsonRpcServer
from reachy_mini.media.media_manager import MediaBackend
from reachy_mini_conversation_app.config import (
    HF_BACKEND,
    GEMINI_BACKEND,
    LOCKED_PROFILE,
    OPENAI_LIVE_BACKEND,
    REALTIME_PROVIDER_ENV,
    HF_REALTIME_WS_URL_ENV,
    AUDIO_OUTPUT_BACKEND_SDK,
    HF_LOCAL_CONNECTION_MODE,
    HF_DEPLOYED_CONNECTION_MODE,
    HF_REALTIME_CONNECTION_MODE_ENV,
    config,
    get_default_voice,
    get_hf_session_url,
    set_custom_profile,
    has_realtime_target,
    get_available_voices,
    get_hf_direct_ws_url,
    get_realtime_provider,
    build_hf_direct_ws_url,
    parse_hf_direct_target,
    get_audio_output_backend,
    get_hf_connection_selection,
    refresh_runtime_config_from_env,
    get_realtime_missing_config_message,
)
from reachy_mini_conversation_app.prompts import get_session_voice, get_session_instructions
from reachy_mini_conversation_app.streaming import AdditionalOutputs, audio_to_int16, resample_audio, audio_to_float32
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.household_memory import (
    MemoryStoreError,
    HouseholdMemoryStore,
    MigrationRequiredError,
)
from reachy_mini_conversation_app.startup_settings import read_startup_settings, write_startup_settings
from reachy_mini_conversation_app.tools.core_tools import initialize_tools
from reachy_mini_conversation_app.audio.diagnostics import AudioDiagnostics
from reachy_mini_conversation_app.tool_space_routes import register_tool_space_methods
from reachy_mini_conversation_app.conversation_recap import ConversationRecap
from reachy_mini_conversation_app.personality_routes import (
    build_personality_ops,
    register_personality_methods,
)
from reachy_mini_conversation_app.profile_tool_routes import register_profile_tool_methods
from reachy_mini_conversation_app.audio.speaker_output import SpeakerOutput, create_speaker_output
from reachy_mini_conversation_app.audio.startup_config import apply_audio_startup_config
from reachy_mini_conversation_app.conversation_handler import ConversationHandler
from reachy_mini_conversation_app.local_audio_backends import pcm16_rms


try:
    # FastAPI is provided by the Reachy Mini Apps runtime
    from fastapi import FastAPI, Request, Response
    from pydantic import BaseModel
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover - only loaded when settings_app is used
    FastAPI = object  # type: ignore
    Request = object  # type: ignore
    FileResponse = object  # type: ignore
    JSONResponse = object  # type: ignore
    StaticFiles = object  # type: ignore
    BaseModel = object  # type: ignore

logger = logging.getLogger(__name__)
_MEMORY_SHUTDOWN_TIMEOUT_S = 20.0


def _detach_framework_root_routes(app: "FastAPI") -> None:
    """Strip framework routes that would shadow the settings UI."""
    routes = getattr(app, "router", None)
    routes = getattr(routes, "routes", None) if routes else getattr(app, "routes", None)
    if routes is None:
        return
    survivors = []
    for route in routes:
        path = getattr(route, "path", None)
        is_catch_all = isinstance(path, str) and path.startswith("/{") and path.endswith(":path}")
        if path in ("/", "/static") or is_catch_all:
            logger.debug("detaching framework-provided route %r (%s)", path, type(route).__name__)
            continue
        survivors.append(route)
    routes[:] = survivors


LOCAL_PLAYER_BACKEND = (
    getattr(MediaBackend, "LOCAL", None)
    or getattr(MediaBackend, "GSTREAMER", None)
    or getattr(MediaBackend, "DEFAULT", None)
)

HandlerFactory = Callable[[Optional[str]], ConversationHandler]
ResultT = TypeVar("ResultT")


class _ConversationCommandHandler(Protocol):
    """Typed command surface shared by active realtime handlers."""

    def _is_connected(self) -> bool:
        """Return whether the handler has an active provider session."""

    async def say(self, text: str) -> None:
        """Queue a spoken text turn."""


LEGACY_STARTUP_ENV_NAMES = (
    "REACHY_MINI_CUSTOM_PROFILE",
    "REACHY_MINI_VOICE_OVERRIDE",
)
BACKEND_RETRY_DELAY_SECONDS = 5.0
CAPTURE_QUEUE_SIZE = 200  # about 2 s of 10 ms SDK frames, enough to ride out event-loop stalls
CAPTURE_POLL_INTERVAL_SECONDS = 0.005
CAPTURE_STOP_JOIN_TIMEOUT_SECONDS = 0.1
PLAYBACK_LEAD_SECONDS = 0.3  # SDK audio kept ahead of the speaker; bounds talk-over after an interruption
PLAYBACK_PIECE_SECONDS = 0.1
HYBRID_ACTIVATION_TIMEOUT_SECONDS = 10.0
LOCAL_TRANSCRIPT_PREVIEW_PATH = "/api/transcript-preview"


def _channel0_rms(audio_frame: NDArray[Any]) -> float:
    """Return the int16 RMS of the channel the direct providers send."""
    samples = audio_frame
    if samples.ndim == 2:
        samples = samples[:, 0] if samples.shape[0] >= samples.shape[1] else samples[0]
    return pcm16_rms(audio_to_int16(samples))


class _HybridLoopUnavailable(RuntimeError):
    """Signal that the LocalStream asyncio loop is not ready for hybrid work."""


class _HybridActivationTimeout(RuntimeError):
    """Signal that hybrid activation did not complete within the RPC bound."""


class _AudioCaptureWorker:
    """Pull microphone frames off the asyncio loop into a bounded queue."""

    def __init__(
        self,
        media: Any,
        *,
        diagnostics: AudioDiagnostics | None = None,
        queue_size: int = CAPTURE_QUEUE_SIZE,
        poll_interval: float = CAPTURE_POLL_INTERVAL_SECONDS,
        stop_join_timeout: float = CAPTURE_STOP_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the worker and its thread-safe frame queue."""
        if queue_size <= 0:
            raise ValueError("Audio capture queue size must be positive")
        if poll_interval < 0 or stop_join_timeout < 0:
            raise ValueError("Audio capture worker timeouts must not be negative")

        self._media = media
        self._diagnostics = diagnostics
        self._queue: "queue.Queue[_CapturedAudioFrame]" = queue.Queue(maxsize=queue_size)
        self._poll_interval = poll_interval
        self._stop_join_timeout = stop_join_timeout
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failure_type: str | None = None
        self._dropped_frames = 0
        self._sequence = 0
        self._capture_epoch = 0
        self._stop_warning_logged = False

    @property
    def dropped_frames(self) -> int:
        """Return the number of frames dropped because the queue was full."""
        with self._state_lock:
            return self._dropped_frames

    @property
    def failure_type(self) -> str | None:
        """Return the failed worker exception type, if capture stopped on error."""
        with self._state_lock:
            return self._failure_type

    @property
    def sequence(self) -> int:
        """Return the most recently captured frame sequence number."""
        with self._state_lock:
            return self._sequence

    @property
    def capture_epoch(self) -> int:
        """Return the latest capture epoch, including an in-flight SDK pull."""
        with self._state_lock:
            return self._capture_epoch

    def start(self) -> None:
        """Start capture once, or leave an already-running worker unchanged."""
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._failure_type = None
            self._stop_warning_logged = False
            self._thread = threading.Thread(
                target=self._run,
                name="audio-capture-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request capture stop and wait only a bounded time for the worker."""
        with self._state_lock:
            self._stop_event.set()
            thread = self._thread
        if thread is None or thread is threading.current_thread():
            return

        thread.join(timeout=self._stop_join_timeout)
        if thread.is_alive() and not self._stop_warning_logged:
            with self._state_lock:
                self._stop_warning_logged = True
            logger.warning("Audio capture worker did not stop within %.2f seconds", self._stop_join_timeout)

    def get_nowait(self) -> "_CapturedAudioFrame | None":
        """Return the oldest queued frame without blocking the asyncio loop."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        try:
            sample_rate = int(self._media.get_input_audio_samplerate())

            previous_capture_at: float | None = None
            burst_dropped_frames = 0
            burst_dropped_seconds = 0.0
            while not self._stop_event.is_set():
                with self._state_lock:
                    self._capture_epoch += 1
                    capture_epoch = self._capture_epoch
                pull_started = time.monotonic()
                audio_frame = self._media.get_audio_sample()
                captured_at = time.monotonic()
                if self._diagnostics is not None:
                    self._diagnostics.observe("capture_pull", captured_at - pull_started)
                if self._stop_event.is_set():
                    break
                if audio_frame is None:
                    self._stop_event.wait(self._poll_interval)
                    continue

                with self._state_lock:
                    self._sequence += 1
                    sequence = self._sequence
                if self._diagnostics is not None and previous_capture_at is not None:
                    self._diagnostics.observe("capture_gap", captured_at - previous_capture_at)
                previous_capture_at = captured_at
                captured_frame = _CapturedAudioFrame(sample_rate, audio_frame, sequence, capture_epoch)
                captured_frame.captured_at = captured_at
                try:
                    self._queue.put_nowait(captured_frame)
                except queue.Full:
                    try:
                        dropped_frame = self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        burst_dropped_frames += 1
                        burst_dropped_seconds += len(dropped_frame.audio) / dropped_frame.sample_rate
                        with self._state_lock:
                            self._dropped_frames += 1
                    self._queue.put_nowait(captured_frame)
                    continue
                if burst_dropped_frames:
                    logger.warning(
                        "Audio capture queue overflowed; dropped %d frames (%.0f ms of microphone audio)",
                        burst_dropped_frames,
                        burst_dropped_seconds * 1000,
                    )
                    burst_dropped_frames = 0
                    burst_dropped_seconds = 0.0
        except Exception as exc:
            with self._state_lock:
                self._failure_type = type(exc).__name__
            logger.warning("Audio capture worker stopped: %s", type(exc).__name__)


class _CapturedAudioFrame:
    """Two-item capture tuple carrying frame and capture-epoch metadata."""

    def __init__(self, sample_rate: int, audio: Any, sequence: int, capture_epoch: int | None = None) -> None:
        """Store a captured frame and the epoch reserved before its SDK pull."""
        self.captured_at = time.monotonic()
        self.sample_rate = sample_rate
        self.audio = audio
        self.sequence = sequence
        self.capture_epoch = sequence if capture_epoch is None else capture_epoch

    def __iter__(self) -> Iterator[Any]:
        """Preserve the existing ``sample_rate, audio`` unpacking contract."""
        yield self.sample_rate
        yield self.audio

    def __getitem__(self, index: int) -> Any:
        """Preserve tuple-style indexing for existing stream tests."""
        if index == 0:
            return self.sample_rate
        if index == 1:
            return self.audio
        raise IndexError(index)


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    handler: Any

    def __init__(
        self,
        handler: ConversationHandler | HybridRuntime,
        robot: ReachyMini,
        *,
        settings_app: Optional[FastAPI] = None,
        instance_path: Optional[str] = None,
        handler_factory: HandlerFactory | None = None,
        startup_voice: Optional[str] = None,
        hybrid_runtime: HybridRuntime | None = None,
        speaker_output_factory: Callable[[int], SpeakerOutput] | None = None,
    ):
        """Initialize the stream with a realtime handler and pipelines.

        - ``settings_app``: the Reachy Mini Apps FastAPI to attach settings endpoints.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        - ``handler_factory``: builds a fresh handler for the currently selected backend.
        """
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._restart_requested = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        self._handler_factory = handler_factory
        self._hybrid_runtime = hybrid_runtime
        self._voice_override = startup_voice
        self._settings_app: Optional[FastAPI] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._settings_initialized = False
        self._asyncio_loop = None
        self._mic_muted = False  # mic starts live; the UI toggles it via the settings API
        self._macos_local_flush_warning_logged = False
        self._backend_connection_state = "not_started"
        self._backend_error: str | None = None
        self._direct_shutdown_blocked = False
        self._backend_retry_delay = BACKEND_RETRY_DELAY_SECONDS
        self._audio_diagnostics: AudioDiagnostics | None = None
        self._capture_worker: _AudioCaptureWorker | None = None
        self._paced_playback: deque[tuple[NDArray[np.float32], int]] = deque()
        self._paced_playback_until = 0.0
        self._speaker_output: SpeakerOutput | None = None
        self._speaker_output_factory = speaker_output_factory
        # JSON-RPC control surface (mounted at /rpc in _init_settings_ui_if_needed).
        # Notifications (conversation.turn/phase/transcript/activity) are pushed
        # here from activity + transcripts. Survives handler rebuilds (mounted once).
        self._rpc: Optional[JsonRpcServer] = None
        self._last_turn_state: Optional[str] = None
        # Per-role throttle timestamps for conversation.level (orb audio meter).
        self._last_level_emit: dict[str, float] = {}
        # Recent turns (memory only) so a session the provider ends can continue instead of restarting.
        self._conversation_recap = ConversationRecap()
        self._continue_after_provider_end = False
        self._install_handler(handler)
        self._attach_hybrid_runtime()

    def _build_speaker_output(self) -> SpeakerOutput | None:
        """Build an explicitly selected app-owned speaker output."""
        backend = get_audio_output_backend()
        if backend == AUDIO_OUTPUT_BACKEND_SDK:
            return None
        try:
            sample_rate = int(self._robot.media.get_output_audio_samplerate())
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("Selected audio output requires a valid SDK output sample rate") from exc
        if self._speaker_output_factory is not None:
            return self._speaker_output_factory(sample_rate)
        return create_speaker_output(backend, sample_rate, logger_instance=logger)

    def _attach_hybrid_runtime(self) -> None:
        """Wire LocalStream's queue, state, and capture boundary into hybrid mode."""
        runtime = self._hybrid_runtime
        if runtime is None:
            return
        runtime.set_playback_clearer(self._clear_robot_playback)
        runtime.set_capture_barrier_provider(self._capture_sequence)
        runtime.set_state_observer(self._dispatch_hybrid_state)

    async def _start_hybrid_runtime(self) -> None:
        """Start local hybrid processing when the runtime exposes a startup hook."""
        runtime = self._hybrid_runtime
        if runtime is None:
            return
        await runtime.start_up()

    async def activate_hybrid(self, reason: str = "rpc") -> bool | dict[str, object]:
        """Schedule hybrid activation on LocalStream's asyncio loop."""
        runtime = self._hybrid_runtime
        if runtime is None:
            raise _HybridLoopUnavailable("hybrid runtime is disabled")
        loop = self._asyncio_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            raise _HybridLoopUnavailable("hybrid runtime loop is unavailable")

        async def run_activation_on_local_loop() -> bool | dict[str, object]:
            """Invoke the runtime coroutine only after entering its owner loop."""
            return await runtime.activate(reason)

        activation = run_activation_on_local_loop()
        try:
            future = asyncio.run_coroutine_threadsafe(activation, loop)
        except RuntimeError as error:
            activation.close()
            raise _HybridLoopUnavailable("hybrid runtime loop is unavailable") from error

        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(future),
                timeout=HYBRID_ACTIVATION_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            future.cancel()
            raise
        except asyncio.TimeoutError as error:
            future.cancel()
            raise _HybridActivationTimeout("hybrid activation timed out") from error

    async def get_transcript_preview(self) -> dict[str, object]:
        """Return the opt-in local transcript preview through the owner loop."""
        enabled = bool(config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED)
        unavailable: dict[str, object] = {"enabled": enabled, "available": False, "text": ""}
        if not enabled:
            return unavailable
        runtime = self._hybrid_runtime
        if runtime is None:
            return unavailable
        try:
            text = await self._run_on_owner_loop(runtime.get_transcript_preview)
        except _HybridLoopUnavailable:
            return unavailable
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local transcript preview unavailable: %s", type(error).__name__)
            return unavailable
        if text is None:
            return unavailable
        if not isinstance(text, str):
            logger.warning("Local transcript preview returned an invalid value")
            return unavailable
        return {"enabled": True, "available": True, "text": text}

    async def _run_on_owner_loop(self, operation: Callable[[], Coroutine[Any, Any, ResultT]]) -> ResultT:
        """Run a control operation on LocalStream's loop, or fail if it is unavailable."""
        loop = self._asyncio_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            raise _HybridLoopUnavailable("LocalStream loop is unavailable")

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            return await operation()

        coroutine = operation()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError as error:
            coroutine.close()
            raise _HybridLoopUnavailable("LocalStream loop is unavailable") from error
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def say(self, text: str) -> None:
        """Queue a spoken text turn on the LocalStream owner loop."""
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("say: empty text")

        async def send_on_owner_loop() -> None:
            handler: _ConversationCommandHandler | None = self.handler
            if self._hybrid_runtime is not None:
                handler = self._hybrid_runtime.active_handler
            if handler is None or not handler._is_connected():
                raise RuntimeError("say: no active session")
            self.clear_audio_queue()
            await handler.say(normalized_text)

        await self._run_on_owner_loop(send_on_owner_loop)

    async def interrupt(self) -> None:
        """Flush active provider output and robot playback on the owner loop."""

        async def interrupt_on_owner_loop() -> None:
            handler: _ConversationCommandHandler | None = self.handler
            if self._hybrid_runtime is not None:
                handler = self._hybrid_runtime.active_handler
            if handler is None or not handler._is_connected():
                raise RuntimeError("no active session")
            self.clear_audio_queue()

        await self._run_on_owner_loop(interrupt_on_owner_loop)

    async def _close_hybrid_runtime(self) -> None:
        """Close hybrid processing exactly once through its public lifecycle hook."""
        runtime = self._hybrid_runtime
        if runtime is None:
            return
        await runtime.shutdown()

    async def _receive_hybrid_frame(self, frame: tuple[int, Any], sequence: int | None) -> None:
        """Forward one captured frame through the hybrid privacy boundary."""
        runtime = self._hybrid_runtime
        if runtime is None:
            return
        await runtime.handle_frame(frame, capture_sequence=sequence, wait_for_local_processing=False)

    def _capture_sequence(self) -> int | None:
        """Return the latest capture epoch for activation fencing."""
        capture_worker = self._capture_worker
        return capture_worker.capture_epoch if capture_worker is not None else None

    def _broadcast_rpc_threadsafe(self, method: str, params: dict[str, object]) -> None:
        """Publish one notification while tolerating a closed UI loop."""
        rpc = self._rpc
        if rpc is None:
            return
        loop = getattr(rpc, "_loop", None)
        if loop is None or loop.is_closed() or not loop.is_running():
            return
        notification = rpc.broadcast(method, params)
        try:
            asyncio.run_coroutine_threadsafe(notification, loop)
        except RuntimeError:
            notification.close()

    def _dispatch_hybrid_state(self, state: RuntimeMode, reason: str, generation: int) -> None:
        """Push a metadata-only hybrid state transition to RPC clients."""
        self._emit_phase(state.value, reason)
        params: dict[str, object] = {
            "state": state.value,
            "reason": reason,
            "generation": generation,
        }
        if state is RuntimeMode.STOPPING:
            runtime = self._hybrid_runtime
            shutdown_error = runtime.status.shutdown_error if runtime is not None else None
            if shutdown_error:
                params["error"] = shutdown_error
            self._set_backend_connection_state("stopping", shutdown_error or "Provider cleanup is in progress.")
        elif state is RuntimeMode.LOCAL_AMBIENT and reason == "shutdown_complete":
            self._set_backend_connection_state("ambient")
        self._broadcast_rpc_threadsafe(
            "conversation.hybrid_state",
            params,
        )

    def _install_handler(self, handler: ConversationHandler | HybridRuntime) -> None:
        """Set the active handler and wire LocalStream-owned helpers into it."""
        self.handler = handler
        if self._hybrid_runtime is None:
            self._direct_shutdown_blocked = False
        if self._hybrid_runtime is None:
            self.handler._clear_queue = self.clear_audio_queue
        self._attach_observers_to_handler()

    def _attach_observers_to_handler(self) -> None:
        """Wire the handler's activity + transcript observers to JSON-RPC pushes."""
        if self._hybrid_runtime is not None:
            self._hybrid_runtime.set_activity_observer(self._dispatch_activity)
            self._hybrid_runtime.set_transcript_observer(self._dispatch_transcript)
            return
        setter = getattr(self.handler, "set_activity_observer", None)
        if callable(setter):
            setter(self._dispatch_activity)
        transcript_setter = getattr(self.handler, "set_transcript_observer", None)
        if callable(transcript_setter):
            transcript_setter(self._dispatch_transcript)

    def _dispatch_transcript(self, role: str, text: str, final: bool) -> None:
        """Push a conversation.transcript notification to JSON-RPC clients."""
        if self._hybrid_runtime is None:
            self._conversation_recap.observe(role, text, final)
        self._broadcast_rpc_threadsafe("conversation.transcript", {"role": role, "text": text, "final": final})

    # Audio level meter for the client orb. RMS is scaled into a visible 0..1
    # range and capped to ~15 Hz so it stays light on the DataChannel.
    _LEVEL_INTERVAL_S = 1.0 / 15.0
    _LEVEL_GAIN = 6.0

    def _emit_level(self, role: str, frame: Any) -> None:
        """Emit a throttled conversation.level (RMS) for ``role`` (user/assistant)."""
        if self._rpc is None:
            return
        now = time.monotonic()
        if now - self._last_level_emit.get(role, 0.0) < self._LEVEL_INTERVAL_S:
            return
        self._last_level_emit[role] = now
        try:
            samples = audio_to_float32(frame)
            rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        except Exception:
            return
        level = max(0.0, min(1.0, rms * self._LEVEL_GAIN))
        self._broadcast_rpc_threadsafe("conversation.level", {"role": role, "rms": round(level, 3)})

    # Map backend activity reasons to the orb's turn states (mirrors the old
    # browser orb's mapActivityToState so the orb reliably reaches listening/
    # thinking/speaking across the reasons the HF handler actually emits).
    # Transcript text is delivered separately via the transcript observer.
    _REASON_TO_TURN = {
        "user_speech_started": "listening",
        "user_transcription_delta": "listening",
        "user_speech_stopped": "thinking",
        "user_transcription_completed": "thinking",
        "response_created": "thinking",
        "tool_call_received": "thinking",
        "tool_result_ready": "thinking",
        "assistant_audio_delta": "speaking",
        "assistant_transcript_done": "ready",
    }

    def _dispatch_activity(self, reason: str) -> None:
        """Fan one activity reason out to JSON-RPC clients."""
        if self._rpc is not None:
            # Raw reason (the browser orb maps it exactly like the old SSE feed)...
            self._broadcast_rpc_threadsafe("conversation.activity", {"reason": reason})
            # ...plus a semantic turn state for clients without that mapping (mobile).
            state = self._REASON_TO_TURN.get(reason)
            if state and state != self._last_turn_state:
                self._last_turn_state = state
                self._broadcast_rpc_threadsafe("conversation.turn", {"state": state})

    def _emit_phase(self, phase: str, reason: Optional[str] = None) -> None:
        """Push a conversation.phase notification to JSON-RPC clients."""
        self._broadcast_rpc_threadsafe("conversation.phase", {"phase": phase, "reason": reason})

    def seconds_since_activity(self) -> float:
        """Seconds since the live handler last saw conversation activity."""
        if self._hybrid_runtime is not None:
            return self._hybrid_runtime.seconds_since_activity()
        return time.monotonic() - float(self.handler.last_activity_time)

    def _read_env_lines(self, env_path: Path) -> list[str]:
        """Load env file contents or a template as a list of lines."""
        inst = env_path.parent
        try:
            if env_path.exists():
                try:
                    return env_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return []
            template_text = None
            ex = inst / ".env.example"
            if ex.exists():
                try:
                    template_text = ex.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                try:
                    cwd_example = Path.cwd() / ".env.example"
                    if cwd_example.exists():
                        template_text = cwd_example.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                packaged = Path(__file__).parent / ".env.example"
                if packaged.exists():
                    try:
                        template_text = packaged.read_text(encoding="utf-8")
                    except Exception:
                        template_text = None
            return template_text.splitlines() if template_text else []
        except Exception:
            return []

    def _backend_connected(self) -> bool:
        """Return whether the active handler currently has a realtime connection."""
        try:
            if self._hybrid_runtime is not None:
                return self._hybrid_runtime._is_connected()
            checker = getattr(self.handler, "_is_connected", None)
            return bool(checker()) if callable(checker) else False
        except Exception as exc:
            logger.debug("Failed to read backend connection state: %s", self._format_backend_error(exc))
            return False

    def _handler_has_resumption_handle(self) -> bool:
        """Return whether the current handler can resume its provider session."""
        return isinstance(getattr(self.handler, "resumption_handle", None), str)

    def _can_rebuild_handler(self) -> bool:
        """Return whether LocalStream can construct handlers for backend changes."""
        return self._hybrid_runtime is None and self._handler_factory is not None

    def _build_handler_for_current_backend(self) -> ConversationHandler | HybridRuntime:
        """Create and install a fresh handler for the current runtime backend config."""
        if self._hybrid_runtime is not None:
            return self._hybrid_runtime
        if self._handler_factory is None:
            if isinstance(self.handler, ConversationHandler):
                return self.handler
            raise RuntimeError("No handler factory is configured")
        previous_handle = getattr(self.handler, "resumption_handle", None)
        handler = self._handler_factory(self._voice_override)
        set_resumption_handle = getattr(handler, "set_resumption_handle", None)
        recap = self._conversation_recap.render(config.LOCAL_CONTEXT_MAX_CHARS)
        if self._continue_after_provider_end and isinstance(previous_handle, str) and callable(set_resumption_handle):
            # Resume the provider session itself: full context, no greeting.
            handler.set_startup_greeting_enabled(False)
            set_resumption_handle(previous_handle)
        elif self._continue_after_provider_end and recap:
            # Continue the same conversation: no fresh greeting, and seed recent turns as context.
            handler.set_startup_greeting_enabled(False)
            handler.set_initial_context(recap)
        self._continue_after_provider_end = False
        self._install_handler(handler)
        return handler

    async def _shutdown_active_handler(self) -> bool:
        """Best-effort shutdown for the currently active handler."""
        if self._hybrid_runtime is not None:
            try:
                result = await self._hybrid_runtime.deactivate_cloud("backend_restart")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                formatted_error = self._format_backend_error(e)
                self._set_backend_connection_state("stopping", formatted_error)
                logger.debug(
                    "Hybrid provider deactivation ignored during restart: %s",
                    formatted_error,
                )
                return False
            status = self._hybrid_runtime.status
            if status.mode is RuntimeMode.STOPPING:
                self._set_backend_connection_state(
                    "stopping", status.shutdown_error or "Provider cleanup is in progress."
                )
                return False
            if status.mode is RuntimeMode.LOCAL_AMBIENT:
                self._set_backend_connection_state("ambient")
                return not status.active and not status.connected and status.shutdown_error is None
            return bool(result)
        if self._direct_shutdown_blocked:
            self._set_backend_connection_state("stopping", self._backend_error or "Provider cleanup is incomplete.")
            return False
        try:
            await self.handler.shutdown()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            formatted_error = self._format_backend_error(e)
            self._direct_shutdown_blocked = True
            self._set_backend_connection_state("stopping", formatted_error)
            logger.warning("Active handler shutdown failed during restart: %s", formatted_error)
            return False
        return True

    def _mark_restart_requested(self, reason: str) -> None:
        """Request a backend restart from a synchronous route handler."""
        logger.info("Backend restart requested: %s", reason)
        self._set_backend_connection_state("connecting")
        loop = self._asyncio_loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.request_backend_restart(reason), loop)
            return
        self._restart_requested.set()

    async def request_backend_restart(self, reason: str) -> None:
        """Ask the startup loop to rebuild the backend and stop the current handler."""
        self._set_backend_connection_state("connecting")
        self._restart_requested.set()
        if not await self._shutdown_active_handler():
            self._set_backend_connection_state("stopping", self._backend_error or "Provider cleanup is incomplete.")

    async def _sleep_or_restart_requested(self, delay: float) -> None:
        """Sleep for a retry interval, waking early if a restart is requested."""
        if self._restart_requested.is_set():
            return
        try:
            await asyncio.wait_for(self._restart_requested.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _format_backend_error(error: BaseException | str) -> str:
        """Return a compact backend error string with credentials redacted."""
        if isinstance(error, str):
            message = error.strip()
        else:
            detail = str(error).strip()
            message = f"{type(error).__name__}: {detail}" if detail else type(error).__name__
        for configured_key in (config.GEMINI_API_KEY, config.OPENAI_API_KEY):
            if isinstance(configured_key, str) and configured_key.strip():
                message = message.replace(configured_key.strip(), "[redacted]")
        return message

    def _set_backend_connection_state(self, state: str, error: BaseException | str | None = None) -> None:
        """Update backend connection status exposed through the settings UI."""
        self._backend_connection_state = state
        if error is not None:
            self._backend_error = self._format_backend_error(error)
        elif state != "disconnected":
            self._backend_error = None

    def _backend_connection_status(self) -> dict[str, object]:
        """Return the backend connection state exposed in the settings API."""
        connected = self._backend_connected()
        state = "connected" if connected else self._backend_connection_state
        return {
            "backend_connected": connected,
            "backend_connection_state": state,
            "backend_error": None if connected else self._backend_error,
        }

    def _persist_env_values(self, updates: dict[str, str]) -> None:
        """Persist non-empty environment values in memory and in the instance `.env`."""
        normalized_updates = {name: (value or "").strip() for name, value in updates.items()}
        normalized_updates = {name: value for name, value in normalized_updates.items() if value}
        if not normalized_updates:
            return

        for env_name, value in normalized_updates.items():
            try:
                os.environ[env_name] = value
            except Exception:
                pass
        refresh_runtime_config_from_env()

        if not self._instance_path:
            return
        try:
            inst = Path(self._instance_path)
            env_path = inst / ".env"
            lines = self._read_env_lines(env_path)
            for env_name, value in normalized_updates.items():
                replaced = False
                for i, ln in enumerate(lines):
                    if ln.strip().startswith(f"{env_name}="):
                        lines[i] = f"{env_name}={value}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"{env_name}={value}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted %s to %s", ", ".join(sorted(normalized_updates)), env_path)

            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path))
            except Exception:
                pass
            refresh_runtime_config_from_env()
        except Exception as e:
            logger.warning("Failed to persist %s: %s", ", ".join(sorted(normalized_updates)), e)

    def _remove_persisted_env_values(self, env_names: tuple[str, ...]) -> None:
        """Remove keys from the instance `.env` without mutating the current runtime."""
        normalized_names = tuple(sorted({name.strip() for name in env_names if name and name.strip()}))
        if not normalized_names or not self._instance_path:
            return

        env_path = Path(self._instance_path) / ".env"
        if not env_path.exists():
            return

        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            filtered_lines = [
                line
                for line in lines
                if not any(line.strip().startswith(f"{env_name}=") for env_name in normalized_names)
            ]
            if filtered_lines == lines:
                return

            final_text = "\n".join(filtered_lines)
            if final_text:
                final_text += "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Removed %s from %s", ", ".join(normalized_names), env_path)
        except Exception as e:
            logger.warning("Failed to remove %s: %s", ", ".join(normalized_names), e)

    def _persist_hf_direct_connection(self, host: str, port: int) -> None:
        """Persist a direct Hugging Face websocket target."""
        self._persist_env_values(
            {
                HF_REALTIME_CONNECTION_MODE_ENV: HF_LOCAL_CONNECTION_MODE,
                HF_REALTIME_WS_URL_ENV: build_hf_direct_ws_url(host, port),
            }
        )

    def _persist_hf_allocator_connection(self) -> None:
        """Persist the deployed Hugging Face allocator mode."""
        self._persist_env_values({HF_REALTIME_CONNECTION_MODE_ENV: HF_DEPLOYED_CONNECTION_MODE})
        self._remove_persisted_env_values(("HF_REALTIME_SESSION_URL",))

    def _persist_realtime_provider(self, provider: str) -> None:
        """Persist the selected realtime provider without accepting credentials."""
        self._persist_env_values({REALTIME_PROVIDER_ENV: provider})

    def _persist_personality(self, profile: Optional[str], voice_override: Optional[str] = None) -> None:
        """Persist startup profile and voice in instance-local UI settings."""
        if LOCKED_PROFILE is not None:
            return
        selection = (profile or "").strip() or None
        normalized_voice_override = (voice_override or "").strip() or None
        set_custom_profile(selection)

        if not self._instance_path:
            return
        try:
            write_startup_settings(
                self._instance_path,
                profile=selection,
                voice=normalized_voice_override,
            )
            self._remove_persisted_env_values(LEGACY_STARTUP_ENV_NAMES)
            logger.info("Persisted startup personality settings to %s", Path(self._instance_path))
        except Exception as e:
            logger.warning("Failed to persist startup personality settings: %s", e)

    def _read_persisted_personality(self) -> Optional[str]:
        """Read the saved startup personality from instance-local UI settings."""
        return read_startup_settings(self._instance_path).profile

    async def apply_personality(self, profile: Optional[str]) -> str:
        """Apply a personality by updating config and restarting the active backend."""
        previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
        set_custom_profile(profile)
        try:
            get_session_instructions()
            get_session_voice(default=get_default_voice())
            initialize_tools(force=True)
        except Exception:
            set_custom_profile(previous_profile)
            raise

        if self._hybrid_runtime is not None:
            await self.request_backend_restart("personality_changed")
            return "Applied personality. It will take effect on the next activation."
        await self.request_backend_restart("personality_changed")
        return "Applied personality and restarting backend."

    async def get_available_voices(self) -> list[str]:
        """Return voices advertised by the active backend."""
        handler = self.handler
        if self._hybrid_runtime is not None:
            handler = self._hybrid_runtime.active_handler
            if handler is None:
                return get_available_voices() if get_realtime_provider() == HF_BACKEND else []
        try:
            voices = await handler.get_available_voices()
        except Exception as exc:
            logger.warning(
                "Failed to read available voices from %s: %s",
                get_realtime_provider(),
                self._format_backend_error(exc),
            )
            return get_available_voices() if get_realtime_provider() == HF_BACKEND else []
        return voices if isinstance(voices, list) else []

    def get_current_voice(self) -> str:
        """Return the voice currently selected by the active backend."""
        handler = self.handler
        if self._hybrid_runtime is not None:
            handler = self._hybrid_runtime.active_handler
            if handler is None:
                return self._voice_override or (get_default_voice() if get_realtime_provider() == HF_BACKEND else "")
        try:
            voice = handler.get_current_voice()
        except Exception as exc:
            logger.warning(
                "Failed to resolve the current %s voice: %s",
                get_realtime_provider(),
                self._format_backend_error(exc),
            )
        else:
            if isinstance(voice, str) and voice.strip():
                return voice

        if self._voice_override:
            return self._voice_override
        try:
            fallback = get_default_voice() if get_realtime_provider() == HF_BACKEND else ""
            return get_session_voice(default=fallback)
        except Exception as exc:
            logger.warning("Failed to resolve the current profile voice: %s", self._format_backend_error(exc))
            return get_default_voice() if get_realtime_provider() == HF_BACKEND else ""

    def get_startup_voice(self) -> Optional[str]:
        """Return the voice override that the next provider generation should use."""
        return self._voice_override

    async def change_voice(self, voice: str) -> str:
        """Change the voice through the active handler without rebuilding the backend."""
        if self._hybrid_runtime is not None and self._hybrid_runtime.active_handler is None:
            self._voice_override = voice.strip()
            if self._voice_override:
                self._persist_voice_override(self._voice_override)
            return "Voice saved. It will take effect on the next activation."
        handler = self.handler
        if self._hybrid_runtime is not None:
            handler = self._hybrid_runtime.active_handler
            if handler is None:
                return "Voice will take effect on the next activation."
        try:
            status = await handler.change_voice(voice)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            formatted_error = self._format_backend_error(e)
            logger.error("Error changing voice to %r: %s", voice, formatted_error)
            return f"Failed to change voice: {formatted_error}"

        try:
            current_voice = handler.get_current_voice()
            if isinstance(current_voice, str) and current_voice.strip():
                self._voice_override = current_voice
        except Exception as e:
            logger.debug("Could not sync LocalStream voice override after voice change: %s", e)
        if self._voice_override:
            self._persist_voice_override(self._voice_override)
        return str(status)

    def _persist_voice_override(self, voice: str) -> None:
        """Persist the chosen voice as the startup voice, keeping the startup profile."""
        if not self._instance_path:
            return
        try:
            existing = read_startup_settings(self._instance_path)
            write_startup_settings(self._instance_path, profile=existing.profile, voice=voice)
        except Exception as e:
            logger.warning("Failed to persist startup voice: %s", e)

    def _hybrid_status_payload(self) -> dict[str, object]:
        """Return state and privacy counters from the optional hybrid runtime."""
        runtime = self._hybrid_runtime
        if runtime is None:
            return {
                "hybrid_runtime_enabled": False,
                "hybrid_state": None,
                "hybrid_generation": 0,
                "ambient_audio_seconds_buffered": 0.0,
                "ambient_audio_bytes_sent_to_cloud": 0,
                "cloud_audio_bytes_sent": 0,
                "cloud_video_frames_sent": 0,
                "gemini_session_duration": 0.0,
                "postactivation_frames_dropped": 0,
                "local_command_last_outcome": None,
                "local_command_last_router_reason": None,
                "local_command_router_rejections": 0,
                "local_command_followup_timeouts": 0,
                "local_processing_frames_dropped": 0,
            }

        status = runtime.status
        metrics = runtime.metrics

        mode_value = status.mode.value
        generation = status.generation
        status_payload = status()
        counters = {
            "ambient_audio_seconds_buffered": metrics.ambient_audio_seconds_buffered,
            "ambient_audio_bytes_sent_to_cloud": metrics.ambient_audio_bytes_sent_to_cloud,
            "cloud_audio_bytes_sent": metrics.cloud_audio_bytes_sent,
            "cloud_video_frames_sent": metrics.cloud_video_frames_sent,
            "gemini_session_duration": metrics.gemini_session_duration,
            "postactivation_frames_dropped": status.postactivation_frames_dropped,
            "local_processing_frames_dropped": status.local_processing_frames_dropped,
        }
        return {
            "hybrid_runtime_enabled": True,
            "hybrid_state": mode_value,
            "hybrid_generation": generation,
            **status_payload,
            **counters,
            "hybrid_metrics": counters,
        }

    def _init_settings_ui_if_needed(self) -> None:
        """Attach minimal settings UI to the settings app.

        Always mounts the UI when a settings_app is provided so that users
        see a confirmation message even if the API key is already configured.
        """
        if self._settings_initialized:
            return
        if self._settings_app is None:
            return
        settings_app = self._settings_app

        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        logger.info("Serving settings UI from %s", static_dir)

        # Framework pre-registers GET / and /static; strip them so our routes aren't shadowed.
        _detach_framework_root_routes(settings_app)

        if hasattr(settings_app, "mount"):
            try:
                settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception:
                logger.exception("Failed to mount settings UI static assets")
                raise

        def _status_payload() -> dict[str, object]:
            realtime_provider = get_realtime_provider()
            try:
                hf_session_url = get_hf_session_url()
                hf_ws_url = get_hf_direct_ws_url()
                hf_direct_host, hf_direct_port = parse_hf_direct_target(hf_ws_url)
                hf_connection_selection = get_hf_connection_selection()
                hf_connection_mode = hf_connection_selection.mode
                has_hf_connection = hf_connection_selection.has_target
            except Exception as exc:
                logger.warning(
                    "Failed to resolve Hugging Face settings for %s status: %s",
                    realtime_provider,
                    self._format_backend_error(exc),
                )
                hf_session_url = None
                hf_ws_url = None
                hf_direct_host = None
                hf_direct_port = None
                hf_connection_mode = None
                has_hf_connection = False
            has_active_provider_target = has_realtime_target()
            backend_connection = self._backend_connection_status()
            hybrid_status = self._hybrid_status_payload()
            hybrid_enabled = self._hybrid_runtime is not None
            return {
                "backend": realtime_provider,
                "realtime_provider": realtime_provider,
                "has_key": has_active_provider_target,
                "has_realtime_target": has_active_provider_target,
                "has_hf_session_url": bool(hf_session_url),
                "has_hf_ws_url": bool(hf_ws_url),
                "has_hf_connection": has_hf_connection,
                "hf_connection_mode": hf_connection_mode,
                "hf_direct_host": hf_direct_host,
                "hf_direct_port": hf_direct_port,
                "can_proceed": hybrid_enabled or has_active_provider_target,
                "can_proceed_with_hf": has_hf_connection,
                "can_proceed_with_gemini": has_active_provider_target
                if realtime_provider == GEMINI_BACKEND
                else False,
                "can_proceed_with_openai_live": has_active_provider_target
                if realtime_provider == OPENAI_LIVE_BACKEND
                else False,
                "missing_config": None
                if hybrid_enabled or has_active_provider_target
                else get_realtime_missing_config_message(),
                "requires_restart": self._hybrid_runtime is None and not self._can_rebuild_handler(),
                **hybrid_status,
                **backend_connection,
            }

        @settings_app.get(LOCAL_TRANSCRIPT_PREVIEW_PATH)
        async def _transcript_preview(request: Request) -> Response:
            client_host = request.client.host if request.client is not None else ""
            try:
                client_address = ipaddress.ip_address(client_host)
            except ValueError:
                client_address = None
            is_loopback = (isinstance(client_address, ipaddress.IPv4Address) and client_address.is_loopback) or (
                client_address == ipaddress.IPv6Address("::1")
            )
            if not is_loopback:
                return Response(status_code=403, headers={"Cache-Control": "no-store"})
            return JSONResponse(
                content=await self.get_transcript_preview(),
                headers={"Cache-Control": "no-store"},
            )

        def _memory_access_denied(request: Request, *, mutation: bool = False) -> Response | None:
            client_host = request.client.host if request.client is not None else ""
            try:
                local_client = ipaddress.ip_address(client_host).is_loopback
            except ValueError:
                local_client = False
            if not local_client or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return Response(status_code=403, headers={"Cache-Control": "no-store"})
            origin = request.headers.get("origin")
            if (mutation or origin is not None) and origin != f"{request.url.scheme}://{request.url.netloc}":
                return Response(status_code=403, headers={"Cache-Control": "no-store"})
            if not config.HOUSEHOLD_MEMORY_ENABLED:
                return Response(status_code=404, headers={"Cache-Control": "no-store"})
            return None

        @settings_app.get("/api/memory")
        async def _memory_list(request: Request) -> Response:
            denied = _memory_access_denied(request)
            if denied is not None:
                return denied
            try:
                entries = HouseholdMemoryStore(self._instance_path).list_entries(include_inactive=True)
            except MigrationRequiredError:
                return JSONResponse(
                    {"error": "Explicit memory migration is required."},
                    status_code=409,
                    headers={"Cache-Control": "no-store"},
                )
            except MemoryStoreError:
                logger.warning("Memory inspection unavailable")
                return JSONResponse(
                    {"error": "Memory is unavailable."}, status_code=503, headers={"Cache-Control": "no-store"}
                )
            return JSONResponse(
                {
                    "entries": [
                        {
                            "id": entry.id,
                            "text": entry.text,
                            "subject": entry.subject_id,
                            "kind": entry.kind,
                            "certainty": entry.certainty,
                            "status": entry.status,
                            "active_until": entry.active_until.isoformat() if entry.active_until else None,
                        }
                        for entry in entries
                    ]
                },
                headers={"Cache-Control": "no-store"},
            )

        @settings_app.post("/api/memory/forget")
        async def _memory_forget(request: Request) -> Response:
            denied = _memory_access_denied(request, mutation=True)
            if denied is not None:
                return denied
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return Response(status_code=415, headers={"Cache-Control": "no-store"})
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1_024:
                    return Response(status_code=413, headers={"Cache-Control": "no-store"})
            try:
                payload: object = json.loads(body)
            except (ValueError, UnicodeError):
                return Response(status_code=400, headers={"Cache-Control": "no-store"})
            if not isinstance(payload, dict) or set(payload) != {"memory_id"}:
                return Response(status_code=400, headers={"Cache-Control": "no-store"})
            memory_id = payload["memory_id"]
            if not isinstance(memory_id, str) or not memory_id.strip() or len(memory_id) > 120:
                return Response(status_code=400, headers={"Cache-Control": "no-store"})

            request_active = threading.Event()
            request_active.set()

            async def _stop_and_forget() -> bool:
                if self._hybrid_runtime is not None:
                    await self._hybrid_runtime.deactivate_cloud("memory_changed")
                    status = self._hybrid_runtime.status
                    if (
                        status.mode is not RuntimeMode.LOCAL_AMBIENT
                        or status.active
                        or status.connected
                        or status.shutdown_error is not None
                    ):
                        raise RuntimeError("conversation shutdown did not complete")
                else:
                    await self.handler.shutdown()
                if not request_active.is_set():
                    raise asyncio.CancelledError
                result = HouseholdMemoryStore(self._instance_path).forget(memory_id=memory_id)
                self._restart_requested.set()
                return result.removed is not None

            operation: asyncio.Future[bool] | None = None
            try:
                loop = self._asyncio_loop
                if loop is not None and loop.is_running() and loop is not asyncio.get_running_loop():
                    coroutine = _stop_and_forget()
                    try:
                        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                    except RuntimeError:
                        coroutine.close()
                        raise
                    operation = asyncio.wrap_future(future)
                else:
                    operation = asyncio.create_task(_stop_and_forget())
                completed, _ = await asyncio.wait({operation}, timeout=_MEMORY_SHUTDOWN_TIMEOUT_S)
                if not completed:
                    raise TimeoutError("memory shutdown timed out")
                removed = operation.result()
            except (MemoryStoreError, TimeoutError, RuntimeError, OSError):
                logger.warning("Memory deletion unavailable")
                return JSONResponse(
                    {"error": "Memory could not be forgotten; retry locally."},
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
            finally:
                request_active.clear()
                if operation is not None and not operation.done():
                    operation.cancel()
            return JSONResponse({"removed": removed}, headers={"Cache-Control": "no-store"})

        # GET / -> index.html
        @settings_app.get("/")
        def _root() -> FileResponse:
            return FileResponse(str(index_file))

        # GET /favicon.ico -> optional, avoid noisy 404s on some browsers
        @settings_app.get("/favicon.ico")
        def _favicon() -> Response:
            return Response(status_code=204)

        # ── JSON-RPC control surface (/rpc) ──────────────────────────────
        # The single wire format both the local browser UI and remote WebRTC
        # clients use (the daemon relays it over the DataChannel). Notifications
        # (conversation.turn/phase/transcript/activity) are pushed from activity.
        rpc = JsonRpcServer()

        # SDK isn't marked py.typed, so mypy sees rpc.method as untyped; safe here.
        @rpc.method("conversation.status")  # type: ignore[untyped-decorator]
        def _rpc_status(_params: dict[str, object]) -> dict[str, object]:
            return _status_payload()

        @rpc.method("conversation.activate")  # type: ignore[untyped-decorator]
        async def _rpc_activate(_params: dict[str, object]) -> dict[str, object]:
            runtime = self._hybrid_runtime
            if runtime is None:
                raise JsonRpcError("hybrid runtime is disabled", reason="hybrid_disabled")
            try:
                resolved_result: object = await self.activate_hybrid("rpc")
            except _HybridLoopUnavailable as error:
                raise JsonRpcError(str(error), reason="loop_unavailable") from error
            except _HybridActivationTimeout as error:
                raise JsonRpcError(str(error), reason="activation_timeout") from error
            if isinstance(resolved_result, dict):
                response = dict(resolved_result)
                response.setdefault("ok", True)
            else:
                response = {"ok": bool(resolved_result)}
            response.update(self._hybrid_status_payload())
            return response

        @rpc.method("conversation.say")  # type: ignore[untyped-decorator]
        async def _rpc_say(params: dict[str, object]) -> dict[str, object]:
            text = str(params.get("text", "")).strip()
            if not text:
                raise JsonRpcError("say requires 'text'", reason="invalid_params", code=-32602)
            if not self._backend_connected():
                raise JsonRpcError("no active session", reason="not_running")
            try:
                await self.say(text)
            except _HybridLoopUnavailable as error:
                raise JsonRpcError(str(error), reason="loop_unavailable") from error
            except (RuntimeError, ValueError) as error:
                raise JsonRpcError(str(error), reason="not_running") from error
            return {"ok": True}

        @rpc.method("conversation.interrupt")  # type: ignore[untyped-decorator]
        async def _rpc_interrupt(_params: dict[str, object]) -> dict[str, object]:
            if not self._backend_connected():
                raise JsonRpcError("no active session", reason="not_running")
            try:
                await self.interrupt()
            except _HybridLoopUnavailable as error:
                raise JsonRpcError(str(error), reason="loop_unavailable") from error
            except RuntimeError as error:
                raise JsonRpcError(str(error), reason="not_running") from error
            self._last_turn_state = "listening"
            self._broadcast_rpc_threadsafe("conversation.turn", {"state": "listening", "reason": "interrupted"})
            return {"ok": True}

        @rpc.method("conversation.mic")  # type: ignore[untyped-decorator]
        def _rpc_mic(params: dict[str, object]) -> dict[str, object]:
            if "muted" in params:
                self._mic_muted = bool(params["muted"])
                logger.info("Microphone %s via /rpc", "muted" if self._mic_muted else "unmuted")
            return {"muted": self._mic_muted}

        @rpc.method("backend.config")  # type: ignore[untyped-decorator]
        def _rpc_backend_config(params: dict[str, object]) -> dict[str, object]:
            if any(
                name in params for name in ("gemini_api_key", "GEMINI_API_KEY", "openai_api_key", "OPENAI_API_KEY")
            ):
                key_provider = (
                    "OpenAI Live" if any(name in params for name in ("openai_api_key", "OPENAI_API_KEY")) else "Gemini"
                )
                raise JsonRpcError(
                    f"{key_provider} API keys must be configured in the app environment.",
                    reason="openai_key_not_editable" if key_provider == "OpenAI Live" else "gemini_key_not_editable",
                    code=-32602,
                )

            configured_provider = get_realtime_provider()
            requested_provider = params.get("provider")
            provider = str(requested_provider or configured_provider).strip().lower()
            if provider == "openai-live":
                provider = OPENAI_LIVE_BACKEND
            if provider not in {HF_BACKEND, GEMINI_BACKEND, OPENAI_LIVE_BACKEND}:
                raise JsonRpcError("invalid realtime provider", reason="invalid_provider", code=-32602)

            if provider in {GEMINI_BACKEND, OPENAI_LIVE_BACKEND}:
                if any(name in params for name in ("hf_mode", "hf_host", "hf_port")):
                    raise JsonRpcError(
                        "Hugging Face connection settings are unavailable while a cloud provider is selected.",
                        reason="hf_settings_unavailable",
                        code=-32602,
                    )
            else:
                hf_selection = get_hf_connection_selection()
                hf_mode = str(params.get("hf_mode") or hf_selection.mode).strip().lower()
                if hf_mode == HF_LOCAL_CONNECTION_MODE:
                    existing_host, existing_port = parse_hf_direct_target(hf_selection.direct_ws_url)
                    host = str(params.get("hf_host") or "").strip() or existing_host or ""
                    if not host:
                        raise JsonRpcError("Hugging Face host required", reason="empty_hf_host", code=-32602)
                    if "://" in host or "/" in host or "?" in host or "#" in host:
                        raise JsonRpcError("invalid Hugging Face host", reason="invalid_hf_host", code=-32602)
                    raw_port = params.get("hf_port")
                    port = int(raw_port) if isinstance(raw_port, (int, float, str)) else (existing_port or 8765)
                    if port < 1 or port > 65535:
                        raise JsonRpcError("invalid Hugging Face port", reason="invalid_hf_port", code=-32602)
                    self._persist_hf_direct_connection(host, port)
                elif hf_mode == HF_DEPLOYED_CONNECTION_MODE:
                    if not bool(get_hf_session_url()):
                        raise JsonRpcError(
                            "missing Hugging Face session url", reason="missing_hf_session_url", code=-32602
                        )
                    self._persist_hf_allocator_connection()
                else:
                    raise JsonRpcError("invalid Hugging Face mode", reason="invalid_hf_mode", code=-32602)

            if requested_provider is not None and provider != configured_provider:
                self._persist_realtime_provider(provider)

            if self._hybrid_runtime is not None:
                message = "Connection saved. It will apply on the next activation."
            elif self._can_rebuild_handler():
                self._mark_restart_requested("backend_config_changed")
                message = "Connection saved. Reconnecting backend."
            else:
                message = "Connection saved. Restart Reachy Mini Conversation from the desktop app to apply it."
            return {"ok": True, "message": message, **_status_payload()}

        rpc.mount(settings_app)
        self._rpc = rpc

        try:
            personality_ops = build_personality_ops(
                self.handler,
                lambda: self._asyncio_loop,
                persist_personality=self._persist_personality,
                get_persisted_personality=self._read_persisted_personality,
                apply_personality=self.apply_personality,
                get_voices=self.get_available_voices,
                get_current_voice=self.get_current_voice,
                change_voice=self.change_voice,
            )
            # personalities.* / voices.* over JSON-RPC — the local UI and remote
            # clients drive personalities the same way, one control surface.
            register_personality_methods(rpc, personality_ops)
        except Exception:
            logger.exception("Failed to register personality methods; the personality UI will be unavailable")

        try:
            register_tool_space_methods(
                rpc,
                lambda: self._asyncio_loop,
                self.request_backend_restart,
                instance_path=self._instance_path,
            )
        except Exception:
            logger.exception("Failed to register Tool Space methods; remote tool settings will be unavailable")

        try:
            register_profile_tool_methods(
                rpc,
                lambda: self._asyncio_loop,
                self.request_backend_restart,
                instance_path=self._instance_path,
            )
        except Exception:
            logger.exception("Failed to register profile tool methods; personality tool settings will be unavailable")

        self._settings_initialized = True

    async def _run_handler_startup_loop(self) -> None:
        """Start the realtime handler and keep settings UI alive after backend failures."""
        if self._hybrid_runtime is not None:
            try:
                await self._start_hybrid_runtime()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                formatted_error = self._format_backend_error(error)
                self._set_backend_connection_state("disconnected", formatted_error)
                logger.warning("Hybrid local runtime failed to start: %s", formatted_error)
                return
            if self._hybrid_runtime.mode is RuntimeMode.STOPPING:
                status = self._hybrid_runtime.status
                self._set_backend_connection_state(
                    "stopping", status.shutdown_error or "Provider cleanup is in progress."
                )
            else:
                self._set_backend_connection_state("ambient")
            await self._stop_event.wait()
            return

        while not self._stop_event.is_set():
            if self._restart_requested.is_set():
                shutdown_contained = await self._shutdown_active_handler()
                if not shutdown_contained:
                    self._restart_requested.clear()
                    self._set_backend_connection_state(
                        "stopping", self._backend_error or "Provider cleanup is incomplete."
                    )
                    return
                if not self._can_rebuild_handler():
                    self._restart_requested.clear()
                    self._set_backend_connection_state("restart_required")
                    await self._sleep_or_restart_requested(0.5)
                    continue
                self._restart_requested.clear()
                try:
                    self._build_handler_for_current_backend()
                except Exception as e:
                    formatted_error = self._format_backend_error(e)
                    self._set_backend_connection_state("disconnected", formatted_error)
                    logger.warning(
                        "Backend handler failed to initialize: %s. Retrying in %.1f seconds.",
                        formatted_error,
                        self._backend_retry_delay,
                    )
                    await self._sleep_or_restart_requested(self._backend_retry_delay)
                    continue

            if not has_realtime_target():
                self._set_backend_connection_state("waiting_for_config", get_realtime_missing_config_message())
                await self._sleep_or_restart_requested(0.5)
                continue

            self._set_backend_connection_state("connecting")
            try:
                await self.handler.start_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                formatted_error = self._format_backend_error(e)
                self._set_backend_connection_state("disconnected", formatted_error)
                resumable = self._can_rebuild_handler() and self._handler_has_resumption_handle()
                if resumable:
                    # A planned close (session time limit, GoAway) that the provider can resume: reconnect at once.
                    logger.info("Backend session closed (%s); resuming it now.", formatted_error)
                else:
                    logger.warning(
                        "Backend failed to start: %s. Settings UI remains available; retrying in %.1f seconds.",
                        formatted_error,
                        self._backend_retry_delay,
                    )
                if self._can_rebuild_handler():
                    # A session the provider closed (e.g. Gemini close code 1000) can leave the handler fenced,
                    # so retry with a fresh handler rather than the same one.
                    if not resumable:
                        await self._sleep_or_restart_requested(self._backend_retry_delay)
                    self._continue_after_provider_end = not self._restart_requested.is_set()
                    self._restart_requested.set()
                    continue
            else:
                if self._stop_event.is_set():
                    return
                self._set_backend_connection_state("disconnected")
                if self._restart_requested.is_set():
                    logger.info("Backend stopped for requested restart.")
                    continue
                if self._can_rebuild_handler() and self._handler_has_resumption_handle():
                    logger.info("Backend session ended; resuming it now.")
                    self._continue_after_provider_end = True
                    self._restart_requested.set()
                    continue
                if self._can_rebuild_handler():
                    # A provider-ended session (e.g. Gemini GoAway at its time limit) fences its handler.
                    logger.info(
                        "Backend session ended. Starting a fresh session in %.1f seconds.", self._backend_retry_delay
                    )
                    await self._sleep_or_restart_requested(self._backend_retry_delay)
                    self._continue_after_provider_end = not self._restart_requested.is_set()
                    self._restart_requested.set()
                    continue
                logger.info(
                    "Backend session ended. Settings UI remains available; retrying in %.1f seconds.",
                    self._backend_retry_delay,
                )

            await self._sleep_or_restart_requested(self._backend_retry_delay)

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        If the selected backend is missing its target, expose a settings UI
        that reports readiness and allows provider/Hugging Face target changes.
        Gemini credentials remain environment-only and are never collected here.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first (covers subsequent runs)
        if self._instance_path:
            try:
                from dotenv import load_dotenv

                env_path = Path(self._instance_path) / ".env"
                if env_path.exists():
                    load_dotenv(dotenv_path=str(env_path), override=True)
                    refresh_runtime_config_from_env()
            except ValueError:
                raise
            except Exception as error:
                logger.debug("Unable to load instance environment: %s", type(error).__name__)

        # Always expose settings UI if a settings app is available
        # (do this AFTER loading the instance .env so status endpoint sees the right value)
        self._init_settings_ui_if_needed()

        # Direct provider mode waits for credentials/targets; hybrid mode can run
        # its local ambient boundary without any cloud configuration.
        if self._hybrid_runtime is None and not has_realtime_target():
            missing_config_message = get_realtime_missing_config_message()
            self._set_backend_connection_state("waiting_for_config", missing_config_message)
            if self._settings_app is None:
                logger.error(
                    "%s Set it in the app .env before starting the %s backend.",
                    missing_config_message,
                    get_realtime_provider(),
                )
                return
            logger.warning(
                "%s Open the app settings page or configure the instance environment.",
                missing_config_message,
            )
            # Poll until a target becomes available.
            try:
                while not self._stop_event.is_set() and not has_realtime_target():
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Interrupted while waiting for %s configuration.", get_realtime_provider())
                return
            if self._stop_event.is_set():
                return
            self._set_backend_connection_state("not_started")

        # Select output after the optional instance .env has been loaded above.
        if self._speaker_output is not None:
            self._speaker_output.close()
        self._speaker_output = self._build_speaker_output()

        # Start media after key is set/available.
        try:
            self._robot.media.start_recording()
            if self._speaker_output is None:
                self._robot.media.start_playing()
            if self._speaker_output is not None:
                self._speaker_output.start()
        except Exception:
            if self._speaker_output is not None:
                self._speaker_output.close()
            try:
                self._robot.media.stop_playing()
            except Exception as error:
                logger.debug("Error stopping playback after startup failure: %s", type(error).__name__)
            try:
                self._robot.media.stop_recording()
            except Exception as error:
                logger.debug("Error stopping recording after startup failure: %s", type(error).__name__)
            raise

        async def runner() -> None:
            # Capture loop for cross-thread personality actions
            loop = asyncio.get_running_loop()
            self._asyncio_loop = loop  # type: ignore[assignment]
            # Connect the backend first so it overlaps the warmup and audio config below.
            handler_task = asyncio.create_task(self._run_handler_startup_loop(), name="realtime-handler")
            self._tasks = [handler_task]
            await asyncio.gather(
                asyncio.sleep(1),  # give the pipelines time to start
                asyncio.to_thread(apply_audio_startup_config, self._robot, logger=logger),
            )
            self._tasks += [
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                if self._hybrid_runtime is not None:
                    await self._close_hybrid_runtime()
                else:
                    # Ensure direct handler connection is closed.
                    await self.handler.shutdown()

        asyncio.run(runner())

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (openai-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Signal the capture worker before stopping the media pipeline. A stuck
        # SDK pull cannot be cancelled, so stop() uses a bounded join and the
        # daemon thread is allowed to finish independently if needed.
        capture_worker = self._capture_worker
        if capture_worker is not None:
            capture_worker.stop()

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug("Error stopping recording (may already be stopped): %s", type(e).__name__)

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug("Error stopping playback (may already be stopped): %s", type(e).__name__)

        speaker_output = self._speaker_output
        if speaker_output is not None:
            try:
                speaker_output.close()
            except Exception as e:
                logger.debug("Error closing app-owned speaker output: %s", type(e).__name__)

        # close() runs on watcher threads, loop-owned state must change on the loop.
        loop = self._asyncio_loop
        if loop is None or not loop.is_running():
            self._stop_event.set()
            return
        loop.call_soon_threadsafe(self._stop_event.set)
        for task in self._tasks:
            if not task.done():
                loop.call_soon_threadsafe(task.cancel)

    def clear_audio_queue(self) -> None:
        """Flush queued playback audio immediately on user barge-in.

        Calls the SDK's ``clear_player()`` on supported backends and drains the
        provider/app queue. macOS LOCAL skips native flushing unless the
        experimental reviewed-SDK override is enabled. Older SDKs fall back
        to ``clear_output_buffer()``.
        """
        logger.info("User intervention: flushing player queue")
        if self._hybrid_runtime is not None:
            self._hybrid_runtime.clear_provider_output()
        if self._speaker_output is not None:
            self._speaker_output.flush()
        else:
            self._clear_robot_playback()
        self._drain_output_queue()
        if self._hybrid_runtime is not None:
            self._hybrid_runtime.set_playback_active(False)
        elif isinstance(self.handler, ConversationHandler):
            self.handler.set_playback_active(False)

    def _clear_robot_playback(self) -> None:
        """Flush SDK playback without recursively entering the hybrid runtime."""
        media = self._robot.media
        if self._speaker_output is not None:
            self._speaker_output.flush()
            return
        self._paced_playback.clear()
        audio = getattr(media, "audio", None)
        if audio is None:
            return

        if (
            sys.platform == "darwin"
            and getattr(media, "backend", None) == LOCAL_PLAYER_BACKEND
            and not config.MACOS_LOCAL_AUDIO_FLUSH_ENABLED
        ):
            if not self._macos_local_flush_warning_logged:
                logger.warning("Skipping native clear_player on macOS LOCAL media; buffered native audio may remain.")
                self._macos_local_flush_warning_logged = True
            return

        if hasattr(audio, "clear_player") and callable(audio.clear_player):
            audio.clear_player()
        elif hasattr(audio, "clear_output_buffer") and callable(audio.clear_output_buffer):
            # Older SDK without clear_player(); best-effort.
            audio.clear_output_buffer()
        # The provider output queue is drained by clear_audio_queue or the
        # hybrid runtime's provider interruption hook.

    def _drain_output_queue(self) -> None:
        """Empty the handler's output queue in place without replacing it."""
        if self._hybrid_runtime is not None:
            self._hybrid_runtime.clear_provider_output()
            return
        else:
            queue = getattr(self.handler, "output_queue", None)
        if queue is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        if getattr(self._robot.media, "backend", None) == MediaBackend.NO_MEDIA:
            logger.info("Media disabled; microphone capture not started.")
            return
        self._audio_diagnostics = (
            AudioDiagnostics(loud_rms_threshold=config.LOCAL_VAD_RMS_THRESHOLD)
            if config.AUDIO_DIAGNOSTICS_ENABLED
            else None
        )
        capture_worker = self._capture_worker
        if capture_worker is None:
            capture_worker = _AudioCaptureWorker(self._robot.media, diagnostics=self._audio_diagnostics)
            self._capture_worker = capture_worker
        capture_worker.start()

        diagnostics_task = (
            asyncio.create_task(self._monitor_audio(capture_worker), name="audio-diagnostics")
            if self._audio_diagnostics is not None
            else None
        )
        logged_sample_rate = False
        try:
            while not self._stop_event.is_set():
                captured_frame = capture_worker.get_nowait()
                if captured_frame is None:
                    failure_type = capture_worker.failure_type
                    if failure_type is not None:
                        self._stop_event.set()
                        raise RuntimeError(f"Audio capture worker failed: {failure_type}")
                    await asyncio.sleep(CAPTURE_POLL_INTERVAL_SECONDS)
                    continue

                if self._audio_diagnostics is not None:
                    self._audio_diagnostics.observe("queue_age", time.monotonic() - captured_frame.captured_at)
                input_sample_rate, audio_frame = captured_frame
                if not logged_sample_rate:
                    logger.debug("Audio recording started at %s Hz", input_sample_rate)
                    logged_sample_rate = True
                if not self._mic_muted:
                    frame = (input_sample_rate, audio_frame)
                    forward_started = time.monotonic()
                    if self._hybrid_runtime is not None:
                        await self._receive_hybrid_frame(frame, captured_frame.capture_epoch)
                    else:
                        await self.handler.receive(frame)
                    if self._audio_diagnostics is not None:
                        self._audio_diagnostics.observe("input_forward", time.monotonic() - forward_started)
                        self._audio_diagnostics.observe_input_level(_channel0_rms(audio_frame))
                    self._emit_level("user", audio_frame)
        finally:
            if diagnostics_task is not None:
                diagnostics_task.cancel()
                await asyncio.gather(diagnostics_task, return_exceptions=True)
            capture_worker.stop()
            if self._audio_diagnostics is not None:
                self._audio_diagnostics.report(
                    captured=capture_worker.sequence,
                    dropped=capture_worker.dropped_frames,
                    server_idle_s=self._server_idle_seconds(),
                )
            self._audio_diagnostics = None
            if self._capture_worker is capture_worker:
                self._capture_worker = None

    async def _monitor_audio(self, capture_worker: _AudioCaptureWorker) -> None:
        diagnostics = self._audio_diagnostics
        if diagnostics is None:
            return
        logger.info(
            "Audio diagnostics enabled backend=%s wobble=%s", get_audio_output_backend(), config.SPEECH_WOBBLE_ENABLED
        )
        last_report = time.monotonic()
        while True:
            scheduled = time.monotonic() + 0.1
            await asyncio.sleep(0.1)
            now = time.monotonic()
            diagnostics.observe("loop_lag", now - scheduled)
            if now - last_report >= 5.0:
                diagnostics.report(
                    captured=capture_worker.sequence,
                    dropped=capture_worker.dropped_frames,
                    server_idle_s=self._server_idle_seconds(),
                )
                last_report = now

    def _server_idle_seconds(self) -> float | None:
        """Return seconds since the direct-mode handler last saw backend activity."""
        if self._hybrid_runtime is not None or not isinstance(self.handler, ConversationHandler):
            return None
        return time.monotonic() - self.handler.last_activity_time

    def _push_playback(self, audio_frame: NDArray[np.float32], sample_rate: int) -> None:
        """Hand one frame to the speaker and record its playback duration."""
        duration_s = audio_frame.shape[0] / sample_rate
        playback_started = time.monotonic()
        if self._speaker_output is not None:
            self._speaker_output.push(audio_frame)
        else:
            self._robot.media.push_audio_sample(audio_frame)
            self._paced_playback_until = max(self._paced_playback_until, playback_started) + duration_s
        if self._audio_diagnostics is not None:
            self._audio_diagnostics.observe("playback_push", time.monotonic() - playback_started)
        if self._hybrid_runtime is not None:
            self._hybrid_runtime.note_playback_queued(duration_s)
        elif isinstance(self.handler, ConversationHandler):
            self.handler.note_playback_queued(duration_s)
        self._emit_level("assistant", audio_frame)

    def _push_due_playback(self) -> float | None:
        """Feed paced SDK audio while less than the lead is queued; return seconds until the next push."""
        while self._paced_playback:
            wait_s = self._paced_playback_until - time.monotonic() - PLAYBACK_LEAD_SECONDS
            if wait_s > 0:
                return wait_s
            audio_frame, sample_rate = self._paced_playback.popleft()
            self._push_playback(audio_frame, sample_rate)
        return None

    async def play_loop(self) -> None:
        """Fetch outputs from the handler and play audio frames.

        SDK playback is paced so only ``PLAYBACK_LEAD_SECONDS`` sits in the native
        pipeline; the rest stays in an app queue that interruptions drop at once.
        """
        while not self._stop_event.is_set():
            handler = self.handler
            next_push_s = self._push_due_playback()
            try:
                handler_output = await asyncio.wait_for(
                    handler.emit(), timeout=0.5 if next_push_s is None else min(0.5, next_push_s)
                )
            except asyncio.TimeoutError:
                continue

            if isinstance(handler_output, AdditionalOutputs):
                logger.debug("Received %d additional output message(s)", len(handler_output.args))

            elif isinstance(handler_output, tuple):
                input_sample_rate, audio_data = handler_output

                # Skip empty audio frames
                if audio_data.size == 0:
                    continue

                # Reshape if needed
                if audio_data.ndim == 2:
                    # channels-last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast and resample to the SDK playback rate when providers differ.
                try:
                    output_sample_rate = (
                        self._speaker_output.sample_rate
                        if self._speaker_output is not None
                        else int(self._robot.media.get_output_audio_samplerate())
                    )
                    audio_frame = resample_audio(
                        audio_to_float32(audio_data),
                        input_sample_rate,
                        output_sample_rate,
                    )
                except (AttributeError, TypeError, ValueError) as exc:
                    logger.warning("Ignoring invalid output audio frame: %s", exc)
                    continue

                if self._speaker_output is not None:
                    self._push_playback(audio_frame, output_sample_rate)
                else:
                    pieces = math.ceil(audio_frame.shape[0] / (output_sample_rate * PLAYBACK_PIECE_SECONDS))
                    self._paced_playback.extend(
                        (piece, output_sample_rate) for piece in np.array_split(audio_frame, max(1, pieces))
                    )
                    self._push_due_playback()

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop
