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
    """Skip the device-activation pause."""
    monkeypatch.setattr(spotify_control, "SPOTIFY_TRANSFER_SETTLE_SECONDS", 0.0)


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
    assert len(calls) == 3


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
        assert request.url.params["device_id"] == "mac"
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    tool = SpotifyControl()
    await tool(Mock(spec=ToolDependencies), action="search", query="sample", search_type="track")
    calls.clear()
    result = await tool(Mock(spec=ToolDependencies), action=action, uri=uri)
    assert "status" in result
    assert calls == [
        ("GET", "/v1/me/player/devices"),
        ("PUT", "/v1/me/player"),
        ("PUT", "/v1/me/player/play"),
    ]


@pytest.mark.asyncio
async def test_track_plays_inside_its_album(configured, monkeypatch):
    """A searched track starts inside its album; the Mac desktop app ignores bare track plays."""
    track = "spotify:track:" + "t" * 22
    album = "spotify:album:" + "b" * 22
    bodies = []

    def handler(request):
        if request.url.path.endswith("/search"):
            item = {"uri": track, "name": "Song", "album": {"uri": album}}
            return httpx.Response(200, json={"tracks": {"items": [item]}})
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": True}]})
        bodies.append(json.loads(request.content))
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    tool = SpotifyControl()
    await tool(Mock(spec=ToolDependencies), action="search", query="song", search_type="track")
    result = await tool(Mock(spec=ToolDependencies), action="play", uri=track)

    assert "status" in result
    assert bodies == [{"context_uri": album, "offset": {"uri": track}}]


@pytest.mark.asyncio
async def test_volume_action_sets_the_configured_device_volume(configured, monkeypatch):
    """The volume action targets the configured device and validates the level."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": True}]})
        assert request.url.path.endswith("/me/player/volume")
        assert request.url.params["device_id"] == "mac"
        assert request.url.params["volume_percent"] == "35"
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    tool = SpotifyControl()
    assert "error" in await tool(Mock(spec=ToolDependencies), action="volume", volume_percent=150)
    result = await tool(Mock(spec=ToolDependencies), action="volume", volume_percent=35)

    assert result == {"status": "Spotify volume set to 35%."}
    assert [call.method for call in calls] == ["GET", "PUT"]


def _player_handler(state, puts):
    """Serve player state for the ducker and record volume changes."""

    def handler(request):
        path = request.url.path
        if path.endswith("/me/player/devices"):
            return httpx.Response(
                200, json={"devices": [{"id": "mac", "volume_percent": state["volume"], "supports_volume": True}]}
            )
        if path.endswith("/me/player/volume"):
            state["volume"] = int(request.url.params["volume_percent"])
            puts.append(state["volume"])
            return httpx.Response(204)
        if path.endswith("/me/player"):
            return httpx.Response(200, json={"is_playing": state["playing"], "device": {"id": "mac"}})
        raise AssertionError(path)

    return handler


@pytest.mark.asyncio
async def test_ducker_lowers_playing_music_and_restores_it(configured, monkeypatch):
    """Music playing at the wake drops to the duck level and returns to its level afterwards."""
    state = {"volume": 60, "playing": True}
    puts: list[int] = []
    install_transport(monkeypatch, _player_handler(state, puts))
    ducker = spotify_control.SpotifyDucker(duck_percent=20)

    await ducker.session_started()
    assert state["volume"] == 20
    await ducker.session_ended()

    assert puts == [20, 60]


@pytest.mark.asyncio
async def test_ducker_keeps_a_volume_the_user_changed(configured, monkeypatch):
    """If the volume moved during the conversation, it is not overwritten on restore."""
    state = {"volume": 60, "playing": True}
    puts: list[int] = []
    install_transport(monkeypatch, _player_handler(state, puts))
    ducker = spotify_control.SpotifyDucker(duck_percent=20)

    await ducker.session_started()
    state["volume"] = 45
    await ducker.session_ended()

    assert puts == [20]


@pytest.mark.asyncio
async def test_ducker_leaves_paused_music_alone_but_lowers_music_started_mid_conversation(configured, monkeypatch):
    """Nothing changes for paused music; a track started during the conversation is lowered then restored."""
    state = {"volume": 70, "playing": False}
    puts: list[int] = []
    install_transport(monkeypatch, _player_handler(state, puts))
    ducker = spotify_control.SpotifyDucker(duck_percent=20)

    await ducker.session_started()
    assert puts == []
    ducker.playback_started()
    await asyncio.gather(*ducker._tasks)
    await ducker.session_ended()

    assert puts == [20, 70]


@pytest.mark.asyncio
async def test_ducker_does_nothing_without_spotify_configuration(monkeypatch):
    """Without credentials or a device, no request is made."""
    monkeypatch.delenv("SPOTIFY_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("SPOTIFY_DEVICE_ID", raising=False)
    install_transport(monkeypatch, lambda request: (_ for _ in ()).throw(AssertionError("no request")))
    ducker = spotify_control.SpotifyDucker()

    await ducker.session_started()
    await ducker.session_ended()


@pytest.mark.asyncio
async def test_play_during_a_conversation_asks_for_a_short_reply_and_winds_down(configured, monkeypatch):
    """A track started mid-conversation tells the model to just say 'Enjoy!' and requests the wind-down."""
    uri = "spotify:track:" + "w" * 22

    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"tracks": {"items": [{"uri": uri, "name": "Song"}]}})
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "mac", "is_restricted": False, "is_active": True}]})
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    wind_downs: list[bool] = []
    ducker = spotify_control.SPOTIFY_DUCKER
    monkeypatch.setattr(ducker, "_session_active", True)
    monkeypatch.setattr(ducker, "music_started_callback", lambda: wind_downs.append(True))
    monkeypatch.setattr(ducker, "_spawn", lambda coroutine: coroutine.close())
    tool = SpotifyControl()

    await tool(Mock(spec=ToolDependencies), action="search", query="song", search_type="track")
    result = await tool(Mock(spec=ToolDependencies), action="play", uri=uri)

    assert result == {"status": spotify_control.MUSIC_STARTED_IN_CONVERSATION}
    assert wind_downs == [True]
