"""Tests for local action execution boundaries."""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock

import pytest

from reachy_mini_conversation_app.local_actions import (
    LocalActionResult,
    LocalActionStatus,
    LocalActionExecutor,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.local_intent_router import (
    IntentRoute,
    LocalIntent,
    LocalIntentDecision,
    IntentDecisionReason,
)


class MovementManagerFake:
    """Record movement-manager calls without exposing robot APIs."""

    def __init__(self) -> None:
        """Initialize call counters."""
        self.clear_move_queue_calls = 0
        self.head_tracking_values: list[bool] = []

    def clear_move_queue(self) -> None:
        """Record a request to clear queued moves."""
        self.clear_move_queue_calls += 1

    def set_head_tracking(self, enabled: bool) -> None:
        """Record a head-tracking toggle."""
        self.head_tracking_values.append(enabled)


@pytest.fixture
def dependencies() -> ToolDependencies:
    """Return dependencies whose robot object can detect accidental raw calls."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MovementManagerFake())


def local_decision(action: LocalIntent) -> LocalIntentDecision:
    """Build a successful local decision for an action."""
    return LocalIntentDecision(
        route=IntentRoute.LOCAL_COMMAND,
        action=action,
        normalized_text=action.value,
        input_mode="wake_stripped",
        wake_word_detected=False,
        reason=IntentDecisionReason.EXACT_MATCH,
    )


def test_stop_uses_movement_manager_and_never_calls_robot(dependencies: ToolDependencies) -> None:
    """The default stop path only clears the existing movement queue."""
    executor = LocalActionExecutor(dependencies)

    result = asyncio.run(executor.execute(local_decision(LocalIntent.STOP)))

    assert result.status is LocalActionStatus.EXECUTED
    assert result.succeeded
    assert isinstance(dependencies.movement_manager, MovementManagerFake)
    assert dependencies.movement_manager.clear_move_queue_calls == 1
    dependencies.reachy_mini.assert_not_called()


def test_stop_can_use_an_async_callback(dependencies: ToolDependencies) -> None:
    """Stop callbacks may perform asynchronous playback/interruption work."""
    callback = AsyncMock()
    executor = LocalActionExecutor(dependencies, stop_callback=callback)

    result = asyncio.run(executor.execute(local_decision(LocalIntent.STOP)))

    assert result.succeeded
    callback.assert_awaited_once_with()
    assert dependencies.movement_manager.clear_move_queue_calls == 0


def test_be_quiet_uses_silence_callback_and_never_calls_robot(dependencies: ToolDependencies) -> None:
    """Quiet uses the caller-owned playback silence boundary."""
    callback = MagicMock()
    executor = LocalActionExecutor(dependencies, silence_callback=callback)

    result = asyncio.run(executor.execute(local_decision(LocalIntent.BE_QUIET)))

    assert result.status is LocalActionStatus.EXECUTED
    callback.assert_called_once_with()
    dependencies.reachy_mini.assert_not_called()


def test_be_quiet_reports_missing_silence_callback(dependencies: ToolDependencies) -> None:
    """A quiet command fails clearly instead of pretending playback stopped."""
    executor = LocalActionExecutor(dependencies)

    result = asyncio.run(executor.execute(local_decision(LocalIntent.BE_QUIET)))

    assert result.status is LocalActionStatus.FAILED
    assert result.error_type == "silence_callback_unavailable"
    dependencies.reachy_mini.assert_not_called()


@pytest.mark.parametrize(
    ("action", "direction"),
    [(LocalIntent.LOOK_LEFT, "left"), (LocalIntent.LOOK_RIGHT, "right")],
)
def test_look_actions_reuse_move_head_tool(
    dependencies: ToolDependencies,
    monkeypatch: pytest.MonkeyPatch,
    action: LocalIntent,
    direction: str,
) -> None:
    """Directional actions delegate to the existing MoveHead tool contract."""
    tool_call = AsyncMock(return_value={"status": f"looking {direction}"})
    monkeypatch.setattr("reachy_mini_conversation_app.local_actions.MoveHead.__call__", tool_call)
    executor = LocalActionExecutor(dependencies)

    result = asyncio.run(executor.execute(local_decision(action)))

    assert result.succeeded
    tool_call.assert_awaited_once_with(dependencies, direction=direction)


def test_look_at_me_reuses_head_tracking_tool(
    dependencies: ToolDependencies,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Look-at-me delegates to the existing head-tracking tool contract."""
    tool_call = AsyncMock(return_value={"status": "following"})
    monkeypatch.setattr("reachy_mini_conversation_app.local_actions.HeadTracking.__call__", tool_call)
    executor = LocalActionExecutor(dependencies)

    result = asyncio.run(executor.execute(local_decision(LocalIntent.LOOK_AT_ME)))

    assert result.succeeded
    tool_call.assert_awaited_once_with(dependencies, enabled=True)


@pytest.mark.parametrize("action", list(LocalIntent))
def test_existing_tool_error_becomes_failure_result(
    dependencies: ToolDependencies,
    monkeypatch: pytest.MonkeyPatch,
    action: LocalIntent,
) -> None:
    """Existing tool error payloads are converted into typed failures."""
    if action is LocalIntent.STOP:
        callback = MagicMock(side_effect=RuntimeError("movement failed"))
        executor = LocalActionExecutor(dependencies, stop_callback=callback)
    elif action is LocalIntent.BE_QUIET:
        callback = MagicMock(side_effect=RuntimeError("playback failed"))
        executor = LocalActionExecutor(dependencies, silence_callback=callback)
    else:
        tool_call = AsyncMock(return_value={"error": "tool failure details"})
        class_name = "MoveHead" if action in {LocalIntent.LOOK_LEFT, LocalIntent.LOOK_RIGHT} else "HeadTracking"
        monkeypatch.setattr(f"reachy_mini_conversation_app.local_actions.{class_name}.__call__", tool_call)
        executor = LocalActionExecutor(dependencies)

    result = asyncio.run(executor.execute(local_decision(action)))

    assert result.status is LocalActionStatus.FAILED
    assert result.action is action
    assert result.error_type in {"RuntimeError", "tool_error"}
    assert "tool failure details" not in result.detail


def test_nonlocal_decision_is_not_executed(dependencies: ToolDependencies) -> None:
    """Escalation decisions cannot accidentally invoke local actions."""
    decision = LocalIntentDecision(
        route=IntentRoute.ESCALATE,
        action=None,
        normalized_text="turn on the kitchen lights",
        input_mode="wake_stripped",
        wake_word_detected=False,
        reason=IntentDecisionReason.UNSUPPORTED_COMMAND,
    )
    executor = LocalActionExecutor(dependencies, silence_callback=MagicMock())

    result = asyncio.run(executor.execute(decision))

    assert result.status is LocalActionStatus.NOT_LOCAL
    assert not result.succeeded
    assert dependencies.movement_manager.clear_move_queue_calls == 0
    dependencies.reachy_mini.assert_not_called()


def test_callback_cancellation_propagates_and_does_not_report_success(dependencies: ToolDependencies) -> None:
    """Cancellation remains cancellation so the owner can abort cleanly."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release() -> None:
        started.set()
        await release.wait()

    executor = LocalActionExecutor(dependencies, silence_callback=wait_for_release)

    async def run_and_cancel() -> None:
        task = asyncio.create_task(executor.execute(local_decision(LocalIntent.BE_QUIET)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())


def test_tool_exception_becomes_failure_result(
    dependencies: ToolDependencies,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected tool exceptions do not escape into the conversation loop."""
    tool_call = AsyncMock(side_effect=RuntimeError("robot details"))
    monkeypatch.setattr("reachy_mini_conversation_app.local_actions.MoveHead.__call__", tool_call)
    executor = LocalActionExecutor(dependencies)

    result = asyncio.run(executor.execute(local_decision(LocalIntent.LOOK_LEFT)))

    assert result.status is LocalActionStatus.FAILED
    assert result.error_type == "RuntimeError"
    assert "robot details" not in result.detail


def test_result_is_frozen_typed_metadata() -> None:
    """Action results cannot be mutated after crossing the execution boundary."""
    result = LocalActionResult(
        action=LocalIntent.STOP,
        status=LocalActionStatus.EXECUTED,
        detail="local action completed",
    )

    with pytest.raises(FrozenInstanceError):
        result.status = LocalActionStatus.FAILED  # type: ignore[misc]
