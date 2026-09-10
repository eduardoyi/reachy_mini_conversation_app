"""Tests for the headless console stream."""

import sys
import time
import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from reachy_mini.media.media_manager import MediaBackend
import reachy_mini_conversation_app.console as console_mod
from reachy_mini_conversation_app.config import (
    HF_AVAILABLE_VOICES,
    AUDIO_OUTPUT_BACKEND_ENV,
    AUDIO_OUTPUT_BACKEND_SDK,
    config,
)
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.console import LOCAL_TRANSCRIPT_PREVIEW_PATH, LocalStream, _AudioCaptureWorker
from reachy_mini_conversation_app.streaming import AdditionalOutputs
from reachy_mini_conversation_app.hybrid_runtime import (
    RuntimeMode,
    HybridRuntime,
    LocalCommandOutcome,
    RuntimeStatusSnapshot,
    TrafficMetricsSnapshot,
)
from reachy_mini_conversation_app.ambient_context import (
    TranscriptSegment,
    AmbientContextConfig,
    AmbientContextStrategy,
    AmbientContextProcessor,
)
from reachy_mini_conversation_app.startup_settings import (
    StartupSettings,
    load_startup_settings_into_runtime,
)
from reachy_mini_conversation_app.personality_routes import (
    RouteError,
    build_personality_ops,
)
from reachy_mini_conversation_app.local_intent_router import IntentDecisionReason


def _rpc_call(app: FastAPI, method: str, params: Any = None) -> dict[str, Any]:
    """Send one JSON-RPC request over /rpc and return the response envelope."""
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}})
        while True:
            response = ws.receive_json()
            if response.get("id") == "1":
                return response


def _transcript_preview_get(app: FastAPI, client_host: str = "127.0.0.1") -> Any:
    """GET the local-only preview endpoint from a chosen client address."""
    with TestClient(app, client=(client_host, 50000)) as client:
        return client.get(LOCAL_TRANSCRIPT_PREVIEW_PATH)


async def _wait_until(predicate: Any, timeout: float = 1.0) -> None:
    """Wait until a test predicate becomes true."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for condition")


class _HybridTestHandler:
    """Provider stub whose lifecycle records the loop that owns it."""

    def __init__(self, *, connect_gate: threading.Event | None = None) -> None:
        self.output_queue: asyncio.Queue[Any] = asyncio.Queue()
        self.started = threading.Event()
        self._connect_gate = connect_gate
        self.start_loop: asyncio.AbstractEventLoop | None = None
        self.shutdown_loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._connected = False
        self.received: list[np.ndarray] = []
        self.say_loop: asyncio.AbstractEventLoop | None = None
        self.say_texts: list[str] = []
        self._activity_observer: Any = None
        self._transcript_observer: Any = None
        self._traffic_observer: Any = None
        self._connection_observer: Any = None
        self.initial_context = ""

    async def start_up(self) -> None:
        self.start_loop = asyncio.get_running_loop()
        if self._connect_gate is not None:
            await asyncio.to_thread(self._connect_gate.wait)
        self._stop_event = asyncio.Event()
        self._connected = True
        self.started.set()
        if self._connection_observer is not None:
            self._connection_observer(True)
        try:
            await self._stop_event.wait()
        finally:
            self._connected = False
            if self._connection_observer is not None:
                self._connection_observer(False)

    async def shutdown(self) -> None:
        self.shutdown_loop = asyncio.get_running_loop()
        if self._stop_event is not None:
            self._stop_event.set()

    def _is_connected(self) -> bool:
        return self._connected

    async def receive(self, frame: tuple[int, np.ndarray]) -> None:
        self.received.append(frame[1].copy())

    async def emit(self) -> None:
        return None

    async def say(self, _text: str) -> None:
        self.say_loop = asyncio.get_running_loop()
        self.say_texts.append(_text)

    def set_activity_observer(self, observer: Any) -> None:
        self._activity_observer = observer

    def set_transcript_observer(self, observer: Any) -> None:
        self._transcript_observer = observer

    def set_traffic_observer(self, observer: Any) -> None:
        self._traffic_observer = observer

    def set_connection_observer(self, observer: Any) -> None:
        self._connection_observer = observer

    def set_startup_greeting_enabled(self, _enabled: bool) -> None:
        return None

    def set_initial_context(self, context: str) -> None:
        self.initial_context = context


def _run_loop_in_thread(loop: asyncio.AbstractEventLoop) -> tuple[threading.Thread, threading.Event]:
    """Run a dedicated event loop and return its thread/readiness signal."""
    ready = threading.Event()

    def run_loop() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()
        loop.close()

    thread = threading.Thread(target=run_loop, name="hybrid-test-loop", daemon=True)
    thread.start()
    assert ready.wait(timeout=1.0)
    return thread, ready


def test_clear_audio_queue_prefers_clear_player() -> None:
    """clear_player() is the canonical flush and is used whenever available."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    handler.output_queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    audio = SimpleNamespace(
        clear_player=MagicMock(),
        clear_output_buffer=MagicMock(),
    )
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.WEBRTC))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    audio.clear_player.assert_called_once()
    audio.clear_output_buffer.assert_not_called()
    assert handler.output_queue.empty()


def test_clear_audio_queue_falls_back_to_output_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Older SDKs without clear_player() still flush via clear_output_buffer()."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    audio = SimpleNamespace(clear_output_buffer=MagicMock())  # no clear_player
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.LOCAL))
    stream = LocalStream(handler, robot)
    monkeypatch.setattr(sys, "platform", "linux")

    stream.clear_audio_queue()

    audio.clear_output_buffer.assert_called_once()
    assert handler.output_queue.empty()


def test_clear_audio_queue_skips_native_flush_on_macos_local_and_drains_queue(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MacOS LOCAL containment preserves app/provider queue draining."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    handler.output_queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    audio = SimpleNamespace(
        clear_player=MagicMock(),
        clear_output_buffer=MagicMock(),
    )
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.LOCAL))
    stream = LocalStream(handler, robot)
    monkeypatch.setattr(sys, "platform", "darwin")

    with caplog.at_level(logging.WARNING, logger=console_mod.logger.name):
        stream.clear_audio_queue()
        stream.clear_audio_queue()

    audio.clear_player.assert_not_called()
    audio.clear_output_buffer.assert_not_called()
    assert handler.output_queue.empty()
    assert sum("Skipping native clear_player" in record.message for record in caplog.records) == 1


def test_clear_audio_queue_flushes_local_backend_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """The LOCAL backend still uses the native flush off macOS."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    audio = SimpleNamespace(clear_player=MagicMock(), clear_output_buffer=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.LOCAL))
    stream = LocalStream(handler, robot)
    monkeypatch.setattr(sys, "platform", "linux")

    stream.clear_audio_queue()

    audio.clear_player.assert_called_once()
    audio.clear_output_buffer.assert_not_called()


def test_clear_audio_queue_flushes_webrtc_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebRTC remains flushable on macOS."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    audio = SimpleNamespace(clear_player=MagicMock(), clear_output_buffer=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.WEBRTC))
    stream = LocalStream(handler, robot)
    monkeypatch.setattr(sys, "platform", "darwin")

    stream.clear_audio_queue()

    audio.clear_player.assert_called_once()
    audio.clear_output_buffer.assert_not_called()


def test_clear_audio_queue_macos_local_drains_active_hybrid_provider_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MacOS LOCAL containment still drains the active hybrid provider queue."""
    provider = MagicMock()
    provider.output_queue = asyncio.Queue()
    provider.output_queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    runtime = HybridRuntime(lambda: MagicMock())
    runtime._active_handler = provider
    audio = SimpleNamespace(clear_player=MagicMock(), clear_output_buffer=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.LOCAL))
    stream = LocalStream(runtime, robot, hybrid_runtime=runtime)
    monkeypatch.setattr(sys, "platform", "darwin")

    stream.clear_audio_queue()

    audio.clear_player.assert_not_called()
    assert provider.output_queue.empty()


