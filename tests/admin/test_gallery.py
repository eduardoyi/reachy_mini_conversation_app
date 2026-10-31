"""The gallery: what it lists from the photo archive and the display cache, and what ids it refuses."""

import os
import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from reachy_mini_conversation_app import photo_archive
from reachy_mini_conversation_app.admin.gallery import Gallery, GalleryError


def _png(path: Path, source: str = "", subject: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    if source:
        info.add_text("source", source)
    if subject:
        info.add_text("subject", subject)
    Image.new("L", (800, 480), 255).save(path, pnginfo=info)
    return path


def _jpeg() -> bytes:
    out = BytesIO()
    Image.new("RGB", (640, 480), (90, 120, 160)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def gallery(tmp_path: Path) -> Gallery:
    """Build a gallery over a temp photo archive and display cache."""
    return Gallery(tmp_path / "photos", tmp_path / "display")


def test_items_cover_photos_drawings_lessons_and_web_pictures(tmp_path: Path, gallery: Gallery) -> None:
    """Each kind is listed with a readable title and its notes, newest first; album art is not."""
    display = tmp_path / "display"
    old = _png(display / "drawings-v2" / "washing-machine-filter.png")
    _png(display / "drawings-v3" / "a-red-duck.png")
    _png(
        display / "drawings-v3" / "enzo-driving-a-convertible-with-a-lion-as-co-pilot-and-a-dolph.png",
        subject="Enzo driving a convertible with a lion as co-pilot and a dolphin in the back seat",
    )
    _png(display / "pictures" / "jaguar.png", source="https://example.org/jaguar")
    _png(display / "lessons" / "duck.png")
    (display / "lessons" / "duck.json").write_text(json.dumps({"Cómo dibujar un pato": ["Un óvalo", "Un círculo"]}))
    _png(display / "album-art" / "6GnzWMUyNEETCq6eftD98v.png")
    photo_archive.keep(_jpeg(), "Tío Sergio", "sent", folder=tmp_path / "photos")
    past = time.time() - 86400
    os.utime(old, (past, past))

    items = {item["id"]: item for item in gallery.items()}
    assert set(items) == {
        "drawings-v2/washing-machine-filter.png", "drawings-v3/a-red-duck.png", "pictures/jaguar.png",
        "drawings-v3/enzo-driving-a-convertible-with-a-lion-as-co-pilot-and-a-dolph.png",
        "lessons/duck.png", next(i for i in items if i.startswith("photos/")),
    }  # fmt: skip
    assert items["drawings-v3/a-red-duck.png"]["title"] == "A red duck"
    long_one = items["drawings-v3/enzo-driving-a-convertible-with-a-lion-as-co-pilot-and-a-dolph.png"]
    assert long_one["title"] == "Enzo driving a convertible with a lion as co-pilot and a dolphin in the back seat"
    assert items["drawings-v2/washing-machine-filter.png"]["old_style"] is True
    assert items["pictures/jaguar.png"]["source"] == "https://example.org/jaguar"
    assert items["lessons/duck.png"]["title"] == "Cómo dibujar un pato"
    assert items["lessons/duck.png"]["steps"] == ["Un óvalo", "Un círculo"]
    photo = next(item for item in items.values() if item["kind"] == "photo")
    assert (photo["title"], photo["who"], photo["width"]) == ("Photo for Tío Sergio", "Tío Sergio", 640)
    assert gallery.items()[-1]["id"] == "drawings-v2/washing-machine-filter.png"


@pytest.mark.parametrize(
    "item_id",
    ["photos/../secret.jpg", "nowhere/a.png", "drawings-v3/missing.png", "album-art/x.png", "lessons/duck.json",
     "photos/.hidden.jpg", "drawings-v3/"],
)  # fmt: skip
def test_ids_outside_the_gallery_are_refused(tmp_path: Path, gallery: Gallery, item_id: str) -> None:
    """Only plain image files in the gallery's own folders can be opened or deleted."""
    (tmp_path / "secret.jpg").write_bytes(b"x")
    _png(tmp_path / "display" / "album-art" / "x.png")
    (tmp_path / "display" / "lessons").mkdir(parents=True)
    (tmp_path / "display" / "lessons" / "duck.json").write_text("{}")
    with pytest.raises(GalleryError):
        gallery.path(item_id)
    with pytest.raises(GalleryError):
        gallery.delete(item_id)


def test_thumbnails_are_small_jpegs_and_delete_takes_the_notes_too(tmp_path: Path, gallery: Gallery) -> None:
    """A thumbnail fits 480 px; deleting a lesson removes its captions file as well."""
    display = tmp_path / "display"
    _png(display / "lessons" / "duck.png")
    (display / "lessons" / "duck.json").write_text("{}")
    thumb = gallery.thumbnail("lessons/duck.png")
    assert thumb[:2] == b"\xff\xd8"
    with Image.open(BytesIO(thumb)) as image:
        assert max(image.size) == 480
    gallery.delete("lessons/duck.png")
    assert not list((display / "lessons").iterdir())


def test_photos_are_kept_owner_only_unless_turned_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A kept photo has its notes beside it, both owner-only; with the archive off nothing is written."""
    folder = tmp_path / "photos"
    path = photo_archive.keep(b"jpeg", "Familia Yi", "failed", folder=folder)
    assert path is not None and path.read_bytes() == b"jpeg"
    assert json.loads(path.with_suffix(".json").read_text())["who"] == "Familia Yi"
    assert path.stat().st_mode & 0o777 == 0o600 and folder.stat().st_mode & 0o777 == 0o700
    second = photo_archive.keep(b"jpeg2", "Familia Yi", "sent", folder=folder)
    assert second is not None and second != path
    monkeypatch.setattr(photo_archive.config, "PHOTO_ARCHIVE_ENABLED", False)
    assert photo_archive.keep(b"jpeg3", "Familia Yi", "sent", folder=folder) is None
    assert len(list(folder.glob("*.jpg"))) == 2
