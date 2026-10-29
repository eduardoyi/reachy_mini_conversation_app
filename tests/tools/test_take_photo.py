from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools import core_tools, take_photo
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.whatsapp import SendResult
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.take_photo import TakePhoto


GROUP = "120363429899172191@g.us"


class _WhatsApp:
    def __init__(self, result: SendResult) -> None:
        self.result = result
        self.sent: list[str] = []

    async def send_image(self, chat: str, jpeg: bytes) -> SendResult:
        self.sent.append(chat)
        return self.result


@pytest.fixture
def photo(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Configure the group and record the countdown wait instead of sleeping."""
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(config, "WHATSAPP_PHOTOS_CHAT", GROUP)
    monkeypatch.setattr(take_photo.asyncio, "sleep", wait)
    return waits


def _deps(whatsapp: Any, camera: bool = True) -> ToolDependencies:
    return ToolDependencies(
        reachy_mini=MagicMock(), movement_manager=MagicMock(), camera_enabled=camera, whatsapp=whatsapp
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "status"),
    [(SendResult.SENT, "sent"), (SendResult.UNSURE, "unsure"), (SendResult.FAILED, "not_sent")],
)
async def test_the_photo_is_taken_after_the_countdown_and_sent_to_the_group(
    photo: list[float], monkeypatch: pytest.MonkeyPatch, result: SendResult, status: str
) -> None:
    """Gemini counts down while the tool waits 3 s; the photo goes to the group and the outcome comes back."""

    async def share(deps: Any, chat: str) -> SendResult:
        return await deps.whatsapp.send_image(chat, b"jpeg")

    monkeypatch.setattr(take_photo, "take_and_share", share)
    whatsapp = _WhatsApp(result)

    reply = await TakePhoto()(_deps(whatsapp))

    assert photo == [take_photo.CAMERA_COUNTDOWN_S]
    assert reply["status"] == status and "instruction" in reply
    assert whatsapp.sent == [GROUP]


@pytest.mark.asyncio
async def test_no_picture_from_the_camera_sends_nothing(photo: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    """The camera gave nothing: an error to pass on kindly, and nothing sent."""

    async def no_picture(deps: Any, chat: str) -> None:
        return None

    monkeypatch.setattr(take_photo, "take_and_share", no_picture)
    whatsapp = _WhatsApp(SendResult.SENT)
    assert "error" in await TakePhoto()(_deps(whatsapp))
    assert whatsapp.sent == []


@pytest.mark.asyncio
async def test_the_tool_needs_whatsapp_and_the_camera(photo: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the link, the group or the camera it says so and takes nothing."""
    assert "error" in await TakePhoto()(_deps(None))
    assert "error" in await TakePhoto()(_deps(_WhatsApp(SendResult.SENT), camera=False))
    monkeypatch.setattr(config, "WHATSAPP_PHOTOS_CHAT", None)
    assert "error" in await TakePhoto()(_deps(_WhatsApp(SendResult.SENT)))
    assert photo == []


@pytest.mark.parametrize("configured", [True, False])
def test_the_tool_is_offered_only_with_whatsapp_photos_set_up(
    monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    """take_photo appears with the three WhatsApp settings and is removed without them."""
    monkeypatch.setattr(config, "WHATSAPP_API_URL", "http://127.0.0.1:8792" if configured else None)
    monkeypatch.setattr(config, "WHATSAPP_API_AUTH", "reachy:secret")
    monkeypatch.setattr(config, "WHATSAPP_PHOTOS_CHAT", GROUP)
    monkeypatch.setattr(config, "AUTOLOAD_EXTERNAL_TOOLS", False)
    monkeypatch.setattr(core_tools, "read_profile_tool_names", lambda *_args: ["camera", "take_photo"])
    monkeypatch.setattr(core_tools, "read_profile_tool_override", lambda *_args: None)

    assert ("take_photo" in core_tools._read_profile_tool_names(None)) is configured