def test_clear_audio_queue_drains_queue_in_place() -> None:
    """The output queue is drained in place, not replaced with a new object."""
    handler = MagicMock()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    handler.output_queue = queue
    audio = SimpleNamespace(clear_player=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    assert handler.output_queue is queue  # same object, not replaced
    assert queue.empty()


def test_mic_reports_and_toggles_mute_state_over_rpc() -> None:
    """The mic starts live; conversation.mic exposes and flips the pause state."""
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()

    assert _rpc_call(app, "conversation.mic")["result"] == {"muted": False}
    assert _rpc_call(app, "conversation.mic", {"muted": True})["result"] == {"muted": True}
    assert stream._mic_muted is True
    assert _rpc_call(app, "conversation.mic", {"muted": False})["result"] == {"muted": False}
    assert stream._mic_muted is False

    # headless streams keep the mic live
    assert LocalStream(MagicMock(), robot)._mic_muted is False


def test_hybrid_activation_rpc_rejects_unavailable_local_loop() -> None:
    """The UI loop cannot invoke a hybrid runtime before LocalStream starts."""
    runtime = MagicMock()
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(runtime, robot, settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    response = _rpc_call(app, "conversation.activate")

    assert response["error"]["data"]["reason"] == "loop_unavailable"
    runtime.activate.assert_not_called()


def test_rpc_commands_reject_unavailable_local_loop() -> None:
    """Say and interrupt report a stable error until LocalStream owns a loop."""
    handler = MagicMock()
    handler._is_connected.return_value = True
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()

    for method, params in (("conversation.say", {"text": "hello"}), ("conversation.interrupt", {})):
        response = _rpc_call(app, method, params)
        assert response["error"]["data"]["reason"] == "loop_unavailable"
        handler.say.assert_not_called()


def test_hybrid_activation_rpc_runs_runtime_on_localstream_loop() -> None:
    """RPC activation and cleanup stay on the LocalStream-owned event loop."""
    target_loop = asyncio.new_event_loop()
    thread, _ = _run_loop_in_thread(target_loop)
    providers: list[_HybridTestHandler] = []

    def provider_factory() -> _HybridTestHandler:
        provider = _HybridTestHandler()
        providers.append(provider)
        return provider

    runtime = HybridRuntime(
        provider_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1.0, sample_rate=16_000),
        inactivity_timeout_s=0,
    )
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(runtime, robot, settings_app=app, hybrid_runtime=runtime)
    stream._asyncio_loop = target_loop
    stream._init_settings_ui_if_needed()
    startup_future = asyncio.run_coroutine_threadsafe(runtime.start_up(), target_loop)

    try:
        response = _rpc_call(app, "conversation.activate")

        assert response.get("result", {}).get("ok") is True, response
        assert len(providers) == 1
        provider = providers[0]
        assert provider.started.wait(timeout=1.0)
        assert provider.start_loop is target_loop

        close_future = asyncio.run_coroutine_threadsafe(stream._close_hybrid_runtime(), target_loop)
        close_future.result(timeout=2.0)
        startup_future.result(timeout=2.0)
        assert provider.shutdown_loop is target_loop
        assert runtime.active_handler is None
    finally:
        if not startup_future.done():
            startup_future.cancel()
        target_loop.call_soon_threadsafe(target_loop.stop)
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_hybrid_rpc_say_and_interrupt_run_on_localstream_loop() -> None:
    """Text commands and playback flushing stay on the LocalStream owner loop."""
    target_loop = asyncio.new_event_loop()
    thread, _ = _run_loop_in_thread(target_loop)
    providers: list[_HybridTestHandler] = []
    clear_loops: list[asyncio.AbstractEventLoop] = []

    def provider_factory() -> _HybridTestHandler:
        provider = _HybridTestHandler()
        providers.append(provider)
        return provider

    runtime = HybridRuntime(
        provider_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1.0, sample_rate=16_000),
        inactivity_timeout_s=0,
    )
    app = FastAPI()
    audio = SimpleNamespace(
        clear_player=lambda: clear_loops.append(asyncio.get_running_loop()),
        clear_output_buffer=MagicMock(),
    )
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=None))
    stream = LocalStream(runtime, robot, settings_app=app, hybrid_runtime=runtime)
    stream._asyncio_loop = target_loop
    stream._init_settings_ui_if_needed()
    startup_future = asyncio.run_coroutine_threadsafe(runtime.start_up(), target_loop)

    try:
        activation_response = _rpc_call(app, "conversation.activate")
        assert activation_response.get("result", {}).get("ok") is True, activation_response
        provider = providers[0]
        assert provider.started.wait(timeout=1.0)

        say_response = _rpc_call(app, "conversation.say", {"text": "hello"})
        interrupt_response = _rpc_call(app, "conversation.interrupt")

        assert say_response.get("result") == {"ok": True}
        assert interrupt_response.get("result") == {"ok": True}
        assert provider.say_loop is target_loop
        assert provider.say_texts == ["hello"]
        assert clear_loops and all(loop is target_loop for loop in clear_loops)

        close_future = asyncio.run_coroutine_threadsafe(stream._close_hybrid_runtime(), target_loop)
        close_future.result(timeout=2.0)
        startup_future.result(timeout=2.0)
        assert provider.shutdown_loop is target_loop

        async def runtime_tasks() -> list[str]:
            return [
                task.get_name()
                for task in asyncio.all_tasks()
                if not task.done() and task.get_name().startswith(("hybrid-",))
            ]

        assert asyncio.run_coroutine_threadsafe(runtime_tasks(), target_loop).result(timeout=1.0) == []
    finally:
        if not startup_future.done():
            startup_future.cancel()
        target_loop.call_soon_threadsafe(target_loop.stop)
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_rest_api_is_removed_in_favor_of_rpc() -> None:
    """The /api/v1 REST + SSE surface is gone; control is JSON-RPC over /rpc."""
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()
    client = TestClient(app)

    for path in (
        "/api/v1/status",
        "/api/v1/mic",
        "/api/v1/personalities",
        "/api/v1/voices",
        "/api/v1/tool_spaces",
        "/api/v1/profile_tools",
    ):
        assert client.get(path).status_code == 404
    assert client.get("/api/v1/conversation_events").status_code == 404

    # ...but /rpc drives it fine.
    assert _rpc_call(app, "conversation.status")["result"]["backend"]


def test_settings_ui_detaches_framework_catch_all_before_own_routes() -> None:
    """Framework fallback routes should not shadow the UI or the /rpc endpoint."""
    app = FastAPI()

    @app.get("/{path:path}")
    def _framework_fallback(path: str) -> None:
        raise HTTPException(status_code=404)

    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/static/js/api.js").status_code == 200
    assert _rpc_call(app, "conversation.status")["result"]["backend"]


@pytest.mark.asyncio
async def test_activity_from_rebuilt_handler_reaches_rpc_clients() -> None:
    """Activity from a rebuilt handler must still reach /rpc subscribers."""

    class FakeHandler:
        def __init__(self) -> None:
            self.observer: Any = None

        def set_activity_observer(self, observer: Any) -> None:
            self.observer = observer

    rebuilt = FakeHandler()
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(FakeHandler(), robot, settings_app=app, handler_factory=lambda voice: rebuilt)
    stream._init_settings_ui_if_needed()
    stream._build_handler_for_current_backend()  # rebuild re-wires the observer

    with TestClient(app).websocket_connect("/rpc") as ws:
        rebuilt.observer("assistant_audio_delta")
        # First frame is conversation.activity (raw reason).
        msg = ws.receive_json()
    assert msg["method"] == "conversation.activity"
    assert msg["params"] == {"reason": "assistant_audio_delta"}


def test_backend_config_requests_in_process_restart_with_handler_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild-capable LocalStream should reconnect in process after a connection change."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    monkeypatch.delenv("HF_REALTIME_CONNECTION_MODE", raising=False)
    monkeypatch.delenv("HF_REALTIME_WS_URL", raising=False)

    app = FastAPI()
    handler = MagicMock()
    handler.shutdown = AsyncMock()
    handler._is_connected.return_value = False
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(
        handler,
        robot,
        settings_app=app,
        instance_path=str(tmp_path),
        handler_factory=lambda _voice: handler,
    )
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {"hf_mode": "local", "hf_host": "localhost", "hf_port": 8765})["result"]

    assert data["ok"] is True
    assert data["message"] == "Connection saved. Reconnecting backend."
    assert data["backend"] == "huggingface"
    assert data["requires_restart"] is False
    assert data["can_proceed"] is True
    assert data["backend_connection_state"] == "connecting"
    assert stream._restart_requested.is_set()


def test_backend_config_persists_local_hf_selection_and_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should persist a direct Hugging Face websocket target."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    monkeypatch.delenv("HF_REALTIME_CONNECTION_MODE", raising=False)
    monkeypatch.delenv("HF_REALTIME_SESSION_URL", raising=False)
    monkeypatch.delenv("HF_REALTIME_WS_URL", raising=False)

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {"hf_mode": "local", "hf_host": "localhost", "hf_port": 8765})["result"]

    assert data["ok"] is True
    assert data["backend"] == "huggingface"
    assert data["has_hf_ws_url"] is True
    assert data["has_hf_connection"] is True
    assert data["hf_connection_mode"] == "local"
    assert data["hf_direct_host"] == "localhost"
    assert data["hf_direct_port"] == 8765

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "HF_REALTIME_CONNECTION_MODE=local" in env_text
    assert "HF_REALTIME_WS_URL=ws://localhost:8765/v1/realtime" in env_text


def test_backend_config_persists_deployed_mode_without_clearing_local_hf_ws_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saving deployed mode should make env selection explicit and remove stale allocator URLs."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HF_REALTIME_SESSION_URL=https://lb.example.test/session\n"
        "HF_REALTIME_WS_URL=ws://localhost:8765/v1/realtime\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", "https://lb.example.test/session")
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://localhost:8765/v1/realtime")
    monkeypatch.delenv("HF_REALTIME_CONNECTION_MODE", raising=False)
    monkeypatch.setenv("HF_REALTIME_SESSION_URL", "https://lb.example.test/session")
    monkeypatch.setenv("HF_REALTIME_WS_URL", "ws://localhost:8765/v1/realtime")

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {"hf_mode": "deployed"})["result"]

    assert data["ok"] is True
    assert data["has_hf_session_url"] is True
    assert data["has_hf_ws_url"] is True
    assert data["hf_connection_mode"] == "deployed"

    env_text = env_path.read_text(encoding="utf-8")
    assert "HF_REALTIME_CONNECTION_MODE=deployed" in env_text
    assert "HF_REALTIME_SESSION_URL=" not in env_text
    assert "HF_REALTIME_WS_URL=ws://localhost:8765/v1/realtime" in env_text


def test_backend_config_switches_to_saved_local_hf_connection_without_payload_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching back to a saved local Hugging Face backend should reuse the persisted target."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HF_REALTIME_CONNECTION_MODE=local\nHF_REALTIME_WS_URL=ws://192.168.1.42:8766/v1/realtime\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://192.168.1.42:8766/v1/realtime")
    monkeypatch.setenv("HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setenv("HF_REALTIME_WS_URL", "ws://192.168.1.42:8766/v1/realtime")

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {})["result"]

    assert data["ok"] is True
    assert data["backend"] == "huggingface"
    assert data["hf_connection_mode"] == "local"
    assert data["hf_direct_host"] == "192.168.1.42"
    assert data["hf_direct_port"] == 8766

    env_text = env_path.read_text(encoding="utf-8")
    assert "HF_REALTIME_CONNECTION_MODE=local" in env_text
    assert "HF_REALTIME_WS_URL=ws://192.168.1.42:8766/v1/realtime" in env_text


