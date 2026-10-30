"""Show what Spotify plays on Reachy's speaker on the e-ink screen.

Spotify has no push events for playback, so a watcher asks ``/me/player``: every 10 s while music
plays (and right when the song should end), every 30 s otherwise, and at once after Reachy's own
play, resume, pause or next. A new song puts up the music card; a screen someone asked for keeps the
screen while their conversation is open, and after it ends the card returns at the next song or
within 2 minutes. When the music stops the card stays 2 minutes, then the resting card returns.

Logs never carry titles, artists or albums.
"""

import io
import time
import asyncio
import logging
from typing import Any, Callable, Awaitable
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from reachy_mini_conversation_app.eink_display import EInkDisplay, render_now_playing


logger = logging.getLogger(__name__)

PLAYER_URL = "https://api.spotify.com/v1/me/player"
ART_HOST = "i.scdn.co"  # Spotify's image server; nothing else is downloaded
PLAYING_POLL_S = 10.0
IDLE_POLL_S = 30.0
# After the conversation that asked for a screen ends, the music card returns at the next song or by then.
REQUESTED_HOLD_S = 120.0
# How long the card stays after the music stops before the resting card returns.
STOPPED_HOLD_S = 120.0
SONG_END_MARGIN_S = 1.0
FETCH_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class Playback:
    """What plays on Reachy's Spotify speaker."""

    item_id: str
    title: str
    artist: str
    album: str
    art_url: str | None
    art_id: str
    playing: bool
    remaining_s: float


