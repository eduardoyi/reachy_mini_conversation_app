"""Provider-neutral household-memory lifecycle tests."""

import json
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from reachy_mini_conversation_app.household_memory import (
    ReviewKeep,
    MemorySnapshot,
    ReviewCheckpoint,
    HouseholdMemoryStore,
)
from reachy_mini_conversation_app.household_memory_session import MemorySession, MemorySessionCloseError
from reachy_mini_conversation_app.household_memory_receipts import (
    MemoryMutationChange,
    MemoryMutationReceipt,
    MutationWriterContext,
    mutation_writer,
    memory_mutation_bus,
)


class _Capture:
    """Small worker fake with optional cleanup gate."""

    def __init__(self, close_gate: asyncio.Event | None = None) -> None:
        self.close_gate = close_gate
        self.start_gate: asyncio.Event | None = None
        self.start_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_calls = 0
        self.generation = ""
        self.invalidated = 0
        self.fragments: list[tuple[str, str, int, int]] = []

    async def start(self) -> None:
        """Start the fake worker."""
        self.start_started.set()
        if self.start_gate is not None:
            await self.start_gate.wait()

    def bind_mutation_generation(self, generation: str) -> None:
        """Record the owning activation."""
        self.generation = generation

    def add_fragment(self, role: str, text: str, start_ms: int, end_ms: int) -> None:
        """Record one forwarded transcript fragment."""
        self.fragments.append((role, text, start_ms, end_ms))

    def invalidate(self) -> None:
        """Record a fence request."""
        self.invalidated += 1

    def rebase_after_review(self, _previous_revision: int, _current_revision: int) -> None:
        """Accept an acknowledged review rebase."""

    async def close(self) -> None:
        """Wait for the test to release cleanup when configured."""
        self.close_calls += 1
        self.close_started.set()
        if self.close_gate is not None:
            await self.close_gate.wait()


def _store_factory(store: HouseholdMemoryStore):
    return lambda _path: store


