"""The household admin app's server: the Tailscale guard and each page's API, against a fake control API."""

import sys
import json
from typing import Any
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from reachy_mini_conversation_app.admin import server
from reachy_mini_conversation_app.whatsapp import ServerStatus, WhatsAppLink
from reachy_mini_conversation_app.json_lines import JsonLines
from reachy_mini_conversation_app.usage_costs import get_ledger
from reachy_mini_conversation_app.admin.server import AdminSettings, create_app
from reachy_mini_conversation_app.admin.records import read_json
from reachy_mini_conversation_app.admin.app_link import AppLink
from reachy_mini_conversation_app.admin.notifier import SUBSCRIPTIONS_FILE, Notifier


HOST = "reachy.example.ts.net"
BASE = f"http://{HOST}"
LOOPBACK = ("127.0.0.1", 50000)
EDUARDO = {"Tailscale-User-Login": "eduardo@example.com", "Tailscale-User-Name": "Eduardo"}
SAME_ORIGIN = {"Origin": BASE}
GROUP = "120363000000000001@g.us"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
STREAM_TYPE = "multipart/x-mixed-replace; boundary=frame"


class ControlAPI:
    """A fake conversation-app control API: records what it is sent and answers like the real one."""

    def __init__(self) -> None:
        """Start running, with one enrolled face, privacy off and the camera streaming."""
        self.requests: list[tuple[str, str, Any]] = []
        self.down = False
        self.camera = "stream"
        self.privacy: dict[str, Any] = {"on": False, "since": None}
        self.change_status = 200
        self.faces = [{"id": "adriana", "name": "Adriana", "samples": 2, "photos": ["1.jpg"]}]

    def status(self) -> dict[str, Any]:
        """Return the app's status as the real control API shapes it."""
        return {"mode": "conversation", "sleeping": False, "privacy": self.privacy, "calls_linked": True}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one control API request (or fail to connect while down)."""
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        assert request.headers["authorization"] == "Bearer control-secret"
        body: Any = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/status":
            return httpx.Response(200, json=self.status())
        if path == "/api/faces":
            return httpx.Response(200, json={"people": [*self.faces, "junk"]})
        if path == "/api/faces/photo":
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
        if path.startswith("/api/faces/"):
            return httpx.Response(self.change_status, json={"ok": self.change_status == 200})
        if path == "/memory":
            return httpx.Response(
                200, json={"entries": [{"id": "m1", "text": "Enzo is called Panda.", "subject": "enzo"}]}
            )
        if path == "/memory/forget":
            return httpx.Response(200, json={"removed": True, "subject": "enzo"})
        if path == "/privacy":
            self.privacy = {"on": body["on"], "since": "2026-10-30T10:00:00+00:00" if body["on"] else None}
            return httpx.Response(200, json=self.status())
        if path == "/camera":
            if self.camera == "busy":
                return httpx.Response(409, json={"error": "privacy is on"})
            return httpx.Response(
                200, headers={"content-type": STREAM_TYPE}, stream=httpx.ByteStream(b"--frame\r\nJPEG1--frame\r\n")
            )
        return httpx.Response(200, json={})

    def calls(self, path: str) -> list[Any]:
        """Return the JSON bodies sent to ``path``, oldest first."""
        return [body for _, called, body in self.requests if called == path]


class PushService:
    """A fake push service that accepts everything."""

    def __init__(self) -> None:
        """No pushes yet, and no expired subscriptions."""
        self.requests: list[httpx.Request] = []
        self.expired: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Accept the push, or answer 410 for an expired subscription."""
        self.requests.append(request)
        return httpx.Response(410 if str(request.url) in self.expired else 201)


@dataclass
class Admin:
    """Everything one test's admin app uses, all in a temp folder."""

    settings: AdminSettings
    control: ControlAPI
    push: PushService
    notifier: Notifier

    @contextmanager
    def client(
        self,
        headers: dict[str, str] | None = None,
        peer: tuple[str, int] = LOOPBACK,
        base_url: str = BASE,
        settings: AdminSettings | None = None,
    ) -> Iterator[TestClient]:
        """Run the admin app (lifespan included) and yield a client signed in as ``headers``."""
        link = AppLink(
            "http://127.0.0.1:7861",
            self.settings.control_token_file,
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.control)),
        )
        app = create_app(settings or self.settings, link=link, notifier=self.notifier)
        with TestClient(app, base_url=base_url, client=peer, headers=EDUARDO if headers is None else headers) as c:
            yield c

    def audit(self) -> list[dict[str, Any]]:
        """Return the audit log, newest first."""
        return JsonLines(self.settings.data_dir / "audit.jsonl").recent()


@pytest.fixture
def admin(tmp_path: Path) -> Admin:
    """Build an admin app over temp files, a fake control API, a fake push service and a closed daemon port."""
    token = tmp_path / "control-token"
    token.write_text("control-secret\n")
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=secret\nQUIET_HOURS=22:00-07:00\n")
    settings = AdminSettings(
        allowed_logins=frozenset({"eduardo@example.com", "adriana@example.com"}),
        data_dir=tmp_path / "admin",
        contacts_file=tmp_path / "contacts.json",
        calls_file=tmp_path / "calls.json",
        env_file=env_file,
        control_url="http://127.0.0.1:7861",
        control_token_file=token,
        daemon_url="http://127.0.0.1:9",
        app_service="",
        display_dir=tmp_path / "display",
        activity_file=tmp_path / "activity.jsonl",
        whatsapp_url=None,
        photos_dir=tmp_path / "photos",
    )
    push = PushService()
    notifier = Notifier(settings.data_dir, client=httpx.AsyncClient(transport=httpx.MockTransport(push)))
    return Admin(settings, ControlAPI(), push, notifier)


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False))


# Guard -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/static/admin/admin.js", "/manifest.webmanifest", "/sw.js", "/api/me"])
def test_requests_without_a_tailscale_login_are_refused(admin: Admin, path: str) -> None:
    """No login header (Funnel, or another local program): even the page and its files are refused."""
    with admin.client(headers={}) as client:
        response = client.get(path)
    assert response.status_code == 403 and response.content == b""
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/static/admin/admin.js", "/manifest.webmanifest", "/sw.js"])
def test_an_allowed_login_gets_the_app(admin: Admin, path: str) -> None:
    """The installed app's files are served to household logins."""
    with admin.client() as client:
        assert client.get(path).status_code == 200


def test_a_login_not_in_the_household_is_refused(admin: Admin) -> None:
    """Another tailnet user is refused everywhere."""
    with admin.client(headers={"Tailscale-User-Login": "guest@example.com"}) as client:
        assert client.get("/").status_code == 403
        assert client.get("/api/people").status_code == 403
        assert client.post("/api/sleep", headers=SAME_ORIGIN).status_code == 403
    assert admin.control.calls("/sleep") == []


def test_login_header_name_and_value_are_case_insensitive(admin: Admin) -> None:
    """Tailscale may send any case; the login is compared lowercased."""
    with admin.client(headers={"TAILSCALE-USER-LOGIN": " Eduardo@Example.COM ", "tailscale-user-name": "Ed"}) as c:
        response = c.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {"login": "eduardo@example.com", "name": "Ed"}


@pytest.mark.parametrize("peer", [("100.64.0.5", 1), ("192.168.1.20", 50000), ("testclient", 50000)])
def test_non_loopback_clients_are_refused_even_with_an_allowed_login(admin: Admin, peer: tuple[str, int]) -> None:
    """Only Tailscale Serve on this computer may reach the app; a forged header from the LAN is refused."""
    with admin.client(peer=peer) as client:
        assert client.get("/api/me").status_code == 403
        assert client.get("/").status_code == 403


def test_ipv6_loopback_is_local(admin: Admin) -> None:
    """Tailscale Serve may connect over ::1."""
    with admin.client(peer=("::1", 50000)) as client:
        assert client.get("/api/me").status_code == 200


def test_no_allowed_logins_refuses_everyone(admin: Admin) -> None:
    """An empty allowlist is a closed door, not an open one."""
    closed = AdminSettings(**{**admin.settings.__dict__, "allowed_logins": frozenset()})
    with admin.client(settings=closed) as client:
        assert client.get("/api/me").status_code == 403
    with admin.client(headers={"Tailscale-User-Login": ""}, settings=closed) as client:
        assert client.get("/api/me").status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://evil.example"},
        {"Origin": "http://reachy.example.ts.net.evil.example"},
        {"Origin": "http://reachy.example.ts.net:8443"},
        {"Origin": "null"},
    ],
)
def test_changes_without_the_apps_own_origin_are_refused(admin: Admin, headers: dict[str, str]) -> None:
    """POST and PUT need an Origin matching the host, so another site cannot make the phone change things."""
    with admin.client() as client:
        assert client.post("/api/privacy", json={"on": True}, headers=headers).status_code == 403
        assert client.put("/api/settings", json={"changes": {}}, headers=headers).status_code == 403
        assert client.post("/api/sleep", headers=headers).status_code == 403
    assert admin.control.calls("/privacy") == [] and admin.control.calls("/sleep") == []
    assert admin.audit() == []


def test_changes_from_the_same_origin_pass(admin: Admin) -> None:
    """The app's own page (any scheme, as Serve terminates TLS) may make changes."""
    with admin.client() as client:
        assert client.post("/api/sleep", headers=SAME_ORIGIN).status_code == 200
        assert client.post("/api/wake", headers={"Origin": f"https://{HOST}"}).status_code == 200
    assert admin.control.calls("/sleep") == [None] and admin.control.calls("/wake") == [None]
    assert [(e["who"], e["action"]) for e in admin.audit()] == [
        ("eduardo@example.com", "woken up"),
        ("eduardo@example.com", "put to sleep"),
    ]


def test_changes_may_match_the_forwarded_host(admin: Admin) -> None:
    """When Serve forwards to 127.0.0.1, the Origin may match X-Forwarded-Host instead."""
    with admin.client(base_url="http://127.0.0.1:7870") as client:
        response = client.post("/api/sleep", headers={"Origin": f"https://{HOST}", "X-Forwarded-Host": HOST})
        refused = client.post("/api/sleep", headers={"Origin": f"https://{HOST}"})
    assert response.status_code == 200 and refused.status_code == 403


def test_me_names_the_signed_in_user(admin: Admin) -> None:
    """The page shows who is signed in."""
    with admin.client() as client:
        response = client.get("/api/me")
    assert response.json() == {"login": "eduardo@example.com", "name": "Eduardo"}
    assert response.headers["cache-control"] == "no-store"


