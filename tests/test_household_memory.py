"""Focused tests for the structured household memory lifecycle."""

import os
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from reachy_mini_conversation_app.household_memory import (
    MAX_FACTS,
    MAX_NOTES,
    CaptureMutation,
    MemoryStoreError,
    MemoryCapacityError,
    MemoryConflictError,
    HouseholdMemoryStore,
    MemoryCorruptionError,
    MemoryValidationError,
    MigrationRequiredError,
    memory_path_for_instance,
)


UTC = timezone.utc


class FakeClock:
    """Mutable deterministic clock used by lifecycle tests."""

    def __init__(self, value: datetime) -> None:
        """Set the current fake time."""
        self.value = value

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self.value


def _store(tmp_path: Path, clock: FakeClock | None = None) -> HouseholdMemoryStore:
    return HouseholdMemoryStore(tmp_path, clock=clock)


def test_remember_sets_attribution_and_lifetimes(tmp_path: Path) -> None:
    """Remembered entries carry explicit attribution and bounded defaults."""
    clock = FakeClock(datetime(2026, 9, 11, 12, tzinfo=UTC))
    store = _store(tmp_path, clock)

    note = store.remember("  likes   jazz  ", subject_id="person:eduardo", source_person_id="person:adriana")
    situation = store.remember("The luggage is missing", subject_id="household", kind="situation")
    plan = store.remember(
        "Maybe Porto", subject_id="household", kind="plan", certainty="tentative", event_end="2026-09-20"
    )
    fact = store.remember("Birthday is in May", subject_id="household", kind="fact")

    assert note.text == "likes jazz"
    assert note.subject_id == "person:eduardo"
    assert note.source_person_id == "person:adriana"
    assert note.active_until == clock.value + timedelta(hours=48)
    assert note.delete_after == clock.value + timedelta(days=7)
    assert situation.active_until == clock.value + timedelta(days=7)
    assert situation.delete_after == clock.value + timedelta(days=37)
    assert plan.active_until == datetime(2026, 9, 27, tzinfo=UTC)
    assert plan.delete_after == datetime(2026, 10, 27, tzinfo=UTC)
    assert fact.active_until is None
    assert fact.delete_after is None


def test_unknown_subject_is_refused_for_new_durable_memory(tmp_path: Path) -> None:
    """An unknown speaker cannot become a durable mystery fact."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))

    with pytest.raises(MemoryValidationError):
        store.remember("I like this")
    assert store.list_entries() == []


def test_duplicate_source_and_text_are_idempotent(tmp_path: Path) -> None:
    """Repeated evidence does not create duplicate entries."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))

    first = store.remember("Likes tea", subject_id="person:a", source_ref="session:1")
    same_source = store.remember("Likes tea", subject_id="person:a", source_ref="session:1")
    same_text = store.remember(" likes   tea ", subject_id="person:a")

    assert same_source == first
    assert same_text == first
    assert len(store.list_entries()) == 1


def test_snapshot_revision_changes_only_for_actual_mutations(tmp_path: Path) -> None:
    """Revisions advance for writes and remain stable for duplicate captures."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)

    assert store.snapshot().revision == 0
    store.clear()
    assert store.snapshot().revision == 1
    store.clear()
    assert store.snapshot().revision == 2

    entry = store.remember("A stable note", subject_id="household", source_ref="capture:1")
    assert store.snapshot().revision == 3
    assert store.remember("A stable note", subject_id="household", source_ref="capture:1") == entry
    assert store.snapshot().revision == 3


def test_empty_clear_invalidates_a_pending_capture(tmp_path: Path) -> None:
    """Clearing an empty store still rejects an older capture revision."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    expected_revision = store.snapshot().revision

    store.clear()

    with pytest.raises(MemoryConflictError):
        store.apply_capture(
            [CaptureMutation(text="stale", subject_id="household")],
            expected_revision=expected_revision,
        )
    assert store.snapshot().revision == expected_revision + 1
    assert store.list_entries(include_inactive=True) == []


def test_capture_migration_starts_at_revision_one_and_old_v2_defaults_zero(tmp_path: Path) -> None:
    """Migration establishes a revision and old v2 files remain readable."""
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "facts": [{"id": "legacy", "text": "old", "createdAt": 1000}]}),
        encoding="utf-8",
    )
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)

    store.migrate_v1()
    assert store.snapshot().revision == 1

    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted.pop("revision")
    path.write_text(json.dumps(persisted), encoding="utf-8")
    assert store.snapshot().revision == 0


