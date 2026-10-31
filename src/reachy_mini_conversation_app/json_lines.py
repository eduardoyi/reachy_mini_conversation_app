"""Small owner-only logs of one JSON object per line: the household activity and the admin app's audit log."""

import os
import json
import logging
import threading
from typing import Any, Final
from pathlib import Path
from datetime import datetime, timezone
from collections import deque


logger = logging.getLogger(__name__)

# Past this size a log keeps only its newest KEPT_LINES lines.
MAX_BYTES: Final[int] = 512 * 1024
KEPT_LINES: Final[int] = 1000


class JsonLines:
    """Append-only log at ``path``, newest last; read back newest first."""

    def __init__(self, path: Path) -> None:
        """Keep the log at ``path`` (its folder is created owner-only on first write)."""
        self.path = path
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        """Add ``entry`` stamped with the time; a failure is logged, never raised."""
        line = json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}, ensure_ascii=False
        )
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8") as file:
                    file.write(line + "\n")
                if self.path.stat().st_size > MAX_BYTES:
                    kept = self.path.read_bytes().splitlines()[-KEPT_LINES:]
                    trimmed = self.path.with_name(f".{self.path.name}.trim")
                    trimmed.write_bytes(b"\n".join(kept) + b"\n")
                    os.chmod(trimmed, 0o600)
                    os.replace(trimmed, self.path)
            except OSError as error:
                logger.warning("%s not written: %s", self.path.name, type(error).__name__)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the newest ``limit`` entries, newest first, skipping unreadable lines."""
        try:
            with self.path.open(encoding="utf-8") as file:
                lines = deque(file, maxlen=limit)
        except FileNotFoundError:
            return []
        except OSError as error:
            logger.warning("%s unreadable: %s", self.path.name, type(error).__name__)
            return []
        entries = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries
