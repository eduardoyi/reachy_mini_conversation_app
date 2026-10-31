"""The Wake word card: its routes, restarting Reachy for a new wake head once he is idle, and the daily reminder."""

import io
import sys
import json
import time
import wave
from typing import Any
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from reachy_mini_conversation_app.admin import wake as wake_module
from reachy_mini_conversation_app.admin import server
from reachy_mini_conversation_app.admin.wake import WakeReminder
from reachy_mini_conversation_app.json_lines import JsonLines
from reachy_mini_conversation_app.admin.server import AdminSettings, create_app
from reachy_mini_conversation_app.admin.app_link import AppLink
from reachy_mini_conversation_app.admin.notifier import Notifier
from reachy_mini_conversation_app.wake_learning.store import HeadRecord, ReviewItem, WakeLearningStore


BASE = "http://reachy.example.ts.net"
EDUARDO = {"Tailscale-User-Login": "eduardo@example.com"}
SAME_ORIGIN = {"Origin": BASE}
SERVICE = "com.example.reachy"
LIVE = HeadRecord(name="official33", threshold=0.5, sha256="a", created="2026-10-30", metrics={})
SHADOW = HeadRecord(name="household-1031", threshold=0.4, sha256="b", created="2026-10-31", metrics={})
SUMMARY = {
    "calls": 41,
    "live_caught": 36,
    "shadow_caught": 41,
    "live_fires_per_day": 3.2,
    "shadow_fires_per_day": 3.0,
    "days": 1,
}
QUESTION = "Is someone calling Reachy?"
TZ = timezone(timedelta(hours=1))


class ControlAPI:
    """A fake control API: a status whose mode the test sets, "missed me" and voice recordings."""

    def __init__(self) -> None:
        """Start running and listening for his name, not in a call."""
        self.mode = "LOCAL_AMBIENT"
        self.in_call = False
        self.down = False
        self.missed_status = 200
        self.take: tuple[int, dict[str, Any]] = (200, {"ok": True, "seconds": 1.4, "level_db": -24.0, "reason": None})
        self.requests: list[tuple[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request (or fail to connect while down)."""
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        if request.url.path == "/status":
            privacy = {"on": False, "since": None}
            return httpx.Response(200, json={"mode": self.mode, "in_call": self.in_call, "privacy": privacy})
        if request.url.path == "/wake/missed":
            if self.missed_status != 200:
                return httpx.Response(self.missed_status, json={"error": "He isn't listening right now."})
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/wake/take":
            status, answer = self.take
            return httpx.Response(status, json=answer)
        return httpx.Response(200, json={})

    def calls(self, path: str) -> list[Any]:
        """Return the bodies sent to ``path``, oldest first."""
        return [body for called, body in self.requests if called == path]


class FakeNotifier(Notifier):
    """Records notifications, with their Android actions and url, instead of pushing them."""

    def __init__(self) -> None:
        """Every phone takes each notification until told otherwise."""
        self.sent: list[dict[str, Any]] = []
        self.delivered = 1

    async def notify(
        self, title: str, body: str, *, actions: list[dict[str, str]] | None = None, url: str | None = None
    ) -> int:
        """Record one notification."""
        self.sent.append({"title": title, "body": body, "actions": actions, "url": url})
        return self.delivered


@dataclass
class Wake:
    """One test's admin app over a temp wake-learning folder, a fake control API and a fake notifier."""

    settings: AdminSettings
    control: ControlAPI
    notifier: FakeNotifier
    store: WakeLearningStore

    @contextmanager
    def client(
        self, settings: AdminSettings | None = None, headers: dict[str, str] | None = None
    ) -> Iterator[TestClient]:
        """Run the admin app and yield a client signed in as Eduardo."""
        link = AppLink(
            "http://127.0.0.1:7861",
            self.settings.control_token_file,
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.control)),
        )
        app = create_app(settings or self.settings, link=link, notifier=self.notifier, wake_store=self.store)
        with TestClient(app, base_url=BASE, client=("127.0.0.1", 50000), headers=headers or EDUARDO) as client:
            yield client

    def audit(self) -> list[dict[str, Any]]:
        """Return the audit log, newest first."""
        return JsonLines(self.settings.data_dir / "audit.jsonl").recent()

    def with_restart(self, monkeypatch: pytest.MonkeyPatch) -> tuple[AdminSettings, list[str]]:
        """Return settings that can restart Reachy, and the list each restart lands in."""
        restarted: list[str] = []

        async def restart_app(service: str) -> None:
            restarted.append(service)

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(server, "restart_app", restart_app)
        return AdminSettings(**{**self.settings.__dict__, "app_service": SERVICE}), restarted