def test_stale_capture_cannot_resurrect_deleted_memory(tmp_path: Path) -> None:
    """A user deletion wins over an older background correction."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Delete this", subject_id="household")
    stale_revision = store.snapshot().revision

    store.forget(memory_id=entry.id)

    with pytest.raises(MemoryConflictError):
        store.apply_capture(
            [CaptureMutation(id=entry.id, text="Resurrected", subject_id="household")],
            expected_revision=stale_revision,
        )
    assert store.list_entries(include_inactive=True) == []


def test_stale_capture_cannot_undo_user_correction(tmp_path: Path) -> None:
    """A correction wins over an older capture result."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Original", subject_id="household")
    stale_revision = store.snapshot().revision

    store.update(entry.id, text="Corrected")

    with pytest.raises(MemoryConflictError):
        store.apply_capture(
            [CaptureMutation(id=entry.id, text="Stale correction")],
            expected_revision=stale_revision,
        )
    assert store.list_entries()[0].text == "Corrected"


def test_invalid_later_capture_does_not_write_earlier_capture(tmp_path: Path) -> None:
    """A failed batch leaves every operation unapplied."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    expected_revision = store.snapshot().revision

    with pytest.raises(MemoryValidationError):
        store.apply_capture(
            [
                CaptureMutation(text="first", subject_id="household"),
                CaptureMutation(text="invalid", subject_id=None),
            ],
            expected_revision=expected_revision,
        )
    assert not memory_path_for_instance(tmp_path).exists()
    assert store.snapshot().revision == expected_revision


def test_capture_batch_has_a_small_operation_bound(tmp_path: Path) -> None:
    """Oversized extraction output is rejected before any operation runs."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)

    with pytest.raises(MemoryValidationError):
        store.apply_capture(
            [CaptureMutation(text=f"entry {index}", subject_id="household") for index in range(7)],
            expected_revision=0,
        )
    assert store.snapshot().revision == 0
    assert not memory_path_for_instance(tmp_path).exists()


def test_source_ref_conflict_rejects_the_whole_capture_batch(tmp_path: Path) -> None:
    """A conflicting evidence reference cannot partially commit a batch."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    existing = store.remember("Existing", subject_id="household", source_ref="evidence:1")
    before = store.snapshot()

    with pytest.raises(MemoryValidationError):
        store.apply_capture(
            [
                CaptureMutation(text="first", subject_id="household", source_ref="evidence:2"),
                CaptureMutation(text="conflict", subject_id="household", source_ref="evidence:1"),
            ],
            expected_revision=before.revision,
        )
    after = store.snapshot()
    assert after.revision == before.revision
    assert after.entries == (existing,)


def test_two_different_memories_cannot_share_one_capture_reference(tmp_path: Path) -> None:
    """A batch reference cannot silently identify two different memories."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)

    with pytest.raises(MemoryValidationError):
        store.apply_capture(
            [
                CaptureMutation(text="first", subject_id="household", source_ref="batch:1"),
                CaptureMutation(text="second", subject_id="household", source_ref="batch:1"),
            ],
            expected_revision=0,
        )
    assert store.snapshot().revision == 0
    assert not memory_path_for_instance(tmp_path).exists()


def test_capture_retry_is_idempotent_and_does_not_extend_lifetime(tmp_path: Path) -> None:
    """Retrying a committed batch does not create or refresh its entry."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    batch = (CaptureMutation(text="Retry me", subject_id="household", source_ref="batch:1"),)

    first = store.apply_capture(batch, expected_revision=0)
    bytes_after_first = memory_path_for_instance(tmp_path).read_bytes()
    second = store.apply_capture(batch, expected_revision=first.revision)

    assert second.revision == first.revision
    assert second.entries == first.entries
    assert memory_path_for_instance(tmp_path).read_bytes() == bytes_after_first
    with pytest.raises(MemoryConflictError):
        store.apply_capture(batch, expected_revision=0)


def test_capture_update_retry_rejects_conflicting_source_evidence(tmp_path: Path) -> None:
    """Stable evidence references make correction retries idempotent and safe."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Original", subject_id="household", source_ref="batch:1")
    expected_revision = store.snapshot().revision

    clock.value += timedelta(days=1)
    unchanged = store.apply_capture(
        [
            CaptureMutation(
                id=entry.id,
                text="Original",
                subject_id="household",
                source_ref="batch:1",
            )
        ],
        expected_revision=expected_revision,
    )
    assert unchanged.revision == expected_revision
    assert unchanged.entries[0] == entry

    with pytest.raises(MemoryValidationError):
        store.apply_capture(
            [CaptureMutation(id=entry.id, text="Different", source_ref="batch:1")],
            expected_revision=expected_revision,
        )
    assert store.snapshot() == unchanged


