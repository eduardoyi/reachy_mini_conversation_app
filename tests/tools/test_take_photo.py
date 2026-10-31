from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools import core_tools, take_photo
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.whatsapp import SendResult
from reachy_mini_conversation_app.photographer import PHOTOS_GROUP
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.take_photo import TakePhoto


GROUP = "120363429899172191@g.us"
SERGIO = "120363413010779824@g.us"


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
    logged_as: list[str] = []

    async def share(deps: Any, chat: str, who: str) -> SendResult:
        logged_as.append(who)
        return await deps.whatsapp.send_image(chat, b"jpeg")

    monkeypatch.setattr(take_photo, "take_and_share", share)
    whatsapp = _WhatsApp(result)

    reply = await TakePhoto()(_deps(whatsapp))

    assert photo == [take_photo.CAMERA_COUNTDOWN_S]
    assert reply["status"] == status and "instruction" in reply
    assert whatsapp.sent == [GROUP] and logged_as == [PHOTOS_GROUP]


@pytest.mark.asyncio
async def test_no_picture_from_the_camera_sends_nothing(photo: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    """The camera gave nothing: an error to pass on kindly, and nothing sent."""

    async def no_picture(deps: Any, chat: str, who: str) -> None:
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


@pytest.mark.asyncio
async def test_a_photo_for_a_family_member_goes_to_their_group(
    photo: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """`to` names a contact: the photo goes to that group, framed, with speaker turns held meanwhile."""
    import json

    contacts = tmp_path / "contacts.json"
    contacts.write_text(json.dumps({"contacts": [{"name": "Tío Sergio", "chat": SERGIO, "say": ["sergio"]}]}))
    monkeypatch.setattr(config, "WHATSAPP_CONTACTS_FILE", str(contacts))
    chats: list[tuple[str, str]] = []

    async def share(deps: Any, chat: str, who: str) -> SendResult:
        chats.append((chat, who))
        return SendResult.SENT

    monkeypatch.setattr(take_photo, "take_and_share", share)
    deps = _deps(_WhatsApp(SendResult.SENT))

    assert (await TakePhoto()(deps, to="el tío Sergio"))["status"] == "sent"
    assert chats == [(SERGIO, "Tío Sergio")]
    assert [call.args for call in deps.movement_manager.frame_faces.call_args_list] == [(True,), (False,)]

    unknown = await TakePhoto()(deps, to="Grandpa")
    assert "error" in unknown and "Tío Sergio" in unknown["error"]
    assert chats == [(SERGIO, "Tío Sergio")] and photo == [
        take_photo.CAMERA_COUNTDOWN_S
    ]  # nothing taken for an unknown name
