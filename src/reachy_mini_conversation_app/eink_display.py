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
import base64
import struct
import asyncio
import hashlib
import logging
import functools
import threading
import subprocess
import unicodedata
from typing import Any, Literal, Callable
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import openai
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageFilter
from google import genai
from openai import AsyncOpenAI
from google.genai import types
from google.genai import errors as genai_errors
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
# Models named gpt-image-* are OpenAI's; any other DISPLAY_IMAGE_MODEL is a Gemini image model. Medium
# quality at 4:3 took 10-12 s and 301 image tokens for a drawing or an edit with gpt-image-2.5-flare; low
# left blotchy fills (checked 2026-10-27).
OPENAI_IMAGE_PREFIX = "gpt-image"
# Typed Any: openai 2.28 lists only the older fixed sizes, though the API takes any multiple-of-16 size.
OPENAI_IMAGE_SIZE: Any = "1024x768"
OPENAI_IMAGE_QUALITY: Literal["medium"] = "medium"
# Leaves time to draw the subject instead within the conversation's 30 s tool deadline.
PICTURE_TIMEOUT_S = 12.0
SERVER_START_TIMEOUT_S = 5.0
MAX_TEXT_LINES = 6
MAX_STEPS = 4
# Accurate shapes but bold lines and big flat areas: thin lines break into dashes in four grays, and busy
# scenes turn into dark noisy blobs on the TRMNL (seen 2026-10-25 with a detailed encyclopedia style).
DRAWING_PROMPT = (
    "A clean, simple illustration of {subject}, like a modern flat illustration in a children's science book: "
    "accurate shapes and proportions with only the main details, bold thick black outlines, large flat areas of "
    "light grey and medium grey on a plain white background. No small details, thin lines, hatching, texture, "
    "gradients or shading. No text, letters, numbers or labels. No cartoon faces or eyes on objects. One subject, "
    "centered, filling most of the frame. Calm and suitable for a family home, nothing gory."
)
# Camera photo edits ("make me a pirate"): clearly them (their real features, a costume for creatures), on white,
# with bold outlines and flat greys. Naming features like glasses made the model add them, so it keeps them "as in
# the photo, adding nothing" (checked 2026-10-25 on synthetic photos).
PHOTO_EDIT_PROMPT = (
    "Make a new picture from this camera photo: {edit}. Make that change clear and obvious, but keep each person's "
    "likeness so the family recognises them at once: their real face shape, eyes, eyebrows, nose, mouth, hairstyle, "
    "facial hair, age and expression exactly as in the photo, adding nothing they do not have. When the change turns "
    "them into an animal, creature or character, show it as a full costume with their own face showing, so it is "
    "clearly them. Draw faces with a little more detail than the rest. Leave out the room and clutter: a plain white "
    "background. Draw it as a clean illustration with bold black outlines and flat areas of light grey and medium "
    "grey; no hatching, texture, gradients or shading. No text, letters, numbers or labels. Calm and suitable for a "
    "family home, nothing gory."
)
# Drawings are cached per style, so a new style never shows old drawings: drawings/ holds the 2026-10-24
# cartoons and drawings-v2/ the too-detailed encyclopedia style; neither is used any more.
DRAWINGS_DIR = "drawings-v3"
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


