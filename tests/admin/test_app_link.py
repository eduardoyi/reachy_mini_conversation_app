"""The admin app's line to the conversation app's control API."""

from pathlib import Path
from collections.abc import Callable

import httpx
import pytest

from reachy_mini_conversation_app.admin.app_link import AppLink, AppUnavailable


URL = "http://127.0.0.1:7861/"


def _link(token_file: Path, handler: Callable[[httpx.Request], httpx.Response]) -> AppLink:
    return AppLink(URL, token_file, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    """Write the shared control token, as the conversation app does."""
    path = tmp_path / "control-token"
    path.write_text("first-token\n")
    return path


@pytest.mark.asyncio
async def test_request_sends_the_current_token_read_on_each_call(token_file: Path) -> None:
    """The bearer token comes from the file every time, so a renewed token is picked up."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    link = _link(token_file, handler)
    first = await link.request("POST", "/privacy", json={"on": True})
    token_file.write_text("renewed-token")
    await link.request("GET", "/api/faces/photo", params={"person_id": "ana", "photo": "1.jpg"})
    await link.aclose()

    assert first.status_code == 200 and first.json() == {"ok": True}
    assert [r.headers["authorization"] for r in seen] == ["Bearer first-token", "Bearer renewed-token"]
    assert str(seen[0].url) == "http://127.0.0.1:7861/privacy" and seen[0].content == b'{"on":true}'
    assert seen[1].url.params == httpx.QueryParams({"person_id": "ana", "photo": "1.jpg"})


@pytest.mark.asyncio
async def test_request_passes_other_statuses_through(token_file: Path) -> None:
    """Only a refused token is an error; a 409 or 500 is the caller's to relay."""
    link = _link(token_file, lambda request: httpx.Response(409, json={"error": "busy"}))
    response = await link.request("GET", "/camera")
    assert response.status_code == 409 and response.json() == {"error": "busy"}


@pytest.mark.asyncio
async def test_request_raises_when_unreachable(token_file: Path) -> None:
    """A transport error means the app is not running."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AppUnavailable):
        await _link(token_file, handler).request("GET", "/status")


@pytest.mark.asyncio
async def test_request_raises_when_the_token_is_refused(token_file: Path) -> None:
    """A 403 means the token is wrong or stale."""
    with pytest.raises(AppUnavailable):
        await _link(token_file, lambda request: httpx.Response(403)).request("POST", "/sleep")


@pytest.mark.asyncio
async def test_request_raises_without_a_token_file_and_sends_nothing(tmp_path: Path) -> None:
    """No token yet (the app never started): nothing is sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    with pytest.raises(AppUnavailable):
        await _link(tmp_path / "missing", handler).request("GET", "/status")
    assert seen == []


@pytest.mark.asyncio
async def test_status_returns_the_apps_status(token_file: Path) -> None:
    """A 200 JSON object is the status."""
    link = _link(token_file, lambda request: httpx.Response(200, json={"mode": "conversation"}))
    assert await link.status() == {"mode": "conversation"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, json={"mode": "x"}), httpx.Response(403), httpx.Response(200, json=["not", "an", "object"])],
)
async def test_status_is_none_when_the_app_answers_badly(token_file: Path, response: httpx.Response) -> None:
    """A refusal, an error or a non-object answer means no status."""
    assert await _link(token_file, lambda request: response).status() is None


@pytest.mark.asyncio
async def test_status_is_none_when_unreachable_or_without_token(tmp_path: Path, token_file: Path) -> None:
    """Not running, or never started, is None rather than an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    assert await _link(token_file, handler).status() is None
    assert await _link(tmp_path / "missing", lambda request: httpx.Response(200, json={})).status() is None


@pytest.mark.asyncio
async def test_open_stream_returns_the_streaming_response(token_file: Path) -> None:
    """The camera stream is handed back unread, with its content type, for the caller to relay and close."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
            stream=httpx.ByteStream(b"--frame\r\nJPEG"),
        )

    link = _link(token_file, handler)
    response = await link.open_stream("/camera")
    assert not response.is_stream_consumed
    assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert b"".join([chunk async for chunk in response.aiter_raw()]) == b"--frame\r\nJPEG"
    await response.aclose()
    assert seen[0].headers["authorization"] == "Bearer first-token"
    assert str(seen[0].url) == "http://127.0.0.1:7861/camera"


@pytest.mark.asyncio
async def test_open_stream_raises_when_refused_unreachable_or_without_token(tmp_path: Path, token_file: Path) -> None:
    """The camera is unavailable on a refused token, a transport error or no token."""

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AppUnavailable):
        await _link(token_file, lambda request: httpx.Response(403)).open_stream("/camera")
    with pytest.raises(AppUnavailable):
        await _link(token_file, unreachable).open_stream("/camera")
    with pytest.raises(AppUnavailable):
        await _link(tmp_path / "missing", lambda request: httpx.Response(200)).open_stream("/camera")


@pytest.mark.asyncio
async def test_open_stream_returns_other_statuses_for_the_caller(token_file: Path) -> None:
    """A 409 (camera busy, privacy on) is returned, not raised."""
    response = await _link(token_file, lambda request: httpx.Response(409, json={"error": "privacy"})).open_stream(
        "/camera"
    )
    assert response.status_code == 409
    await response.aread()
    assert response.json() == {"error": "privacy"}
    await response.aclose()
