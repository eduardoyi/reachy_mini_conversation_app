import time
import logging
from typing import Any, Final

from reachy_mini_conversation_app.games.catalog import GAMES
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# A start counts only this soon after the game's rules were read (the same conversation, with its question).
RULES_VALID_S: Final[float] = 300.0


class PlayGame(Tool):
    """Start one of Reachy's games: read its rules first, ask whether they want to play, then start it.

    Starting needs the rules first in the same conversation, so the model always asks before a game starts.
    The game then runs locally (games/host.py) with the conversation closed.
    """

    name = "play_game"
    description = (
        "Reachy's games, played locally after this conversation closes: "
        + "; ".join(f"{game.key} ({game.title['en']}, '{game.title['es']}')" for game in GAMES.values())
        + ". When someone wants to play, call action=rules first: it says how to explain the game in one "
        "sentence and ask whether they want to play. Only after a yes, call action=start, then say only "
        "'¡Vamos!' (or 'Let's go!') and call end_conversation. Never start a game without that yes."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "game": {"type": "string", "enum": list(GAMES)},
            "action": {"type": "string", "enum": ["rules", "start"]},
            "language": {"type": "string", "enum": ["es", "en"], "description": "The language they asked in."},
        },
        "required": ["game", "action"],
    }

    def __init__(self) -> None:
        """No game's rules read yet."""
        self._rules_read: dict[str, float] = {}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return a game's rules, or start it once its rules were read."""
        key = str(kwargs.get("game") or "")
        action = str(kwargs.get("action") or "")
        language = "en" if kwargs.get("language") == "en" else "es"
        logger.info("Tool call: play_game game=%s action=%s", key, action)
        game = GAMES.get(key)
        if game is None:
            return {"error": f"Unknown game {key!r}.", "games": list(GAMES)}
        host = getattr(deps, "games", None)
        if host is None:
            return {"error": "Games are not available right now."}
        if action == "rules":
            self._rules_read[key] = time.monotonic()
            return {
                "game": key,
                "rules": game.rules,
                "ask": f"Explain it in one short sentence ({game.pitch[language]}) and ask whether they want "
                "to play. Start it only after a yes.",
            }
        if action != "start":
            return {"error": "action must be rules or start."}
        read_at = self._rules_read.get(key)
        if read_at is None or time.monotonic() - read_at >= RULES_VALID_S:
            return {"error": "Read the rules first (action=rules) and ask whether they want to play."}
        problem = host.start(key, language)
        if problem is not None:
            return {"error": problem}
        self._rules_read.clear()
        return {"status": "started", "note": "Say only '¡Vamos!' (or 'Let's go!') and call end_conversation now."}