@pytest.fixture
def wake(tmp_path: Path) -> Wake:
    """Build an admin app whose daily reminder is off, so only the routes act."""
    token = tmp_path / "control-token"
    token.write_text("control-secret\n")
    env_file = tmp_path / ".env"
    env_file.write_text("WAKE_REMINDER_TIME=off\n")
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
        photos_dir=tmp_path / "photos",
    )
    store = WakeLearningStore(tmp_path / "wake-learning", clips=tmp_path / "wake-clips")
    return Wake(settings, ControlAPI(), FakeNotifier(), store)


def _queue(store: WakeLearningStore, count: int = 2) -> None:
    """Write ``count`` four-second clips and queue the middle two seconds of each to be checked."""
    items = []
    for index in range(count):
        path = store.clips / "2026-10-31" / f"clip-{index}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(16000)
            clip.writeframes(b"\x10\x00" * 64000)
        items.append(ReviewItem(f"c{index}", f"2026-10-31/clip-{index}.wav", 1.0, 3.0, QUESTION))
    store.write_queue(items)


def _propose(store: WakeLearningStore) -> None:
    store.set_live(LIVE)
    store.set_shadow(SHADOW)
    store.propose(SUMMARY)


def _recorded(store: WakeLearningStore, person: str, at: datetime) -> None:
    """Add a recording of ``person`` made at ``at``."""
    name = f"{person.lower()}-{at:%Y%m%d}.wav"
    store.recordings.mkdir(parents=True, exist_ok=True)
    (store.recordings / name).write_bytes(b"RIFF")
    made = at.astimezone(timezone.utc).isoformat(timespec="seconds")  # the store writes UTC
    entry = {"person": person, "prompt": "sofa", "file": name, "seconds": 1.0, "at": made}
    with (store.recordings / "recordings.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")


def _until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Wait for ``condition`` while the app's event loop runs in its own thread."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


# What the card shows ---------------------------------------------------------------------------------


def test_before_the_first_night_the_card_has_nothing_yet(wake: Wake) -> None:
    """No scoreboard, nothing to check, no heads, no recordings: the card can say learning starts tonight."""
    with wake.client() as client:
        response = client.get("/api/wake")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "scoreboard": {},
        "to_check": 0,
        "proposal": None,
        "live": None,
        "shadow": None,
        "undo": None,
        "history": [],
        "recordings": {},
        "reminder": "off",
    }


def test_the_card_shows_the_score_what_waits_and_who_recorded(wake: Wake) -> None:
    """The nightly scoreboard, the clips left, the proposal and each person's recordings come through."""
    _queue(wake.store, 3)
    _propose(wake.store)
    week = {"calls": 49, "first_time": 47, "false_wakes": 4, "days": 7, "false_wakes_per_day": 0.6}
    wake.store.write_scoreboard({"week": week, "targets": {"first_time": 0.95, "false_wakes_per_day": 1.0}})
    wake.store.save_recording("Lucía", "sofa", 16000, b"\x00\x00" * 1600)
    wake.store.answer("c0", "call", "adriana@example.com")
    wake.settings.env_file.write_text("")
    with wake.client() as client:
        summary = client.get("/api/wake").json()
    assert summary["scoreboard"]["week"] == week and summary["scoreboard"]["made"]
    assert summary["to_check"] == 2
    assert summary["proposal"]["head"] == SHADOW.name and summary["proposal"]["summary"] == SUMMARY
    assert summary["live"]["name"] == LIVE.name and summary["shadow"]["name"] == SHADOW.name
    assert summary["recordings"]["Lucía"]["count"] == 1
    assert summary["reminder"] == "19:30"


