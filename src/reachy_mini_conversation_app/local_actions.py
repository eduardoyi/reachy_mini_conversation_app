"""Execute allowlisted local intents through existing app boundaries."""

import asyncio
import inspect
import logging
from enum import Enum
from typing import TypeAlias
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

from reachy_mini_conversation_app.tools.move_head import MoveHead
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.local_intent_router import (
    IntentRoute,
    LocalIntent,
    LocalIntentDecision,
)
from reachy_mini_conversation_app.tools.head_tracking import HeadTracking


logger = logging.getLogger(__name__)

ActionCallback: TypeAlias = Callable[[], None | Awaitable[None]]


class LocalActionStatus(str, Enum):
    """Outcome categories returned by the local action executor."""

    EXECUTED = "executed"
    FAILED = "failed"
    NOT_LOCAL = "not_local"


@dataclass(frozen=True, slots=True)
class LocalActionResult:
    """Immutable metadata describing one local action attempt."""

    action: LocalIntent | None
    status: LocalActionStatus
    detail: str
    error_type: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether the requested action completed successfully."""
        return self.status is LocalActionStatus.EXECUTED

    @property
    def failed(self) -> bool:
        """Return whether a local action was selected but could not complete."""
        return self.status is LocalActionStatus.FAILED


_LOOK_ACTIONS: dict[LocalIntent, tuple[Tool, dict[str, object]]] = {
    LocalIntent.LOOK_LEFT: (MoveHead(), {"direction": "left"}),
    LocalIntent.LOOK_RIGHT: (MoveHead(), {"direction": "right"}),
    LocalIntent.LOOK_AT_ME: (HeadTracking(), {"enabled": True}),
}


class LocalActionExecutor:
    """Run local intents without bypassing existing tools or movement ownership."""

    def __init__(
        self,
        dependencies: ToolDependencies,
        *,
        stop_callback: ActionCallback | None = None,
        silence_callback: ActionCallback | None = None,
    ) -> None:
        """Configure tool dependencies and callbacks for non-tool actions."""
        self._dependencies = dependencies
        self._stop_callback = stop_callback if stop_callback is not None else self._clear_movement_queue
        self._silence_callback = silence_callback

    async def execute(self, decision: LocalIntentDecision) -> LocalActionResult:
        """Execute one local decision or report that it must escalate."""
        if decision.route is not IntentRoute.LOCAL_COMMAND or decision.action is None:
            logger.info("Local action skipped; decision requires escalation")
            return LocalActionResult(
                action=decision.action,
                status=LocalActionStatus.NOT_LOCAL,
                detail="decision requires cloud escalation",
            )

        action = decision.action
        try:
            if action is LocalIntent.STOP:
                await self._invoke_callback(self._stop_callback)
            elif action is LocalIntent.BE_QUIET:
                if self._silence_callback is None:
                    return self._failure(action, "silence callback is not configured", "silence_callback_unavailable")
                await self._invoke_callback(self._silence_callback)
            else:
                tool_and_arguments = _LOOK_ACTIONS.get(action)
                if tool_and_arguments is None:
                    return self._failure(action, "no local executor is registered", "unsupported_action")
                tool, arguments = tool_and_arguments
                result = await tool(self._dependencies, **arguments)
                if "error" in result:
                    return self._failure(action, "existing tool reported an error", "tool_error")
        except asyncio.CancelledError:
            logger.info("Local action cancelled: %s", action.value)
            raise
        except Exception as error:
            return self._failure(action, "local action raised an exception", type(error).__name__)

        logger.info("Local action executed: %s", action.value)
        return LocalActionResult(
            action=action,
            status=LocalActionStatus.EXECUTED,
            detail="local action completed",
        )

    def _clear_movement_queue(self) -> None:
        self._dependencies.movement_manager.clear_move_queue()

    @staticmethod
    async def _invoke_callback(callback: ActionCallback) -> None:
        result = callback()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _failure(action: LocalIntent, detail: str, error_type: str) -> LocalActionResult:
        logger.warning("Local action failed: %s (%s)", action.value, error_type)
        return LocalActionResult(
            action=action,
            status=LocalActionStatus.FAILED,
            detail=detail,
            error_type=error_type,
        )


__all__ = [
    "ActionCallback",
    "LocalActionExecutor",
    "LocalActionResult",
    "LocalActionStatus",
]
