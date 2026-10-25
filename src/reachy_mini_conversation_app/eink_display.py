"""Calm e-ink screens for a TRMNL display: drawings, web pictures, word cards and short text.

The TRMNL polls a small HTTP server here (its "bring your own server" API: ``/api/setup``,
``/api/display``, ``/api/log``) and draws whatever PNG the reply points to. Screens are 800x480
2-bit grayscale, the four grays the TRMNL OG draws. Text is typed by the renderer, never drawn by
the image model, so accents and spelling are always right. With nothing new for 30 minutes the
display returns to a resting "¡Hola! Soy Reachy" card.
"""

import io
import os
import re
import sys
import time
import zlib
import struct
import asyncio
import hashlib
import logging
import functools
import threading
import subprocess
import unicodedata
from typing import Callable
from pathlib import Path
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageFilter
from google import genai
from google.genai import types
from PIL.PngImagePlugin import PngInfo

from reachy_mini_conversation_app.eink_server import (
    SERVER,
    CURRENT,
    is_awake,
    read_json,
    write_json,
    fingerprint,
    running_pid,
    current_filename,
)
from reachy_mini_conversation_app.usage_costs import get_ledger
from reachy_mini_conversation_app.web_pictures import WebPicture, WebPictures, PictureError


logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 800, 480
MAX_PNG_BYTES = 90_000  # the TRMNL OG does not draw larger images
DRAWING_TIMEOUT_S = 20.0
# Leaves time to draw the subject instead within the conversation's 30 s tool deadline.
PICTURE_TIMEOUT_S = 12.0
SERVER_START_TIMEOUT_S = 5.0
MAX_TEXT_LINES = 6
MAX_STEPS = 4
DRAWING_PROMPT = (
    "A clear, accurate illustration of {subject}, like a drawing in a good illustrated encyclopedia: true "
    "proportions and real details, clean confident black ink outlines, flat fills in light grey and medium grey "
    "on a plain white background. No gradients, photographic shading or texture. No text, letters, numbers or "
    "labels. No cartoon faces or eyes on objects. One subject, centered, filling most of the frame. Calm and "
    "suitable for a family home, nothing gory."
)
# Drawings are cached per style; the 2026-10-24 cartoon style lives on in drawings/ and is no longer used.
DRAWINGS_DIR = "drawings-v2"
_ASSETS = Path(__file__).parent / "static" / "eink"
_GREY, _LIGHT_GREY = 85, 170
_LEVEL_EDGES = (64, 128, 230)


class DrawingError(RuntimeError):
    """A drawing could not be made."""


@functools.lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Atkinson-Bold.ttf" if bold else "Atkinson-Regular.ttf"
    return ImageFont.truetype(str(_ASSETS / name), size)