def test_clips_to_check_are_listed_and_played_without_being_cached(wake: Wake) -> None:
    """The queue gives each clip's question and length; the clip is that part of the file, as WAV, never stored."""
    _queue(wake.store)
    with wake.client() as client:
        items = client.get("/api/wake/queue").json()["items"]
        clip = client.get("/api/wake/clip", params={"item": "c1"})
        unknown = client.get("/api/wake/clip", params={"item": "nope"})
        (wake.store.clips / "2026-10-31" / "clip-0.wav").unlink()
        gone = client.get("/api/wake/clip", params={"item": "c0"})
    assert items == [
        {"id": "c0", "question": QUESTION, "seconds": 2.0},
        {"id": "c1", "question": QUESTION, "seconds": 2.0},
    ]
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/wav"
    assert clip.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(clip.content)) as audio:
        assert audio.getnframes() == 32000 and audio.getframerate() == 16000
    assert unknown.status_code == 404 and gone.status_code == 404


def test_an_answer_is_kept_with_who_gave_it_and_audited(wake: Wake) -> None:
    """Each answer is stored at once, leaves the queue and is audited without any content."""
    _queue(wake.store)
    with wake.client(headers={"Tailscale-User-Login": "adriana@example.com"}) as client:
        response = client.post("/api/wake/answer", headers=SAME_ORIGIN, json={"item": "c0", "answer": "not_call"})
        left = client.get("/api/wake/queue").json()["items"]
    assert response.json() == {"answered": True}
    assert wake.store.answers() == {"c0": "not_call"}
    assert [item["id"] for item in left] == ["c1"]
    (entry,) = wake.audit()
    assert (entry["who"], entry["action"], entry["target"]) == ("adriana@example.com", "wake clip checked", "not_call")


@pytest.mark.parametrize(
    "body,status",
    [
        ({"item": "c0", "answer": "maybe"}, 400),
        ({"item": "c0"}, 400),
        ({"item": "nope", "answer": "call"}, 404),
        ({"answer": "call"}, 404),
    ],
)
def test_bad_answers_and_unknown_clips_are_refused(wake: Wake, body: dict[str, Any], status: int) -> None:
    """Only call, not_call or unsure, for a clip still waiting; nothing is kept or audited otherwise."""
    _queue(wake.store)
    with wake.client() as client:
        response = client.post("/api/wake/answer", headers=SAME_ORIGIN, json=body)
    assert response.status_code == status and response.json()["error"]
    assert wake.store.answers() == {} and wake.audit() == []


def test_a_clip_already_checked_on_the_other_phone_is_refused(wake: Wake) -> None:
    """The second answer to the same clip is a 404, so the phone just moves on."""
    _queue(wake.store)
    with wake.client() as client:
        first = client.post("/api/wake/answer", headers=SAME_ORIGIN, json={"item": "c0", "answer": "call"})
        second = client.post("/api/wake/answer", headers=SAME_ORIGIN, json={"item": "c0", "answer": "not_call"})
    assert first.status_code == 200 and second.status_code == 404
    assert wake.store.answers() == {"c0": "call"}


def test_wake_routes_need_a_household_login_and_the_apps_own_origin(wake: Wake) -> None:
    """The same guard as every other route: no login, no clip; no origin, no change."""
    _queue(wake.store)
    with wake.client(headers={"Tailscale-User-Login": "guest@example.com"}) as client:
        assert client.get("/api/wake").status_code == 403
        assert client.get("/api/wake/clip", params={"item": "c0"}).status_code == 403
    with wake.client() as client:
        assert client.post("/api/wake/answer", json={"item": "c0", "answer": "call"}).status_code == 403
        assert client.post("/api/wake/missed").status_code == 403
    assert wake.store.answers() == {} and wake.control.calls("/wake/missed") == []


# "He missed me" and voice recordings -------------------------------------------------------------------


def test_he_missed_me_is_relayed_with_who_and_audited(wake: Wake) -> None:
    """The app marks the last minute for tonight; the phone's login says who."""
    with wake.client() as client:
        response = client.post("/api/wake/missed", headers=SAME_ORIGIN)
    assert response.json() == {"ok": True}
    assert wake.control.calls("/wake/missed") == [{"who": "eduardo@example.com"}]
    assert [entry["action"] for entry in wake.audit()] == ["wake miss marked"]


