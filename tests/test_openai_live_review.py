"""Daily maintenance at the active Live session boundary."""

import json
import asyncio
from typing import NoReturn
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Mapping

import pytest
from test_openai_live import FakeSocket, _wait_until
from test_openai_live_memory import _start_ready, _memory_handler
from test_household_memory_review import _FakeClient

import reachy_mini_conversation_app.openai_live as live_module
from reachy_mini_conversation_app.config import config, refresh_runtime_config_from_env
from reachy_mini_conversation_app.openai_live import ReviewApplied
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore
from reachy_mini_conversation_app.household_memory_review import ReviewClocks, ReviewResult, HouseholdMemoryReview


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_review_only_runs_after_live_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """Review only runs after live readiness."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", enabled)
    store = HouseholdMemoryStore(tmp_path)
    store.remember("Alex likes jazz", subject_id="person:alex")
    client = _FakeClient(lambda _: '{"proposals":[]}')
    worker = HouseholdMemoryReview(store, "test", client=client)
    socket = FakeSocket()
    handler = _memory_handler(
        socket,
        None,
        instance_path=str(tmp_path),
        memory_review_factory=lambda _path, _callback: worker,
        monkeypatch=monkeypatch,
    )
    assert client.responses.calls == []
    startup = await _start_ready(handler, socket)
    if enabled:
        await _wait_until(lambda: worker.stats.successes == 1)
    else:
        await asyncio.sleep(0.02)
        assert client.responses.calls == []
    assert handler.connection is socket
    handler.stop_background_work()
    assert (await worker.run_once()).status == "inactive"
    await handler.shutdown()
    await startup
    await worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("forget", [False, True])
async def test_standby_or_spoken_forget_fences_pending_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forget: bool
) -> None:
    """Standby or spoken forget fences pending review."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", True)
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    store = HouseholdMemoryStore(tmp_path, clock=lambda: now)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    client = _FakeClient(lambda _: '{"proposals":[]}')
    client.responses.block = True
    client.responses.ignore_cancellation = True
    worker = HouseholdMemoryReview(store, "test", client=client)
    socket = FakeSocket()
    handler = _memory_handler(
        socket,
        None,
        instance_path=str(tmp_path),
        memory_review_factory=lambda _path, _callback: worker,
        monkeypatch=monkeypatch,
    )
    startup = await _start_ready(handler, socket)
    await client.responses.started.wait()
    if forget:
        store.forget(memory_id=entry.id)
        handler._on_memory_forgotten(entry.id)
        assert await handler._check_memory_snapshot()
    else:
        handler.stop_background_work()
    client.responses.release.set()
    await asyncio.sleep(0.03)
    assert store.review_checkpoint() is None
    await handler.shutdown()
    await startup


@pytest.mark.asyncio
async def test_retention_cleanup_rebases_live_without_hiding_later_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention cleanup rebases live without hiding later deletion."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", True)
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    store = HouseholdMemoryStore(tmp_path, clock=lambda: now)
    store.remember("Old note", subject_id="household")
    lasting = store.remember("Alex likes jazz", subject_id="person:alex", kind="fact")
    socket = FakeSocket()
    monkeypatch.setattr(live_module, "HouseholdMemoryStore", lambda _path: store)
    worker_holder: list[HouseholdMemoryReview] = []

    def factory(_path: str | None, callback: ReviewApplied) -> HouseholdMemoryReview:
        worker = HouseholdMemoryReview(
            store,
            "test",
            client=_FakeClient(lambda _: '{"proposals":[]}'),
            clocks=ReviewClocks(now=lambda: now, monotonic=lambda: 0.0),
            on_applied=callback,
        )
        worker_holder.append(worker)
        return worker

    handler = _memory_handler(
        socket,
        None,
        instance_path=str(tmp_path),
        memory_review_factory=factory,
        monkeypatch=monkeypatch,
    )
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    startup = await _start_ready(handler, socket)
    await _wait_until(lambda: bool(worker_holder) and worker_holder[0].stats.successes == 1)
    assert await handler._check_memory_snapshot()
    store.forget(memory_id=lasting.id)
    assert not await handler._check_memory_snapshot()
    await handler.shutdown()
    await startup


@pytest.mark.asyncio
async def test_review_constructor_failure_does_not_abort_live_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review setup failure leaves the active Live session usable."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", True)
    socket = FakeSocket()
    factory_calls: list[str | None] = []

    def failing_factory(instance_path: str | None, _callback: ReviewApplied) -> NoReturn:
        factory_calls.append(instance_path)
        raise RuntimeError("review client setup failed")

    handler = _memory_handler(
        socket,
        None,
        instance_path=str(tmp_path),
        memory_review_factory=failing_factory,
        monkeypatch=monkeypatch,
    )
    startup = await _start_ready(handler, socket)
    assert handler.connection is socket
    assert factory_calls == [str(tmp_path)]
    await handler.shutdown()
    await startup


