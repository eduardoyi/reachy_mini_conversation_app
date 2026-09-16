"""GPT Live household-memory lifecycle tests."""

import json
import asyncio
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Mapping, Callable

import pytest
from test_openai_live import FakeSocket, FakeBackend, _deps, _wait_until
from test_openai_live_backend import (
    _response,
    _tool_spec,
    _FakeClient,
    _text_message,
    _function_call,
)
from test_household_memory_capture import _FakeClient as CaptureClient
from test_household_memory_capture import _proposal_for_request

import reachy_mini_conversation_app.openai_live as live_module
import reachy_mini_conversation_app.openai_live_backend as backend_module
import reachy_mini_conversation_app.household_memory_capture as capture_module
from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.tools import background_tool_manager as background_module
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.openai_live import OPENAI_LIVE_ENDPOINT, OpenAILiveHandler
from reachy_mini_conversation_app.tools.forget import Forget
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.household_memory import MemoryEntry, MemoryStoreError, HouseholdMemoryStore
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.openai_live_backend import OpenAILiveDelegationBackend
from reachy_mini_conversation_app.household_memory_capture import CaptureStats, CaptureClocks, MemoryCapture
from reachy_mini_conversation_app.household_memory_session import (
    MemorySession,
    ReviewFactory,
    CaptureFactory,
    MemorySessionCloseError,
)
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


class FakeCapture:
    """Synchronous fragment sink with observable lifecycle ordering."""

    def __init__(self, events: list[str]) -> None:
        """Initialize an empty fragment sink."""
        self.events = events
        self.fragments: list[tuple[str, str, int, int]] = []
        self.started = False
        self.closed = False
        self.mutation_generation: str | None = None
        self.on_invalidate: Callable[[], None] | None = None

    @property
    def stats(self) -> CaptureStats:
        """Return numeric fake capture counters for provider logging tests."""
        return CaptureStats(
            fragments_received=len(self.fragments),
            fragments_dropped=0,
            requests_attempted=0,
            requests_failed=0,
            proposals_applied=0,
            incomplete_batches=0,
            stale_batches=0,
            budget_blocked=0,
        )

    async def start(self) -> None:
        """Mark the worker active."""
        self.events.append("capture.start")
        self.started = True

    def bind_mutation_generation(self, generation: str) -> None:
        """Record the activation generation used for store writes."""
        self.mutation_generation = generation

    def add_fragment(self, role: str, text: str, start_ms: int, end_ms: int) -> None:
        """Record one active-session fragment."""
        if self.closed:
            raise AssertionError("fragment arrived after capture close")
        self.fragments.append((role, text, start_ms, end_ms))

    def invalidate(self) -> None:
        """Record a pending-work invalidation."""
        self.events.append("capture.invalidate")
        if self.on_invalidate is not None:
            self.on_invalidate()

    async def close(self) -> None:
        """Stop accepting fragments."""
        self.events.append("capture.close")
        self.closed = True


class BlockingCloseCapture(FakeCapture):
    """Capture fake that keeps invalidation cleanup in flight."""

    def __init__(self, events: list[str]) -> None:
        """Initialize a close gate."""
        super().__init__(events)
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        """Wait until the test releases capture cleanup."""
        self.events.append("capture.close")
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


class OrderedSocket(FakeSocket):
    """Fake socket that records close ordering with capture and backend."""

    def __init__(self, events: list[str]) -> None:
        """Initialize an ordered fake transport."""
        super().__init__()
        self.events = events

    async def send(self, message: str) -> None:
        """Record close ordering before delegating to the base fake."""
        event_type = json.loads(message)["type"]
        if event_type == "session.close":
            self.events.append("socket.session.close")
        await super().send(message)


class OrderedBackend(FakeBackend):
    """Fake delegation backend that records shutdown ordering."""

    def __init__(self, events: list[str]) -> None:
        """Initialize an ordered fake backend."""
        super().__init__()
        self.events = events

    async def close(self) -> None:
        """Record backend shutdown."""
        self.events.append("backend.close")
        await super().close()


