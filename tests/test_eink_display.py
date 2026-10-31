"""Tests for the TRMNL e-ink screens, device server and drawings."""

import io
import os
import json
import time
import base64
import signal
import socket
import asyncio
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Iterator
from pathlib import Path

import httpx
import openai
import pytest
from PIL import Image, ImageDraw

from reachy_mini_conversation_app import eink_server, eink_display
from reachy_mini_conversation_app.eink_server import SERVER, AWAKE_GAP_S, IDLE_AFTER_S, DeviceServer, read_json
from reachy_mini_conversation_app.usage_costs import get_ledger
from reachy_mini_conversation_app.eink_display import (
    WIDTH,
    HEIGHT,
    LESSONS_DIR,
    DRAWINGS_DIR,
    MAX_PNG_BYTES,
    LESSON_CAPTION_MODEL,
    EInkDisplay,
    DrawingError,
    encode_png,
    render_home,
    render_text,
    render_word,
    render_photo,
    render_steps,
    render_lesson,
    render_drawing,
    render_writing,
    render_game_paused,
    render_game_prompt,
    render_now_playing,
)
from reachy_mini_conversation_app.web_pictures import WebPicture, PictureError


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


def _display(tmp_path: Path, models: Any = None, **kwargs: Any) -> EInkDisplay:
    client: Any = SimpleNamespace(aio=SimpleNamespace(models=models)) if models else None
    options: dict[str, Any] = {
        "port": 8090,
        "refresh_s": 60,
        "cache_dir": tmp_path,
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
        pytest.param(
            lambda: render_photo(Image.linear_gradient("L").resize((500, 375)), "La Gioconda", "wikipedia.org"),
            id="photo",
        ),
        pytest.param(lambda: render_text(["3 + 4 = 7"]), id="big-line"),
        pytest.param(lambda: render_text(["leche", "plátanos", "¿huevos?"], title="Lista de la compra"), id="list"),
        pytest.param(lambda: render_text(["palabra " * 80], title="Muy largo " * 20), id="overflowing-text"),
        *(
            pytest.param(
                lambda count=count: render_steps(
                    [(f"Paso {n}: algo pasa", Image.new("L", (400, 300), 170)) for n in range(count)],
                    "¿Cómo crece una planta?",
                ),
                id=f"steps-{count}",
            )
            for count in (2, 3, 4)
        ),
        pytest.param(
            lambda: render_steps([("muy largo " * 30, None), ("sin dibujo", None), ("x" * 200, None)], "Título " * 20),
            id="steps-overflowing-without-pictures",
        ),
        pytest.param(
            lambda: render_lesson(
                "Cómo dibujar una jirafa",
                [(f"Paso {n}: un óvalo grande para el cuerpo", Image.new("L", (300, 260), 170)) for n in range(4)],
            ),
            id="lesson",
        ),
        pytest.param(
            lambda: render_lesson(
                "Título " * 20, [(caption, Image.new("L", (300, 260), 170)) for caption in ("", "muy largo " * 30) * 3]
            ),
            id="lesson-overflowing-and-without-captions",
        ),
        *(
            pytest.param(lambda letters=letters, step=step: render_writing(list(letters), step), id=f"writing-{name}")
            for name, letters, step in (
                ("first", "PAPÁ", 0),
                ("space", "TE QUIERO", 2),
                ("longest", "ÑANDÚ ROJO 12", 12),
                ("done", "TE QUIERO", 9),
            )
        ),
        pytest.param(lambda: render_game_prompt("ROJO", 2, 5, "Busca algo…"), id="game-prompt"),
        pytest.param(lambda: render_game_prompt("AMARILLO", 5, 5), id="game-prompt-all-stars"),
        pytest.param(lambda: render_game_paused("a los colores", "es"), id="game-paused"),
        pytest.param(lambda: render_game_paused("color hunt", "en"), id="game-paused-english"),
        pytest.param(
            lambda: render_now_playing(
                Image.effect_noise((640, 640), 80),
                "Bohemian Rhapsody - Remastered 2011",
                "Queen",
                "A Night At The Opera",
            ),
            id="now-playing",
        ),
        pytest.param(
            lambda: render_now_playing(None, "Episodio " * 30, "Artista " * 10, "Álbum " * 10),
            id="now-playing-without-art-overflowing",
        ),
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
    """Names are <name>-<id>-<unix time>, at most 31 chars, new for every screen, even several in one second."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock)
    names = {display.current().filename}
    for _ in range(3):
        display.show(render_text(["hola"]))
        names.add(display.current().filename)

    assert len(names) == 4
    for name in names:
        prefix, serial, stamp = name.split("-")
        assert len(name) <= 31 and len(f"{prefix}-{serial}") == 13 and len(stamp) == 10
    server = _server(display, clock)
    assert server.screen_png(display.current().filename) == display.current().png
    assert server.screen_png("reachy-999999-1792878594") is None
    server.server_close()


def test_shown_screens_share_one_id_so_the_device_keeps_only_the_latest(tmp_path: Path) -> None:
    """The TRMNL deletes a saved image when a newer one shares its <name>-<id>; the resting card has its own."""
    display = _display(tmp_path)
    home_id = display.current().filename.rsplit("-", 1)[0]
    shown_ids = set()
    for words in ("uno", "dos", "tres"):
        display.show(render_text([words]))
        shown_ids.add(display.current().filename.rsplit("-", 1)[0])

    assert len(shown_ids) == 1 and home_id not in shown_ids


def _server(display: EInkDisplay, clock: Any = time.time) -> DeviceServer:
    """Return a device server on a free local port, reading ``display``'s spool folder."""
    return DeviceServer(display.spool, 0, display.refresh_s, host="127.0.0.1", clock=clock)


def test_a_held_check_in_answers_as_soon_as_a_screen_is_shown(tmp_path: Path) -> None:
    """A check-in with nothing new waits; showing a screen answers it at once instead of after the hold."""
    display = _display(tmp_path)
    server = _server(display)
    assert server.screen_for_check_in(5.0) == display.current().filename  # first check-in: nothing sent yet

    answered: list[tuple[str | None, float]] = []

    def check_in() -> None:
        started = time.monotonic()
        answered.append((server.screen_for_check_in(5.0), time.monotonic() - started))

    waiting = threading.Thread(target=check_in)
    waiting.start()
    time.sleep(0.2)
    display.show(render_text(["hola"]))
    waiting.join(timeout=5)
    server.server_close()

    assert answered and answered[0][0] == display.current().filename
    assert 0.1 < answered[0][1] < 2.0


def test_a_check_in_with_nothing_new_returns_the_same_screen_after_the_hold(tmp_path: Path) -> None:
    """With no change the held check-in gives up after the hold and repeats the screen the device already has."""
    server = _server(_display(tmp_path))
    sent = server.screen_for_check_in(1.0)

    started = time.monotonic()
    assert server.screen_for_check_in(0.3) == sent
    assert time.monotonic() - started >= 0.3
    server.server_close()


def test_the_screen_counts_as_awake_only_while_it_keeps_checking_in(tmp_path: Path) -> None:
    """Never checked in, or silent for longer than a normal gap, means a new screen will not arrive soon."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock, refresh_s=1)
    assert not display.awake()

    server = _server(display, clock)
    server.screen_for_check_in(0.0)
    server.server_close()
    clock.now += AWAKE_GAP_S
    assert display.awake()
    clock.now += 2
    assert not display.awake()


def test_a_held_check_in_counts_as_awake(tmp_path: Path) -> None:
    """While the device waits on a held check-in it is awake, however long ago the last one ended."""
    display = _display(tmp_path)
    server = _server(display)
    holding = threading.Thread(target=server.screen_for_check_in, args=(1.0,))
    holding.start()
    time.sleep(0.2)
    assert display.awake()
    holding.join()
    server.server_close()


def test_a_restarted_app_keeps_the_screen_and_the_resting_card_name(tmp_path: Path) -> None:
    """The screen lives in the spool folder, so restarting Reachy neither blanks it nor makes the TRMNL redraw."""
    first = _display(tmp_path)
    home = first.current().filename
    first.show(render_text(["hola"]))
    shown = first.current()

    again = _display(tmp_path)
    assert again.current() == shown
    again.show_home()
    assert again.current().filename == home
    assert sorted(path.name for path in again.spool.glob("reachy-*.png")) == [f"{home}.png"]


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
    assert "A Cat" in models.prompts[0] and "No text" in models.prompts[0]
    assert "No cartoon faces" in models.prompts[0] and "bold thick black outlines" in models.prompts[0]
    assert (tmp_path / DRAWINGS_DIR / "a-cat.png").is_file()  # a new style never reuses old drawings
    with Image.open(tmp_path / DRAWINGS_DIR / "a-cat.png") as cached:
        assert cached.info["subject"] == "A Cat"  # the gallery's title; the file name is cut short
    record = get_ledger().entries()[-1]
    assert record["feature"] == "display_drawing"
    assert record["tokens"] == {"input_text": 52, "output_image": 1407}
    assert record["cost_usd"] is not None


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

    assert not list(tmp_path.rglob("cat.png"))


@pytest.fixture
def served_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[EInkDisplay]:
    """Run the device server on a free local port for the test."""
    monkeypatch.setattr(eink_server, "CHECK_IN_HOLD_S", 0.2)
    display = _display(tmp_path)
    server = _server(display)
    display.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield display
    server.shutdown()
    server.server_close()


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


def test_after_a_reported_failure_the_screen_is_sent_under_a_new_name(served_display: EInkDisplay) -> None:
    """A device log makes the next check-in send the same screen as an alias (redrawn), kept until it changes."""
    first = json.loads(_get(served_display, "/api/display")[1])["filename"]
    log = urllib.request.Request(
        f"http://127.0.0.1:{served_display.port}/api/log", data=b'{"logs": [{"level": "error"}]}', method="POST"
    )
    urllib.request.urlopen(log, timeout=5).close()
    alias = json.loads(_get(served_display, "/api/display")[1])
    assert alias["filename"] == f"{first}~1" and alias["image_url"].endswith(f"/screens/{first}~1.png")
    assert _get(served_display, f"/screens/{first}~1.png")[1] == served_display.current().png
    assert json.loads(_get(served_display, "/api/display")[1])["filename"] == f"{first}~1"  # no second redraw
    served_display.show(render_text(["hola"]))
    assert json.loads(_get(served_display, "/api/display")[1])["filename"] == served_display.current().filename


def test_photos_are_dithered_so_their_tones_survive_four_grays() -> None:
    """A smooth photo keeps its gradient as a dither of all four levels rather than four flat bands."""
    image = _decoded(render_photo(Image.linear_gradient("L").resize((800, 400)))).convert("L")
    band = [image.getpixel((x, 200)) for x in range(300, 340)]
    assert set(image.get_flattened_data()) == {0, 85, 170, 255}
    assert len(set(band)) > 1 and len({band[i] != band[i + 1] for i in range(len(band) - 1)}) == 2


class _FakePictures:
    def __init__(self, error: Exception | None = None, delay_s: float = 0.0) -> None:
        self.error = error
        self.delay_s = delay_s
        self.subjects: list[str] = []

    async def find(self, subject: str) -> WebPicture:
        self.subjects.append(subject)
        await asyncio.sleep(self.delay_s)
        if self.error:
            raise self.error
        return WebPicture(Image.new("RGB", (500, 375), (90, 90, 90)), "wikipedia.org")


@pytest.mark.asyncio
async def test_web_pictures_are_cached_with_their_source(tmp_path: Path) -> None:
    """A found picture is kept with the site it came from; asking again does not search again."""
    finder = _FakePictures()
    display = _display(tmp_path, pictures=finder)

    first = await display.picture("Mona Lisa")
    again = await display.picture("mona lisa")

    assert finder.subjects == ["Mona Lisa"]
    assert first.source == again.source == "wikipedia.org"
    assert again.image.size == (500, 375)


@pytest.mark.asyncio
async def test_picture_failures_raise_picture_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without keys, after a failed search, or when it is too slow, there is no picture."""
    with pytest.raises(PictureError, match="BRAVE_API_KEY"):
        await _display(tmp_path).picture("cat")
    with pytest.raises(PictureError, match="nothing"):
        await _display(tmp_path, pictures=_FakePictures(PictureError("nothing"))).picture("cat")
    monkeypatch.setattr(eink_display, "PICTURE_TIMEOUT_S", 0.01)
    with pytest.raises(PictureError, match="too long"):
        await _display(tmp_path, pictures=_FakePictures(delay_s=1.0)).picture("cat")
    assert not list(tmp_path.rglob("cat.png"))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_server(display: EInkDisplay, not_pid: int | None = None) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = read_json(display.spool / SERVER).get("pid")
        if isinstance(pid, int) and pid != not_pid:
            return pid
        time.sleep(0.05)
    raise AssertionError("the device server did not start")


def test_the_device_server_outlives_the_app_and_is_replaced_only_when_it_changes(tmp_path: Path) -> None:
    """start() launches a separate server, a restarted app reuses it, and new settings replace it in place."""
    display = _display(tmp_path, port=_free_port())
    pids: list[int] = []
    try:
        display.start()
        pids.append(_wait_for_server(display))
        display.stop()
        assert _get(display, "/api/setup")[0] == 200  # still answering with the app stopped

        _display(tmp_path, port=display.port).start()
        assert read_json(display.spool / SERVER)["pid"] == pids[0]

        replacement = _display(tmp_path, port=display.port, refresh_s=5)
        replacement.start()
        pids.append(_wait_for_server(replacement, not_pid=pids[0]))
        assert json.loads(_get(replacement, "/api/display")[1])["refresh_rate"] == 5
        assert _exited(pids[0]), "the replaced server is still running"
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            _exited(pid)


def _exited(pid: int, timeout_s: float = 5.0) -> bool:
    """Wait for a server this test started to exit, reaping it."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return True
        except ChildProcessError:
            return True
        time.sleep(0.05)
    return False


def test_steps_show_numbered_panels_in_rows_of_up_to_three() -> None:
    """Two or three steps sit in one row; four make two rows of two, each panel numbered."""

    def panel_columns(count: int) -> int:
        image = _decoded(render_steps([("x", None)] * count)).convert("L")
        row = [image.getpixel((x, 19)) for x in range(WIDTH)]  # across the tops of the first row's badges
        return sum(1 for x in range(1, WIDTH) if row[x] < 128 <= row[x - 1])

    assert [panel_columns(count) for count in (2, 3, 4)] == [2, 3, 2]
    assert render_steps([("x", None)] * 6) == render_steps([("x", None)] * 4)  # at most four


def test_screens_remember_who_put_them_up(tmp_path: Path) -> None:
    """The now-playing watcher needs to know whether a screen was asked for or is its own music card."""
    display = _display(tmp_path)
    assert display.owner() == "home"
    display.show(render_text(["hola"]))
    assert display.owner() == "requested"
    display.show(render_text(["♪"]), owner="music")
    assert display.owner() == "music"
    display.show_home()
    assert display.owner() == "home"


def test_keep_alive_holds_only_the_music_card(tmp_path: Path) -> None:
    """keep_alive stops the music card idling away, but never keeps a requested screen past its time."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock)
    display.show(render_text(["canción"]), owner="music")
    card = display.current()
    clock.now += IDLE_AFTER_S - 1
    display.keep_alive()
    clock.now += IDLE_AFTER_S - 1
    assert display.current() == card

    display.show(render_text(["hola"]))
    clock.now += IDLE_AFTER_S - 1
    display.keep_alive()
    clock.now += 1
    assert display.owner() == "home"


def test_a_camera_photo_leaves_the_screen_and_the_disk_after_its_time(tmp_path: Path) -> None:
    """After 10 minutes the photo is no longer shown or served, and the next check-in deletes its file."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock)
    home = display.current()
    display.show(render_text(["foto"]), expires_after_s=600)
    photo = display.current()
    server = _server(display, clock)
    assert server.screen_png(photo.filename) == photo.png

    clock.now += 600
    assert display.current() == home
    assert server.screen_png(photo.filename) is None
    server.screen_for_check_in(0.0)
    assert not (display.spool / f"{photo.filename}.png").exists()
    server.server_close()


def test_only_private_screens_are_deleted(tmp_path: Path) -> None:
    """A normal screen past its idle time keeps its file until the next screen replaces it."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock)
    display.show(render_text(["hola"]))
    shown = display.current()
    server = _server(display, clock)

    clock.now += IDLE_AFTER_S
    server.screen_for_check_in(0.0)
    assert (display.spool / f"{shown.filename}.png").exists()
    server.server_close()


@pytest.mark.asyncio
async def test_photo_edits_record_their_cost_and_are_never_cached(tmp_path: Path) -> None:
    """An edit sends the photo with the change, records its tokens, and leaves no file behind."""
    models = _FakeModels(_jpeg())
    display = _display(tmp_path, models)

    edited = await display.edit_photo(_jpeg(90), "make him a pirate")

    assert edited.size == (643, 480)
    assert "make him a pirate" in models.prompts[0][0]
    assert get_ledger().entries()[-1]["feature"] == "display_photo_edit"
    assert not (tmp_path / DRAWINGS_DIR).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "reason"), [(402, "out of credits"), (500, "did not answer")])
