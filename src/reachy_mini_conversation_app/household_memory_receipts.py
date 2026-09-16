"""In-process receipts for local household-memory mutations."""

import uuid
import logging
import threading
from typing import Literal
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Callable, Iterator


logger = logging.getLogger(__name__)

MutationSource = Literal[
    "tool.remember",
    "tool.update",
    "tool.forget",
    "capture",
    "review",
    "console.clear",
    "migration",
]


@dataclass(frozen=True, slots=True)
class MutationWriterContext:
    """Identify the in-process writer that owns a store mutation."""

    source: MutationSource
    generation: str


@dataclass(frozen=True, slots=True, repr=False)
class MemoryMutationChange:
    """Describe one entry's exact before and after serialized values."""

    memory_id: str
    before: str | None
    after: str | None

    def __repr__(self) -> str:
        """Show IDs and presence flags without private entry values."""
        return (
            "MemoryMutationChange("
            f"memory_id={self.memory_id!r}, before_present={self.before is not None}, "
            f"after_present={self.after is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class MemoryMutationReceipt:
    """Record one committed local mutation without exposing memory text."""

    source: MutationSource
    generation: str
    before_revision: int
    after_revision: int
    changes: tuple[MemoryMutationChange, ...]

    def __repr__(self) -> str:
        """Show receipt metadata without private entry values."""
        changed_ids = tuple(change.memory_id for change in self.changes)
        return (
            "MemoryMutationReceipt("
            f"source={self.source!r}, generation={self.generation}, "
            f"before_revision={self.before_revision}, after_revision={self.after_revision}, "
            f"changed_ids={changed_ids!r})"
        )


MutationReceiptListener = Callable[[MemoryMutationReceipt], None]


_CURRENT_WRITER: ContextVar[MutationWriterContext | None] = ContextVar("household_memory_writer", default=None)


@contextmanager
def mutation_writer(context: MutationWriterContext) -> Iterator[None]:
    """Set the writer context for one synchronous atomic store operation."""
    if not isinstance(context, MutationWriterContext):
        raise TypeError("mutation writer context has an invalid type")
    if not isinstance(context.source, str) or not context.source.strip():
        raise ValueError("mutation writer source must be non-empty")
    if not isinstance(context.generation, str) or not context.generation.strip():
        raise ValueError("mutation writer generation must be non-empty")
    token = _CURRENT_WRITER.set(context)
    try:
        yield
    finally:
        _CURRENT_WRITER.reset(token)


def current_mutation_writer() -> MutationWriterContext | None:
    """Return the current writer context for this task, if one is set."""
    return _CURRENT_WRITER.get()


def new_mutation_generation() -> str:
    """Return an opaque process-unique generation token."""
    return uuid.uuid4().hex


def _normalise_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


class MemoryMutationBus:
    """Deliver bounded, live-only receipts to listeners for one process."""

    def __init__(self) -> None:
        """Create an empty listener registry."""
        self._lock = threading.RLock()
        self._next_token = 0
        self._listeners: dict[str, dict[int, MutationReceiptListener]] = {}

    def subscribe(self, path: str | Path, listener: MutationReceiptListener) -> Callable[[], None]:
        """Subscribe to future receipts for one exact memory path."""
        if not callable(listener):
            raise TypeError("mutation receipt listener must be callable")
        normalized_path = _normalise_path(path)
        with self._lock:
            self._next_token += 1
            token = self._next_token
            self._listeners.setdefault(normalized_path, {})[token] = listener

        def unsubscribe() -> None:
            with self._lock:
                path_listeners = self._listeners.get(normalized_path)
                if path_listeners is None:
                    return
                path_listeners.pop(token, None)
                if not path_listeners:
                    self._listeners.pop(normalized_path, None)

        return unsubscribe

    def publish(self, path: str | Path, receipt: MemoryMutationReceipt) -> None:
        """Deliver one receipt synchronously while the store commit is locked."""
        normalized_path = _normalise_path(path)
        with self._lock:
            listeners = tuple(self._listeners.get(normalized_path, {}).values())
        for listener in listeners:
            try:
                listener(receipt)
            except Exception as exc:
                logger.warning("Household memory receipt listener failed error=%s", type(exc).__name__)


_BUS = MemoryMutationBus()


def memory_mutation_bus() -> MemoryMutationBus:
    """Return the process-local household-memory receipt bus."""
    return _BUS