class SnapshotStore:
    """Tiny revisioned store fake for provider invalidation tests."""

    def __init__(self, text: str = "old") -> None:
        """Initialize one existing memory entry."""
        self.revision = 1
        self.text = text
        self.deleted = False

    def snapshot(self) -> SimpleNamespace:
        """Return the current entry snapshot."""
        entry_payload = {"id": "m_1", "text": self.text, "subject_id": "person:alex", "status": "current"}
        entry = SimpleNamespace(
            id="m_1",
            subject_id="person:alex",
            status="current",
            to_json=lambda: entry_payload,
        )
        return SimpleNamespace(revision=self.revision, entries=() if self.deleted else (entry,))

    def active_snapshot(self) -> SimpleNamespace:
        """Return the active entry snapshot used by MemorySession."""
        return self.snapshot()


def _memory_handler(
    socket: FakeSocket,
    capture: FakeCapture | None,
    *,
    backend: FakeBackend | None = None,
    instance_path: str | None = None,
    memory_capture_factory: CaptureFactory | None = None,
    memory_review_factory: ReviewFactory | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> OpenAILiveHandler:
    """Build one provider handler with a fake local capture worker."""
    monkeypatch.setattr(config.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_MODEL", "gpt-live-test")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_VOICE", "marin")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_BACKEND_MODEL", "gpt-backend-test")
    monkeypatch.setattr(live_module.core_tools, "get_tool_specs", lambda: [])

    async def connect(uri: str, headers: Mapping[str, str]) -> FakeSocket:
        assert uri == OPENAI_LIVE_ENDPOINT
        assert headers["Authorization"] == "Bearer test-key"
        return socket

    def factory(_instance_path: str | None) -> FakeCapture:
        if capture is None:
            raise AssertionError("capture factory should not be called while disabled")
        return capture

    handler = OpenAILiveHandler(
        _deps(),
        instance_path=instance_path,
        startup_voice="marin",
        websocket_connect=connect,
        delegation_backend=backend,
        memory_capture_factory=memory_capture_factory or factory,
        memory_review_factory=memory_review_factory,
    )
    handler.set_startup_greeting_enabled(False)
    return handler


async def _start_ready(handler: OpenAILiveHandler, socket: FakeSocket) -> asyncio.Task[None]:
    """Start one fake Live session and release readiness."""
    task = asyncio.create_task(handler.start_up())
    await _wait_until(lambda: any(event.get("type") == "session.start" for event in socket.sent))
    socket.push({"type": "session.started"})
    await _wait_until(handler._is_connected)
    return task


@pytest.mark.asyncio
async def test_capture_starts_after_readiness_and_only_receives_active_deltas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not send ambient seed context or injected speech to the capture worker."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True)
    events: list[str] = []
    capture = FakeCapture(events)
    socket = OrderedSocket(events)
    backend = OrderedBackend(events)
    handler = _memory_handler(socket, capture, backend=backend, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = asyncio.create_task(handler.start_up())
    await _wait_until(lambda: len(socket.sent) == 1)
    assert not capture.started
    socket.push({"type": "session.input_transcript.delta", "delta": "ambient", "start_ms": 1, "end_ms": 2})
    socket.push({"type": "session.started"})
    await _wait_until(handler._is_connected)

    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "active", "start_ms": 10, "end_ms": 20}
    )
    await handler._handle_event(
        {"type": "session.output_transcript.delta", "delta": "answer", "start_ms": 21, "end_ms": 30}
    )
    await handler.say("background command")
    assert capture.fragments == [("user", "active", 10, 20), ("assistant", "answer", 21, 30)]

    await handler.shutdown()
    await startup_task
    assert events.index("capture.close") < events.index("socket.session.close")
    assert events.index("capture.close") < events.index("backend.close")
    before = list(capture.fragments)
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "late", "start_ms": 31, "end_ms": 40}
    )
    assert capture.fragments == before


