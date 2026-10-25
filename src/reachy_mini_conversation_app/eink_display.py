"""Calm e-ink screens for a TRMNL display: drawings, word cards and short text.

The TRMNL polls a small HTTP server here (its "bring your own server" API: ``/api/setup``,
``/api/display``, ``/api/log``) and draws whatever PNG the reply points to. Screens are 800x480
2-bit grayscale, the four grays the TRMNL OG draws. Text is typed by the renderer, never drawn by
the image model, so accents and spelling are always right. With nothing new for 30 minutes the
display returns to a resting "¡Hola! Soy Reachy" card.
"""

import io
import re
import json
import time
import zlib
import struct
import asyncio
import hashlib
import logging
import functools
import threading
import unicodedata
from typing import Callable
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageFilter
from google import genai
from google.genai import types

from reachy_mini_conversation_app.usage_costs import get_ledger


logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 800, 480
MAX_PNG_BYTES = 90_000  # the TRMNL OG does not draw larger images
IDLE_AFTER_S = 30 * 60
# A check-in waits this long for a new screen before answering; the firmware gives up after 15 s.
CHECK_IN_HOLD_S = 10.0
DRAWING_TIMEOUT_S = 20.0
MAX_TEXT_LINES = 6
DRAWING_PROMPT = (
    "A friendly, simple cartoon of {subject} for young children. Bold clean black outlines on a plain white "
    "background, flat fills in light grey and medium grey only, no gradients, no shading texture, no text, "
    "letters or numbers. One clear subject, centered, filling most of the frame. Cheerful and gentle, never scary."
)
_ASSETS = Path(__file__).parent / "static" / "eink"
_GREY, _LIGHT_GREY = 85, 170
_LEVEL_EDGES = (64, 128, 230)


class DrawingError(RuntimeError):
    """A drawing could not be made."""


class DrawingLimitError(DrawingError):
    """Today's drawings are used up."""


