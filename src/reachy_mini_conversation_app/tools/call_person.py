import logging
from typing import Any

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.quick_commands import CALLER_CHECK_S, CALLER_CHECK_INTERVAL_S, normalize
from reachy_mini_conversation_app.whatsapp_calls import call_request, load_callees, parent_in_front
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.whatsapp_contacts import find_contact


logger = logging.getLogger(__name__)


class CallPerson(Tool):
    """Hand a WhatsApp call to the call command: the conversation closes and the call goes through Reachy."""

    name = "call_person"
    description = (
        "Start a WhatsApp voice call through you (your speaker and microphone) to someone on the household's "
        "call list, when Eduardo or Adriana asks (the camera checks it; kids cannot start calls). Use it "
        "whenever they ask you to call someone; never say you cannot make calls. Pass the name as they said "
        "it and the language you are speaking. The conversation ends at once and you say 'Calling …!' yourself."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Who to call, as they said it (e.g. 'Eduardo', 'papá')."},
            "language": {"type": "string", "enum": ["en", "es", "it", "pt"]},
        },
        "required": ["name"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Check the name and the caller, then close the conversation and start the call."""
        request = deps.request_after_close
        if request is None:
            return {"error": "Calls are not set up."}
        callees = load_callees(config.WHATSAPP_CALLS_APPROVED_FILE, normalize)
        contact = find_contact(normalize(str(kwargs.get("name") or "")), callees)
        if contact is None:
            names = ", ".join(callee.name for callee in callees) or "nobody yet"
            return {
                "error": f"That name is not on the call list. You can call: {names}.",
                "instruction": "Say so, or offer those names. Never guess; no call was made.",
            }
        if not await parent_in_front(
            deps.face_recognition, config.WHATSAPP_CALLERS, CALLER_CHECK_S, CALLER_CHECK_INTERVAL_S
        ):
            logger.info("call_person refused: no allowed caller in front of the camera")
            return {
                "error": "Only Eduardo or Adriana can start a call, and the camera does not see either of them.",
                "instruction": "Say that only Mom or Dad can start a call. No call was made.",
            }
        language = str(kwargs.get("language") or "en")
        if not request(call_request(language, contact.name)):
            return {"error": "No conversation is open to hand the call from."}
        logger.info("Tool call: call_person handed to the call command")
        return {
            "status": "calling",
            "note": "Say nothing more: the conversation is closing and you start the call yourself now.",
        }
