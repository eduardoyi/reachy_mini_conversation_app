"""Push subscriptions, delivery to subscribed phones, and the watch that decides what is worth a notification."""

import json
import asyncio
from typing import Any
from pathlib import Path
from collections.abc import Callable, Awaitable

import httpx
import pytest
from cryptography.hazmat.primitives import hmac, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reachy_mini_conversation_app.admin import notifier as notifier_module
from reachy_mini_conversation_app.json_lines import JsonLines
from reachy_mini_conversation_app.admin.records import read_json
from reachy_mini_conversation_app.admin.notifier import (
    KEY_FILE,
    MAX_SUBSCRIPTIONS,
    PRIVACY_REMINDER_S,
    SUBSCRIPTIONS_FILE,
    Watch,
    Notifier,
)
from reachy_mini_conversation_app.admin.web_push import unb64url


# RFC 8291 Appendix A: the user agent's key pair and auth secret, so pushed messages can be decrypted here.
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
SUBJECT = "https://reachy.example.ts.net"


def _subscription(endpoint: str) -> dict[str, Any]:
    return {"endpoint": endpoint, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}


def _hmac(key: bytes, message: bytes) -> bytes:
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(message)
    return mac.finalize()


def _decrypt(body: bytes) -> dict[str, Any]:
    """Decrypt the aes128gcm record with the subscription's private key, as the phone does."""
    salt, key_length = body[:16], body[20]
    as_public, ciphertext = body[21 : 21 + key_length], body[21 + key_length :]
    ua_private = ec.derive_private_key(int.from_bytes(unb64url(UA_PRIVATE), "big"), ec.SECP256R1())
    secret = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hmac(_hmac(unb64url(AUTH), secret), b"WebPush: info\x00" + unb64url(UA_PUBLIC) + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    key = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    assert plaintext.endswith(b"\x02")
    decoded: dict[str, Any] = json.loads(plaintext[:-1])
    return decoded


class PushService:
    """A fake push service answering each endpoint with a chosen status (or a transport error)."""

    def __init__(self, statuses: dict[str, int | None] | None = None) -> None:
        """``statuses`` maps endpoint URLs to an HTTP status, or None for an unreachable service."""
        self.statuses = statuses or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the push and answer with the endpoint's status."""
        self.requests.append(request)
        status = self.statuses.get(str(request.url), 201)
        if status is None:
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(status)


def _notifier(directory: Path, service: PushService) -> Notifier:
    return Notifier(directory, client=httpx.AsyncClient(transport=httpx.MockTransport(service)))


def _endpoints(directory: Path) -> list[str]:
    return [entry["endpoint"] for entry in read_json(directory / SUBSCRIPTIONS_FILE)[0]["subscriptions"]]


# Notifier ----------------------------------------------------------------------------------------------


def test_notifier_creates_its_key_once(tmp_path: Path) -> None:
    """The public key is stable across restarts of the admin app."""
    first = _notifier(tmp_path, PushService()).public_key
    assert (tmp_path / KEY_FILE).is_file()
    assert _notifier(tmp_path, PushService()).public_key == first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subscription",
    [
        {},
        {"endpoint": "http://push.example.com/1", "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}},
        {"endpoint": 3, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}},
        {"endpoint": "https://push.example.com/1"},
        {"endpoint": "https://push.example.com/1", "keys": ["x"]},
        {"endpoint": "https://push.example.com/1", "keys": {"p256dh": UA_PUBLIC}},
        {"endpoint": "https://push.example.com/1", "keys": {"p256dh": 1, "auth": AUTH}},
    ],
)
async def test_subscribe_refuses_what_is_not_a_push_subscription(tmp_path: Path, subscription: dict[str, Any]) -> None:
    """Only https endpoints with text keys are kept; nothing is written otherwise."""
    with pytest.raises(ValueError):
        await _notifier(tmp_path, PushService()).subscribe(subscription, "eduardo@example.com", SUBJECT)
    assert not (tmp_path / SUBSCRIPTIONS_FILE).exists()


