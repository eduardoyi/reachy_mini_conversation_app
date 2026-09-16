"""Integration tests for OpenAI Live inside the local/cloud runtime."""

import json
import base64
import asyncio
from unittest.mock import MagicMock
from collections.abc import Mapping, Callable

import numpy as np
import pytest

import reachy_mini_conversation_app.openai_live as live_module
from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.openai_live import OPENAI_LIVE_ENDPOINT, OpenAILiveHandler
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.openai_live_backend import OpenAILiveDelegationBackend
from reachy_mini_conversation_app.conversation_handler import AudioFrame


class FakeClock:
    """Controllable clock for the inactivity boundary."""

    def __init__(self) -> None:
        """Start the clock at zero."""
        self.value = 0.0

    def __call__(self) -> float:
        """Return the current fake timestamp."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.value += seconds


class FakeSocket:
    """Small WebSocket fake that gates session readiness and records events."""

    def __init__(self) -> None:
        """Initialize an unopened socket with no server events."""
        self._events: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent_events: list[dict[str, object]] = []
        self.session_start_sent = asyncio.Event()
        self.close_calls = 0
        self.closed = False
        self.received_server_events = 0

    def release_ready(self) -> None:
        """Deliver the server readiness event to the provider."""
        self.push({"type": "session.started"})

    def push(self, event: Mapping[str, object]) -> None:
        """Deliver one server event to the provider."""
        self._events.put_nowait(json.dumps(dict(event)))

    async def send(self, message: str) -> None:
        """Record one client event without making a network request."""
        decoded = json.loads(message)
        if not isinstance(decoded, dict):
            raise AssertionError("client event must be a JSON object")
        event = {str(key): value for key, value in decoded.items()}
        self.sent_events.append(event)
        if event.get("type") == "session.start":
            self.session_start_sent.set()
        elif event.get("type") == "session.close":
            self.push({"type": "session.closed"})

    async def recv(self) -> str:
        """Wait for the next fake server event."""
        event = await self._events.get()
        if event is None:
            raise RuntimeError("fake socket closed")
        self.received_server_events += 1
        return event

    async def close(self) -> None:
        """Close the socket and wake any pending receiver."""
        self.close_calls += 1
        if not self.closed:
            self.closed = True
            self._events.put_nowait(None)


class FakeResponsesResource:
    """Responses surface that fails if a test accidentally reaches the API."""

    async def create(self, **_kwargs: object) -> object:
        """Reject unexpected delegated network work."""
        raise AssertionError("delegation backend must remain unused in this lifecycle test")


class FakeResponsesClient:
    """Injected Responses client with observable shutdown."""

    def __init__(self) -> None:
        """Initialize the fake Responses resource."""
        self.responses = FakeResponsesResource()
        self.close_calls = 0

    async def close(self) -> None:
        """Record client shutdown without contacting OpenAI."""
        self.close_calls += 1


async def wait_until(predicate: Callable[[], bool], *, attempts: int = 200) -> None:
    """Yield until a lifecycle condition becomes true."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("lifecycle condition did not become true")


def media_events(socket: FakeSocket) -> list[dict[str, object]]:
    """Return only audio append events from a fake socket's client traffic."""
    return [event for event in socket.sent_events if event.get("type") == "session.input_audio.append"]


def audio_frame(value: int) -> AudioFrame:
    """Return one deterministic 24 kHz mono PCM frame."""
    return 24_000, np.array([value, -value], dtype=np.int16)


@pytest.mark.asyncio
async def test_openai_live_handler_preserves_hybrid_privacy_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambient audio stays local, then ready sessions receive audio and reconnect cleanly."""
    monkeypatch.setattr(config.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config.config, "OPENAI_LIVE_MODEL", "test-live-model")
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", config.REALTIME_PROVIDER_OPENAI_LIVE)
    monkeypatch.setattr(live_module, "_SESSION_CLOSE_TIMEOUT_S", 0.01)

    dependencies = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    clock = FakeClock()
    sockets = [FakeSocket(), FakeSocket()]
    handlers: list[OpenAILiveHandler] = []
    clients: list[FakeResponsesClient] = []
    connection_calls = 0

    async def connect(uri: str, headers: Mapping[str, str]) -> FakeSocket:
        nonlocal connection_calls
        assert uri == OPENAI_LIVE_ENDPOINT
        assert headers["Authorization"] == "Bearer test-key"
        socket = sockets[connection_calls]
        connection_calls += 1
        return socket

    def factory() -> OpenAILiveHandler:
        client = FakeResponsesClient()
        backend = OpenAILiveDelegationBackend(dependencies, client=client)
        handler = OpenAILiveHandler(
            dependencies,
            startup_voice="marin",
            websocket_connect=connect,
            delegation_backend=backend,
        )
        handlers.append(handler)
        clients.append(client)
        return handler

    runtime = HybridRuntime(
        factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1, sample_rate=24_000),
        inactivity_timeout_s=5,
        monitor_interval_s=60,
        clock=clock,
    )

    await runtime.handle_frame(audio_frame(1))
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert handlers == []
    assert connection_calls == 0
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert runtime.metrics.cloud_audio_bytes_sent == 0

    assert await runtime.activate("keyboard")
    await wait_until(lambda: connection_calls == 1 and sockets[0].session_start_sent.is_set())
    queued_frame = audio_frame(10)
    await runtime.handle_frame(queued_frame)
    assert media_events(sockets[0]) == []

    sockets[0].release_ready()
    await wait_until(lambda: runtime.status.connected)
    await wait_until(lambda: len(media_events(sockets[0])) == 1)
    first_audio_event = media_events(sockets[0])[0]
    encoded_audio = first_audio_event.get("audio")
    assert isinstance(encoded_audio, str)
    assert base64.b64decode(encoded_audio) == queued_frame[1].astype("<i2", copy=False).tobytes()
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert runtime.metrics.cloud_audio_bytes_sent == queued_frame[1].nbytes
    assert len(handlers) == 1

    clock.advance(4)
    quiet_delta = base64.b64encode(np.array([46, -46], dtype="<i2").tobytes()).decode("ascii")
    for _ in range(4):
        sockets[0].push({"type": "session.output_audio.delta", "delta": quiet_delta})
    await wait_until(lambda: sockets[0].received_server_events >= 5)
    for _ in range(4):
        assert await runtime.emit() is not None
    clock.advance(2)
    assert await runtime.check_inactivity()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.active_handler is None
    assert sockets[0].closed
    assert clients[0].close_calls == 1

    assert await runtime.activate("keyboard")
    await wait_until(lambda: connection_calls == 2 and sockets[1].session_start_sent.is_set())
    sockets[1].release_ready()
    await wait_until(lambda: runtime.status.connected)
    assert len(handlers) == 2
    assert handlers[1] is not handlers[0]

    reactivation_frame = audio_frame(20)
    await runtime.handle_frame(reactivation_frame)
    await wait_until(lambda: len(media_events(sockets[1])) == 1)
    assert runtime.metrics.cloud_audio_bytes_sent == queued_frame[1].nbytes + reactivation_frame[1].nbytes

    await runtime.close()
    await runtime.close()
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert sockets[1].closed
    assert clients[1].close_calls == 1
