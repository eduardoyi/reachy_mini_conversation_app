"""Focused tests for the bounded daily household-memory review store."""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from reachy_mini_conversation_app.household_memory import (
    MemoryKind,
    ReviewKeep,
    MemoryEntry,
    ReviewMerge,
    ReviewPromote,
    MemoryStoreError,
    ReviewCheckpoint,
    MemoryConflictError,
    HouseholdMemoryStore,
    MemoryCorruptionError,
    MemoryValidationError,
    memory_path_for_instance,
)


UTC = timezone.utc


class FakeClock:
    """Mutable clock for deterministic lifecycle checks."""

    def __init__(self, value: datetime) -> None:
        """Set the current fake time."""
        self.value = value

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self.value


def _checkpoint(store: HouseholdMemoryStore, day: str, entries: tuple[MemoryEntry, ...]) -> ReviewCheckpoint:
    return ReviewCheckpoint(local_day=day, watermark=store.review_watermark(entries))


def test_review_merges_selected_current_entries_and_commits_checkpoint_atomically(tmp_path: Path) -> None:
    """A review keeps one existing text and stores its day with the mutation."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    kept = store.remember("Eduardo likes tea", subject_id="person:eduardo", kind="note")
    duplicate = store.remember("Eduardo likes tea in the morning", subject_id="person:eduardo", kind="note")
    before = store.snapshot()
    checkpoint = _checkpoint(store, "2026-09-13", before.entries)

    result = store.apply_review(
        [ReviewMerge(kept.id, (duplicate.id,), (kept.id, duplicate.id))],
        expected_revision=before.revision,
        checkpoint=checkpoint,
    )

    assert result.snapshot.revision == before.revision + 1
    assert result.removed_ids == ()
    assert result.applied_operations == 1
    entries = {entry.id: entry for entry in store.snapshot().entries}
    assert entries[kept.id].text == kept.text
    assert entries[kept.id].learned_at == kept.learned_at
    assert entries[kept.id].delete_after == kept.delete_after
    assert entries[duplicate.id].status == "superseded"
    assert result.checkpoint.local_day == "2026-09-13"
    persisted = json.loads(memory_path_for_instance(tmp_path).read_text(encoding="utf-8"))
    assert persisted["review_checkpoint"]["local_day"] == "2026-09-13"
    assert set(persisted["review_checkpoint"]["watermark"]) == {kept.id, duplicate.id}


def test_stale_review_revision_does_not_prune_or_write_checkpoint(tmp_path: Path) -> None:
    """A stale review fails before retention cleanup or any metadata write."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    entry = store.remember("Temporary note", subject_id="household")
    before = store.snapshot()
    checkpoint = _checkpoint(store, "2026-09-13", before.entries)
    store.remember("A newer note", subject_id="household")
    path = memory_path_for_instance(tmp_path)
    bytes_before = path.read_bytes()

    with pytest.raises(MemoryConflictError):
        store.apply_review([], expected_revision=before.revision, checkpoint=checkpoint)

    assert path.read_bytes() == bytes_before
    assert any(candidate.id == entry.id for candidate in store.list_entries(include_inactive=True))
    assert store.review_checkpoint() is None


def test_review_retry_is_idempotent_with_original_and_result_checkpoint(tmp_path: Path) -> None:
    """A lost response can be retried without repeating the merge or revision."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    kept = store.remember("Likes tea", subject_id="person:a")
    duplicate = store.remember("Likes tea in the morning", subject_id="person:a")
    before = store.snapshot()
    checkpoint = _checkpoint(store, "2026-09-13", before.entries)
    operation = ReviewMerge(kept.id, (duplicate.id,), (kept.id, duplicate.id))

    first = store.apply_review([operation], expected_revision=before.revision, checkpoint=checkpoint)
    bytes_after_first = memory_path_for_instance(tmp_path).read_bytes()
    retried_original = store.apply_review([operation], expected_revision=before.revision, checkpoint=checkpoint)
    retried_result = store.apply_review(
        [operation], expected_revision=first.snapshot.revision, checkpoint=first.checkpoint
    )

    assert retried_original.snapshot == first.snapshot
    assert retried_result.snapshot == first.snapshot
    assert memory_path_for_instance(tmp_path).read_bytes() == bytes_after_first


def test_only_one_successful_review_is_allowed_per_local_day(tmp_path: Path) -> None:
    """The checkpoint prevents a second distinct review on the same local day."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    first = store.remember("First note", subject_id="household")
    first_snapshot = store.snapshot()
    first_checkpoint = _checkpoint(store, "2026-09-13", (first,))
    store.apply_review([ReviewKeep(first.id)], expected_revision=first_snapshot.revision, checkpoint=first_checkpoint)

    second = store.remember("Second note", subject_id="household")
    second_snapshot = store.snapshot()
    second_checkpoint = _checkpoint(store, "2026-09-13", (second,))
    with pytest.raises(MemoryConflictError):
        store.apply_review([], expected_revision=second_snapshot.revision, checkpoint=second_checkpoint)

    assert [entry.text for entry in store.review_candidates("2026-09-13")] == [second.text]


