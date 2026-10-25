"""Tests for showing the song Spotify plays on Reachy's speaker on the e-ink screen."""

import io
import json
import time
import asyncio
from typing import Any
from pathlib import Path

import httpx
import pytest
from PIL import Image

from reachy_mini_conversation_app import now_playing
from reachy_mini_conversation_app.now_playing import (
    PLAYING_POLL_S,
    STOPPED_HOLD_S,
    REQUESTED_HOLD_S,
    Playback,
    NowPlaying,
    parse_playback,
    spotify_fetcher,
)
from reachy_mini_conversation_app.eink_display import EInkDisplay, render_text


ART = "https://i.scdn.co/image/abc640"


def _track(item_id: str = "t1", album_id: str = "al1", *, device: str = "speaker", **extra: Any) -> dict[str, Any]:
    return {
        "device": {"id": device},
        "is_playing": True,
        "progress_ms": 60_000,
        "item": {
            "type": "track",
            "id": item_id,
            "name": f"Song {item_id}",
            "duration_ms": 200_000,
            "artists": [{"name": "Queen"}, {"name": "David Bowie"}],
            "album": {
                "id": album_id,
                "name": "Hot Space",
                "images": [
                    {"url": "https://i.scdn.co/image/abc64", "width": 64},
                    {"url": ART, "width": 640},
                    {"url": "https://i.scdn.co/image/abc300", "width": 300},
                ],
            },
        },
        **extra,
    }


def _art_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 640), (120, 40, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_song_on_reachys_speaker_is_read_with_its_art() -> None:
    """Title, every artist, the album, the 640 px cover and the time left come from one /me/player body."""
    playback = parse_playback(_track(), "speaker")

    assert playback == Playback(
        item_id="t1",
        title="Song t1",
        artist="Queen, David Bowie",
        album="Hot Space",
        art_url=ART,
        art_id="al1",
        playing=True,
        remaining_s=140.0,
    )


def test_an_episode_shows_its_show() -> None:
    """Podcast episodes show the show's name and publisher, and the episode's art."""
    body = {
        "device": {"id": "speaker"},
        "is_playing": False,
        "item": {
            "type": "episode",
            "id": "ep1",
            "name": "Volcanes",
            "images": [{"url": "https://i.scdn.co/image/ep", "width": 640}],
            "show": {"id": "sh1", "name": "Ciencia para niños", "publisher": "Radio X"},
        },
    }
    playback = parse_playback(body, "speaker")

    assert playback is not None
    assert (playback.title, playback.artist, playback.album, playback.art_id) == (
        "Volcanes",
        "Radio X",
        "Ciencia para niños",
        "sh1",
    )
    assert playback.art_url == "https://i.scdn.co/image/ep" and playback.playing is False


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_track(device="phone"), id="another-device"),
        pytest.param({"device": {"id": "speaker"}, "item": None}, id="nothing-loaded"),
        pytest.param("not json", id="garbage"),
    ],
)
def test_other_devices_and_empty_players_are_ignored(body: Any) -> None:
    """Music on a phone or another speaker never reaches Reachy's screen."""
    assert parse_playback(body, "speaker") is None


def test_art_only_comes_from_spotifys_image_server() -> None:
    """A cover URL on any other host, or over plain http, is never downloaded."""
    body = _track()
    body["item"]["album"]["images"] = [
        {"url": "https://192.168.1.1/cover.jpg", "width": 640},
        {"url": "http://i.scdn.co/image/plain", "width": 640},
    ]
    playback = parse_playback(body, "speaker")
    assert playback is not None and playback.art_url is None


class _Clock:
    def __init__(self) -> None:
        self.now = 1_792_878_594.0

    def __call__(self) -> float:
        return self.now


