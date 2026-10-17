from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.go_to_sleep import GoToSleep


def test_go_to_sleep_has_no_required_arguments() -> None:
    """The tool should be callable without a confirmation argument."""
    assert GoToSleep.parameters_schema == {
        "type": "object",
        "properties": {},
        "required": [],
    }


@pytest.mark.asyncio
async def test_go_to_sleep_returns_unavailable_without_runtime_callback() -> None:
    """The tool should fail gracefully if the runtime did not inject a sleep callback."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    result = await GoToSleep()(deps)

    assert result == {"error": "go_to_sleep is unavailable in this runtime"}


@pytest.mark.asyncio
async def test_go_to_sleep_calls_runtime_callback() -> None:
    """The tool should delegate the actual movement and app stop to the host runtime."""
    expected = {
        "status": "sleeping",
        "stop_current_app_requested": True,
        "local_stop_requested": True,
    }
    go_to_sleep = MagicMock(return_value=expected)
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        go_to_sleep=go_to_sleep,
    )

    result = await GoToSleep()(deps)

    assert result == expected
    go_to_sleep.assert_called_once_with()


@pytest.mark.asyncio
async def test_go_to_sleep_prefers_standby_and_turn_off_always_stops_the_app() -> None:
    """With the hybrid runtime, "go to sleep" is standby; turn_off keeps the old sleep-and-stop."""
    from reachy_mini_conversation_app.tools.turn_off import TurnOff

    standby = MagicMock(return_value={"status": "sleeping", "wakes_on_name": True})
    stop_app = MagicMock(return_value={"status": "sleeping", "local_stop_requested": True})
    deps = ToolDependencies(
        reachy_mini=MagicMock(), movement_manager=MagicMock(), go_to_sleep=stop_app, standby=standby
    )

    assert await GoToSleep()(deps) == {"status": "sleeping", "wakes_on_name": True}
    stop_app.assert_not_called()
    assert await TurnOff()(deps) == {"status": "sleeping", "local_stop_requested": True}
    stop_app.assert_called_once_with()
    assert await TurnOff()(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())) == {
        "error": "turn_off is unavailable in this runtime"
    }


@pytest.mark.asyncio
async def test_end_conversation_asks_the_runtime_to_close() -> None:
    """end_conversation delegates to the runtime and fails gracefully without one."""
    from reachy_mini_conversation_app.tools.end_conversation import EndConversation

    end = MagicMock(return_value={"status": "ending"})
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), end_conversation=end)

    assert await EndConversation()(deps) == {"status": "ending"}
    assert await EndConversation()(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())) == {
        "error": "end_conversation is unavailable in this runtime"
    }


@pytest.mark.parametrize(("hybrid", "expected"), [(True, True), (False, False)])
def test_standby_tools_join_go_to_sleep_with_the_hybrid_runtime(
    monkeypatch: pytest.MonkeyPatch, hybrid: bool, expected: bool
) -> None:
    """end_conversation and turn_off load next to go_to_sleep only when standby exists."""
    from reachy_mini_conversation_app.tools import core_tools
    from reachy_mini_conversation_app.config import config

    monkeypatch.setattr(config, "HYBRID_RUNTIME_ENABLED", hybrid)
    monkeypatch.setattr(config, "AUTOLOAD_EXTERNAL_TOOLS", False)
    monkeypatch.setattr(core_tools, "read_profile_tool_names", lambda *_args: ["go_to_sleep", "end_conversation"])
    monkeypatch.setattr(core_tools, "read_profile_tool_override", lambda *_args: None)

    names = core_tools._read_profile_tool_names(None)

    assert all((name in names) is expected for name in core_tools.STANDBY_TOOLS)
