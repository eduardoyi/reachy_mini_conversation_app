import io
import asyncio
import logging
from typing import Any
from datetime import date

from PIL import Image

from reachy_mini_conversation_app import photo_archive
from reachy_mini_conversation_app.eink_display import render_magazine
from reachy_mini_conversation_app.morning_page import MONTHS, WEEKDAYS
from reachy_mini_conversation_app.photographer import FACES, PHOTO_TAKE_S, take_photo, start_framing
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools.show_on_display import PHOTO_ON_SCREEN_S, CAMERA_COUNTDOWN_S


logger = logging.getLogger(__name__)

MAGAZINE = "magazine"


def issue_line(number: int, day: date) -> str:
    """Return the cover's issue line, e.g. "Nº 12 · DOMINGO 15 DE NOVIEMBRE"."""
    return f"Nº {number} · {WEEKDAYS[day.weekday()]} {day.day} de {MONTHS[day.month - 1]}".upper()


class MagazineCover(Tool):
    """Take a photo and show it on the e-ink screen as the cover of "RICHIE" magazine, with a headline."""

    name = "magazine_cover"
    description = (
        "Put the people in front of you on the cover of RICHIE magazine on the e-ink screen: a photo with a "
        "headline and cover lines. Use it when someone asks for a magazine photo or cover ('sácanos una foto de "
        "revista', 'put us on a magazine cover'). It is taken 3 seconds after the call: say '3, 2, 1' as you call "
        "it. Write a funny tabloid `headline` (at most 8 words) from what they just said or did, quoting someone "
        "by name when you can (e.g. 'Enzo: «El T-Rex es mío»'), and up to two short `lines` of silly cover "
        "teasers about the family, in the language they speak. Kind and playful, never mocking. The cover stays "
        "at home: it is not sent to WhatsApp."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "The cover's headline, at most 8 words."},
            "lines": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 2,
                "description": "Up to two short cover teasers.",
            },
        },
        "required": ["headline"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Frame the group through the countdown, take the burst photo and show the cover."""
        display = deps.display
        if display is None:
            return {"error": "There is no screen here."}
        if not deps.camera_enabled:
            return {"error": "The camera is off."}
        headline = str(kwargs.get("headline") or "").strip()
        if not headline:
            return {"error": "A cover needs a headline.", "instruction": "Call again with a short headline."}
        raw_lines = kwargs.get("lines")
        lines = [line.strip() for line in raw_lines or [] if isinstance(line, str) and line.strip()]
        logger.info("Tool call: magazine_cover lines=%d", len(lines))
        start_framing(deps)
        try:
            await asyncio.sleep(CAMERA_COUNTDOWN_S)
            jpeg = await asyncio.wait_for(asyncio.to_thread(take_photo, deps.reachy_mini.media, FACES), PHOTO_TAKE_S)
        except asyncio.TimeoutError:
            logger.warning("Magazine photo took longer than %.0f s", PHOTO_TAKE_S)
            jpeg = None
        finally:
            deps.movement_manager.frame_faces(False)
        if jpeg is None:
            return {"error": "The camera gave no picture.", "instruction": "Tell them kindly. Do not retry."}

        def cover() -> bytes:
            number = photo_archive.count(MAGAZINE) + 1
            with Image.open(io.BytesIO(jpeg)) as photo:
                return render_magazine(photo.convert("RGB"), headline, lines, issue_line(number, date.today()))

        png = await asyncio.to_thread(cover)
        await asyncio.to_thread(photo_archive.keep, png, "", MAGAZINE, suffix=".png", edit=f"Magazine: {headline}")
        display.show(png, expires_after_s=PHOTO_ON_SCREEN_S)
        return {"ok": True, "instruction": "Tell them to look at the screen: they are on the cover."}