@pytest.mark.asyncio
async def test_capture_disabled_keeps_provider_path_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The capture factory is never touched when either opt-in flag is off."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_MODEL", "gpt-live-test")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_VOICE", "marin")
    monkeypatch.setattr(live_module.core_tools, "get_tool_specs", lambda: [])
    socket = FakeSocket()
    calls: list[str] = []

    async def connect(uri: str, headers: Mapping[str, str]) -> FakeSocket:
        del uri, headers
        return socket

    def factory(_instance_path: str | None) -> FakeCapture:
        calls.append("factory")
        raise AssertionError("capture must stay disabled")

    handler = OpenAILiveHandler(
        _deps(), instance_path=str(tmp_path), websocket_connect=connect, memory_capture_factory=factory
    )
    handler.set_startup_greeting_enabled(False)
    startup_task = await _start_ready(handler, socket)
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "active", "start_ms": 1, "end_ms": 2}
    )
    await handler.shutdown()
    await startup_task
    assert calls == []


@pytest.mark.asyncio
async def test_hybrid_deactivation_fences_capture_before_ambient_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cloud teardown fences extraction before the runtime enters ambient mode."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True)
    monkeypatch.setattr(live_module, "_SESSION_CLOSE_TIMEOUT_S", 0.01)
    events: list[str] = []
    capture = FakeCapture(events)
    socket = OrderedSocket(events)
    handler = _memory_handler(socket, capture, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    runtime = HybridRuntime(
        lambda: handler,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=24_000),
        monitor_interval_s=60,
    )
    observed_modes: list[RuntimeMode] = []
    capture.on_invalidate = lambda: observed_modes.append(runtime.mode)

    assert await runtime.activate("keyboard")
    await _wait_until(lambda: any(event.get("type") == "session.start" for event in socket.sent))
    socket.push({"type": "session.started"})
    await _wait_until(lambda: runtime.status.connected)
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "active note.", "start_ms": 1, "end_ms": 2}
    )
    assert capture.fragments

    assert await runtime.deactivate_cloud("test")
    assert observed_modes
    assert observed_modes[0] is RuntimeMode.STOPPING
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.status.shutdown_error is None
    assert capture.closed
    await runtime.close()


@pytest.mark.asyncio
async def test_memory_invalidation_drops_events_while_capture_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction closes the provider before cleanup can admit old output."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True)
    events: list[str] = []
    socket = OrderedSocket(events)
    capture = BlockingCloseCapture(events)
    handler = _memory_handler(socket, capture, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)

    invalidation = asyncio.create_task(handler.invalidate_memory_context())
    await capture.close_started.wait()
    await handler._handle_event({"type": "session.output_audio.delta", "delta": "AAE=", "start_ms": 1, "end_ms": 2})
    assert handler.output_queue.empty()
    capture.release_close.set()
    await invalidation
    await startup_task


@pytest.mark.asyncio
async def test_memory_capture_close_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancellation-resistant capture cannot hold provider shutdown open."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True)
    monkeypatch.setattr(live_module, "_MEMORY_CAPTURE_CLOSE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(live_module, "_MEMORY_REVIEW_CLOSE_TIMEOUT_S", 0.01)
    events: list[str] = []
    socket = OrderedSocket(events)
    capture = BlockingCloseCapture(events)
    handler = _memory_handler(socket, capture, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)

    invalidation = asyncio.create_task(handler.invalidate_memory_context())
    await capture.close_started.wait()
    with pytest.raises(MemorySessionCloseError):
        await asyncio.wait_for(invalidation, timeout=0.5)
    assert handler.connection is None
    capture.release_close.set()
    await startup_task


@pytest.mark.asyncio
async def test_memory_deletion_is_watched_when_capture_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit memory deletions still close a stale Live session without capture."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    events: list[str] = []
    socket = OrderedSocket(events)
    store = SnapshotStore()
    monkeypatch.setattr(live_module, "HouseholdMemoryStore", lambda _path: store)
    handler = _memory_handler(socket, None, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)
    assert handler._memory_session.active
    store.revision = 2
    store.deleted = True
    assert await handler._check_memory_snapshot() is False
    await startup_task
    assert handler.connection is None


@pytest.mark.asyncio
async def test_memory_session_corrupt_snapshot_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A store read failure cannot leave an old memory context authorized."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)

    class CorruptStore:
        path = tmp_path / "memory.json"

        def active_snapshot(self) -> object:
            raise MemoryStoreError("corrupt")

    session = MemorySession(
        str(tmp_path),
        enabled=True,
        store_factory=lambda _path: CorruptStore(),
    )
    session.prepare()

    assert session.context_is_current() is False
    assert session.fenced


@pytest.mark.asyncio
async def test_memory_change_is_checked_before_luna_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deletion seen before delegation prevents the stale backend request."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    socket = FakeSocket()
    backend = FakeBackend()
    store = SnapshotStore()
    monkeypatch.setattr(live_module, "HouseholdMemoryStore", lambda _path: store)
    handler = _memory_handler(socket, None, backend=backend, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "do it", "start_ms": 10, "end_ms": 20}
    )
    store.revision = 2
    store.deleted = True
    await handler._handle_event({"type": "session.delegation.created", "offset_ms": 30, "delegation": {"id": "d-2"}})
    await startup_task
    assert backend.calls == []


