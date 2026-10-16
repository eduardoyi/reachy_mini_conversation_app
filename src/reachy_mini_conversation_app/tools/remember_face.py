import asyncio
import logging
from typing import Any, Dict

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class RememberFace(Tool):
    """Enroll the face of the person in front of the camera under their name."""

    name = "remember_face"
    description = (
        "Remember the face of the person in front of the camera so you recognize them later. "
        "Only use when that person asks you to remember their face, and use the name they give for themselves; "
        "never enroll someone else. Tell them to look at you for about three seconds first."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The name the person gave for themselves."},
        },
        "required": ["name"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Capture a few seconds of the face in view and save it."""
        service = deps.face_recognition
        if service is None:
            return {"error": "face recognition is disabled"}
        name = str(kwargs.get("name") or "").strip()
        if not name:
            return {"error": "name is required"}
        logger.info("Tool call: remember_face")
        deps.movement_manager.turn_toward_speaker()
        result = await asyncio.to_thread(service.enroll_from_camera, name)
        if result.person is None:
            return {"error": result.error or "enrollment failed"}
        return {"status": "remembered", "name": result.person.name, "samples": result.person.sample_count}