@pytest.mark.asyncio
async def test_subscribe_keeps_only_the_subscription_fields(tmp_path: Path) -> None:
    """The stored entry has the endpoint, keys, who subscribed and the admin app's URL, nothing extra."""
    notifier = _notifier(tmp_path, PushService())
    raw = {**_subscription("https://push.example.com/1"), "expirationTime": None, "extra": "dropped"}
    raw["keys"] = {**raw["keys"], "other": "dropped"}
    await notifier.subscribe(raw, "eduardo@example.com", SUBJECT)
    assert read_json(tmp_path / SUBSCRIPTIONS_FILE)[0] == {
        "subscriptions": [
            {
                "endpoint": "https://push.example.com/1",
                "keys": {"p256dh": UA_PUBLIC, "auth": AUTH},
                "who": "eduardo@example.com",
                "subject": SUBJECT,
            }
        ]
    }


@pytest.mark.asyncio
async def test_subscribing_the_same_endpoint_replaces_it(tmp_path: Path) -> None:
    """A phone that subscribes again is listed once, with the newest details, at the end."""
    notifier = _notifier(tmp_path, PushService())
    await notifier.subscribe(_subscription("https://push.example.com/1"), "eduardo@example.com", SUBJECT)
    await notifier.subscribe(_subscription("https://push.example.com/2"), "adriana@example.com", SUBJECT)
    await notifier.subscribe(_subscription("https://push.example.com/1"), "adriana@example.com", SUBJECT)
    entries = read_json(tmp_path / SUBSCRIPTIONS_FILE)[0]["subscriptions"]
    assert [(e["endpoint"], e["who"]) for e in entries] == [
        ("https://push.example.com/2", "adriana@example.com"),
        ("https://push.example.com/1", "adriana@example.com"),
    ]


@pytest.mark.asyncio
async def test_subscriptions_are_capped_keeping_the_newest(tmp_path: Path) -> None:
    """Past MAX_SUBSCRIPTIONS the oldest are forgotten."""
    notifier = _notifier(tmp_path, PushService())
    for index in range(MAX_SUBSCRIPTIONS + 3):
        await notifier.subscribe(_subscription(f"https://push.example.com/{index}"), "e@example.com", SUBJECT)
    assert _endpoints(tmp_path) == [f"https://push.example.com/{i}" for i in range(3, MAX_SUBSCRIPTIONS + 3)]


@pytest.mark.asyncio
async def test_unsubscribe_forgets_only_that_endpoint(tmp_path: Path) -> None:
    """Unsubscribing one phone leaves the others; an unknown endpoint changes nothing."""
    notifier = _notifier(tmp_path, PushService())
    await notifier.subscribe(_subscription("https://push.example.com/1"), "e@example.com", SUBJECT)
    await notifier.subscribe(_subscription("https://push.example.com/2"), "a@example.com", SUBJECT)
    await notifier.unsubscribe("https://push.example.com/1")
    await notifier.unsubscribe("https://push.example.com/unknown")
    assert _endpoints(tmp_path) == ["https://push.example.com/2"]


@pytest.mark.asyncio
async def test_notify_with_no_subscriptions_sends_nothing(tmp_path: Path) -> None:
    """No phones: nothing delivered, no request made."""
    service = PushService()
    assert await _notifier(tmp_path, service).notify("Reachy", "Hi") == 0
    assert service.requests == []


@pytest.mark.asyncio
async def test_notify_counts_deliveries_and_forgets_dropped_subscriptions(tmp_path: Path) -> None:
    """Accepted pushes are counted; 404/410 subscriptions are forgotten; errors and outages are kept."""
    service = PushService(
        {
            "https://push.example.com/gone-404": 404,
            "https://push.example.com/gone-410": 410,
            "https://push.example.com/error": 500,
            "https://push.example.com/down": None,
        }
    )
    notifier = _notifier(tmp_path, service)
    endpoints = [
        "https://push.example.com/ok",
        "https://push.example.com/gone-404",
        "https://push.example.com/gone-410",
        "https://push.example.com/error",
        "https://push.example.com/down",
        "https://push.example.com/ok-too",
    ]
    for endpoint in endpoints:
        await notifier.subscribe(_subscription(endpoint), "e@example.com", SUBJECT)

    assert await notifier.notify("Reachy stopped", "The conversation app is not running.") == 2
    assert [str(r.url) for r in service.requests] == endpoints
    assert _endpoints(tmp_path) == [
        "https://push.example.com/ok",
        "https://push.example.com/error",
        "https://push.example.com/down",
        "https://push.example.com/ok-too",
    ]