def test_truncated_review_watermark_does_not_hide_omitted_entries(tmp_path: Path) -> None:
    """Only entries included in a bounded request become checkpointed."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    first = store.remember("First note", subject_id="household")
    second = store.remember("Second note", subject_id="household")
    before = store.snapshot()
    checkpoint = ReviewCheckpoint("2026-09-13", store.review_watermark((first,)))

    store.apply_review([], expected_revision=before.revision, checkpoint=checkpoint)

    assert [entry.id for entry in store.review_candidates("2026-09-14")] == [second.id]


def test_review_prunes_retention_expired_entries_and_returns_removed_ids(tmp_path: Path) -> None:
    """The successful review write performs retention cleanup in the same envelope."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    expired = store.remember("Old note", subject_id="household")
    before = store.snapshot()
    clock.value += timedelta(days=8)

    result = store.apply_review(
        [],
        expected_revision=before.revision,
        checkpoint=ReviewCheckpoint("2026-09-21", {}),
    )

    assert result.removed_ids == (expired.id,)
    assert result.snapshot.entries == ()
    persisted = json.loads(memory_path_for_instance(tmp_path).read_text(encoding="utf-8"))
    assert persisted["entries"] == []


def test_review_refuses_unknown_subject_and_tentative_fact_promotion(tmp_path: Path) -> None:
    """Review cannot assign unknown ownership or upgrade certainty."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "facts": [{"id": "legacy", "text": "Unknown note", "createdAt": 1}]}),
        encoding="utf-8",
    )
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    unknown = store.migrate_v1()[0]
    before = store.snapshot()
    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [ReviewPromote(unknown.id, "fact", (unknown.id,))],
            expected_revision=before.revision,
            checkpoint=_checkpoint(store, "2026-09-13", (unknown,)),
        )

    tentative = store.remember("Maybe likes tea", subject_id="person:a", certainty="tentative")
    before_tentative = store.snapshot()
    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [ReviewPromote(tentative.id, "fact", (tentative.id,))],
            expected_revision=before_tentative.revision,
            checkpoint=_checkpoint(store, "2026-09-14", (tentative,)),
        )
    assert store.snapshot().revision == before_tentative.revision


def test_situation_promotion_preserves_original_evidence_lifetime(tmp_path: Path) -> None:
    """Promotion uses existing evidence and does not refresh a note's deadlines."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    note = store.remember("The luggage is missing", subject_id="household")
    original_active_until = note.active_until
    original_delete_after = note.delete_after
    clock.value += timedelta(hours=12)
    before = store.snapshot()

    result = store.apply_review(
        [ReviewPromote(note.id, "situation", (note.id,))],
        expected_revision=before.revision,
        checkpoint=_checkpoint(store, "2026-09-14", (note,)),
    )

    promoted = result.snapshot.entries[0]
    assert promoted.kind == "situation"
    assert promoted.text == note.text
    assert promoted.learned_at == note.learned_at
    assert promoted.active_until == original_active_until
    assert promoted.delete_after == original_delete_after