async def test_image_service_errors_become_drawing_errors(tmp_path: Path, code: int, reason: str) -> None:
    """No credits or a server error is a DrawingError Reachy can explain, not a crash in the tool."""
    from google.genai import errors as genai_errors

    class FailingModels(_FakeModels):
        async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
            raise genai_errors.APIError(code, {"error": {"code": code, "message": "sentinel", "status": "X"}})

    display = _display(tmp_path, FailingModels(None))
    with pytest.raises(DrawingError, match=reason):
        await display.edit_photo(_jpeg(), "make him a pirate")
    with pytest.raises(DrawingError, match=reason):
        await display.drawing("cat")


class _FakeImages:
    """The two OpenAI Images API calls drawings use, answering with ``image`` (or raising ``error``)."""

    def __init__(self, image: bytes | None, error: Exception | None = None) -> None:
        self.image = image
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def generate(self, **kwargs: Any) -> Any:
        return self._answer("generate", kwargs)

    async def edit(self, **kwargs: Any) -> Any:
        return self._answer("edit", kwargs)

    def _answer(self, call: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((call, kwargs))
        if self.error is not None:
            raise self.error
        data = [SimpleNamespace(b64_json=base64.b64encode(self.image).decode())] if self.image else []
        usage = SimpleNamespace(
            input_tokens=959,
            input_tokens_details=SimpleNamespace(text_tokens=191, image_tokens=768),
            output_tokens=301,
        )
        return SimpleNamespace(data=data, usage=usage)


def _openai_display(tmp_path: Path, images: _FakeImages | None) -> EInkDisplay:
    client: Any = SimpleNamespace(images=images) if images else None
    return _display(tmp_path, image_model="gpt-image-2.5-flare", openai_client=client)


@pytest.mark.asyncio
async def test_gpt_image_models_draw_through_openai(tmp_path: Path) -> None:
    """A gpt-image model draws a 4:3 medium-quality picture, records its tokens and caches it like Gemini's."""
    images = _FakeImages(_jpeg())
    display = _openai_display(tmp_path, images)

    first = await display.drawing("A Cat")
    again = await display.drawing("a cat")

    assert first.size == again.size == (643, 480)
    assert len(images.calls) == 1
    call, kwargs = images.calls[0]
    assert call == "generate" and "A Cat" in kwargs["prompt"]
    assert kwargs["model"] == "gpt-image-2.5-flare"
    assert (kwargs["size"], kwargs["quality"]) == ("1024x768", "medium")
    record = get_ledger().entries()[-1]
    assert record["feature"] == "display_drawing"
    assert record["tokens"] == {"input_text": 191, "input_image": 768, "output_image": 301}
    assert record["cost_usd"] is not None


@pytest.mark.asyncio
async def test_gpt_image_photo_edits_send_the_photo(tmp_path: Path) -> None:
    """An edit goes to the Images edit call with the JPEG and the change, and is not cached."""
    images = _FakeImages(_jpeg())
    photo = _jpeg(90)

    edited = await _openai_display(tmp_path, images).edit_photo(photo, "make him a pirate")

    assert edited.size == (643, 480)
    call, kwargs = images.calls[0]
    assert call == "edit" and "make him a pirate" in kwargs["prompt"]
    assert kwargs["image"] == ("photo.jpg", photo, "image/jpeg")
    assert get_ledger().entries()[-1]["feature"] == "display_photo_edit"
    assert not (tmp_path / DRAWINGS_DIR).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "reason"), [(402, "out of credits"), (429, "busy"), (400, "blocked"), (500, "did not answer")]
)
async def test_openai_image_errors_become_drawing_errors(tmp_path: Path, code: int, reason: str) -> None:
    """OpenAI's credit, rate, moderation and server errors are DrawingErrors Reachy can explain."""
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    error = openai.APIStatusError("sentinel", response=httpx.Response(code, request=request), body=None)
    display = _openai_display(tmp_path, _FakeImages(None, error))

    with pytest.raises(DrawingError, match=reason):
        await display.drawing("cat")
    with pytest.raises(DrawingError, match=reason):
        await display.edit_photo(_jpeg(), "make him a pirate")
    assert not list(tmp_path.rglob("cat.png"))


