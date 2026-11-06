"""Tests for send_voice_message, Gemini's way to have Reachy record and send a WhatsApp voice message."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app import quick_commands
from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.send_voice_message import SendVoiceMessage, message_request


@pytest.fixture
def groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """One family group; the hand-off records the requests it is given."""
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps({"contacts": [{"name": "Tío Sergio", "chat": "1203630@g.us", "say": ["sergio"]}]}))
    monkeypatch.setattr(config, "WHATSAPP_CONTACTS_FILE", str(path))
    return []


def _deps(requests: list[str]) -> ToolDependencies:
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    def request(text: str) -> bool:
        requests.append(text)
        return True

    deps.request_after_close = request
    return deps


@pytest.mark.asyncio
async def test_asking_for_a_message_hands_it_to_the_message_command(groups: list[str]) -> None:
    """2026-11-06: asked inside a conversation, Gemini said it could not send messages; now it hands it over."""
    result = await SendVoiceMessage()(_deps(groups), to="Sergio", language="es")
    assert result["status"] == "recording"
    assert groups == ["Reachy, manda un mensaje a Tío Sergio"]


@pytest.mark.asyncio
async def test_someone_not_in_the_groups_gets_no_message(groups: list[str]) -> None:
    """A name that is not a group: Gemini hears who can get one, and nothing is handed off."""
    result = await SendVoiceMessage()(_deps(groups), to="grandma")
    assert "You can send a message to: Tío Sergio" in result["error"]
    assert groups == []


@pytest.mark.asyncio
async def test_without_the_runtime_there_are_no_voice_messages(groups: list[str]) -> None:
    """No hand-off wired (no hybrid runtime): voice messages are not set up."""
    deps = _deps(groups)
    deps.request_after_close = None
    assert "not set up" in (await SendVoiceMessage()(deps, to="Sergio"))["error"]


@pytest.mark.parametrize("language", ["en", "es", "it", "pt"])
def test_the_handed_over_request_is_one_the_message_command_understands(language: str) -> None:
    """Each language's request parses as a voice message for that group."""
    text = message_request(language, "Tío Sergio").removeprefix("Reachy, ")
    command = quick_commands.parse(text)
    assert command is not None
    assert (command.kind, command.language, command.args["to"]) == ("message", language, "tio sergio")


@pytest.mark.parametrize(("hybrid", "offered"), [(True, True), (False, False)])
def test_offered_only_with_the_hybrid_runtime_and_whatsapp(
    monkeypatch: pytest.MonkeyPatch, hybrid: bool, offered: bool
) -> None:
    """The message command records through the hybrid runtime, so without it Gemini is not offered the tool."""
    monkeypatch.setattr(config, "HYBRID_RUNTIME_ENABLED", hybrid)
    monkeypatch.setattr(config, "QUICK_COMMANDS_ENABLED", True)
    monkeypatch.setattr(core_tools, "whatsapp_photos_configured", lambda: True)
    assert ("send_voice_message" in core_tools._read_profile_tool_names(None)) is offered