def parse_playback(body: Any, device_id: str) -> Playback | None:
    """Return the playback on ``device_id`` from a ``/me/player`` body, or None when nothing is there."""
    if not isinstance(body, dict) or (body.get("device") or {}).get("id") != device_id:
        return None
    item = body.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    if item.get("type") == "episode":
        show: dict[str, Any] = item["show"] if isinstance(item.get("show"), dict) else {}
        artist, album = str(show.get("publisher") or ""), str(show.get("name") or "")
        images, art_id = item.get("images") or show.get("images") or [], str(show.get("id") or item["id"])
    else:
        album_info: dict[str, Any] = item["album"] if isinstance(item.get("album"), dict) else {}
        artists = [str(a["name"]) for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
        artist, album = ", ".join(artists), str(album_info.get("name") or "")
        images, art_id = album_info.get("images") or [], str(album_info.get("id") or item["id"])
    duration = float(item.get("duration_ms") or 0) / 1000
    progress = float(body.get("progress_ms") or 0) / 1000
    return Playback(
        item_id=item["id"],
        title=str(item.get("name") or ""),
        artist=artist,
        album=album,
        art_url=_art_url(images),
        art_id=art_id,
        playing=bool(body.get("is_playing")),
        remaining_s=max(0.0, duration - progress),
    )


def _art_url(images: Any) -> str | None:
    """Pick the image closest to 640 px from Spotify's own image server."""
    candidates = images if isinstance(images, list) else []
    usable = [
        image
        for image in candidates
        if isinstance(image, dict)
        and isinstance(image.get("url"), str)
        and urlparse(image["url"]).scheme == "https"
        and urlparse(image["url"]).hostname == ART_HOST
    ]
    if not usable:
        return None
    return str(min(usable, key=lambda image: abs(int(image.get("width") or 640) - 640))["url"])


class NowPlaying:
    """Keeps the e-ink screen on the song that plays on Reachy's Spotify speaker."""

    def __init__(
        self,
        display: EInkDisplay,
        fetch: Callable[[], Awaitable[Playback | None]],
        *,
        in_conversation: Callable[[], bool],
        art_dir: Path,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Watch with ``fetch``; ``in_conversation`` tells whether someone is talking with Reachy."""
        self._display = display
        self._fetch = fetch
        self._in_conversation = in_conversation
        self._art_dir = art_dir
        self._transport = transport
        self._clock = clock
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shown_id: str | None = None  # the item on the music card
        self._last_id: str | None = None  # the item seen last time
        self._song_changed = False  # since the requested screen went up or its conversation ended
        self._talking = False
        self._talk_ended_at: float | None = None
        self._stopped_at: float | None = None
        self._requested_screen: str | None = None

    def refresh_soon(self) -> None:
        """Check Spotify now (after Reachy plays, resumes, pauses or skips); safe from any thread."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)

    async def run(self) -> None:
        """Watch until cancelled."""
        self._loop = asyncio.get_running_loop()
        logger.info("Now playing on the e-ink screen enabled")
        while True:
            try:
                delay = await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # the watcher must outlive any one bad response
                logger.warning("Now playing check failed: %s", type(error).__name__)
                delay = IDLE_POLL_S
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def check(self) -> float:
        """Look at Spotify once, update the screen, and return the seconds until the next look."""
        playback = await self._fetch()
        now = self._clock()
        talking = self._in_conversation()
        if self._talking and not talking:
            self._talk_ended_at, self._song_changed = now, False
        self._talking = talking
        owner = self._display.owner()
        if owner == "requested":
            screen = self._display.current().filename
            if screen != self._requested_screen:  # someone asked for a new screen since the last look
                self._requested_screen = screen
                self._song_changed = False
                self._talk_ended_at = None if talking else now
        if playback is None or not playback.playing:
            self._last_id = None
            if self._stopped_at is None:
                self._stopped_at = now
            if owner == "music" and now - self._stopped_at >= STOPPED_HOLD_S:
                logger.info("Music stopped; back to the resting card")
                self._display.show_home()
                self._shown_id = None
            return PLAYING_POLL_S if owner == "music" else IDLE_POLL_S
        self._stopped_at = None
        new_song = playback.item_id != self._last_id
        self._last_id = playback.item_id
        if owner == "requested":
            self._song_changed = self._song_changed or new_song
            ended_at = self._talk_ended_at
            if not talking and ended_at is not None and (self._song_changed or now - ended_at >= REQUESTED_HOLD_S):
                await self.show(playback)
        elif owner == "writing":
            pass  # a child is copying a word letter by letter; the card waits until it is done
        elif owner == "home" or playback.item_id != self._shown_id:
            await self.show(playback)
        else:
            self._display.keep_alive()
        return max(1.0, min(PLAYING_POLL_S, playback.remaining_s + SONG_END_MARGIN_S))

    async def show(self, playback: Playback) -> None:
        """Put the music card for ``playback`` on the screen."""
        art = await self._art(playback)
        png = render_now_playing(art, playback.title, playback.artist, playback.album)
        self._display.show(png, owner="music")
        self._shown_id = playback.item_id
        logger.info("Now playing card updated (%s)", "with art" if art is not None else "no art")

    async def show_now(self) -> Playback | None:
        """Put the card up at once ("what song is this?"); None when nothing plays on the speaker."""
        playback = await self._fetch()
        if playback is None:
            return None
        self._last_id = playback.item_id
        await self.show(playback)
        return playback

    async def _art(self, playback: Playback) -> Image.Image | None:
        if playback.art_url is None:
            return None
        path = self._art_dir / f"{''.join(c for c in playback.art_id if c.isalnum())[:40] or 'art'}.png"
        if path.is_file():
            with Image.open(path) as cached:
                return cached.convert("L")
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, transport=self._transport) as client:
                response = await client.get(playback.art_url)
                response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as downloaded:
                art = downloaded.convert("L")
        except (httpx.HTTPError, UnidentifiedImageError, OSError) as error:
            logger.info("Album art not available: %s", type(error).__name__)
            return None
        art.thumbnail((640, 640))
        path.parent.mkdir(parents=True, exist_ok=True)
        art.save(path)
        return art


def spotify_fetcher(
    credentials_path: str, device_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> Callable[[], Awaitable[Playback | None]]:
    """Return a fetch that asks Spotify what plays on ``device_id``."""
    from reachy_mini_conversation_app.tools.spotify_control import spotify_access_token

    async def fetch() -> Playback | None:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, transport=transport) as client:
            token = await spotify_access_token(client, credentials_path)
            response = await client.get(
                PLAYER_URL, params={"additional_types": "episode"}, headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code == 204:
                return None
            response.raise_for_status()
            return parse_playback(response.json(), device_id)

    return fetch
