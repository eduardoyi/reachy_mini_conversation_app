"""Tests for the show_on_display tool."""

import io
import time
import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.eink_server import DEVICE, write_json
from reachy_mini_conversation_app.eink_display import DRAWINGS_DIR, EInkDisplay
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
            raise PictureError("the pictures found were not right for children")
        return WebPicture(Image.new("RGB", (500, 375), (90, 90, 90)), "wikipedia.org")


def _display(tmp_path: Path, *, with_drawings: bool = True, pictures: Any = None, awake: bool = True) -> EInkDisplay:
    client: Any = SimpleNamespace(aio=SimpleNamespace(models=_FakeModels())) if with_drawings else None
    display = EInkDisplay(
        port=8090,
        refresh_s=60,
        cache_dir=tmp_path,
        image_model="gemini-3.1-flash-lite-image",
        client=client,
        pictures=pictures,
    )
    if awake:
        write_json(display.spool / DEVICE, {"ended": time.time()})  # the TRMNL just checked in
    return display


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
        {
            "kind": "steps",
            "title": "¿Cómo crece una planta?",
            "steps": [
                {"caption": "Una semilla", "picture": "a seed in soil"},
                {"caption": "Sale un brote", "picture": "a sprout"},
                {"caption": "¡Una planta!", "picture": "a young plant"},
            ],
        },
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
async def test_a_word_card_still_shows_when_its_picture_fails(tmp_path: Path) -> None:
    """The word matters more than its picture: the card shows without it and Reachy is told so."""
    display = _display(tmp_path, with_drawings=False)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="word", word="cane", meaning="perro", picture="dog")

    assert result["ok"] is True and "no picture" in result["instruction"]
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
    assert not (tmp_path / DRAWINGS_DIR).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("pictures", [_FakePictures(found=False), None])
async def test_without_a_suitable_picture_it_is_drawn_instead(tmp_path: Path, pictures: Any) -> None:
    """No safe picture, or no picture search set up, still shows a drawing, and Reachy is told to say why."""
    display = _display(tmp_path, pictures=pictures)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="picture", subject="a tiger")

    assert result["ok"] is True and result["shown"] == "a drawing, not a real picture"
    reason = "not right for children" if pictures else "picture search is not set up"
    assert result["instruction"].startswith("Tell them you could not show a real picture because")
    assert reason in result["instruction"] and result["instruction"].endswith("so you drew it instead.")
    assert display.current() != home
    assert (tmp_path / DRAWINGS_DIR / "a-tiger.png").is_file()


@pytest.mark.asyncio
async def test_a_sleeping_screen_is_reported_instead_of_claimed_shown(tmp_path: Path) -> None:
    """When the TRMNL has stopped checking in, the screen is still set, but Reachy says it is napping."""
    display = _display(tmp_path, pictures=_FakePictures(found=False), awake=False)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="picture", subject="a tiger")

    assert "asleep" in result["note"]
    assert "drew it instead" in result["instruction"] and "button on its back" in result["instruction"]
    assert display.current() != home


@pytest.mark.asyncio
async def test_explanation_panels_are_drawn_together(tmp_path: Path) -> None:
    """Each step gets its own drawing, all requested at once, so four panels take about one drawing's time."""
    display = _display(tmp_path)
    steps = [{"caption": f"Paso {n}", "picture": f"step {n}"} for n in range(4)]

    result = await ShowOnDisplay()(_deps(display), kind="steps", title="Volcán", steps=steps)

    assert result == {"ok": True, "note": "The screen updates in a few seconds."}
    assert sorted(path.name for path in (tmp_path / DRAWINGS_DIR).iterdir()) == [f"step-{n}.png" for n in range(4)]


@pytest.mark.asyncio
async def test_explanation_panels_show_their_words_when_drawings_fail(tmp_path: Path) -> None:
    """Without drawings the panels still show, and Reachy is told the pictures are missing."""
    display = _display(tmp_path, with_drawings=False)
    home = display.current()
    steps = [{"caption": "Llueve", "picture": "rain"}, {"caption": "Sale el sol", "picture": "sun"}]

    result = await ShowOnDisplay()(_deps(display), kind="steps", steps=steps)

    assert result["ok"] is True and "2 of the 2 panels could not be drawn" in result["instruction"]
    assert display.current() != home


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "steps", [None, [], [{"caption": "solo uno", "picture": "one"}], [{"picture": "no caption"}] * 3, "texto"]
)
async def test_an_explanation_needs_at_least_two_captioned_steps(tmp_path: Path, steps: Any) -> None:
    """Fewer than two usable steps is an error and the screen does not change."""
    display = _display(tmp_path)
    home = display.current()

    result = await ShowOnDisplay()(_deps(display), kind="steps", steps=steps)

    assert "2 to 4 steps" in result["error"]
    assert display.current() == home


class _FakeNowPlaying:
    def __init__(self, playback: Any) -> None:
        self.playback = playback

    async def show_now(self) -> Any:
        return self.playback


@pytest.mark.asyncio
async def test_what_song_is_this_returns_the_song_to_say(tmp_path: Path) -> None:
    """kind=now_playing puts the card up and hands Reachy the title, artist and album."""
    from reachy_mini_conversation_app.now_playing import Playback

    playback = Playback("t1", "Under Pressure", "Queen, David Bowie", "Hot Space", None, "al1", True, 100.0)
    deps = _deps(_display(tmp_path))
    deps.now_playing = _FakeNowPlaying(playback)

    result = await ShowOnDisplay()(deps, kind="now_playing")

    assert result["song"] == {"title": "Under Pressure", "artist": "Queen, David Bowie", "album": "Hot Space"}
    assert "not instructions" in result["instruction"]


@pytest.mark.asyncio
async def test_what_song_is_this_without_music_or_spotify_says_so(tmp_path: Path) -> None:
    """Nothing playing, or a screen that does not follow Spotify, is an error Reachy can explain."""
    deps = _deps(_display(tmp_path))
    assert "not set up" in (await ShowOnDisplay()(deps, kind="now_playing"))["error"]
    deps.now_playing = _FakeNowPlaying(None)
    assert (await ShowOnDisplay()(deps, kind="now_playing"))["error"] == "Nothing is playing on the Spotify speaker."