def test_backend_config_rejects_invalid_hf_port_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should reject invalid local Hugging Face ports from direct callers."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    resp = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": "localhost", "hf_port": 0},
    )

    assert resp["error"]["data"]["reason"] == "invalid_hf_port"


def test_status_reports_direct_hf_ws_url_as_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should treat a direct Hugging Face websocket as a valid configuration."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "conversation.status")["result"]

    assert data["backend"] == "huggingface"
    assert data["has_hf_session_url"] is False
    assert data["has_hf_ws_url"] is True
    assert data["has_hf_connection"] is True
    assert data["hf_connection_mode"] == "local"
    assert data["can_proceed_with_hf"] is True


def test_status_reports_backend_connection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should expose backend connection failures without hiding controls."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    app = FastAPI()
    handler = MagicMock()
    handler.connection = None
    handler._is_connected.return_value = False
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(handler, robot, settings_app=app, instance_path=str(tmp_path))
    stream._set_backend_connection_state("disconnected", RuntimeError("connect failed"))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "conversation.status")["result"]
    assert data["backend"] == "huggingface"
    assert data["backend_connected"] is False
    assert data["backend_connection_state"] == "disconnected"
    assert data["backend_error"] == "RuntimeError: connect failed"
    assert data["can_proceed"] is True
    assert data["can_proceed_with_hf"] is True


def test_backend_startup_failure_is_recorded_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend startup failures should become status state instead of killing LocalStream."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    app = FastAPI()
    handler = MagicMock()
    handler.connection = None
    handler._is_connected.return_value = False
    handler.shutdown = AsyncMock()
    media = SimpleNamespace(
        audio=None,
        backend=None,
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    robot = SimpleNamespace(media=media)
    stream = LocalStream(handler, robot, settings_app=app, instance_path=str(tmp_path))
    stream._backend_retry_delay = 0
    stream.record_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    stream.play_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    monkeypatch.setattr("reachy_mini_conversation_app.console.apply_audio_startup_config", MagicMock())

    async def fail_and_stop() -> None:
        stream._stop_event.set()
        raise RuntimeError("local server unavailable")

    handler.start_up = AsyncMock(side_effect=fail_and_stop)

    try:
        stream.launch()
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())

    handler.start_up.assert_awaited_once()
    data = _rpc_call(app, "conversation.status")["result"]
    assert data["backend_connected"] is False
    assert data["backend_connection_state"] == "disconnected"
    assert data["backend_error"] == "RuntimeError: local server unavailable"


def test_media_warmup_overlaps_audio_startup_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audio configuration should run while the media pipelines warm up."""
    monkeypatch.setattr("reachy_mini_conversation_app.console.has_realtime_target", lambda: True)
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: AUDIO_OUTPUT_BACKEND_SDK)

    handler = MagicMock()
    handler.shutdown = AsyncMock()
    media = SimpleNamespace(
        audio=None,
        backend=None,
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    stream = LocalStream(handler, SimpleNamespace(media=media))
    stream.record_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    stream.play_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

    startup_barrier = threading.Barrier(2)

    async def wait_for_audio_config(_delay: float) -> None:
        await asyncio.to_thread(startup_barrier.wait, 5.0)

    def apply_audio_config(*_args: Any, **_kwargs: Any) -> bool:
        startup_barrier.wait(5.0)
        return True

    async def start_and_stop() -> None:
        stream._stop_event.set()

    handler.start_up = AsyncMock(side_effect=start_and_stop)
    monkeypatch.setattr("reachy_mini_conversation_app.console.asyncio.sleep", wait_for_audio_config)
    monkeypatch.setattr("reachy_mini_conversation_app.console.apply_audio_startup_config", apply_audio_config)

    try:
        stream.launch()
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())

    media.start_recording.assert_called_once_with()
    media.start_playing.assert_called_once_with()


def test_app_speaker_output_skips_sdk_playback_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app-owned path uses the recording pipeline without starting SDK playback."""

    class FakeSpeakerOutput:
        sample_rate = 16_000

        def __init__(self) -> None:
            self.started = 0

        def start(self) -> None:
            self.started += 1

        def push(self, _audio: np.ndarray) -> None:
            return None

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    output = FakeSpeakerOutput()
    monkeypatch.setattr(console_mod, "has_realtime_target", lambda: True)
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: "gstreamer")
    monkeypatch.setattr(console_mod.asyncio, "run", lambda runner: runner.close())
    media = SimpleNamespace(
        get_output_audio_samplerate=MagicMock(return_value=16_000),
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    stream = LocalStream(
        MagicMock(),
        SimpleNamespace(media=media),
        speaker_output_factory=lambda _sample_rate: output,
    )

    stream.launch()

    media.start_recording.assert_called_once_with()
    media.start_playing.assert_not_called()
    assert output.started == 1


@pytest.mark.asyncio
async def test_startup_loop_rebuilds_handler_on_restart_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """LocalStream should shut down and rebuild the handler when a restart is requested."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    class FakeHandler:
        def __init__(self) -> None:
            self.connected = False
            self.output_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()
            self.shutdown_calls = 0

        async def start_up(self) -> None:
            self.connected = True
            self.started.set()
            await self.stopped.wait()
            self.connected = False

        def _is_connected(self) -> bool:
            return self.connected

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.stopped.set()

        async def receive(self, _frame: Any) -> None:
            return None

        async def emit(self) -> None:
            return None

    handlers: list[FakeHandler] = []

    def handler_factory(_voice: str | None) -> FakeHandler:
        handler = FakeHandler()
        handlers.append(handler)
        return handler

    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    initial_handler = handler_factory(None)
    stream = LocalStream(initial_handler, robot, handler_factory=handler_factory)
    stream._backend_retry_delay = 0.01

    startup_task = asyncio.create_task(stream._run_handler_startup_loop())
    try:
        await _wait_until(lambda: initial_handler.started.is_set())

        await stream.request_backend_restart("backend_config_changed")

        await _wait_until(lambda: len(handlers) == 2 and handlers[1].started.is_set())

        assert initial_handler.shutdown_calls >= 1
        assert stream.handler is handlers[1]
        assert stream._backend_connected() is True
    finally:
        stream._stop_event.set()
        await stream._shutdown_active_handler()
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_personality_ops_return_hf_voices() -> None:
    """With no running loop, voices() falls back to the Hugging Face catalog."""
    ops = build_personality_ops(MagicMock(), lambda: None)
    assert await ops.voices() == HF_AVAILABLE_VOICES


def test_personality_ops_delete_builtin_is_not_deletable() -> None:
    """Deleting a built-in personality raises not_deletable (was REST 404)."""
    ops = build_personality_ops(MagicMock(), lambda: None)
    with pytest.raises(RouteError) as ei:
        ops.delete("mad_scientist_assistant")
    assert ei.value.reason == "not_deletable"


def test_personality_ops_load_builtin_default_profile() -> None:
    """The bulk personality API should retain the complete profile payload."""
    ops = build_personality_ops(MagicMock(), lambda: None)
    data = ops.load("default")
    assert "Reachy Mini" in data["instructions"]
    assert data["tools_text"]
    assert data["enabled_tools"]
    assert data["available_tools"]


@pytest.mark.asyncio
async def test_personality_ops_apply_voice() -> None:
    """apply_voice delegates to the handler and reports the status."""
    handler = MagicMock()
    handler.change_voice = AsyncMock(return_value="Voice changed to cedar.")
    ops = build_personality_ops(handler, lambda: asyncio.get_running_loop())

    result = await ops.apply_voice("cedar")

    assert result == {"ok": True, "status": "Voice changed to cedar."}
    handler.change_voice.assert_awaited_once_with("cedar")


@pytest.mark.asyncio
async def test_personality_ops_persist_startup_with_voice_override() -> None:
    """Applying with persist=True saves the active manual voice override."""
    handler = MagicMock()
    handler.apply_personality = AsyncMock(return_value="Applied personality and restarted realtime session.")
    handler.get_current_voice = MagicMock(return_value="shimmer")
    persist_personality = MagicMock()
    ops = build_personality_ops(handler, lambda: asyncio.get_running_loop(), persist_personality=persist_personality)

    result = await ops.apply("sorry_bro", persist=True)

    assert result["ok"] is True
    handler.apply_personality.assert_awaited_once_with("sorry_bro")
    persist_personality.assert_called_once_with("sorry_bro", "shimmer")


@pytest.mark.asyncio
async def test_personality_ops_apply_same_profile_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-applying the active personality is a no-op for the realtime handler."""
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "sorry_bro")
    handler = MagicMock()
    handler.apply_personality = AsyncMock(return_value="should not be called")
    handler.get_current_voice = MagicMock(return_value="shimmer")
    ops = build_personality_ops(handler, lambda: None)

    result = await ops.apply("sorry_bro")

    assert result["status"] == "Personality unchanged."
    handler.apply_personality.assert_not_awaited()
    handler.get_current_voice.assert_not_called()


def test_personality_ops_startup_choice_survives_runtime_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime profile switching should not redefine the saved startup personality."""
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "captain_circuit")
    ops = build_personality_ops(MagicMock(), lambda: None)

    first = ops.get_choices()
    assert first["current"] == "captain_circuit"
    assert first["startup"] == "captain_circuit"

    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "chess_coach")

    second = ops.get_choices()
    assert second["current"] == "chess_coach"
    assert second["startup"] == "captain_circuit"


@pytest.mark.asyncio
async def test_personality_ops_use_apply_callback() -> None:
    """Apply delegates to the injected apply_personality callback, not the handler."""
    handler = MagicMock()
    handler.apply_personality = AsyncMock(return_value="handler should not be called")
    apply_personality = AsyncMock(return_value="Applied personality and restarting backend.")
    get_current_voice = MagicMock(return_value="cedar")
    ops = build_personality_ops(
        handler,
        lambda: asyncio.get_running_loop(),
        apply_personality=apply_personality,
        get_current_voice=get_current_voice,
    )

    result = await ops.apply("sorry_bro")

    assert result["status"] == "Applied personality and restarting backend."
    apply_personality.assert_awaited_once_with("sorry_bro")
    handler.apply_personality.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_personality_propagates_restart_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancellation during backend restart should not be converted into a status string."""
    monkeypatch.setattr(console_mod, "set_custom_profile", lambda _profile: None)
    monkeypatch.setattr(console_mod, "get_session_instructions", lambda _instance_path=None: "instructions")
    monkeypatch.setattr(console_mod, "get_session_voice", lambda default: default)

    stream = LocalStream(MagicMock(), MagicMock())

    async def cancel_restart(_reason: str) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(stream, "request_backend_restart", cancel_restart)

    with pytest.raises(asyncio.CancelledError):
        await stream.apply_personality("sorry_bro")


