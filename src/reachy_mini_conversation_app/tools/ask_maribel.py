import logging
from typing import Any

from reachy_mini_conversation_app.quick_commands import MARIBEL_PASSED_ON, MARIBEL_UNREACHABLE, guess_language
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_SAY_EXACTLY = "Say exactly `say`, in your voice, and nothing else about it. Never read out links or code."


class AskMaribel(Tool):
    """Hand a request to Maribel, the household's Grok Bot assistant, and return what she says."""

    name = "ask_maribel"
    description = (
        "Pass a request to Maribel, the household's assistant in the Grok Bot app. Use it ONLY when the person "
        "explicitly asks you to ask, tell or message Maribel, or is answering a question Maribel just asked. "
        "Pass their words as they said them, without rewording. Before calling it, say a two-word "
        "acknowledgement. Maribel takes care of longer work in the Grok Bot app."
    )
    parameters_schema = {
        "type": "object",
        "properties": {"request": {"type": "string", "description": "The person's words for Maribel, verbatim."}},
        "required": ["request"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Send the request and wait briefly for Maribel's first reply."""
        link = deps.maribel
        text = str(kwargs.get("request") or "").strip()
        if link is None:
            return {"error": "Maribel is not set up."}
        if not text:
            return {"error": "Say what to pass to Maribel."}
        language = guess_language(text)
        logger.info("Tool call: ask_maribel answers_question=%s", link.has_open_question())
        request_id = await link.send(text)
        if request_id is None:
            return {"status": "unreachable", "say": MARIBEL_UNREACHABLE[language], "note": _SAY_EXACTLY}
        reply = await link.first_reply(request_id)
        if reply is None:
            return {
                "status": "passed_on",
                "say": MARIBEL_PASSED_ON[language],
                "note": _SAY_EXACTLY + " If her answer comes later, Reachy speaks it himself once you are done.",
            }
        note = _SAY_EXACTLY
        if reply.type == "question":
            note += " She is asking the person; pass their answer back with ask_maribel."
        return {"status": reply.type, "say": reply.speak, "note": note}
