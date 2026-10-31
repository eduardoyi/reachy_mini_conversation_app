"""The app's loopback control API for the admin app: token, privacy, camera stream, head, sleep and wake."""

import io
import sys
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from reachy_mini_conversation_app import control_api, camera_frames
from reachy_mini_conversation_app.privacy import PrivacySwitch
from reachy_mini_conversation_app.control_api import PEEK_YAW_STEP, PEEK_PITCH_STEP, ControlApi, load_or_create_token


TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _jpeg(width: int = 1280, height: int = 720) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (90, 120, 150)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PrivacySwitch:
    """Give the control API and the camera a fresh privacy switch, off."""
    privacy = PrivacySwitch(tmp_path / "privacy.json")
    monkeypatch.setattr(control_api, "PRIVACY", privacy)
    monkeypatch.setattr(camera_frames, "PRIVACY", privacy)
    return privacy


def _api(*, sleeping: bool = False, media: Any = None, faces: Any = None) -> tuple[ControlApi, Any, Any]:
    movement = MagicMock()
    movement.sleeping = sleeping
    runtime = MagicMock()
    runtime.mode.value = "local_ambient"
    deps = SimpleNamespace(
        reachy_mini=SimpleNamespace(media=media),
        movement_manager=movement,
        face_recognition=faces,
        calls=SimpleNamespace(linked=True),
        camera_enabled=True,
    )
    return ControlApi(deps, runtime, TOKEN), movement, runtime  # type: ignore[arg-type]


def _client(api: ControlApi, host: str = "127.0.0.1") -> TestClient:
    return TestClient(api.app, base_url="http://127.0.0.1:7861", client=(host, 50000))


@pytest.mark.parametrize(
    ("headers", "host"),
    [
        ({}, "127.0.0.1"),
        ({"Authorization": "Bearer wrong"}, "127.0.0.1"),
        ({"Authorization": "Bearer \u00e9".encode("latin-1")}, "127.0.0.1"),
        (AUTH, "192.168.1.20"),
    ],
)
def test_requests_need_this_computer_and_the_token(switch: PrivacySwitch, headers: dict[str, Any], host: str) -> None:
    """Requests need this computer and the token."""
    api, movement, _ = _api()
    client = _client(api, host)
    assert client.get("/status", headers=headers).status_code == 403
    assert client.post("/privacy", json={"on": True}, headers=headers).status_code == 403
    assert client.get("/camera", headers=headers).status_code == 403
    assert switch.on is False
    movement.go_to_sleep.assert_not_called()


def test_status_reports_mode_sleep_privacy_and_calls(switch: PrivacySwitch) -> None:
    """Status reports mode sleep privacy and calls."""
    api, _, _ = _api(sleeping=True)
    status = _client(api).get("/status", headers=AUTH).json()
    assert status["mode"] == "local_ambient"
    assert status["sleeping"] is True
    assert status["privacy"] == {"on": False, "since": None}
    assert status["calls_linked"] is True


def test_privacy_switches_and_the_camera_is_refused_while_it_is_on(switch: PrivacySwitch) -> None:
    """Privacy switches and the camera is refused while it is on."""
    api, _, _ = _api(media=SimpleNamespace(get_frame_jpeg=MagicMock(return_value=_jpeg())))
    client = _client(api)
    assert client.post("/privacy", json={"on": "yes"}, headers=AUTH).status_code == 400
    response = client.post("/privacy", json={"on": True}, headers=AUTH)
    assert response.status_code == 200 and response.json()["privacy"]["on"] is True
    assert switch.on is True
    assert client.get("/camera", headers=AUTH).status_code == 409
    assert camera_frames.capture_jpeg(api._deps.reachy_mini.media) is None


