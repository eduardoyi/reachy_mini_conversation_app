"""Provider-neutral household-memory lifecycle for one active conversation."""

import json
import asyncio
import logging
import threading
from typing import Literal, Protocol
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from collections.abc import Mapping, Callable, Coroutine

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.household_memory import (
    MemoryEntry,
    MemorySnapshot,
    ReviewApplyResult,
    HouseholdMemoryStore,
    memory_path_for_instance,
)
from reachy_mini_conversation_app.household_memory_review import HouseholdMemoryReview
from reachy_mini_conversation_app.household_memory_capture import MemoryCapture
from reachy_mini_conversation_app.household_memory_receipts import (
    MemoryMutationReceipt,
    memory_mutation_bus,
    new_mutation_generation,
)


logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_S = 0.25
_DEFAULT_CLOSE_TIMEOUT_S = 2.0
_MAX_RECEIPTS = 128
_MAX_REVIEW_ACKS = 16
_UNTRUSTED_GENERATION = "untrusted"
Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True, repr=False)
class MemorySessionSnapshot:
    """Active known-owner memory values held by one conversation."""

    revision: int
    entries: dict[str, str]

    def __repr__(self) -> str:
        """Show revision and IDs without private entry values."""
        return f"MemorySessionSnapshot(revision={self.revision}, entry_ids={tuple(self.entries)!r})"


class MemorySessionCloseError(RuntimeError):
    """The memory session still owns one or more unclosed auxiliary tasks."""

    def __init__(self, components: tuple[str, ...]) -> None:
        """Record cleanup components that remain active."""
        self.components = components
        super().__init__("household memory cleanup is still active: " + ", ".join(components))


class MemoryStore(Protocol):
    """Store methods needed by the lifecycle."""

    path: Path

    def active_snapshot(self) -> MemorySnapshot:
        """Return the current revision and active entries."""


class MemoryCaptureWorker(Protocol):
    """Capture operations shared by provider adapters."""

    async def start(self) -> None:
        """Start capture."""

    def bind_mutation_generation(self, generation: str) -> None:
        """Attach the active session generation to future local writes."""

    def add_fragment(self, role: Role, text: str, start_ms: int, end_ms: int, speaker_id: str | None = None) -> None:
        """Queue one finalized transcript fragment, with the recognized speaker of a user fragment."""

    def invalidate(self) -> None:
        """Discard buffered work and fence late provider results."""

    def rebase_after_review(self, previous_revision: int, current_revision: int) -> None:
        """Rebase buffered work across one acknowledged review write."""

    async def close(self) -> None:
        """Stop capture and close its auxiliary client."""


class MemoryReviewWorker(Protocol):
    """Review operations shared by provider adapters."""

    def activate(self) -> None:
        """Open the review opportunity for this activation."""

    def bind_mutation_generation(self, generation: str) -> None:
        """Attach the active session generation to future local writes."""

    async def run_once(self) -> object:
        """Run the bounded review opportunity."""

    def invalidate(self) -> None:
        """Fence a request that must not apply."""

    async def close(self) -> None:
        """Stop review and close its auxiliary client."""


CaptureFactory = Callable[[str | None], MemoryCaptureWorker]
ReviewApplied = Callable[[MemorySnapshot, ReviewApplyResult], None]
ReviewFactory = Callable[[str | None, ReviewApplied], MemoryReviewWorker]
StoreFactory = Callable[[str | None], MemoryStore]
InvalidatedCallback = Callable[[str], Coroutine[object, object, None]]
Flag = bool | Callable[[], bool]


def default_memory_capture_factory(instance_path: str | None) -> MemoryCaptureWorker:
    """Build the configured text extraction worker on first activation."""
    return MemoryCapture(
        HouseholdMemoryStore(instance_path),
        model=config.OPENAI_LIVE_BACKEND_MODEL,
        api_key=config.OPENAI_API_KEY,
    )


def default_memory_review_factory(instance_path: str | None, on_applied: ReviewApplied) -> MemoryReviewWorker:
    """Build the configured text review worker on first activation."""
    return HouseholdMemoryReview(
        HouseholdMemoryStore(instance_path),
        model=config.OPENAI_LIVE_BACKEND_MODEL,
        api_key=config.OPENAI_API_KEY,
        on_applied=on_applied,
    )


