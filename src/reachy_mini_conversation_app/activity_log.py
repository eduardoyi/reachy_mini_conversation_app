"""What Reachy did for the household, for the admin app's activity page: calls, voice notes and photos sent.

Entries hold the person's or group's name and the outcome only; never phone numbers, chat ids or media.
"""

from pathlib import Path

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.json_lines import JsonLines


ACTIVITY = JsonLines(Path(config.ACTIVITY_LOG_FILE))


def record(kind: str, who: str, outcome: str, seconds: float | None = None) -> None:
    """Log one event: ``kind`` (call_out, call_in, voice_note, photo), its person or group, and how it ended."""
    entry: dict[str, object] = {"kind": kind, "who": who, "outcome": outcome}
    if seconds is not None:
        entry["seconds"] = round(seconds)
    ACTIVITY.append(entry)