# People ------------------------------------------------------------------------------------------------


def test_people_lists_cards_with_faces_from_the_running_app(admin: Admin) -> None:
    """Cards join both files and the enrolled faces the control API lists."""
    _write(admin.settings.contacts_file, {"contacts": [{"name": "Adriana", "chat": GROUP, "say": ["mamá"]}]})
    _write(admin.settings.calls_file, {"approved": [{"name": "Abuela", "phone": "+34600111222", "say": ["mamá"]}]})
    with admin.client() as client:
        response = client.get("/api/people")
    assert response.status_code == 200
    payload = response.json()
    assert payload["people"] == [
        {"name": "Adriana", "say": ["mamá"], "chat": GROUP, "phone": "", "face": "adriana"},
        {"name": "Abuela", "say": ["mamá"], "chat": "", "phone": "+34600111222", "face": ""},
    ]
    assert payload["faces"] == admin.control.faces
    assert payload["shared"] == [{"said": "mama", "people": ["Adriana", "Abuela"]}]
    assert payload["versions"] == {
        "contacts": read_json(admin.settings.contacts_file)[1],
        "calls": read_json(admin.settings.calls_file)[1],
    }
    assert response.headers["cache-control"] == "no-store"


def test_people_still_lists_cards_while_the_app_is_down(admin: Admin) -> None:
    """Without the running app there are no faces, but the files are still shown."""
    _write(admin.settings.contacts_file, {"contacts": [{"name": "Adriana", "chat": GROUP}]})
    admin.control.down = True
    with admin.client() as client:
        payload = client.get("/api/people").json()
    assert payload["faces"] is None and [p["name"] for p in payload["people"]] == ["Adriana"]


def test_people_with_a_corrupt_file_is_unavailable(admin: Admin) -> None:
    """A file that is not JSON is reported, never overwritten."""
    admin.settings.contacts_file.write_text("{oops")
    with admin.client() as client:
        response = client.get("/api/people")
    assert response.status_code == 503 and "not valid JSON" in response.json()["error"]


def test_saving_people_writes_both_files_and_audits(admin: Admin) -> None:
    """A save from the page writes the files the app reads and records who changed them."""
    with admin.client() as client:
        loaded = client.get("/api/people").json()
        response = client.put(
            "/api/people",
            headers=SAME_ORIGIN,
            json={
                "people": [
                    {"name": "Adriana", "say": ["mamá"], "chat": GROUP, "phone": "+34 600 111 222", "face": "adriana"},
                    {"name": "Primos", "chat": "555@g.us"},
                ],
                "versions": loaded["versions"],
            },
        )
    assert response.status_code == 200
    assert [p["name"] for p in response.json()["people"]] == ["Adriana", "Primos"]
    assert response.json()["versions"]["contacts"] == read_json(admin.settings.contacts_file)[1]
    assert read_json(admin.settings.contacts_file)[0]["contacts"] == [
        {"name": "Adriana", "chat": GROUP, "say": ["mamá"], "face": "adriana"},
        {"name": "Primos", "chat": "555@g.us"},
    ]
    assert read_json(admin.settings.calls_file)[0]["approved"] == [
        {"name": "Adriana", "phone": "+34600111222", "say": ["mamá"], "face": "adriana"}
    ]
    (entry,) = admin.audit()
    assert entry["who"] == "eduardo@example.com"
    assert entry["action"] == "people saved" and entry["target"] == "2 people"


def test_saving_people_on_a_stale_version_is_a_conflict(admin: Admin) -> None:
    """When the other phone saved first, this save is refused with 409 and nothing changes."""
    _write(admin.settings.contacts_file, {"contacts": [{"name": "Adriana", "chat": GROUP}]})
    with admin.client() as client:
        versions = client.get("/api/people").json()["versions"]
        _write(admin.settings.contacts_file, {"contacts": [{"name": "Adriana", "chat": "999@g.us"}]})
        before = admin.settings.contacts_file.read_bytes()
        response = client.put(
            "/api/people", headers=SAME_ORIGIN, json={"people": [{"name": "Ben", "chat": GROUP}], "versions": versions}
        )
    assert response.status_code == 409 and "Reload" in response.json()["error"]
    assert admin.settings.contacts_file.read_bytes() == before
    assert admin.audit() == []


@pytest.mark.parametrize(
    "people",
    [
        [{"name": "Ana", "chat": "not-a-group"}],
        [{"name": "Ana"}, {"name": "ANA"}],
        [{"name": "Ana", "phone": "12"}],
        [{"name": "Ana", "say": "anita"}],
        ["Ana"],
    ],
)
def test_saving_invalid_people_is_refused(admin: Admin, people: list[Any]) -> None:
    """An invalid card is a 422 with a message; the files are not created."""
    with admin.client() as client:
        response = client.put(
            "/api/people", headers=SAME_ORIGIN, json={"people": people, "versions": {"contacts": "", "calls": ""}}
        )
    assert response.status_code == 422 and response.json()["error"]
    assert not admin.settings.contacts_file.exists() and not admin.settings.calls_file.exists()