@functools.lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "AtkinsonHyperlegible-Bold.ttf" if bold else "AtkinsonHyperlegible-Regular.ttf"
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
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    picture_height = HEIGHT - 80 if caption else HEIGHT - 20
    fitted = ImageOps.contain(picture.convert("L"), (WIDTH - 20, picture_height))
    canvas.paste(fitted, ((WIDTH - fitted.width) // 2, 10 + (picture_height - fitted.height) // 2))
    if caption:
        font = _fitted(caption, WIDTH - 40, 48, 26)
        _draw_lines(
            ImageDraw.Draw(canvas),
            _wrapped(caption, font, WIDTH - 40, 1),
            font,
            left=20,
            width=WIDTH - 40,
            top=HEIGHT - 66,
            centered=True,
        )
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
    """The current screen, the device server and the drawings."""

    def __init__(
        self,
        *,
        port: int,
        refresh_s: int,
        cache_dir: Path,
        drawings_per_day: int,
        image_model: str,
        client: genai.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Prepare the resting screen; the device server starts with ``start()``."""
        self.port = port
        self.refresh_s = refresh_s
        self._cache_dir = cache_dir
        self._drawings_per_day = drawings_per_day
        self._image_model = image_model
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        # The TRMNL keeps images by file name: <name>-<id>-<unix time>, a new id for every screen.
        self._home = Screen(f"reachy-000000-{int(clock())}", render_home())
        self._screen: Screen | None = None
        self._shown_at = 0.0
        self._serial = 0
        self._last_sent: str | None = None
        self._server: ThreadingHTTPServer | None = None

    def show(self, png: bytes) -> None:
        """Make ``png`` the screen the TRMNL fetches at its next check-in."""
        if len(png) > MAX_PNG_BYTES:
            raise ValueError(f"Screen is {len(png)} bytes; the TRMNL draws at most {MAX_PNG_BYTES}.")
        with self._changed:
            self._serial += 1
            self._screen = Screen(f"reachy-{self._serial:06d}-{int(self._clock())}", png)
            self._shown_at = self._clock()
            self._changed.notify_all()

    def show_home(self) -> None:
        """Go back to the resting card."""
        with self._changed:
            self._screen = None
            self._changed.notify_all()

    def current(self) -> Screen:
        """Return the screen to show now: the last one shown, or the resting card once it is old."""
        with self._lock:
            return self._current()

    def screen_for_check_in(self, hold_s: float) -> Screen:
        """Return the screen for a TRMNL check-in, holding up to ``hold_s`` for one it has not been sent yet."""
        with self._changed:
            self._changed.wait_for(lambda: self._current().filename != self._last_sent, timeout=hold_s)
            screen = self._current()
            self._last_sent = screen.filename
            return screen

    def _current(self) -> Screen:
        if self._screen is None or self._clock() - self._shown_at >= IDLE_AFTER_S:
            return self._home
        return self._screen

    def screen_png(self, filename: str) -> bytes | None:
        """Return the PNG of the current or resting screen with this file name, else None."""
        with self._lock:
            for screen in (self._screen, self._home):
                if screen is not None and screen.filename == filename:
                    return screen.png
        return None

    async def drawing(self, subject: str) -> Image.Image:
        """Return a drawing of ``subject``: from the cache, else a new one counted against today's limit."""
        path = self._cache_dir / f"{_cache_key(subject)}.png"
        if path.is_file():
            with Image.open(path) as cached:
                return cached.convert("L")
        if self._client is None:
            raise DrawingError("drawings need GEMINI_API_KEY")
        self._take_drawing_allowance()
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
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        picture.save(path)
        return picture

    def _take_drawing_allowance(self) -> None:
        """Count one new drawing for today, or raise DrawingLimitError when today's are used up."""
        today = datetime.fromtimestamp(self._clock()).date().isoformat()
        counts_path = self._cache_dir / "drawings-per-day.json"
        try:
            counts = json.loads(counts_path.read_text())
        except (OSError, ValueError):
            counts = {}
        used = int(counts.get(today, 0)) if isinstance(counts, dict) else 0
        if used >= self._drawings_per_day:
            raise DrawingLimitError(f"{used} drawings made today")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        counts_path.write_text(json.dumps({today: used + 1}))

    def start(self) -> None:
        """Start the device server on all interfaces; the TRMNL reaches it over the LAN."""
        if self._server is not None:
            return
        try:
            self._server = _DeviceServer(("0.0.0.0", self.port), self)
        except OSError as error:
            logger.error("E-ink display server could not start on port %d: %s", self.port, error)
            return
        threading.Thread(target=self._server.serve_forever, name="trmnl-server", daemon=True).start()
        logger.info("E-ink display server listening on port %d", self.port)

    def stop(self) -> None:
        """Stop the device server."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class _DeviceServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False  # a held check-in must not delay shutdown

    def __init__(self, address: tuple[str, int], display: EInkDisplay) -> None:
        self.display = display
        super().__init__(address, _DeviceRequestHandler)


class _DeviceRequestHandler(BaseHTTPRequestHandler):
    """The three endpoints the TRMNL firmware calls, plus the current screen's image."""

    server: _DeviceServer

    def do_GET(self) -> None:
        display = self.server.display
        path = self.path.split("?", 1)[0]
        base = f"http://{self.headers.get('Host') or f'127.0.0.1:{display.port}'}"
        if path == "/api/setup":
            logger.info(
                "TRMNL connected (model %s, firmware %s)", self.headers.get("Model"), self.headers.get("FW-Version")
            )
            home = display.current()
            self._send_json(
                {
                    "status": 200,
                    "api_key": "reachy",
                    "friendly_id": "REACHY",
                    "image_url": f"{base}/screens/{home.filename}.png",
                    "message": "Connected to Reachy",
                }
            )
        elif path == "/api/display":
            logger.debug(
                "TRMNL check-in: battery %s V, signal %s dBm",
                self.headers.get("Battery-Voltage"),
                self.headers.get("RSSI"),
            )
            # Answering as soon as a screen changes saves the device a sleep, a reboot and a Wi-Fi reconnect.
            screen = display.screen_for_check_in(CHECK_IN_HOLD_S)
            self._send_json(
                {
                    "status": 0,
                    "image_url": f"{base}/screens/{screen.filename}.png",
                    "filename": screen.filename,
                    "refresh_rate": display.refresh_s,  # an int: the firmware reads a string as 0
                    "reset_firmware": False,
                    "update_firmware": False,
                    "firmware_url": None,
                    "special_function": "none",
                }
            )
        elif path.startswith("/screens/") and path.endswith(".png"):
            png = display.screen_png(path[len("/screens/") : -len(".png")])
            if png is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/log":
            self.send_error(404)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 64_000)
        logger.info("TRMNL reported: %s", self.rfile.read(length).decode(errors="replace")[:500])
        self.send_response(204)
        self.end_headers()

    def _send_json(self, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug(format, *args)