def _session(store: HouseholdMemoryStore, **kwargs: object) -> MemorySession:
    return MemorySession(
        str(store.path.parent),
        store_factory=_store_factory(store),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_disabled_parent_flag_never_constructs_store_or_workers(tmp_path: Path) -> None:
    """A disabled household flag wins over conflicting child flags."""
    calls: list[str] = []

    def store_factory(_path: str | None) -> HouseholdMemoryStore:
        calls.append("store")
        raise AssertionError("disabled memory must not construct its store")

    def capture_factory(_path: str | None) -> _Capture:
        calls.append("capture")
        raise AssertionError("disabled memory must not construct capture")

    session = MemorySession(
        str(tmp_path),
        enabled=False,
        capture_enabled=True,
        review_enabled=True,
        store_factory=store_factory,
        capture_factory=capture_factory,
    )
    session.prepare()
    await session.start()
    assert calls == []
    await session.close()


@pytest.mark.asyncio
async def test_start_rechecks_same_id_change_before_constructing_workers(tmp_path: Path) -> None:
    """A correction between prompt preparation and readiness fences activation."""
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    capture_calls: list[str] = []

    def capture_factory(_path: str | None) -> _Capture:
        capture_calls.append("capture")
        return _Capture()

    session = _session(store, capture_enabled=True, capture_factory=capture_factory)
    session.prepare()
    store.update(entry.id, text="Alex likes tea")
    with pytest.raises(RuntimeError, match="changed before activation"):
        await session.start()
    assert capture_calls == []
    assert session.fenced
    await session.close()


@pytest.mark.asyncio
async def test_start_closes_capture_and_skips_review_after_capture_fence(
    tmp_path: Path,
) -> None:
    """A fence during capture startup prevents review construction and closes capture."""
    store = HouseholdMemoryStore(tmp_path)
    capture = _Capture()
    capture.start_gate = asyncio.Event()
    review_calls: list[str] = []

    def review_factory(_path: str | None, _on_applied: object) -> _Capture:
        review_calls.append("review")
        return _Capture()

    session = _session(
        store,
        capture_enabled=True,
        review_enabled=True,
        capture_factory=lambda _path: capture,
        review_factory=review_factory,
    )
    session.prepare()
    start_task = asyncio.create_task(session.start())
    await capture.start_started.wait()
    session.invalidate()
    capture.start_gate.set()

    with pytest.raises(RuntimeError, match="during activation"):
        await start_task
    assert capture.close_calls == 1
    assert review_calls == []
    assert session.review is None
    await session.close()


def test_sync_context_guard_can_fence_without_a_running_event_loop(tmp_path: Path) -> None:
    """A synchronous provider boundary can fence a changed store without an event loop."""
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    session = _session(store)
    session.prepare()
    store.update(entry.id, text="Alex likes tea")

    assert not session.context_is_current()
    assert session.fenced


@pytest.mark.asyncio
async def test_trusted_correction_rebases_exact_after_value_then_external_edit_fences(tmp_path: Path) -> None:
    """A trusted update uses its receipt value before checking for later edits."""
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    session = _session(store)
    session.prepare()

    with mutation_writer(MutationWriterContext("tool.update", session.generation)):
        store.update(entry.id, text="Alex likes tea")
    assert session.context_is_current()
    snapshot = session.snapshot
    assert snapshot is not None
    assert json.loads(snapshot.entries[entry.id])["text"] == "Alex likes tea"

    store.update(entry.id, text="Alex likes coffee")
    assert not session.context_is_current()
    assert session.fenced
    await session.close()


@pytest.mark.asyncio
async def test_receipt_revision_must_advance_exactly_one(tmp_path: Path) -> None:
    """A malformed revision jump cannot advance an active context."""
    store = HouseholdMemoryStore(tmp_path)
    session = _session(store)
    session.prepare()
    memory_mutation_bus().publish(
        store.path,
        MemoryMutationReceipt(
            source="migration",
            generation="untrusted",
            before_revision=0,
            after_revision=2,
            changes=(MemoryMutationChange("m_missing", None, None),),
        ),
    )
    assert not session.context_is_current()
    assert session.fenced
    await session.close()


@pytest.mark.asyncio
async def test_receipt_revision_must_be_nonnegative(tmp_path: Path) -> None:
    """A negative receipt revision fails closed without reading private data."""
    store = HouseholdMemoryStore(tmp_path)
    session = _session(store)
    session.prepare()
    memory_mutation_bus().publish(
        store.path,
        MemoryMutationReceipt(
            source="migration",
            generation="untrusted",
            before_revision=-1,
            after_revision=0,
            changes=(),
        ),
    )
    assert not session.context_is_current()
    assert session.fenced
    await session.close()


@pytest.mark.asyncio
async def test_expiry_fences_even_when_store_revision_does_not_change(tmp_path: Path) -> None:
    """An active entry expiring at the same revision invalidates the prompt."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HouseholdMemoryStore(tmp_path, clock=lambda: now)
    store.remember("Short lived note", subject_id="person:alex", kind="note")
    session = _session(store)
    session.prepare()
    seeded_revision = session.snapshot.revision if session.snapshot is not None else -1
    now = now + timedelta(days=2)
    assert store.active_snapshot().revision == seeded_revision
    assert not session.context_is_current()
    assert session.fenced
    await session.close()


@pytest.mark.asyncio
async def test_spoken_forget_removes_only_acknowledged_id_and_keeps_session(tmp_path: Path) -> None:
    """A trusted spoken forget pauses memory work while keeping conversation state live."""
    store = HouseholdMemoryStore(tmp_path)
    forgotten = store.remember("Alex likes jazz", subject_id="person:alex")
    kept = store.remember("Alex likes tea", subject_id="person:alex")
    capture = _Capture()
    session = _session(store, capture_enabled=True, capture_factory=lambda _path: capture)
    session.prepare()
    await session.start()

    with mutation_writer(MutationWriterContext("tool.forget", session.generation)):
        result = store.forget(memory_id=forgotten.id)
    assert result.removed is not None
    assert session.notify_spoken_forget(forgotten.id)
    assert session.active
    assert capture.invalidated == 1
    snapshot = session.snapshot
    assert snapshot is not None
    assert forgotten.id not in snapshot.entries
    assert kept.id in snapshot.entries
    session.add_fragment("user", "late", 0, 1)
    assert capture.fragments == []
    await session.close()


@pytest.mark.asyncio
async def test_review_receipt_allows_exact_transition_rebase(tmp_path: Path) -> None:
    """A review rebase is accepted only for its committed revision pair."""
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    session = _session(store)
    session.prepare()
    before = store.snapshot()
    checkpoint = ReviewCheckpoint("2026-01-02", store.review_watermark(before.entries))
    with mutation_writer(MutationWriterContext("review", session.generation)):
        result = store.apply_review(
            (ReviewKeep(entry.id, (entry.id,)),),
            expected_revision=before.revision,
            checkpoint=checkpoint,
        )
    assert session.rebase_after_review(before, result)
    assert session.active
    await session.close()


@pytest.mark.asyncio
async def test_close_reports_active_worker_and_retries_without_duplicate_close(tmp_path: Path) -> None:
    """Cleanup failure is visible and the tracked worker can finish on retry."""
    close_gate = asyncio.Event()
    capture = _Capture(close_gate)
    store = HouseholdMemoryStore(tmp_path)
    session = _session(store, capture_enabled=True, capture_factory=lambda _path: capture, close_timeout_s=0.01)
    session.prepare()
    await session.start()
    close_task = asyncio.create_task(session.close())
    await capture.close_started.wait()
    with pytest.raises(MemorySessionCloseError, match="capture"):
        await close_task
    assert capture.close_calls == 1
    close_gate.set()
    await session.close()
    assert capture.close_calls == 1
    assert not session.active


@pytest.mark.asyncio
async def test_invalidation_callback_can_close_session_without_cycle(tmp_path: Path) -> None:
    """Provider invalidation may await close without making the session await itself."""
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    callback_reasons: list[str] = []
    session: MemorySession

    async def on_invalidated(reason: str) -> None:
        callback_reasons.append(reason)
        await session.close()

    session = MemorySession(
        str(tmp_path),
        store_factory=_store_factory(store),
        on_invalidated=on_invalidated,
    )
    session.prepare()
    store.forget(memory_id=entry.id)
    assert not await asyncio.wait_for(session.check_current(), timeout=0.5)
    assert callback_reasons == ["memory_changed"]
    assert not session.active


@pytest.mark.asyncio
async def test_close_does_not_reuse_pending_provider_invalidation_callback(tmp_path: Path) -> None:
    """A new activation waits for provider-owned invalidation teardown to finish."""
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("Alex likes jazz", subject_id="person:alex")
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def on_invalidated(_reason: str) -> None:
        callback_started.set()
        await release_callback.wait()

    session = MemorySession(
        str(tmp_path),
        store_factory=_store_factory(store),
        on_invalidated=on_invalidated,
    )
    session.prepare()
    store.forget(memory_id=entry.id)
    validation_task = asyncio.create_task(session.check_current())
    await callback_started.wait()
    await session.close()
    with pytest.raises(MemorySessionCloseError, match="invalidation"):
        session.prepare()
    release_callback.set()
    assert not await validation_task
    session.prepare()
    await session.close()


@pytest.mark.asyncio
async def test_receipts_are_path_isolated_and_writer_thread_does_not_deadlock(tmp_path: Path) -> None:
    """Concurrent additive writes stay isolated to their store path."""
    first_store = HouseholdMemoryStore(tmp_path / "first")
    second_store = HouseholdMemoryStore(tmp_path / "second")
    first = _session(first_store)
    second = _session(second_store)
    first.prepare()
    second.prepare()

    def write_first() -> None:
        for index in range(8):
            first_store.remember(f"First note {index}", subject_id="person:alex")

    writer = threading.Thread(target=write_first)
    writer.start()
    while writer.is_alive():
        assert first.context_is_current()
        assert second.context_is_current()
        await asyncio.sleep(0)
    writer.join()
    assert first.context_is_current()
    assert second.context_is_current()
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_receipt_drain_preserves_writes_after_snapshot_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write queued after the snapshot stays for the next validation pass."""
    store = HouseholdMemoryStore(tmp_path)
    session = _session(store)
    bus = memory_mutation_bus()
    snapshot_taken = threading.Event()
    revision_three_blocked = threading.Event()
    release_revision_three = threading.Event()
    writer_done = threading.Event()
    snapshot_calls = 0
    original_snapshot = store.active_snapshot

    def block_revision_three(receipt: MemoryMutationReceipt) -> None:
        if receipt.after_revision == 3:
            revision_three_blocked.set()
            release_revision_three.wait(timeout=1.0)

    unsubscribe_blocker = bus.subscribe(store.path, block_revision_three)
    session.prepare()

    def coordinated_snapshot() -> MemorySnapshot:
        nonlocal snapshot_calls
        snapshot_calls += 1
        snapshot = original_snapshot()
        if snapshot_calls == 1:
            snapshot_taken.set()
            assert revision_three_blocked.wait(timeout=1.0)
        elif snapshot_calls == 2:
            release_revision_three.set()
        return snapshot

    monkeypatch.setattr(store, "active_snapshot", coordinated_snapshot)

    def write_revisions() -> None:
        assert snapshot_taken.wait(timeout=1.0)
        store.remember("First note", subject_id="person:alex")
        store.remember("Second note", subject_id="person:alex")
        store.remember("Third note", subject_id="person:alex")
        writer_done.set()

    writer = threading.Thread(target=write_revisions)
    writer.start()
    try:
        assert session.context_is_current()
        release_revision_three.set()
        writer.join(timeout=1.0)
        assert writer_done.is_set()
        assert session.context_is_current()
        assert not session.fenced
    finally:
        release_revision_three.set()
        writer.join(timeout=1.0)
        unsubscribe_blocker()
        await session.close()
