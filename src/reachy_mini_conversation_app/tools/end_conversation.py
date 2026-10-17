import logging
from typing import Any

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class EndConversation(Tool):
    """Close the conversation after Reachy's goodbye and wait for his name again."""

    name = "end_conversation"
    description = (
        "End this conversation when it is clearly over: people said goodbye or that they are done "
        '("thanks, that\'s all", "ok bye", "gracias"), or they have clearly turned to talking among '
        "themselves instead of to you. You stop listening until someone says your name; any short goodbye you "
        "say still plays. Do not use while someone is still talking to you, when unsure, or when asked to go to "
        "sleep (use go_to_sleep)."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Ask the runtime to close the conversation once the current reply has played."""
        if deps.end_conversation is None:
            return {"error": "end_conversation is unavailable in this runtime"}
        logger.info("Tool call: end_conversation")
        try:
            return deps.end_conversation()
        except Exception as e:
            logger.error("end_conversation failed: %s", e)
            return {"error": f"end_conversation failed: {type(e).__name__}: {e}"}