@pytest.mark.asyncio
async def test_gpt_image_models_need_the_openai_key_and_a_picture(tmp_path: Path) -> None:
    """Without an OpenAI client the error names the key; an answer with no image is a blocked drawing."""
    with pytest.raises(DrawingError, match="OPENAI_API_KEY"):
        await _openai_display(tmp_path, None).drawing("cat")
    with pytest.raises(DrawingError, match="blocked"):
        await _openai_display(tmp_path, _FakeImages(None)).drawing("cat")


def _lesson_sheet(stray_grey: bool = False) -> bytes:
    """Return a 2x2 lesson sheet: one more line per step, the new one black and the earlier ones grey."""
    sheet = Image.new("L", (1024, 768), 255)
    draw = ImageDraw.Draw(sheet)
    for step in range(4):
        left, top = (step % 2) * 512, (step // 2) * 384
        for line in range(step + 1):
            x = left + 150 + 50 * line
            draw.line((x, top + 100, x, top + 300), fill=0 if line == step else 170, width=8)
    if stray_grey:
        draw.line((400, 100, 400, 300), fill=170, width=8)  # a later step's line, faintly, in step 1
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeLessonModels:
    """Gemini drawing a lesson sheet and, as the caption model, answering ``caption_answer``."""

    def __init__(self, sheet: bytes, caption_answer: str, caption_delays_s: list[float] | None = None) -> None:
        self.sheet = sheet
        self.caption_answer = caption_answer
        self.caption_delays_s = caption_delays_s or []
        self.calls: list[tuple[str, Any]] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append((model, contents))
        if model == LESSON_CAPTION_MODEL:
            await asyncio.sleep(self.caption_delays_s.pop(0) if self.caption_delays_s else 0.0)
            usage = SimpleNamespace(prompt_token_count=1267, candidates_token_count=72, thoughts_token_count=0)
            return SimpleNamespace(text=self.caption_answer, usage_metadata=usage)
        part = SimpleNamespace(inline_data=SimpleNamespace(data=self.sheet))
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
            usage_metadata=SimpleNamespace(prompt_token_count=212, candidates_token_count=1290),
        )


