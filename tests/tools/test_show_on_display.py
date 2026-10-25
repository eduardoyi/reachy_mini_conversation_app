"""Tests for the show_on_display tool."""

import io
import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.eink_display import EInkDisplay
from reachy_mini_conversation_app.web_pictures import WebPicture, PictureError
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.show_on_display import ShowOnDisplay


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 200, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeModels:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_content(self, **_kwargs: Any) -> Any:
        self.calls += 1
        await asyncio.sleep(0)
        part = SimpleNamespace(inline_data=SimpleNamespace(data=_jpeg()))
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))], usage_metadata=None
        )


class _FakePictures:
    def __init__(self, found: bool = True) -> None:
        self.found = found

    async def find(self, subject: str) -> WebPicture:
        if not self.found:
            raise PictureError("no picture passed the check")
        return WebPicture(Image.new("RGB", (500, 375), (90, 90, 90)), "wikipedia.org")


def _display(
    tmp_path: Path, *, with_drawings: bool = True, drawings_per_day: int = 20, pictures: Any = None
) -> EInkDisplay:
    client: Any = SimpleNamespace(aio=SimpleNamespace(models=_FakeModels())) if with_drawings else None
    return EInkDisplay(
        port=8090,
        refresh_s=60,
        cache_dir=tmp_path,
        drawings_per_day=drawings_per_day,
        image_model="gemini-3.1-flash-lite-image",
        client=client,
        pictures=pictures,
    )


def _deps(display: EInkDisplay | None) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), display=display)


@pytest.mark.asyncio
async def test_without_a_display_the_tool_says_so() -> None:
    """With no e-ink screen set up the tool returns an error instead of failing."""
    assert await ShowOnDisplay()(_deps(None), kind="text", lines=["hola"]) == {"error": "No e-ink screen is set up."}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "drawing", "subject": "a dinosaur", "caption": "Un dinosaurio"},
        {"kind": "word", "word": "la mela", "pronunciation": "la MÉ-la", "meaning": "la manzana", "picture": "apple"},
        {"kind": "word", "word": "arcobaleno", "meaning": "arcoíris", "header": "¿Cómo se dice «arcoíris»?"},
        {"kind": "text", "lines": ["3 + 4 = 7"]},
        {"kind": "text", "title": "Lista", "lines": ["leche", "pan"]},
    ],
)
async def test_each_kind_becomes_the_next_screen(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    """Drawings, word cards and text replace the resting card."""
    display = _display(tmp_path)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), **kwargs)

    assert result["ok"] is True
    assert display.current() != home


@pytest.mark.asyncio
async def test_home_goes_back_to_the_resting_card(tmp_path: Path) -> None:
    """kind=home brings back the resting card."""
    display = _display(tmp_path)
    home = display.current()
    await ShowOnDisplay()(_deps(display), kind="text", lines=["hola"])

    assert (await ShowOnDisplay()(_deps(display), kind="home"))["ok"] is True
    assert display.current() == home


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"kind": "drawing"}, {"kind": "word", "word": " "}, {"kind": "text", "lines": ["", 3]}, {"kind": "video"}],
)
async def test_incomplete_requests_are_errors_and_change_nothing(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    """Missing subjects, words or lines, and unknown kinds, return an error and keep the screen."""
    display = _display(tmp_path)
    home = display.current()

    assert "error" in await ShowOnDisplay()(_deps(display), **kwargs)
    assert display.current() == home


@pytest.mark.asyncio
async def test_drawing_failures_tell_the_model_not_to_retry(tmp_path: Path) -> None:
    """A drawing that cannot be made keeps the screen and asks Reachy to say so without retrying."""
    display = _display(tmp_path, with_drawings=False)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="drawing", subject="a cat")

    assert "Could not draw" in result["error"] and "Do not retry" in result["instruction"]
    assert display.current() == home


@pytest.mark.asyncio
async def test_the_daily_limit_offers_cards_instead(tmp_path: Path) -> None:
    """After today's drawings are used, Reachy is told to offer a word or text card."""
    display = _display(tmp_path, drawings_per_day=0)

    result = await ShowOnDisplay()(_deps(display), kind="drawing", subject="a cat")

    assert result["error"] == "No more new drawings today."
    assert "word or text card" in result["instruction"]


@pytest.mark.asyncio
async def test_a_word_card_still_shows_when_its_picture_fails(tmp_path: Path) -> None:
    """The word matters more than its picture: the card shows without it and the note says so."""
    display = _display(tmp_path, with_drawings=False)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="word", word="cane", meaning="perro", picture="dog")

    assert result["ok"] is True and "no picture" in result["note"]
    assert display.current() != home


@pytest.mark.parametrize("enabled", [True, False])
def test_the_tool_is_offered_only_with_trmnl_enabled(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """show_on_display appears with TRMNL_ENABLED and is removed without it, even if a profile lists it."""
    monkeypatch.setattr(config, "TRMNL_ENABLED", enabled)
    monkeypatch.setattr(config, "AUTOLOAD_EXTERNAL_TOOLS", False)
    monkeypatch.setattr(core_tools, "read_profile_tool_names", lambda *_args: ["camera", "show_on_display"])
    monkeypatch.setattr(core_tools, "read_profile_tool_override", lambda *_args: None)

    assert ("show_on_display" in core_tools._read_profile_tool_names(None)) is enabled


@pytest.mark.asyncio
async def test_a_web_picture_is_shown_when_one_passes_the_check(tmp_path: Path) -> None:
    """kind=picture shows the checked web picture and draws nothing."""
    display = _display(tmp_path, pictures=_FakePictures())
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="picture", subject="Mona Lisa", caption="La Gioconda")

    assert result == {"ok": True, "note": "The screen updates in a few seconds."}
    assert display.current() != home
    assert not (tmp_path / "drawings").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("pictures", [_FakePictures(found=False), None])
async def test_without_a_suitable_picture_it_is_drawn_instead(tmp_path: Path, pictures: Any) -> None:
    """No safe picture, or no picture search set up, still shows something: a drawing, and the note says so."""
    display = _display(tmp_path, pictures=pictures)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="picture", subject="a tiger")

    assert result["ok"] is True and "drawing" in result["note"]
    assert display.current() != home
    assert (tmp_path / "drawings" / "a-tiger.png").is_file()
