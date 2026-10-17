import asyncio
import logging
from typing import Any

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class TurnOff(Tool):
    """Put Reachy in the sleep pose and stop the app."""

    name = "turn_off"
    description = (
        "Use only when someone explicitly asks you to turn off, shut down, switch off, or quit the app, in any "
        "language. You will stop completely and cannot be woken by your name until the app is restarted. Not for "
        '"go to sleep" (use go_to_sleep) or for ending a conversation.'
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Sleep, then request app shutdown."""
        if deps.go_to_sleep is None:
            return {"error": "turn_off is unavailable in this runtime"}

        logger.info("Tool call: turn_off")
        try:
            return await asyncio.to_thread(deps.go_to_sleep)
        except Exception as e:
            logger.error("turn_off failed: %s", e)
            return {"error": f"turn_off failed: {type(e).__name__}: {e}"}
