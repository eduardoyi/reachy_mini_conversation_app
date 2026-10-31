"""The app's loopback control API for the admin app: token, privacy, camera stream, head, sleep and wake."""

import io
import sys
import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from reachy_mini_conversation_app import control_api, camera_frames
from reachy_mini_conversation_app.privacy import PrivacySwitch
from reachy_mini_conversation_app.control_api import PEEK_YAW_STEP, PEEK_PITCH_STEP, ControlApi, load_or_create_token
from reachy_mini_conversation_app.wake_learning.store import WakeLearningStore


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
        instance_path=None,
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
    assert status["in_call"] is False


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


def test_a_missed_call_is_marked_for_whoever_tapped_it_unless_privacy_is_on(switch: PrivacySwitch) -> None:
    """The phone's "He missed me" keeps the minute before now; with the microphone off there is nothing to keep."""
    api, _, runtime = _api()
    runtime.mark_missed.return_value = True
    client = _client(api)

    response = client.post("/wake/missed", json={"who": "adriana@example.com"}, headers=AUTH)
    assert response.status_code == 200 and response.json() == {"ok": True}
    runtime.mark_missed.assert_called_once_with(by="app", who="adriana@example.com")

    switch.set(True)
    assert client.post("/wake/missed", json={"who": ""}, headers=AUTH).status_code == 409
    assert runtime.mark_missed.call_count == 1


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        (np.concatenate([np.full(8000, 2), np.full(8000, 3000), np.full(16000, 2)]), {"ok": True, "reason": None}),
        (np.full(32000, 2), {"ok": False, "reason": "quiet"}),
        (np.concatenate([np.full(8000, 2), np.full(128000, 3000)]), {"ok": False, "reason": "too_long"}),
    ],
)
def test_a_recording_of_the_name_keeps_only_the_voice(
    switch: PrivacySwitch, tmp_path: Path, samples: Any, expected: dict[str, Any]
) -> None:
    """A take is cut to the voice and kept for the person; silence or a long speech is not kept."""
    api, _, runtime = _api()
    store = WakeLearningStore(tmp_path / "learning", clips=tmp_path / "clips")
    api._wake_store = store

    async def record_name() -> tuple[int, Any]:
        return 16000, samples.astype(np.int16)

    runtime.record_name = record_name
    response = _client(api).post("/wake/take", json={"person": "Lucía", "prompt": "from the kitchen"}, headers=AUTH)

    assert response.status_code == 200
    assert {key: response.json()[key] for key in expected} == expected
    kept = store.all_recordings()
    assert len(kept) == int(expected["ok"])
    if kept:
        assert kept[0]["person"] == "Lucía" and kept[0]["prompt"] == "from the kitchen"
        assert 0.5 < kept[0]["seconds"] <= 1.05  # the voice and a quarter second either side


def test_a_recording_is_refused_when_he_cannot_listen(switch: PrivacySwitch, tmp_path: Path) -> None:
    """In a conversation (or without wake learning) there is no take; a bad request is refused before trying."""
    api, _, runtime = _api()
    api._wake_store = WakeLearningStore(tmp_path / "learning", clips=tmp_path / "clips")

    async def record_name() -> None:
        return None

    runtime.record_name = record_name
    client = _client(api)

    assert client.post("/wake/take", json={"person": "Lucía", "prompt": ""}, headers=AUTH).status_code == 409
    assert client.post("/wake/take", json={"person": " ", "prompt": ""}, headers=AUTH).status_code == 400


class _Games:
    """A stand-in game host that records what the phone asked."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, ...]] = []

    def status(self) -> dict[str, Any]:
        return {"state": "idle", "game": None, "progress": "", "games": [{"key": "colors", "title": {}}]}

    def start(self, key: str, language: str) -> str | None:
        self.actions.append(("start", key, language))
        return None if key == "colors" else f"There is no game {key!r}."

    def pause(self) -> None:
        self.actions.append(("pause",))

    def resume(self, *, say: bool = False) -> None:
        self.actions.append(("resume",))

    def end(self, reason: str) -> None:
        self.actions.append(("end",))


@pytest.fixture
def runtime_loop() -> Any:
    """Run the runtime's event loop in its own thread, as in the app."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


def test_games_start_pause_and_end_from_the_phone(switch: PrivacySwitch, runtime_loop: Any) -> None:
    """A tap starts a game (waking him first), and pauses or ends it; status shows the games."""
    api, movement, runtime = _api(sleeping=True)
    games = _Games()
    api._deps.games = games  # type: ignore[attr-defined]
    runtime.loop = runtime_loop
    client = _client(api)
    assert client.get("/status", headers=AUTH).json()["game"]["games"][0]["key"] == "colors"
    assert client.post("/game", json={"action": "start", "game": "colors"}, headers=AUTH).status_code == 200
    movement.wake_up.assert_called_once()
    assert client.post("/game", json={"action": "pause"}, headers=AUTH).status_code == 200
    assert client.post("/game", json={"action": "end"}, headers=AUTH).status_code == 200
    refused = client.post("/game", json={"action": "start", "game": "chess"}, headers=AUTH)
    assert refused.status_code == 400 and "chess" in refused.json()["error"]
    assert games.actions == [("start", "colors", "es"), ("pause",), ("end",), ("start", "chess", "es")]
    switch.set(True)
    assert client.post("/game", json={"action": "start", "game": "colors"}, headers=AUTH).status_code == 409


def test_games_are_refused_when_not_set_up(switch: PrivacySwitch) -> None:
    """Without the game host (no hybrid runtime or no TypeSafe key) the phone is told so."""
    api, _, _ = _api()
    client = _client(api)
    assert client.get("/status", headers=AUTH).json()["game"] is None
    assert client.post("/game", json={"action": "pause"}, headers=AUTH).status_code == 404
