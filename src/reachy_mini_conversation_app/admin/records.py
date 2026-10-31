"""Files the admin app edits in place: versioned JSON reads and atomic owner-only writes.

A version is a hash of the file as read. A write names the version it was based on, so when both phones
edit at once the second is told the file changed instead of silently overwriting the first.
"""

import os
import json
import hashlib
import logging
import tempfile
import threading
from typing import Any, Final
from pathlib import Path


logger = logging.getLogger(__name__)

BACKUPS_KEPT: Final[int] = 3
_WRITE_LOCK = threading.Lock()


class RecordError(ValueError):
    """A file that cannot be read or written, or holds something other than a JSON object."""


class StaleVersion(RecordError):
    """The file changed since the version a write was based on."""


def _version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16] if raw else ""


def read_json(path: Path) -> tuple[dict[str, Any], str]:
    """Return the JSON object in ``path`` and its version (``{}`` and ``""`` when the file does not exist)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, ""
    except OSError as error:
        raise RecordError(f"{path.name} cannot be read") from error
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise RecordError(f"{path.name} is not valid JSON") from error
    if not isinstance(document, dict):
        raise RecordError(f"{path.name} does not hold a JSON object")
    return document, _version(raw)


def write_atomic(path: Path, raw: bytes) -> None:
    """Replace ``path`` with ``raw`` in one step, owner-only, creating its folder owner-only."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_json(path: Path, document: dict[str, Any], based_on: str) -> str:
    """Write ``document`` to ``path`` if it is still at version ``based_on``; returns the new version."""
    raw = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    with _WRITE_LOCK:
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = b""
        except OSError as error:
            raise RecordError(f"{path.name} cannot be read") from error
        if _version(current) != based_on:
            raise StaleVersion(f"{path.name} changed elsewhere")
        try:
            if current:
                for index in range(BACKUPS_KEPT - 1, 0, -1):
                    older = path.with_name(f"{path.name}.bak{index}")
                    if older.exists():
                        os.replace(older, path.with_name(f"{path.name}.bak{index + 1}"))
                write_atomic(path.with_name(f"{path.name}.bak1"), current)
            write_atomic(path, raw)
        except OSError as error:
            raise RecordError(f"{path.name} cannot be written") from error
    return _version(raw)