@pytest.mark.asyncio
async def test_apply_personality_restores_profile_when_tool_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed tool rebuild must not leave the rejected profile selected."""
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "default")
    monkeypatch.setattr(
        console_mod,
        "set_custom_profile",
        lambda profile: setattr(config, "REACHY_MINI_CUSTOM_PROFILE", profile),
    )
    monkeypatch.setattr(console_mod, "get_session_instructions", lambda: "instructions")
    monkeypatch.setattr(console_mod, "get_session_voice", lambda default: default)
    monkeypatch.setattr(console_mod, "initialize_tools", MagicMock(side_effect=RuntimeError("invalid tools")))
    stream = LocalStream(MagicMock(), MagicMock())

    with pytest.raises(RuntimeError, match="invalid tools"):
        await stream.apply_personality("broken")

    assert config.REACHY_MINI_CUSTOM_PROFILE == "default"


@pytest.mark.asyncio
async def test_local_stream_change_voice_delegates_without_backend_restart() -> None:
    """LocalStream voice changes should update the active handler without rebuilding it."""
    handler = MagicMock()
    handler.change_voice = AsyncMock(return_value="Voice changed to Serena.")
    handler.get_current_voice = MagicMock(return_value="Serena")
    stream = LocalStream(handler, MagicMock())

    status = await stream.change_voice("Serena")

    assert status == "Voice changed to Serena."
    handler.change_voice.assert_awaited_once_with("Serena")
    assert stream._voice_override == "Serena"
    assert not stream._restart_requested.is_set()


def test_local_stream_persist_personality_stores_voice_override(tmp_path) -> None:
    """Persisting startup settings should write both profile and voice override."""
    stream = LocalStream(MagicMock(), MagicMock(), instance_path=str(tmp_path))

    stream._persist_personality("sorry_bro", "shimmer")

    settings_path = tmp_path / "startup_settings.json"
    assert settings_path.exists()
    assert settings_path.read_text(encoding="utf-8") == '{\n  "profile": "sorry_bro",\n  "voice": "shimmer"\n}\n'
    assert stream._read_persisted_personality() == "sorry_bro"


def test_local_stream_persist_personality_clears_legacy_startup_env_overrides(tmp_path, monkeypatch) -> None:
    """Saving startup settings should remove legacy `.env` profile and voice overrides."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HF_TOKEN=test-token\n"
        "REACHY_MINI_CUSTOM_PROFILE=mad_scientist_assistant\n"
        "REACHY_MINI_VOICE_OVERRIDE=shimmer\n",
        encoding="utf-8",
    )
    stream = LocalStream(MagicMock(), MagicMock(), instance_path=str(tmp_path))

    stream._persist_personality(None, "Aiden")

    env_text = env_path.read_text(encoding="utf-8")
    assert "HF_TOKEN=test-token" in env_text
    assert "REACHY_MINI_CUSTOM_PROFILE=" not in env_text
    assert "REACHY_MINI_VOICE_OVERRIDE=" not in env_text

    applied_profiles: list[str | None] = []
    monkeypatch.delenv("REACHY_MINI_CUSTOM_PROFILE", raising=False)
    monkeypatch.setattr(
        "reachy_mini_conversation_app.config.set_custom_profile",
        lambda profile: applied_profiles.append(profile),
    )

    settings = load_startup_settings_into_runtime(tmp_path)

    assert settings == StartupSettings(voice="Aiden")
    assert applied_profiles == [None]


def test_local_stream_launch_waits_for_missing_hf_target_without_starting_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup should wait for settings input when the Hugging Face target is missing."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)

    media = SimpleNamespace(
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    robot = SimpleNamespace(media=media)
    stream = LocalStream(MagicMock(), robot, settings_app=FastAPI(), instance_path=str(tmp_path))

    init_settings_ui = MagicMock()
    monkeypatch.setattr(stream, "_init_settings_ui_if_needed", init_settings_ui)
    monkeypatch.setattr("reachy_mini_conversation_app.console.time.sleep", MagicMock(side_effect=KeyboardInterrupt))

    stream.launch()

    init_settings_ui.assert_called_once()
    media.start_recording.assert_not_called()
    media.start_playing.assert_not_called()


def test_backend_connected_uses_handler_contract_without_connection_attribute() -> None:
    """Connection status should follow the handler contract, not provider internals."""
    handler = MagicMock()
    handler._is_connected.return_value = False
    stream = LocalStream(handler, MagicMock())

    assert stream._backend_connected() is False

    handler._is_connected.return_value = True

    assert stream._backend_connected() is True


@pytest.mark.asyncio
async def test_local_stream_delegates_voice_catalog_to_active_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-Hugging-Face provider's voices should reach the settings operations unchanged."""
    monkeypatch.setattr(console_mod, "get_realtime_provider", lambda: "gemini")
    handler = MagicMock()
    handler.get_available_voices = AsyncMock(return_value=["Puck", "Kore"])
    handler.get_current_voice.return_value = "Puck"
    stream = LocalStream(handler, MagicMock())

    assert await stream.get_available_voices() == ["Puck", "Kore"]
    assert stream.get_current_voice() == "Puck"

    handler.get_available_voices.assert_awaited_once_with()
    handler.get_current_voice.assert_called_once_with()


def _patch_provider_status(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    ready: bool,
    missing_message: str,
) -> None:
    """Patch provider/config boundaries for deterministic status RPC tests."""
    monkeypatch.setattr(console_mod, "get_realtime_provider", lambda: provider)
    monkeypatch.setattr(console_mod, "has_realtime_target", lambda: ready)
    monkeypatch.setattr(console_mod, "get_realtime_missing_config_message", lambda: missing_message)
    monkeypatch.setattr(console_mod, "get_hf_session_url", lambda: None)
    monkeypatch.setattr(console_mod, "get_hf_direct_ws_url", lambda: None)
    monkeypatch.setattr(
        console_mod,
        "get_hf_connection_selection",
        lambda: SimpleNamespace(mode="deployed", has_target=False),
    )


def test_status_reports_ready_gemini_without_exposing_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini readiness is provider-specific and status never includes its key."""
    secret = "gemini-status-secret"
    _patch_provider_status(monkeypatch, "gemini", True, "GEMINI_API_KEY is not configured.")
    handler = MagicMock()
    handler._is_connected.return_value = True
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    status = _rpc_call(app, "conversation.status")["result"]

    assert status["backend"] == "gemini"
    assert status["realtime_provider"] == "gemini"
    assert status["has_realtime_target"] is True
    assert status["can_proceed"] is True
    assert status["backend_connected"] is True
    assert secret not in repr(status)


def test_status_redacts_gemini_key_from_backend_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider errors must not echo a configured Gemini credential to the UI."""
    secret = "gemini-error-secret"
    _patch_provider_status(monkeypatch, "gemini", True, "GEMINI_API_KEY is not configured.")
    monkeypatch.setattr(config, "GEMINI_API_KEY", secret, raising=False)
    handler = MagicMock()
    handler._is_connected.return_value = False
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app, instance_path=str(tmp_path))
    stream._set_backend_connection_state("disconnected", RuntimeError(f"authentication failed for {secret}"))
    stream._init_settings_ui_if_needed()

    status = _rpc_call(app, "conversation.status")["result"]

    assert secret not in repr(status)
    assert status["backend_error"] == "RuntimeError: authentication failed for [redacted]"


def test_status_reports_missing_gemini_key_without_hf_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Gemini configuration should be visible without claiming HF controls apply."""
    _patch_provider_status(monkeypatch, "gemini", False, "GEMINI_API_KEY is not configured.")
    handler = MagicMock()
    handler._is_connected.return_value = False
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app, instance_path=str(tmp_path))
    stream._set_backend_connection_state("waiting_for_config", "GEMINI_API_KEY is not configured.")
    stream._init_settings_ui_if_needed()

    status = _rpc_call(app, "conversation.status")["result"]

    assert status["backend"] == "gemini"
    assert status["has_realtime_target"] is False
    assert status["can_proceed"] is False
    assert status["can_proceed_with_gemini"] is False
    assert status["missing_config"] == "GEMINI_API_KEY is not configured."
    assert status["has_hf_connection"] is False
    assert "GEMINI_API_KEY" in status["backend_error"]


@pytest.mark.asyncio
async def test_startup_loop_uses_selected_provider_readiness_not_hf_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured Gemini provider must start even when Hugging Face has no target."""
    _patch_provider_status(monkeypatch, "gemini", True, "GEMINI_API_KEY is not configured.")

    class FakeHandler:
        def __init__(self) -> None:
            self.started = False
            self.output_queue = asyncio.Queue()

        def _is_connected(self) -> bool:
            return self.started

        async def start_up(self) -> None:
            self.started = True
            stream._stop_event.set()

        async def shutdown(self) -> None:
            self.started = False

    handler = FakeHandler()
    stream = LocalStream(handler, MagicMock())

    await stream._run_handler_startup_loop()

    assert handler.started is True
    assert stream._backend_connection_state == "connecting"


