"""Tests for the show_on_display tool."""

import io
import json
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
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
            text=json.dumps({"captions": ["Un óvalo", "Un cuello", "Cuatro patas", "La carita"]}),  # lesson captions
            usage_metadata=None,
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
        {"kind": "lesson", "subject": "a giraffe", "title": "Cómo dibujar una jirafa"},
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
    [
        {"kind": "drawing"},
        {"kind": "lesson", "subject": "a giraffe"},
        {"kind": "word", "word": " "},
        {"kind": "text", "lines": ["", 3]},
        {"kind": "video"},
    ],
)
async def test_incomplete_requests_are_errors_and_change_nothing(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    """Missing subjects, words or lines, and unknown kinds, return an error and keep the screen."""
    display = _display(tmp_path)
    home = display.current()

    assert "error" in await ShowOnDisplay()(_deps(display), **kwargs)
    assert display.current() == home


@pytest.mark.asyncio
async def test_a_lesson_tells_reachy_not_to_read_the_steps_out(tmp_path: Path) -> None:
    """A lesson is one screen of captioned steps; Reachy only points to it."""
    display = _display(tmp_path)

    result = await ShowOnDisplay()(_deps(display), kind="lesson", subject="a shark", title="Cómo dibujar un tiburón")

    assert result["ok"] is True
    assert "do not read them out" in result["instruction"]
    assert "lesson" in ShowOnDisplay.parameters_schema["properties"]["kind"]["enum"]


@pytest.mark.asyncio
async def test_writing_starts_practice_and_asks_reachy_to_end_the_conversation(tmp_path: Path) -> None:
    """kind=writing hands the word to writing practice; a word too long comes back with a hint."""
    started: list[str] = []

    class Writing:
        def start(self, text: str) -> str | None:
            if len(text) > 12:
                return "It has 17 letters."
            started.append(text)
            return None

    deps = _deps(_display(tmp_path))
    deps.writing = Writing()

    result = await ShowOnDisplay()(deps, kind="writing", word="PAPÁ")
    assert result["ok"] is True and "end_conversation" in result["instruction"]
    assert started == ["PAPÁ"]

    refused = await ShowOnDisplay()(deps, kind="writing", word="MURCIÉLAGO GIGANTE")
    assert refused["error"] == "It has 17 letters." and "shorter" in refused["instruction"]

    deps.writing = None
    assert "error" in await ShowOnDisplay()(deps, kind="writing", word="PAPÁ")


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


def _camera_deps(display: EInkDisplay, frame: bytes | None = None, enabled: bool = True) -> ToolDependencies:
    deps = _deps(display)
    deps.camera_enabled = enabled
    deps.reachy_mini.media.get_frame_jpeg.return_value = frame
    return deps


@pytest.fixture
def no_countdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take camera photos at once in tests."""
    from reachy_mini_conversation_app.tools import show_on_display

    monkeypatch.setattr(show_on_display, "CAMERA_COUNTDOWN_S", 0.0)


@pytest.mark.asyncio
async def test_a_camera_photo_is_shown_for_ten_minutes_and_kept_only_for_the_gallery(
    tmp_path: Path, no_countdown: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kind=camera shows the photo, marked to leave after 10 minutes; it is kept for the gallery, not cached."""
    from reachy_mini_conversation_app import photo_archive
    from reachy_mini_conversation_app.eink_server import CURRENT, read_json

    monkeypatch.setattr(photo_archive.config, "PHOTO_ARCHIVE_DIR", str(tmp_path / "photos"))
    display = _display(tmp_path)
    home = display.current()

    result = await ShowOnDisplay()(_camera_deps(display, _jpeg()), kind="camera", caption="¡Hola!")

    assert result["ok"] is True and display.current() != home
    current = read_json(display.spool / CURRENT)
    assert current["private"] is True and current["expires_at"] == pytest.approx(current["shown_at"] + 600)
    assert not (tmp_path / DRAWINGS_DIR).exists() and not (tmp_path / "pictures").exists()
    assert [path.suffix for path in (tmp_path / "photos").iterdir()] and len(
        list((tmp_path / "photos").glob("*.jpg"))
    ) == 1


@pytest.mark.asyncio
async def test_an_edited_photo_sends_the_photo_and_the_change(
    tmp_path: Path, no_countdown: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an edit, the photo and the change go to the image model; the photo and the edit are kept, not cached."""
    from reachy_mini_conversation_app import photo_archive

    monkeypatch.setattr(photo_archive.config, "PHOTO_ARCHIVE_DIR", str(tmp_path / "photos"))
    display = _display(tmp_path)
    models = display._client.aio.models
    sent: list[Any] = []
    original = models.generate_content

    async def recording(**kwargs: Any) -> Any:
        sent.append(kwargs["contents"])
        return await original(**kwargs)

    models.generate_content = recording

    result = await ShowOnDisplay()(_camera_deps(display, _jpeg()), kind="camera", edit="make him a pirate")

    assert result["ok"] is True
    prompt, photo = sent[0]
    assert "make him a pirate" in prompt and photo.inline_data.mime_type == "image/jpeg"
    assert not (tmp_path / DRAWINGS_DIR).exists()
    [edit] = (tmp_path / "photos").glob("*.png")
    assert json.loads(edit.with_suffix(".json").read_text())["edit"] == "make him a pirate"
    assert len(list((tmp_path / "photos").glob("*.jpg"))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "frame", "error"),
    [(False, None, "camera is off"), (True, None, "gave no picture")],
)
async def test_no_camera_or_no_frame_is_an_error(
    tmp_path: Path, no_countdown: None, enabled: bool, frame: Any, error: str
) -> None:
    """A disabled camera or an empty frame leaves the screen as it was."""
    display = _display(tmp_path)
    home = display.current()

    result = await ShowOnDisplay()(_camera_deps(display, frame, enabled), kind="camera")

    assert error in result["error"] and display.current() == home


@pytest.mark.asyncio
async def test_a_failed_edit_offers_the_plain_photo(tmp_path: Path, no_countdown: None) -> None:
    """When the edit cannot be made (refused, no key, too slow), Reachy offers the plain photo instead."""
    display = _display(tmp_path, with_drawings=False)
    home = display.current()

    result = await ShowOnDisplay()(_camera_deps(display, _jpeg()), kind="camera", edit="make her a superhero")

    assert "Could not make that change" in result["error"] and "plain photo" in result["instruction"]
    assert display.current() == home


def _held_models(display: EInkDisplay) -> tuple[_FakeModels, asyncio.Event]:
    """Make the display's image model wait until the returned event is set."""
    models = display._client.aio.models
    release = asyncio.Event()
    original = models.generate_content

    async def held(**kwargs: Any) -> Any:
        await release.wait()
        return await original(**kwargs)

    models.generate_content = held
    return models, release


@pytest.mark.asyncio
async def test_only_one_picture_is_made_at_a_time(tmp_path: Path, no_countdown: None) -> None:
    """While a drawing is made, other pictures are refused without calling the model; text still shows."""
    display = _display(tmp_path)
    models, release = _held_models(display)
    tool = ShowOnDisplay()
    deps = _camera_deps(display, _jpeg())
    first = asyncio.create_task(tool(deps, kind="drawing", subject="a cat"))
    await asyncio.sleep(0)

    for kwargs in (
        {"kind": "camera", "edit": "make him a Minecraft character"},
        {"kind": "drawing", "subject": "a blocky character"},
        {"kind": "word", "word": "gato", "picture": "a cat"},
    ):
        refused = await tool(deps, **kwargs)
        assert "still being made" in refused["error"] and "Do not call" in refused["instruction"]
    assert (await tool(deps, kind="text", lines=["hola"]))["ok"] is True

    release.set()
    assert (await first)["ok"] is True
    assert models.calls == 1
    assert (await tool(deps, kind="drawing", subject="a dog"))["ok"] is True


@pytest.mark.asyncio
async def test_a_cancelled_picture_does_not_block_the_next(tmp_path: Path) -> None:
    """A picture cancelled with its conversation frees the screen for the next one."""
    display = _display(tmp_path)
    _held_models(display)
    tool = ShowOnDisplay()
    first = asyncio.create_task(tool(_deps(display), kind="drawing", subject="a cat"))
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert tool._making_picture is False