_CAPTIONS = json.dumps({"captions": ["Un óvalo grande.", "Un cuello largo", "Cuatro patas", "La carita"]})


@pytest.mark.asyncio
async def test_a_lesson_is_cut_from_one_sheet_and_kept_with_its_captions(tmp_path: Path) -> None:
    """One sheet becomes four equal panels with captions read from it; a repeat asks nothing, a new title only captions."""
    models = _FakeLessonModels(_lesson_sheet(), _CAPTIONS)
    display = _display(tmp_path, models)

    panels = await display.lesson("A Giraffe", "Cómo dibujar una jirafa")

    assert [caption for caption, _ in panels] == ["Un óvalo grande", "Un cuello largo", "Cuatro patas", "La carita"]
    sizes = {picture.size for _, picture in panels}
    assert len(sizes) == 1 and sizes.pop() < (320, 240)  # trimmed to the drawing, the same box in every panel
    grey = [sum(1 for value in picture.get_flattened_data() if 110 <= value <= 215) for _, picture in panels]
    assert grey[0] < grey[1] < grey[2] < grey[3]  # each step keeps the earlier lines, in grey
    (sheet_model, sheet_prompt), (caption_model, caption_contents) = models.calls
    assert sheet_model == "gemini-3.1-flash-lite-image" and caption_model == LESSON_CAPTION_MODEL
    assert "A Giraffe" in sheet_prompt and "never draw a face on objects" in sheet_prompt
    assert "Cómo dibujar una jirafa" in caption_contents[0]
    assert [entry["feature"] for entry in get_ledger().entries()[-2:]] == ["display_lesson", "display_lesson_captions"]
    assert get_ledger().entries()[-1]["cost_usd"] is not None

    again = await display.lesson("a giraffe", "Cómo dibujar una jirafa")
    assert len(models.calls) == 2
    assert [caption for caption, _ in again] == [caption for caption, _ in panels]

    await display.lesson("a giraffe", "How to draw a giraffe")
    assert [model for model, _ in models.calls[2:]] == [LESSON_CAPTION_MODEL]
    assert (tmp_path / LESSONS_DIR / "a-giraffe.png").is_file()