@pytest.mark.asyncio
async def test_startup_logs_redact_gemini_key_from_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Startup diagnostics should not log a Gemini credential from provider errors."""
    secret = "gemini-startup-secret"
    _patch_provider_status(monkeypatch, "gemini", True, "GEMINI_API_KEY is not configured.")
    monkeypatch.setattr(config, "GEMINI_API_KEY", secret, raising=False)
    handler = MagicMock()
    handler._is_connected.return_value = False
    stream = LocalStream(handler, MagicMock())
    stream._backend_retry_delay = 0

    async def fail_and_stop() -> None:
        stream._stop_event.set()
        raise RuntimeError(f"authentication failed for {secret}")

    handler.start_up = AsyncMock(side_effect=fail_and_stop)

    with caplog.at_level(logging.WARNING, logger=console_mod.logger.name):
        await stream._run_handler_startup_loop()

    assert secret not in caplog.text
    assert "RuntimeError: authentication failed for [redacted]" in caplog.text


@pytest.mark.asyncio
async def test_record_loop_forwards_ordered_frames_and_polls_empty_capture() -> None:
    """Capture polling preserves frame order and keeps empty polls off the loop."""
    pending_frames: list[np.ndarray | None] = [
        None,
        np.array([1, 2], dtype=np.int16),
        None,
        np.array([3, 4], dtype=np.int16),
        np.array([5, 6], dtype=np.int16),
    ]

    def get_audio_sample() -> np.ndarray | None:
        if pending_frames:
            return pending_frames.pop(0)
        return None

    media = SimpleNamespace(
        get_input_audio_samplerate=MagicMock(return_value=16_000),
        get_audio_sample=get_audio_sample,
    )
    received: list[tuple[int, np.ndarray]] = []
    handler = MagicMock()
    stream = LocalStream(handler, SimpleNamespace(media=media))

    async def receive(audio: tuple[int, np.ndarray]) -> None:
        received.append(audio)
        if len(received) == 3:
            stream._stop_event.set()

    handler.receive = receive

    await asyncio.wait_for(stream.record_loop(), timeout=1.0)

    assert [sample_rate for sample_rate, _ in received] == [16_000, 16_000, 16_000]
    np.testing.assert_array_equal(received[0][1], np.array([1, 2], dtype=np.int16))
    np.testing.assert_array_equal(received[1][1], np.array([3, 4], dtype=np.int16))
    np.testing.assert_array_equal(received[2][1], np.array([5, 6], dtype=np.int16))


@pytest.mark.asyncio
async def test_hybrid_record_loop_fences_capture_queue_at_activation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real capture worker keeps pre-barrier frames local and sends only newer frames."""
    pre_frames = [np.array([11 + index], dtype=np.int16) for index in range(3)]
    post_frames = [np.array([21 + index], dtype=np.int16) for index in range(2)]
    emitted_pre = 0
    phase = "pre"
    release_pre = threading.Event()
    release_post = threading.Event()

    def get_audio_sample() -> np.ndarray | None:
        nonlocal emitted_pre
        if pre_frames:
            emitted_pre += 1
            return pre_frames.pop(0)
        if phase == "pre":
            release_pre.wait()
            return None
        if post_frames:
            return post_frames.pop(0)
        release_post.wait()
        return None

    media = SimpleNamespace(
        get_input_audio_samplerate=MagicMock(return_value=16_000),
        get_audio_sample=get_audio_sample,
    )
    local_frames: list[np.ndarray] = []

    class LocalProcessor:
        def observe_frame(self, frame: tuple[int, np.ndarray]) -> None:
            local_frames.append(frame[1].copy())

    providers: list[_HybridTestHandler] = []

    def provider_factory() -> _HybridTestHandler:
        provider = _HybridTestHandler()
        providers.append(provider)
        return provider

    runtime = HybridRuntime(
        provider_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20.0, sample_rate=16_000),
        local_processor=LocalProcessor(),
        inactivity_timeout_s=0,
    )
    stream = LocalStream(runtime, SimpleNamespace(media=media), hybrid_runtime=runtime)
    worker = _AudioCaptureWorker(media, poll_interval=0)
    stream._capture_worker = worker

    with caplog.at_level(logging.DEBUG, logger=console_mod.logger.name):
        worker.start()
        await _wait_until(lambda: emitted_pre == 3)
        release_pre.set()
        worker.stop()
        assert worker.sequence == 3
        assert providers == []

        phase = "post"
        assert await runtime.activate("integration")
        assert len(providers) == 1
        assert providers[0].started.wait(timeout=1.0)

        record_task = asyncio.create_task(stream.record_loop())
        await _wait_until(lambda: len(providers[0].received) == 2)
        stream._stop_event.set()
        release_post.set()
        await asyncio.wait_for(record_task, timeout=1.0)

    assert [int(frame[0]) for frame in local_frames] == [11, 12, 13]
    assert [int(frame[0]) for frame in providers[0].received] == [21, 22]
    assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
    assert not list(tmp_path.iterdir())
    assert "ambient-household-sentinel" not in caplog.text

    await runtime.close()


@pytest.mark.asyncio
async def test_hybrid_capture_epoch_fences_inflight_frame_and_preserves_fifo() -> None:
    """A pull started before activation stays local while later frames keep FIFO order."""
    pull_started = threading.Event()
    release_preactivation_pull = threading.Event()
    allow_postactivation_pulls = threading.Event()
    release_postactivation_pull = threading.Event()
    postactivation_frames = [
        np.array([21], dtype=np.int16),
        np.array([22], dtype=np.int16),
    ]
    first_pull = True

    def get_audio_sample() -> np.ndarray | None:
        nonlocal first_pull
        if first_pull:
            first_pull = False
            pull_started.set()
            release_preactivation_pull.wait()
            return np.array([11], dtype=np.int16)
        allow_postactivation_pulls.wait()
        if postactivation_frames:
            return postactivation_frames.pop(0)
        release_postactivation_pull.wait()
        return None

    media = SimpleNamespace(
        get_input_audio_samplerate=MagicMock(return_value=16_000),
        get_audio_sample=get_audio_sample,
    )
    local_frames: list[int] = []

    class LocalProcessor:
        def observe_frame(self, frame: tuple[int, np.ndarray]) -> None:
            local_frames.append(int(frame[1][0]))

    providers: list[_HybridTestHandler] = []

    def provider_factory() -> _HybridTestHandler:
        provider = _HybridTestHandler()
        providers.append(provider)
        return provider

    runtime = HybridRuntime(
        provider_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20.0, sample_rate=16_000),
        local_processor=LocalProcessor(),
        inactivity_timeout_s=0,
    )
    stream = LocalStream(runtime, SimpleNamespace(media=media), hybrid_runtime=runtime)
    record_task = asyncio.create_task(stream.record_loop())

    try:
        assert await asyncio.to_thread(pull_started.wait, 1.0)
        worker = stream._capture_worker
        assert worker is not None
        assert worker.capture_epoch == 1

        assert await runtime.activate("inflight-race")
        assert providers and providers[0].started.wait(timeout=1.0)

        release_preactivation_pull.set()
        await _wait_until(lambda: local_frames == [11])
        assert providers[0].received == []

        allow_postactivation_pulls.set()
        await _wait_until(lambda: [int(frame[0]) for frame in providers[0].received] == [21, 22])
    finally:
        release_preactivation_pull.set()
        allow_postactivation_pulls.set()
        release_postactivation_pull.set()
        stream._stop_event.set()
        await asyncio.wait_for(record_task, timeout=1.0)
        await runtime.close()


@pytest.mark.asyncio
async def test_hybrid_capture_flushes_ordered_postactivation_frames_after_delayed_ready() -> None:
    """The real capture loop retains over one second until delayed provider readiness."""
    pre_frames = [np.full(16_000, value, dtype=np.int16) for value in (11, 12)]
    post_frames = [np.full(16_000, value, dtype=np.int16) for value in (21, 22, 23)]
    phase = "pre"
    pre_complete = threading.Event()
    post_complete = threading.Event()
    release_pre = threading.Event()
    release_post = threading.Event()
    connect_gate = threading.Event()

    def get_audio_sample() -> np.ndarray | None:
        if pre_frames:
            frame = pre_frames.pop(0)
            if not pre_frames:
                pre_complete.set()
            return frame
        if phase == "pre":
            release_pre.wait()
            return None
        if post_frames:
            frame = post_frames.pop(0)
            if not post_frames:
                post_complete.set()
            return frame
        release_post.wait()
        return None

    media = SimpleNamespace(
        get_input_audio_samplerate=MagicMock(return_value=16_000),
        get_audio_sample=get_audio_sample,
    )
    local_frames: list[int] = []

    class LocalProcessor:
        def observe_frame(self, frame: tuple[int, np.ndarray]) -> None:
            local_frames.append(int(frame[1][0]))

    providers: list[_HybridTestHandler] = []

    def provider_factory() -> _HybridTestHandler:
        provider = _HybridTestHandler(connect_gate=connect_gate)
        providers.append(provider)
        return provider

    runtime = HybridRuntime(
        provider_factory,
        ambient_buffer=AmbientAudioBuffer(max_seconds=20.0, sample_rate=16_000),
        local_processor=LocalProcessor(),
        inactivity_timeout_s=0,
    )
    stream = LocalStream(runtime, SimpleNamespace(media=media), hybrid_runtime=runtime)
    capture_worker = _AudioCaptureWorker(media, poll_interval=0)
    stream._capture_worker = capture_worker
    record_task = asyncio.create_task(stream.record_loop())

    try:
        assert await asyncio.to_thread(pre_complete.wait, 1.0)
        release_pre.set()
        await _wait_until(lambda: local_frames == [11, 12])

        assert await runtime.activate("delayed-ready")
        phase = "post"
        assert await asyncio.to_thread(post_complete.wait, 1.0)
        await asyncio.sleep(0)

        assert providers and providers[0].received == []
        connect_gate.set()
        assert await asyncio.to_thread(providers[0].started.wait, 1.0)
        await _wait_until(lambda: [int(frame[0]) for frame in providers[0].received] == [21, 22, 23])
    finally:
        release_pre.set()
        release_post.set()
        connect_gate.set()
        stream._stop_event.set()
        await asyncio.wait_for(record_task, timeout=1.0)
        await runtime.close()

    assert local_frames == [11, 12]
    assert [int(frame[0]) for frame in providers[0].received] == [21, 22, 23]
    assert runtime.postactivation_frames_dropped == 0


