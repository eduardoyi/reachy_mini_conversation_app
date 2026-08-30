"""Regression tests for the local head movement tool."""

import asyncio
from unittest.mock import MagicMock

import numpy as np

from reachy_mini_conversation_app.tools.move_head import MoveHead
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove


def test_move_head_uses_head_yaw_and_antenna_joints_from_sdk_tuple() -> None:
    """Head yaw must anchor body interpolation instead of the first antenna angle."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = (
        [0.73, 0.1, -0.2, 0.3, -0.4, 0.5, -0.6],
        [0.11, -0.22],
    )
    movement_manager = MagicMock()
    deps = ToolDependencies(reachy_mini=robot, movement_manager=movement_manager)

    result = asyncio.run(MoveHead()(deps, direction="left"))

    assert result == {"status": "looking left"}
    queued_move = movement_manager.queue_move.call_args.args[0]
    assert isinstance(queued_move, GotoQueueMove)
    assert queued_move.start_body_yaw == 0.73
    assert queued_move.start_antennas == (0.11, -0.22)
    robot.set_target.assert_not_called()
    robot.goto_target.assert_not_called()
