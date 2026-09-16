import logging
from typing import TypedDict

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tools.remember import (
    _memory_kind,
    _optional_date,
    _optional_string,
    _required_string,
    _memory_certainty,
)
from reachy_mini_conversation_app.household_memory import (
    DateInput,
    MemoryKind,
    MemoryStatus,
    MemoryCertainty,
    MemoryStoreError,
    HouseholdMemoryStore,
    MemoryValidationError,
)
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.household_memory_receipts import MutationWriterContext, mutation_writer


logger = logging.getLogger(__name__)


class _UpdateArguments(TypedDict, total=False):
    text: str
    subject_id: str | None
    source_person_id: str | None
    kind: MemoryKind
    certainty: MemoryCertainty
    event_start: DateInput | None
    event_end: DateInput | None
    source_ref: str | None
    status: MemoryStatus


def _memory_status(value: object) -> MemoryStatus:
    if value == "current":
        return "current"
    if value == "resolved":
        return "resolved"
    if value == "superseded":
        return "superseded"
    raise ValueError("status must be one of the supported values")


class UpdateMemory(Tool):
    """Correct one saved household memory by stable ID."""

    name = "update_memory"
    description = (
        "Correct one saved household memory by its stable memory_id using grounded user evidence. "
        "Preserve tentative status and do not resolve or reopen a memory based only on the assistant's guess."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "The stable memory ID returned by remember or a previous update.",
            },
            "fact": {
                "type": "string",
                "description": "The corrected short memory text.",
            },
            "subject_id": {
                "type": ["string", "null"],
                "description": "The explicit subject label; use null only when correcting it to unknown.",
            },
            "source_person_id": {
                "type": ["string", "null"],
                "description": "The explicit source person label, or null when unknown.",
            },
            "kind": {
                "type": "string",
                "enum": ["note", "situation", "plan", "fact"],
            },
            "certainty": {
                "type": "string",
                "enum": ["stated", "tentative"],
            },
            "event_start": {
                "type": ["string", "null"],
                "description": "A normalized ISO date/time after resolving a grounded relative date; never guess.",
            },
            "event_end": {
                "type": ["string", "null"],
                "description": "A normalized ISO date/time after resolving a grounded relative date; never guess.",
            },
            "source_ref": {
                "type": ["string", "null"],
            },
            "status": {
                "type": "string",
                "enum": ["current", "resolved", "superseded"],
            },
        },
        "required": ["memory_id"],
        "additionalProperties": False,
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: object) -> dict[str, object]:
        """Correct one memory when structured household memory is enabled."""
        if not config.HOUSEHOLD_MEMORY_ENABLED:
            return {"error": "household memory is disabled"}

        try:
            allowed_fields = {
                "memory_id",
                "fact",
                "subject_id",
                "source_person_id",
                "kind",
                "certainty",
                "event_start",
                "event_end",
                "source_ref",
                "status",
                "tool_manager",
            }
            if set(kwargs) - allowed_fields:
                raise ValueError("unknown household memory field")
            memory_id = _required_string(kwargs.get("memory_id"), "memory_id")
            changes: _UpdateArguments = {}
            if "fact" in kwargs:
                changes["text"] = _required_string(kwargs["fact"], "fact")
            if "subject_id" in kwargs:
                changes["subject_id"] = _optional_string(kwargs["subject_id"], "subject_id")
            if "source_person_id" in kwargs:
                changes["source_person_id"] = _optional_string(kwargs["source_person_id"], "source_person_id")
            if "kind" in kwargs:
                changes["kind"] = _memory_kind(kwargs["kind"])
            if "certainty" in kwargs:
                changes["certainty"] = _memory_certainty(kwargs["certainty"])
            if "event_start" in kwargs:
                changes["event_start"] = _optional_date(kwargs["event_start"], "event_start")
            if "event_end" in kwargs:
                changes["event_end"] = _optional_date(kwargs["event_end"], "event_end")
            if "source_ref" in kwargs:
                changes["source_ref"] = _optional_string(kwargs["source_ref"], "source_ref")
            if "status" in kwargs:
                changes["status"] = _memory_status(kwargs["status"])
            if not changes:
                return {"error": "update requires at least one field"}

            with mutation_writer(MutationWriterContext("tool.update", deps.memory_mutation_generation or "untrusted")):
                updated = HouseholdMemoryStore(deps.instance_path).update(memory_id, **changes)
        except (MemoryValidationError, ValueError):
            logger.warning("update_memory: invalid household memory input")
            return {"error": "invalid household memory input"}
        except MemoryStoreError:
            logger.warning("update_memory: household memory store unavailable")
            return {"error": "household memory is unavailable"}

        logger.info("Tool call: update_memory memory_id=%s", updated.id)
        return {"updated": updated.text, "memory_id": updated.id}