@pytest.mark.asyncio
async def test_existing_memory_deletion_closes_live_without_stale_commentary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted stored entry closes the session before a delegated result can speak."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True)
    events: list[str] = []
    capture = FakeCapture(events)
    socket = OrderedSocket(events)
    gate = asyncio.Event()
    backend = OrderedBackend(events)
    backend.gate = gate

    store = SnapshotStore()
    monkeypatch.setattr(live_module, "HouseholdMemoryStore", lambda _path: store)
    handler = _memory_handler(socket, capture, backend=backend, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "do it", "start_ms": 10, "end_ms": 20}
    )
    await handler._handle_event({"type": "session.delegation.created", "offset_ms": 30, "delegation": {"id": "d-1"}})
    await _wait_until(backend.started.is_set)
    store.revision = 2
    store.deleted = True
    assert await handler._check_memory_snapshot() is False
    gate.set()
    await startup_task
    assert [event for event in socket.sent if event.get("type") == "session.commentary.append"] == []
    assert capture.closed
    assert handler.connection is None


@pytest.mark.asyncio
async def test_backend_guard_stops_responses_after_memory_tool_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not issue a second Luna request after a memory-changing tool call."""
    changed = False
    dispatches: list[str] = []
    client = _FakeClient([_response(_function_call("write-1", "write")), _response(_text_message("stale"))])
    manager = BackgroundToolManager()

    async def _dispatch_memory_change(*, tool_name: str, args_json: str, deps: ToolDependencies) -> dict[str, object]:
        nonlocal changed
        del args_json, deps
        assert tool_name == "write"
        dispatches.append(tool_name)
        changed = True
        return {"saved": True}

    monkeypatch.setattr(background_module, "dispatch_tool_call", _dispatch_memory_change)
    monkeypatch.setattr(backend_module, "get_tool_specs", lambda: [_tool_spec("write")])
    backend = OpenAILiveDelegationBackend(
        _deps(),
        client=client,
        tool_manager=manager,
        context_guard=lambda: not changed,
    )
    result = await backend.run("save the memory")
    assert result == "I couldn't complete that request."
    assert dispatches == ["write"]
    assert len(client.responses.calls) == 1
    await backend.close()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_backend_guard_stops_second_tool_after_memory_tool_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not dispatch a later tool from a stale multi-call response."""
    changed = False
    dispatches: list[str] = []
    first_response = _response(
        _function_call("write-1", "write", arguments="{}"),
        _function_call("write-2", "write", arguments="{}"),
    )
    client = _FakeClient([first_response])
    manager = BackgroundToolManager()

    async def _dispatch_memory_change(*, tool_name: str, args_json: str, deps: ToolDependencies) -> dict[str, object]:
        nonlocal changed
        del args_json, deps
        assert tool_name == "write"
        dispatches.append(tool_name)
        changed = True
        return {"saved": True}

    monkeypatch.setattr(background_module, "dispatch_tool_call", _dispatch_memory_change)
    monkeypatch.setattr(backend_module, "get_tool_specs", lambda: [_tool_spec("write")])
    backend = OpenAILiveDelegationBackend(
        _deps(),
        client=client,
        tool_manager=manager,
        context_guard=lambda: not changed,
    )
    result = await backend.run("save the memory")
    assert result == "I couldn't complete that request."
    assert dispatches == ["write"]
    assert len(client.responses.calls) == 1
    await backend.close()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_live_wires_real_capture_to_local_store_without_final_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Active Live transcript deltas reach the real local capture/store boundary."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True)
    monkeypatch.setattr(live_module, "_MEMORY_REVISION_POLL_S", 0.01)
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    capture_client = CaptureClient(_proposal_for_request)
    clocks = CaptureClocks(
        now=lambda: datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        monotonic=lambda: 100.0,
    )

    def factory(instance_path: str | None) -> MemoryCapture:
        assert instance_path == str(tmp_path)
        return MemoryCapture(
            HouseholdMemoryStore(instance_path),
            "gpt-5.6-luna",
            client=capture_client,
            clocks=clocks,
        )

    socket = FakeSocket()
    handler = _memory_handler(
        socket,
        None,
        instance_path=str(tmp_path),
        memory_capture_factory=factory,
        monkeypatch=monkeypatch,
    )
    startup_task = await _start_ready(handler, socket)
    await handler._handle_event(
        {
            "type": "session.input_transcript.delta",
            "delta": "Eduardo likes jazz.",
            "start_ms": 10,
            "end_ms": 20,
        }
    )
    await asyncio.wait_for(capture_client.responses.started.wait(), timeout=1.0)

    entries: list[MemoryEntry] = []
    for _ in range(100):
        entries = list(HouseholdMemoryStore(tmp_path).list_entries())
        if entries:
            break
        await asyncio.sleep(0.01)
    assert len(entries) == 1
    assert entries[0].text == "Eduardo likes jazz"
    assert capture_client.close_count == 0

    await handler.shutdown()
    await startup_task
    assert capture_client.close_count == 1
    await asyncio.sleep(0.02)
    assert len(capture_client.responses.calls) == 1
    assert handler._memory_session.capture is None


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_enabled", [False, True])
async def test_external_memory_correction_closes_live_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture_enabled: bool
) -> None:
    """An unacknowledged same-ID correction fences the live memory context."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", capture_enabled)
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes tea", subject_id="person:alex")
    socket = FakeSocket()
    handler = _memory_handler(socket, FakeCapture([]), instance_path=str(tmp_path), monkeypatch=monkeypatch)
    startup_task = await _start_ready(handler, socket)

    store.update(entry.id, text="Alex likes coffee")
    assert await handler._check_memory_snapshot() is False
    assert not handler._memory_context_is_current()
    await startup_task
    assert handler.connection is None
    await handler._handle_event({"type": "session.output_audio.delta", "delta": "AAE=", "start_ms": 1, "end_ms": 2})
    assert handler.output_queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_enabled", [False, True])
async def test_spoken_forget_preserves_session_and_fences_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture_enabled: bool
) -> None:
    """Spoken deletion keeps speech live without accepting more automatic memory fragments."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", capture_enabled)
    store = HouseholdMemoryStore(tmp_path)
    first = store.remember("Alex likes tea", subject_id="person:alex")
    second = store.remember("Alex likes jazz", subject_id="person:alex")
    socket = FakeSocket()
    capture = FakeCapture([])
    handler = _memory_handler(socket, capture, instance_path=str(tmp_path), monkeypatch=monkeypatch)
    handler.deps.instance_path = tmp_path
    startup_task = await _start_ready(handler, socket)

    result = await Forget()(handler.deps, memory_id=first.id)
    assert result["memory_id"] == first.id
    assert [entry.id for entry in store.snapshot().entries] == [second.id]
    assert await handler._check_memory_snapshot() is True
    assert handler._memory_context_is_current()
    assert handler.connection is socket
    await handler._handle_event(
        {"type": "session.input_transcript.delta", "delta": "Alex likes tea", "start_ms": 1, "end_ms": 2}
    )
    assert capture.fragments == []
    if capture_enabled:
        assert "capture.invalidate" in capture.events
    await handler._handle_event({"type": "session.output_audio.delta", "delta": "AAE=", "start_ms": 1, "end_ms": 2})
    assert not handler.output_queue.empty()

    store.forget(memory_id=second.id)
    assert await handler._check_memory_snapshot() is False
    await startup_task
    assert handler.connection is None
