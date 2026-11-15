import io
import json
from typing import Any
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock

import pytest
from PIL import Image

from reachy_mini_conversation_app import photo_archive
from reachy_mini_conversation_app.tools import magazine_cover
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.eink_display import MAX_PNG_BYTES, render_magazine
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.magazine_cover import MagazineCover, issue_line
from reachy_mini_conversation_app.tools.show_on_display import PHOTO_ON_SCREEN_S


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1280, 720), (120, 90, 60)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep covers in a temporary gallery, skip the countdown and take a fixed photo."""
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(config, "PHOTO_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(config, "PHOTO_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setattr(magazine_cover.asyncio, "sleep", wait)
    monkeypatch.setattr(magazine_cover, "start_framing", lambda deps: None)
    monkeypatch.setattr(magazine_cover, "take_photo", lambda media, faces: _jpeg())
    return tmp_path


def _deps(camera: bool = True, display: Any = "screen") -> ToolDependencies:
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_enabled=camera,
        display=MagicMock() if display == "screen" else display,
    )


@pytest.mark.asyncio
async def test_the_cover_is_shown_like_a_photo_and_kept_at_home_with_its_issue_number(archive: Path) -> None:
    """The cover goes on the screen for 10 minutes and into the gallery; the next cover is the next issue."""
    deps = _deps()
    reply = await MagazineCover()(deps, headline="Enzo: «El T-Rex es mío»", lines=["La abuela llega el sábado"])

    assert reply["ok"] and "instruction" in reply
    png = deps.display.show.call_args.args[0]
    assert png.startswith(b"\x89PNG") and deps.display.show.call_args.kwargs == {"expires_after_s": PHOTO_ON_SCREEN_S}
    deps.movement_manager.frame_faces.assert_called_once_with(False)
    [meta] = [json.loads(path.read_text()) for path in archive.glob("*.json")]
    assert meta["outcome"] == "magazine" and meta["edit"] == "Magazine: Enzo: «El T-Rex es mío»"
    assert photo_archive.count("magazine") == 1

    await MagazineCover()(deps, headline="Otra portada")
    assert photo_archive.count("magazine") == 2


@pytest.mark.asyncio
async def test_no_screen_no_camera_no_headline_or_no_picture_says_why(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each missing piece is an error Reachy can say; nothing is kept."""
    assert "error" in await MagazineCover()(_deps(display=None), headline="Hola")
    assert "error" in await MagazineCover()(_deps(camera=False), headline="Hola")
    assert "error" in await MagazineCover()(_deps(), headline="  ")
    monkeypatch.setattr(magazine_cover, "take_photo", lambda media, faces: None)
    assert "error" in await MagazineCover()(_deps(), headline="Hola")
    assert list(archive.iterdir()) == []


def test_the_cover_fits_the_trmnl_and_drops_what_its_fonts_cannot_draw() -> None:
    """A busy photo, emoji and curly quotes still make a cover the TRMNL can draw."""
    photo = Image.effect_noise((1280, 720), 120).convert("RGB")
    png = render_magazine(
        photo, "“Papá” baila salsa 🕺 " * 3, ["Uno", "Dos", "Tres"], issue_line(7, date(2026, 11, 15))
    )
    assert len(png) <= MAX_PNG_BYTES
    assert issue_line(7, date(2026, 11, 15)) == "Nº 7 · DOMINGO 15 DE NOVIEMBRE"