@pytest.mark.asyncio
async def test_hybrid_localstream_silence_frames_do_not_refresh_cloud_inactivity() -> None:
    """Continuous capture polling cannot keep an otherwise idle cloud session alive."""

    class FakeClock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    provider = _HybridTestHandler()
    runtime = HybridRuntime(
        lambda: provider,
        ambient_buffer=AmbientAudioBuffer(max_seconds=1.0, sample_rate=16_000),
        inactivity_timeout_s=5.0,
        clock=clock,
    )
    media = SimpleNamespace()
    stream = LocalStream(runtime, SimpleNamespace(media=media), hybrid_runtime=runtime)

    assert await runtime.activate("inactivity-test")
    assert provider.started.wait(timeout=1.0)
    for _ in range(20):
        clock.now += 0.25
        await stream._receive_hybrid_frame((16_000, np.zeros(160, dtype=np.int16)), None)

    clock.now = 5.0
    assert await runtime.check_inactivity()
    assert runtime.mode.value == "LOCAL_AMBIENT"
    assert provider.shutdown_loop is asyncio.get_running_loop()


@pytest.mark.asyncio
async def test_hybrid_capture_pressure_keeps_ordered_local_processing_off_capture_worker() -> None:
    """A slow local processor must not make the recorder discard its bounded capture queue."""

    class BlockingLocalProcessor:
        """Hold one local frame while the capture worker produces a paced burst."""

        def __init__(self) -> None:
            """Initialize deterministic release and observation signals."""
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.frames: list[np.ndarray] = []

        async def observe_frame(self, frame: tuple[int, np.ndarray]) -> None:
            """Retain ordered frame labels and wait for explicit release."""
            self.frames.append(frame[1].copy())
            self.started.set()
            await self.release.wait()

    processor = BlockingLocalProcessor()
    produced_all = threading.Event()
    capture_blocked = threading.Event()
    release_media = threading.Event()
    next_value = 0

    def get_audio_sample() -> np.ndarray | None:
        """Emit six paced frames, then stop producing until teardown releases the fake media."""
        nonlocal next_value
        if next_value < 6:
            frame = np.array([next_value], dtype=np.int16)
            next_value += 1
            if next_value > 1:
                threading.Event().wait(0.01)
            if next_value == 6:
                produced_all.set()
            return frame
        capture_blocked.set()
        release_media.wait()
        return None

    media = SimpleNamespace(
        get_input_audio_samplerate=MagicMock(return_value=16_000),
        get_audio_sample=get_audio_sample,
    )
    runtime = HybridRuntime(
        lambda: _HybridTestHandler(),
        ambient_buffer=AmbientAudioBuffer(max_seconds=20.0, sample_rate=16_000),
        local_processor=processor,
    )
    stream = LocalStream(runtime, SimpleNamespace(media=media), hybrid_runtime=runtime)
    worker = _AudioCaptureWorker(media, queue_size=4, poll_interval=0)
    stream._capture_worker = worker
    record_task = asyncio.create_task(stream.record_loop())

    try:
        await processor.started.wait()
        assert await asyncio.to_thread(produced_all.wait, 1.0)
        assert await asyncio.to_thread(capture_blocked.wait, 1.0)

        assert worker.dropped_frames == 0

        processor.release.set()
        await _wait_until(lambda: len(processor.frames) == 6)
        assert [int(frame[0]) for frame in processor.frames] == [0, 1, 2, 3, 4, 5]
    finally:
        processor.release.set()
        release_media.set()
        stream._stop_event.set()
        await asyncio.wait_for(record_task, timeout=1.0)
        await runtime.close()


@pytest.mark.asyncio
async def test_blocked_capture_does_not_block_event_loop_or_close() -> None:
    """A stuck SDK pull cannot starve the loop or delay bounded shutdown."""
    pull_started = threading.Event()
    release_pull = threading.Event()

    def get_audio_sample() -> None:
        pull_started.set()
        release_pull.wait()
        return None

    media = SimpleNamespace(
        get_input_audio_samplerate=MagicMock(return_value=16_000),
        get_audio_sample=get_audio_sample,
        stop_recording=MagicMock(),
        stop_playing=MagicMock(),
    )
    stream = LocalStream(MagicMock(), SimpleNamespace(media=media))
    record_task = asyncio.create_task(stream.record_loop())

    try:
        assert await asyncio.to_thread(pull_started.wait, 1.0)
        worker = stream._capture_worker
        assert worker is not None
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(0.02)
        assert asyncio.get_running_loop().time() - started < 0.2

        shutdown_started = asyncio.get_running_loop().time()
        stream.close()
        await asyncio.wait_for(record_task, timeout=0.4)
        assert asyncio.get_running_loop().time() - shutdown_started < 0.35
        assert media.stop_recording.called
        assert media.stop_playing.called
    finally:
        release_pull.set()
        worker = locals().get("worker")
        if worker is not None and worker._thread is not None:
            await asyncio.to_thread(worker._thread.join, 1.0)


@pytest.mark.asyncio
async def test_capture_worker_drops_oldest_frame_when_queue_is_full() -> None:
    """Overflow keeps the newest bounded window in FIFO order."""
    next_frame = 0

    def get_audio_sample() -> np.ndarray:
        nonlocal next_frame
        frame = np.array([next_frame], dtype=np.int16)
        next_frame += 1
        return frame

    worker = _AudioCaptureWorker(
        SimpleNamespace(get_input_audio_samplerate=lambda: 16_000, get_audio_sample=get_audio_sample),
        queue_size=2,
        poll_interval=0,
    )
    worker.start()
    try:
        await _wait_until(lambda: worker.dropped_frames >= 3)
        worker.stop()

        queued_frames: list[np.ndarray] = []
        while (item := worker.get_nowait()) is not None:
            queued_frames.append(item[1])

        assert len(queued_frames) == 2
        retained_values = [int(frame[0]) for frame in queued_frames]
        assert retained_values[1] == retained_values[0] + 1
        assert retained_values[-1] >= 3
    finally:
        worker.stop()


@pytest.mark.asyncio
async def test_capture_worker_exception_is_typed_and_does_not_log_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Capture errors stop the worker with metadata-only diagnostics."""
    sentinel = "microphone-household-secret"

    def get_audio_sample() -> None:
        raise RuntimeError(sentinel)

    worker = _AudioCaptureWorker(
        SimpleNamespace(get_input_audio_samplerate=lambda: 16_000, get_audio_sample=get_audio_sample),
        stop_join_timeout=0.01,
    )
    with caplog.at_level(logging.WARNING, logger=console_mod.logger.name):
        worker.start()
        await _wait_until(lambda: worker.failure_type == "RuntimeError")
        worker.stop()

    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_capture_worker_stop_is_idempotent_for_blocked_pull(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeated stop requests do not repeat diagnostics for one stuck worker."""
    pull_started = threading.Event()
    release_pull = threading.Event()

    def get_audio_sample() -> None:
        pull_started.set()
        release_pull.wait()
        return None

    worker = _AudioCaptureWorker(
        SimpleNamespace(get_input_audio_samplerate=lambda: 16_000, get_audio_sample=get_audio_sample),
        stop_join_timeout=0.01,
    )
    worker.start()
    try:
        assert await asyncio.to_thread(pull_started.wait, 1.0)
        with caplog.at_level(logging.WARNING, logger=console_mod.logger.name):
            worker.stop()
            assert worker._thread is not None and worker._thread.is_alive()
            release_pull.set()
            worker.stop()
        assert caplog.text.count("Audio capture worker did not stop") == 1
        assert worker._thread is not None and not worker._thread.is_alive()
    finally:
        release_pull.set()
        if worker._thread is not None:
            worker.stop()
            await asyncio.to_thread(worker._thread.join, 1.0)


@pytest.mark.asyncio
async def test_play_loop_resamples_provider_output_to_media_rate() -> None:
    """Playback should convert Gemini's 24 kHz output to the SDK's device rate."""
    source = np.arange(6, dtype=np.float32)
    media = SimpleNamespace(
        get_output_audio_samplerate=MagicMock(return_value=16_000),
        push_audio_sample=MagicMock(),
    )
    handler = MagicMock()
    robot = SimpleNamespace(media=media)
    stream = LocalStream(handler, robot)

    async def emit_output() -> tuple[int, np.ndarray] | None:
        if not media.push_audio_sample.called:
            return 24_000, source
        stream._stop_event.set()
        return None

    handler.emit = emit_output

    await stream.play_loop()

    media.get_output_audio_samplerate.assert_called_once_with()
    media.push_audio_sample.assert_called_once()
    np.testing.assert_allclose(
        media.push_audio_sample.call_args.args[0],
        np.array([0.0, 1.5, 3.0, 4.5], dtype=np.float32),
    )


