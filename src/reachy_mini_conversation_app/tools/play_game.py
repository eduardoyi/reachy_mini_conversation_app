import time
import logging
from typing import Any, Final

from reachy_mini_conversation_app.games.host import start_question
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
        + ". The stories (kind story, told in Spanish or Italian, the children join in) are started the same "
        "way when someone asks for a story. When someone wants to play, call action=rules first: it gives the short question to ask "
        "whether they want to play (just the name, no description). Only after a yes, call action=start, then "
        "say only '¡Vamos!' (or 'Let's go!'): the conversation closes by itself. Never start a game without "
        "that yes."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "game": {"type": "string", "enum": list(GAMES)},
            "action": {"type": "string", "enum": ["rules", "start"]},
            "language": {
                "type": "string",
                "enum": ["es", "en", "it"],
                "description": "The language they asked in (stories are told in es or it).",
            },
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
        language = str(kwargs.get("language")) if kwargs.get("language") in ("en", "it") else "es"
        logger.info("Tool call: play_game game=%s action=%s", key, action)
        game = GAMES.get(key)
        if game is None:
            return {"error": f"Unknown game {key!r}.", "games": list(GAMES)}
        if language not in game.languages:
            language = game.languages[0]
        host = getattr(deps, "games", None)
        if host is None:
            return {"error": "Games are not available right now."}
        if action == "rules":
            self._rules_read[key] = time.monotonic()
            return {
                "game": key,
                "rules": game.rules,
                "ask": f"Ask only this, word for word, with no description or summary: "
                f"{start_question(game, language)!r} Start it only after a yes.",
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
        if deps.writing is not None:
            deps.writing.stop()
        if deps.end_conversation is not None:
            deps.end_conversation(goodbye=False)
        return {
            "status": "started",
            "note": "Say only '¡Vamos!' (or 'Let's go!'), nothing more: no goodbye, and do not call "
            "end_conversation. The conversation closes by itself.",
        }