def _flag_value(flag: Flag) -> bool:
    return flag() if callable(flag) else flag


def _entry_payload(entry: MemoryEntry) -> str:
    return json.dumps(entry.to_json(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _context_entries(snapshot: MemorySnapshot) -> dict[str, str]:
    return {entry.id: _entry_payload(entry) for entry in snapshot.entries if entry.subject_id is not None}


class MemorySession:
    """Own memory capture, review, fencing, receipts and cleanup for one provider session."""

    def __init__(
        self,
        instance_path: str | None = None,
        *,
        enabled: Flag = True,
        capture_enabled: Flag = False,
        review_enabled: Flag = False,
        store_factory: StoreFactory | None = None,
        capture_factory: CaptureFactory | None = None,
        review_factory: ReviewFactory | None = None,
        on_invalidated: InvalidatedCallback | None = None,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        close_timeout_s: float = _DEFAULT_CLOSE_TIMEOUT_S,
        max_receipts: int = _MAX_RECEIPTS,
    ) -> None:
        """Create a provider-independent memory lifecycle."""
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if close_timeout_s <= 0:
            raise ValueError("close_timeout_s must be positive")
        if max_receipts <= 0:
            raise ValueError("max_receipts must be positive")
        self.instance_path = instance_path
        self._enabled = enabled
        self._capture_enabled = capture_enabled
        self._review_enabled = review_enabled
        self._store_factory = store_factory or (lambda path: HouseholdMemoryStore(path))
        self._capture_factory = capture_factory
        self._review_factory = review_factory
        self._on_invalidated = on_invalidated
        self._poll_interval_s = poll_interval_s
        self._close_timeout_s = close_timeout_s
        self._max_receipts = max_receipts
        self._store: MemoryStore | None = None
        self._path = memory_path_for_instance(instance_path)
        self._unsubscribe: Callable[[], None] | None = None
        self._receipt_lock = threading.RLock()
        self._receipts: deque[MemoryMutationReceipt] = deque(maxlen=max_receipts)
        self._receipt_overflow = False
        self._review_acks: deque[tuple[int, int]] = deque(maxlen=_MAX_REVIEW_ACKS)
        self._generation = ""
        self._active = False
        self._fenced = False
        self._closed = True
        self._capture_paused = False
        self._stale_reason: str | None = None
        self._invalidation_task: asyncio.Task[None] | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        self._review_task: asyncio.Task[None] | None = None
        self._capture: MemoryCaptureWorker | None = None
        self._review: MemoryReviewWorker | None = None
        self._capture_started = False
        self._worker_close_tasks: dict[str, asyncio.Task[None]] = {}
        self._close_task: asyncio.Task[None] | None = None
        self._prepared = False
        self._seeded_entries: dict[str, str] = {}
        self._observed_revision: int | None = None
        self._last_validated_snapshot: MemorySessionSnapshot | None = None
        self._invalidation_callback_sent = False

    @property
    def generation(self) -> str:
        """Return the opaque token for this provider activation."""
        return self._generation

    @property
    def active(self) -> bool:
        """Return whether this lifecycle accepts active-session work."""
        return self._active and not self._fenced

    @property
    def fenced(self) -> bool:
        """Return whether this lifecycle has rejected its memory context."""
        return self._fenced

    @property
    def capture(self) -> MemoryCaptureWorker | None:
        """Return the active capture worker for count-only diagnostics."""
        return self._capture

    @property
    def review(self) -> MemoryReviewWorker | None:
        """Return the active review worker."""
        return self._review

    @property
    def snapshot(self) -> MemorySessionSnapshot | None:
        """Return the current private context without exposing it in repr/logs."""
        if self._observed_revision is None:
            return None
        return MemorySessionSnapshot(self._observed_revision, dict(self._seeded_entries))

    @property
    def stale_reason(self) -> str | None:
        """Return the count-only reason for a fence."""
        return self._stale_reason

    def prepare(self) -> None:
        """Seed active context before provider prompts or transport writes."""
        if self._prepared:
            return
        if self._close_task is not None and not self._close_task.done():
            raise RuntimeError("household memory cleanup is still active")
        if self._worker_close_tasks:
            raise MemorySessionCloseError(tuple(sorted(self._worker_close_tasks)))
        if self._invalidation_task is not None:
            if not self._invalidation_task.done():
                raise MemorySessionCloseError(("invalidation",))
            self._invalidation_task = None
        self._generation = new_mutation_generation()
        self._active = True
        self._fenced = False
        self._closed = False
        self._capture_paused = False
        self._stale_reason = None
        self._invalidation_callback_sent = False
        self._review_acks.clear()
        with self._receipt_lock:
            self._receipts.clear()
            self._receipt_overflow = False
        if not _flag_value(self._enabled):
            self._prepared = True
            return
        self._subscribe()
        current = self._read_context_snapshot()
        if current is not None:
            self._seed(current)
            self._last_validated_snapshot = current
            self._discard_receipts_through(current.revision)
        else:
            self._seeded_entries = {}
            self._observed_revision = None
            self._last_validated_snapshot = None

        self._prepared = True

    async def start(self) -> None:
        """Start enabled workers after the provider is ready."""
        if not self._prepared:
            self.prepare()
        if self._fenced or not self._active:
            raise RuntimeError("household memory session is fenced")
        if self._watcher_task is not None:
            return
        if not _flag_value(self._enabled):
            return
        if not await self.check_current():
            raise RuntimeError("household memory context changed before activation")

        if _flag_value(self._capture_enabled) and self._capture_factory is not None:
            try:
                capture = self._capture_factory(self.instance_path)
                self._capture = capture
                self._bind_generation(capture)
                await capture.start()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Household memory capture could not start error=%s", type(exc).__name__)
            else:
                self._capture_started = True

        if not self.active:
            started_capture = self._capture
            if started_capture is not None:
                self._start_worker_close("capture", started_capture)
                close_task = self._worker_close_tasks["capture"]
                await self._await_task("capture", close_task)
                if self._worker_close_tasks.get("capture") is close_task:
                    self._worker_close_tasks.pop("capture")
                self._capture = None
                self._capture_started = False
            raise RuntimeError("household memory context changed during activation")

        if _flag_value(self._review_enabled) and self._review_factory is not None:
            try:
                review = self._review_factory(self.instance_path, self._on_review_applied)
                self._review = review
                self._bind_generation(review)
                review.activate()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Household memory review could not start error=%s", type(exc).__name__)
            else:
                if self.active:
                    self._review_task = asyncio.create_task(self._run_review(review), name="household-memory-review")

        if not self.active:
            review = self._review
            if review is not None:
                self._start_worker_close("review-client", review)
                close_task = self._worker_close_tasks["review-client"]
                await self._await_task("review-client", close_task)
                if self._worker_close_tasks.get("review-client") is close_task:
                    self._worker_close_tasks.pop("review-client")
                self._review = None
            raise RuntimeError("household memory context changed during activation")

        self._watcher_task = asyncio.create_task(self._watch_memory_changes(), name="household-memory-watch")

    def _subscribe(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = memory_mutation_bus().subscribe(self._path, self._enqueue_receipt)

    def _enqueue_receipt(self, receipt: MemoryMutationReceipt) -> None:
        with self._receipt_lock:
            if len(self._receipts) >= self._max_receipts:
                self._receipt_overflow = True
            self._receipts.append(receipt)

    def _discard_receipts_through(self, revision: int) -> None:
        with self._receipt_lock:
            while self._receipts and self._receipts[0].after_revision <= revision:
                self._receipts.popleft()

    def _drain_receipts_through(self, revision: int, observed: int | None) -> tuple[MemoryMutationReceipt, ...]:
        with self._receipt_lock:
            if self._receipt_overflow:
                self._receipt_overflow = False
                self._receipts.clear()
                overflowed = True
                receipts: tuple[MemoryMutationReceipt, ...] = ()
            else:
                overflowed = False
                selected: list[MemoryMutationReceipt] = []
                remaining: deque[MemoryMutationReceipt] = deque(maxlen=self._max_receipts)
                for receipt in self._receipts:
                    valid_revision = (
                        type(receipt.before_revision) is int
                        and type(receipt.after_revision) is int
                        and receipt.before_revision >= 0
                        and receipt.after_revision == receipt.before_revision + 1
                    )
                    if (
                        not valid_revision
                        or observed is None
                        or receipt.after_revision <= revision
                        or receipt.before_revision < observed
                    ):
                        selected.append(receipt)
                    else:
                        remaining.append(receipt)
                self._receipts.clear()
                self._receipts.extend(remaining)
                receipts = tuple(selected)
        if overflowed:
            self._fence("memory_receipt_overflow")
            return ()
        return receipts

    def _seed(self, snapshot: MemorySessionSnapshot) -> None:
        self._observed_revision = snapshot.revision
        self._seeded_entries = dict(snapshot.entries)

    def _read_context_snapshot(self) -> MemorySessionSnapshot | None:
        store = self._store
        if store is None:
            store = self._store_factory(self.instance_path)
            self._store = store
        try:
            snapshot = store.active_snapshot()
            return MemorySessionSnapshot(snapshot.revision, _context_entries(snapshot))
        except Exception as exc:
            logger.warning("Household memory context read failed error=%s", type(exc).__name__)
            return None

    async def _watch_memory_changes(self) -> None:
        current_task = asyncio.current_task()
        try:
            while self.active:
                await asyncio.sleep(self._poll_interval_s)
                if self.active:
                    await self.check_current()
        except asyncio.CancelledError:
            raise
        finally:
            if self._watcher_task is current_task:
                self._watcher_task = None

    def _fence(self, reason: str) -> None:
        if self._fenced:
            return
        self._fenced = True
        self._active = False
        self._stale_reason = reason
        self._capture_paused = True
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()
        capture = self._capture
        if capture is not None:
            try:
                capture.invalidate()
            except Exception as exc:
                logger.warning("Household memory capture invalidation failed error=%s", type(exc).__name__)
        review = self._review
        if review is not None:
            try:
                review.invalidate()
            except Exception as exc:
                logger.warning("Household memory review invalidation failed error=%s", type(exc).__name__)
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        for task in (self._watcher_task, self._review_task):
            if task is not None and task is not current_task and not task.done():
                task.cancel()

    def invalidate(self) -> None:
        """Fence this activation and synchronously cancel memory work."""
        self._fence("memory_invalidated")

    async def _notify_invalidation(self, reason: str) -> None:
        callback = self._on_invalidated
        if callback is None or self._invalidation_callback_sent:
            return
        self._invalidation_callback_sent = True
        callback_task = self._invalidation_task
        if callback_task is None or callback_task.done():
            callback_task = asyncio.create_task(callback(reason), name="household-memory-invalidation")
            self._invalidation_task = callback_task
            callback_task.add_done_callback(self._consume_invalidation_task)
        if callback_task is asyncio.current_task():
            return
        try:
            await asyncio.shield(callback_task)
        except Exception:
            self._invalidation_callback_sent = False
            raise

    @staticmethod
    def _consume_invalidation_task(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            logger.warning("Household memory invalidation callback failed error=%s", type(exc).__name__)

    def _compare_context(self, current: MemorySessionSnapshot) -> bool:
        observed = self._observed_revision
        if observed is None:
            self._seed(current)
            return True
        if current.revision < observed:
            self._fence("memory_changed")
            return False
        if any(current.entries.get(memory_id) != value for memory_id, value in self._seeded_entries.items()):
            self._fence("memory_changed")
            return False
        self._observed_revision = current.revision
        return True

    @staticmethod
    def _receipt_after_membership(after: str | None) -> tuple[bool, bool]:
        if after is None:
            return True, False
        try:
            parsed = json.loads(after)
        except (TypeError, ValueError):
            return False, False
        if not isinstance(parsed, Mapping):
            return False, False
        status = parsed.get("status")
        subject_id = parsed.get("subject_id")
        if not isinstance(status, str):
            return False, False
        if status != "current":
            return True, False
        if subject_id is None:
            return True, False
        if not isinstance(subject_id, str):
            return False, False
        return True, True

    def _consume_receipts(self) -> bool:
        if self._fenced:
            return False
        current = self._read_context_snapshot()
        if current is None:
            self._fence("memory_changed")
            return False
        receipts = self._drain_receipts_through(current.revision, self._observed_revision)
        if self._fenced:
            return False
        observed = self._observed_revision
        for receipt in receipts:
            if (
                observed is None
                or type(receipt.before_revision) is not int
                or type(receipt.after_revision) is not int
                or receipt.before_revision < 0
                or receipt.after_revision != receipt.before_revision + 1
                or receipt.before_revision != observed
            ):
                self._fence("memory_receipt_gap")
                return False
            touched_seeded = {
                change.memory_id for change in receipt.changes if change.memory_id in self._seeded_entries
            }
            if receipt.generation == _UNTRUSTED_GENERATION:
                if touched_seeded:
                    self._fence("memory_changed")
                    return False
                observed = receipt.after_revision
                continue
            if receipt.generation != self._generation:
                self._fence("memory_generation_changed")
                return False
            if any(
                change.before != self._seeded_entries.get(change.memory_id)
                for change in receipt.changes
                if change.memory_id in self._seeded_entries
            ):
                self._fence("memory_receipt_mismatch")
                return False
            if receipt.source == "review":
                self._review_acks.append((receipt.before_revision, receipt.after_revision))
            if touched_seeded:
                for memory_id in touched_seeded:
                    change = next(item for item in receipt.changes if item.memory_id == memory_id)
                    valid, seedable = self._receipt_after_membership(change.after)
                    if not valid:
                        self._fence("memory_receipt_mismatch")
                        return False
                    if seedable and change.after is not None:
                        self._seeded_entries[memory_id] = change.after
                    else:
                        self._seeded_entries.pop(memory_id, None)
            observed = receipt.after_revision
        self._observed_revision = observed
        self._last_validated_snapshot = current
        return self._compare_context(current)

    async def check_current(self) -> bool:
        """Validate seeded active context and notify the provider before unsafe use."""
        if not _flag_value(self._enabled) or self._fenced:
            return not self._fenced
        if not self._consume_receipts():
            await self._notify_invalidation(self._stale_reason or "memory_changed")
            return False
        return True

    def context_is_current(self) -> bool:
        """Return whether a synchronous backend boundary may use this context."""
        if not _flag_value(self._enabled) or self._fenced:
            return not self._fenced
        return self._consume_receipts()

    def add_fragment(self, role: Role, text: str, start_ms: int, end_ms: int, speaker_id: str | None = None) -> None:
        """Forward finalized transcript text (and a user fragment's recognized speaker) to active capture."""
        capture = self._capture
        if not self.active or self._capture_paused or not self._capture_started or capture is None:
            return
        try:
            if speaker_id is None:
                capture.add_fragment(role, text, start_ms, end_ms)
            else:
                capture.add_fragment(role, text, start_ms, end_ms, speaker_id=speaker_id)
        except (TypeError, ValueError) as exc:
            logger.debug("Household memory fragment rejected error=%s", type(exc).__name__)

    def notify_spoken_forget(self, memory_id: str) -> bool:
        """Acknowledge one spoken forget while keeping the provider session live."""
        if not self.active or not _flag_value(self._enabled):
            return False
        if not self._consume_receipts():
            return False
        current = self._last_validated_snapshot
        if current is None:
            self._fence("memory_changed")
            return False
        if any(
            current.entries.get(existing_id) != value
            for existing_id, value in self._seeded_entries.items()
            if existing_id != memory_id
        ):
            self._fence("memory_changed")
            return False
        self._seeded_entries.pop(memory_id, None)
        self._observed_revision = current.revision
        self._capture_paused = True
        capture = self._capture
        if capture is not None:
            capture.invalidate()
        review = self._review
        if review is not None:
            review.invalidate()
        task = self._review_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        return True

    def rebase_after_review(self, before: MemorySnapshot, result: ReviewApplyResult) -> bool:
        """Rebase only after the exact acknowledged review transition."""
        if not self.active:
            return False
        if before.revision == result.snapshot.revision and not result.removed_ids:
            return True
        if not self._consume_receipts():
            return False
        transition = (before.revision, result.snapshot.revision)
        if transition not in self._review_acks:
            self._fence("memory_review_receipt_missing")
            return False
        self._review_acks.remove(transition)
        current = self._last_validated_snapshot
        if current is None:
            self._fence("memory_changed")
            return False
        if current.revision < result.snapshot.revision:
            self._fence("memory_review_revision_mismatch")
            return False
        self._observed_revision = current.revision
        capture = self._capture
        if capture is not None and self._capture_started and not self._capture_paused:
            try:
                capture.rebase_after_review(before.revision, result.snapshot.revision)
            except Exception as exc:
                logger.warning("Household memory capture review rebase failed error=%s", type(exc).__name__)
        return self._compare_context(current)

    def _on_review_applied(self, before: MemorySnapshot, result: ReviewApplyResult) -> None:
        if not self.rebase_after_review(before, result):
            logger.info("Household memory review rebase fenced the active context")

    async def _run_review(self, worker: MemoryReviewWorker) -> None:
        current_task = asyncio.current_task()
        try:
            await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Household memory review failed error=%s", type(exc).__name__)
        finally:
            if self._review_task is current_task:
                self._review_task = None

    def _bind_generation(self, worker: MemoryCaptureWorker | MemoryReviewWorker) -> None:
        worker.bind_mutation_generation(self._generation)

    def _start_worker_close(self, name: str, worker: MemoryCaptureWorker | MemoryReviewWorker) -> None:
        task = self._worker_close_tasks.get(name)
        if task is not None:
            if not task.done():
                return
            if not task.cancelled():
                try:
                    task.result()
                except Exception as exc:
                    self._worker_close_tasks.pop(name, None)
                    logger.warning("Household memory %s close retrying after error=%s", name, type(exc).__name__)
                else:
                    return
            else:
                self._worker_close_tasks.pop(name, None)
        task = asyncio.create_task(worker.close(), name=f"household-memory-{name}-close")
        self._worker_close_tasks[name] = task

    async def _await_task(self, name: str, task: asyncio.Task[None]) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._close_timeout_s)
        except asyncio.TimeoutError as exc:
            raise MemorySessionCloseError((name,)) from exc
        except asyncio.CancelledError:
            if task.cancelled():
                return
            raise
        except Exception as exc:
            raise MemorySessionCloseError((name,)) from exc

    async def _close_impl(self) -> None:
        # Provider-owned invalidation teardown may call close recursively.
        self._fence("memory_invalidated")
        current_task = asyncio.current_task()
        errors: list[str] = []
        watcher = self._watcher_task
        if watcher is not None and watcher is not current_task and not watcher.done():
            watcher.cancel()
        review_task = self._review_task
        if review_task is not None and review_task is not current_task and not review_task.done():
            review_task.cancel()
        for name, task in (("watcher", watcher), ("review", review_task)):
            if task is None or task is current_task:
                continue
            try:
                await self._await_task(name, task)
            except MemorySessionCloseError:
                errors.append(name)

        capture = self._capture
        if capture is not None:
            self._start_worker_close("capture", capture)
        review = self._review
        if review is not None:
            self._start_worker_close("review-client", review)
        for name, task in tuple(self._worker_close_tasks.items()):
            try:
                await self._await_task(name, task)
            except MemorySessionCloseError:
                errors.append(name)

        if errors:
            self._closed = False
            raise MemorySessionCloseError(tuple(dict.fromkeys(errors)))

        self._capture = None
        self._review = None
        self._capture_started = False
        self._watcher_task = None
        self._review_task = None
        self._worker_close_tasks.clear()
        self._active = False
        self._closed = True
        self._prepared = False
        if self._invalidation_task is not None and self._invalidation_task.done():
            self._invalidation_task = None

    async def close(self) -> None:
        """Await every tracked memory task or propagate containment failure."""
        close_task = self._close_task
        if close_task is asyncio.current_task():
            return
        if close_task is None or close_task.done():
            close_task = asyncio.create_task(self._close_impl(), name="household-memory-session-close")
            self._close_task = close_task
        await asyncio.shield(close_task)
