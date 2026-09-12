"""Structured, local-only household memory storage."""

import os
import json
import uuid
import logging
import tempfile
import threading
from typing import Literal
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from dataclasses import dataclass
from collections.abc import Mapping, Callable, Sequence


logger = logging.getLogger(__name__)

MEMORY_FILENAME = "memory.v1.json"
SCHEMA_VERSION = 2
MAX_TEXT_CHARS = 280
MAX_FACTS = 60
MAX_NOTES = 200
MAX_FACT_CHARS = MAX_TEXT_CHARS
MAX_CAPTURE_MUTATIONS = 6

MemoryKind = Literal["note", "situation", "plan", "fact"]
MemoryCertainty = Literal["stated", "tentative"]
MemoryStatus = Literal["current", "resolved", "superseded"]
DateInput = str | date | datetime

_KINDS: frozenset[str] = frozenset({"note", "situation", "plan", "fact"})
_CERTAINTIES: frozenset[str] = frozenset({"stated", "tentative"})
_STATUSES: frozenset[str] = frozenset({"current", "resolved", "superseded"})
_UTC = timezone.utc
_STORE_LOCK = threading.RLock()


class MemoryStoreError(Exception):
    """Base class for local memory store failures."""


class MemoryCorruptionError(MemoryStoreError):
    """The memory file is unreadable or does not match a supported schema."""


class MemoryValidationError(MemoryStoreError, ValueError):
    """A requested memory mutation is invalid."""


class MemoryCapacityError(MemoryStoreError):
    """The bounded memory store has no safe capacity for a new entry."""


class MemoryConflictError(MemoryStoreError):
    """A capture batch was based on a stale store revision."""


class MigrationRequiredError(MemoryStoreError):
    """The file is legacy v1 and needs explicit migration before mutation."""


@dataclass(frozen=True)
class MemoryEntry:
    """One grounded household memory entry."""

    id: str
    text: str
    created_at: int
    subject_id: str | None
    source_person_id: str | None
    kind: MemoryKind
    certainty: MemoryCertainty
    source_ref: str | None
    learned_at: datetime
    updated_at: datetime
    event_start: str | None
    event_end: str | None
    active_until: datetime | None
    delete_after: datetime | None
    status: MemoryStatus

    def to_json(self) -> dict[str, object]:
        """Return the v2 JSON record, including legacy-compatible fields."""
        return {
            "id": self.id,
            "text": self.text,
            "createdAt": self.created_at,
            "subject_id": self.subject_id,
            "source_person_id": self.source_person_id,
            "kind": self.kind,
            "certainty": self.certainty,
            "source_ref": self.source_ref,
            "learned_at": _format_datetime(self.learned_at),
            "updated_at": _format_datetime(self.updated_at),
            "event_start": self.event_start,
            "event_end": self.event_end,
            "active_until": _format_optional_datetime(self.active_until),
            "delete_after": _format_optional_datetime(self.delete_after),
            "status": self.status,
        }


@dataclass(frozen=True)
class ForgetResult:
    """Result of an exact-id or unambiguous query deletion."""

    removed: MemoryEntry | None
    candidates: tuple[MemoryEntry, ...]

    @property
    def ambiguous(self) -> bool:
        """Whether a query matched more than one entry and was refused."""
        return self.removed is None and len(self.candidates) > 1


@dataclass(frozen=True)
class _Unset:
    pass


_UNSET = _Unset()


@dataclass(frozen=True)
class MemorySnapshot:
    """A revisioned, retained view of the local memory store."""

    revision: int
    entries: tuple[MemoryEntry, ...]


@dataclass(frozen=True)
class CaptureMutation:
    """One new capture or stable-ID correction for an atomic batch."""

    id: str | None = None
    text: str | None | _Unset = _UNSET
    subject_id: str | None | _Unset = _UNSET
    source_person_id: str | None | _Unset = _UNSET
    kind: MemoryKind | _Unset = _UNSET
    certainty: MemoryCertainty | _Unset = _UNSET
    event_start: DateInput | None | _Unset = _UNSET
    event_end: DateInput | None | _Unset = _UNSET
    source_ref: str | None | _Unset = _UNSET
    status: MemoryStatus | _Unset = _UNSET


@dataclass(frozen=True)
class _Envelope:
    version: int
    entries: tuple[MemoryEntry, ...]
    revision: int


def memory_path_for_instance(instance_path: str | Path | None = None) -> Path:
    """Return the existing local memory path used by the legacy store."""
    if instance_path is not None:
        return Path(instance_path).expanduser() / MEMORY_FILENAME

    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_mini_conversation_app" / MEMORY_FILENAME


