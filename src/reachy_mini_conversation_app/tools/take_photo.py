import asyncio
import logging
from typing import Any

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.whatsapp import SendResult
from reachy_mini_conversation_app.photographer import take_and_share
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.tools.show_on_display import CAMERA_COUNTDOWN_S


logger = logging.getLogger(__name__)


class TakePhoto(Tool):
    """Take a photo and send it to the household's WhatsApp photos group, as "take a picture" does."""

    name = "take_photo"
    description = (
        "Take a photo with your camera and send it to the family's WhatsApp group; it also shows on the "
        "screen. Use it whenever someone asks you to take a picture or a photo (of them, of us, of everyone, "
        "of what you see). It is taken 3 seconds after the call: say '3, 2, 1' out loud as you call it. "
        "For a photo redrawn with a change ('make me a pirate'), use show_on_display kind=camera instead."
    )
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Wait out the spoken countdown, take the photo, show it and send it to the group."""
        chat = config.WHATSAPP_PHOTOS_CHAT
        if deps.whatsapp is None or not chat:
            return {"error": "Sending photos to WhatsApp is not set up."}
        if not deps.camera_enabled:
            return {"error": "The camera is off."}
        logger.info("Tool call: take_photo")
        await asyncio.sleep(CAMERA_COUNTDOWN_S)
        result = await take_and_share(deps, chat)
        if result is None:
            return {"error": "The camera gave no picture.", "instruction": "Tell them kindly. Do not retry."}
        if result is SendResult.SENT:
            return {"status": "sent", "instruction": "Tell them it is in the family WhatsApp group."}
        if result is SendResult.UNSURE:
            return {
                "status": "unsure",
                "instruction": "Say you are not sure it reached WhatsApp; they can check the group. Do not retry.",
            }
        return {"status": "not_sent", "instruction": "Say you could not send it to WhatsApp. Do not retry."}