@pytest.mark.parametrize(
    "content,content_type,status",
    [
        (json.dumps({"people": "Ana", "versions": {}}), "application/json", 400),
        (json.dumps({"people": []}), "application/json", 400),
        (json.dumps(["not", "an", "object"]), "application/json", 400),
        ("{broken", "application/json", 400),
        (json.dumps({"people": [], "versions": {}}), "text/plain", 415),
        (json.dumps({"people": [{"name": "x" * 70_000}], "versions": {}}), "application/json", 413),
    ],
)
def test_saving_people_with_a_malformed_body_is_refused(
    admin: Admin, content: str, content_type: str, status: int
) -> None:
    """Bodies that are not a small JSON object with people and versions are refused before any file is read."""
    with admin.client() as client:
        response = client.put(
            "/api/people", headers={**SAME_ORIGIN, "Content-Type": content_type}, content=content.encode()
        )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


# Faces -------------------------------------------------------------------------------------------------


def test_face_upload_is_relayed_and_audited(admin: Admin) -> None:
    """Photos go to the running app (which owns the face store); a success is audited by name."""
    payload = {"name": "Lucía", "photos": ["data:image/jpeg;base64,/9j/AA=="]}
    with admin.client() as client:
        response = client.post("/api/faces/upload", headers=SAME_ORIGIN, json=payload)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert admin.control.calls("/api/faces/upload") == [payload]
    (entry,) = admin.audit()
    assert (entry["who"], entry["action"], entry["target"]) == ("eduardo@example.com", "face photos added", "Lucía")


def test_face_change_refused_by_the_app_is_relayed_without_audit(admin: Admin) -> None:
    """The app's refusal reaches the page as is; nothing is recorded as done."""
    admin.control.change_status = 422
    with admin.client() as client:
        response = client.post("/api/faces/forget", headers=SAME_ORIGIN, json={"person_id": "adriana"})
    assert response.status_code == 422 and response.json() == {"ok": False}
    assert admin.control.calls("/api/faces/forget") == [{"person_id": "adriana"}]
    assert admin.audit() == []


def test_face_forget_audits_the_person_id(admin: Admin) -> None:
    """Without a name the audit names the person id."""
    with admin.client() as client:
        assert client.post("/api/faces/forget", headers=SAME_ORIGIN, json={"person_id": "adriana"}).status_code == 200
    assert [(e["action"], e["target"]) for e in admin.audit()] == [("face forgotten", "adriana")]


def test_face_change_while_the_app_is_down_is_unavailable(admin: Admin) -> None:
    """No running app: 503 and nothing audited."""
    admin.control.down = True
    with admin.client() as client:
        response = client.post("/api/faces/enroll", headers=SAME_ORIGIN, json={"name": "Ana"})
    assert response.status_code == 503 and admin.audit() == []


def test_face_photo_is_relayed_as_jpeg(admin: Admin) -> None:
    """A face photo comes from the running app, never cached by the browser."""
    with admin.client() as client:
        response = client.get("/api/faces/photo", params={"person_id": "adriana", "photo": "1.jpg"})
    assert response.status_code == 200 and response.content == b"\xff\xd8jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"


# Camera ------------------------------------------------------------------------------------------------


def test_camera_refusal_is_relayed(admin: Admin) -> None:
    """When the app refuses the camera (privacy on), the page gets the same 409 and message."""
    admin.control.camera = "busy"
    with admin.client() as client:
        response = client.get("/api/camera")
    assert response.status_code == 409 and response.json() == {"error": "privacy is on"}
    assert response.headers["cache-control"] == "no-store"


def test_camera_stream_is_passed_through(admin: Admin) -> None:
    """The MJPEG stream is relayed with its multipart content type, uncached."""
    with admin.client() as client:
        response = client.get("/api/camera")
    assert response.status_code == 200
    assert response.headers["content-type"] == STREAM_TYPE
    assert response.content == b"--frame\r\nJPEG1--frame\r\n"
    assert response.headers["cache-control"] == "no-store"


def test_camera_while_the_app_is_down_is_unavailable(admin: Admin) -> None:
    """No running app: 503."""
    admin.control.down = True
    with admin.client() as client:
        assert client.get("/api/camera").status_code == 503


# Controls ----------------------------------------------------------------------------------------------


def test_privacy_is_relayed_and_audited(admin: Admin) -> None:
    """Turning privacy on reaches the app as {"on": true} and is recorded."""
    with admin.client() as client:
        response = client.post("/api/privacy", headers=SAME_ORIGIN, json={"on": True})
        client.post("/api/privacy", headers=SAME_ORIGIN, json={"on": False})
    assert response.status_code == 200 and response.json()["privacy"]["on"] is True
    assert admin.control.calls("/privacy") == [{"on": True}, {"on": False}]
    assert [e["action"] for e in admin.audit()] == ["privacy off", "privacy on"]


@pytest.mark.parametrize("payload", [{}, {"on": "yes"}, {"on": 1}, {"on": None}])
def test_privacy_needs_true_or_false(admin: Admin, payload: dict[str, Any]) -> None:
    """Anything but a boolean is refused without reaching the app."""
    with admin.client() as client:
        assert client.post("/api/privacy", headers=SAME_ORIGIN, json=payload).status_code == 400
    assert admin.control.calls("/privacy") == []


def test_privacy_while_the_app_is_down_is_unavailable(admin: Admin) -> None:
    """No running app: 503 and nothing audited."""
    admin.control.down = True
    with admin.client() as client:
        assert client.post("/api/privacy", headers=SAME_ORIGIN, json={"on": True}).status_code == 503
    assert admin.audit() == []


