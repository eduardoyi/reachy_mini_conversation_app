"""The gallery: photos Reachy took and the pictures he made for the e-ink screen, read from where they already live.

Each item's id is ``<source>/<file>``; only the folders in ``sources`` and plain file names are ever opened, so an id
cannot reach anything else. Thumbnails are made on request and kept in memory.
"""

import io
import re
import json
import logging
from typing import Any, Final
from pathlib import Path
from datetime import datetime, timezone
from functools import lru_cache

from PIL import Image


logger = logging.getLogger(__name__)

THUMB_PX: Final[int] = 480
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(png|jpg)")
# Display cache folders and what they hold; drawings/ and drawings-v2/ are older drawing styles.
DISPLAY_SOURCES: Final[dict[str, str]] = {
    "drawings-v3": "drawing",
    "drawings-v2": "drawing",
    "drawings": "drawing",
    "lessons": "lesson",
    "pictures": "picture",
}


class GalleryError(ValueError):
    """An id that names no gallery item."""


def _title(stem: str) -> str:
    words = stem.replace("-", " ").strip()
    return words[:1].upper() + words[1:]


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=2048)
def _image_info(path: Path, mtime: float) -> tuple[int, int, str, str]:
    """Return width, height and the PNG's notes: ``source`` (where a web picture came from), ``subject`` (a drawing's)."""
    with Image.open(path) as image:
        return image.width, image.height, str(image.info.get("source", "")), str(image.info.get("subject", ""))


@lru_cache(maxsize=256)
def _thumbnail(path: Path, mtime: float) -> bytes:
    with Image.open(path) as image:
        image.thumbnail((THUMB_PX, THUMB_PX))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=82)
        return out.getvalue()


class Gallery:
    """Photos in ``photos_dir`` and the display cache's pictures under ``display_dir``."""

    def __init__(self, photos_dir: Path, display_dir: Path) -> None:
        """Read photos from ``photos_dir`` and drawings, lessons and web pictures from ``display_dir``."""
        self.sources: dict[str, tuple[Path, str]] = {"photos": (photos_dir, "photo")}
        self.sources.update({name: (display_dir / name, kind) for name, kind in DISPLAY_SOURCES.items()})

    def items(self) -> list[dict[str, Any]]:
        """Return every item, newest first."""
        found = []
        for source, (folder, kind) in self.sources.items():
            try:
                files = [f for f in folder.iterdir() if _FILE.fullmatch(f.name)]
            except OSError:
                continue
            for file in files:
                try:
                    found.append(self._item(source, kind, file))
                except (OSError, ValueError) as error:
                    logger.warning("Gallery skipped a %s: %s", kind, type(error).__name__)
        return sorted(found, key=lambda item: item["at"], reverse=True)

    def _item(self, source: str, kind: str, file: Path) -> dict[str, Any]:
        mtime = file.stat().st_mtime
        width, height, web, subject = _image_info(file, mtime)
        item: dict[str, Any] = {
            "id": f"{source}/{file.name}", "kind": kind, "title": _title(file.stem), "at": _iso(mtime),
            "width": width, "height": height,
        }  # fmt: skip
        if kind == "photo":
            meta = self._sidecar(file)
            item["who"] = str(meta.get("who") or "")
            item["edit"] = str(meta.get("edit") or "")
            if item["edit"]:
                item["title"] = item["edit"][:1].upper() + item["edit"][1:]
            else:
                item["title"] = f"Photo for {item['who']}" if item["who"] else "Photo"
            item["at"] = str(meta.get("at") or item["at"])
        elif kind == "lesson":
            lesson = self._sidecar(file)
            if lesson:
                title, steps = next(iter(lesson.items()))
                item["title"] = str(title)
                item["steps"] = [str(step) for step in steps] if isinstance(steps, list) else []
        elif kind == "picture" and web:
            item["source"] = web
        if subject:
            item["title"] = subject[:1].upper() + subject[1:]
        if source != "drawings-v3" and kind == "drawing":
            item["old_style"] = True
        return item

    @staticmethod
    def _sidecar(file: Path) -> dict[str, Any]:
        try:
            data = json.loads(file.with_suffix(".json").read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def path(self, item_id: str) -> Path:
        """Return the file for ``item_id``; raises GalleryError for anything that is not a gallery item."""
        source, _, name = item_id.partition("/")
        if source not in self.sources or not _FILE.fullmatch(name):
            raise GalleryError("no such picture")
        path = self.sources[source][0] / name
        if not path.is_file():
            raise GalleryError("no such picture")
        return path

    def thumbnail(self, item_id: str) -> bytes:
        """Return a small JPEG of the item."""
        path = self.path(item_id)
        return _thumbnail(path, path.stat().st_mtime)

    def delete(self, item_id: str) -> None:
        """Delete the item and its notes (a photo's or lesson's JSON)."""
        path = self.path(item_id)
        path.unlink()
        path.with_suffix(".json").unlink(missing_ok=True)
