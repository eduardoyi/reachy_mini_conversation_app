"""Tests for offering rarely used tools as skills."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools import skills, core_tools
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


class _Lights(Tool):
    name = "home_lights"
    description = "Control the home's lights. action=status lists rooms; action=set changes a target."
    parameters_schema = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["status", "set"]}, "target": {"type": "string"}},
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "got": kwargs}


class _Camera(Tool):
    name = "camera"
    description = "Take a picture."
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> dict[str, Tool]:
    """Use a registry of one skill tool (lights) and one direct tool (camera)."""
    registry: dict[str, Tool] = {"home_lights": _Lights(), "camera": _Camera()}
    monkeypatch.setattr(core_tools, "ALL_TOOLS", registry)
    monkeypatch.setattr(core_tools, "initialize_tools", lambda *args, **kwargs: None)
    return registry


def _deps() -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


def test_skill_tools_become_one_menu_line_each(tools: dict[str, Tool], monkeypatch: pytest.MonkeyPatch) -> None:
    """With skills on, rare tools leave the declarations and appear as a line in use_skill; others stay."""
    monkeypatch.setattr(config, "SKILL_TOOLS_ENABLED", True)
    specs = {spec["name"]: spec for spec in core_tools.get_tool_specs()}

    assert set(specs) == {"camera", "use_skill", "skill_help"}
    assert f"- home_lights: {skills.SKILL_SUMMARIES['home_lights']}" in specs["use_skill"]["description"]
    assert specs["use_skill"]["parameters"]["properties"]["skill"]["enum"] == ["home_lights"]
    assert "action=status" not in specs["use_skill"]["description"]  # the full instructions wait for skill_help


def test_spotify_stays_a_direct_tool() -> None:
    """Playing music is search then play; behind a skill Gemini re-read the help after the search and never played."""
    assert "spotify_control" not in skills.SKILL_SUMMARIES


def test_switching_skills_off_restores_every_declaration(
    tools: dict[str, Tool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SKILL_TOOLS_ENABLED=0 offers the full tool list, as before."""
    monkeypatch.setattr(config, "SKILL_TOOLS_ENABLED", False)
    assert {spec["name"] for spec in core_tools.get_tool_specs()} == {"home_lights", "camera"}


def test_without_skill_tools_nothing_is_added() -> None:
    """A profile with no rare tools gets no skill tools."""
    camera = _Camera().spec()
    assert skills.fold_into_skills([camera]) == [camera]


@pytest.mark.asyncio
async def test_skill_help_returns_the_full_instructions(tools: dict[str, Tool]) -> None:
    """skill_help gives the tool's own description and argument schema."""
    result = await core_tools.dispatch_tool_call("skill_help", '{"skill": "home_lights"}', _deps())
    assert result == {
        "skill": "home_lights",
        "instructions": _Lights.description,
        "arguments": _Lights.parameters_schema,
    }


@pytest.mark.asyncio
async def test_use_skill_runs_the_tool_with_its_arguments(tools: dict[str, Tool]) -> None:
    """use_skill calls the skill's tool exactly as a direct call would."""
    args = '{"skill": "home_lights", "arguments": {"action": "set", "target": "kitchen"}}'
    assert await core_tools.dispatch_tool_call("use_skill", args, _deps()) == {
        "ok": True,
        "got": {"action": "set", "target": "kitchen"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({}, "missing action"),
        ({"action": "dim"}, "action must be one of"),
        ({"action": "set", "room": "kitchen"}, "unknown argument room"),
        ("set kitchen", "arguments must be an object"),
    ],
)
async def test_a_wrong_use_skill_call_returns_the_instructions(
    tools: dict[str, Tool], arguments: Any, problem: str
) -> None:
    """A guess at the arguments fails safely and teaches the model the skill in the same round trip."""
    result = await core_tools._dispatch_tool_call(
        "use_skill", {"skill": "home_lights", "arguments": arguments}, _deps()
    )
    assert problem in result["error"]
    assert result["instructions"] == _Lights.description and result["arguments"] == _Lights.parameters_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", ["camera", "tv", None])
async def test_only_enabled_skills_can_be_used(tools: dict[str, Tool], skill: Any) -> None:
    """Direct tools, skills that are not enabled and missing names are refused with the list of skills."""
    for tool_name in ("use_skill", "skill_help"):
        result = await core_tools._dispatch_tool_call(tool_name, {"skill": skill}, _deps())
        assert result == {"error": f"unknown skill: {skill}", "skills": ["home_lights"]}
