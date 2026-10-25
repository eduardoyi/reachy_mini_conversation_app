"""Tests for the TRMNL e-ink screens, device server and drawings."""

import io
import json
import time
import socket
import asyncio
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Iterator
from pathlib import Path

import pytest
from PIL import Image

from reachy_mini_conversation_app import eink_display
from reachy_mini_conversation_app.usage_costs import get_ledger
from reachy_mini_conversation_app.eink_display import (
    WIDTH,
    HEIGHT,
    IDLE_AFTER_S,
    MAX_PNG_BYTES,
    EInkDisplay,
    DrawingError,
    DrawingLimitError,
    encode_png,
    render_home,
    render_text,
    render_word,
    render_drawing,
)


def _jpeg(color: int = 200) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 896), (color, color, color)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeModels:
    def __init__(self, image: bytes | None, delay_s: float = 0.0) -> None:
        self.image = image
        self.delay_s = delay_s
        self.prompts: list[str] = []

    async def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.prompts.append(contents)
        await asyncio.sleep(self.delay_s)
        parts = [SimpleNamespace(inline_data=SimpleNamespace(data=self.image))] if self.image else []
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
            usage_metadata=SimpleNamespace(prompt_token_count=52, candidates_token_count=1407),
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_792_878_594.0

    def __call__(self) -> float:
        return self.now


def _display(tmp_path: Path, models: _FakeModels | None = None, **kwargs: Any) -> EInkDisplay:
    client: Any = SimpleNamespace(aio=SimpleNamespace(models=models)) if models else None
    options: dict[str, Any] = {
        "port": 8090,
        "refresh_s": 60,
        "cache_dir": tmp_path / "drawings",
        "drawings_per_day": 20,
        "image_model": "gemini-3.1-flash-lite-image",
        "client": client,
    }
    options.update(kwargs)
    return EInkDisplay(**options)


