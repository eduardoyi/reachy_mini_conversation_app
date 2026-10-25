import logging
from typing import Any

from reachy_mini_conversation_app.eink_display import (
    DrawingError,
    DrawingLimitError,
    render_text,
    render_word,
    render_photo,
    render_drawing,
)
from reachy_mini_conversation_app.web_pictures import PictureError
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


def _text(kwargs: dict[str, Any], key: str) -> str:
    value = kwargs.get(key)
    return value.strip() if isinstance(value, str) else ""


class ShowOnDisplay(Tool):
    """Show a drawing, a web picture, a word card or short text on the e-ink screen."""

    name = "show_on_display"
    description = (
        "Show something on the e-ink screen beside you: a calm black-and-white screen with no video that "
        "updates in a few seconds. Use it when seeing helps, never for long text. Every time you translate or "
        "teach a word or short phrase, also show it with kind=word; do not only say it. "
        "kind=drawing: a picture of `subject` (in English) with a short `caption`. "
        "kind=picture: a real photo or artwork from the web, for real things worth seeing as they are (a "
        "painting, a monument, an animal, a rocket); `subject` names it precisely in English, e.g. 'Mona Lisa "
        "painting by Leonardo da Vinci' or 'a tiger in the wild, photo'. If no suitable picture is found it is "
        "drawn instead. "
        "kind=word: a word card with `word`, `pronunciation`, `meaning`, an optional `header` and an optional "
        "`picture` (its subject in English); for every translation ('how do you say X in Y') and every word you "
        "teach. "
        "kind=text: one big line (a name, a sum) or a short `title` with up to six `lines`. "
        "kind=home: back to the resting screen. Tell them to look at the screen in a moment."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["drawing", "picture", "word", "text", "home"]},
            "subject": {"type": "string", "description": "For drawing and picture: what to show, in English."},
            "caption": {"type": "string", "description": "For drawing and picture: a few words under it."},
            "word": {"type": "string", "description": "For word: the word or short phrase, shown large."},
            "pronunciation": {"type": "string", "description": "For word: how to say it."},
            "meaning": {"type": "string", "description": "For word: what it means."},
            "header": {"type": "string", "description": "For word: an optional line above, e.g. the question."},
            "picture": {"type": "string", "description": "For word: optional small drawing subject, in English."},
            "title": {"type": "string", "description": "For text: an optional title over the lines."},
            "lines": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 6,
                "description": "For text: one big line, or up to six short lines.",
            },
        },
        "required": ["kind"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Render the requested screen and make it the display's next screen."""
        display = deps.display
        if display is None:
            return {"error": "No e-ink screen is set up."}
        kind = kwargs.get("kind")
        logger.info("Tool call: show_on_display kind=%s", kind)
        result: dict[str, Any] = {"ok": True, "note": "The screen updates in a few seconds."}
        try:
            if kind == "home":
                display.show_home()
                return result
            if kind in ("drawing", "picture"):
                subject = _text(kwargs, "subject")
                if not subject:
                    return {"error": f"{kind} needs a subject."}
                caption = _text(kwargs, "caption")
                png = None
                if kind == "picture":
                    try:
                        found = await display.picture(subject)
                        png = render_photo(found.image, caption, found.source)
                    except PictureError as error:
                        logger.info("No web picture, drawing instead: %s", error)
                        result["shown"] = "a drawing, not a real picture"
                        result["instruction"] = (
                            f"Tell them you could not show a real picture because {error}, so you drew it instead."
                        )
                if png is None:
                    png = render_drawing(await display.drawing(subject), caption)
            elif kind == "word":
                word = _text(kwargs, "word")
                if not word:
                    return {"error": "word needs a word."}
                picture = None
                if subject := _text(kwargs, "picture"):
                    try:
                        picture = await display.drawing(subject)
                    except DrawingError as error:
                        logger.info("Word card shown without its picture: %s", error)
                        result["instruction"] = "The card has no picture this time; mention it if they expected one."
                png = render_word(
                    word,
                    _text(kwargs, "pronunciation"),
                    _text(kwargs, "meaning"),
                    _text(kwargs, "header"),
                    picture,
                )
            elif kind == "text":
                raw_lines = kwargs.get("lines")
                lines = [line.strip() for line in raw_lines or [] if isinstance(line, str) and line.strip()]
                if not lines:
                    return {"error": "text needs at least one line."}
                png = render_text(lines, _text(kwargs, "title"))
            else:
                return {"error": "kind must be drawing, word, text or home."}
        except DrawingLimitError:
            return {
                "error": "No more new drawings today.",
                "instruction": "Say your pencil needs a rest until tomorrow; offer a word or text card instead.",
            }
        except DrawingError as error:
            logger.warning("show_on_display drawing failed: %s", error)
            return {"error": f"Could not draw that: {error}.", "instruction": "Tell them kindly. Do not retry."}
        display.show(png)
        if not display.awake():
            result["note"] = "The screen is asleep; it wakes and shows this within about five minutes."
            napping = (
                "Say the screen is napping, so it will show up in a few minutes, "
                "or right away if they press the button on its back."
            )
            result["instruction"] = f"{result['instruction']} {napping}" if "instruction" in result else napping
        return result
