import json
import asyncio
import logging
from typing import Any
from datetime import datetime

import httpx
import pytest

from reachy_mini_conversation_app import maribel
from reachy_mini_conversation_app.maribel import (
    REPLY_PATH,
    LATE_REPLY_S,
    QUESTION_OPEN_S,
    MaribelLink,
    MaribelReply,
    serve_replies,
)


WEBHOOK = "https://maribel.example/webhook"
CALLBACK = "https://reachy.example/maribel/reply"
KEY = "webhook-key-123"
SECRET = "callback-secret-456"
SIGNED = f"Bearer {SECRET}"


class Clock:
    """Controllable monotonic clock."""

    def __init__(self) -> None:
        """Start at an arbitrary time."""
        self.now = 100.0

    def __call__(self) -> float:
        """Return the current time."""
        return self.now


def _link(statuses: list[int | Exception], sent: list[httpx.Request], clock: Clock | None = None) -> MaribelLink:
    """Return a link whose webhook answers with ``statuses`` in turn (an exception is a network error)."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        status = statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json={"success": status < 300})

    return MaribelLink(
        WEBHOOK,
        KEY,
        CALLBACK,
        SECRET,
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        clock=clock or Clock(),
        retry_pause_s=0.0,
    )


def _callback(request: httpx.Request, reply_type: str = "answer", **extra: Any) -> bytes:
    body = json.loads(request.content)
    return json.dumps(
        {
            "version": 1,
            "request_id": body["request_id"],
            "conversation_id": body["conversation_id"],
            "type": reply_type,
            "speak": "It's Wednesday.",
            **extra,
        }
    ).encode()


@pytest.mark.asyncio
async def test_a_request_is_posted_with_the_key_and_the_agreed_body() -> None:
    """The webhook gets the key as a Bearer header and the agreed body."""
    sent: list[httpx.Request] = []
    link = _link([200], sent)

    request_id = await link.send("ask Maribel what day it is")

    assert request_id is not None
    (request,) = sent
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["version"] == 1 and body["request_id"] == request_id
    assert body["text"] == "ask Maribel what day it is"
    assert body["in_reply_to"] is None and body["callback_url"] == CALLBACK
    assert datetime.fromisoformat(body["sent_at"]).utcoffset() is not None


@pytest.mark.asyncio
async def test_a_failed_post_is_retried_once_then_given_up() -> None:
    """A non-2xx or network error is retried once, then the request counts as unreachable."""
    sent: list[httpx.Request] = []
    assert await _link([503, 202], sent).send("hi") is not None
    assert len(sent) == 2

    sent.clear()
    assert await _link([httpx.ConnectError("down"), 500], sent).send("hi") is None
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_the_first_callback_reaches_the_waiter() -> None:
    """A callback from the server thread wakes the request waiting for it."""
    sent: list[httpx.Request] = []
    link = _link([200], sent)
    request_id = await link.send("what day is it?")
    assert request_id is not None

    waiting = asyncio.create_task(link.first_reply(request_id, timeout_s=2.0))
    await asyncio.sleep(0)
    assert await asyncio.to_thread(link.receive, SIGNED, _callback(sent[0])) == 200
    reply = await waiting

    assert reply == MaribelReply(
        request_id, json.loads(sent[0].content)["conversation_id"], "answer", "It's Wednesday."
    )


@pytest.mark.asyncio
async def test_strangers_and_bad_bodies_are_turned_away(caplog: pytest.LogCaptureFixture) -> None:
    """Wrong secret 401, bad body 400, unknown request 404; no secret or text is logged."""
    sent: list[httpx.Request] = []
    link = _link([200], sent)
    await link.send("secret plans")
    good = _callback(sent[0])
    unknown = json.dumps({**json.loads(good), "request_id": "someone-else"}).encode()

    with caplog.at_level(logging.DEBUG):
        assert link.receive(None, good) == 401
        assert link.receive("Bearer wrong", good) == 401
        assert link.receive(SIGNED, b"not json") == 400
        assert link.receive(SIGNED, _callback(sent[0], "shout")) == 400
        assert link.receive(SIGNED, _callback(sent[0], "question")) == 400  # a question needs its id
        assert link.receive(SIGNED, unknown) == 404
        assert link.receive(SIGNED, good) == 200

    logged = caplog.text
    assert SECRET not in logged and KEY not in logged
    assert "secret plans" not in logged and "Wednesday" not in logged


@pytest.mark.asyncio
async def test_a_reply_after_the_wait_is_late_and_only_the_latest_request_is_spoken() -> None:
    """Late replies are spoken only for the latest request, once each."""
    sent: list[httpx.Request] = []
    clock = Clock()
    link = _link([200, 200], sent, clock)
    late: list[MaribelReply] = []
    link.on_late_reply = late.append

    first = await link.send("first")
    second = await link.send("second")
    assert first is not None and second is not None
    assert await link.first_reply(second, timeout_s=0.01) is None

    assert link.receive(SIGNED, _callback(sent[0])) == 200  # superseded by the second request
    clock.now += 30
    assert link.receive(SIGNED, _callback(sent[1])) == 200
    assert [reply.request_id for reply in late] == [second]
    assert link.receive(SIGNED, _callback(sent[1])) == 200  # one reply per request
    assert len(late) == 1


@pytest.mark.asyncio
async def test_a_reply_too_late_is_dropped() -> None:
    """A reply after LATE_REPLY_S is not spoken."""
    sent: list[httpx.Request] = []
    clock = Clock()
    link = _link([200], sent, clock)
    late: list[MaribelReply] = []
    link.on_late_reply = late.append
    request_id = await link.send("hi")
    assert request_id is not None
    assert await link.first_reply(request_id, timeout_s=0.01) is None

    clock.now += LATE_REPLY_S + 1
    assert link.receive(SIGNED, _callback(sent[0])) == 200
    assert late == []


@pytest.mark.asyncio
async def test_an_answer_to_her_question_continues_her_conversation_for_two_minutes() -> None:
    """After her question, the next request answers it in the same conversation."""
    sent: list[httpx.Request] = []
    clock = Clock()
    link = _link([200, 200, 200], sent, clock)

    await link.send("book dinner on Friday")
    assert link.receive(SIGNED, _callback(sent[0], "question", question_id="q-1")) == 200
    assert link.has_open_question()
    await link.send("four of us")
    first, answer = (json.loads(request.content) for request in sent[:2])
    assert answer["conversation_id"] == first["conversation_id"] and answer["in_reply_to"] == "q-1"
    assert answer["request_id"] != first["request_id"]

    assert link.receive(SIGNED, _callback(sent[1])) == 200  # her answer closes the conversation
    assert not link.has_open_question()
    await link.send("something new")
    fresh = json.loads(sent[2].content)
    assert fresh["conversation_id"] != first["conversation_id"] and fresh["in_reply_to"] is None


@pytest.mark.asyncio
async def test_an_unanswered_question_expires() -> None:
    """After QUESTION_OPEN_S a request starts a new conversation."""
    sent: list[httpx.Request] = []
    clock = Clock()
    link = _link([200, 200], sent, clock)
    await link.send("book dinner")
    link.receive(SIGNED, _callback(sent[0], "question", question_id="q-1"))

    clock.now += QUESTION_OPEN_S + 1
    assert not link.has_open_question()
    await link.send("four of us")
    assert json.loads(sent[1].content)["in_reply_to"] is None


@pytest.mark.asyncio
async def test_the_callback_server_listens_on_loopback_and_answers_at_once() -> None:
    """Only POST /maribel/reply on 127.0.0.1, small bodies, signed callbacks."""
    sent: list[httpx.Request] = []
    link = _link([200], sent)
    request_id = await link.send("hi")
    assert request_id is not None
    server = serve_replies(link, 0)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}"
    try:
        assert host == "127.0.0.1"
        async with httpx.AsyncClient() as client:
            assert (await client.post(f"{url}/other", content=b"{}")).status_code == 404
            assert (await client.get(f"{url}{REPLY_PATH}")).status_code == 404
            too_big = b"x" * (maribel.MAX_BODY_BYTES + 1)
            assert (await client.post(f"{url}{REPLY_PATH}", content=too_big)).status_code == 413
            assert (await client.post(f"{url}{REPLY_PATH}", content=_callback(sent[0]))).status_code == 401
            waiting = asyncio.create_task(link.first_reply(request_id, timeout_s=2.0))
            response = await client.post(
                f"{url}{REPLY_PATH}", content=_callback(sent[0]), headers={"Authorization": SIGNED}
            )
            assert response.status_code == 200 and response.json() == {"ok": True}
            assert (await waiting) is not None
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_the_webhook_address_never_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """Httpx logs every request's URL; hers works as a credential, so that line is dropped."""
    sent: list[httpx.Request] = []
    link = _link([200], sent)
    with caplog.at_level(logging.DEBUG):
        assert await link.send("what is on my calendar") is not None
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(204))) as other:
            await other.get("https://other.example/ping")
    assert len(sent) == 1
    assert WEBHOOK not in caplog.text
    assert "https://other.example/ping" in caplog.text
