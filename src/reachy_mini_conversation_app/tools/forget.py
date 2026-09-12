import logging

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.memory import forget_memory_fact
from reachy_mini_conversation_app.household_memory import (
    MemoryStoreError,
    HouseholdMemoryStore,
    MemoryValidationError,
)
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolSpec, ToolDependencies


logger = logging.getLogger(__name__)


def _forget_schema() -> dict[str, object]:
    """Return the current forget tool schema."""
    if not config.HOUSEHOLD_MEMORY_ENABLED:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A short search phrase that should be present in the fact to remove. Matching is case-insensitive."
                    ),
                },
            },
            "required": ["query"],
        }

    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A specific phrase matching exactly one saved memory.",
            },
            "memory_id": {
                "type": "string",
                "description": "The stable memory ID returned by remember or update_memory.",
            },
        },
        "oneOf": [{"required": ["query"]}, {"required": ["memory_id"]}],
        "additionalProperties": False,
    }


class Forget(Tool):
    """Remove one long-term memory fact."""

    name = "forget"
    description = (
        "Remove a previously saved fact from long-term memory. Call this when the user asks you to forget something, "
        "or when saved information becomes obsolete. Match by a specific free-text phrase present in the fact."
    )
    parameters_schema = _forget_schema()

    def __init__(self) -> None:
        """Initialize the feature-aware tool schema."""
        self.parameters_schema = _forget_schema()

    def spec(self) -> ToolSpec:
        """Return the current feature-aware function schema."""
        self.parameters_schema = _forget_schema()
        return super().spec()

    async def __call__(self, deps: ToolDependencies, **kwargs: object) -> dict[str, object]:
        """Forget one memory fact by query or stable ID."""
        query = kwargs.get("query")
        memory_id = kwargs.get("memory_id")

        if not config.HOUSEHOLD_MEMORY_ENABLED:
            if not isinstance(query, str) or not query.strip():
                logger.warning("forget: empty query")
                return {"error": "query must be a non-empty string"}

            try:
                legacy_result = forget_memory_fact(deps.instance_path, query=query)
            except (MemoryStoreError, OSError):
                logger.warning("forget: memory store unavailable")
                return {"error": "household memory is unavailable"}
            if legacy_result.removed is None:
                logger.info("Tool call: forget no_match")
                return {"error": f'no memory matched "{query}"; nothing was removed'}

            response: dict[str, object] = {
                "removed": legacy_result.removed.text,
                "memory_id": legacy_result.removed.id,
            }
            if len(legacy_result.candidates) > 1:
                response["other_matches"] = [fact.text for fact in legacy_result.candidates[1:]]

            logger.info("Tool call: forget removed memory_id=%s", legacy_result.removed.id)
            return response

        if set(kwargs) - {"query", "memory_id", "tool_manager"}:
            logger.warning("forget: unknown field supplied")
            return {"error": "invalid household memory input"}
        if query is not None and memory_id is not None:
            logger.warning("forget: both query and memory_id supplied")
            return {"error": "provide either query or memory_id, not both"}
        if query is None and memory_id is None:
            logger.warning("forget: no query or memory_id supplied")
            return {"error": "query or memory_id is required"}
        if query is not None and (not isinstance(query, str) or not query.strip()):
            logger.warning("forget: invalid query")
            return {"error": "query must be a non-empty string"}
        if memory_id is not None and (not isinstance(memory_id, str) or not memory_id.strip()):
            logger.warning("forget: invalid memory_id")
            return {"error": "memory_id must be a non-empty string"}

        try:
            result = HouseholdMemoryStore(deps.instance_path).forget(
                query=query if isinstance(query, str) else None,
                memory_id=memory_id if isinstance(memory_id, str) else None,
            )
        except MemoryValidationError:
            logger.warning("forget: invalid household memory input")
            return {"error": "invalid household memory input"}
        except MemoryStoreError:
            logger.warning("forget: household memory store unavailable")
            return {"error": "household memory is unavailable"}

        if result.ambiguous:
            logger.info("Tool call: forget ambiguous_query count=%s", len(result.candidates))
            return {
                "error": "memory query is ambiguous; provide the memory id",
                "candidates": [{"memory_id": entry.id, "text": entry.text} for entry in result.candidates],
            }
        if result.removed is None:
            logger.info("Tool call: forget no_match")
            return {"error": "no memory matched; nothing was removed"}

        if deps.on_memory_forgotten is not None:
            deps.on_memory_forgotten(result.removed.id)

        logger.info("Tool call: forget removed memory_id=%s", result.removed.id)
        return {"removed": result.removed.text, "memory_id": result.removed.id}
