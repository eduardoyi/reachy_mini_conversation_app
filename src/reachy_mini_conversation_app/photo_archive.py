"""Photos Reachy takes, kept owner-only for the admin app's gallery: ``<time>.jpg`` with ``<time>.json`` beside it.

Photos sent to WhatsApp and photos shown on the e-ink screen are kept, and so are photo edits (``<time>.png``, with
the change asked for). The JSON holds who a photo was for, what happened to it and any edit; never a chat id.
``PHOTO_ARCHIVE_ENABLED=0`` keeps nothing.
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


def keep(
    image: bytes, who: str, outcome: str, folder: Path | None = None, *, suffix: str = ".jpg", edit: str = ""
) -> Path | None:
    """Save ``image`` taken for ``who`` with its ``outcome`` (and the ``edit`` it shows); None when not kept."""
    if not config.PHOTO_ARCHIVE_ENABLED:
        return None
    folder = folder or Path(config.PHOTO_ARCHIVE_DIR)
    now = datetime.now(timezone.utc)
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        stem = now.astimezone().strftime(STAMP)
        count = 1
        while (folder / f"{stem}.jpg").exists() or (folder / f"{stem}.png").exists():
            count += 1
            stem = f"{now.astimezone().strftime(STAMP)}-{count}"
        _write_private(folder / f"{stem}{suffix}", image)
        meta = {"who": who, "outcome": outcome, "at": now.isoformat(timespec="seconds")}
        if edit:
            meta["edit"] = edit
        _write_private(folder / f"{stem}.json", json.dumps(meta, ensure_ascii=False).encode())
    except OSError as error:
        logger.warning("Photo not kept for the gallery: %s", type(error).__name__)
        return None
    return folder / f"{stem}{suffix}"