@pytest.mark.parametrize("status", ["resolved", "superseded"])
def test_capture_status_update_preserves_original_times_and_restarts(tmp_path: Path, status: str) -> None:
    """Atomic status corrections preserve provenance and survive restart."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("A lasting fact", subject_id="household", kind="fact")
    expected_revision = store.snapshot().revision

    result = store.apply_capture(
        [CaptureMutation(id=entry.id, status=status)],
        expected_revision=expected_revision,
    )
    updated = result.entries[0]
    assert updated.status == status
    assert updated.created_at == entry.created_at
    assert updated.learned_at == entry.learned_at
    assert updated.active_until == clock.value
    assert updated.delete_after == clock.value + timedelta(days=30)

    restarted = _store(tmp_path, clock)
    assert restarted.list_entries() == []
    assert restarted.snapshot().entries == (updated,)


def test_update_corrects_entry_and_quarantines_resolved_history(tmp_path: Path) -> None:
    """Corrections apply immediately and resolved history remains inspectable."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Maybe Porto", subject_id="household", kind="plan", certainty="tentative")

    corrected = store.update(entry.id, text="Porto trip cancelled", status="resolved")

    assert corrected.status == "resolved"
    assert corrected.active_until == clock.value
    assert store.list_entries() == []
    restarted = _store(tmp_path, clock)
    assert restarted.list_entries(include_inactive=True)[0].text == "Porto trip cancelled"


def test_forget_refuses_ambiguous_query_and_supports_id(tmp_path: Path) -> None:
    """Ambiguous text deletion is refused while exact IDs remain usable."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))
    first = store.remember("Likes jazz", subject_id="person:a")
    second = store.remember("Likes jazz piano", subject_id="person:b")

    ambiguous = store.forget(query="jazz")
    assert ambiguous.removed is None
    assert ambiguous.ambiguous is True
    assert {entry.id for entry in ambiguous.candidates} == {first.id, second.id}
    assert len(store.list_entries()) == 2

    removed = store.forget(memory_id=first.id)
    assert removed.removed == first
    assert [entry.id for entry in store.list_entries()] == [second.id]


def test_expiry_filters_reads_but_delete_after_removes_retained_entry(tmp_path: Path) -> None:
    """Active filtering and retention expiry use the injected clock."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Temporary note", subject_id="household")

    clock.value += timedelta(days=3)
    assert store.list_entries() == []
    assert store.list_entries(include_inactive=True) == [entry]
    clock.value += timedelta(days=5)
    assert store.list_entries(include_inactive=True) == []


def test_forget_prunes_retention_expired_bytes(tmp_path: Path) -> None:
    """An explicit forget also removes entries past their retention deadline."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    store.remember("Temporary note", subject_id="household")
    path = memory_path_for_instance(tmp_path)

    clock.value += timedelta(days=8)
    result = store.forget(query="Temporary")

    assert result.removed is None
    assert "Temporary note" not in path.read_text(encoding="utf-8")


def test_capacity_never_evicts_existing_entries(tmp_path: Path) -> None:
    """Capacity errors preserve existing protected and bounded entries."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    facts = [store.remember(f"Fact {index}", subject_id="household", kind="fact") for index in range(MAX_FACTS)]

    with pytest.raises(MemoryCapacityError):
        store.remember("One more fact", subject_id="household", kind="fact")
    assert [entry.id for entry in store.list_entries()] == [entry.id for entry in reversed(facts)]

    bounded_store = _store(tmp_path / "bounded", clock)
    for index in range(MAX_NOTES):
        bounded_store.remember(f"Note {index}", subject_id="household")
    with pytest.raises(MemoryCapacityError):
        bounded_store.remember("One more note", subject_id="household")
    assert len(bounded_store.list_entries()) == MAX_NOTES


def test_invalid_mutation_preserves_original_file(tmp_path: Path) -> None:
    """A rejected correction leaves the original bytes untouched."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))
    entry = store.remember("Keep this", subject_id="household")
    path = memory_path_for_instance(tmp_path)
    before = path.read_bytes()

    with pytest.raises(MemoryValidationError):
        store.update(entry.id, text="x" * 281)

    assert path.read_bytes() == before


@pytest.mark.parametrize("created_at", [1001, 1_789_084_800_123])
def test_v1_migration_is_explicit_and_preserves_legacy_shape_and_unknown_subject(
    tmp_path: Path, created_at: int
) -> None:
    """Migration keeps legacy IDs, timestamps, and unknown subjects for review."""
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "facts": [{"id": "m_legacy", "text": "Likes tea", "createdAt": created_at}],
            }
        ),
        encoding="utf-8",
    )
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))

    with pytest.raises(MigrationRequiredError):
        store.list_entries()
    migrated = store.migrate_v1()

    assert migrated[0].id == "m_legacy"
    assert migrated[0].created_at == created_at
    assert migrated[0].subject_id is None
    assert migrated[0].delete_after is None
    assert migrated[0].to_json()["createdAt"] == created_at
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["entries"][0]["id"] == "m_legacy"

    migrated_store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))
    migrated_store.remember("New household note", subject_id="household")
    retained = migrated_store.list_entries(include_inactive=True)
    assert len(retained) == 2
    assert any(entry.id == "m_legacy" and entry.subject_id is None for entry in retained)


def test_conflicting_source_reference_is_rejected(tmp_path: Path) -> None:
    """One evidence reference cannot silently overwrite a different memory."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))
    store.remember("Likes tea", subject_id="person:a", source_ref="session:1")

    with pytest.raises(MemoryValidationError):
        store.remember("Likes coffee", subject_id="person:a", source_ref="session:1")
    assert len(store.list_entries()) == 1


