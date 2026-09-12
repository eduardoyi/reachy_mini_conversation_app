import logging
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.profile_store import write_profile
from reachy_mini_conversation_app.profile_toolsets import write_profile_tool_override


@pytest.mark.parametrize("profile_tools", [["remember", "forget"], ["update_memory"], []])
def test_memory_registry_respects_flag_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile_tools: list[str]
) -> None:
    """Correction tools follow the feature flag and the selected profile's memory access."""
    registry = importlib.import_module("reachy_mini_conversation_app.tools.core_tools")
    profiles = tmp_path / "profiles"
    write_profile("memory_test", profiles / "memory_test", "Test profile.", profile_tools)
    monkeypatch.setattr(config, "PROFILES_DIRECTORY", profiles)
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "memory_test")
    monkeypatch.setattr(config, "TOOLS_DIRECTORY", None)
    monkeypatch.setattr(config, "AUTOLOAD_EXTERNAL_TOOLS", False)
    monkeypatch.setattr(registry, "_TOOLS_INSTANCE_PATH", tmp_path)
    monkeypatch.setattr(registry, "_TOOLS_SIGNATURE", None)
    monkeypatch.setattr(registry, "ALL_TOOLS", {})

    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
    assert "update_memory" not in registry.get_tools()
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    assert ("update_memory" in registry.get_tools()) == bool(profile_tools)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
    assert "update_memory" not in registry.get_tools()

    write_profile_tool_override("memory_test", ["remember", "forget"], tmp_path)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    assert "update_memory" not in registry.get_tools()
    write_profile_tool_override("memory_test", ["remember", "forget", "update_memory"], tmp_path)
    registry.initialize_tools(force=True)
    assert "update_memory" in registry.get_tools()
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
    assert "update_memory" not in registry.get_tools()


@pytest.mark.asyncio
async def test_malformed_memory_arguments_do_not_leak_into_dispatch_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Even malformed tool arguments must keep household content out of logs."""
    registry = importlib.import_module("reachy_mini_conversation_app.tools.core_tools")
    monkeypatch.setattr(registry, "get_tools", lambda: {})
    private_text = "synthetic-household-private-sentinel"
    with caplog.at_level(logging.WARNING):
        await registry.dispatch_tool_call("remember", '{"fact": "' + private_text, MagicMock())
    assert private_text not in caplog.text
    assert "not valid JSON" in caplog.text