class _Room:
    """A fake Spotify speaker, conversation state and image server shared by one watcher."""

    def __init__(self, tmp_path: Path) -> None:
        self.clock = _Clock()
        self.playback: dict[str, Any] | None = _track()
        self.talking = False
        self.art_requests = 0
        self.display = EInkDisplay(port=8090, refresh_s=1, cache_dir=tmp_path, image_model="unused", clock=self.clock)
        self.watcher = NowPlaying(
            self.display,
            self.fetch,
            in_conversation=lambda: self.talking,
            art_dir=tmp_path / "album-art",
            transport=httpx.MockTransport(self.serve_art),
            clock=self.clock,
        )

    async def fetch(self) -> Playback | None:
        return None if self.playback is None else parse_playback(self.playback, "speaker")

    def serve_art(self, request: httpx.Request) -> httpx.Response:
        self.art_requests += 1
        return httpx.Response(200, content=_art_png())

    def play(self, item_id: str, album_id: str = "al1") -> None:
        self.playback = _track(item_id, album_id)

    def screen(self) -> tuple[str, str]:
        return self.display.owner(), self.display.current().filename


@pytest.mark.asyncio
async def test_each_new_song_gets_the_music_card_once(tmp_path: Path) -> None:
    """A song puts up the card; the same song keeps it; the next song replaces it; covers are cached."""
    room = _Room(tmp_path)

    assert await room.watcher.check() == PLAYING_POLL_S
    first = room.screen()
    assert first[0] == "music"
    room.clock.now += 10
    await room.watcher.check()
    assert room.screen() == first

    room.play("t2")  # same album
    room.clock.now += 10
    await room.watcher.check()
    assert room.screen()[0] == "music" and room.screen() != first
    assert room.art_requests == 1


@pytest.mark.asyncio
async def test_the_watcher_looks_again_right_when_the_song_ends(tmp_path: Path) -> None:
    """With 3 s left, the next look comes at the end of the song rather than 10 s later."""
    room = _Room(tmp_path)
    room.playback = _track(progress_ms=197_000)

    assert await room.watcher.check() == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_the_music_card_does_not_idle_away_during_a_long_track(tmp_path: Path) -> None:
    """A 40-minute track keeps its card past the screen's 30-minute idle return."""
    room = _Room(tmp_path)
    await room.watcher.check()
    card = room.screen()
    for _ in range(4 * 60):  # 40 minutes of checks every 10 s
        room.clock.now += 10
        await room.watcher.check()
    assert room.screen() == card


@pytest.mark.asyncio
async def test_a_requested_screen_holds_while_the_conversation_is_open(tmp_path: Path) -> None:
    """A drawing asked for mid-song stays while they talk, whatever the music does."""
    room = _Room(tmp_path)
    await room.watcher.check()
    room.talking = True
    room.display.show(render_text(["un dinosaurio"]))
    asked = room.screen()

    for song in ("t2", "t3"):
        room.play(song)
        room.clock.now += 200
        await room.watcher.check()
        assert room.screen() == asked


@pytest.mark.asyncio
async def test_after_the_conversation_the_card_returns_at_the_next_song(tmp_path: Path) -> None:
    """Once they stop talking, the next song brings the music card back."""
    room = _Room(tmp_path)
    room.talking = True
    room.display.show(render_text(["un dinosaurio"]))
    await room.watcher.check()
    room.talking = False
    room.clock.now += 10
    await room.watcher.check()  # conversation over, same song
    assert room.screen()[0] == "requested"

    room.play("t2")
    room.clock.now += 10
    await room.watcher.check()
    assert room.screen()[0] == "music"


@pytest.mark.asyncio
async def test_after_the_conversation_the_card_returns_within_two_minutes(tmp_path: Path) -> None:
    """With one long song playing, the music card returns 2 minutes after the conversation ends."""
    room = _Room(tmp_path)
    room.talking = True
    room.display.show(render_text(["un dinosaurio"]))
    await room.watcher.check()
    room.talking = False
    await room.watcher.check()

    room.clock.now += REQUESTED_HOLD_S - 1
    await room.watcher.check()
    assert room.screen()[0] == "requested"
    room.clock.now += 1
    await room.watcher.check()
    assert room.screen()[0] == "music"


@pytest.mark.asyncio
async def test_when_the_music_stops_the_card_stays_two_minutes(tmp_path: Path) -> None:
    """Paused or stopped music leaves the card up for 2 minutes, then the resting card returns."""
    room = _Room(tmp_path)
    await room.watcher.check()
    room.playback = {**_track(), "is_playing": False}

    room.clock.now += 10
    await room.watcher.check()  # the first look that sees it stopped
    room.clock.now += STOPPED_HOLD_S - 1
    await room.watcher.check()
    assert room.screen()[0] == "music"
    room.clock.now += 1
    await room.watcher.check()
    assert room.screen()[0] == "home"


