import asyncio
import logging
from typing import Any

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GoToSleep(Tool):
    """Put Reachy to sleep: standby with the hybrid runtime, otherwise stop the app."""

    name = "go_to_sleep"
    description = (
        'Use when someone tells you to go to sleep, take a nap or rest, in any language ("go to sleep", '
        '"a dormir", "vai dormir", "vai a dormire"). You lower your head as if asleep and stop listening '
        "until someone says your name; say a very short good night. Do not use for idle turns, sleepy emotions, "
        "silence, ambiguous requests, or being asked to turn off."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Go to standby when the runtime has one, else sleep and request app shutdown."""
        sleep = deps.standby or deps.go_to_sleep
        if sleep is None:
            return {"error": "go_to_sleep is unavailable in this runtime"}

        logger.info("Tool call: go_to_sleep standby=%s", deps.standby is not None)
        try:
            return await asyncio.to_thread(sleep)
        except Exception as e:
            logger.error("go_to_sleep failed: %s", e)
            return {"error": f"go_to_sleep failed: {type(e).__name__}: {e}"}
