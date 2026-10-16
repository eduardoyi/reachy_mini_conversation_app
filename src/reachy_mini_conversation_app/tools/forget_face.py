import asyncio
import logging
from typing import Any, Dict

from reachy_mini_conversation_app.face_identity import FaceStoreError
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class ForgetFace(Tool):
    """Delete someone's enrolled face."""

    name = "forget_face"
    description = (
        "Delete the saved face of an enrolled person so you no longer recognize them. "
        "Use when someone asks you to forget their face (or a household member's)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the person whose face to forget."},
        },
        "required": ["name"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Remove the person's fingerprints and photos."""
        service = deps.face_recognition
        if service is None:
            return {"error": "face recognition is disabled"}
        logger.info("Tool call: forget_face")
        try:
            removed = await asyncio.to_thread(service.registry.forget, str(kwargs.get("name") or ""))
        except FaceStoreError as error:
            return {"error": str(error)}
        if removed is None:
            return {"error": "no saved face under that name"}
        return {"status": "forgotten", "name": removed.name}