@pytest.mark.asyncio
async def test_play_loop_routes_selected_app_output_without_sdk_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in speaker bypass receives resampled audio while SDK output stays untouched."""

    class FakeSpeakerOutput:
        sample_rate = 16_000

        def __init__(self) -> None:
            self.frames: list[np.ndarray] = []
            self.flushes = 0
            self.closed = 0

        def start(self) -> None:
            return None

        def push(self, audio: np.ndarray) -> None:
            self.frames.append(audio.copy())

        def flush(self) -> None:
            self.flushes += 1

        def close(self) -> None:
            self.closed += 1

    output = FakeSpeakerOutput()
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: "gstreamer")
    monkeypatch.setattr(console_mod, "create_speaker_output", lambda *_args, **_kwargs: output)

    source = np.arange(6, dtype=np.float32)
    media = SimpleNamespace(
        get_output_audio_samplerate=MagicMock(return_value=16_000),
        push_audio_sample=MagicMock(),
    )
    handler = MagicMock()
    stream = LocalStream(
        handler,
        SimpleNamespace(media=media),
        speaker_output_factory=lambda _sample_rate: output,
    )
    stream._speaker_output = stream._build_speaker_output()

    async def emit_output() -> tuple[int, np.ndarray] | None:
        if not output.frames:
            return 24_000, source
        stream._stop_event.set()
        return None

    handler.emit = emit_output

    await stream.play_loop()

    media.push_audio_sample.assert_not_called()
    media.get_output_audio_samplerate.assert_called_once_with()
    assert len(output.frames) == 1
    np.testing.assert_allclose(output.frames[0], np.array([0.0, 1.5, 3.0, 4.5], dtype=np.float32))


def test_close_stops_sdk_playback_and_closes_selected_app_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown stops the SDK playback pipeline and the app-owned output."""

    class FakeSpeakerOutput:
        sample_rate = 16_000

        def __init__(self) -> None:
            self.flushes = 0
            self.closed = 0

        def start(self) -> None:
            return None

        def push(self, _audio: np.ndarray) -> None:
            return None

        def flush(self) -> None:
            self.flushes += 1

        def close(self) -> None:
            self.closed += 1

    output = FakeSpeakerOutput()
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: "gstreamer")
    monkeypatch.setattr(console_mod, "create_speaker_output", lambda *_args, **_kwargs: output)

    media = SimpleNamespace(
        get_output_audio_samplerate=MagicMock(return_value=16_000),
        push_audio_sample=MagicMock(),
        stop_playing=MagicMock(),
    )
    stream = LocalStream(
        MagicMock(),
        SimpleNamespace(media=media),
        speaker_output_factory=lambda _sample_rate: output,
    )
    assert stream._speaker_output is None
    stream._speaker_output = stream._build_speaker_output()

    assert stream._speaker_output is output
    stream.close()
    media.stop_playing.assert_called_once_with()
    assert output.closed == 1


def test_speaker_output_is_not_built_during_stream_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Output selection waits until launch has reloaded any instance environment."""
    factory = MagicMock()
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: "gstreamer")
    stream = LocalStream(
        MagicMock(),
        SimpleNamespace(media=SimpleNamespace(get_output_audio_samplerate=MagicMock(return_value=16_000))),
        speaker_output_factory=factory,
    )

    assert stream._speaker_output is None
    factory.assert_not_called()


def test_invalid_instance_audio_output_fails_before_media_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid persisted output selector must not silently restore SDK routing."""
    (tmp_path / ".env").write_text("AUDIO_OUTPUT_BACKEND=unknown\n", encoding="utf-8")
    monkeypatch.setenv(AUDIO_OUTPUT_BACKEND_ENV, AUDIO_OUTPUT_BACKEND_SDK)
    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    monkeypatch.setattr(console_mod, "has_realtime_target", lambda: True)
    media = SimpleNamespace(start_recording=MagicMock(), start_playing=MagicMock())
    stream = LocalStream(MagicMock(), SimpleNamespace(media=media), instance_path=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid AUDIO_OUTPUT_BACKEND"):
        stream.launch()

    media.start_recording.assert_not_called()
    media.start_playing.assert_not_called()


def test_output_start_failure_stops_sdk_media_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed app-owned output cannot resume assistant routing through the SDK."""

    class FailingSpeakerOutput:
        sample_rate = 16_000

        def __init__(self) -> None:
            self.closed = 0

        def start(self) -> None:
            raise RuntimeError("speaker start failed")

        def push(self, _audio: np.ndarray) -> None:
            return None

        def flush(self) -> None:
            return None

        def close(self) -> None:
            self.closed += 1

    output = FailingSpeakerOutput()
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: "gstreamer")
    monkeypatch.setattr(console_mod, "has_realtime_target", lambda: True)
    media = SimpleNamespace(
        get_output_audio_samplerate=MagicMock(return_value=16_000),
        start_recording=MagicMock(),
        start_playing=MagicMock(),
        stop_recording=MagicMock(),
        stop_playing=MagicMock(),
    )
    stream = LocalStream(
        MagicMock(),
        SimpleNamespace(media=media),
        speaker_output_factory=lambda _sample_rate: output,
    )

    with pytest.raises(RuntimeError, match="speaker start failed"):
        stream.launch()

    media.start_recording.assert_called_once_with()
    media.start_playing.assert_not_called()
    media.stop_playing.assert_called_once_with()
    media.stop_recording.assert_called_once_with()
    assert output.closed == 1


def test_clear_audio_queue_flushes_selected_app_output_without_sdk_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    """Barge-in flushes only the selected app-owned path."""

    class FakeSpeakerOutput:
        sample_rate = 16_000

        def __init__(self) -> None:
            self.flushes = 0

        def start(self) -> None:
            return None

        def push(self, _audio: np.ndarray) -> None:
            return None

        def flush(self) -> None:
            self.flushes += 1

        def close(self) -> None:
            return None

    output = FakeSpeakerOutput()
    monkeypatch.setattr(console_mod, "get_audio_output_backend", lambda: "gstreamer")
    monkeypatch.setattr(console_mod, "create_speaker_output", lambda *_args, **_kwargs: output)
    audio = SimpleNamespace(clear_player=MagicMock(), clear_output_buffer=MagicMock())
    media = SimpleNamespace(audio=audio, get_output_audio_samplerate=MagicMock(return_value=16_000))
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    stream = LocalStream(
        handler,
        SimpleNamespace(media=media),
        speaker_output_factory=lambda _sample_rate: output,
    )
    stream._speaker_output = stream._build_speaker_output()

    stream.clear_audio_queue()

    assert output.flushes == 1
    audio.clear_player.assert_not_called()
    audio.clear_output_buffer.assert_not_called()


@pytest.mark.asyncio
async def test_play_loop_does_not_log_additional_output_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Queued transcripts and tool results remain out of playback logs."""
    sentinel = "playback-household-secret"
    handler = MagicMock()
    stream = LocalStream(handler, SimpleNamespace(media=SimpleNamespace()))

    async def emit_output() -> AdditionalOutputs:
        stream._stop_event.set()
        return AdditionalOutputs({"role": "assistant", "content": sentinel})

    handler.emit = emit_output

    with caplog.at_level(logging.DEBUG, logger=console_mod.logger.name):
        await stream.play_loop()

    assert sentinel not in caplog.text


def test_backend_config_can_select_gemini_without_accepting_a_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider selection may be persisted, but browser-supplied Gemini keys are rejected."""
    _patch_provider_status(monkeypatch, "huggingface", True, "GEMINI_API_KEY is not configured.")
    captured_providers: list[str] = []
    monkeypatch.setattr(
        console_mod, "get_realtime_provider", lambda: "gemini" if captured_providers else "huggingface"
    )

    handler = MagicMock()
    handler._is_connected.return_value = False
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app, instance_path=str(tmp_path))
    stream._persist_realtime_provider = captured_providers.append  # type: ignore[method-assign]
    stream._init_settings_ui_if_needed()

    selected = _rpc_call(app, "backend.config", {"provider": "gemini"})

    assert selected["result"]["backend"] == "gemini"
    assert captured_providers == ["gemini"]
    assert not (tmp_path / ".env").exists()

    rejected = _rpc_call(app, "backend.config", {"provider": "gemini", "gemini_api_key": "browser-secret"})

    assert rejected["error"]["data"]["reason"] == "gemini_key_not_editable"
    assert "browser-secret" not in repr(rejected)
    assert not (tmp_path / ".env").exists()


def _rpc_robot() -> SimpleNamespace:
    """Return a robot mock whose audio pipeline supports clear_audio_queue()."""
    audio = SimpleNamespace(clear_player=MagicMock(), clear_output_buffer=MagicMock())
    return SimpleNamespace(media=SimpleNamespace(audio=audio))


def test_rpc_status_and_mic_over_websocket() -> None:
    """conversation.status/mic are reachable over the /rpc JSON-RPC WebSocket."""
    app = FastAPI()
    stream = LocalStream(MagicMock(), _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    client = TestClient(app)
    with client.websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status"})
        resp = ws.receive_json()
        assert resp["id"] == "1"
        assert "result" in resp

        ws.send_json({"jsonrpc": "2.0", "id": "2", "method": "conversation.mic", "params": {"muted": True}})
        resp = ws.receive_json()
        assert resp["result"] == {"muted": True}
    assert stream._mic_muted is True


def test_transcript_preview_is_not_exposed_over_shared_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local preview cannot be relayed through the shared WebRTC RPC surface."""
    monkeypatch.setattr(config, "LOCAL_TRANSCRIPT_PREVIEW_ENABLED", True)
    runtime = MagicMock()
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    result = _rpc_call(app, "conversation.transcript_preview")

    assert "error" in result
    runtime.get_transcript_preview.assert_not_called()


def test_transcript_preview_http_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled preview requests return no text and do not touch the runtime."""
    monkeypatch.setattr(config, "LOCAL_TRANSCRIPT_PREVIEW_ENABLED", False)
    runtime = MagicMock()
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    response = _transcript_preview_get(app)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"enabled": False, "available": False, "text": ""}
    runtime.get_transcript_preview.assert_not_called()


def test_transcript_preview_http_reports_unavailable_without_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in preview remains safe when hybrid local STT was not configured."""
    monkeypatch.setattr(config, "LOCAL_TRANSCRIPT_PREVIEW_ENABLED", True)
    runtime = HybridRuntime(lambda: MagicMock())
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    response = _transcript_preview_get(app)

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "available": False, "text": ""}