def test_retained_inactive_note_remains_eligible_for_review_after_three_days(tmp_path: Path) -> None:
    """Retention keeps a missed note reviewable after it leaves active context."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    note = store.remember("Birthday is in May", subject_id="person:a")
    clock.value += timedelta(days=3)
    before = store.snapshot()

    assert [entry.id for entry in store.review_candidates("2026-09-16")] == [note.id]
    result = store.apply_review(
        [ReviewPromote(note.id, "fact", (note.id,))],
        expected_revision=before.revision,
        checkpoint=_checkpoint(store, "2026-09-16", (note,)),
    )

    assert result.snapshot.entries[0].kind == "fact"


def test_review_merge_rejects_cross_subject_evidence(tmp_path: Path) -> None:
    """Review cannot merge entries belonging to different subjects."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    first = store.remember("Likes tea", subject_id="person:a")
    second = store.remember("Likes tea", subject_id="person:b")
    before = store.snapshot()

    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [ReviewMerge(first.id, (second.id,), (first.id, second.id))],
            expected_revision=before.revision,
            checkpoint=_checkpoint(store, "2026-09-13", before.entries),
        )
    assert store.snapshot() == before


@pytest.mark.parametrize(
    ("keep_kind", "duplicate_kind", "keep_end", "duplicate_end"),
    [
        ("note", "fact", None, None),
        ("plan", "plan", "2026-09-20", "2026-09-21"),
    ],
)
def test_review_merge_rejects_incompatible_kind_or_event_dates(
    tmp_path: Path,
    keep_kind: MemoryKind,
    duplicate_kind: MemoryKind,
    keep_end: str | None,
    duplicate_end: str | None,
) -> None:
    """Review cannot merge entries that would discard kind or date meaning."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    kept = store.remember(
        "A memory to keep",
        subject_id="household",
        kind=keep_kind,
        event_end=keep_end,
    )
    duplicate = store.remember(
        "A distinct memory",
        subject_id="household",
        kind=duplicate_kind,
        event_end=duplicate_end,
    )
    before = store.snapshot()

    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [ReviewMerge(kept.id, (duplicate.id,), (kept.id, duplicate.id))],
            expected_revision=before.revision,
            checkpoint=_checkpoint(store, "2026-09-13", before.entries),
        )
    assert store.snapshot() == before


def test_review_merge_keeps_the_longest_retention(tmp_path: Path) -> None:
    """Keeping an older note while superseding a newer one extends the kept note to the newer deadlines."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    kept = store.remember("An earlier note", subject_id="household")
    clock.value += timedelta(days=2)
    duplicate = store.remember("A later note", subject_id="household")
    before = store.snapshot()

    store.apply_review(
        [ReviewMerge(kept.id, (duplicate.id,), (kept.id, duplicate.id))],
        expected_revision=before.revision,
        checkpoint=_checkpoint(store, "2026-09-13", before.entries),
    )

    entries = {entry.id: entry for entry in store.list_entries(include_inactive=True)}
    assert entries[kept.id].text == "An earlier note" and entries[kept.id].status == "current"
    assert entries[kept.id].active_until == duplicate.active_until
    assert entries[kept.id].delete_after == duplicate.delete_after
    assert entries[duplicate.id].status == "superseded"


def test_review_can_merge_notes_and_promote_the_kept_one(tmp_path: Path) -> None:
    """Two notes saying the same lasting thing become one fact in a single review."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    kept = store.remember("Ana likes fresh figs", subject_id="ana")
    duplicate = store.remember("Ana really loves figs", subject_id="ana")
    before = store.snapshot()

    store.apply_review(
        [
            ReviewMerge(kept.id, (duplicate.id,), (kept.id, duplicate.id)),
            ReviewPromote(kept.id, "fact", (kept.id,)),
        ],
        expected_revision=before.revision,
        checkpoint=_checkpoint(store, "2026-09-13", before.entries),
    )

    current = store.list_entries()
    assert [(entry.text, entry.kind, entry.active_until) for entry in current] == [
        ("Ana likes fresh figs", "fact", None)
    ]


def test_review_cannot_reopen_resolved_entry(tmp_path: Path) -> None:
    """Resolved history cannot be selected as current review evidence."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    resolved = store.remember("Trip was cancelled", subject_id="household", kind="situation")
    store.update(resolved.id, status="resolved")
    before = store.snapshot()

    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [ReviewKeep(resolved.id)],
            expected_revision=before.revision,
            checkpoint=ReviewCheckpoint("2026-09-13", {}),
        )
    assert store.snapshot() == before