def _fitted(text: str, width: int, largest: int, smallest: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Return the largest font, down to ``smallest``, that fits ``text`` on one line of ``width``."""
    for size in range(largest, smallest, -2):
        if _font(size, bold).getlength(text) <= width:
            return _font(size, bold)
    return _font(smallest, bold)


def _wrapped(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    """Break ``text`` into at most ``max_lines`` lines of ``width``, ending with "…" when cut."""
    lines: list[str] = []
    for word in text.split():
        if lines and font.getlength(f"{lines[-1]} {word}") <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.;:") + "…"
    return lines


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    *,
    left: int,
    width: int,
    top: int,
    fill: int = 0,
    centered: bool = False,
) -> int:
    """Draw ``lines`` from ``top`` and return the y just below them."""
    line_height = int(font.size * 1.2)
    for line in lines:
        x = left + (width - font.getlength(line)) / 2 if centered else left
        draw.text((x, top), line, font=font, fill=fill)
        top += line_height
    return top


def encode_png(image: Image.Image) -> bytes:
    """Encode an 800x480 image as the 2-bit grayscale PNG the TRMNL OG draws (levels 0, 85, 170, 255)."""
    gray = np.asarray(image.convert("L").resize((WIDTH, HEIGHT)))
    # Only near-white stays white, so a drawing's light-grey fill becomes one even grey, not speckles.
    levels = np.digitize(gray, _LEVEL_EDGES).astype(np.uint8)
    packed = (levels[:, 0::4] << 6) | (levels[:, 1::4] << 4) | (levels[:, 2::4] << 2) | levels[:, 3::4]
    scanlines = b"".join(b"\x00" + row.tobytes() for row in packed)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 2, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines, 9))
        + chunk(b"IEND", b"")
    )


def render_home() -> bytes:
    """Render the resting card: the cartoon Reachy and a greeting."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    robot = Image.open(_ASSETS / "reachy.png")
    canvas.paste(robot, (60, (HEIGHT - robot.height) // 2))
    draw = ImageDraw.Draw(canvas)
    draw.text((440, 150), "¡Hola!", font=_font(84, bold=True), fill=0)
    draw.text((444, 262), "Soy Reachy", font=_font(40, bold=True), fill=0)
    draw.text((444, 318), "Hi, I'm Reachy", font=_font(30), fill=_GREY)
    return encode_png(canvas)


def render_drawing(picture: Image.Image, caption: str = "") -> bytes:
    """Render a drawing filling the screen, with an optional caption below it."""
    return _picture_screen(picture.convert("L"), caption)


def render_photo(picture: Image.Image, caption: str = "", source: str = "") -> bytes:
    """Render a photo dithered to the four grays, with an optional caption and its source site."""
    gray = ImageOps.autocontrast(picture.convert("L"), cutoff=1)
    palette = Image.new("P", (1, 1))
    palette.putpalette([level for level in (0, 85, 170, 255) for _ in range(3)])
    return _picture_screen(gray, caption, source, palette)


def _picture_screen(picture: Image.Image, caption: str, source: str = "", palette: Image.Image | None = None) -> bytes:
    """Lay out a picture over an optional caption; with ``palette``, dither it to those grays."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    picture_height = HEIGHT - 80 if caption else HEIGHT - 20
    fitted = ImageOps.contain(picture, (WIDTH - 20, picture_height))
    if palette is not None:
        fitted = fitted.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    canvas.paste(fitted, ((WIDTH - fitted.width) // 2, 10 + (picture_height - fitted.height) // 2))
    draw = ImageDraw.Draw(canvas)
    if caption:
        font = _fitted(caption, WIDTH - 40, 48, 26)
        _draw_lines(
            draw,
            _wrapped(caption, font, WIDTH - 40, 1),
            font,
            left=20,
            width=WIDTH - 40,
            top=HEIGHT - 66,
            centered=True,
        )
    if source:
        font = _font(16)
        draw.text((WIDTH - 12 - font.getlength(source), HEIGHT - 22), source, font=font, fill=_GREY)
    return encode_png(canvas)


def render_word(
    word: str, pronunciation: str = "", meaning: str = "", header: str = "", picture: Image.Image | None = None
) -> bytes:
    """Render a word card: optional header, the word, how to say it, its meaning and an optional picture."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    if picture is not None:
        fitted = ImageOps.contain(picture.convert("L"), (320, HEIGHT - 40))
        canvas.paste(fitted, (20 + (320 - fitted.width) // 2, (HEIGHT - fitted.height) // 2))
        left, width, centered = 370, WIDTH - 400, False
    else:
        left, width, centered = 40, WIDTH - 80, True
    top = 40
    if header:
        font = _font(30)
        lines = _wrapped(header, font, width, 2)
        top = _draw_lines(draw, lines, font, left=left, width=width, top=top, fill=_GREY, centered=centered) + 10
    word_font = _fitted(word, width, 120, 44)
    top = _draw_lines(
        draw, _wrapped(word, word_font, width, 2), word_font, left=left, width=width, top=top, centered=centered
    )
    if pronunciation:
        font = _font(38)
        top = _draw_lines(
            draw,
            _wrapped(pronunciation, font, width, 1),
            font,
            left=left,
            width=width,
            top=top,
            fill=_GREY,
            centered=centered,
        )
    if meaning:
        top += 16
        rule_left = left + width // 4 if centered else left
        draw.line((rule_left, top, rule_left + (width // 2 if centered else width), top), fill=_LIGHT_GREY, width=3)
        font = _font(44, bold=True)
        _draw_lines(
            draw, _wrapped(meaning, font, width, 2), font, left=left, width=width, top=top + 22, centered=centered
        )
    return encode_png(canvas)


def render_steps(steps: list[tuple[str, Image.Image | None]], title: str = "") -> bytes:
    """Render a visual explanation: up to four numbered panels (a row, or two rows of two) under a title."""
    steps = steps[:MAX_STEPS]
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    top = 10
    if title:
        font = _fitted(title, WIDTH - 40, 40, 26)
        top = _draw_lines(
            draw, _wrapped(title, font, WIDTH - 40, 1), font, left=20, width=WIDTH - 40, top=8, centered=True
        )
    rows = 2 if len(steps) == 4 else 1
    columns = (len(steps) + rows - 1) // rows
    gap = 12
    panel_width = (WIDTH - 20 - gap * (columns - 1)) // columns
    panel_height = (HEIGHT - top - 10 - gap * (rows - 1)) // rows
    caption_size = 26 if rows == 1 else 22
    for index, (caption, picture) in enumerate(steps):
        left = 10 + (index % columns) * (panel_width + gap)
        panel_top = top + (index // columns) * (panel_height + gap)
        draw.rounded_rectangle(
            (left, panel_top, left + panel_width, panel_top + panel_height), radius=12, outline=_LIGHT_GREY, width=2
        )
        font = _fitted(caption, panel_width - 20, caption_size, 16, bold=False)
        lines = _wrapped(caption, font, panel_width - 20, 2 if picture is not None else 5)
        line_height = int(font.size * 1.2)
        if picture is not None:
            caption_top = panel_top + panel_height - 8 - line_height * len(lines)
            box = (panel_width - 16, caption_top - panel_top - 14)
            fitted = ImageOps.contain(picture.convert("L"), box)
            canvas.paste(
                fitted, (left + (panel_width - fitted.width) // 2, panel_top + 8 + (box[1] - fitted.height) // 2)
            )
        else:
            caption_top = panel_top + (panel_height - line_height * len(lines)) // 2
        _draw_lines(draw, lines, font, left=left + 10, width=panel_width - 20, top=caption_top, centered=True)
        badge = 34 if rows == 1 else 28
        draw.ellipse((left + 6, panel_top + 6, left + 6 + badge, panel_top + 6 + badge), fill=0)
        number = _font(badge - 10, bold=True)
        label = str(index + 1)
        draw.text(
            (left + 6 + (badge - number.getlength(label)) / 2, panel_top + 6 + badge * 0.12),
            label,
            font=number,
            fill=255,
        )
    return encode_png(canvas)


def render_text(lines: list[str], title: str = "") -> bytes:
    """Render one big centered line, or a title over a short list."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    if not title and len(lines) == 1:
        font = _fitted(lines[0], WIDTH - 80, 160, 56)
        shown = _wrapped(lines[0], font, WIDTH - 80, 4)
        top = (HEIGHT - int(font.size * 1.2) * len(shown)) // 2
        _draw_lines(draw, shown, font, left=40, width=WIDTH - 80, top=top, centered=True)
        return encode_png(canvas)
    top = 30
    if title:
        font = _font(52, bold=True)
        top = _draw_lines(draw, _wrapped(title, font, WIDTH - 80, 1), font, left=40, width=WIDTH - 80, top=top)
        draw.line((40, top + 4, WIDTH - 40, top + 4), fill=_LIGHT_GREY, width=3)
        top += 24
    font = _font(40)
    bullet = "• " if len(lines) > 1 else ""
    for line in lines[:MAX_TEXT_LINES]:
        top = _draw_lines(draw, _wrapped(bullet + line, font, WIDTH - 80, 1), font, left=40, width=WIDTH - 80, top=top)
    return encode_png(canvas)


def _cache_key(subject: str) -> str:
    """Return a file name for a drawing subject, so "A Cat" and "a cat" share one drawing."""
    folded = unicodedata.normalize("NFKD", subject.casefold()).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:60]
    return slug or hashlib.sha1(subject.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Screen:
    """One screen as the TRMNL fetches it: a unique file name and its PNG."""

    filename: str
    png: bytes


class EInkDisplay:
    """Screens for the TRMNL, drawings and web pictures; the device server runs in its own process."""

    def __init__(
        self,
        *,
        port: int,
        refresh_s: int,
        cache_dir: Path,
        image_model: str,
        client: genai.Client | None = None,
        pictures: WebPictures | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Write the resting screen to the spool folder the device server reads; ``start()`` starts the server."""
        self.port = port
        self.refresh_s = refresh_s
        self.spool = cache_dir / "screen"
        self._cache_dir = cache_dir
        self._image_model = image_model
        self._client = client
        self._pictures = pictures
        self._clock = clock
        self._lock = threading.Lock()
        self.spool.mkdir(parents=True, exist_ok=True)
        # The TRMNL keeps images by file name, <name>-<id>-<unix time>, and deletes a saved image when a
        # newer one shares its <name>-<id>. Shown screens therefore share one id and differ by time, so
        # each replaces the last in the device's small flash instead of piling up for a day. The resting
        # card keeps its name while it is unchanged, so a restart does not redraw it.
        home_png = render_home()
        home = read_json(self.spool / CURRENT).get("home")
        if not isinstance(home, str) or self._read(home) != home_png:
            home = f"reachy-000000-{int(clock())}"
            (self.spool / f"{home}.png").write_bytes(home_png)
            self._update(home=home)

    def show(self, png: bytes) -> None:
        """Make ``png`` the screen the TRMNL fetches at its next check-in."""
        if len(png) > MAX_PNG_BYTES:
            raise ValueError(f"Screen is {len(png)} bytes; the TRMNL draws at most {MAX_PNG_BYTES}.")
        with self._lock:
            last = read_json(self.spool / CURRENT).get("screen")
            last_stamp = int(last.rsplit("-", 1)[1]) if isinstance(last, str) and last[-1:].isdigit() else 0
            stamp = max(int(self._clock()), last_stamp + 1)  # unique even for two screens in a second
            filename = f"reachy-000001-{stamp}"
            (self.spool / f"{filename}.png").write_bytes(png)
            self._update(screen=filename, shown_at=self._clock())

    def show_home(self) -> None:
        """Go back to the resting card."""
        with self._lock:
            self._update(screen=None)

    def current(self) -> Screen:
        """Return the screen to show now: the last one shown, or the resting card once it is old."""
        filename = current_filename(self.spool, self._clock()) or ""
        return Screen(filename, self._read(filename) or b"")

    def awake(self) -> bool:
        """Whether the TRMNL is checking in, so a new screen reaches it within seconds."""
        return is_awake(self.spool, self._clock(), self.refresh_s)

    def _read(self, filename: str) -> bytes | None:
        try:
            return (self.spool / f"{filename}.png").read_bytes()
        except OSError:
            return None

    def _update(self, **changes: object) -> None:
        """Change ``current.json`` and delete screens it no longer names."""
        current = {**read_json(self.spool / CURRENT), **changes}
        write_json(self.spool / CURRENT, current)
        keep = {f"{current.get('screen')}.png", f"{current.get('home')}.png"}
        for old in self.spool.glob("reachy-*.png"):
            if old.name not in keep:
                old.unlink(missing_ok=True)

    async def drawing(self, subject: str) -> Image.Image:
        """Return a drawing of ``subject``: from the cache, else a new one counted against today's limit."""
        path = self._cache_dir / DRAWINGS_DIR / f"{_cache_key(subject)}.png"
        if path.is_file():
            with Image.open(path) as cached:
                return cached.convert("L")
        if self._client is None:
            raise DrawingError("drawings need GEMINI_API_KEY")
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._image_model,
                    contents=DRAWING_PROMPT.format(subject=subject),
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="4:3")
                    ),
                ),
                DRAWING_TIMEOUT_S,
            )
        except asyncio.TimeoutError as error:
            raise DrawingError("the drawing took too long") from error
        usage = response.usage_metadata
        if usage is not None:
            get_ledger().record(
                "display_drawing",
                self._image_model,
                {
                    "input_text": int(usage.prompt_token_count or 0),
                    "output_image": int(usage.candidates_token_count or 0),
                },
            )
        parts = response.candidates[0].content.parts if response.candidates and response.candidates[0].content else []
        data = next(
            (part.inline_data.data for part in parts or [] if part.inline_data and part.inline_data.data), None
        )
        if data is None:
            raise DrawingError("no picture came back (it may have been blocked)")
        with Image.open(io.BytesIO(data)) as made:
            picture = made.convert("L")
        picture.thumbnail((WIDTH, HEIGHT))
        picture = picture.filter(ImageFilter.MedianFilter(3))  # JPEG noise would speckle flat fills
        path.parent.mkdir(parents=True, exist_ok=True)
        picture.save(path)
        return picture

    async def picture(self, subject: str) -> WebPicture:
        """Return a checked web picture of ``subject``: from the cache, else a new search counted against today's."""
        path = self._cache_dir / "pictures" / f"{_cache_key(subject)}.png"
        if path.is_file():
            with Image.open(path) as cached:
                return WebPicture(cached.convert("L"), str(cached.info.get("source", "")))
        if self._pictures is None:
            raise PictureError("picture search is not set up (it needs BRAVE_API_KEY and TYPESAFE_API_KEY)")
        try:
            found = await asyncio.wait_for(self._pictures.find(subject), PICTURE_TIMEOUT_S)
        except asyncio.TimeoutError as error:
            raise PictureError("the picture search took too long") from error
        picture = found.image.convert("L")
        picture.thumbnail((WIDTH, HEIGHT))
        path.parent.mkdir(parents=True, exist_ok=True)
        info = PngInfo()
        info.add_text("source", found.source)
        picture.save(path, pnginfo=info)
        return WebPicture(picture, found.source)

    def start(self) -> None:
        """Start the device server unless one with this code and settings is already running.

        The server keeps running after Reachy stops: the TRMNL sleeps 5 minutes after a check-in fails,
        so it must find the server there across restarts.
        """
        expected = fingerprint(self.port, self.refresh_s)
        pid = running_pid(self.spool)
        if pid is not None and read_json(self.spool / SERVER).get("fingerprint") == expected:
            logger.info("E-ink display server already running on port %d (pid %d)", self.port, pid)
            return
        log_path = self.spool / "server.log"
        with log_path.open("a") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "reachy_mini_conversation_app.eink_server",
                    "--spool",
                    str(self.spool),
                    "--port",
                    str(self.port),
                    "--refresh",
                    str(self.refresh_s),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,  # not stopped with the app, nor by its Ctrl-C
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        deadline = time.monotonic() + SERVER_START_TIMEOUT_S
        while time.monotonic() < deadline and process.poll() is None:
            if read_json(self.spool / SERVER).get("pid") == process.pid:
                logger.info(
                    "E-ink display server started on port %d (pid %d); log: %s", self.port, process.pid, log_path
                )
                return
            time.sleep(0.05)
        logger.error("E-ink display server did not start on port %d; see %s", self.port, log_path)

    def stop(self) -> None:
        """Leave the device server running, so the TRMNL keeps checking in while Reachy restarts."""
