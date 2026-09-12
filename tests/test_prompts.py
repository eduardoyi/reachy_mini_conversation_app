import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import reachy_mini_conversation_app.openai_live as live_module
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.prompts import (
    HOUSEHOLD_MEMORY_CONVERSATION_POLICY,
    get_session_instructions,
)
from reachy_mini_conversation_app.openai_live import OpenAILiveHandler
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore, memory_path_for_instance
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.openai_live_backend import OpenAILiveDelegationBackend


def test_enabled_empty_memory_includes_unknown_person_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled memory policy remains active before any assigned note exists."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", None)

    instructions = get_session_instructions(tmp_path)

    assert instructions.startswith(HOUSEHOLD_MEMORY_CONVERSATION_POLICY)
    assert "quietly skip durable storage" in instructions
    assert "do not ask who someone is merely to save an ordinary detail" in instructions
    assert "do not mention memory mechanics" in instructions
    assert "explicitly asks you to remember" in instructions


def test_enabled_unassigned_memory_keeps_policy_without_injecting_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown migrated notes stay out of context while the conversation rule remains present."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "facts": [{"id": "m_unknown", "text": "UNASSIGNED_NOTE", "createdAt": 1_000}]}),
        encoding="utf-8",
    )
    HouseholdMemoryStore(tmp_path).migrate_v1()

    instructions = get_session_instructions(tmp_path)

    assert HOUSEHOLD_MEMORY_CONVERSATION_POLICY in instructions
    assert "UNASSIGNED_NOTE" not in instructions


def test_enabled_policy_is_shared_by_live_and_luna_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both provider instruction paths receive the same policy and assigned memory."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", None)
    monkeypatch.setattr(live_module.core_tools, "get_tool_specs", lambda: [])
    stored = HouseholdMemoryStore(tmp_path).remember("Eduardo likes jazz", subject_id="person:eduardo")
    shared = get_session_instructions(tmp_path)

    handler = OpenAILiveHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        instance_path=str(tmp_path),
        startup_voice="marin",
    )
    live_instructions = handler._build_live_instructions()
    luna_backend = OpenAILiveDelegationBackend(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        client=MagicMock(),
        instructions=shared,
    )

    for instructions in (live_instructions, luna_backend._instructions):
        assert HOUSEHOLD_MEMORY_CONVERSATION_POLICY in instructions
        assert stored.text in instructions
        assert "backend, which must call the forget tool" in instructions
        assert (
            "Only confirm that a saved fact was forgotten after the forget tool reports successful removal"
            in instructions
        )


def test_disabled_legacy_memory_does_not_add_household_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off v1 prompts keep the legacy memory wording and behavior."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", None)
    path = memory_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "facts": [{"id": "m_legacy", "text": "Likes tea", "createdAt": 1_000}]}),
        encoding="utf-8",
    )

    instructions = get_session_instructions(tmp_path)

    assert instructions.startswith("Things you remember about the user")
    assert "- Likes tea" in instructions
    assert HOUSEHOLD_MEMORY_CONVERSATION_POLICY not in instructions