@pytest.mark.parametrize("status", ["resolved", "superseded"])
def test_fact_resolution_round_trips_after_restart(tmp_path: Path, status: str) -> None:
    """Inactive fact history remains valid after a process restart."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path / status, clock)
    entry = store.remember("Birthday is in May", subject_id="household", kind="fact")

    if status == "resolved":
        updated = store.update(entry.id, status="resolved")
    else:
        updated = store.update(entry.id, status="superseded")
    restarted = _store(tmp_path / status, clock)

    assert updated.status == status
    assert restarted.list_entries() == []
    assert restarted.list_entries(include_inactive=True)[0].status == status


def test_resolving_far_future_plan_uses_short_history_retention(tmp_path: Path) -> None:
    """Resolving a far-future plan does not retain its old deadline for years."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Trip to Porto", subject_id="household", kind="plan", event_end="2028-09-20")

    updated = store.update(entry.id, status="resolved")

    assert updated.delete_after == clock.value + timedelta(days=30)


def test_clear_v1_writes_empty_v2_and_cannot_resurrect_legacy_facts(tmp_path: Path) -> None:
    """Explicit clear converts legacy storage to an empty v2 envelope."""
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "facts": [{"id": "m_legacy", "text": "old", "createdAt": 1000}]}),
        encoding="utf-8",
    )
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))

    store.clear()

    assert store.list_entries() == []
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_corrupt_or_unknown_version_is_read_only_failure(tmp_path: Path) -> None:
    """Corruption and unsupported versions never rewrite the file."""
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))

    with pytest.raises(MemoryCorruptionError):
        store.list_entries()
    assert path.read_text(encoding="utf-8") == "{not-json"

    path.write_text(json.dumps({"version": 99, "entries": []}), encoding="utf-8")
    with pytest.raises(MemoryCorruptionError):
        store.migrate_v1()

    path.write_bytes(b"\xff\xfe")
    with pytest.raises(MemoryCorruptionError):
        store.list_entries()
    assert path.read_bytes() == b"\xff\xfe"


def test_atomic_replace_failure_preserves_original(tmp_path: Path) -> None:
    """A failed atomic replacement does not overwrite the prior envelope."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))
    store.remember("Keep this", subject_id="household")
    path = memory_path_for_instance(tmp_path)
    before = path.read_bytes()

    with patch.object(Path, "replace", side_effect=OSError("replacement failed")):
        with pytest.raises(MemoryStoreError):
            store.remember("Second", subject_id="household")

    assert path.read_bytes() == before


def test_backward_clock_cannot_write_invalid_updated_at(tmp_path: Path) -> None:
    """A backwards wall clock keeps persisted update order valid."""
    clock = FakeClock(datetime(2026, 9, 11, tzinfo=UTC))
    store = _store(tmp_path, clock)
    entry = store.remember("Original", subject_id="household")
    clock.value -= timedelta(days=1)

    updated = store.update(entry.id, text="Corrected")
    restarted = _store(tmp_path, clock)

    assert updated.updated_at >= updated.learned_at
    assert restarted.list_entries()[0].text == "Corrected"


def test_file_is_private_after_write(tmp_path: Path) -> None:
    """Persisted memory is written with owner-only permissions."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))
    store.remember("Private local fact", subject_id="household", kind="fact")

    mode = os.stat(memory_path_for_instance(tmp_path)).st_mode & 0o777
    assert mode == 0o600


def test_date_inputs_require_timezone_for_datetimes(tmp_path: Path) -> None:
    """Date inputs reject naive datetimes and unbounded tentative facts."""
    store = _store(tmp_path, FakeClock(datetime(2026, 9, 11, tzinfo=UTC)))

    with pytest.raises(MemoryValidationError):
        store.remember("Meeting", kind="plan", event_end=datetime(2026, 9, 12))
    with pytest.raises(MemoryValidationError):
        store.remember("Tentative fact", kind="fact", certainty="tentative")
