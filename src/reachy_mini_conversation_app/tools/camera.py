import time
import base64
import asyncio
import logging
from typing import Any, Dict

from reachy_mini_conversation_app.camera_frames import capture_jpeg, boost_dim_jpeg, fit_jpeg_for_model
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class Camera(Tool):
    """Take a picture with the camera to see what is in front of the robot."""

    name = "camera"
    description = (
        "Take a picture with the camera to see what is in front of the robot. "
        "Use this when the user asks you to look at something, see what they are holding, "
        "check their appearance, describe the scene, or comment on how they look. "
        "Also use it when the user asks what you can see or wants your visual opinion. "
        "The camera is live, each call captures the current moment. "
        "If the user asks you to look without saying at what, do not ask for clarification, call this tool and describe what you see. "
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to observe or ask about in the picture. "
                    "Examples: what is the user holding, describe the user's outfit, "
                    "what do you see around you, how does the user look today."
                ),
            },
        },
        "required": ["question"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Take a picture with the camera and return the base64-encoded JPEG."""
        question = (kwargs.get("question") or "").strip()
        if not question:
            logger.warning("camera: empty question")
            return {"error": "question must be a non-empty string"}

        logger.info("Tool call: camera")

        if not deps.camera_enabled:
            logger.error("Camera is disabled")
            return {"error": "Camera is disabled"}

        started_at = time.perf_counter()
        jpeg_bytes = await asyncio.to_thread(capture_jpeg, deps.reachy_mini.media)
        if jpeg_bytes is None:
            logger.error("No frame available from camera")
            return {"error": "No frame available"}
        captured_at = time.perf_counter()
        model_jpeg = await asyncio.to_thread(lambda raw: boost_dim_jpeg(fit_jpeg_for_model(raw)), jpeg_bytes)
        logger.info(
            "Camera frame ready capture_ms=%.1f scale_ms=%.1f bytes=%d->%d",
            (captured_at - started_at) * 1000.0,
            (time.perf_counter() - captured_at) * 1000.0,
            len(jpeg_bytes),
            len(model_jpeg),
        )

        return {"b64_im": base64.b64encode(model_jpeg).decode("utf-8")}
