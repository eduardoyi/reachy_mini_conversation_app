"""Deterministic GPT Live provider tests."""

import json
import base64
import asyncio
from unittest.mock import MagicMock
from collections.abc import Mapping, Callable

import numpy as np
import pytest

from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.openai_live import OPENAI_LIVE_ENDPOINT, OpenAILiveHandler
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.openai_live_backend import TranscriptTurn
from reachy_mini_conversation_app.conversation_handler import AudioFrame


class FakeSocket:
    """Small asynchronous WebSocket fake with explicit server events."""

    def __init__(self, *, close_on_session_close: bool = True) -> None:
        """Initialize a fake transport."""
        self._events: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.close_calls = 0
        self.close_on_session_close = close_on_session_close
        self.fail_send_type: str | None = None

    def push(self, event: Mapping[str, object]) -> None:
        """Deliver one server event to the provider."""
        self._events.put_nowait(dict(event))

    async def send(self, message: str) -> None:
        """Record one client JSON event."""
        event = json.loads(message)
        assert isinstance(event, dict)
        self.sent.append(event)
        if event.get("type") == self.fail_send_type:
            raise OSError("private transport failure")
        if self.close_on_session_close and event.get("type") == "session.close":
            self.push({"type": "session.closed"})

    async def recv(self) -> str:
        """Return the next server event."""
        event = await self._events.get()
        if event is None:
            raise RuntimeError("fake socket closed")
        return json.dumps(event)

    async def close(self) -> None:
        """Close the fake transport and wake blocked readers."""
        self.close_calls += 1
        if not self.closed:
            self.closed = True
            self._events.put_nowait(None)


class FakeBackend:
    """Bounded delegation backend fake."""

    def __init__(self, result: str = "backend answer", *, gate: asyncio.Event | None = None) -> None:
        """Initialize a backend result and optional wait gate."""
        self.result = result
        self.gate = gate
        self.calls: list[tuple[str, tuple[TranscriptTurn, ...]]] = []
        self.started = asyncio.Event()
        self.close_calls = 0

    async def run(self, task_text: str, transcript_context: tuple[TranscriptTurn, ...] = ()) -> str:
        """Record one task and return the configured result."""
        self.calls.append((task_text, transcript_context))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.result

    async def close(self) -> None:
        """Record backend shutdown."""
        self.close_calls += 1


class FailOnceCloseBackend(FakeBackend):
    """Fail the first close call so provider retry behavior is observable."""

    async def close(self) -> None:
        """Raise once, then close successfully."""
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("private close failure")


def _deps() -> ToolDependencies:
    """Build dependencies with observable movement calls."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


async def _wait_until(predicate: Callable[[], bool], *, attempts: int = 200) -> None:
    """Yield until a fake transport condition is true."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


def _handler(
    socket: FakeSocket,
    backend: FakeBackend | None = None,
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> OpenAILiveHandler:
    """Build a handler connected to one fake socket."""
    monkeypatch.setattr(config.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_MODEL", "gpt-live-test")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_VOICE", "marin")
    monkeypatch.setattr("reachy_mini_conversation_app.openai_live.core_tools.get_tool_specs", lambda: [])

    async def connect(uri: str, headers: Mapping[str, str]) -> FakeSocket:
        assert uri == OPENAI_LIVE_ENDPOINT
        assert headers["Authorization"] == "Bearer test-key"
        return socket

    handler = OpenAILiveHandler(
        _deps(),
        startup_voice="marin",
        websocket_connect=connect,
        delegation_backend=backend,
    )
    handler.set_startup_greeting_enabled(False)
    return handler


async def _start_ready(handler: OpenAILiveHandler, socket: FakeSocket) -> asyncio.Task[None]:
    """Start one fake session and release the documented readiness event."""
    task = asyncio.create_task(handler.start_up())
    await _wait_until(lambda: any(event.get("type") == "session.start" for event in socket.sent))
    socket.push({"type": "session.started"})
    await _wait_until(handler._is_connected)
    return task


async def _stop(handler: OpenAILiveHandler, startup_task: asyncio.Task[None]) -> None:
    """Close a running fake session and collect its reader task."""
    await handler.shutdown()
    await startup_task


@pytest.mark.asyncio
async def test_startup_waits_for_started_and_preserves_24khz_pcm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defer all media until session.started and keep matching-rate PCM exact."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    startup_task = asyncio.create_task(handler.start_up())
    await _wait_until(lambda: len(socket.sent) == 1)

    frame: AudioFrame = (24_000, np.array([10, -10], dtype=np.int16))
    await handler.receive(frame)
    assert [event for event in socket.sent if event["type"] == "session.input_audio.append"] == []

    socket.push({"type": "session.started"})
    await _wait_until(handler._is_connected)
    await handler.receive(frame)
    media = [event for event in socket.sent if event["type"] == "session.input_audio.append"]
    assert len(media) == 1
    assert base64.b64decode(media[0]["audio"]) == frame[1].tobytes()

    start = socket.sent[0]
    assert start["type"] == "session.start"
    session = start["session"]
    assert isinstance(session, dict)
    assert session["model"] == "gpt-live-test"
    assert session["audio"] == {
        "format": {"type": "audio/pcm", "rate": 24_000},
        "output": {"voice": "marin"},
    }
    assert session["delegation"] == {"type": "client"}
    await _stop(handler, startup_task)


@pytest.mark.asyncio
async def test_connected_session_does_not_freeze_local_movement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep antennas and breathing available throughout an active speech session."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)
    try:
        handler.deps.movement_manager.set_listening.assert_called_once_with(False)
        await handler._handle_event({"type": "session.input_transcript.delta", "delta": "hello"})
        await handler._handle_event(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(np.array([2_000, -2_000], dtype="<i2").tobytes()).decode("ascii"),
            }
        )
        handler.deps.movement_manager.set_speaking.assert_called_with(True)
        assert all(not invocation.args[0] for invocation in handler.deps.movement_manager.set_listening.call_args_list)
    finally:
        await _stop(handler, startup_task)
    handler.deps.movement_manager.set_listening.assert_called_with(False)
    handler.deps.movement_manager.set_speaking.assert_called_with(False)