def test_transcript_preview_http_denies_non_loopback_without_runtime_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote clients receive an empty 403 before preview retrieval is scheduled."""
    monkeypatch.setattr(config, "LOCAL_TRANSCRIPT_PREVIEW_ENABLED", True)
    runtime = MagicMock()
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    response = _transcript_preview_get(app, client_host="192.0.2.10")

    assert response.status_code == 403
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    runtime.get_transcript_preview.assert_not_called()


def test_transcript_preview_http_reads_bounded_text_on_owner_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled preview reads the existing bounded transcript on LocalStream's loop."""
    monkeypatch.setattr(config, "LOCAL_TRANSCRIPT_PREVIEW_ENABLED", True)
    target_loop = asyncio.new_event_loop()
    thread, _ = _run_loop_in_thread(target_loop)
    processor = AmbientContextProcessor(
        vad=MagicMock(),
        stt=MagicMock(),
        config=AmbientContextConfig(
            strategy=AmbientContextStrategy.CONTINUOUS_TRANSCRIPT,
            max_transcript_chars=8,
        ),
    )
    sentinel = "diagnostic transcript"
    now = time.monotonic()
    runtime = HybridRuntime(lambda: MagicMock(), context_processor=processor)
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._asyncio_loop = target_loop
    stream._init_settings_ui_if_needed()

    try:
        for loopback_host in ("127.0.0.1", "127.255.255.254", "::1"):
            empty_response = _transcript_preview_get(app, client_host=loopback_host)
            assert empty_response.status_code == 200
            assert empty_response.headers["cache-control"] == "no-store"
            assert empty_response.json() == {"enabled": True, "available": True, "text": ""}

        processor.transcript.add(TranscriptSegment(sentinel, now - 1.0, now, 1))
        response = _transcript_preview_get(app)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"enabled": True, "available": True, "text": "anscript"}
        assert runtime._owner_loop is target_loop
        status = _rpc_call(app, "conversation.status")["result"]
        assert sentinel not in repr(status)
        assert "transcript_preview" not in repr(status)

        processor_close_future = asyncio.run_coroutine_threadsafe(processor.close(), target_loop)
        processor_close_future.result(timeout=2.0)
        closed_response = _transcript_preview_get(app)
        assert closed_response.status_code == 200
        assert closed_response.headers["cache-control"] == "no-store"
        assert closed_response.json() == {"enabled": True, "available": False, "text": ""}
        assert sentinel not in closed_response.text
    finally:
        close_future = asyncio.run_coroutine_threadsafe(runtime.close(), target_loop)
        close_future.result(timeout=2.0)
        processor_close_future = asyncio.run_coroutine_threadsafe(processor.close(), target_loop)
        processor_close_future.result(timeout=2.0)
        target_loop.call_soon_threadsafe(target_loop.stop)
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_transcript_preview_http_runtime_error_is_safe_and_payload_free(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A preview read failure returns unavailable without logging transcript content."""
    monkeypatch.setattr(config, "LOCAL_TRANSCRIPT_PREVIEW_ENABLED", True)
    target_loop = asyncio.new_event_loop()
    thread, _ = _run_loop_in_thread(target_loop)
    runtime = HybridRuntime(lambda: MagicMock())

    async def fail_preview() -> str | None:
        raise RuntimeError("preview-sensitive transcript")

    setattr(runtime, "get_transcript_preview", fail_preview)
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._asyncio_loop = target_loop
    stream._init_settings_ui_if_needed()

    try:
        with caplog.at_level(logging.WARNING, logger=console_mod.logger.name):
            response = _transcript_preview_get(app)

        assert response.status_code == 200
        assert response.json() == {"enabled": True, "available": False, "text": ""}
        assert "preview-sensitive transcript" not in caplog.text
    finally:
        close_future = asyncio.run_coroutine_threadsafe(runtime.close(), target_loop)
        close_future.result(timeout=2.0)
        target_loop.call_soon_threadsafe(target_loop.stop)
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_hybrid_status_exposes_postactivation_drop_diagnostic() -> None:
    """Status includes the bounded handshake overflow diagnostic."""
    runtime = HybridRuntime(lambda: MagicMock())
    runtime._postactivation_frames_dropped = 3
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    status = _rpc_call(app, "conversation.status")["result"]

    assert status["postactivation_frames_dropped"] == 3


def test_conversation_status_exposes_all_runtime_diagnostics_without_payloads() -> None:
    """The status RPC forwards runtime counters but never transcript/audio payloads."""
    runtime_status = RuntimeStatusSnapshot(
        mode=RuntimeMode.CLOUD_CONVERSATION,
        generation=7,
        active=True,
        closed=False,
        provider_task_running=True,
        inactivity_monitor_running=True,
        connected=True,
        ambient_audio_seconds_buffered=12.5,
        ambient_audio_bytes_sent_to_cloud=0,
        cloud_audio_bytes_sent=123,
        cloud_video_frames_sent=4,
        gemini_session_duration=5.5,
        postactivation_frames_dropped=2,
        local_context_chars=17,
        transcript_segments_accepted=3,
        wake_detections=4,
        wake_cooldown_suppressed=5,
        wake_final_update_count=6,
        local_commands_executed=7,
        local_command_failures=8,
        local_command_escalations=9,
        local_command_last_outcome=LocalCommandOutcome.ROUTER_REJECTED,
        local_command_last_router_reason=IntentDecisionReason.UNSUPPORTED_COMMAND,
        local_command_router_rejections=10,
        local_command_followup_timeouts=11,
        local_processing_frames_dropped=12,
        local_command_latency_seconds_total=10.5,
        local_command_latency_seconds_max=11.5,
        context_activation_latency_seconds_total=12.5,
        context_activation_latency_seconds_max=13.5,
        local_context_strategy="continuous_transcript",
        local_audio_seconds_observed=14.5,
        local_speech_frames=15,
        local_stt_requests=16,
        local_stt_completed=17,
        local_stt_failures=18,
        local_stt_timeouts=19,
        local_stt_dropped=20,
        local_stt_latency_seconds_total=21.5,
        local_stt_latency_seconds_max=22.5,
        local_stt_inflight_peak=23,
        local_stt_pending_at_activation=24,
    )
    runtime = MagicMock()
    runtime.status = runtime_status
    runtime.metrics = TrafficMetricsSnapshot(
        ambient_audio_seconds_buffered=12.5,
        ambient_audio_bytes_sent_to_cloud=0,
        cloud_audio_bytes_sent=123,
        cloud_video_frames_sent=4,
        gemini_session_duration=5.5,
    )
    runtime._is_connected.return_value = False
    app = FastAPI()
    stream = LocalStream(runtime, _rpc_robot(), settings_app=app, hybrid_runtime=runtime)
    stream._init_settings_ui_if_needed()

    status = _rpc_call(app, "conversation.status")["result"]

    for key, value in runtime_status().items():
        assert status[key] == value
    assert status["hybrid_metrics"] == {
        "ambient_audio_seconds_buffered": 12.5,
        "ambient_audio_bytes_sent_to_cloud": 0,
        "cloud_audio_bytes_sent": 123,
        "cloud_video_frames_sent": 4,
        "gemini_session_duration": 5.5,
        "postactivation_frames_dropped": 2,
        "local_processing_frames_dropped": 12,
    }
    assert "raw_audio" not in status
    assert "transcript_text" not in status


def test_rpc_interrupt_broadcasts_turn_listening() -> None:
    """conversation.interrupt clears playback and pushes a turn:listening event."""
    target_loop = asyncio.new_event_loop()
    thread, _ = _run_loop_in_thread(target_loop)
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    handler._is_connected.return_value = True
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app)
    stream._asyncio_loop = target_loop
    stream._init_settings_ui_if_needed()
    try:
        with TestClient(app).websocket_connect("/rpc") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.interrupt"})
            msgs = [ws.receive_json(), ws.receive_json()]
        results = [m for m in msgs if "result" in m]
        notes = [m for m in msgs if m.get("method") == "conversation.turn"]
        assert results and results[0]["result"] == {"ok": True}
        assert notes and notes[0]["params"] == {"state": "listening", "reason": "interrupted"}
    finally:
        target_loop.call_soon_threadsafe(target_loop.stop)
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_rpc_say_requires_active_session() -> None:
    """conversation.say fails with not_running when no session is connected."""
    handler = MagicMock()
    handler._is_connected.return_value = False
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.say", "params": {"text": "hi"}})
        resp = ws.receive_json()
    assert resp["error"]["data"]["reason"] == "not_running"


def test_rpc_transcript_notification_broadcast() -> None:
    """The handler's transcript observer pushes conversation.transcript events."""
    app = FastAPI()
    stream = LocalStream(MagicMock(), _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        stream._dispatch_transcript("assistant", "hello there", True)
        msg = ws.receive_json()
    assert msg["method"] == "conversation.transcript"
    assert msg["params"] == {"role": "assistant", "text": "hello there", "final": True}


def test_rpc_settings_methods() -> None:
    """Personality, voice, and tool settings are reachable over /rpc."""
    app = FastAPI()
    stream = LocalStream(MagicMock(), _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "personalities.list"})
        r1 = ws.receive_json()
        ws.send_json({"jsonrpc": "2.0", "id": "2", "method": "voices.list"})
        r2 = ws.receive_json()
        ws.send_json({"jsonrpc": "2.0", "id": "3", "method": "tool_spaces.list"})
        r3 = ws.receive_json()
        ws.send_json({"jsonrpc": "2.0", "id": "4", "method": "profile_tools.get"})
        r4 = ws.receive_json()
    assert "choices" in r1["result"] and "current" in r1["result"]
    assert isinstance(r2["result"], list)
    assert "spaces" in r3["result"]
    assert "enabled_tools" in r4["result"]
