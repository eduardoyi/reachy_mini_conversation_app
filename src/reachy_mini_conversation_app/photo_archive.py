"""Photos Reachy takes, kept owner-only for the admin app's gallery: ``<time>.jpg`` with ``<time>.json`` beside it.

The JSON holds who the photo was for and whether it was sent; never a chat id. ``PHOTO_ARCHIVE_ENABLED=0`` keeps
nothing (photos are then only sent).
"""

import os
import json
import logging
from typing import Final
from pathlib import Path
from datetime import datetime, timezone

from reachy_mini_conversation_app.config import config


logger = logging.getLogger(__name__)

STAMP: Final[str] = "%Y%m%d-%H%M%S"


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)


def keep(jpeg: bytes, who: str, outcome: str, folder: Path | None = None) -> Path | None:
    """Save ``jpeg`` taken for ``who`` with its send ``outcome``; returns the file, or None when not kept."""
    if not config.PHOTO_ARCHIVE_ENABLED:
        return None
    folder = folder or Path(config.PHOTO_ARCHIVE_DIR)
    now = datetime.now(timezone.utc)
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        stem = now.astimezone().strftime(STAMP)
        suffix = 1
        while (folder / f"{stem}.jpg").exists():
            suffix += 1
            stem = f"{now.astimezone().strftime(STAMP)}-{suffix}"
        _write_private(folder / f"{stem}.jpg", jpeg)
        meta = {"who": who, "outcome": outcome, "at": now.isoformat(timespec="seconds")}
        _write_private(folder / f"{stem}.json", json.dumps(meta, ensure_ascii=False).encode())
    except OSError as error:
        logger.warning("Photo not kept for the gallery: %s", type(error).__name__)
        return None
    return folder / f"{stem}.jpg"