def test_the_camera_streams_small_jpeg_frames_and_stops_when_privacy_comes_on(
    switch: PrivacySwitch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The camera streams small jpeg frames and stops when privacy comes on."""
    monkeypatch.setattr(control_api, "STREAM_FRAME_S", 0.0)
    calls = 0

    def get_frame_jpeg() -> bytes:
        nonlocal calls
        calls += 1
        if calls == 3:
            switch.set(True)
        return _jpeg()

    api, _, _ = _api(media=SimpleNamespace(get_frame_jpeg=get_frame_jpeg))
    with _client(api).stream("GET", "/camera", headers=AUTH) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
        body = response.read()
    frames = body.split(b"--frame\r\n")[1:]
    assert 1 <= len(frames) <= 3
    jpeg = frames[0].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
    with Image.open(io.BytesIO(jpeg)) as image:
        assert max(image.size) == control_api.STREAM_MAX_SIDE


def test_while_asleep_the_head_lifts_and_turns_in_steps_without_waking(switch: PrivacySwitch) -> None:
    """While asleep the head lifts and turns in steps without waking."""
    api, movement, _ = _api(sleeping=True)
    client = _client(api)
    assert client.post("/look-up", headers=AUTH).status_code == 200
    movement.peek.assert_called_with(0.0, 0.0, control_api.PEEK_HOLD_S)
    client.post("/head", json={"direction": "left"}, headers=AUTH)
    client.post("/head", json={"direction": "up"}, headers=AUTH)
    movement.peek.assert_called_with(PEEK_YAW_STEP, -PEEK_PITCH_STEP, control_api.PEEK_HOLD_S)
    for _ in range(10):
        client.post("/head", json={"direction": "right"}, headers=AUTH)
    assert movement.peek.call_args.args[0] == -60.0
    client.post("/head", json={"direction": "center"}, headers=AUTH)
    movement.peek.assert_called_with(0.0, 0.0, control_api.PEEK_HOLD_S)
    movement.wake_up.assert_not_called()
    assert client.post("/head", json={"direction": "spin"}, headers=AUTH).status_code == 400


def test_while_awake_the_head_turns_like_the_move_head_tool(
    switch: PrivacySwitch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While awake the head turns like the move head tool."""
    turns: list[str] = []

    class FakeMoveHead:
        async def __call__(self, deps: Any, **kwargs: Any) -> dict[str, Any]:
            turns.append(kwargs["direction"])
            return {"status": f"looking {kwargs['direction']}"}

    monkeypatch.setattr(control_api, "MoveHead", FakeMoveHead)
    api, movement, _ = _api(sleeping=False)
    client = _client(api)
    assert client.post("/head", json={"direction": "left"}, headers=AUTH).status_code == 200
    client.post("/head", json={"direction": "center"}, headers=AUTH)
    assert turns == ["left", "front"]
    movement.peek.assert_not_called()
    assert client.post("/look-up", headers=AUTH).status_code == 409


def test_sleep_and_wake(switch: PrivacySwitch) -> None:
    """Sleep and wake."""
    api, movement, runtime = _api()
    client = _client(api)
    assert client.post("/sleep", headers=AUTH).status_code == 200
    movement.go_to_sleep.assert_called_once()
    runtime.request_close.assert_called_once_with("go_to_sleep")
    runtime.set_asleep.assert_called_with(True)
    assert client.post("/wake", headers=AUTH).status_code == 200
    movement.wake_up.assert_called_once()
    runtime.set_asleep.assert_called_with(False)


def test_waking_is_refused_while_privacy_is_on(switch: PrivacySwitch) -> None:
    """Waking is refused while privacy is on."""
    switch.set(True)
    api, movement, _ = _api(sleeping=True)
    assert _client(api).post("/wake", headers=AUTH).status_code == 409
    movement.wake_up.assert_not_called()


def test_face_routes_need_face_recognition_and_the_token(switch: PrivacySwitch) -> None:
    """Face routes need face recognition and the token."""
    api, _, _ = _api(faces=None)
    assert _client(api).get("/api/faces", headers=AUTH).status_code == 404
    registry = MagicMock()
    registry.people.return_value = []
    api, _, _ = _api(faces=SimpleNamespace(registry=registry))
    client = _client(api)
    assert client.get("/api/faces").status_code == 403
    assert client.get("/api/faces", headers=AUTH).json() == {"people": []}


def test_the_token_is_created_once_owner_only(tmp_path: Path) -> None:
    """The token is created once owner only."""
    path = tmp_path / "share" / "control-token"
    token = load_or_create_token(path)
    assert len(token) >= 32
    assert load_or_create_token(path) == token
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
