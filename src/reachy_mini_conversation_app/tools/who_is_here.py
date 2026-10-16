import asyncio
import logging
from typing import Any, Dict

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class WhoIsHere(Tool):
    """Recognize the people currently in front of the camera."""

    name = "who_is_here"
    description = (
        "Check who is in front of the camera right now by recognizing enrolled household members' faces. "
        "Use when you need to know who you are talking to, or when asked who someone is."
    )
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Recognize every face in the current frame, largest (closest) first."""
        service = deps.face_recognition
        if service is None:
            return {"error": "face recognition is disabled"}
        if not service.ready:
            return {"error": "face recognition is still starting; try again in a moment"}
        logger.info("Tool call: who_is_here")
        try:
            matches = await asyncio.to_thread(service.identify_now)
        except RuntimeError as error:
            return {"error": str(error)}
        recognized = [{"name": m.name, "confidence": round(m.score, 2)} for m in matches if m is not None]
        unknown = sum(1 for m in matches if m is None)
        if not matches:
            return {"status": "no faces in view"}
        return {"recognized": recognized, "unrecognized_faces": unknown}