@pytest.mark.asyncio
async def test_resampling_is_chunk_invariant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source buffering makes split microphone frames equal one combined frame."""
    whole_socket = FakeSocket()
    whole_handler = _handler(whole_socket, monkeypatch=monkeypatch)
    whole_handler.connection = whole_socket
    whole_handler._session_ready = True
    source = np.sin(np.arange(1_000, dtype=np.float32) / 11).astype(np.float32)
    await whole_handler.receive((16_000, source))
    whole_audio = base64.b64decode(whole_socket.sent[0]["audio"])

    split_socket = FakeSocket()
    split_handler = _handler(split_socket, monkeypatch=monkeypatch)
    split_handler.connection = split_socket
    split_handler._session_ready = True
    for chunk in np.array_split(source, 7):
        await split_handler.receive((16_000, chunk))
    split_audio = b"".join(base64.b64decode(event["audio"]) for event in split_socket.sent)
    assert split_audio == whole_audio


@pytest.mark.asyncio
async def test_failed_backend_close_is_retried_on_next_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not permanently mark a delegation backend closed after failure."""
    backend = FailOnceCloseBackend()
    handler = _handler(FakeSocket(), backend, monkeypatch=monkeypatch)

    await handler.shutdown()
    assert backend.close_calls == 1
    assert handler._backend_closed is False

    await handler.shutdown()
    assert backend.close_calls == 2
    assert handler._backend_closed is True


@pytest.mark.asyncio
async def test_send_failure_closes_socket_and_blocked_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed media send deactivates the socket and its blocked reader."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)
    socket.fail_send_type = "session.input_audio.append"
    with pytest.raises(RuntimeError, match="transport send failed"):
        await handler.receive((24_000, np.array([1, -1], dtype=np.int16)))
    with pytest.raises(asyncio.CancelledError):
        await startup_task
    assert socket.closed
    assert handler.connection is None


@pytest.mark.asyncio
async def test_shutdown_cancels_blocked_connect_without_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown during deferred connect prevents a late session.started state."""
    connect_started = asyncio.Event()
    connect_cancelled = asyncio.Event()
    socket = FakeSocket()

    async def connect(_uri: str, _headers: Mapping[str, str]) -> FakeSocket:
        connect_started.set()
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            connect_cancelled.set()
            raise
        return socket

    monkeypatch.setattr(config.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("reachy_mini_conversation_app.openai_live.core_tools.get_tool_specs", lambda: [])
    handler = OpenAILiveHandler(_deps(), websocket_connect=connect)
    handler.set_startup_greeting_enabled(False)
    startup_task = asyncio.create_task(handler.start_up())
    await _wait_until(connect_started.is_set)
    await handler.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await startup_task
    assert connect_cancelled.is_set()
    assert not handler._is_connected()
    assert socket.sent == []


@pytest.mark.asyncio
async def test_voice_precedence_prefers_startup_then_profile_then_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Honor explicit startup voice, then profile voice, then configured voice."""
    monkeypatch.setattr(config.config, "OPENAI_LIVE_VOICE", "stone")
    monkeypatch.setattr(
        "reachy_mini_conversation_app.openai_live.get_session_voice",
        lambda default=None: "bossa",
    )
    profile_handler = OpenAILiveHandler(_deps(), websocket_connect=None)
    assert profile_handler.get_current_voice() == "bossa"
    startup_handler = OpenAILiveHandler(_deps(), startup_voice="tempo", websocket_connect=None)
    assert startup_handler.get_current_voice() == "tempo"
    fallback_handler = OpenAILiveHandler(_deps(), startup_voice="unsupported", websocket_connect=None)
    assert fallback_handler.get_current_voice() == "stone"