def test_he_missed_me_when_he_is_not_listening_is_relayed_without_audit(wake: Wake) -> None:
    """Privacy, quiet hours or a conversation: the app's 409 reaches the phone; Reachy down is a 503."""
    wake.control.missed_status = 409
    with wake.client() as client:
        refused = client.post("/api/wake/missed", headers=SAME_ORIGIN)
        wake.control.down = True
        down = client.post("/api/wake/missed", headers=SAME_ORIGIN)
    assert refused.status_code == 409 and refused.json()["error"] == "He isn't listening right now."
    assert down.status_code == 503 and wake.audit() == []


def test_a_take_is_relayed_and_audited_when_he_heard_it(wake: Wake) -> None:
    """The take's level comes back; a heard take is audited with whose voice, a missed one is not."""
    with wake.client() as client:
        heard = client.post("/api/wake/take", headers=SAME_ORIGIN, json={"person": " Lucía ", "prompt": "sofa"})
        wake.control.take = (200, {"ok": False, "seconds": 0.0, "level_db": -61.0, "reason": "quiet"})
        quiet = client.post("/api/wake/take", headers=SAME_ORIGIN, json={"person": "Lucía", "prompt": "loud"})
    assert heard.json()["ok"] is True and heard.json()["level_db"] == -24.0
    assert quiet.json() == {"ok": False, "seconds": 0.0, "level_db": -61.0, "reason": "quiet"}
    assert wake.control.calls("/wake/take") == [
        {"person": "Lucía", "prompt": "sofa"},
        {"person": "Lucía", "prompt": "loud"},
    ]
    (entry,) = wake.audit()
    assert (entry["action"], entry["target"]) == ("wake call recorded", "Lucía")


def test_a_take_while_he_cannot_record_is_relayed(wake: Wake) -> None:
    """The app's 409 (in a conversation, privacy on) reaches the phone as is."""
    wake.control.take = (409, {"error": "He can't record right now."})
    with wake.client() as client:
        response = client.post("/api/wake/take", headers=SAME_ORIGIN, json={"person": "Enzo", "prompt": "sofa"})
    assert response.status_code == 409 and response.json() == {"error": "He can't record right now."}
    assert wake.audit() == []


@pytest.mark.parametrize(
    "body",
    [
        {"person": "", "prompt": "sofa"},
        {"person": "  ", "prompt": "sofa"},
        {"person": "x" * 61, "prompt": "sofa"},
        {"person": "Enzo\nGEMINI", "prompt": "sofa"},
        {"person": "Enzo", "prompt": 3},
        {"person": "Enzo", "prompt": "x" * 201},
        {"prompt": "sofa"},
    ],
)
def test_a_take_needs_a_name_and_a_short_prompt(wake: Wake, body: dict[str, Any]) -> None:
    """Who is calling him is required, one line each; nothing reaches the app otherwise."""
    with wake.client() as client:
        response = client.post("/api/wake/take", headers=SAME_ORIGIN, json=body)
    assert response.status_code == 400 and wake.control.calls("/wake/take") == []


# Switching the wake head -------------------------------------------------------------------------------


