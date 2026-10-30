"""Tests for call_person, Gemini's way to start a WhatsApp call through Reachy."""

import json
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app import quick_commands
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.call_person import CallPerson


class _Faces:
    def __init__(self, *person_ids: str) -> None:
        self.person_ids = person_ids

    def identify_now(self) -> list[Any]:
        return [MagicMock(person_id=person_id) for person_id in self.person_ids]


@pytest.fixture
def approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """One approved person; the hand-off records the requests it is given."""
    path = tmp_path / "calls.json"
    path.write_text(json.dumps({"approved": [{"name": "Eduardo", "phone": "+15550100000", "say": ["papa"]}]}))
    monkeypatch.setattr(config, "WHATSAPP_CALLS_APPROVED_FILE", str(path))
    monkeypatch.setattr(config, "WHATSAPP_CALLERS", frozenset({"eduardo", "adriana"}))
    monkeypatch.setattr("reachy_mini_conversation_app.tools.call_person.CALLER_CHECK_S", 0.05)
    monkeypatch.setattr("reachy_mini_conversation_app.tools.call_person.CALLER_CHECK_INTERVAL_S", 0.01)
    return []


def _deps(requests: list[str], *faces: str) -> ToolDependencies:
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    deps.face_recognition = _Faces(*faces)

    def request(text: str) -> bool:
        requests.append(text)
        return True

    deps.request_after_close = request
    return deps


@pytest.mark.asyncio
async def test_a_parent_asking_hands_the_call_to_the_call_command(approved: list[str]) -> None:
    """Adriana in front of the camera: the conversation closes and the call is asked for in Spanish."""
    result = await CallPerson()(_deps(approved, "adriana"), name="papá", language="es")
    assert result["status"] == "calling"
    assert approved == ["Reachy, llama a Eduardo"]


@pytest.mark.asyncio
async def test_a_kid_cannot_start_a_call_through_gemini(approved: list[str]) -> None:
    """Enzo asking: an error for Gemini to relay, and nothing is handed off."""
    result = await CallPerson()(_deps(approved, "enzo"), name="Eduardo")
    assert "Only Eduardo or Adriana" in result["error"]
    assert approved == []


@pytest.mark.asyncio
async def test_someone_not_on_the_list_is_not_called(approved: list[str]) -> None:
    """A name not on the call list: Gemini hears who can be called."""
    result = await CallPerson()(_deps(approved, "eduardo"), name="grandma")
    assert "You can call: Eduardo" in result["error"]
    assert approved == []


@pytest.mark.asyncio
async def test_without_the_runtime_there_are_no_calls(approved: list[str]) -> None:
    """No hand-off wired (no hybrid runtime): calls are not set up."""
    deps = _deps(approved, "eduardo")
    deps.request_after_close = None
    assert "not set up" in (await CallPerson()(deps, name="Eduardo"))["error"]


def test_the_check_window_is_the_call_command_s() -> None:
    """Gemini's check waits as long as the call command's."""
    from reachy_mini_conversation_app.tools import call_person

    assert call_person.CALLER_CHECK_S == quick_commands.CALLER_CHECK_S