@pytest.mark.asyncio
async def test_successful_merge_updates_watcher_without_resetting_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review merge is acknowledged while a later deletion still resets Live."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", True)
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    store = HouseholdMemoryStore(tmp_path, clock=lambda: now)
    keep = store.remember("Alex enjoys jazz", subject_id="person:alex", kind="fact")
    duplicate = store.remember("Alex listens to jazz", subject_id="person:alex", kind="fact")

    def merge_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        ids = [entry["id"] for entry in payload["changed_entries"]]
        return json.dumps(
            {
                "proposals": [
                    {
                        "action": "merge",
                        "keep_id": keep.id,
                        "merge_ids": [duplicate.id],
                        "evidence_ids": ids,
                    }
                ]
            }
        )

    client = _FakeClient(merge_output)
    worker_holder: list[HouseholdMemoryReview] = []
    socket = FakeSocket()

    def factory(_path: str | None, callback: ReviewApplied) -> HouseholdMemoryReview:
        worker = HouseholdMemoryReview(store, "test", client=client, on_applied=callback)
        worker_holder.append(worker)
        return worker

    handler = _memory_handler(
        socket,
        None,
        instance_path=str(tmp_path),
        memory_review_factory=factory,
        monkeypatch=monkeypatch,
    )
    startup = await _start_ready(handler, socket)
    await _wait_until(lambda: bool(worker_holder) and worker_holder[0].stats.successes == 1)
    assert handler.connection is socket
    assert await handler._check_memory_snapshot()
    assert not handler._memory_invalidated
    removed = store.forget(memory_id=keep.id)
    assert removed.removed is not None
    assert not await handler._check_memory_snapshot()
    await handler.shutdown()
    await startup


@pytest.mark.asyncio
async def test_new_activation_builds_a_new_review_worker_after_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each activation gets a fresh worker after the prior client closes."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", True)
    sockets = [FakeSocket(), FakeSocket()]
    connect_index = 0

    async def connect(_uri: str, _headers: Mapping[str, str]) -> FakeSocket:
        nonlocal connect_index
        socket = sockets[connect_index]
        connect_index += 1
        return socket

    class Worker:
        def __init__(self) -> None:
            self.close_calls = 0
            self.invalidate_calls = 0
            self.mutation_generation: str | None = None

        def bind_mutation_generation(self, generation: str) -> None:
            self.mutation_generation = generation

        def activate(self) -> None:
            pass

        async def run_once(self) -> ReviewResult:
            return ReviewResult("inactive")

        def invalidate(self) -> None:
            self.invalidate_calls += 1

        async def close(self) -> None:
            self.close_calls += 1

    workers: list[Worker] = []

    def factory(_path: str | None, _callback: ReviewApplied) -> Worker:
        worker = Worker()
        workers.append(worker)
        return worker

    handler = _memory_handler(
        sockets[0],
        None,
        instance_path=str(tmp_path),
        memory_review_factory=factory,
        monkeypatch=monkeypatch,
    )
    handler._connect = connect
    first_startup = await _start_ready(handler, sockets[0])
    await _wait_until(lambda: len(workers) == 1)
    await handler.shutdown()
    await first_startup
    second_startup = await _start_ready(handler, sockets[1])
    await _wait_until(lambda: len(workers) == 2)
    await handler.shutdown()
    await second_startup
    assert len(workers) == 2
    assert all(worker.close_calls == 1 for worker in workers)
    assert workers[0] is not workers[1]


def test_review_flag_refresh_requires_household_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refreshing the opt-in flag cannot enable review without base memory."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", config.HOUSEHOLD_MEMORY_ENABLED)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", config.HOUSEHOLD_MEMORY_REVIEW_ENABLED)
    monkeypatch.setenv("HOUSEHOLD_MEMORY_ENABLED", "0")
    monkeypatch.setenv("HOUSEHOLD_MEMORY_REVIEW_ENABLED", "1")
    refresh_runtime_config_from_env()
    assert config.HOUSEHOLD_MEMORY_ENABLED is False
    assert config.HOUSEHOLD_MEMORY_REVIEW_ENABLED is False
    monkeypatch.setenv("HOUSEHOLD_MEMORY_ENABLED", "1")
    refresh_runtime_config_from_env()
    assert config.HOUSEHOLD_MEMORY_REVIEW_ENABLED is True
