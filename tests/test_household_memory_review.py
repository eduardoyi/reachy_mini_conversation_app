import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from collections.abc import Callable

import pytest
from openai.types.responses.response import Response
from openai.types.responses.response_output_text import ResponseOutputText
from openai.types.responses.response_output_message import ResponseOutputMessage

import reachy_mini_conversation_app.household_memory_review as review_module
from reachy_mini_conversation_app.household_memory import (
    MemoryKind,
    ReviewKeep,
    MemoryEntry,
    ReviewMerge,
    MemoryStatus,
    ReviewPromote,
    MemorySnapshot,
    MemoryCertainty,
    ReviewCheckpoint,
    ReviewApplyResult,
    MemoryConflictError,
    HouseholdMemoryStore,
)
from reachy_mini_conversation_app.household_memory_review import (
    ReviewClocks,
    HouseholdMemoryReview,
)


def _response(text: str, *, status: str = "completed") -> Response:
    output_text = ResponseOutputText.model_construct(annotations=[], text=text, type="output_text")
    message = ResponseOutputMessage.model_construct(
        id="message-1",
        content=[output_text],
        role="assistant",
        status=status,
        type="message",
    )
    return Response.model_construct(
        id="response-1",
        created_at=0.0,
        model="gpt-5.6-luna",
        object="response",
        output=[message],
        status=status,
    )