@pytest.mark.asyncio
async def test_a_lesson_with_later_lines_in_its_first_step_is_shown_but_not_kept(tmp_path: Path) -> None:
    """Grey lines in step 1 mean the sheet came out wrong: it is still returned, and the next ask draws anew."""
    models = _FakeLessonModels(_lesson_sheet(stray_grey=True), _CAPTIONS)
    display = _display(tmp_path, models)

    assert len(await display.lesson("a butterfly", "Cómo dibujar una mariposa")) == 4
    await display.lesson("a butterfly", "Cómo dibujar una mariposa")

    assert [model for model, _ in models.calls].count("gemini-3.1-flash-lite-image") == 2
    assert not (tmp_path / LESSONS_DIR).exists()


@pytest.mark.asyncio
async def test_a_lesson_without_captions_still_shows_its_panels(tmp_path: Path) -> None:
    """An unusable caption answer, or no Gemini client, leaves the panels without captions; none are kept."""
    models = _FakeLessonModels(_lesson_sheet(), "not json")
    display = _display(tmp_path, models)

    assert [caption for caption, _ in await display.lesson("a cat", "Cómo dibujar un gato")] == [""] * 4
    await display.lesson("a cat", "Cómo dibujar un gato")
    assert [model for model, _ in models.calls].count(LESSON_CAPTION_MODEL) == 2

    openai_only = _openai_display(tmp_path / "openai", _FakeImages(_lesson_sheet()))
    assert [caption for caption, _ in await openai_only.lesson("a cat", "Cómo dibujar un gato")] == [""] * 4