@pytest.mark.asyncio
async def test_transcript_delegation_uses_offset_and_groups_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use current user speech for the task while retaining grouped context."""
    socket = FakeSocket()
    backend = FakeBackend()
    handler = _handler(socket, backend, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    observed: list[tuple[str, str, bool]] = []
    handler.set_transcript_observer(lambda role, text, final: observed.append((role, text, final)))

    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "What is", "start_ms": 100, "end_ms": 160}
    )
    await handler._handle_event(
        {"type": "session.output_transcript.delta", "delta": "I can help", "start_ms": 180, "end_ms": 220}
    )
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": " Lisbon?", "start_ms": 240, "end_ms": 300}
    )
    await handler._handle_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 350,
            "delegation": {"id": "item_1", "target": "client"},
        }
    )
    await _wait_until(lambda: len(backend.calls) == 1)
    task_text, context = backend.calls[0]
    assert task_text == "What is Lisbon?"
    assert all(isinstance(turn, TranscriptTurn) for turn in context)
    assert [(turn.role, turn.text) for turn in context] == [
        ("user", "What is"),
        ("assistant", "I can help"),
        ("user", " Lisbon?"),
    ]
    result_events = [event for event in socket.sent if event.get("type") == "session.commentary.append"]
    await _wait_until(lambda: len(result_events) == 1)
    assert result_events[0]["delegation_id"] == "item_1"
    assert result_events[0]["content"] == "backend answer"
    assert observed == [
        ("user", "What is", False),
        ("assistant", "I can help", False),
        ("user", " Lisbon?", False),
    ]
    await handler.shutdown()


@pytest.mark.asyncio
async def test_late_unknown_transcript_does_not_replay_old_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait for a transcript after a delegation instead of guessing from old text."""
    socket = FakeSocket()
    backend = FakeBackend()
    handler = _handler(socket, backend, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    await handler._handle_event({"type": "session.input_transcript.delta", "delta": "old context"})
    await handler._handle_event(
        {"type": "session.delegation.created", "offset_ms": 100, "delegation": {"id": "item_2", "target": "client"}}
    )
    await asyncio.sleep(0.05)
    assert backend.calls == []
    await handler._handle_event({"type": "session.input_transcript.delta", "delta": "new task"})
    await _wait_until(lambda: len(backend.calls) == 1)
    assert backend.calls[0][0] == "new task"
    await handler.shutdown()


@pytest.mark.asyncio
async def test_say_delegates_injected_request_and_sends_only_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hybrid activation text goes to the backend before Live commentary."""
    socket = FakeSocket()
    backend = FakeBackend(result="Music is playing.")
    handler = _handler(socket, backend, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    await handler.say("play music")
    assert backend.calls == [("play music", ())]
    commentary = [event for event in socket.sent if event.get("type") == "session.commentary.append"]
    assert commentary == [
        {
            "type": "session.commentary.append",
            "event_id": commentary[0]["event_id"],
            "delegation_id": None,
            "content": "Music is playing.",
        }
    ]
    assert commentary[0]["content"] != "play music"
    await handler.shutdown()


@pytest.mark.asyncio
async def test_delegation_work_and_overflow_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep backend concurrency bounded and tell an overflow request to retry."""
    socket = FakeSocket()
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    handler = _handler(socket, backend, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    for index in range(4):
        await handler._handle_event(
            {
                "type": "session.input_transcript.delta",
                "delta": f"task {index}",
                "start_ms": index * 200,
                "end_ms": index * 200 + 50,
            }
        )
        await handler._handle_event(
            {
                "type": "session.delegation.created",
                "offset_ms": index * 200 + 100,
                "delegation": {"id": f"item_{index}", "target": "client"},
            }
        )
    await _wait_until(lambda: len(backend.calls) == 4)

    await handler._handle_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 900,
            "delegation": {"id": "item_overflow", "target": "client"},
        }
    )
    await _wait_until(
        lambda: any(
            event.get("delegation_id") == "item_overflow"
            for event in socket.sent
            if event.get("type") == "session.commentary.append"
        )
    )
    assert len(handler._delegation_tasks) == 4
    gate.set()
    await _wait_until(lambda: all(task.done() for task in tuple(handler._delegation_tasks)))
    await handler.shutdown()