def test_switching_while_he_is_idle_restarts_him_at_once(wake: Wake, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shadow head goes live, the old one can come back, and Reachy restarts now to load it."""
    _propose(wake.store)
    settings, restarted = wake.with_restart(monkeypatch)
    with wake.client(settings=settings) as client:
        response = client.post("/api/wake/switch", headers=SAME_ORIGIN)
        summary = client.get("/api/wake").json()
    assert response.json() == {
        "head": SHADOW.name,
        "restart": "now",
        "message": "Switching now. He's back in a minute.",
    }
    assert restarted == [SERVICE]
    assert wake.store.live() == SHADOW and wake.store.shadow() is None and wake.store.proposal() is None
    assert summary["undo"]["to"]["name"] == SHADOW.name
    assert datetime.fromisoformat(summary["undo"]["until"]) - datetime.fromisoformat(
        summary["undo"]["at"]
    ) == timedelta(days=7)
    (entry,) = wake.audit()
    assert (entry["action"], entry["target"]) == ("wake model switched", SHADOW.name)


def test_switching_while_he_is_busy_restarts_him_once_he_is_idle(wake: Wake, monkeypatch: pytest.MonkeyPatch) -> None:
    """In a conversation, then idle but in a call: no restart; idle and free: one restart."""
    monkeypatch.setattr(wake_module, "IDLE_CHECK_S", 0.01)
    _propose(wake.store)
    settings, restarted = wake.with_restart(monkeypatch)
    wake.control.mode = "CLOUD_CONVERSATION"
    with wake.client(settings=settings) as client:
        response = client.post("/api/wake/switch", headers=SAME_ORIGIN)
        assert response.json()["restart"] == "when_free"
        assert response.json()["message"] == "Switching when he's free."
        assert not _until(lambda: bool(restarted), timeout=0.2)
        wake.control.mode, wake.control.in_call = "LOCAL_AMBIENT", True
        assert not _until(lambda: bool(restarted), timeout=0.2)
        wake.control.in_call = False
        assert _until(lambda: bool(restarted))
        time.sleep(0.1)
    assert restarted == [SERVICE]
    assert wake.store.live() == SHADOW


def test_a_busy_switch_gives_up_after_the_wait(wake: Wake, monkeypatch: pytest.MonkeyPatch) -> None:
    """Busy for the whole wait: no restart; the live head loads at his next one."""
    monkeypatch.setattr(wake_module, "IDLE_CHECK_S", 0.01)
    monkeypatch.setattr(wake_module, "IDLE_WAIT_S", 0.1)
    _propose(wake.store)
    settings, restarted = wake.with_restart(monkeypatch)
    wake.control.mode = "CLOUD_CONVERSATION"
    with wake.client(settings=settings) as client:
        client.post("/api/wake/switch", headers=SAME_ORIGIN)
        checks = len(wake.control.calls("/status"))
        assert _until(lambda: len(wake.control.calls("/status")) > checks + 3)
        time.sleep(0.3)
        settled = len(wake.control.calls("/status"))
        time.sleep(0.2)
        assert len(wake.control.calls("/status")) == settled
    assert restarted == []


def test_switching_without_a_restart_service_says_to_restart(wake: Wake) -> None:
    """No ADMIN_APP_SERVICE: the switch happens and the phone is told to restart Reachy."""
    _propose(wake.store)
    with wake.client() as client:
        response = client.post("/api/wake/switch", headers=SAME_ORIGIN)
    assert response.json()["restart"] == "manual" and wake.store.live() == SHADOW


def test_switching_while_reachy_is_stopped_waits_for_his_next_start(
    wake: Wake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reachy not running: nothing is started; he loads the head when he next starts."""
    _propose(wake.store)
    settings, restarted = wake.with_restart(monkeypatch)
    wake.control.down = True
    with wake.client(settings=settings) as client:
        response = client.post("/api/wake/switch", headers=SAME_ORIGIN)
    assert response.json()["restart"] == "on_start" and restarted == []


def test_keeping_the_current_model_declines_but_switch_stays_possible(wake: Wake) -> None:
    """Keeping the current model declines the proposal; it stays on the sheet, and Switch still works."""
    _propose(wake.store)
    with wake.client() as client:
        kept = client.post("/api/wake/keep", headers=SAME_ORIGIN)
        proposal = client.get("/api/wake").json()["proposal"]
        switched = client.post("/api/wake/switch", headers=SAME_ORIGIN)
    assert kept.json() == {"kept": SHADOW.name}
    assert proposal["declined"] is True and proposal["declined_by"] == "eduardo@example.com"
    assert switched.status_code == 200 and wake.store.live() == SHADOW
    assert [entry["action"] for entry in reversed(wake.audit())] == ["wake model kept", "wake model switched"]


def test_undo_puts_the_previous_head_back_and_restarts_him(wake: Wake, monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo restores the head the switch replaced, restarts Reachy, and is recorded in the history."""
    _propose(wake.store)
    settings, restarted = wake.with_restart(monkeypatch)
    with wake.client(settings=settings) as client:
        client.post("/api/wake/switch", headers=SAME_ORIGIN)
        assert _until(lambda: restarted == [SERVICE])
        response = client.post("/api/wake/undo", headers=SAME_ORIGIN)
        summary = client.get("/api/wake").json()
    assert response.json() == {
        "head": LIVE.name,
        "restart": "now",
        "message": "Going back now. He's back in a minute.",
    }
    assert restarted == [SERVICE, SERVICE] and wake.store.live() == LIVE
    assert summary["undo"] is None
    assert [entry.get("undo", False) for entry in summary["history"]] == [True, False]
    assert wake.audit()[0]["action"] == "wake model switch undone" and wake.audit()[0]["target"] == LIVE.name


@pytest.mark.parametrize("path", ["/api/wake/switch", "/api/wake/keep", "/api/wake/undo"])
def test_switch_keep_and_undo_with_nothing_pending_are_refused(wake: Wake, path: str) -> None:
    """No proposal (or no switch to undo): a 409 with a reason, nothing changed or audited."""
    wake.store.set_live(LIVE)
    with wake.client() as client:
        response = client.post(path, headers=SAME_ORIGIN)
    assert response.status_code == 409 and response.json()["error"]
    assert wake.store.live() == LIVE and wake.audit() == []


# The daily reminder ------------------------------------------------------------------------------------


class Clock:
    """A local time the test moves."""

    def __init__(self, start: datetime) -> None:
        """Start at ``start``."""
        self.now = start

    def __call__(self) -> datetime:
        """Return the current time."""
        return self.now


def _reminder(
    tmp_path: Path, store: WakeLearningStore, clock: Clock, env: str = "QUIET_HOURS=21:30-07:00\n"
) -> tuple[WakeReminder, FakeNotifier]:
    env_file = tmp_path / ".env"
    env_file.write_text(env)
    notifier = FakeNotifier()
    return WakeReminder(store, notifier, env_file, tmp_path / "admin" / "wake-reminder.json", now=clock), notifier


@pytest.fixture
def store(tmp_path: Path) -> WakeLearningStore:
    """Return an empty wake-learning folder."""
    return WakeLearningStore(tmp_path / "wake-learning", clips=tmp_path / "wake-clips")


@pytest.mark.asyncio
async def test_the_reminder_bundles_what_waits_once_a_day_at_the_time(
    tmp_path: Path, store: WakeLearningStore
) -> None:
    """At 19:30: one push with the new model, the clips and a voice refresh; nothing more that day."""
    _queue(store, 6)
    _propose(store)
    clock = Clock(datetime(2026, 10, 31, 19, 29, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock)
    await reminder.check()
    assert notifier.sent == []
    clock.now = clock.now.replace(minute=30)
    await reminder.check()
    clock.now = clock.now.replace(minute=45)
    await reminder.check()
    assert notifier.sent == [
        {
            "title": "Wake word",
            "body": "A better wake model is ready: it caught 5 more of yesterday's 41 calls, with no more false fires. "
            "Switch?\n6 clips to check, about a minute.\n2-minute voice refresh? Nobody has recorded their call yet.",
            "actions": [{"action": "switch", "title": "Switch"}, {"action": "later", "title": "Not now"}],
            "url": "/#wake",
        }
    ]
    assert (store.proposal() or {})["notified"] is True


@pytest.mark.asyncio
async def test_the_next_day_only_what_still_waits_is_sent(tmp_path: Path, store: WakeLearningStore) -> None:
    """The proposal and the refresh were told yesterday; the clips still waiting get "Check now"."""
    _queue(store, 2)
    _propose(store)
    clock = Clock(datetime(2026, 10, 31, 19, 30, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock)
    await reminder.check()
    clock.now += timedelta(days=1)
    await reminder.check()
    assert len(notifier.sent) == 2
    assert notifier.sent[1]["body"] == "2 clips to check, about a minute."
    assert notifier.sent[1]["actions"] == [{"action": "check", "title": "Check now"}]


@pytest.mark.asyncio
async def test_nothing_waiting_sends_nothing(tmp_path: Path, store: WakeLearningStore) -> None:
    """No clips, no new model, everyone recorded lately: no push."""
    _recorded(store, "Eduardo", datetime(2026, 10, 20, tzinfo=timezone.utc))
    clock = Clock(datetime(2026, 10, 31, 19, 30, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock)
    await reminder.check()
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_a_declined_or_already_told_proposal_is_not_pushed_again(
    tmp_path: Path, store: WakeLearningStore
) -> None:
    """Only a proposal nobody has been told about goes in the push."""
    _recorded(store, "Eduardo", datetime(2026, 10, 30, tzinfo=timezone.utc))
    _propose(store)
    store.decline("eduardo@example.com")
    clock = Clock(datetime(2026, 10, 31, 20, 0, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock)
    await reminder.check()
    assert notifier.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env,at",
    [
        ("QUIET_HOURS=21:30-07:00\nWAKE_REMINDER_TIME=22:00\n", (22, 0)),
        ("QUIET_HOURS=21:30-07:00\nWAKE_REMINDER_TIME=06:30\n", (6, 30)),
        ("WAKE_REMINDER_TIME=off\n", (19, 30)),
    ],
)
async def test_no_reminder_in_quiet_hours_or_when_turned_off(
    tmp_path: Path, store: WakeLearningStore, env: str, at: tuple[int, int]
) -> None:
    """A reminder time inside quiet hours never fires; "off" turns it off."""
    _queue(store, 3)
    clock = Clock(datetime(2026, 10, 31, *at, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock, env)
    for _ in range(3):
        await reminder.check()
        clock.now += timedelta(minutes=10)
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_the_reminder_time_comes_from_the_settings_file(tmp_path: Path, store: WakeLearningStore) -> None:
    """A time changed in the app applies to the next check, without restarting anything."""
    _queue(store, 1)
    clock = Clock(datetime(2026, 10, 31, 8, 15, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock)
    await reminder.check()
    (tmp_path / ".env").write_text("QUIET_HOURS=21:30-07:00\nWAKE_REMINDER_TIME=08:00\n")
    await reminder.check()
    assert [sent["body"].splitlines()[0] for sent in notifier.sent] == ["1 clip to check, about a minute."]


@pytest.mark.asyncio
async def test_a_voice_refresh_names_who_has_not_recorded_for_a_month(
    tmp_path: Path, store: WakeLearningStore
) -> None:
    """Monthly: Lucía recorded 40 days ago, Eduardo last week; only Lucía is named, and not again for a month."""
    now = datetime(2026, 10, 31, 19, 30, tzinfo=TZ)
    _recorded(store, "Lucía", now - timedelta(days=40))
    _recorded(store, "Eduardo", now - timedelta(days=7))
    clock = Clock(now)
    reminder, notifier = _reminder(tmp_path, store, clock)
    for _ in range(31):
        await reminder.check()
        clock.now += timedelta(days=1)
    assert [sent["body"] for sent in notifier.sent] == [
        "2-minute voice refresh? Lucía hasn't recorded in a month.",
        "2-minute voice refresh? Eduardo and Lucía haven't recorded in a month.",
    ]
    assert notifier.sent[0]["actions"] == [] and notifier.sent[0]["url"] == "/#wake"


@pytest.mark.asyncio
async def test_the_day_is_remembered_across_restarts(tmp_path: Path, store: WakeLearningStore) -> None:
    """The admin app restarting after the reminder does not send it again that day."""
    _queue(store, 2)
    clock = Clock(datetime(2026, 10, 31, 19, 30, tzinfo=TZ))
    first, notifier = _reminder(tmp_path, store, clock)
    await first.check()
    again = WakeReminder(store, notifier, tmp_path / ".env", tmp_path / "admin" / "wake-reminder.json", now=clock)
    clock.now = clock.now.replace(minute=50)
    await again.check()
    assert len(notifier.sent) == 1


@pytest.mark.asyncio
async def test_an_undelivered_reminder_leaves_the_proposal_to_tell(tmp_path: Path, store: WakeLearningStore) -> None:
    """No phone took it: the proposal stays untold for tomorrow's push, and today is not retried every minute."""
    _propose(store)
    clock = Clock(datetime(2026, 10, 31, 19, 30, tzinfo=TZ))
    reminder, notifier = _reminder(tmp_path, store, clock)
    notifier.delivered = 0
    await reminder.check()
    await reminder.check()
    assert len(notifier.sent) == 1 and (store.proposal() or {})["notified"] is False
    notifier.delivered = 1
    clock.now += timedelta(days=1)
    await reminder.check()
    assert "A better wake model is ready" in notifier.sent[1]["body"]
    assert (store.proposal() or {})["notified"] is True