class _FakeResponses:
    def __init__(self, output_factory: Callable[[dict[str, object]], str]) -> None:
        self.output_factory = output_factory
        self.calls: list[dict[str, object]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.ignore_cancellation = False
        self.outputs: list[str] = []
        self.status = "completed"

    async def create(self, **kwargs: object) -> Response:
        self.calls.append(kwargs)
        self.started.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancellation:
                    raise
                await self.release.wait()
        text = self.outputs.pop(0) if self.outputs else self.output_factory(kwargs)
        return _response(text, status=self.status)


class _FakeClient:
    def __init__(self, output_factory: Callable[[dict[str, object]], str]) -> None:
        self.responses = _FakeResponses(output_factory)
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


class _FakeStore:
    def __init__(self, path: Path, entries: tuple[MemoryEntry, ...] = ()) -> None:
        self.path = path / "memory.v1.json"
        self.entries = entries
        self.revision = 0
        self.checkpoint: ReviewCheckpoint | None = None
        self.apply_calls: list[tuple[object, ...]] = []

    def review_checkpoint(self) -> ReviewCheckpoint | None:
        return self.checkpoint

    def review_candidates(self, _local_day: str) -> tuple[MemoryEntry, ...]:
        watermark = {} if self.checkpoint is None else self.checkpoint.watermark
        return tuple(
            entry
            for entry in self.entries
            if entry.status == "current" and watermark.get(entry.id) != self._fingerprint(entry)
        )

    def review_watermark(self, entries: tuple[MemoryEntry, ...]) -> dict[str, str]:
        return {entry.id: self._fingerprint(entry) for entry in entries}

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(self.revision, self.entries)

    def apply_review(
        self,
        operations: tuple[object, ...],
        *,
        expected_revision: int,
        checkpoint: ReviewCheckpoint,
    ) -> ReviewApplyResult:
        if expected_revision != self.revision:
            raise MemoryConflictError("stale")
        self.apply_calls.append(operations)
        updated = list(self.entries)
        touched: set[str] = set()
        for operation in operations:
            if isinstance(operation, ReviewKeep):
                touched.update(operation.evidence_ids or (operation.memory_id,))
            elif isinstance(operation, ReviewMerge):
                touched.update(operation.evidence_ids)
                for memory_id in operation.merge_ids:
                    index = next(index for index, entry in enumerate(updated) if entry.id == memory_id)
                    updated[index] = replace(updated[index], status="superseded")
            elif isinstance(operation, ReviewPromote):
                touched.update(operation.evidence_ids)
                index = next(index for index, entry in enumerate(updated) if entry.id == operation.memory_id)
                updated[index] = replace(updated[index], kind=operation.target_kind)
            else:
                raise AssertionError(f"unexpected operation {operation!r}")
        self.entries = tuple(updated)
        self.checkpoint = checkpoint
        self.revision += 1
        applied = ReviewApplyResult(MemorySnapshot(self.revision, self.entries), checkpoint, (), len(operations))
        return applied

    @staticmethod
    def _fingerprint(entry: MemoryEntry) -> str:
        import hashlib

        payload = json.dumps(entry.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry(
    entry_id: str,
    *,
    learned_at: datetime,
    text: str = "Clara likes tea",
    subject_id: str | None = "clara",
    kind: MemoryKind = "note",
    certainty: MemoryCertainty = "stated",
    status: MemoryStatus = "current",
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        text=text,
        created_at=int(learned_at.timestamp() * 1000),
        subject_id=subject_id,
        source_person_id=None,
        kind=kind,
        certainty=certainty,
        source_ref=None,
        learned_at=learned_at,
        updated_at=learned_at,
        event_start=None,
        event_end=None,
        active_until=None,
        delete_after=None,
        status=status,
    )


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _review(
    store: _FakeStore,
    client: _FakeClient,
    clock: _MutableClock,
    on_applied: Callable[[MemorySnapshot, ReviewApplyResult], None] | None = None,
) -> HouseholdMemoryReview:
    return HouseholdMemoryReview(
        store,
        "gpt-5.6-luna",
        client=client,
        clocks=ReviewClocks(now=clock.now, monotonic=lambda: 10.0),
        on_applied=on_applied,
    )


@pytest.mark.asyncio
async def test_review_is_active_only_and_sends_bounded_tool_free_luna_request(tmp_path: Path) -> None:
    """Only an active conversation can trigger the bounded review request."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))

    def output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        memory_id = payload["changed_entries"][0]["id"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "action": "promote",
                        "memory_id": memory_id,
                        "target_kind": "fact",
                        "evidence_ids": [memory_id],
                    }
                ]
            }
        )

    client = _FakeClient(output)
    clock = _MutableClock(now)
    callbacks: list[tuple[int, int]] = []
    review = _review(
        store, client, clock, lambda before, result: callbacks.append((before.revision, result.snapshot.revision))
    )

    assert (await review.run_once()).status == "inactive"
    review.activate()
    result = await review.run_once()

    assert result.status == "applied"
    assert store.entries[0].kind == "fact"
    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["model"] == "gpt-5.6-luna"
    assert request["tools"] == []
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert request["max_output_tokens"] == 2_000
    assert request["timeout"] == 30.0
    assert len(str(request["input"])) + len(str(request["instructions"])) <= review_module.MAX_INPUT_CHARS
    assert "untrusted data" in str(request["instructions"])
    payload = json.loads(str(request["input"]))
    assert {"active_until", "delete_after"} <= set(payload["changed_entries"][0])
    assert callbacks == [(0, 1)]
    await review.close()
    assert client.close_count == 1


@pytest.mark.asyncio
async def test_empty_checkpoint_does_not_open_a_request_or_repeat_same_day(tmp_path: Path) -> None:
    """An empty local review checkpoints cleanup without opening a cloud request."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path)
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    clock = _MutableClock(now)
    callbacks: list[tuple[int, int]] = []
    review = _review(
        store, client, clock, lambda before, result: callbacks.append((before.revision, result.snapshot.revision))
    )

    review.activate()
    assert (await review.run_once()).status == "checkpointed"
    assert store.checkpoint is not None
    assert callbacks == [(0, 1)]
    store.entries = (_entry("m1", learned_at=now + timedelta(seconds=1)),)
    review.activate()
    assert (await review.run_once()).status == "budget_blocked"
    assert client.responses.calls == []

    clock.value = now + timedelta(days=1)
    review.activate()
    assert (await review.run_once()).status == "applied"
    assert len(client.responses.calls) == 1
    await review.close()


@pytest.mark.asyncio
async def test_unassigned_entries_are_checkpointed_without_provider_request(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unassigned migrated entries stay local and do not reach the review model."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    entry = _entry("m1", learned_at=now, text="Legacy unassigned detail", subject_id=None, kind="fact")
    store = _FakeStore(tmp_path, (entry,))
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    review = _review(store, client, _MutableClock(now))

    review.activate()
    with caplog.at_level(logging.INFO, logger=review_module.__name__):
        result = await review.run_once()

    assert result.status == "checkpointed"
    assert client.responses.calls == []
    assert store.checkpoint is not None
    assert set(store.checkpoint.watermark) == {entry.id}
    assert review.stats.skipped_unassigned == 1
    assert review.stats.skipped_unchanged == 1
    assert "checkpointed_without_provider_request=1" in caplog.text
    assert entry.text not in caplog.text
    await review.close()


@pytest.mark.asyncio
async def test_mixed_review_excludes_unassigned_entries_from_provider_payload(tmp_path: Path) -> None:
    """A known-owner review never sends an unassigned legacy entry as related data."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    assigned = _entry("m1", learned_at=now, text="Clara likes tea")
    unassigned = _entry("m2", learned_at=now, text="Legacy unassigned detail", subject_id=None, kind="fact")
    store = _FakeStore(tmp_path, (assigned, unassigned))

    def output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        assert {entry["id"] for entry in payload["changed_entries"]} == {assigned.id}
        assert unassigned.text not in str(request["input"])
        return '{"proposals":[]}'

    client = _FakeClient(output)
    review = _review(store, client, _MutableClock(now))
    review.activate()

    assert (await review.run_once()).status == "applied"
    assert len(client.responses.calls) == 1
    assert review.stats.skipped_unassigned == 1
    await review.close()


@pytest.mark.asyncio
async def test_invalid_proposal_is_distinguished_from_malformed_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A structurally valid but unsafe proposal is counted separately from malformed JSON."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    entry = _entry("m1", learned_at=now)
    store = _FakeStore(tmp_path, (entry,))
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    client.responses.outputs = [
        json.dumps({"proposals": [{"action": "resolve", "memory_id": "m1", "evidence_ids": ["m1"]}]}),
        json.dumps({"proposals": [{"action": "keep", "memory_id": "m1", "evidence_ids": ["m1"]}]}),
    ]
    clock = _MutableClock(now)
    review = _review(store, client, clock)

    review.activate()
    with caplog.at_level(logging.INFO, logger=review_module.__name__):
        assert (await review.run_once()).status == "failed"
    assert store.checkpoint is None
    assert review.stats.proposals_rejected == 1
    assert review.stats.malformed_outputs == 0
    assert review.stats.failures == 1
    assert "proposals_rejected=1" in caplog.text
    assert "output_malformed=1" not in caplog.text
    review.activate()
    assert (await review.run_once()).status == "applied"
    assert review.stats.attempts == 2
    assert review.stats.successes == 1
    assert len(client.responses.calls) == 2
    await review.close()


@pytest.mark.asyncio
async def test_malformed_output_is_counted_without_proposal_rejection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Malformed JSON consumes an attempt without applying or checkpointing anything."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))
    client = _FakeClient(lambda _request: "not json")
    review = _review(store, client, _MutableClock(now))
    review.activate()

    with caplog.at_level(logging.INFO, logger=review_module.__name__):
        assert (await review.run_once()).status == "failed"
    assert store.checkpoint is None
    assert review.stats.malformed_outputs == 1
    assert review.stats.proposals_rejected == 0
    assert review.stats.failures == 1
    assert "output_malformed=1" in caplog.text
    assert "proposals_rejected=" not in caplog.text
    await review.close()


@pytest.mark.asyncio
async def test_success_budget_survives_worker_restart(tmp_path: Path) -> None:
    """A successful review remains blocked after a worker is recreated."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))
    clock = _MutableClock(now)

    first_client = _FakeClient(lambda _request: '{"proposals":[]}')
    first = _review(store, first_client, clock)
    first.activate()
    assert (await first.run_once()).status == "applied"
    await first.close()

    second_client = _FakeClient(lambda _request: '{"proposals":[]}')
    second = _review(store, second_client, clock)
    second.activate()
    assert (await second.run_once()).status == "budget_blocked"
    budget = json.loads(second.budget_path.read_text())
    assert budget["attempts"] == 1
    assert budget["successes"] == 1
    assert second_client.responses.calls == []
    await second.close()


@pytest.mark.asyncio
async def test_changed_entries_beyond_input_cap_are_reviewed_across_days(tmp_path: Path) -> None:
    """A large changed set advances by fingerprint batches without starvation."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    entries = tuple(
        _entry(f"m{index:03d}", learned_at=now + timedelta(seconds=index), text=f"Clara detail {index} " + "x" * 240)
        for index in range(100)
    )
    store = _FakeStore(tmp_path, entries)
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    clock = _MutableClock(now)
    review = _review(store, client, clock)

    review.activate()
    assert (await review.run_once()).status == "applied"
    first_payload = json.loads(str(client.responses.calls[0]["input"]))
    first_ids = {entry["id"] for entry in first_payload["changed_entries"]}
    assert len(first_ids) < len(entries)
    assert (
        len(str(client.responses.calls[0]["input"])) + len(str(client.responses.calls[0]["instructions"]))
        <= review_module.MAX_INPUT_CHARS
    )

    clock.value = now + timedelta(days=1)
    review.activate()
    assert (await review.run_once()).status == "applied"
    second_payload = json.loads(str(client.responses.calls[1]["input"]))
    second_ids = {entry["id"] for entry in second_payload["changed_entries"]}
    assert second_ids
    assert not second_ids.issubset(first_ids)
    await review.close()


@pytest.mark.asyncio
async def test_invalidate_fences_a_slow_request_and_does_not_apply_late_output(tmp_path: Path) -> None:
    """Synchronous invalidation fences a slow provider response before apply."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    client.responses.block = True
    client.responses.ignore_cancellation = True
    review = _review(store, client, _MutableClock(now))
    review.activate()
    pending = asyncio.create_task(review.run_once())
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    review.invalidate()
    result = await asyncio.wait_for(pending, timeout=1.0)
    assert result.status == "cancelled"
    assert store.apply_calls == []
    client.responses.release.set()
    await review.close()


@pytest.mark.asyncio
async def test_direct_run_cancellation_cancels_provider_task(tmp_path: Path) -> None:
    """Cancelling the caller also cancels the shielded provider request."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    client.responses.block = True
    review = _review(store, client, _MutableClock(now))
    review.activate()
    pending = asyncio.create_task(review.run_once())
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    pending.cancel()
    assert (await asyncio.wait_for(pending, timeout=1.0)).status == "cancelled"
    assert store.apply_calls == []
    await review.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["correction", "forget"])
async def test_real_store_revision_change_fences_inflight_review(tmp_path: Path, mutation: str) -> None:
    """A real correction or deletion wins over an in-flight review response."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = HouseholdMemoryStore(tmp_path, clock=lambda: now)
    entry = store.remember("Clara likes tea", subject_id="person:clara")
    client = _FakeClient(lambda _request: '{"proposals":[]}')
    client.responses.block = True
    client.responses.ignore_cancellation = True
    review = HouseholdMemoryReview(
        store,
        "gpt-5.6-luna",
        client=client,
        clocks=ReviewClocks(now=lambda: now, monotonic=lambda: 10.0),
    )
    review.activate()
    pending = asyncio.create_task(review.run_once())
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    if mutation == "correction":
        store.update(entry.id, text="Clara prefers coffee")
    else:
        assert store.forget(memory_id=entry.id).removed is not None
    client.responses.release.set()

    assert (await asyncio.wait_for(pending, timeout=1.0)).status == "stale"
    assert store.review_checkpoint() is None
    current = store.list_entries(include_inactive=True)
    if mutation == "correction":
        assert current[0].text == "Clara prefers coffee"
    else:
        assert current == []
    await review.close()


@pytest.mark.asyncio
async def test_parallel_workers_share_two_attempt_budget_and_one_success(tmp_path: Path) -> None:
    """Parallel workers cannot exceed two reservations or two daily writes."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    store = HouseholdMemoryStore(tmp_path, clock=lambda: now)
    store.remember("Clara likes tea", subject_id="person:clara")
    clients = [_FakeClient(lambda _request: '{"proposals":[]}') for _ in range(2)]
    workers = [
        HouseholdMemoryReview(
            HouseholdMemoryStore(tmp_path, clock=lambda: now),
            "gpt-5.6-luna",
            client=client,
            clocks=ReviewClocks(now=lambda: now, monotonic=lambda: 10.0),
        )
        for client in clients
    ]
    for worker in workers:
        worker.activate()
    results = await asyncio.gather(*(worker.run_once() for worker in workers))

    budget = json.loads(workers[0].budget_path.read_text(encoding="utf-8"))
    assert budget["attempts"] == 2
    assert budget["successes"] == 1
    assert sum(result.status == "applied" for result in results) == 2
    assert HouseholdMemoryStore(tmp_path, clock=lambda: now).review_checkpoint() is not None
    for worker in workers:
        await worker.close()


@pytest.mark.asyncio
async def test_tentative_promotion_and_unknown_operations_are_rejected(tmp_path: Path) -> None:
    """Tentative notes cannot be promoted to facts by the review worker."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    entry = _entry("m1", learned_at=now, certainty="tentative")
    store = _FakeStore(tmp_path, (entry,))
    client = _FakeClient(
        lambda _request: json.dumps(
            {
                "proposals": [
                    {
                        "action": "promote",
                        "memory_id": "m1",
                        "target_kind": "fact",
                        "evidence_ids": ["m1"],
                    }
                ]
            }
        )
    )
    review = _review(store, client, _MutableClock(now))
    review.activate()

    assert (await review.run_once()).status == "failed"
    assert store.apply_calls == []
    assert store.checkpoint is None
    await review.close()


@pytest.mark.asyncio
async def test_incompatible_merge_is_rejected_before_store_apply(tmp_path: Path) -> None:
    """The worker rejects a merge that would shorten retention or change kind."""
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    kept = _entry("m1", learned_at=now, kind="note")
    duplicate = _entry("m2", learned_at=now, kind="situation")
    store = _FakeStore(tmp_path, (kept, duplicate))
    client = _FakeClient(
        lambda _request: json.dumps(
            {
                "proposals": [
                    {
                        "action": "merge",
                        "keep_id": "m1",
                        "merge_ids": ["m2"],
                        "evidence_ids": ["m1", "m2"],
                    }
                ]
            }
        )
    )
    review = _review(store, client, _MutableClock(now))
    review.activate()

    assert (await review.run_once()).status == "failed"
    assert store.apply_calls == []
    assert store.checkpoint is None
    await review.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {"version": True, "local_date": "2026-09-13", "attempts": 0, "successes": 0},
        {"version": 1, "local_date": "2026-09-14", "attempts": 0, "successes": 0},
        {"version": 1, "local_date": "nonsense", "attempts": 0, "successes": 0},
    ],
)
async def test_invalid_or_future_budget_skips_without_network(tmp_path: Path, record: dict[str, object]) -> None:
    """Invalid or future budget skips without network."""
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))
    client = _FakeClient(lambda _: '{"proposals":[]}')
    review = _review(store, client, _MutableClock(now))
    review.budget_path.write_text(json.dumps(record))
    review.activate()
    assert (await review.run_once()).status == "budget_blocked"
    assert client.responses.calls == []
    await review.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "incomplete", "offline"])
async def test_failed_requests_never_commit_and_have_two_attempt_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Failed requests never commit and have two attempt limit."""
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    store = _FakeStore(tmp_path, (_entry("m1", learned_at=now),))
    monkeypatch.setattr(review_module, "REQUEST_TIMEOUT_S", 0.01)
    for attempt in range(3):

        def output(_request: dict[str, object]) -> str:
            if failure == "offline":
                raise OSError("offline")
            return '{"proposals":[]}'

        client = _FakeClient(output)
        client.responses.block = failure == "timeout"
        client.responses.status = "incomplete" if failure == "incomplete" else "completed"
        worker = _review(store, client, _MutableClock(now))
        worker.activate()
        result = await worker.run_once()
        assert result.status == ("budget_blocked" if attempt == 2 else "failed")
        assert store.review_checkpoint() is None
        await worker.close()
