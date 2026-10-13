"""Tests for the camera tool."""

import base64
import logging
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools.camera import Camera
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


@pytest.mark.asyncio
async def test_camera_tool_returns_base64_of_sdk_jpeg() -> None:
    """The tool base64-encodes the JPEG bytes returned by the SDK."""
    jpeg_bytes = b"\xff\xd8jpeg\xff\xd9"
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = jpeg_bytes

    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What color is this?")

    assert result["b64_im"] == base64.b64encode(jpeg_bytes).decode("utf-8")


@pytest.mark.asyncio
async def test_camera_tool_reports_error_when_no_frame() -> None:
    """With no frame available the tool returns an error."""
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = None

    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What color is this?")

    assert "error" in result


@pytest.mark.asyncio
async def test_camera_tool_reports_error_when_camera_disabled() -> None:
    """With the camera disabled the tool returns an error and never reads a frame."""
    reachy_mini = MagicMock()
    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=False,
    )

    result = await Camera()(deps, question="What color is this?")

    assert "error" in result
    reachy_mini.media.get_frame_jpeg.assert_not_called()


@pytest.mark.asyncio
async def test_camera_tool_does_not_log_question(caplog: pytest.LogCaptureFixture) -> None:
    """Camera observations remain out of application logs."""
    sentinel = "camera-household-secret"
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = b"jpeg"
    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    with caplog.at_level(logging.INFO, logger="reachy_mini_conversation_app.tools.camera"):
        result = await Camera()(deps, question=sentinel)

    assert "b64_im" in result
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_camera_tool_sends_the_downscaled_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool returns the model-sized JPEG rather than the full camera frame."""
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = b"full-frame"
    # Patch the globals the tool really uses; other tests may reload the tools modules.
    monkeypatch.setitem(Camera.__call__.__globals__, "fit_jpeg_for_model", lambda jpeg: b"small-" + jpeg)
    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What do you see?")

    assert result["b64_im"] == base64.b64encode(b"small-full-frame").decode("utf-8")
