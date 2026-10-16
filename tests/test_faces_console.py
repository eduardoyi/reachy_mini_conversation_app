from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.console import LocalStream
from reachy_mini_conversation_app.face_identity import EnrollResult, FaceRegistry


LOCAL = {"base_url": "http://127.0.0.1", "client": ("127.0.0.1", 50000)}
SAME_ORIGIN = {"Origin": "http://127.0.0.1"}


def _console(tmp_path: Path, service: object) -> FastAPI:
    app = FastAPI()
    stream = LocalStream(
        MagicMock(), MagicMock(), settings_app=app, instance_path=str(tmp_path), face_recognition=service
    )
    stream._init_settings_ui_if_needed()
    return app


@pytest.fixture
def faces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, MagicMock]:
    """Real settings routes over a real face store with one enrolled person; no camera."""
    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    service = MagicMock()
    service.registry = FaceRegistry(tmp_path / "faces")
    service.registry.enroll("Ana", [(np.eye(128, dtype=np.float32)[0], b"\xff\xd8ana")])
    return _console(tmp_path, service), service


def test_faces_list_and_photo_are_local_and_uncached(faces) -> None:
    """The list names enrolled people; their photos are served only by owner and file name."""
    app, service = faces
    photo = service.registry.people()[0].photos[0]
    with TestClient(app, **LOCAL) as client:
        listed = client.get("/api/faces")
        image = client.get("/api/faces/photo", params={"person_id": "ana", "photo": photo})
        stranger = client.get("/api/faces/photo", params={"person_id": "ben", "photo": photo})
        traversal = client.get("/api/faces/photo", params={"person_id": "ana", "photo": "../faces.json"})

    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert listed.json()["people"] == [
        {
            "id": "ana",
            "name": "Ana",
            "samples": 1,
            "photos": [photo],
            "updated_at": listed.json()["people"][0]["updated_at"],
        }
    ]
    assert "embedding" not in listed.text
    assert (
        image.status_code == 200 and image.content == b"\xff\xd8ana" and image.headers["content-type"] == "image/jpeg"
    )
    assert stranger.status_code == 404 and traversal.status_code == 404


@pytest.mark.parametrize("host,peer", [("evil.example", "127.0.0.1"), ("127.0.0.1", "192.0.2.1")])
def test_faces_reject_remote_clients_and_rebinding_hosts(faces, host: str, peer: str) -> None:
    """Faces are as private as memory: no remote peers, no third-party hostnames."""
    app, service = faces
    with TestClient(app, base_url=f"http://{host}", client=(peer, 50000)) as client:
        assert client.get("/api/faces").status_code == 403
        assert client.post("/api/faces/enroll", json={"name": "Eve"}).status_code == 403
        assert client.post("/api/faces/forget", json={"person_id": "ana"}).status_code == 403
    service.enroll_from_camera.assert_not_called()
    assert service.registry.people()


def test_faces_enroll_from_the_camera(faces) -> None:
    """Enrollment runs the camera capture and reports the saved person or why it failed."""
    app, service = faces
    service.enroll_from_camera.side_effect = [
        EnrollResult(service.registry.people()[0]),
        EnrollResult(None, "no clear face in view"),
    ]
    with TestClient(app, **LOCAL) as client:
        ok = client.post("/api/faces/enroll", json={"name": "Ana"}, headers=SAME_ORIGIN)
        failed = client.post("/api/faces/enroll", json={"name": "Ana"}, headers=SAME_ORIGIN)
        cross_origin = client.post(
            "/api/faces/enroll", json={"name": "Ana"}, headers={"Origin": "http://evil.example"}
        )
        no_origin = client.post("/api/faces/enroll", json={"name": "Ana"})
        wrong_keys = client.post("/api/faces/enroll", json={"name": "Ana", "x": "1"}, headers=SAME_ORIGIN)

    assert ok.status_code == 200 and ok.json()["person"]["name"] == "Ana"
    assert failed.status_code == 422 and failed.json() == {"error": "no clear face in view"}
    assert cross_origin.status_code == 403 and no_origin.status_code == 403
    assert wrong_keys.status_code == 400
    assert service.enroll_from_camera.call_count == 2


def test_faces_forget_deletes_the_person(faces) -> None:
    """Forgetting removes the person; repeating it reports nothing removed."""
    app, service = faces
    with TestClient(app, **LOCAL) as client:
        first = client.post("/api/faces/forget", json={"person_id": "ana"}, headers=SAME_ORIGIN)
        second = client.post("/api/faces/forget", json={"person_id": "ana"}, headers=SAME_ORIGIN)
        empty = client.post("/api/faces/forget", json={"person_id": ""}, headers=SAME_ORIGIN)

    assert first.json() == {"removed": True} and second.json() == {"removed": False}
    assert empty.status_code == 400
    assert service.registry.people() == []


def test_faces_routes_are_absent_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without face recognition every Faces route is a 404."""
    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    app = _console(tmp_path, None)
    with TestClient(app, **LOCAL) as client:
        assert client.get("/api/faces").status_code == 404
        assert client.post("/api/faces/enroll", json={"name": "Ana"}, headers=SAME_ORIGIN).status_code == 404