def test_head_and_look_up_are_relayed_without_audit(admin: Admin) -> None:
    """Small head moves are relayed but not worth an audit entry."""
    with admin.client() as client:
        assert client.post("/api/head", headers=SAME_ORIGIN, json={"direction": "left"}).status_code == 200
        assert client.post("/api/look-up", headers=SAME_ORIGIN).status_code == 200
    assert admin.control.calls("/head") == [{"direction": "left"}]
    assert admin.control.calls("/look-up") == [None]
    assert admin.audit() == []


def test_restart_is_not_available_without_a_service(admin: Admin) -> None:
    """No ADMIN_APP_SERVICE: restart is 404 and nothing runs."""
    with admin.client() as client:
        response = client.post("/api/restart", headers=SAME_ORIGIN)
    assert response.status_code == 404 and "ADMIN_APP_SERVICE" in response.json()["error"]
    assert response.headers["cache-control"] == "no-store"
    assert admin.audit() == []


def test_restart_runs_in_the_background_and_quiets_the_stopped_notice(
    admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a service, restart answers at once, is audited, and status says Reachy is restarting."""
    started: list[str] = []

    async def restart_app(service: str) -> None:
        started.append(service)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(server, "restart_app", restart_app)
    with_service = AdminSettings(**{**admin.settings.__dict__, "app_service": "com.example.reachy"})
    with admin.client(settings=with_service) as client:
        assert client.post("/api/restart", headers=SAME_ORIGIN).json() == {"restarted": True}
        assert client.get("/api/status").json()["app"]["restarting"] is True
    assert started == ["com.example.reachy"]
    assert [entry["action"] for entry in admin.audit()] == ["restarted Reachy"]


@pytest.mark.asyncio
async def test_restart_stops_the_app_waits_for_it_then_starts_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGINT to the app, wait while it or its launcher still runs, then kickstart the LaunchAgent."""
    commands: list[tuple[str, ...]] = []
    still_running = [0, 0, 1]  # pgrep finds something twice, then nothing

    async def run_command(*args: str) -> tuple[int, str]:
        commands.append(args)
        return (still_running.pop(0), "") if args[0] == "pgrep" else (0, "")

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(server, "run_command", run_command)
    monkeypatch.setattr(server.asyncio, "sleep", no_wait)
    await server.restart_app("com.example.reachy")
    assert commands[0] == ("pkill", "-INT", "-f", "bin/reachy-mini-conversation-app")
    assert [c[0] for c in commands] == ["pkill", "pgrep", "pgrep", "pgrep", "launchctl"]
    assert commands[-1][:3] == ("launchctl", "kickstart", "-k") and commands[-1][3].endswith("/com.example.reachy")


@pytest.mark.asyncio
async def test_restart_gives_up_when_the_app_does_not_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Something still running after the wait: nothing is started (a second Reachy would fight the first)."""
    commands: list[str] = []

    async def run_command(*args: str) -> tuple[int, str]:
        commands.append(args[0])
        return 0, ""

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(server, "run_command", run_command)
    monkeypatch.setattr(server.asyncio, "sleep", no_wait)
    monkeypatch.setattr(server, "RESTART_STOP_S", -1)
    await server.restart_app("com.example.reachy")
    assert "launchctl" not in commands


@pytest.mark.parametrize("level", [-1, 101, 50.5, True, "50", None])
def test_volume_level_must_be_0_to_100(admin: Admin, level: object) -> None:
    """Only whole numbers 0 to 100 are accepted."""
    with admin.client() as client:
        assert client.post("/api/volume", headers=SAME_ORIGIN, json={"level": level}).status_code == 400


def test_volume_with_the_daemon_down_is_unavailable(admin: Admin) -> None:
    """The daemon (a closed port here) not answering is a 503, not audited."""
    with admin.client() as client:
        response = client.post("/api/volume", headers=SAME_ORIGIN, json={"level": 40})
    assert response.status_code == 503 and admin.audit() == []


# Status ------------------------------------------------------------------------------------------------


def test_status_reports_the_app_and_unreachable_parts(admin: Admin) -> None:
    """The running app's status is shown; WhatsApp not set up, no volume (daemon down), no restart."""
    with admin.client() as client:
        payload = client.get("/api/status").json()
    assert payload["app"]["running"] is True and payload["app"]["mode"] == "conversation"
    assert payload["privacy"] == {"on": False, "since": None}
    assert payload["whatsapp"] == {"ok": None, "detail": "Not set up"}
    assert payload["calls"] == {"ok": True, "detail": ""}
    assert payload["volume"] is None
    assert payload["can_restart"] is False
    assert payload["display"] == {"last_check_in": None, "recent": False, "battery_v": None}
    assert isinstance(payload["disk_free_gb"], float)


def test_status_with_privacy_on_reports_how_long(admin: Admin) -> None:
    """Privacy on carries how long it has been on."""
    admin.control.privacy = {"on": True, "since": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()}
    with admin.client() as client:
        privacy = client.get("/api/status").json()["privacy"]
    assert privacy["on"] is True and 5 * 3600 - 60 < privacy["on_for_s"] < 5 * 3600 + 60


def test_status_shows_a_recent_display_check_in(admin: Admin) -> None:
    """The e-ink display's last check-in and battery come from its device file."""
    admin.settings.display_dir.mkdir(parents=True)
    ended = datetime.now(timezone.utc).timestamp() - 60
    _write(admin.settings.display_dir / "device.json", {"ended": ended, "battery_v": 3.9})
    with admin.client() as client:
        display = client.get("/api/status").json()["display"]
    assert display == {"last_check_in": ended, "recent": True, "battery_v": 3.9}


def test_status_while_the_app_is_down(admin: Admin) -> None:
    """The app not answering is shown as not running, with privacy off and calls unknown."""
    admin.control.down = True
    with admin.client() as client:
        payload = client.get("/api/status").json()
    assert payload["app"] == {"running": False, "restarting": False}
    assert payload["privacy"] == {"on": False, "since": None}
    assert payload["calls"]["ok"] is None


# Settings ----------------------------------------------------------------------------------------------


def test_settings_lists_the_editable_keys_with_current_values(admin: Admin) -> None:
    """Only allowlisted keys appear, with their label, kind and value; secrets never do."""
    with admin.client() as client:
        response = client.get("/api/settings")
    settings = {s["key"]: s for s in response.json()["settings"]}
    assert settings["QUIET_HOURS"]["value"] == "22:00-07:00" and settings["QUIET_HOURS"]["kind"] == "hours"
    assert settings["WHATSAPP_CALLS_LANGUAGE"]["choices"] == ["es", "en", "it", "pt"]
    assert "GEMINI_API_KEY" not in settings and "secret" not in response.text


def test_saving_settings_updates_the_env_file_and_audits(admin: Admin) -> None:
    """A change rewrites only its lines and is recorded with the keys changed (not the values)."""
    with admin.client() as client:
        response = client.put(
            "/api/settings",
            headers=SAME_ORIGIN,
            json={"changes": {"QUIET_HOURS": "21:30-07:00", "FACE_RECOGNITION_ENABLED": "1"}},
        )
    assert response.status_code == 200
    assert {s["key"]: s["value"] for s in response.json()["settings"]}["QUIET_HOURS"] == "21:30-07:00"
    assert admin.settings.env_file.read_text() == (
        "GEMINI_API_KEY=secret\nQUIET_HOURS=21:30-07:00\nFACE_RECOGNITION_ENABLED=1\n"
    )
    (entry,) = admin.audit()
    assert entry["action"] == "settings changed"
    assert entry["target"] == "FACE_RECOGNITION_ENABLED, QUIET_HOURS"


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"GEMINI_API_KEY": "stolen"}, 422),
        ({"QUIET_HOURS": "25:00-01:00"}, 422),
        ({"FACE_RECOGNITION_ENABLED": 1}, 400),
        ("QUIET_HOURS=1", 400),
    ],
)
def test_saving_invalid_settings_changes_nothing(admin: Admin, changes: object, status: int) -> None:
    """Unknown keys, bad values and non-text values are refused; the file is untouched."""
    before = admin.settings.env_file.read_bytes()
    with admin.client() as client:
        response = client.put("/api/settings", headers=SAME_ORIGIN, json={"changes": changes})
    assert response.status_code == status
    assert admin.settings.env_file.read_bytes() == before and admin.audit() == []


