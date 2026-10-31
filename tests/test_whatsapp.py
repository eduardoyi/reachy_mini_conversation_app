import json
import base64
import asyncio
import logging
from typing import Any

import httpx
import pytest

from reachy_mini_conversation_app import whatsapp
from reachy_mini_conversation_app.whatsapp import SendResult, WhatsAppLink


URL = "http://127.0.0.1:8792"
AUTH = "reachy:server-password-123"
GROUP = "120363416355796813@g.us"
JPEG = b"\xff\xd8photo-bytes\xff\xd9"


def _link(
    answers: list[httpx.Response | Exception], sent: list[httpx.Request], deadline_s: float = 15.0
) -> WhatsAppLink:
    """Return a link whose server answers with ``answers`` in turn (an exception is raised instead)."""

    def answer(request: httpx.Request) -> httpx.Response:
        request.read()
        sent.append(request)
        reply = answers.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    return WhatsAppLink(URL, AUTH, client=client, retry_pause_s=0.0, deadline_s=deadline_s)


def _ok(**results: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": "SUCCESS", "message": "Success", "results": results})


@pytest.mark.asyncio
async def test_a_photo_is_posted_as_a_jpeg_to_the_group_with_basic_auth() -> None:
    """The photo goes as an HD JPEG part, to the group, with GOWA's basic auth."""
    sent: list[httpx.Request] = []
    link = _link([_ok(message_id="3EB0")], sent)

    assert await link.send_image(GROUP, JPEG) is SendResult.SENT

    (request,) = sent
    assert request.method == "POST"
    assert str(request.url) == f"{URL}/send/image"
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(AUTH.encode()).decode()
    body = request.content
    assert f'name="phone"\r\n\r\n{GROUP}'.encode() in body
    assert b'name="hd"\r\n\r\ntrue' in body
    assert b'name="image"; filename="photo.jpg"\r\nContent-Type: image/jpeg\r\n\r\n' + JPEG in body


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(400, json={"code": "BAD_REQUEST", "message": "your image is not allowed"}),
        httpx.Response(200, json={"code": "INTERNAL_SERVER_ERROR", "message": "not logged in"}),
        httpx.Response(500, text="oops"),
    ],
)
@pytest.mark.asyncio
async def test_a_refused_photo_is_not_sent_and_not_retried(reply: httpx.Response) -> None:
    """Any refusal from GOWA is a failure, tried only once."""
    sent: list[httpx.Request] = []
    assert await _link([reply], sent).send_image(GROUP, JPEG) is SendResult.FAILED
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_photo_is_retried_once_when_the_server_cannot_be_reached() -> None:
    """A request that never left is tried once more."""
    sent: list[httpx.Request] = []
    assert await _link([httpx.ConnectError("refused"), _ok()], sent).send_image(GROUP, JPEG) is SendResult.SENT
    assert len(sent) == 2

    sent.clear()
    assert await _link([httpx.ConnectTimeout("syn"), _ok()], sent).send_image(GROUP, JPEG) is SendResult.SENT
    assert len(sent) == 2

    sent.clear()
    unreachable = [httpx.ConnectError("refused"), httpx.ConnectTimeout("syn")]
    assert await _link(unreachable, sent).send_image(GROUP, JPEG) is SendResult.FAILED
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_a_photo_that_timed_out_is_unsure_and_never_sent_again() -> None:
    """A send that may have left is never repeated."""
    sent: list[httpx.Request] = []
    assert await _link([httpx.ReadTimeout("slow"), _ok()], sent).send_image(GROUP, JPEG) is SendResult.UNSURE
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_send_past_its_deadline_is_unsure() -> None:
    """The whole send is bounded; past the deadline it may still arrive."""

    async def stalled(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return _ok()

    client = httpx.AsyncClient(transport=httpx.MockTransport(stalled))
    link = WhatsAppLink(URL, AUTH, client=client, deadline_s=0.05)
    assert await link.send_image(GROUP, JPEG) is SendResult.UNSURE


@pytest.mark.asyncio
async def test_logs_hold_neither_the_group_nor_the_password(caplog: pytest.LogCaptureFixture) -> None:
    """Logs keep outcomes, never the chat id or the credentials."""
    caplog.set_level(logging.DEBUG)
    sent: list[httpx.Request] = []
    link = _link([_ok(), httpx.Response(400, json={"code": "BAD_REQUEST"}), httpx.ReadTimeout("slow")], sent)
    for _ in range(3):
        await link.send_image(GROUP, JPEG)

    assert "WhatsApp photo sent" in caplog.text
    assert GROUP not in caplog.text
    assert "server-password-123" not in caplog.text


@pytest.mark.asyncio
async def test_status_reports_the_server_or_none_when_it_does_not_answer() -> None:
    """Status says whether GOWA is logged in, or None when it does not answer."""
    sent: list[httpx.Request] = []
    reply = _ok(is_connected=True, is_logged_in=True, device_id="reachy", jid="351900000000@s.whatsapp.net")
    status = await _link([reply], sent).status()
    assert status is not None and status.connected and status.logged_in and status.number == "351900000000"
    assert str(sent[0].url) == f"{URL}/app/status"

    unpaired = httpx.Response(404, json={"code": "DEVICE_NOT_FOUND", "message": "create a device first"})
    status = await _link([unpaired], []).status()
    assert status is not None and not status.logged_in and "DEVICE_NOT_FOUND" in status.message

    assert await _link([httpx.ConnectError("refused")], []).status() is None


@pytest.mark.asyncio
async def test_groups_lists_names_and_ids() -> None:
    """Groups come back as names with their ids."""
    reply = _ok(data=[{"JID": GROUP, "Name": "Reachy photos"}, {"JID": "1@g.us", "Name": "Family"}])
    assert await _link([reply], []).groups() == [("Reachy photos", GROUP), ("Family", "1@g.us")]


@pytest.mark.asyncio
async def test_create_group_sends_the_title_and_numbers_and_returns_its_id() -> None:
    """A new group is posted with its title and members; its id comes back, or None when refused."""
    sent: list[httpx.Request] = []
    group = await _link([_ok(group_id=GROUP)], sent).create_group("Reachy & Sergio", ["447450000000"])
    assert group == GROUP
    assert (sent[0].method, sent[0].url.path) == ("POST", "/group")
    assert json.loads(sent[0].content) == {"title": "Reachy & Sergio", "participants": ["447450000000"]}
    refused = httpx.Response(400, json={"code": "VALIDATION_ERROR", "message": "participants: cannot be blank"})
    assert await _link([refused], []).create_group("Reachy", ["1"]) is None


@pytest.mark.asyncio
async def test_pairing_creates_the_device_slot_on_a_fresh_server() -> None:
    """A fresh server gets its device slot before the pairing code is asked for."""
    sent: list[httpx.Request] = []
    answers: list[httpx.Response | Exception] = [
        httpx.Response(200, json={"code": "SUCCESS", "results": []}),
        _ok(device_id="reachy"),
        _ok(device_id="reachy", pair_code="ABCD-1234"),
    ]
    assert await _link(answers, sent).pairing_code("351912345678") == "ABCD-1234"
    assert [(request.method, request.url.path) for request in sent] == [
        ("GET", "/devices"),
        ("POST", "/devices"),
        ("GET", "/app/login-with-code"),
    ]
    assert sent[2].url.params["phone"] == "351912345678"


def test_groups_command_prints_each_group(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The groups command prints one name and id per line."""
    from reachy_mini_conversation_app.config import config

    monkeypatch.setattr(config, "WHATSAPP_API_URL", URL)
    monkeypatch.setattr(config, "WHATSAPP_API_AUTH", AUTH)

    async def groups(self: WhatsAppLink) -> list[tuple[str, str]]:
        return [("Reachy photos", GROUP)]

    monkeypatch.setattr(WhatsAppLink, "groups", groups)
    assert whatsapp.main(["groups"]) == 0
    assert capsys.readouterr().out == f"Reachy photos\t{GROUP}\n"


def test_commands_need_the_server_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the server settings the commands stop with a usage error."""
    from reachy_mini_conversation_app.config import config

    monkeypatch.setattr(config, "WHATSAPP_API_URL", None)
    assert whatsapp.main(["status"]) == 2


@pytest.mark.asyncio
async def test_a_voice_note_is_posted_as_ogg_with_ptt() -> None:
    """A message goes to /send/audio as a voice note (ptt), the OGG file as is."""
    sent: list[httpx.Request] = []
    ogg = b"OggS-voice"
    assert await _link([_ok(message_id="3EB1")], sent).send_audio(GROUP, ogg) is SendResult.SENT

    (request,) = sent
    assert request.url.path == "/send/audio"
    body = request.content
    assert f'name="phone"\r\n\r\n{GROUP}'.encode() in body
    assert b'name="ptt"\r\n\r\ntrue' in body
    assert b'name="audio"; filename="message.ogg"\r\nContent-Type: audio/ogg\r\n\r\n' + ogg in body


@pytest.mark.asyncio
async def test_a_voice_note_follows_the_same_retry_rules() -> None:
    """Retried once when it never left; never repeated after a timeout."""
    sent: list[httpx.Request] = []
    assert await _link([httpx.ConnectError("refused"), _ok()], sent).send_audio(GROUP, b"OggS") is SendResult.SENT
    assert len(sent) == 2
    sent.clear()
    assert await _link([httpx.ReadTimeout("slow")], sent).send_audio(GROUP, b"OggS") is SendResult.UNSURE
    assert len(sent) == 1
