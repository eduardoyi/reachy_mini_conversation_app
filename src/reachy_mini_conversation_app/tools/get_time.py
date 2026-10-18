import logging
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


def _describe(now: datetime) -> dict[str, Any]:
    offset = now.utcoffset()
    hours = offset.total_seconds() / 3600 if offset is not None else 0.0
    return {
        "time": now.strftime("%H:%M"),
        "date": now.strftime("%A %d %B %Y"),
        "timezone": str(now.tzinfo),
        "utc_offset_hours": round(hours, 2),
    }


class GetTime(Tool):
    """The current date and time from the robot's clock (local replacement for the remote time Space)."""

    name = "get_time"
    description = (
        "Get the current date and time. Leave timezone empty for home; for a named place pass its IANA "
        "timezone like 'Europe/Madrid' or 'America/Caracas' (derive it from the place). Call this directly "
        "whenever the user asks the time, the date or the day; do not ask for their city."
    )
    parameters_schema = {
        "type": "object",
        "properties": {"timezone": {"type": "string", "description": "IANA timezone; empty for home."}},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return the time at home, or in ``timezone``."""
        name = str(kwargs.get("timezone") or "").strip()
        logger.info("Tool call: get_time home=%s", not name)
        if not name:
            return _describe(datetime.now().astimezone())
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return {"error": f"Unknown timezone {name!r}; use an IANA name like 'Europe/Madrid'."}
        result = _describe(datetime.now(zone))
        result["timezone"] = name
        result["home"] = _describe(datetime.now().astimezone())
        return result