@pytest.mark.asyncio
async def test_notify_sends_the_title_and_body_only_the_phone_can_read(tmp_path: Path) -> None:
    """The pushed body decrypts, with the subscription's private key, to exactly the title and body."""
    service = PushService()
    notifier = _notifier(tmp_path, service)
    await notifier.subscribe(_subscription("https://push.example.com/1"), "e@example.com", SUBJECT)
    await notifier.notify("Missed call", "Abuela called Reachy.")
    (request,) = service.requests
    assert b"Abuela" not in request.content
    assert _decrypt(request.content) == {"title": "Missed call", "body": "Abuela called Reachy."}
    assert request.headers["authorization"].startswith("vapid t=")


@pytest.mark.asyncio
async def test_notify_carries_android_actions_and_the_url_a_tap_opens(tmp_path: Path) -> None:
    """A wake-word push adds its buttons and the page to open; the phone reads them after decrypting."""
    service = PushService()
    notifier = _notifier(tmp_path, service)
    await notifier.subscribe(_subscription("https://push.example.com/1"), "e@example.com", SUBJECT)
    actions = [{"action": "switch", "title": "Switch"}, {"action": "later", "title": "Not now"}]
    await notifier.notify("Wake word", "A better wake model is ready.", actions=actions, url="/#wake")
    (request,) = service.requests
    assert _decrypt(request.content) == {
        "title": "Wake word",
        "body": "A better wake model is ready.",
        "actions": actions,
        "url": "/#wake",
    }


@pytest.mark.asyncio
async def test_notify_with_unreadable_subscriptions_sends_nothing(tmp_path: Path) -> None:
    """A corrupt subscriptions file is reported, not raised."""
    service = PushService()
    notifier = _notifier(tmp_path, service)
    (tmp_path / SUBSCRIPTIONS_FILE).write_text("{broken")
    assert await notifier.notify("Reachy", "Hi") == 0
    assert service.requests == []


@pytest.mark.asyncio
async def test_one_malformed_subscription_does_not_block_the_others(tmp_path: Path) -> None:
    """A subscription with unusable keys must not stop delivery to the good ones."""
    service = PushService()
    notifier = _notifier(tmp_path, service)
    await notifier.subscribe(_subscription("https://push.example.com/good"), "e@example.com", SUBJECT)
    bad = {"endpoint": "https://push.example.com/bad", "keys": {"p256dh": "x", "auth": "y"}}
    try:
        await notifier.subscribe(bad, "a@example.com", SUBJECT)
    except ValueError:
        pass  # refusing it at subscribe time is a fine fix too
    assert await notifier.notify("Reachy", "Hi") == 1


# Watch -------------------------------------------------------------------------------------------------


class FakeNotifier(Notifier):
    """Records notifications instead of pushing them."""

    def __init__(self) -> None:
        """No key, no subscriptions."""
        self.sent: list[tuple[str, str]] = []

    async def notify(self, title: str, body: str) -> int:
        """Record one notification."""
        self.sent.append((title, body))
        return 1