# Activity ----------------------------------------------------------------------------------------------


def test_activity_combines_household_activity_changes_and_usage(admin: Admin) -> None:
    """The activity page shows what Reachy did, who changed what, and AI spend."""
    JsonLines(admin.settings.activity_file).append({"kind": "call_in", "who": "Abuela", "outcome": "missed"})
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    get_ledger().path.write_text(json.dumps({"ts": now, "feature": "conversation", "cost_usd": 0.25}) + "\n")
    with admin.client() as client:
        client.post("/api/sleep", headers=SAME_ORIGIN)
        payload = client.get("/api/activity").json()
    assert [(e["kind"], e["who"], e["outcome"]) for e in payload["activity"]] == [("call_in", "Abuela", "missed")]
    assert [(e["who"], e["action"]) for e in payload["changes"]] == [("eduardo@example.com", "put to sleep")]
    assert payload["usage"]["totals"]["today"] == 0.25
    assert payload["usage"]["by_feature"] == {"conversation": 0.25}


# Notifications -----------------------------------------------------------------------------------------


def test_push_subscribe_test_and_unsubscribe(admin: Admin) -> None:
    """The phone gets the key, subscribes, receives a test notification and can unsubscribe."""
    subscription = {"endpoint": "https://push.example.com/phone", "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
    with admin.client() as client:
        key = client.get("/api/push/key")
        subscribed = client.post("/api/push/subscribe", headers=SAME_ORIGIN, json=subscription)
        tested = client.post("/api/push/test", headers=SAME_ORIGIN, json={"endpoint": subscription["endpoint"]})
        unsubscribed = client.post(
            "/api/push/unsubscribe", headers=SAME_ORIGIN, json={"endpoint": subscription["endpoint"]}
        )
        after = client.post("/api/push/test", headers=SAME_ORIGIN, json={})

    assert key.json() == {"key": admin.notifier.public_key}
    assert subscribed.json() == {"subscribed": True}
    assert tested.json() == {"delivered": 1, "gone": False}
    assert unsubscribed.json() == {"subscribed": False}
    assert after.json() == {"delivered": 0, "gone": False}
    assert [str(r.url) for r in admin.push.requests] == ["https://push.example.com/phone"]
    assert read_json(admin.settings.data_dir / SUBSCRIPTIONS_FILE)[0] == {"subscriptions": []}
    for response in (key, subscribed, tested, unsubscribed):
        assert response.headers["cache-control"] == "no-store"


def test_a_test_tells_the_phone_its_expired_subscription_was_dropped(admin: Admin) -> None:
    """The push service saying 410 for this phone comes back as gone, so the phone subscribes afresh."""
    subscription = {"endpoint": "https://push.example.com/old", "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
    admin.push.expired.add(subscription["endpoint"])
    with admin.client() as client:
        client.post("/api/push/subscribe", headers=SAME_ORIGIN, json=subscription)
        tested = client.post("/api/push/test", headers=SAME_ORIGIN, json={"endpoint": subscription["endpoint"]})
    assert tested.json() == {"delivered": 0, "gone": True}
    assert read_json(admin.settings.data_dir / SUBSCRIPTIONS_FILE)[0] == {"subscriptions": []}


def test_push_subscription_records_who_and_the_apps_origin(admin: Admin) -> None:
    """The subscription is stored for the signed-in login, with the page's origin as the VAPID subject."""
    subscription = {"endpoint": "https://push.example.com/phone", "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
    with admin.client(headers={"Tailscale-User-Login": "adriana@example.com"}) as client:
        client.post("/api/push/subscribe", headers=SAME_ORIGIN, json=subscription)
    (entry,) = read_json(admin.settings.data_dir / SUBSCRIPTIONS_FILE)[0]["subscriptions"]
    assert entry["who"] == "adriana@example.com" and entry["subject"] == BASE


def test_push_subscribe_refuses_what_is_not_a_subscription(admin: Admin) -> None:
    """A non-https endpoint or missing keys is a 400."""
    with admin.client() as client:
        response = client.post("/api/push/subscribe", headers=SAME_ORIGIN, json={"endpoint": "http://x"})
    assert response.status_code == 400 and response.json()["error"]


# Every API answer is uncached ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/me", None),
        ("GET", "/api/people", None),
        ("GET", "/api/status", None),
        ("GET", "/api/settings", None),
        ("GET", "/api/activity", None),
        ("GET", "/api/push/key", None),
        ("GET", "/api/groups", None),
        ("GET", "/api/nope", None),
        ("POST", "/api/sleep", None),
        ("POST", "/api/privacy", {"on": "x"}),
        ("PUT", "/api/people", {"people": [], "versions": {"contacts": "", "calls": ""}}),
    ],
)
def test_every_api_response_is_not_cached(admin: Admin, method: str, path: str, body: object) -> None:
    """Household data never lands in a browser or proxy cache, whatever the answer."""
    with admin.client() as client:
        response = client.request(method, path, headers=SAME_ORIGIN, json=body)
    assert response.headers["cache-control"] == "no-store"


def test_groups_without_whatsapp_is_not_found(admin: Admin) -> None:
    """No WhatsApp server configured: the group picker says so."""
    with admin.client() as client:
        response = client.get("/api/groups")
    assert response.status_code == 404 and "not set up" in response.json()["error"]


# Groups --------------------------------------------------------------------------------------------


@pytest.fixture
def whatsapp_admin(
    admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> tuple[Admin, AdminSettings, list[tuple[str, list[str]]]]:
    """Run the admin app against a fake WhatsApp server with two groups, recording the groups created."""
    created: list[tuple[str, list[str]]] = []

    async def groups(self: WhatsAppLink) -> list[tuple[str, str]]:
        return [("Reachy <> Sergio", GROUP), ("", "120363000000000002@g.us")]

    async def status(self: WhatsAppLink) -> ServerStatus:
        return ServerStatus(True, True, "", "351913705633")

    async def create_group(self: WhatsAppLink, title: str, phones: list[str]) -> str | None:
        created.append((title, phones))
        return "120363000000000099@g.us"

    monkeypatch.setattr(WhatsAppLink, "groups", groups)
    monkeypatch.setattr(WhatsAppLink, "status", status)
    monkeypatch.setattr(WhatsAppLink, "create_group", create_group)
    settings = AdminSettings(**{**admin.settings.__dict__, "whatsapp_url": "http://gowa", "whatsapp_auth": "u:p"})
    return admin, settings, created


def test_groups_are_listed_by_name_with_reachys_number(whatsapp_admin: tuple[Admin, AdminSettings, Any]) -> None:
    """Each group shows its name (or "Unnamed group") with its id, and Reachy's own number to add him elsewhere."""
    admin, settings, _ = whatsapp_admin
    with admin.client(settings=settings) as client:
        payload = client.get("/api/groups").json()
    assert payload == {
        "groups": [
            {"id": GROUP, "name": "Reachy <> Sergio"},
            {"id": "120363000000000002@g.us", "name": "Unnamed group"},
        ],
        "number": "+351913705633",
    }


def test_a_group_is_created_with_their_number_and_audited(whatsapp_admin: tuple[Admin, AdminSettings, Any]) -> None:
    """Creating a group passes the title and digits only, and the change is audited by title."""
    admin, settings, created = whatsapp_admin
    with admin.client(settings=settings) as client:
        response = client.post(
            "/api/groups", headers=SAME_ORIGIN, json={"title": " Reachy & Sergio ", "phones": ["+44 7450 000000"]}
        )
    assert response.json() == {"id": "120363000000000099@g.us", "name": "Reachy & Sergio"}
    assert created == [("Reachy & Sergio", ["447450000000"])]
    assert [(e["action"], e["target"]) for e in admin.audit()] == [("group created", "Reachy & Sergio")]


@pytest.mark.parametrize(
    "body",
    [{"title": "", "phones": ["+447450000000"]}, {"title": "a\nb", "phones": ["+447450000000"]},
     {"title": "Family", "phones": []}, {"title": "Family", "phones": ["12"]}, {"title": "Family", "phones": [7]}],
)  # fmt: skip
def test_a_group_needs_a_name_and_full_numbers(
    whatsapp_admin: tuple[Admin, AdminSettings, Any], body: dict[str, Any]
) -> None:
    """A blank or multi-line name, no members or a number without its country code is refused."""
    admin, settings, created = whatsapp_admin
    with admin.client(settings=settings) as client:
        assert client.post("/api/groups", headers=SAME_ORIGIN, json=body).status_code == 400
    assert created == [] and admin.audit() == []


# Gallery -------------------------------------------------------------------------------------------


def test_the_gallery_lists_serves_and_deletes_pictures(admin: Admin) -> None:
    """The gallery lists the display cache's pictures, serves a file and a thumbnail, and audits a delete."""
    folder = admin.settings.display_dir.parent / "drawings-v3"
    folder.mkdir(parents=True)
    Image.new("L", (800, 480), 255).save(folder / "duck.png")
    with admin.client() as client:
        items = client.get("/api/gallery").json()["items"]
        assert [(i["id"], i["kind"], i["title"]) for i in items] == [("drawings-v3/duck.png", "drawing", "Duck")]
        file = client.get("/api/gallery/file", params={"id": "drawings-v3/duck.png"})
        assert (file.status_code, file.headers["content-type"]) == (200, "image/png")
        thumb = client.get("/api/gallery/file", params={"id": "drawings-v3/duck.png", "thumb": "1"})
        assert thumb.headers["content-type"] == "image/jpeg"
        assert client.get("/api/gallery/file", params={"id": "../contacts.json"}).status_code == 404
        response = client.post("/api/gallery/delete", headers=SAME_ORIGIN, json={"id": "drawings-v3/duck.png"})
        assert response.json() == {"deleted": True}
        missing = client.post("/api/gallery/delete", headers=SAME_ORIGIN, json={"id": "drawings-v3/duck.png"})
        assert missing.status_code == 404
    assert not (folder / "duck.png").exists()
    assert [(e["action"], e["target"]) for e in admin.audit()] == [("picture deleted", "drawings-v3")]


# Costs and personality -----------------------------------------------------------------------------


def test_costs_by_period_and_only_known_periods(admin: Admin) -> None:
    """The breakdown answers for 1, 7 and 30 days and refuses any other period."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    get_ledger().path.write_text(json.dumps({"ts": now, "feature": "conversation", "cost_usd": 0.25}) + "\n")
    with admin.client() as client:
        week = client.get("/api/costs", params={"days": 7}).json()
        assert client.get("/api/costs", params={"days": 3}).status_code == 400
    assert week["total"] == 0.25 and week["features"] == [{"name": "conversation", "cost": 0.25, "count": 1}]


def test_personality_saves_are_audited_and_stale_ones_refused(
    admin: Admin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save is audited as a change; one based on an older version is a 409."""
    monkeypatch.setattr(server.config, "PERSONALITY_DIR", str(tmp_path / "personality"))
    with admin.client() as client:
        current = client.get("/api/personality").json()
        saved = client.put(
            "/api/personality",
            headers=SAME_ORIGIN,
            json={"text": current["text"] + " Be brief.", "version": current["version"]},
        )
        stale = client.put("/api/personality", headers=SAME_ORIGIN, json={"text": "x", "version": current["version"]})
    assert saved.json()["edited"] is True and stale.status_code == 409
    assert [(e["action"], e["target"]) for e in admin.audit()] == [("personality changed", "default")]


def test_memory_is_relayed_and_changes_audited_without_their_text(admin: Admin) -> None:
    """The admin app relays memory to and from the running app and audits who changed whose memory."""
    with admin.client() as client:
        listed = client.get("/api/memory")
        forgot = client.post("/api/memory/forget", headers=SAME_ORIGIN, json={"id": "m1"})
    assert listed.status_code == 200 and forgot.json() == {"removed": True, "subject": "enzo"}
    assert admin.control.calls("/memory/forget") == [{"id": "m1"}]
    assert [(e["action"], e["target"]) for e in admin.audit()] == [("memory forgotten", "enzo")]


def test_game_actions_are_relayed_and_audited(admin: Admin) -> None:
    """Starting a game from the phone reaches the app and the activity log names the game."""
    with admin.client() as client:
        assert (
            client.post("/api/game", json={"action": "start", "game": "colors"}, headers=SAME_ORIGIN).status_code
            == 200
        )
        assert client.post("/api/game", json={"action": "end"}, headers=SAME_ORIGIN).status_code == 200
    assert admin.control.calls("/game") == [
        {"action": "start", "game": "colors", "language": None},
        {"action": "end", "game": None, "language": None},
    ]
    assert [entry["action"] for entry in admin.audit()] == ["game end", "game start colors"]
