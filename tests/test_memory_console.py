import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_mini_conversation_app import console
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.console import LocalStream
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore


@pytest.fixture
def memory_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, MagicMock, HouseholdMemoryStore]:
    """Create the real local settings routes with synthetic memory and no hardware."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    app = FastAPI()
    handler = MagicMock()
    handler.shutdown = AsyncMock()
    stream = LocalStream(handler, MagicMock(), settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()
    store = HouseholdMemoryStore(tmp_path)
    store.remember("Synthetic memory", subject_id="household", kind="fact")
    return app, handler, store


def test_memory_list_is_local_uncached_and_contains_only_inspection_fields(memory_console) -> None:
    """The list is explicit, local, uncached and omits provenance identifiers."""
    app, _, store = memory_console
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/memory")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    entry = response.json()["entries"][0]
    assert entry["id"] == store.list_entries()[0].id
    assert entry["text"] == "Synthetic memory"
    assert "source_ref" not in entry


@pytest.mark.parametrize("host,peer", [("evil.example", "127.0.0.1"), ("127.0.0.1", "192.0.2.1")])
def test_memory_rejects_remote_clients_and_rebinding_hosts(memory_console, host: str, peer: str) -> None:
    """Neither a remote peer nor a third-party hostname can read local memory."""
    app, handler, _ = memory_console
    with TestClient(app, base_url=f"http://{host}", client=(peer, 50000)) as client:
        assert client.get("/api/memory").status_code == 403
        assert client.post("/api/memory/forget", json={"memory_id": "test"}).status_code == 403
    handler.shutdown.assert_not_awaited()


def test_memory_forget_stops_conversation_before_deleting(memory_console) -> None:
    """A successful deletion stops old conversation context before changing the file."""
    app, handler, store = memory_console
    entry = store.list_entries()[0]

    async def shutdown() -> None:
        assert store.list_entries()[0].id == entry.id

    handler.shutdown.side_effect = shutdown
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/memory/forget", json={"memory_id": entry.id}, headers={"Origin": "http://127.0.0.1"}
        )
    assert response.json() == {"removed": True}
    handler.shutdown.assert_awaited_once()
    assert store.list_entries() == []
    assert entry.text not in store.path.read_text()


def test_memory_forget_rejects_cross_origin_and_invalid_requests(memory_console) -> None:
    """Malformed or cross-origin requests cannot reset a conversation or delete data."""
    app, handler, store = memory_console
    before = store.path.read_bytes()
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/memory", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/memory/forget", json={"memory_id": "test"}).status_code == 403
        assert client.post("/api/memory/forget", json={}, headers={"Origin": "http://127.0.0.1"}).status_code == 400
        assert (
            client.post(
                "/api/memory/forget",
                content="x" * 1025,
                headers={"Origin": "http://127.0.0.1", "Content-Type": "application/json"},
            ).status_code
            == 413
        )
    assert store.path.read_bytes() == before
    handler.shutdown.assert_not_awaited()


def test_memory_failed_context_shutdown_preserves_file(memory_console) -> None:
    """A shutdown error must not claim that stale conversation context was cleared."""
    app, handler, store = memory_console
    handler.shutdown.side_effect = RuntimeError("synthetic failure")
    before = store.path.read_bytes()
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/memory/forget",
            json={"memory_id": store.list_entries()[0].id},
            headers={"Origin": "http://127.0.0.1"},
        )
    assert response.status_code == 503
    assert "synthetic failure" not in response.text
    assert store.path.read_bytes() == before


def test_memory_console_disabled_and_corrupt_states(memory_console, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled and damaged stores remain unavailable without revealing content."""
    app, _, store = memory_console
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
        assert client.get("/api/memory").status_code == 404
        monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
        store.path.write_bytes(b"\xff")
        assert client.get("/api/memory").status_code == 503
    assert store.path.read_bytes() == b"\xff"


@pytest.mark.asyncio
async def test_memory_forget_runs_shutdown_on_conversation_owner_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settings server cannot call an active provider on its own worker loop."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    app = FastAPI()
    owner_loop = asyncio.get_running_loop()
    handler = MagicMock()

    async def shutdown() -> None:
        assert asyncio.get_running_loop() is owner_loop

    handler.shutdown = AsyncMock(side_effect=shutdown)
    stream = LocalStream(handler, MagicMock(), settings_app=app, instance_path=str(tmp_path))
    stream._asyncio_loop = owner_loop
    stream._init_settings_ui_if_needed()
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Synthetic owner loop test", subject_id="household")

    def forget_from_settings_thread() -> int:
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
            return client.post(
                "/api/memory/forget",
                json={"memory_id": entry.id},
                headers={"Origin": "http://127.0.0.1"},
            ).status_code

    assert await asyncio.to_thread(forget_from_settings_thread) == 200
    handler.shutdown.assert_awaited_once()
    assert store.list_entries() == []


@pytest.mark.asyncio
async def test_timed_out_forget_cannot_delete_after_resistant_shutdown(
    memory_console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late shutdown completion cannot apply an already failed delete request."""
    app, handler, store = memory_console
    monkeypatch.setattr(console, "_MEMORY_SHUTDOWN_TIMEOUT_S", 0.01)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def resistant_shutdown() -> None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            finished.set()

    handler.shutdown.side_effect = resistant_shutdown
    before = store.path.read_bytes()
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post(
            "/api/memory/forget",
            json={"memory_id": store.list_entries()[0].id},
            headers={"Origin": "http://127.0.0.1"},
        )
    assert response.status_code == 503
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1)
    await asyncio.sleep(0)
    assert store.path.read_bytes() == before


def test_hybrid_forget_requires_successful_provider_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Best-effort runtime deactivation is not evidence that old context closed."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    app = FastAPI()
    handler = MagicMock()
    handler.shutdown = AsyncMock(side_effect=RuntimeError("synthetic close failure"))
    stream = LocalStream(handler, MagicMock(), settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()
    runtime = MagicMock()
    runtime.active_handler = handler
    runtime.deactivate_cloud = AsyncMock(return_value=True)
    stream._hybrid_runtime = runtime
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Synthetic hybrid note", subject_id="household")
    before = store.path.read_bytes()
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/memory/forget", json={"memory_id": entry.id}, headers={"Origin": "http://127.0.0.1"}
        )
    assert response.status_code == 503
    runtime.deactivate_cloud.assert_not_awaited()
    assert store.path.read_bytes() == before