@pytest.mark.asyncio
async def test_a_slow_caption_answer_gets_a_second_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow caption answer starts a second request and the first answer wins; two slow ones leave no captions."""
    monkeypatch.setattr(eink_display, "CAPTION_HEDGE_S", 0.05)
    monkeypatch.setattr(eink_display, "CAPTION_TIMEOUT_S", 0.2)
    models = _FakeLessonModels(_lesson_sheet(), _CAPTIONS, caption_delays_s=[5.0, 0.0])

    panels = await _display(tmp_path, models).lesson("a cat", "Cómo dibujar un gato")

    assert panels[0][0] == "Un óvalo grande"
    assert [model for model, _ in models.calls].count(LESSON_CAPTION_MODEL) == 2

    slow = _FakeLessonModels(_lesson_sheet(), _CAPTIONS, caption_delays_s=[5.0, 5.0])
    panels = await _display(tmp_path / "slow", slow).lesson("a cat", "Cómo dibujar un gato")
    assert [caption for caption, _ in panels] == [""] * 4


def test_a_resting_card_replaces_the_home_screen_until_it_is_cleared(tmp_path: Path) -> None:
    """The privacy card is what every screen idles back to while privacy is on; clearing it brings Reachy's back."""
    clock = _Clock()
    display = _display(tmp_path, clock=clock)
    home = display.current()
    privacy_card = render_text(["Privacy on"])

    display.set_resting_card(privacy_card)
    assert display.current().png == privacy_card and display.owner() == "home"
    display.show(render_text(["hola"]))
    clock.now += IDLE_AFTER_S + 1
    assert display.current().png == privacy_card

    display.set_resting_card(None)
    assert display.current().png == home.png
    assert not (display.spool / f"{home.filename}.png").exists() or display.current().filename == home.filename
    assert len(list(display.spool.glob("reachy-000000-*.png"))) == 1


def test_a_check_in_keeps_the_battery_voltage(tmp_path: Path) -> None:
    """The admin app's status page reads the last reported battery voltage from the device file."""
    server = _server(_display(tmp_path))
    server.record_battery("4.12345")
    server.record_battery(None)
    server.record_battery("not a number")
    server.server_close()
    assert read_json(server.spool / eink_server.DEVICE)["battery_v"] == 4.12
