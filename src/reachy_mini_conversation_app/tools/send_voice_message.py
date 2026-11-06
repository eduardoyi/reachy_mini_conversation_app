import logging
from typing import Any

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.whatsapp_contacts import find_contact, load_contacts


logger = logging.getLogger(__name__)

# The request (after his name) the voice message command handles, in each language.
_MESSAGE_REQUEST = {
    "en": "send a message to {name}",
    "es": "manda un mensaje a {name}",
    "it": "manda un messaggio a {name}",
    "pt": "manda uma mensagem para {name}",
}


def message_request(language: str, name: str) -> str:
    """Return the "send a message to x" request the voice message command handles, in ``language``."""
    return "Reachy, " + _MESSAGE_REQUEST.get(language, _MESSAGE_REQUEST["en"]).format(name=name)


class SendVoiceMessage(Tool):
    """Hand a voice message to the message command: the conversation closes and Reachy records and sends it.

    Voice messages were a quick command only; asked inside a conversation, Gemini said it could not send
    messages (2026-11-06, a message for Tío Sergio).
    """

    name = "send_voice_message"
    description = (
        "Record a WhatsApp voice message from whoever is talking to you and send it to one of the household's "
        "WhatsApp groups. Use it whenever someone asks you to send a message, a voice note or an audio to "
        "someone; never say you cannot send messages. Pass who it is for as they said it and the language you "
        "are speaking; do not ask what the message says. The conversation ends at once and you then ask them "
        "to speak, record it and send it yourself."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Who it is for, as they said it (e.g. 'Sergio', 'la familia')."},
            "language": {"type": "string", "enum": ["en", "es", "it", "pt"]},
        },
        "required": ["to"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Check who it is for, then close the conversation and start recording the message."""
        request = deps.request_after_close
        if request is None:
            return {"error": "Voice messages are not set up."}
        contacts = load_contacts(config.WHATSAPP_CONTACTS_FILE, normalize)
        contact = find_contact(normalize(str(kwargs.get("to") or "")), contacts)
        if contact is None:
            names = ", ".join(contact.name for contact in contacts) or "nobody yet"
            return {
                "error": f"That is not one of the WhatsApp groups. You can send a message to: {names}.",
                "instruction": "Say so, or offer those names. Never guess; nothing was sent.",
            }
        language = str(kwargs.get("language") or "en")
        if not request(message_request(language, contact.name)):
            return {"error": "No conversation is open to hand the message from."}
        logger.info("Tool call: send_voice_message handed to the message command")
        return {
            "status": "recording",
            "note": "Say nothing more: the conversation is closing and you ask for the message yourself now.",
        }
