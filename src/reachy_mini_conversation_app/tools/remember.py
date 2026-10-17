import logging
from datetime import date, datetime

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.memory import add_memory_fact
from reachy_mini_conversation_app.household_memory import (
    DateInput,
    MemoryKind,
    MemoryCertainty,
    MemoryStoreError,
    HouseholdMemoryStore,
    MemoryValidationError,
)
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolSpec, ToolDependencies
from reachy_mini_conversation_app.household_memory_receipts import MutationWriterContext, mutation_writer


logger = logging.getLogger(__name__)

_MEMORY_KINDS = ("note", "situation", "plan", "fact")
_MEMORY_CERTAINTIES = ("stated", "tentative")


def _remember_schema() -> dict[str, object]:
    """Return the current remember tool schema."""
    if not config.HOUSEHOLD_MEMORY_ENABLED:
        return {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "A short, third-person statement about the user, such as "
                        '"Has a dog named Mochi" or "Prefers replies in French". One fact per call.'
                    ),
                },
            },
            "required": ["fact"],
        }

    return {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "One short, grounded memory statement. Keep it under one sentence.",
            },
            "subject_id": {
                "type": "string",
                "description": "The explicit known person ID or household label. Do not guess; skip unknown subjects.",
            },
            "source_person_id": {
                "type": ["string", "null"],
                "description": "The explicit known person ID of the source. Omit when unknown; never guess.",
            },
            "kind": {
                "type": "string",
                "enum": list(_MEMORY_KINDS),
                "description": "The grounded memory kind.",
            },
            "certainty": {
                "type": "string",
                "enum": list(_MEMORY_CERTAINTIES),
                "description": "Use tentative when the statement is explicitly uncertain.",
            },
            "event_start": {
                "type": ["string", "null"],
                "description": "Use a normalized ISO date/time after resolving a grounded relative date; never guess.",
            },
            "event_end": {
                "type": ["string", "null"],
                "description": "Use a normalized ISO date/time after resolving a grounded relative date; never guess.",
            },
            "source_ref": {
                "type": ["string", "null"],
                "description": "An opaque active-session evidence reference, when available.",
            },
        },
        "required": ["fact", "subject_id"],
        "additionalProperties": False,
    }


def _remember_description() -> str:
    if not config.HOUSEHOLD_MEMORY_ENABLED:
        return (
            "Save ONE short fact about the user to long-term memory so it is available in future sessions. "
            "Use this for stable user information they explicitly shared: name, preferences, hobbies, recurring projects, "
            "important people, or plans. Keep each fact atomic and under one sentence. Do not save sensitive data "
            "(passwords, addresses, payment info, health diagnoses) or fleeting details. Use this silently in the "
            'background; acknowledge naturally without saying "I will remember that".'
        )
    description = (
        "Save one grounded household memory with an explicit subject_id. "
        "Use note, situation, plan, or fact as appropriate; skip unknown subjects and never infer identity. "
        "Do not save sensitive data or ask a memory-management question."
    )
    if config.HOUSEHOLD_MEMORY_CAPTURE_ENABLED:
        # Background capture already saves what people mention; two writers would store the same fact twice.
        description = (
            "Things people mention are saved to household memory automatically in the background, so do not call "
            "this for them. Call it only when someone explicitly asks you to remember something. " + description
        )
    return description


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_date(value: object, field_name: str) -> DateInput | None:
    if value is None:
        return None
    if not isinstance(value, (str, date, datetime)):
        raise ValueError(f"{field_name} must be an ISO date or date-time")
    return value


def _memory_kind(value: object) -> MemoryKind:
    if value == "note":
        return "note"
    if value == "situation":
        return "situation"
    if value == "plan":
        return "plan"
    if value == "fact":
        return "fact"
    raise ValueError("kind must be one of the supported values")


def _memory_certainty(value: object) -> MemoryCertainty:
    if value == "stated":
        return "stated"
    if value == "tentative":
        return "tentative"
    raise ValueError("certainty must be one of the supported values")


class Remember(Tool):
    """Save one short, grounded memory fact."""

    name = "remember"
    description = _remember_description()
    parameters_schema = _remember_schema()

    def __init__(self) -> None:
        """Initialize the feature-aware tool schema."""
        self.description = _remember_description()
        self.parameters_schema = _remember_schema()

    def spec(self) -> ToolSpec:
        """Return the current feature-aware function schema."""
        self.description = _remember_description()
        self.parameters_schema = _remember_schema()
        return super().spec()

    async def __call__(self, deps: ToolDependencies, **kwargs: object) -> dict[str, object]:
        """Save one memory fact."""
        fact = kwargs.get("fact")
        if not isinstance(fact, str) or not fact.strip():
            logger.warning("remember: empty fact")
            return {"error": "fact must be a non-empty string"}

        if not config.HOUSEHOLD_MEMORY_ENABLED:
            try:
                legacy_stored = add_memory_fact(deps.instance_path, fact)
            except (MemoryStoreError, OSError):
                logger.warning("remember: memory store unavailable")
                return {"error": "household memory is unavailable"}
            if legacy_stored is None:
                return {"error": "fact was empty or invalid; nothing was saved"}

            logger.info("Tool call: remember memory_id=%s", legacy_stored.id)
            return {"saved": legacy_stored.text, "memory_id": legacy_stored.id}

        try:
            allowed_fields = {
                "fact",
                "subject_id",
                "source_person_id",
                "kind",
                "certainty",
                "event_start",
                "event_end",
                "source_ref",
                "tool_manager",
            }
            if set(kwargs) - allowed_fields:
                raise ValueError("unknown household memory field")
            subject_id = _required_string(kwargs.get("subject_id"), "subject_id")
            source_person_id = _optional_string(kwargs.get("source_person_id"), "source_person_id")
            if source_person_id is None and deps.current_person_id is not None:
                # The face recognized in front of the camera is who told us, unless the model said otherwise.
                source_person_id = deps.current_person_id()
            kind = _memory_kind(kwargs.get("kind", "note"))
            certainty = _memory_certainty(kwargs.get("certainty", "stated"))
            event_start = _optional_date(kwargs.get("event_start"), "event_start")
            event_end = _optional_date(kwargs.get("event_end"), "event_end")
            source_ref = _optional_string(kwargs.get("source_ref"), "source_ref")
            with mutation_writer(
                MutationWriterContext("tool.remember", deps.memory_mutation_generation or "untrusted")
            ):
                stored = HouseholdMemoryStore(deps.instance_path).remember(
                    text=fact,
                    subject_id=subject_id,
                    source_person_id=source_person_id,
                    kind=kind,
                    certainty=certainty,
                    event_start=event_start,
                    event_end=event_end,
                    source_ref=source_ref,
                )
        except (MemoryValidationError, ValueError):
            logger.warning("remember: invalid household memory input")
            return {"error": "invalid household memory input"}
        except (MemoryStoreError, OSError):
            logger.warning("remember: household memory store unavailable")
            return {"error": "household memory is unavailable"}

        logger.info("Tool call: remember memory_id=%s", stored.id)
        return {"saved": stored.text, "memory_id": stored.id}