def _default_clock() -> datetime:
    return datetime.now(_UTC)


def _format_datetime(value: datetime) -> str:
    normalized = value.astimezone(_UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _format_optional_datetime(value: datetime | None) -> str | None:
    return _format_datetime(value) if value is not None else None


def _normalise_text(text: str) -> str:
    if not isinstance(text, str):
        raise MemoryValidationError("text must be a string")
    normalized = " ".join(text.split()).strip()
    if not normalized:
        raise MemoryValidationError("text must not be empty")
    if len(normalized) > MAX_TEXT_CHARS:
        raise MemoryValidationError(f"text must be at most {MAX_TEXT_CHARS} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise MemoryValidationError("text contains a control character")
    return normalized


def _normalise_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise MemoryValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise MemoryValidationError(f"{field_name} must not be empty")
    if len(normalized) > 120:
        raise MemoryValidationError(f"{field_name} is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise MemoryValidationError(f"{field_name} contains a control character")
    return normalized


def _normalise_source_ref(value: str) -> str:
    if not isinstance(value, str):
        raise MemoryValidationError("source_ref must be a string")
    normalized = value.strip()
    if not normalized:
        raise MemoryValidationError("source_ref must not be empty")
    if len(normalized) > 240:
        raise MemoryValidationError("source_ref is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise MemoryValidationError("source_ref contains a control character")
    return normalized


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise MemoryCorruptionError(f"{field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryCorruptionError(f"{field_name} is not a valid ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemoryCorruptionError(f"{field_name} must include a timezone")
    return parsed.astimezone(_UTC)


def _normalise_clock_value(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MemoryValidationError("clock must return an aware datetime")
    return value.astimezone(_UTC)


def _parse_event_value(value: object, field_name: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise MemoryValidationError(f"{field_name} must be timezone-aware")
        return _format_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise MemoryValidationError(f"{field_name} must be an ISO date or aware datetime")
    stripped = value.strip()
    try:
        if len(stripped) == 10:
            parsed_date = date.fromisoformat(stripped)
            return parsed_date.isoformat()
        parsed_datetime = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryValidationError(f"{field_name} must be an ISO date or aware datetime") from exc
    if parsed_datetime.tzinfo is None or parsed_datetime.utcoffset() is None:
        raise MemoryValidationError(f"{field_name} must be timezone-aware")
    return _format_datetime(parsed_datetime)


def _event_as_datetime(value: str) -> datetime:
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=_UTC)
    return _parse_datetime(value, "event date")


def _validate_event_range(event_start: str | None, event_end: str | None) -> None:
    if (
        event_start is not None
        and event_end is not None
        and _event_as_datetime(event_end) < _event_as_datetime(event_start)
    ):
        raise MemoryValidationError("event_end must not be before event_start")


def _validate_enum(value: str, allowed: frozenset[str], field_name: str) -> None:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise MemoryValidationError(f"{field_name} must be one of {allowed_values}")


def _kind_from_json(value: object) -> MemoryKind:
    if value == "note" or value == "situation" or value == "plan" or value == "fact":
        return value
    raise MemoryCorruptionError("kind is invalid")


def _certainty_from_json(value: object) -> MemoryCertainty:
    if value == "stated" or value == "tentative":
        return value
    raise MemoryCorruptionError("certainty is invalid")


def _status_from_json(value: object) -> MemoryStatus:
    if value == "current" or value == "resolved" or value == "superseded":
        return value
    raise MemoryCorruptionError("status is invalid")


def _derive_lifecycle(
    kind: MemoryKind,
    certainty: MemoryCertainty,
    evidence_at: datetime,
    event_end: str | None,
    subject_id: str | None,
) -> tuple[datetime | None, datetime | None]:
    if kind == "fact":
        if certainty == "tentative":
            raise MemoryValidationError("tentative facts require a bounded kind")
        if subject_id is None:
            return evidence_at + timedelta(hours=48), evidence_at + timedelta(days=7)
        return None, None
    if kind == "note":
        return evidence_at + timedelta(hours=48), evidence_at + timedelta(days=7)
    if kind == "situation":
        active_until = evidence_at + timedelta(days=7)
        return active_until, active_until + timedelta(days=30)

    active_until = evidence_at + timedelta(days=7)
    if event_end is not None:
        active_until = _event_as_datetime(event_end) + timedelta(days=7)
    return active_until, active_until + timedelta(days=30)


def _created_at_for(value: datetime) -> int:
    normalized = value.astimezone(_UTC)
    epoch = datetime(1970, 1, 1, tzinfo=_UTC)
    elapsed = normalized - epoch
    if elapsed.days < 0:
        raise MemoryValidationError("clock must be on or after the Unix epoch")
    return elapsed.days * 86_400_000 + elapsed.seconds * 1_000 + elapsed.microseconds // 1_000


def _entry_from_v1(value: object) -> MemoryEntry:
    if not isinstance(value, Mapping):
        raise MemoryCorruptionError("v1 fact must be an object")
    raw_id = value.get("id")
    raw_text = value.get("text")
    raw_created_at = value.get("createdAt")
    if not isinstance(raw_id, str) or not isinstance(raw_text, str) or type(raw_created_at) is not int:
        raise MemoryCorruptionError("v1 fact has invalid fields")
    if raw_created_at < 0:
        raise MemoryCorruptionError("v1 fact has an invalid creation time")
    try:
        learned_at = datetime(1970, 1, 1, tzinfo=_UTC) + timedelta(milliseconds=raw_created_at)
    except (OverflowError, OSError, ValueError) as exc:
        raise MemoryCorruptionError("v1 fact has an invalid creation time") from exc
    try:
        normalized_id = _normalise_identifier(raw_id, "id")
        normalized_text = _normalise_text(raw_text)
    except MemoryValidationError as exc:
        raise MemoryCorruptionError("v1 fact has invalid text or id") from exc
    return MemoryEntry(
        id=normalized_id,
        text=normalized_text,
        created_at=raw_created_at,
        subject_id=None,
        source_person_id=None,
        kind="fact",
        certainty="stated",
        source_ref=None,
        learned_at=learned_at,
        updated_at=learned_at,
        event_start=None,
        event_end=None,
        active_until=None,
        delete_after=None,
        status="current",
    )


def _optional_identifier(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryCorruptionError(f"{field_name} is invalid")
    try:
        return _normalise_identifier(value, field_name)
    except MemoryValidationError as exc:
        raise MemoryCorruptionError(f"{field_name} is invalid") from exc


def _entry_from_v2(value: object) -> MemoryEntry:
    if not isinstance(value, Mapping):
        raise MemoryCorruptionError("v2 entry must be an object")
    raw_id = value.get("id")
    raw_text = value.get("text")
    raw_created_at = value.get("createdAt")
    if not isinstance(raw_id, str) or not isinstance(raw_text, str) or type(raw_created_at) is not int:
        raise MemoryCorruptionError("v2 entry has invalid legacy fields")
    if raw_created_at < 0:
        raise MemoryCorruptionError("v2 entry has an invalid creation time")
    try:
        normalized_id = _normalise_identifier(raw_id, "id")
        normalized_text = _normalise_text(raw_text)
    except MemoryValidationError as exc:
        raise MemoryCorruptionError("v2 entry has invalid text or id") from exc

    raw_kind = _kind_from_json(value.get("kind"))
    raw_certainty = _certainty_from_json(value.get("certainty"))
    raw_status = _status_from_json(value.get("status"))
    if raw_kind == "fact" and raw_certainty == "tentative":
        raise MemoryCorruptionError("v2 entry cannot be a tentative fact")

    learned_at = _parse_datetime(value.get("learned_at"), "learned_at")
    updated_at = _parse_datetime(value.get("updated_at"), "updated_at")
    if raw_created_at != _created_at_for(learned_at):
        raise MemoryCorruptionError("createdAt does not match learned_at")
    if updated_at < learned_at:
        raise MemoryCorruptionError("updated_at must not be before learned_at")
    raw_event_start = value.get("event_start")
    raw_event_end = value.get("event_end")
    try:
        event_start = None if raw_event_start is None else _parse_event_value(raw_event_start, "event_start")
        event_end = None if raw_event_end is None else _parse_event_value(raw_event_end, "event_end")
        _validate_event_range(event_start, event_end)
    except MemoryValidationError as exc:
        raise MemoryCorruptionError("event dates are invalid") from exc

    raw_active_until = value.get("active_until")
    raw_delete_after = value.get("delete_after")
    active_until = None if raw_active_until is None else _parse_datetime(raw_active_until, "active_until")
    delete_after = None if raw_delete_after is None else _parse_datetime(raw_delete_after, "delete_after")
    if active_until is not None and delete_after is not None and delete_after < active_until:
        raise MemoryCorruptionError("delete_after must not be before active_until")
    subject_id = _optional_identifier(value.get("subject_id"), "subject_id")
    if raw_kind == "fact" and raw_status == "current" and subject_id is not None:
        if active_until is not None or delete_after is not None:
            raise MemoryCorruptionError("lasting facts must not have expiry fields")
    elif raw_kind == "fact" and raw_status == "current" and (active_until is None) != (delete_after is None):
        raise MemoryCorruptionError("unknown facts must have both or neither expiry fields")
    elif raw_kind == "fact" and raw_status != "current":
        if active_until is None or delete_after is None:
            raise MemoryCorruptionError("inactive facts require expiry fields")
    elif raw_kind != "fact" and (active_until is None or delete_after is None):
        raise MemoryCorruptionError("bounded entries require expiry fields")

    raw_source_ref = value.get("source_ref")
    if raw_source_ref is not None and not isinstance(raw_source_ref, str):
        raise MemoryCorruptionError("source_ref is invalid")
    try:
        source_ref = None if raw_source_ref is None else _normalise_source_ref(raw_source_ref)
    except MemoryValidationError as exc:
        raise MemoryCorruptionError("source_ref is invalid") from exc

    return MemoryEntry(
        id=normalized_id,
        text=normalized_text,
        created_at=raw_created_at,
        subject_id=subject_id,
        source_person_id=_optional_identifier(value.get("source_person_id"), "source_person_id"),
        kind=raw_kind,
        certainty=raw_certainty,
        source_ref=source_ref,
        learned_at=learned_at,
        updated_at=updated_at,
        event_start=event_start,
        event_end=event_end,
        active_until=active_until,
        delete_after=delete_after,
        status=raw_status,
    )


def _validate_entries(entries: tuple[MemoryEntry, ...]) -> None:
    ids: set[str] = set()
    fact_count = 0
    note_count = 0
    for entry in entries:
        if entry.id in ids:
            raise MemoryCorruptionError("memory entries contain duplicate ids")
        ids.add(entry.id)
        if entry.kind == "fact":
            fact_count += 1
        else:
            note_count += 1
    if fact_count > MAX_FACTS or note_count > MAX_NOTES:
        raise MemoryCorruptionError("memory store exceeds its capacity")


def _read_envelope(path: Path) -> _Envelope:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _Envelope(version=SCHEMA_VERSION, entries=(), revision=0)
    except UnicodeError as exc:
        logger.warning("Household memory file is not valid UTF-8 at %s", path)
        raise MemoryCorruptionError("memory store is not valid UTF-8") from exc
    except OSError as exc:
        logger.warning("Failed to read household memory from %s: %s", path, exc)
        raise MemoryStoreError("memory store could not be read") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Household memory file is not valid JSON at %s", path)
        raise MemoryCorruptionError("memory store is not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise MemoryCorruptionError("memory store envelope must be an object")
    version = parsed.get("version")
    if type(version) is not int:
        raise MemoryCorruptionError("memory store version is invalid")
    if version == 1:
        raw_facts = parsed.get("facts")
        if not isinstance(raw_facts, list):
            raise MemoryCorruptionError("v1 memory store facts must be a list")
        entries = tuple(_entry_from_v1(item) for item in raw_facts)
        _validate_entries(entries)
        return _Envelope(version=1, entries=entries, revision=0)
    if version != SCHEMA_VERSION:
        raise MemoryCorruptionError("memory store version is unsupported")

    raw_entries = parsed.get("entries")
    if not isinstance(raw_entries, list):
        raise MemoryCorruptionError("v2 memory store envelope is invalid")
    raw_revision = parsed.get("revision", 0)
    if type(raw_revision) is not int or raw_revision < 0:
        raise MemoryCorruptionError("v2 memory store envelope is invalid")
    entries = tuple(_entry_from_v2(item) for item in raw_entries)
    _validate_entries(entries)
    return _Envelope(version=SCHEMA_VERSION, entries=entries, revision=raw_revision)


def _write_envelope(path: Path, entries: tuple[MemoryEntry, ...], revision: int) -> None:
    payload: dict[str, object] = {
        "version": SCHEMA_VERSION,
        "revision": revision,
        "entries": [entry.to_json() for entry in entries],
    }
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(path)
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        logger.warning("Failed to write household memory at %s: %s", path, exc)
        raise MemoryStoreError("memory store could not be written") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Failed to remove temporary household memory file at %s: %s", temporary_path, exc)


class HouseholdMemoryStore:
    """Manage bounded v2 household memory in the existing local JSON file."""

    def __init__(
        self, instance_path: str | Path | None = None, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        """Create a store rooted at an app instance directory."""
        self.path = memory_path_for_instance(instance_path)
        self._clock = clock or _default_clock

    def _now(self) -> datetime:
        return _normalise_clock_value(self._clock())

    @staticmethod
    def _require_v2(envelope: _Envelope) -> None:
        if envelope.version == 1:
            raise MigrationRequiredError("explicit v1 migration is required")

    @staticmethod
    def _retained_entries(entries: tuple[MemoryEntry, ...], now: datetime) -> tuple[MemoryEntry, ...]:
        return tuple(entry for entry in entries if entry.delete_after is None or entry.delete_after > now)

    @staticmethod
    def _active_entries(entries: tuple[MemoryEntry, ...], now: datetime) -> tuple[MemoryEntry, ...]:
        return tuple(
            entry
            for entry in entries
            if entry.status == "current"
            and (entry.active_until is None or entry.active_until > now)
            and (entry.delete_after is None or entry.delete_after > now)
        )

    def _apply_new_mutation(
        self,
        mutation: CaptureMutation,
        entries: tuple[MemoryEntry, ...],
        now: datetime,
    ) -> tuple[tuple[MemoryEntry, ...], MemoryEntry, bool]:
        if isinstance(mutation.text, _Unset):
            raise MemoryValidationError("new capture requires text")
        if mutation.text is None:
            raise MemoryValidationError("text must not be null")
        normalized_text = _normalise_text(mutation.text)
        if isinstance(mutation.subject_id, _Unset) or mutation.subject_id is None:
            raise MemoryValidationError("subject_id is required for durable memory")
        normalized_subject = _normalise_identifier(mutation.subject_id, "subject_id")
        if isinstance(mutation.source_person_id, _Unset) or mutation.source_person_id is None:
            normalized_source_person = None
        else:
            normalized_source_person = _normalise_identifier(mutation.source_person_id, "source_person_id")
        normalized_kind: MemoryKind = "note" if isinstance(mutation.kind, _Unset) else mutation.kind
        normalized_certainty: MemoryCertainty = (
            "stated" if isinstance(mutation.certainty, _Unset) else mutation.certainty
        )
        _validate_enum(normalized_kind, _KINDS, "kind")
        _validate_enum(normalized_certainty, _CERTAINTIES, "certainty")
        normalized_event_start = (
            None
            if isinstance(mutation.event_start, _Unset) or mutation.event_start is None
            else _parse_event_value(mutation.event_start, "event_start")
        )
        normalized_event_end = (
            None
            if isinstance(mutation.event_end, _Unset) or mutation.event_end is None
            else _parse_event_value(mutation.event_end, "event_end")
        )
        _validate_event_range(normalized_event_start, normalized_event_end)
        normalized_source_ref = (
            None
            if isinstance(mutation.source_ref, _Unset) or mutation.source_ref is None
            else _normalise_source_ref(mutation.source_ref)
        )
        normalized_status: MemoryStatus = "current" if isinstance(mutation.status, _Unset) else mutation.status
        _validate_enum(normalized_status, _STATUSES, "status")
        if normalized_status != "current":
            raise MemoryValidationError("new captures must have current status")
        active_until, delete_after = _derive_lifecycle(
            normalized_kind,
            normalized_certainty,
            now,
            normalized_event_end,
            normalized_subject,
        )

        for existing in entries:
            if normalized_source_ref is not None and existing.source_ref == normalized_source_ref:
                if (
                    existing.text.casefold() == normalized_text.casefold()
                    and existing.subject_id == normalized_subject
                    and existing.source_person_id == normalized_source_person
                    and existing.kind == normalized_kind
                    and existing.certainty == normalized_certainty
                    and existing.event_start == normalized_event_start
                    and existing.event_end == normalized_event_end
                ):
                    return entries, existing, False
                raise MemoryValidationError("source_ref conflicts with another memory")
            if (
                existing.status == "current"
                and existing.subject_id == normalized_subject
                and existing.kind == normalized_kind
                and existing.text.casefold() == normalized_text.casefold()
            ):
                return entries, existing, False

        facts = sum(entry.kind == "fact" for entry in entries)
        notes = len(entries) - facts
        if normalized_kind == "fact" and facts >= MAX_FACTS:
            raise MemoryCapacityError("fact capacity is full")
        if normalized_kind != "fact" and notes >= MAX_NOTES:
            raise MemoryCapacityError("bounded memory capacity is full")

        entry = MemoryEntry(
            id=self._new_id(now, entries),
            text=normalized_text,
            created_at=_created_at_for(now),
            subject_id=normalized_subject,
            source_person_id=normalized_source_person,
            kind=normalized_kind,
            certainty=normalized_certainty,
            source_ref=normalized_source_ref,
            learned_at=now,
            updated_at=now,
            event_start=normalized_event_start,
            event_end=normalized_event_end,
            active_until=active_until,
            delete_after=delete_after,
            status="current",
        )
        updated_entries = (entry, *entries)
        _validate_entries(updated_entries)
        return updated_entries, entry, True

    def _apply_update_mutation(
        self,
        mutation: CaptureMutation,
        entries: tuple[MemoryEntry, ...],
        now: datetime,
    ) -> tuple[tuple[MemoryEntry, ...], MemoryEntry, bool]:
        if mutation.id is None:
            raise MemoryValidationError("update requires a memory id")
        normalized_id = _normalise_identifier(mutation.id, "memory_id")
        if all(
            isinstance(value, _Unset)
            for value in (
                mutation.text,
                mutation.subject_id,
                mutation.source_person_id,
                mutation.kind,
                mutation.certainty,
                mutation.event_start,
                mutation.event_end,
                mutation.source_ref,
                mutation.status,
            )
        ):
            raise MemoryValidationError("update requires at least one editable field")
        index = next((index for index, entry in enumerate(entries) if entry.id == normalized_id), None)
        if index is None:
            raise MemoryValidationError("memory id was not found")
        current = entries[index]
        effective_now = max(now, current.updated_at)

        if isinstance(mutation.text, _Unset):
            normalized_text = current.text
        elif mutation.text is None:
            raise MemoryValidationError("text must not be null")
        else:
            normalized_text = _normalise_text(mutation.text)
        if isinstance(mutation.subject_id, _Unset):
            normalized_subject = current.subject_id
        elif mutation.subject_id is None:
            normalized_subject = None
        else:
            normalized_subject = _normalise_identifier(mutation.subject_id, "subject_id")
        if isinstance(mutation.source_person_id, _Unset):
            normalized_source_person = current.source_person_id
        elif mutation.source_person_id is None:
            normalized_source_person = None
        else:
            normalized_source_person = _normalise_identifier(mutation.source_person_id, "source_person_id")
        if isinstance(mutation.kind, _Unset):
            normalized_kind = current.kind
        else:
            normalized_kind = mutation.kind
        if isinstance(mutation.certainty, _Unset):
            normalized_certainty = current.certainty
        else:
            normalized_certainty = mutation.certainty
        _validate_enum(normalized_kind, _KINDS, "kind")
        _validate_enum(normalized_certainty, _CERTAINTIES, "certainty")
        if isinstance(mutation.event_start, _Unset):
            normalized_event_start = current.event_start
        elif mutation.event_start is None:
            normalized_event_start = None
        else:
            normalized_event_start = _parse_event_value(mutation.event_start, "event_start")
        if isinstance(mutation.event_end, _Unset):
            normalized_event_end = current.event_end
        elif mutation.event_end is None:
            normalized_event_end = None
        else:
            normalized_event_end = _parse_event_value(mutation.event_end, "event_end")
        _validate_event_range(normalized_event_start, normalized_event_end)
        if isinstance(mutation.source_ref, _Unset):
            normalized_source_ref = current.source_ref
        elif mutation.source_ref is None:
            normalized_source_ref = None
        else:
            normalized_source_ref = _normalise_source_ref(mutation.source_ref)
        if isinstance(mutation.status, _Unset):
            normalized_status = current.status
        else:
            normalized_status = mutation.status
        _validate_enum(normalized_status, _STATUSES, "status")
        if (
            not isinstance(mutation.source_ref, _Unset)
            and normalized_source_ref is not None
            and normalized_source_ref == current.source_ref
        ):
            same_evidence = (
                normalized_text == current.text
                and normalized_subject == current.subject_id
                and normalized_source_person == current.source_person_id
                and normalized_kind == current.kind
                and normalized_certainty == current.certainty
                and normalized_event_start == current.event_start
                and normalized_event_end == current.event_end
            )
            if not same_evidence:
                raise MemoryValidationError("source_ref conflicts with another memory")
            if normalized_status == current.status:
                return entries, current, False
        active_until, delete_after = _derive_lifecycle(
            normalized_kind,
            normalized_certainty,
            effective_now,
            normalized_event_end,
            normalized_subject,
        )
        if normalized_status != "current":
            active_until = effective_now
            retention = timedelta(days=7) if normalized_kind == "note" else timedelta(days=30)
            deadline = effective_now + retention
            if delete_after is None or delete_after <= effective_now:
                delete_after = deadline
            else:
                delete_after = min(delete_after, deadline)

        if normalized_kind != current.kind:
            facts = sum(entry.kind == "fact" for entry in entries)
            bounded = len(entries) - facts
            if normalized_kind == "fact" and facts >= MAX_FACTS:
                raise MemoryCapacityError("fact capacity is full")
            if normalized_kind != "fact" and bounded >= MAX_NOTES:
                raise MemoryCapacityError("bounded memory capacity is full")

        for other in entries:
            if other.id == current.id:
                continue
            if normalized_source_ref is not None and other.source_ref == normalized_source_ref:
                raise MemoryValidationError("source_ref is already used by another memory")
            if (
                normalized_status == "current"
                and other.status == "current"
                and other.subject_id == normalized_subject
                and other.kind == normalized_kind
                and other.text.casefold() == normalized_text.casefold()
            ):
                raise MemoryValidationError("update would duplicate another memory")

        updated = MemoryEntry(
            id=current.id,
            text=normalized_text,
            created_at=current.created_at,
            subject_id=normalized_subject,
            source_person_id=normalized_source_person,
            kind=normalized_kind,
            certainty=normalized_certainty,
            source_ref=normalized_source_ref,
            learned_at=current.learned_at,
            updated_at=effective_now,
            event_start=normalized_event_start,
            event_end=normalized_event_end,
            active_until=active_until,
            delete_after=delete_after,
            status=normalized_status,
        )
        if updated == current:
            return entries, current, False
        updated_entries = (*entries[:index], updated, *entries[index + 1 :])
        _validate_entries(updated_entries)
        return updated_entries, updated, True

    def _apply_capture_mutation(
        self,
        mutation: CaptureMutation,
        entries: tuple[MemoryEntry, ...],
        now: datetime,
    ) -> tuple[tuple[MemoryEntry, ...], MemoryEntry, bool]:
        if mutation.id is None:
            return self._apply_new_mutation(mutation, entries, now)
        return self._apply_update_mutation(mutation, entries, now)

    def list_entries(self, include_inactive: bool = False) -> list[MemoryEntry]:
        """Return retained entries, filtering inactive entries by default."""
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            self._require_v2(envelope)
            if not envelope.entries:
                return []
            now = self._now()
            entries = self._retained_entries(envelope.entries, now)
            if include_inactive:
                return list(entries)
            return list(self._active_entries(entries, now))

    def snapshot(self) -> MemorySnapshot:
        """Return the current revision and all retained entries."""
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            self._require_v2(envelope)
            now = self._now()
            return MemorySnapshot(envelope.revision, self._retained_entries(envelope.entries, now))

    def apply_capture(
        self,
        mutations: Sequence[CaptureMutation],
        expected_revision: int,
    ) -> MemorySnapshot:
        """Apply a bounded capture batch only when its revision is current."""
        if type(expected_revision) is not int or expected_revision < 0:
            raise MemoryValidationError("expected_revision must be a non-negative integer")
        if not isinstance(mutations, Sequence):
            raise MemoryValidationError("mutations must be a sequence")
        operations = tuple(mutations)
        if len(operations) > MAX_CAPTURE_MUTATIONS:
            raise MemoryValidationError(f"a capture batch may contain at most {MAX_CAPTURE_MUTATIONS} mutations")
        if any(not isinstance(mutation, CaptureMutation) for mutation in operations):
            raise MemoryValidationError("capture mutations have an invalid type")

        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            self._require_v2(envelope)
            if expected_revision != envelope.revision:
                raise MemoryConflictError("memory store revision is stale")
            now = self._now()
            working = self._retained_entries(envelope.entries, now)
            changed = False
            for mutation in operations:
                working, _entry, mutation_changed = self._apply_capture_mutation(mutation, working, now)
                changed = changed or mutation_changed
            revision = envelope.revision + 1 if changed else envelope.revision
            if changed:
                _write_envelope(self.path, working, revision)
            return MemorySnapshot(revision, self._retained_entries(working, now))

    def remember(
        self,
        text: str,
        subject_id: str | None = None,
        source_person_id: str | None = None,
        kind: MemoryKind = "note",
        certainty: MemoryCertainty = "stated",
        event_start: DateInput | None = None,
        event_end: DateInput | None = None,
        source_ref: str | None = None,
    ) -> MemoryEntry:
        """Add one grounded entry, returning an existing duplicate when possible."""
        mutation = CaptureMutation(
            text=text,
            subject_id=subject_id,
            source_person_id=source_person_id,
            kind=kind,
            certainty=certainty,
            event_start=event_start,
            event_end=event_end,
            source_ref=source_ref,
        )
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            self._require_v2(envelope)
            now = self._now()
            entries = self._retained_entries(envelope.entries, now)
            updated_entries, entry, changed = self._apply_capture_mutation(mutation, entries, now)
            if changed:
                _write_envelope(self.path, updated_entries, envelope.revision + 1)
            return entry

    @staticmethod
    def _new_id(now: datetime, entries: tuple[MemoryEntry, ...]) -> str:
        existing_ids = {entry.id for entry in entries}
        for _ in range(10):
            candidate = f"m_{_created_at_for(now)}_{uuid.uuid4().hex[:10]}"
            if candidate not in existing_ids:
                return candidate
        raise MemoryStoreError("could not allocate a memory id")

    def update(
        self,
        memory_id: str,
        *,
        text: str | None | _Unset = _UNSET,
        subject_id: str | None | _Unset = _UNSET,
        source_person_id: str | None | _Unset = _UNSET,
        kind: MemoryKind | _Unset = _UNSET,
        certainty: MemoryCertainty | _Unset = _UNSET,
        event_start: DateInput | None | _Unset = _UNSET,
        event_end: DateInput | None | _Unset = _UNSET,
        source_ref: str | None | _Unset = _UNSET,
        status: MemoryStatus | _Unset = _UNSET,
    ) -> MemoryEntry:
        """Correct one entry by stable ID and refresh its retention from new evidence."""
        mutation = CaptureMutation(
            id=memory_id,
            text=text,
            subject_id=subject_id,
            source_person_id=source_person_id,
            kind=kind,
            certainty=certainty,
            event_start=event_start,
            event_end=event_end,
            source_ref=source_ref,
            status=status,
        )
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            self._require_v2(envelope)
            now = self._now()
            entries = self._retained_entries(envelope.entries, now)
            updated_entries, updated, changed = self._apply_capture_mutation(mutation, entries, now)
            if changed:
                _write_envelope(self.path, updated_entries, envelope.revision + 1)
            return updated

    def forget(self, query: str | None = None, memory_id: str | None = None) -> ForgetResult:
        """Remove one exact ID or one unambiguous case-insensitive text match."""
        if query is None and memory_id is None:
            raise MemoryValidationError("query or memory_id is required")
        if query is not None and memory_id is not None:
            raise MemoryValidationError("query and memory_id cannot both be supplied")
        normalized_query = None if query is None else _normalise_text(query)
        normalized_id = None if memory_id is None else _normalise_identifier(memory_id, "memory_id")
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            self._require_v2(envelope)
            now = self._now()
            entries = self._retained_entries(envelope.entries, now)
            if normalized_id is not None:
                candidates = tuple(entry for entry in entries if entry.id == normalized_id)
            else:
                assert normalized_query is not None
                folded_query = normalized_query.casefold()
                candidates = tuple(entry for entry in entries if folded_query in entry.text.casefold())
            if not candidates:
                if entries != envelope.entries:
                    _write_envelope(self.path, entries, envelope.revision + 1)
                return ForgetResult(removed=None, candidates=())
            if len(candidates) > 1:
                return ForgetResult(removed=None, candidates=candidates)
            removed = candidates[0]
            remaining = tuple(entry for entry in entries if entry.id != removed.id)
            _write_envelope(self.path, remaining, envelope.revision + 1)
            return ForgetResult(removed=removed, candidates=candidates)

    def clear(self) -> None:
        """Delete all entries and establish an empty v2 envelope."""
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            _write_envelope(self.path, (), envelope.revision + 1)

    def migrate_v1(self) -> list[MemoryEntry]:
        """Explicitly convert a v1 envelope in place, preserving IDs and unknown subjects."""
        with _STORE_LOCK:
            envelope = _read_envelope(self.path)
            if envelope.version == SCHEMA_VERSION:
                return list(envelope.entries)
            _validate_entries(envelope.entries)
            _write_envelope(self.path, envelope.entries, envelope.revision + 1)
            return list(envelope.entries)
