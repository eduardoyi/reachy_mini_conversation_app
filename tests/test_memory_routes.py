"""Household memory as the admin app sees it through the running app: list, correct, forget, and when it is off."""

from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.memory_routes import register_memory_routes
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore


def _client(tmp_path: Path, denied: Response | None = None) -> TestClient:
    app = FastAPI()

    def access(request: Request, mutation: bool = False) -> Response | None:
        return denied

    register_memory_routes(app, tmp_path, access)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn household memory on."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)


def test_memories_are_listed_corrected_and_forgotten(tmp_path: Path) -> None:
    """Every memory is listed with who it is about; one is corrected by id, another forgotten by id."""
    store = HouseholdMemoryStore(tmp_path)
    panda = store.remember("Enzo is called Panda.", subject_id="enzo", kind="fact")
    porto = store.remember("The family is going to Porto.", subject_id="household", kind="plan")
    client = _client(tmp_path)

    entries = client.get("/memory").json()["entries"]
    assert {(e["text"], e["subject"], e["kind"]) for e in entries} == {
        ("Enzo is called Panda.", "enzo", "fact"),
        ("The family is going to Porto.", "household", "plan"),
    }
    corrected = client.post("/memory/update", json={"id": panda.id, "text": "Enzo's nickname is Panda."})
    assert corrected.json()["entry"]["text"] == "Enzo's nickname is Panda."
    assert client.post("/memory/forget", json={"id": porto.id}).json() == {"removed": True, "subject": "household"}
    assert [e.text for e in store.list_entries()] == ["Enzo's nickname is Panda."]


def test_bad_requests_and_unknown_ids_change_nothing(tmp_path: Path) -> None:
    """No id, empty text or an unknown id are refused."""
    HouseholdMemoryStore(tmp_path).remember("Enzo likes dinosaurs.", subject_id="enzo")
    client = _client(tmp_path)
    assert client.post("/memory/update", json={"text": "x"}).status_code == 400
    assert client.post("/memory/update", json={"id": "nope", "text": " "}).status_code == 422
    assert client.post("/memory/update", json={"id": "nope", "text": "Something"}).status_code == 422
    assert client.post("/memory/forget", json={"id": "nope"}).json()["removed"] is False
    assert [e.text for e in HouseholdMemoryStore(tmp_path).list_entries()] == ["Enzo likes dinosaurs."]


def test_memory_off_or_access_denied_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With memory off the routes are not found; the caller's guard refusing wins first."""
    assert _client(tmp_path, Response(status_code=403)).get("/memory").status_code == 403
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
    assert _client(tmp_path).get("/memory").status_code == 404