def _health(
    *,
    running: bool = True,
    whatsapp: bool | None = True,
    detail: str = "",
    calls: bool | None = True,
    privacy: bool = False,
    since: str | None = None,
    on_for_s: float | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {"on": privacy, "since": since}
    if on_for_s is not None:
        state["on_for_s"] = on_for_s
    return {
        "app": {"running": running},
        "whatsapp": {"ok": whatsapp, "detail": detail},
        "calls": {"ok": calls, "detail": ""},
        "privacy": state,
    }


def _sequence(*checks: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    remaining = list(checks)

    async def health() -> dict[str, Any]:
        return remaining.pop(0)

    return health


def _watch(tmp_path: Path, *checks: dict[str, Any]) -> tuple[Watch, FakeNotifier, JsonLines]:
    fake = FakeNotifier()
    activity = JsonLines(tmp_path / "activity.jsonl")
    return Watch(fake, _sequence(*checks), activity), fake, activity


async def _run_checks(watch: Watch, count: int) -> None:
    for _ in range(count):
        await watch.check()


@pytest.mark.asyncio
async def test_first_check_sends_nothing_even_when_things_are_down(tmp_path: Path) -> None:
    """The first check only learns the state; it has nothing to compare with."""
    watch, fake, _ = _watch(tmp_path, _health(running=False, whatsapp=False, calls=False))
    await watch.check()
    assert fake.sent == []


@pytest.mark.asyncio
async def test_app_stopping_is_notified_once(tmp_path: Path) -> None:
    """Running then not running is one notification; staying down is not repeated."""
    watch, fake, _ = _watch(tmp_path, _health(), _health(running=False), _health(running=False), _health())
    await _run_checks(watch, 4)
    assert fake.sent == [("Reachy stopped", "The conversation app is not running.")]


@pytest.mark.asyncio
async def test_app_stopping_during_a_restart_from_the_app_is_not_notified(tmp_path: Path) -> None:
    """A restart started from the admin app takes Reachy down on purpose; that is not news."""
    restarting = _health(running=False)
    restarting["app"]["restarting"] = True
    watch, fake, _ = _watch(tmp_path, _health(), restarting, _health())
    await _run_checks(watch, 3)
    assert fake.sent == []


@pytest.mark.asyncio
async def test_whatsapp_unlinking_is_notified_with_its_detail(tmp_path: Path) -> None:
    """WhatsApp going from linked to not linked is notified, with the server's message when there is one."""
    watch, fake, _ = _watch(
        tmp_path,
        _health(),
        _health(whatsapp=False, detail="Logged out from phone"),
        _health(),
        _health(whatsapp=False),
    )
    await _run_checks(watch, 4)
    assert fake.sent == [
        ("WhatsApp unlinked", "Logged out from phone"),
        ("WhatsApp unlinked", "Reachy's WhatsApp is not linked."),
    ]


@pytest.mark.asyncio
async def test_unknown_states_are_not_notified(tmp_path: Path) -> None:
    """Not set up (None) is not "worse": None to False and True to None send nothing."""
    watch, fake, _ = _watch(
        tmp_path,
        _health(whatsapp=None, calls=None),
        _health(whatsapp=False, calls=False),
        _health(whatsapp=True, calls=True),
        _health(whatsapp=None, calls=None),
    )
    await _run_checks(watch, 4)
    assert fake.sent == []


@pytest.mark.asyncio
async def test_call_service_disconnecting_is_notified(tmp_path: Path) -> None:
    """The call service going from connected to not connected is notified."""
    watch, fake, _ = _watch(tmp_path, _health(), _health(calls=False))
    await _run_checks(watch, 2)
    assert fake.sent == [("Calls unavailable", "The call service is not connected.")]


@pytest.mark.asyncio
async def test_several_changes_at_once_are_all_notified(tmp_path: Path) -> None:
    """Everything that got worse in one check is notified."""
    watch, fake, _ = _watch(tmp_path, _health(), _health(running=False, whatsapp=False, calls=False))
    await _run_checks(watch, 2)
    assert [title for title, _ in fake.sent] == ["Reachy stopped", "WhatsApp unlinked", "Calls unavailable"]


@pytest.mark.asyncio
async def test_privacy_left_on_is_reminded_once_per_time_it_was_turned_on(tmp_path: Path) -> None:
    """Long privacy earns one reminder; turning it on again later earns another."""
    first, second = "2026-10-30T08:00:00+00:00", "2026-10-30T20:00:00+00:00"
    watch, fake, _ = _watch(
        tmp_path,
        _health(),
        _health(privacy=True, since=first, on_for_s=PRIVACY_REMINDER_S - 1),
        _health(privacy=True, since=first, on_for_s=PRIVACY_REMINDER_S),
        _health(privacy=True, since=first, on_for_s=PRIVACY_REMINDER_S + 600),
        _health(),
        _health(privacy=True, since=second, on_for_s=PRIVACY_REMINDER_S + 1),
    )
    await _run_checks(watch, 3)
    assert fake.sent == [("Privacy is still on", "Reachy's mic and camera have been off for hours.")]
    await _run_checks(watch, 2)
    assert len(fake.sent) == 1
    await watch.check()
    assert [title for title, _ in fake.sent] == ["Privacy is still on", "Privacy is still on"]


@pytest.mark.asyncio
async def test_privacy_without_duration_or_since_is_not_reminded(tmp_path: Path) -> None:
    """No start time or no duration: no reminder."""
    watch, fake, _ = _watch(
        tmp_path,
        _health(),
        _health(privacy=True, since=None, on_for_s=PRIVACY_REMINDER_S * 2),
        _health(privacy=True, since="2026-10-30T08:00:00+00:00"),
    )
    await _run_checks(watch, 3)
    assert fake.sent == []


@pytest.mark.asyncio
async def test_new_missed_call_is_notified_once(tmp_path: Path) -> None:
    """A missed incoming call logged after the first check is notified, once."""
    watch, fake, activity = _watch(tmp_path, _health(), _health(), _health())
    activity.append({"at": "2026-10-30T10:00:00+00:00", "kind": "call_in", "who": "Old", "outcome": "missed"})
    await watch.check()
    activity.append({"at": "2026-10-30T10:05:00+00:00", "kind": "call_in", "who": "Abuela", "outcome": "missed"})
    await _run_checks(watch, 2)
    assert fake.sent == [("Missed call", "Abuela called Reachy.")]


@pytest.mark.asyncio
async def test_only_missed_incoming_calls_are_notified_oldest_first(tmp_path: Path) -> None:
    """Answered calls, outgoing calls and photos are not notified; several missed calls come in order."""
    watch, fake, activity = _watch(tmp_path, _health(), _health())
    await watch.check()  # empty log: everything after it is new
    for minute, kind, who, outcome in [
        (1, "call_in", "Ana", "missed"),
        (2, "call_in", "Ben", "answered"),
        (3, "call_out", "Carla", "missed"),
        (4, "photo", "Primos", "sent"),
        (5, "call_in", "", "missed"),
    ]:
        activity.append({"at": f"2026-10-30T10:0{minute}:00+00:00", "kind": kind, "who": who, "outcome": outcome})
    await watch.check()
    assert fake.sent == [("Missed call", "Ana called Reachy."), ("Missed call", "Someone called Reachy.")]


@pytest.mark.asyncio
async def test_each_check_also_runs_the_reminder(tmp_path: Path) -> None:
    """The wake-word reminder rides on the watch's minute, after the health notifications."""
    fake = FakeNotifier()
    runs: list[int] = []

    async def reminder() -> None:
        runs.append(len(fake.sent))

    watch = Watch(
        fake, _sequence(_health(), _health(running=False)), JsonLines(tmp_path / "a.jsonl"), reminder=reminder
    )
    await _run_checks(watch, 2)
    assert runs == [0, 1]


@pytest.mark.asyncio
async def test_watch_run_outlives_a_failing_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A check that raises is logged and the next one still runs."""
    monkeypatch.setattr(notifier_module, "WATCH_INTERVAL_S", 0.0)
    calls: list[int] = []
    fake = FakeNotifier()
    healthy = [_health(), _health(running=False)]

    async def health() -> dict[str, Any]:
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("control API hiccup")
        return healthy.pop(0) if healthy else _health(running=False)

    task = asyncio.create_task(Watch(fake, health, JsonLines(tmp_path / "a.jsonl")).run())
    for _ in range(200):
        if len(calls) >= 4:
            break
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(calls) >= 4
    assert fake.sent == [("Reachy stopped", "The conversation app is not running.")]
