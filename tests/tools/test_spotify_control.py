import json
import time
import asyncio
import logging
from unittest.mock import Mock

import httpx
import pytest

from reachy_mini_conversation_app.tools import spotify_control
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.spotify_control import SpotifyControl


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """Prepare private test credentials."""
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "client_id": "client",
                "expires_at": time.time() + 3600,
            }
        )
    )
    monkeypatch.setenv("SPOTIFY_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "mac")
    return path


@pytest.fixture(autouse=True)
def no_spotify_waits(monkeypatch):
    """Skip the device-activation and playback-check pauses."""
    monkeypatch.setattr(spotify_control, "SPOTIFY_TRANSFER_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(spotify_control, "SPOTIFY_PLAYBACK_CHECK_SECONDS", 0.0)


def playing_on(device_id: str) -> httpx.Response:
    """Return a current-playback response for one device."""
    return httpx.Response(200, json={"is_playing": True, "device": {"id": device_id}, "item": {"name": "x"}})


def install_transport(monkeypatch, handler):
    """Install transport."""
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action,method,suffix", [("pause", "PUT", "pause"), ("next", "POST", "next")])
async def test_controls_only_selected_device(configured, monkeypatch, action, method, suffix):
    """Test controls only selected device."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("devices"):
            return httpx.Response(
                200, json={"devices": [{"id": "tv", "is_active": True}, {"id": "mac", "is_restricted": False}]}
            )
        assert request.method == method
        assert request.url.path.endswith(suffix)
        assert request.url.params["device_id"] == "mac"
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    result = await SpotifyControl()(Mock(spec=ToolDependencies), action=action)
    assert "status" in result
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_status_does_not_expose_other_device_track(configured, monkeypatch):
    """Test status does not expose other device track."""

    def handler(request):
        if request.url.path.endswith("devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False}]})
        return httpx.Response(200, json={"device": {"id": "tv"}, "item": {"name": "private other track"}})

    install_transport(monkeypatch, handler)
    result = await SpotifyControl()(Mock(spec=ToolDependencies), action="status")
    assert "private" not in str(result)
    assert "not on" in result["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403, 404, 429, 500])
async def test_failed_mutation_is_not_retried(configured, monkeypatch, caplog, code):
    """Test failed mutation is not retried."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False}]})
        return httpx.Response(code, json={"error": "secret-response"})

    install_transport(monkeypatch, handler)
    result = await SpotifyControl()(Mock(spec=ToolDependencies), action="next")
    assert "error" in result
    assert len(calls) == 2
    assert "secret" not in caplog.text + str(result)


@pytest.mark.asyncio
async def test_refresh_saved_privately_before_playback(configured, monkeypatch):
    """Test refresh saved privately before playback."""
    credentials = json.loads(configured.read_text())
    credentials["expires_at"] = 0
    configured.write_text(json.dumps(credentials))

    def handler(request):
        if request.url.path.endswith("token"):
            return httpx.Response(200, json={"access_token": "renewed", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer renewed"
        return httpx.Response(200, json={"devices": []})

    install_transport(monkeypatch, handler)
    result = await SpotifyControl()(Mock(spec=ToolDependencies), action="resume")
    assert "error" in result
    saved = json.loads(configured.read_text())
    assert saved["access_token"] == "renewed"
    assert saved["refresh_token"] == "secret-refresh"
    assert configured.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_timeout_and_cancellation(configured, monkeypatch):
    """Test timeout and cancellation."""

    async def handler(request):
        raise httpx.ReadTimeout("secret", request=request)

    install_transport(monkeypatch, handler)
    assert "error" in await SpotifyControl()(Mock(spec=ToolDependencies), action="status")


@pytest.mark.asyncio
async def test_cancel_inflight_does_not_send_playback(configured, monkeypatch):
    """Test cancel inflight does not send playback."""
    entered = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await asyncio.Event().wait()

    install_transport(monkeypatch, handler)
    task = asyncio.create_task(SpotifyControl()(Mock(spec=ToolDependencies), action="next"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unconfigured_and_invalid_action_make_no_requests(monkeypatch):
    """Test unconfigured and invalid action make no requests."""
    monkeypatch.delenv("SPOTIFY_CREDENTIALS_FILE", raising=False)

    def handler(request):
        pytest.fail("Unexpected request")

    install_transport(monkeypatch, handler)
    tool = SpotifyControl()
    assert "error" in await tool(Mock(spec=ToolDependencies), action="status")
    assert "error" in await tool(Mock(spec=ToolDependencies), action="transfer")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,action",
    [
        ("track", "play"),
        ("album", "play"),
        ("artist", "play"),
        ("episode", "play"),
        ("playlist", "play"),
        ("track", "queue"),
        ("episode", "queue"),
    ],
)
async def test_search_then_select(configured, monkeypatch, kind, action):
    """Use actual search selections with explicit device targeting."""
    uri = "spotify:" + kind + ":" + "a" * 22
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/search"):
            assert request.url.params["type"] == kind
            assert "market" not in request.url.params  # needs a profile scope this app does not request
            return httpx.Response(200, json={kind + "s": {"items": [None, {"uri": uri, "name": "Selection"}]}})
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": True}]})
        if request.url.path.endswith("/me/player"):
            return playing_on("mac")
        assert request.url.params["device_id"] == "mac"
        if action == "queue":
            assert request.url.params["uri"] == uri
            assert request.url.path.endswith("/queue")
        else:
            body = json.loads(request.content)
            assert body == ({"uris": [uri]} if kind in {"track", "episode"} else {"context_uri": uri})
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    tool = SpotifyControl()
    found = await tool(Mock(spec=ToolDependencies), action="search", query="sample", search_type=kind)
    assert found["results"][0]["uri"] == uri
    assert "status" in await tool(Mock(spec=ToolDependencies), action=action, uri=uri)
    assert len(calls) == (4 if action == "play" else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,uri",
    [("play", "https://example.com"), ("play", "spotify:track:" + "a" * 22), ("queue", "spotify:album:" + "a" * 22)],
)
async def test_unverified_selections_never_network(configured, monkeypatch, action, uri):
    """Reject invented identifiers and unsupported queue contexts."""

    def handler(request):
        pytest.fail("Unexpected network call")

    install_transport(monkeypatch, handler)
    assert "error" in await SpotifyControl()(Mock(spec=ToolDependencies), action=action, uri=uri)


@pytest.mark.asyncio
async def test_search_query_is_not_logged(configured, monkeypatch, caplog):
    """Keep search queries out of HTTP info logs."""
    caplog.set_level(logging.INFO, logger="httpx")

    def handler(request):
        return httpx.Response(200, json={"tracks": {"items": []}})

    install_transport(monkeypatch, handler)
    assert (await SpotifyControl()(Mock(spec=ToolDependencies), action="search", query="private-search"))[
        "results"
    ] == []
    assert "private-search" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["play", "resume"])
async def test_idle_device_is_activated_before_playback(configured, monkeypatch, action):
    """An idle desktop app accepts play with HTTP 204 but stays silent unless it is the active device first."""
    uri = "spotify:track:" + "a" * 22
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"tracks": {"items": [{"uri": uri, "name": "Selection"}]}})
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": False}]})
        if request.method == "PUT" and request.url.path == "/v1/me/player":
            assert json.loads(request.content) == {"device_ids": ["mac"], "play": False}
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/v1/me/player":
            return playing_on("mac")
        assert request.url.params["device_id"] == "mac"
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    tool = SpotifyControl()
    await tool(Mock(spec=ToolDependencies), action="search", query="sample", search_type="track")
    calls.clear()
    result = await tool(Mock(spec=ToolDependencies), action=action, uri=uri)
    assert result == {"status": "Spotify is playing on the configured device."}
    assert calls == [
        ("GET", "/v1/me/player/devices"),
        ("PUT", "/v1/me/player"),
        ("PUT", "/v1/me/player/play"),
        ("GET", "/v1/me/player"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "playback", [None, {"is_playing": False, "device": {"id": "mac"}}, {"is_playing": True, "device": {"id": "tv"}}]
)
async def test_accepted_play_that_does_not_start_is_reported(configured, monkeypatch, caplog, playback):
    """Unconfirmed playback is reported as such: neither claimed as playing nor as a failure."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": True}]})
        if request.method == "GET":
            return httpx.Response(204) if playback is None else httpx.Response(200, json=playback)
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    with caplog.at_level(logging.INFO):
        result = await SpotifyControl()(Mock(spec=ToolDependencies), action="resume")
    assert "not confirmed playback" in result["status"]
    assert "error" not in result
    assert len(calls) == 2 + spotify_control.SPOTIFY_PLAYBACK_CHECK_ATTEMPTS
    assert "Spotify play not confirmed: playback_reported=" in caplog.text


@pytest.mark.asyncio
async def test_slow_starting_device_is_confirmed_once_it_plays(configured, monkeypatch):
    """An idle TV can take seconds to start; keep checking instead of reporting a false failure."""
    checks = []

    def handler(request):
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": True}]})
        if request.method == "GET":
            checks.append(request)
            if len(checks) < 3:
                return httpx.Response(200, json={"is_playing": False, "device": {"id": "mac"}, "item": None})
            return playing_on("mac")
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    result = await SpotifyControl()(Mock(spec=ToolDependencies), action="resume")
    assert result == {"status": "Spotify is playing on the configured device."}
    assert len(checks) == 3
