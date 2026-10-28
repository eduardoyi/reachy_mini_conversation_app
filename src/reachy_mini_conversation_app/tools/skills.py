"""Rarely used tools offered as skills, so their long descriptions are not resent on every turn.

Realtime models reread every tool declaration on every turn, and Gemini Live has no cache to make the
repeats cheap. A skill appears only as one line in the ``use_skill`` menu; the model reads its full
instructions with ``skill_help`` when it needs them (they then sit in the conversation until context
compression drops them), or calls ``use_skill`` straight away and gets the instructions back with
any argument error. Frequently used, latency-sensitive and system tools stay ordinary tools, and so do
tools that take several calls in a row: behind a skill, Spotify's search-then-play stalled after the search.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict


if TYPE_CHECKING:
    from reachy_mini_conversation_app.tools.core_tools import Tool, ToolSpec


logger = logging.getLogger(__name__)

SKILL_HELP = "skill_help"
USE_SKILL = "use_skill"

# One line each for the menu; the tool's own description is what skill_help returns.
SKILL_SUMMARIES: Dict[str, str] = {
    "home_lights": "home lights and smart plugs: what is on, on/off, brightness, warmth, color, scenes",
    "tv": "the TV: on/off, volume, mute, play/pause, remote buttons, open an app, search YouTube",
    "remember": "save a household memory, only when someone explicitly asks you to remember something",
    "forget": "remove a saved memory when someone asks you to forget it",
    "update_memory": "correct a saved household memory",
    "remember_face": "learn the face of the person in front of you, when they ask you to",
    "forget_face": "delete someone's saved face",
    "who_is_here": "recognize who is in front of the camera",
    "head_tracking": "start or stop following someone's face with your head",
    "sweep_look": "look around the room from left to right",
}


def fold_into_skills(specs: "list[ToolSpec]") -> "list[ToolSpec]":
    """Replace the specs of skill tools with skill_help and use_skill, when any skill tool is enabled."""
    skills = [spec["name"] for spec in specs if spec["name"] in SKILL_SUMMARIES]
    if not skills:
        return specs
    menu = "\n".join(f"- {name}: {SKILL_SUMMARIES[name]}" for name in skills)
    direct = [spec for spec in specs if spec["name"] not in SKILL_SUMMARIES]
    return [
        *direct,
        {
            "type": "function",
            "name": USE_SKILL,
            "description": (
                "Use one of these skills by name, with its arguments. Call skill_help first unless you "
                "already know the skill's arguments in this conversation; a wrong call returns them.\n" + menu
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "enum": skills},
                    "arguments": {"type": "object", "description": "The skill's arguments, as skill_help gives them."},
                },
                "required": ["skill"],
            },
        },
        {
            "type": "function",
            "name": SKILL_HELP,
            "description": "Get a use_skill skill's instructions and arguments before using it.",
            "parameters": {
                "type": "object",
                "properties": {"skill": {"type": "string", "enum": skills}},
                "required": ["skill"],
            },
        },
    ]


def skill_help(tools: "dict[str, Tool]", args: Dict[str, Any]) -> Dict[str, Any]:
    """Return a skill's full instructions and argument schema."""
    tool = _skill(tools, args.get("skill"))
    if tool is None:
        return _unknown(tools, args.get("skill"))
    return {"skill": tool.name, "instructions": tool.description, "arguments": tool.parameters_schema}


def check_arguments(tools: "dict[str, Tool]", args: Dict[str, Any]) -> tuple["Tool | None", Dict[str, Any]]:
    """Return (the skill's tool, its arguments) for use_skill, or (None, an error with the skill's help)."""
    tool = _skill(tools, args.get("skill"))
    if tool is None:
        logger.info("use_skill rejected: unknown skill")
        return None, _unknown(tools, args.get("skill"))
    arguments = args.get("arguments") or {}
    if not isinstance(arguments, dict):
        logger.info("use_skill rejected: skill=%s arguments not an object", tool.name)
        return None, {"error": "arguments must be an object.", **skill_help(tools, {"skill": tool.name})}
    schema = tool.parameters_schema
    properties = schema.get("properties") or {}
    problems = [f"missing {name}" for name in schema.get("required") or [] if name not in arguments]
    unknown = [name for name in arguments if name not in properties]
    problems += [f"unknown argument {name}" for name in unknown]
    invalid = []
    for name, value in arguments.items():
        allowed = (properties.get(name) or {}).get("enum")
        if allowed and value not in allowed:
            problems.append(f"{name} must be one of {allowed}")
            invalid.append(name)
    if problems:
        # Argument names only: their values can carry the person's words.
        logger.info(
            "use_skill rejected: skill=%s missing=%s unknown=%s invalid=%s",
            tool.name,
            [name for name in schema.get("required") or [] if name not in arguments],
            unknown,
            invalid,
        )
        return None, {"error": "; ".join(problems), **skill_help(tools, {"skill": tool.name})}
    return tool, arguments


def _skill(tools: "dict[str, Tool]", name: object) -> "Tool | None":
    return tools.get(name) if isinstance(name, str) and name in SKILL_SUMMARIES else None


def _unknown(tools: "dict[str, Tool]", name: object) -> Dict[str, Any]:
    available = [skill for skill in SKILL_SUMMARIES if skill in tools]
    return {"error": f"unknown skill: {name}", "skills": available}