def _draw_note(draw: ImageDraw.ImageDraw, left: int, top: int, size: int, fill: int) -> None:
    """Draw a music note in a ``size`` square (the bundled font has no note glyph)."""
    head = size * 0.42
    stem = max(2, size // 12)
    head_top = top + size - head
    draw.ellipse((left, head_top, left + head * 1.3, top + size), fill=fill)
    x = left + head * 1.3 - stem
    draw.rectangle((x, top, x + stem, head_top + head / 2), fill=fill)
    draw.polygon(
        [(x, top), (x + size * 0.45, top + size * 0.22), (x + size * 0.45, top + size * 0.4), (x, top + size * 0.18)],
        fill=fill,
    )


def render_now_playing(art: Image.Image | None, title: str, artist: str = "", album: str = "") -> bytes:
    """Render the music card: album art on the left, a small note, the title, the artist and the album."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    side = HEIGHT - 40
    if art is not None:
        gray = ImageOps.autocontrast(ImageOps.fit(art.convert("L"), (side, side)), cutoff=1)
        palette = Image.new("P", (1, 1))
        palette.putpalette([level for level in (0, 85, 170, 255) for _ in range(3)])
        dithered = gray.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG).convert("L")
        canvas.paste(dithered, (20, 20))
    else:
        draw.rounded_rectangle((20, 20, 20 + side, 20 + side), radius=16, fill=_LIGHT_GREY)
        _draw_note(draw, 20 + side // 2 - 70, 20 + side // 2 - 80, 160, _GREY)
    left = 20 + side + 30
    width = WIDTH - left - 24
    blocks: list[tuple[list[str], ImageFont.FreeTypeFont, int]] = []
    font = _fitted(title, width, 46, 30)
    blocks.append((_wrapped(title, font, width, 4), font, 0))
    if artist:
        font = _font(30)
        blocks.append((_wrapped(artist, font, width, 2), font, 0))
    if album:
        font = _font(24)
        blocks.append((_wrapped(album, font, width, 2), font, _GREY))
    gaps = 14
    height = 44 + sum(int(f.size * 1.2) * len(lines) + gaps for lines, f, _ in blocks)
    top = max(24, (HEIGHT - height) // 2)
    _draw_note(draw, left, top, 30, _GREY)
    top += 44
    for lines, font, fill in blocks:
        top = _draw_lines(draw, lines, font, left=left, width=width, top=top, fill=fill) + gaps
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
        openai_client: AsyncOpenAI | None = None,
        pictures: WebPictures | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Write the resting screen to the spool folder the device server reads; ``start()`` starts the server.

        ``client`` draws with a Gemini image model and ``openai_client`` with a gpt-image model; only the one
        matching ``image_model`` is used.
        """
        self.port = port
        self.refresh_s = refresh_s
        self.spool = cache_dir / "screen"
        self._cache_dir = cache_dir
        self._image_model = image_model
        self._client = client
        self._openai_client = openai_client
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

    def show(self, png: bytes, owner: str = "requested", *, expires_after_s: float | None = None) -> None:
        """Make ``png`` the screen the TRMNL fetches at its next check-in.

        ``owner`` says who put it up: "requested" (someone asked) or "music" (the now-playing card).
        With ``expires_after_s`` (camera photos) the screen leaves after that long and its file is deleted.
        """
        if len(png) > MAX_PNG_BYTES:
            raise ValueError(f"Screen is {len(png)} bytes; the TRMNL draws at most {MAX_PNG_BYTES}.")
        with self._lock:
            last = read_json(self.spool / CURRENT).get("screen")
            last_stamp = int(last.rsplit("-", 1)[1]) if isinstance(last, str) and last[-1:].isdigit() else 0
            stamp = max(int(self._clock()), last_stamp + 1)  # unique even for two screens in a second
            filename = f"reachy-000001-{stamp}"
            (self.spool / f"{filename}.png").write_bytes(png)
            now = self._clock()
            expires_at = None if expires_after_s is None else now + expires_after_s
            self._update(
                screen=filename, shown_at=now, owner=owner, expires_at=expires_at, private=expires_after_s is not None
            )

    def show_home(self) -> None:
        """Go back to the resting card."""
        with self._lock:
            self._update(screen=None, owner=None)

    def owner(self) -> str:
        """Who the screen showing now belongs to: "home", "requested" or "music"."""
        current = read_json(self.spool / CURRENT)
        if current_filename(self.spool, self._clock()) in (None, current.get("home")):
            return "home"
        owner = current.get("owner")
        return owner if isinstance(owner, str) else "requested"

    def keep_alive(self) -> None:
        """Keep the music card from idling back to the resting card while music plays."""
        with self._lock:
            if self.owner() == "music":
                self._update(shown_at=self._clock())

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
        """Return a drawing of ``subject``: from the cache, else a new one."""
        path = self._cache_dir / DRAWINGS_DIR / f"{_cache_key(subject)}.png"
        if path.is_file():
            with Image.open(path) as cached:
                return cached.convert("L")
        picture = await self._generate(DRAWING_PROMPT.format(subject=subject), None, "display_drawing", "the drawing")
        path.parent.mkdir(parents=True, exist_ok=True)
        picture.save(path)
        return picture

    async def edit_photo(self, jpeg: bytes, edit: str) -> Image.Image:
        """Redraw a camera photo with ``edit`` in the drawing style; nothing is cached."""
        return await self._generate(PHOTO_EDIT_PROMPT.format(edit=edit), jpeg, "display_photo_edit", "the edit")

    async def _generate(self, prompt: str, photo: bytes | None, feature: str, what: str) -> Image.Image:
        """Ask the image model for one picture, record its cost, and return it cleaned for four grays."""
        try:
            if self._image_model.startswith(OPENAI_IMAGE_PREFIX):
                data, tokens = await asyncio.wait_for(self._openai_image(prompt, photo), DRAWING_TIMEOUT_S)
            else:
                data, tokens = await asyncio.wait_for(self._gemini_image(prompt, photo), DRAWING_TIMEOUT_S)
        except asyncio.TimeoutError as error:
            raise DrawingError(f"{what} took too long") from error
        if tokens:
            get_ledger().record(feature, self._image_model, tokens)
        if data is None:
            raise DrawingError("no picture came back (it may have been blocked)")
        with Image.open(io.BytesIO(data)) as made:
            picture = made.convert("L")
        picture.thumbnail((WIDTH, HEIGHT))
        return picture.filter(ImageFilter.MedianFilter(3))  # JPEG noise would speckle flat fills

    async def _gemini_image(self, prompt: str, photo: bytes | None) -> tuple[bytes | None, dict[str, int]]:
        """Return a Gemini image model's picture (None when it sent none) and its token counts."""
        if self._client is None:
            raise DrawingError("drawings need GEMINI_API_KEY")
        contents: Any = (
            prompt if photo is None else [prompt, types.Part.from_bytes(data=photo, mime_type="image/jpeg")]
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._image_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="4:3")
                ),
            )
        except genai_errors.APIError as error:
            logger.warning("Image model refused the request: HTTP %s", error.code)
            if error.code in (402, 429):
                raise DrawingError("Google's drawing service is out of credits or busy right now") from error
            raise DrawingError("Google's drawing service did not answer") from error
        usage = response.usage_metadata
        tokens = (
            {
                "input_text": int(usage.prompt_token_count or 0),
                "output_image": int(usage.candidates_token_count or 0),
            }
            if usage is not None
            else {}
        )
        parts = response.candidates[0].content.parts if response.candidates and response.candidates[0].content else []
        data = next(
            (part.inline_data.data for part in parts or [] if part.inline_data and part.inline_data.data), None
        )
        return data, tokens

    async def _openai_image(self, prompt: str, photo: bytes | None) -> tuple[bytes | None, dict[str, int]]:
        """Return a gpt-image model's picture (None when it sent none) and its token counts."""
        if self._openai_client is None:
            raise DrawingError("drawings need OPENAI_API_KEY")
        try:
            if photo is None:
                response = await self._openai_client.images.generate(
                    model=self._image_model, prompt=prompt, size=OPENAI_IMAGE_SIZE, quality=OPENAI_IMAGE_QUALITY
                )
            else:
                response = await self._openai_client.images.edit(
                    model=self._image_model,
                    image=("photo.jpg", photo, "image/jpeg"),
                    prompt=prompt,
                    size=OPENAI_IMAGE_SIZE,
                    quality=OPENAI_IMAGE_QUALITY,
                )
        except openai.APIStatusError as error:
            logger.warning("Image model refused the request: HTTP %s", error.status_code)
            if error.status_code in (402, 429):
                raise DrawingError("OpenAI's drawing service is out of credits or busy right now") from error
            if error.status_code == 400:  # moderation_blocked comes back as a 400
                raise DrawingError("the drawing service refused it (it may have been blocked)") from error
            raise DrawingError("OpenAI's drawing service did not answer") from error
        except openai.APIConnectionError as error:
            raise DrawingError("OpenAI's drawing service did not answer") from error
        usage = response.usage
        tokens = {}
        if usage is not None:
            details = usage.input_tokens_details
            tokens = {
                "input_text": details.text_tokens if details else usage.input_tokens,
                "input_image": details.image_tokens if details else 0,
                "output_image": usage.output_tokens,
            }
        encoded = response.data[0].b64_json if response.data else None
        return (base64.b64decode(encoded) if encoded else None), tokens

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