def _decoded(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    image.load()
    return image


def test_screens_are_2_bit_grayscale_pngs_in_four_levels() -> None:
    """Every screen is an 800x480 2-bit grayscale PNG small enough for the TRMNL OG."""
    png = encode_png(Image.linear_gradient("L").resize((WIDTH, HEIGHT)))

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[24:26] == bytes([2, 0])  # bit depth 2, grayscale
    image = _decoded(png)
    assert image.size == (WIDTH, HEIGHT)
    assert set(image.convert("L").get_flattened_data()) == {0, 85, 170, 255}


def test_light_grey_fills_become_one_even_grey() -> None:
    """A drawing's light grey (between two levels) lands on one level, not a speckled mix."""
    noisy = Image.new("L", (WIDTH, HEIGHT), 205)
    noisy.putpixel((10, 10), 220)
    assert set(_decoded(encode_png(noisy)).convert("L").get_flattened_data()) == {170}


@pytest.mark.parametrize(
    "png",
    [
        pytest.param(lambda: render_home(), id="home"),
        pytest.param(lambda: render_drawing(Image.open(io.BytesIO(_jpeg())), "Un dinosaurio"), id="drawing"),
        pytest.param(lambda: render_word("la mela", "la MÉ-la", "la manzana", "En italiano"), id="word"),
        pytest.param(
            lambda: render_word("arcobaleno", "ar-co-ba-LÉ-no", "arcoíris", picture=Image.new("L", (300, 300), 170)),
            id="word-with-picture",
        ),
        pytest.param(lambda: render_text(["3 + 4 = 7"]), id="big-line"),
        pytest.param(lambda: render_text(["leche", "plátanos", "¿huevos?"], title="Lista de la compra"), id="list"),
        pytest.param(lambda: render_text(["palabra " * 80], title="Muy largo " * 20), id="overflowing-text"),
    ],
)
def test_every_layout_renders_a_drawable_screen(png: Any) -> None:
    """Each layout, long text included, renders a screen the TRMNL can draw."""
    rendered = png()
    assert len(rendered) <= MAX_PNG_BYTES
    assert _decoded(rendered).size == (WIDTH, HEIGHT)


def test_the_resting_card_returns_after_the_idle_time(tmp_path: Path) -> None:
    """A shown screen stays until 30 minutes pass or someone asks for home, then the resting card is back."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock)
    home = display.current()

    display.show(render_text(["hola"]))
    shown = display.current()
    assert shown != home
    clock.now += IDLE_AFTER_S - 1
    assert display.current() == shown
    clock.now += 1
    assert display.current() == home

    display.show(render_text(["otra vez"]))
    display.show_home()
    assert display.current() == home


def test_each_screen_gets_a_new_firmware_friendly_filename(tmp_path: Path) -> None:
    """The TRMNL caches by file name, so every screen has a new <name>-<id>-<unix time> name of at most 31 chars."""
    display = _display(tmp_path)
    names = {display.current().filename}
    for _ in range(3):
        display.show(render_text(["hola"]))
        names.add(display.current().filename)

    assert len(names) == 4
    for name in names:
        prefix, serial, stamp = name.split("-")
        assert len(name) <= 31 and len(f"{prefix}-{serial}") == 13 and len(stamp) == 10
    assert display.screen_png(display.current().filename) == display.current().png
    assert display.screen_png("reachy-999999-1792878594") is None


def test_a_held_check_in_answers_as_soon_as_a_screen_is_shown(tmp_path: Path) -> None:
    """A check-in with nothing new waits; showing a screen answers it at once instead of after the hold."""
    display = _display(tmp_path)
    assert display.screen_for_check_in(5.0) == display.current()  # first check-in: nothing sent yet

    answered: list[tuple[str, float]] = []

    def check_in() -> None:
        started = time.monotonic()
        answered.append((display.screen_for_check_in(5.0).filename, time.monotonic() - started))

    waiting = threading.Thread(target=check_in)
    waiting.start()
    time.sleep(0.2)
    display.show(render_text(["hola"]))
    waiting.join(timeout=5)

    assert answered and answered[0][0] == display.current().filename
    assert 0.1 < answered[0][1] < 2.0


def test_a_check_in_with_nothing_new_returns_the_same_screen_after_the_hold(tmp_path: Path) -> None:
    """With no change the held check-in gives up after the hold and repeats the screen the device already has."""
    display = _display(tmp_path)
    sent = display.screen_for_check_in(1.0)

    started = time.monotonic()
    assert display.screen_for_check_in(0.3) == sent
    assert time.monotonic() - started >= 0.3


def test_oversized_screens_are_refused(tmp_path: Path) -> None:
    """The TRMNL OG does not draw images over 90 KB, so they never become the screen."""
    with pytest.raises(ValueError):
        _display(tmp_path).show(b"x" * (MAX_PNG_BYTES + 1))


@pytest.mark.asyncio
async def test_drawings_are_made_once_and_then_cached(tmp_path: Path) -> None:
    """A new subject calls Gemini in the fixed style and records the cost; the same subject again does not."""
    models = _FakeModels(_jpeg())
    display = _display(tmp_path, models)

    first = await display.drawing("A Cat")
    again = await display.drawing("a cat")

    assert first.size == again.size == (643, 480)
    assert len(models.prompts) == 1
    assert "A Cat" in models.prompts[0] and "no text" in models.prompts[0]
    record = get_ledger().entries()[-1]
    assert record["feature"] == "display_drawing"
    assert record["tokens"] == {"input_text": 52, "output_image": 1407}
    assert record["cost_usd"] is not None


@pytest.mark.asyncio
async def test_new_drawings_stop_at_the_daily_limit_but_cached_ones_still_show(tmp_path: Path) -> None:
    """After today's drawings are used, new subjects are refused while drawn ones still come from the cache."""
    clock = _Clock()
    display = _display(tmp_path, _FakeModels(_jpeg()), drawings_per_day=1, clock=clock)

    await display.drawing("cat")
    with pytest.raises(DrawingLimitError):
        await display.drawing("dog")
    assert (await display.drawing("cat")).size == (643, 480)

    clock.now += 24 * 3600
    assert (await display.drawing("dog")).size == (643, 480)


@pytest.mark.asyncio
async def test_failed_drawings_raise_drawing_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key, a blocked picture and a slow model each fail with a DrawingError, and nothing is cached."""
    with pytest.raises(DrawingError):
        await _display(tmp_path).drawing("cat")

    with pytest.raises(DrawingError, match="blocked"):
        await _display(tmp_path, _FakeModels(None)).drawing("cat")

    monkeypatch.setattr(eink_display, "DRAWING_TIMEOUT_S", 0.01)
    with pytest.raises(DrawingError, match="too long"):
        await _display(tmp_path, _FakeModels(_jpeg(), delay_s=1.0)).drawing("cat")

    assert not (tmp_path / "drawings" / "cat.png").exists()


@pytest.fixture
def served_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[EInkDisplay]:
    """Run the device server on a free local port for the test."""
    monkeypatch.setattr(eink_display, "CHECK_IN_HOLD_S", 0.2)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    display = _display(tmp_path, port=port)
    display.start()
    yield display
    display.stop()


def _get(display: EInkDisplay, path: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(f"http://127.0.0.1:{display.port}{path}", headers={"ID": "A0:85:E3:27:46:00"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def test_the_server_speaks_the_trmnl_device_api(served_display: EInkDisplay) -> None:
    """Setup, display and image requests answer the way the TRMNL firmware expects."""
    _, body, _ = _get(served_display, "/api/setup")
    setup = json.loads(body)
    assert setup["status"] == 200 and setup["api_key"]

    served_display.show(render_text(["hola"]))
    _, body, _ = _get(served_display, "/api/display")
    reply = json.loads(body)
    assert reply["status"] == 0
    assert reply["refresh_rate"] == 60 and isinstance(reply["refresh_rate"], int)

    _, body, _ = _get(served_display, "/api/display")  # nothing new: answered after the hold, same screen
    assert json.loads(body)["filename"] == reply["filename"]
    assert reply["filename"] == served_display.current().filename
    assert reply["image_url"] == f"http://127.0.0.1:{served_display.port}/screens/{reply['filename']}.png"

    status, png, content_type = _get(served_display, f"/screens/{reply['filename']}.png")
    assert (status, content_type) == (200, "image/png")
    assert png == served_display.current().png


def test_the_server_serves_nothing_but_the_current_screens(served_display: EInkDisplay) -> None:
    """Old or unknown screens and other paths are not found; device logs are accepted."""
    home = served_display.current().filename
    served_display.show(render_text(["hola"]))
    replaced = served_display.current().filename
    served_display.show(render_text(["adiós"]))

    for path in (f"/screens/{replaced}.png", "/screens/nope.png", "/api/other"):
        with pytest.raises(urllib.error.HTTPError) as raised:
            _get(served_display, path)
        assert raised.value.code == 404
    assert _get(served_display, f"/screens/{home}.png")[0] == 200  # the resting card stays available

    log = urllib.request.Request(
        f"http://127.0.0.1:{served_display.port}/api/log", data=b'{"logs": [{"message": "ok"}]}', method="POST"
    )
    with urllib.request.urlopen(log, timeout=5) as response:
        assert response.status == 204