def test_invalid_later_review_operation_rolls_back_earlier_operation(tmp_path: Path) -> None:
    """Validation of the whole review batch prevents partial writes."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    first = store.remember("Likes tea", subject_id="person:a")
    second = store.remember("Likes tea in the morning", subject_id="person:a")
    before = store.snapshot()
    checkpoint = _checkpoint(store, "2026-09-13", before.entries)

    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [
                ReviewMerge(first.id, (second.id,), (first.id, second.id)),
                ReviewPromote("missing", "fact", ("missing",)),
            ],
            expected_revision=before.revision,
            checkpoint=checkpoint,
        )
    assert store.snapshot() == before
    assert store.review_checkpoint() is None


def test_corrupt_review_checkpoint_is_read_only_failure(tmp_path: Path) -> None:
    """Malformed review metadata never gets repaired by a read."""
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "revision": 1,
        "entries": [],
        "review_checkpoint": {"local_day": "not-a-date", "watermark": {}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = HouseholdMemoryStore(tmp_path, clock=FakeClock(datetime(2026, 9, 13, tzinfo=UTC)))

    with pytest.raises(MemoryCorruptionError):
        store.review_checkpoint()
    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_review_write_failure_preserves_entries_and_checkpoint(tmp_path: Path) -> None:
    """An atomic replacement failure leaves the old envelope untouched."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    entry = store.remember("Keep this", subject_id="household")
    before = store.snapshot()
    checkpoint = _checkpoint(store, "2026-09-13", before.entries)
    path = memory_path_for_instance(tmp_path)
    bytes_before = path.read_bytes()

    with patch.object(Path, "replace", side_effect=OSError("replacement failed")):
        with pytest.raises(MemoryStoreError):
            store.apply_review([ReviewKeep(entry.id)], expected_revision=before.revision, checkpoint=checkpoint)

    assert path.read_bytes() == bytes_before
    assert store.review_checkpoint() is None


def test_forget_and_clear_remove_deleted_ids_from_checkpoint(tmp_path: Path) -> None:
    """Legacy deletion paths cannot leave stale review IDs that later resurrect data."""
    clock = FakeClock(datetime(2026, 9, 13, 10, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    first = store.remember("First note", subject_id="household")
    before = store.snapshot()
    checkpoint = _checkpoint(store, "2026-09-13", before.entries)
    store.apply_review([], expected_revision=before.revision, checkpoint=checkpoint)
    store.forget(memory_id=first.id)
    after_forget = store.review_checkpoint()
    assert after_forget is not None
    assert first.id not in after_forget.watermark

    second = store.remember("Second note", subject_id="household")
    second_snapshot = store.snapshot()
    second_checkpoint = _checkpoint(store, "2026-09-14", (second,))
    store.apply_review([], expected_revision=second_snapshot.revision, checkpoint=second_checkpoint)
    store.clear()
    after_clear = store.review_checkpoint()
    assert after_clear is not None
    assert after_clear.watermark == {}


def test_offline_birthday_can_be_promoted_before_retention_expires(tmp_path: Path) -> None:
    """Offline birthday can be promoted before retention expires."""
    clock = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))
    store = HouseholdMemoryStore(tmp_path, clock=clock)
    note = store.remember("Alex's birthday is May 4", subject_id="person:alex")
    clock.value += timedelta(days=3)
    assert note.id in {entry.id for entry in store.review_candidates()}
    before = store.snapshot()
    result = store.apply_review(
        [ReviewPromote(note.id, "fact", (note.id,))],
        expected_revision=before.revision,
        checkpoint=_checkpoint(store, "2026-09-16", before.entries),
    )
    assert result.snapshot.entries[0].delete_after is None
    assert result.snapshot.entries[0].learned_at == note.learned_at


@pytest.mark.parametrize("other_subject,resolved", [(True, False), (False, True)])
def test_invalid_merge_is_atomic(tmp_path: Path, other_subject: bool, resolved: bool) -> None:
    """Invalid merge is atomic."""
    store = HouseholdMemoryStore(tmp_path)
    first = store.remember("Likes tea", subject_id="person:a")
    second = store.remember("Likes tea too", subject_id="person:b" if other_subject else "person:a")
    if resolved:
        store.update(second.id, status="resolved")
    before = store.snapshot()
    original = store.path.read_bytes()
    with pytest.raises(MemoryValidationError):
        store.apply_review(
            [ReviewKeep(first.id), ReviewMerge(first.id, (second.id,), (first.id, second.id))],
            expected_revision=before.revision,
            checkpoint=_checkpoint(store, "2026-09-13", before.entries),
        )
    assert store.path.read_bytes() == original