@pytest.mark.asyncio
async def test_output_audio_handles_split_pcm_and_flushes_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decode ordered output deltas without relying on an undocumented done event."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    await handler._handle_event(
        {"type": "session.output_audio.delta", "delta": base64.b64encode(b"\x01").decode("ascii")}
    )
    await handler._handle_event(
        {"type": "session.output_audio.delta", "delta": base64.b64encode(b"\x02\x00").decode("ascii")}
    )
    await handler._handle_event(
        {"type": "session.output_audio.delta", "delta": base64.b64encode(b"\x00").decode("ascii")}
    )
    first_rate, first_audio = await handler.output_queue.get()
    second_rate, second_audio = await handler.output_queue.get()
    assert first_rate == second_rate == 24_000
    assert first_audio.tolist() == [513]
    assert second_audio.tolist() == [0]
    clearer = MagicMock()
    handler._clear_queue = clearer
    await handler.shutdown()
    clearer.assert_called_once_with()


@pytest.mark.asyncio
async def test_quiet_output_does_not_refresh_activity_or_speaking_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue quiet PCM unchanged while ignoring it for idle and speaking state."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    activities: list[str] = []
    handler.set_activity_observer(activities.append)

    quiet_audio = np.array([46, -46, 46, -46], dtype="<i2")
    await handler._handle_event(
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(quiet_audio.tobytes()).decode("ascii"),
        }
    )
    assert activities == []
    handler.deps.movement_manager.set_speaking.assert_not_called()
    _, queued_audio = await handler.output_queue.get()
    assert queued_audio.tobytes() == quiet_audio.tobytes()

    meaningful_audio = np.array([1_000, -1_000], dtype="<i2")
    await handler._handle_event(
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(meaningful_audio.tobytes()).decode("ascii"),
        }
    )
    assert activities == ["assistant_audio_delta"]
    handler.deps.movement_manager.set_speaking.assert_called_once_with(True)
    _, queued_audio = await handler.output_queue.get()
    assert queued_audio.tobytes() == meaningful_audio.tobytes()
    await handler.shutdown()


@pytest.mark.asyncio
async def test_whitespace_transcript_keeps_context_without_refreshing_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain whitespace transcript fragments for assembly without resetting idle time."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    activities: list[str] = []
    handler.set_activity_observer(activities.append)

    await handler._handle_event({"type": "session.input_transcript.delta", "delta": "  "})
    assert activities == []
    assert handler._transcript_fragments[-1].text == "  "
    await handler._handle_event({"type": "session.input_transcript.delta", "delta": "hello"})
    assert activities == ["user_transcription"]
    await handler.shutdown()


@pytest.mark.asyncio
async def test_error_before_readiness_fails_safely(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail startup on a server error without exposing provider text."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    task = asyncio.create_task(handler.start_up())
    await _wait_until(lambda: len(socket.sent) == 1)
    socket.push({"type": "error", "error": {"code": "invalid_api_key", "message": "secret value"}})
    with pytest.raises(RuntimeError, match="before readiness"):
        await task
    assert socket.closed
    assert "secret value" not in caplog.text
    assert not handler._is_connected()


@pytest.mark.asyncio
async def test_running_error_is_reported_without_server_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A running server error produces a short truthful voice message."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    await handler._handle_event(
        {"type": "error", "error": {"code": "bad_request", "message": "private server detail"}}
    )
    commentary = [event for event in socket.sent if event.get("type") == "session.commentary.append"]
    assert len(commentary) == 1
    assert commentary[0]["delegation_id"] is None
    assert commentary[0]["content"] == "I couldn't complete that request."
    await handler.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_say_and_suppresses_late_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the handler cancels an injected backend task before it can speak."""
    socket = FakeSocket()
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    handler = _handler(socket, backend, monkeypatch=monkeypatch)
    handler.connection = socket
    handler._session_ready = True
    say_task = asyncio.create_task(handler.say("take a photo"))
    await _wait_until(lambda: backend.started.is_set())
    await handler.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await say_task
    assert [event for event in socket.sent if event.get("type") == "session.commentary.append"] == []
    assert backend.close_calls == 1
    assert handler.output_queue.empty()


@pytest.mark.asyncio
async def test_shutdown_reader_consumes_session_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send close and keep the reader alive long enough to consume session.closed."""
    socket = FakeSocket()
    handler = _handler(socket, monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)
    await handler.shutdown()
    await startup_task
    assert any(event.get("type") == "session.close" for event in socket.sent)
    assert socket.closed
    assert not handler._is_connected()
