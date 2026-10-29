import asyncio
import logging
from typing import Any

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.whatsapp import SendResult
from reachy_mini_conversation_app.photographer import take_and_share
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.whatsapp_contacts import find_contact, load_contacts
from reachy_mini_conversation_app.tools.show_on_display import CAMERA_COUNTDOWN_S


logger = logging.getLogger(__name__)


class TakePhoto(Tool):
    """Take a photo and send it to the household's WhatsApp photos group, as "take a picture" does."""

    name = "take_photo"
    description = (
        "Take a photo with your camera and send it to the family's WhatsApp group; it also shows on the "
        "screen. Use it whenever someone asks you to take a picture or a photo (of them, of us, of everyone, "
        "of what you see). It is taken 3 seconds after the call: say '3, 2, 1' out loud as you call it. "
        "To send it to a family member ('send a picture to Sergio'), pass `to`. "
        "For a photo redrawn with a change ('make me a pirate'), use show_on_display kind=camera instead."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Only when they name who it is for: that name as they said it (e.g. 'Sergio').",
            }
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Wait out the spoken countdown, take the photo, show it and send it to the group."""
        to = str(kwargs.get("to") or "").strip()
        chat = config.WHATSAPP_PHOTOS_CHAT
        if to:
            contacts = load_contacts(config.WHATSAPP_CONTACTS_FILE, normalize)
            contact = find_contact(normalize(to), contacts)
            if contact is None:
                names = ", ".join(contact.name for contact in contacts) or "nobody yet"
                return {
                    "error": f"There is no family group for that name. Photos can go to: {names}.",
                    "instruction": "Ask who it is for, or offer those names. Never guess; no photo was taken.",
                }
            chat = contact.chat
        if deps.whatsapp is None or not chat:
            return {"error": "Sending photos to WhatsApp is not set up."}
        if not deps.camera_enabled:
            return {"error": "The camera is off."}
        logger.info("Tool call: take_photo to_family=%s", bool(to))
        deps.movement_manager.frame_faces(True)
        try:
            await asyncio.sleep(CAMERA_COUNTDOWN_S)
            result = await take_and_share(deps, chat)
        finally:
            deps.movement_manager.frame_faces(False)
        if result is None:
            return {"error": "The camera gave no picture.", "instruction": "Tell them kindly. Do not retry."}
        if result is SendResult.SENT:
            return {"status": "sent", "instruction": "Tell them it was sent on WhatsApp."}
        if result is SendResult.UNSURE:
            return {
                "status": "unsure",
                "instruction": "Say you are not sure it reached WhatsApp; they can check the group. Do not retry.",
            }
        return {"status": "not_sent", "instruction": "Say you could not send it to WhatsApp. Do not retry."}