@pytest.mark.asyncio
async def test_music_never_replaces_a_requested_screen_when_nothing_plays(tmp_path: Path) -> None:
    """Without music the watcher leaves requested screens and the resting card alone."""
    room = _Room(tmp_path)
    room.playback = None
    room.display.show(render_text(["hola"]))
    asked = room.screen()

    room.clock.now += 600
    await room.watcher.check()
    assert room.screen() == asked


@pytest.mark.asyncio
async def test_what_song_is_this_puts_the_card_up_at_once(tmp_path: Path) -> None:
    """show_now replaces even a requested screen and returns the song to say."""
    room = _Room(tmp_path)
    room.talking = True
    room.display.show(render_text(["hola"]))

    playback = await room.watcher.show_now()

    assert playback is not None and playback.title == "Song t1"
    assert room.screen()[0] == "music"
    room.playback = None
    assert await room.watcher.show_now() is None


@pytest.mark.asyncio
async def test_a_missing_cover_still_shows_the_card(tmp_path: Path) -> None:
    """A cover that fails to download leaves a card without art."""
    room = _Room(tmp_path)
    room.watcher._transport = httpx.MockTransport(lambda request: httpx.Response(404))

    await room.watcher.check()

    assert room.screen()[0] == "music"
    assert not (tmp_path / "album-art").exists()


@pytest.mark.asyncio
async def test_refresh_soon_makes_the_watcher_look_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After Reachy skips a song the watcher does not wait for its next scheduled look."""
    monkeypatch.setattr(now_playing, "IDLE_POLL_S", 60.0)
    room = _Room(tmp_path)
    room.playback = None
    looks: list[float] = []

    async def counting_fetch() -> Playback | None:
        looks.append(time.monotonic())
        return None

    room.watcher._fetch = counting_fetch
    task = asyncio.create_task(room.watcher.run())
    try:
        while not looks:
            await asyncio.sleep(0.01)
        room.watcher.refresh_soon()
        started = time.monotonic()
        while len(looks) < 2 and time.monotonic() - started < 2:
            await asyncio.sleep(0.01)
        assert len(looks) == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_spotify_fetcher_asks_for_episodes_with_the_saved_token(tmp_path: Path) -> None:
    """The fetch uses the shared credentials file and treats 204 as nothing playing."""
    credentials = tmp_path / "credentials.json"
    credentials.write_text(
        json.dumps({"access_token": "tok", "refresh_token": "r", "client_id": "c", "expires_at": time.time() + 3600})
    )
    answers = [httpx.Response(200, json=_track()), httpx.Response(204)]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answers.pop(0)

    fetch = spotify_fetcher(str(credentials), "speaker", transport=httpx.MockTransport(handler))

    assert (await fetch()) is not None
    assert await fetch() is None
    assert requests[0].headers["Authorization"] == "Bearer tok"
    assert requests[0].url.params["additional_types"] == "episode"


def test_the_watcher_needs_the_screen_the_switch_and_spotify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No display, DISPLAY_NOW_PLAYING=0 or no Spotify set up means no watcher; otherwise it is hooked to Spotify."""
    from reachy_mini_conversation_app import main
    from reachy_mini_conversation_app.config import config
    from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

    display = EInkDisplay(port=8090, refresh_s=1, cache_dir=tmp_path, image_model="unused")
    monkeypatch.setattr(config, "DISPLAY_NOW_PLAYING", True)
    monkeypatch.setattr(SPOTIFY_DUCKER, "playback_changed_callback", None)
    monkeypatch.delenv("SPOTIFY_DEVICE_ID", raising=False)
    assert main._build_now_playing(None) is None
    assert main._build_now_playing(display) is None

    monkeypatch.setenv("SPOTIFY_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "speaker")
    watcher = main._build_now_playing(display)
    assert isinstance(watcher, NowPlaying)
    assert SPOTIFY_DUCKER.playback_changed_callback == watcher.refresh_soon

    monkeypatch.setattr(config, "DISPLAY_NOW_PLAYING", False)
    assert main._build_now_playing(display) is None
