"""Optional Spotify playback control restricted to one configured device."""

import os
import re
import json
import math
import time
import asyncio
import logging
import tempfile
from pathlib import Path
from collections.abc import Callable, Awaitable

import httpx

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class SpotifySearchLogFilter(logging.Filter):
    """Keep search text out of HTTP request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Suppress Spotify search URL logging while preserving other requests."""
        return "api.spotify.com/v1/search" not in record.getMessage()


logging.getLogger("httpx").addFilter(SpotifySearchLogFilter())
SPOTIFY_TRANSFER_SETTLE_SECONDS = 0.5
MUSIC_STARTED_IN_CONVERSATION = (
    "Spotify is playing (quietly while you talk). Reply with only a brief 'Enjoy!' in the user's language and "
    "nothing else; the conversation closes in a few seconds so the user can listen, unless they say more."
)


_CREDENTIALS_LOCK = asyncio.Lock()


async def spotify_access_token(client: httpx.AsyncClient, credentials_path: str) -> str:
    """Return a valid access token, refreshing and privately persisting it when it is about to expire."""
    async with _CREDENTIALS_LOCK:
        path = Path(credentials_path).expanduser()
        credentials: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(credentials, dict):
            raise ValueError("Invalid credentials")
        access = credentials.get("access_token")
        refresh = credentials.get("refresh_token")
        client_id = credentials.get("client_id")
        expiry = credentials.get("expires_at")
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
            or not isinstance(client_id, str)
            or not client_id
            or not isinstance(expiry, (int, float))
            or not math.isfinite(expiry)
        ):
            raise ValueError("Invalid credentials")
        if expiry > time.time() + 60:
            return access
        response = await client.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id},
        )
        response.raise_for_status()
        token: object = response.json()
        if not isinstance(token, dict):
            raise ValueError("Invalid token response")
        access = token.get("access_token")
        lifetime = token.get("expires_in")
        refresh = token.get("refresh_token", refresh)
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
            or not isinstance(lifetime, (int, float))
            or not math.isfinite(lifetime)
            or lifetime <= 0
        ):
            raise ValueError("Invalid token response")
        credentials.update(access_token=access, refresh_token=refresh, expires_at=time.time() + lifetime)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".spotify-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(credentials, output)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return access


class SpotifyDucker:
    """Lower Spotify on the configured device during a wake-word conversation and restore it afterwards.

    Like a smart speaker: music playing when the user wakes Reachy (or started during the conversation)
    drops to a third of its level (at most ``duck_percent``) so the user can be heard, then returns to its previous level when the
    conversation ends, unless the user changed the volume meanwhile. A podcast or audiobook episode is
    paused instead (its voices would be heard as the user) and resumed afterwards.
    """

    # A failed restore is retried once; if that fails too, the state is kept for the next conversation end.
    _RESTORE_ATTEMPTS = 2
    _RESTORE_RETRY_DELAY_S = 1.0
    _EXIT_RESTORE_TIMEOUT_S = 5.0
    # While the user talks to Reachy, music drops to this fraction of its level (at most duck_percent).
    _DUCK_FRACTION = 1 / 3

    def __init__(self, duck_percent: int = 20) -> None:
        """Create an idle ducker that lowers music to ``duck_percent``."""
        self._duck_percent = duck_percent
        self._session_active = False
        self._restore_to: int | None = None
        self._ducked_to: int | None = None
        # The episode paused for the conversation (its URI, or "" when Spotify did not report one).
        self._paused_episode: str | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        # Set by the hybrid runtime: close the conversation quickly while music is playing, and stop doing
        # so when the user pauses it.
        self.music_playing_callback: Callable[[], None] | None = None
        self.music_stopped_callback: Callable[[], None] | None = None
        # Called after Reachy plays, resumes, pauses or skips, so the e-ink screen can follow at once.
        self.playback_changed_callback: Callable[[], None] | None = None

    @property
    def in_conversation(self) -> bool:
        """Return whether a wake-word conversation is currently open."""
        return self._session_active

    @staticmethod
    def _configuration() -> tuple[str, str] | None:
        credentials_path = os.getenv("SPOTIFY_CREDENTIALS_FILE", "").strip()
        device_id = os.getenv("SPOTIFY_DEVICE_ID", "").strip()
        return (credentials_path, device_id) if credentials_path and device_id else None

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def session_started(self) -> None:
        """Lower music that is already playing when a conversation begins, and close that conversation quickly."""
        self._session_active = True
        if await self._duck(require_playing=True):
            self._notify_music_playing()

    def _notify_music_playing(self) -> None:
        self._notify(self.music_playing_callback)

    @staticmethod
    def _notify(callback: Callable[[], None] | None) -> None:
        if callback is not None:
            try:
                callback()
            except Exception as error:
                logger.warning("Music wind-down callback failed: %s", type(error).__name__)

    async def session_ended(self) -> None:
        """Restore the volume lowered during the conversation."""
        self._session_active = False
        # A lowering for music started just before the end (a quick "play") may still be on its way.
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._restore()

    def playback_changed(self) -> None:
        """Tell the now-playing screen that Reachy just changed what plays."""
        self._notify(self.playback_changed_callback)

    def playback_paused(self) -> None:
        """Keep user-paused playback paused afterwards and stop closing the conversation quickly."""
        if not self._session_active:
            return
        self._paused_episode = None
        self._notify(self.music_stopped_callback)

    def restore_on_exit(self) -> None:
        """Undo lowering or pausing left by a conversation the app quit during, after its event loop ended."""
        if self._restore_to is None and self._paused_episode is None:
            return
        self._session_active = False
        self._lock = asyncio.Lock()  # the lock belonged to the app's event loop, which is gone
        try:
            asyncio.run(asyncio.wait_for(self._restore(), self._EXIT_RESTORE_TIMEOUT_S))
        except (TimeoutError, RuntimeError) as error:
            logger.warning("Spotify restore at exit failed: %s", type(error).__name__)

    def playback_started(self) -> None:
        """Lower music started during a conversation and let the conversation wind down."""
        if not self._session_active:
            return
        self._spawn(self._duck_quietly())
        self._notify_music_playing()

    async def _duck_quietly(self) -> None:
        await self._duck(require_playing=False)

    @property
    def level_before_ducking(self) -> int | None:
        """The volume the music had before Reachy lowered it to listen, or None when it is not lowered."""
        return self._restore_to

    def forget_restore_level(self) -> None:
        """Keep an explicitly requested volume instead of restoring the pre-conversation level."""
        self._restore_to = None
        self._ducked_to = None

    async def _device_volume(self, client: httpx.AsyncClient, headers: dict[str, str], device_id: str) -> int | None:
        response = await client.get("https://api.spotify.com/v1/me/player/devices", headers=headers)
        response.raise_for_status()
        payload: object = response.json()
        devices = payload.get("devices") if isinstance(payload, dict) else None
        for device in devices if isinstance(devices, list) else []:
            if isinstance(device, dict) and device.get("id") == device_id:
                volume = device.get("volume_percent")
                if device.get("supports_volume") is False or not isinstance(volume, int):
                    return None
                return volume
        return None

    def _duck_target(self, volume: int) -> int:
        """Return the level to lower ``volume`` to: a third of it, and never above ``duck_percent``.

        A fixed level barely helped at low volumes: on 2026-10-19 music at 29 went to 20 and Whisper heard
        "stop the music" as "start the music".
        """
        return max(1, min(self._duck_percent, round(volume * self._DUCK_FRACTION)))

    async def _duck(self, *, require_playing: bool) -> bool:
        """Lower the configured device; return whether music is playing there (always True when not checked)."""
        configuration = self._configuration()
        if configuration is None:
            return False
        credentials_path, device_id = configuration
        async with self._lock:
            if not require_playing and not self._session_active:
                return False  # the conversation ended before music it started could be lowered
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    headers = {"Authorization": "Bearer " + await spotify_access_token(client, credentials_path)}
                    if require_playing:
                        response = await client.get("https://api.spotify.com/v1/me/player", headers=headers)
                        response.raise_for_status()
                        playback: object = response.json() if response.status_code != 204 else None
                        device = playback.get("device") if isinstance(playback, dict) else None
                        if (
                            not isinstance(playback, dict)
                            or playback.get("is_playing") is not True
                            or not isinstance(device, dict)
                            or device.get("id") != device_id
                        ):
                            return False
                        if playback.get("currently_playing_type") == "episode":
                            await self._pause_episode(client, headers, device_id, playback)
                            return True
                    volume = await self._device_volume(client, headers, device_id)
                    if volume is None or (self._restore_to is not None and volume == self._ducked_to):
                        return True  # still lowered from a conversation whose restore failed: not lower again
                    target = self._duck_target(volume)
                    if volume <= target:
                        return True
                    response = await client.put(
                        "https://api.spotify.com/v1/me/player/volume",
                        params={"device_id": device_id, "volume_percent": target},
                        headers=headers,
                    )
                    response.raise_for_status()
                    if self._restore_to is None:
                        self._restore_to = volume
                    self._ducked_to = target
                    logger.info("Spotify lowered for conversation from=%d to=%d", volume, target)
                    return True
            except (httpx.HTTPError, OSError, ValueError, TypeError) as error:
                logger.warning("Spotify lowering failed: %s", type(error).__name__)
                return False

    async def _pause_episode(
        self, client: httpx.AsyncClient, headers: dict[str, str], device_id: str, playback: dict[str, object]
    ) -> None:
        """Pause a playing podcast or audiobook episode: lowered speech would still be heard as the user."""
        response = await client.put(
            "https://api.spotify.com/v1/me/player/pause", params={"device_id": device_id}, headers=headers
        )
        response.raise_for_status()
        item = playback.get("item")
        uri = item.get("uri") if isinstance(item, dict) else None
        self._paused_episode = uri if isinstance(uri, str) else ""
        logger.info("Spotify episode paused for conversation")

    async def _restore(self) -> None:
        configuration = self._configuration()
        if configuration is None or (self._restore_to is None and self._paused_episode is None):
            return
        credentials_path, device_id = configuration
        async with self._lock:
            for attempt in range(1, self._RESTORE_ATTEMPTS + 1):
                try:
                    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                        headers = {"Authorization": "Bearer " + await spotify_access_token(client, credentials_path)}
                        await self._restore_volume(client, headers, device_id)
                        await self._resume_episode(client, headers, device_id)
                    return
                except (httpx.HTTPError, OSError, ValueError, TypeError) as error:
                    if attempt == self._RESTORE_ATTEMPTS:
                        # Keep the state: the next conversation end (or app exit) tries again.
                        logger.warning("Spotify restore failed after %d attempts: %s", attempt, type(error).__name__)
                        return
                    logger.info("Spotify restore failed (%s); retrying", type(error).__name__)
                    await asyncio.sleep(self._RESTORE_RETRY_DELAY_S)

    async def _restore_volume(self, client: httpx.AsyncClient, headers: dict[str, str], device_id: str) -> None:
        restore_to, ducked_to = self._restore_to, self._ducked_to
        if restore_to is None:
            return
        if await self._device_volume(client, headers, device_id) != ducked_to:
            self.forget_restore_level()  # the user changed the volume during the conversation; keep it
            return
        response = await client.put(
            "https://api.spotify.com/v1/me/player/volume",
            params={"device_id": device_id, "volume_percent": restore_to},
            headers=headers,
        )
        response.raise_for_status()
        self.forget_restore_level()
        logger.info("Spotify volume restored to=%d", restore_to)

    async def _resume_episode(self, client: httpx.AsyncClient, headers: dict[str, str], device_id: str) -> None:
        paused = self._paused_episode
        if paused is None:
            return
        response = await client.get("https://api.spotify.com/v1/me/player", headers=headers)
        response.raise_for_status()
        playback: object = response.json() if response.status_code != 204 else None
        item = playback.get("item") if isinstance(playback, dict) else None
        device = playback.get("device") if isinstance(playback, dict) else None
        if (
            not isinstance(playback, dict)
            or playback.get("is_playing") is True
            or not isinstance(device, dict)
            or device.get("id") != device_id
            or (paused and (not isinstance(item, dict) or item.get("uri") != paused))
        ):
            self._paused_episode = None  # something else was played or resumed meanwhile; leave it
            return
        response = await client.put(
            "https://api.spotify.com/v1/me/player/play", params={"device_id": device_id}, headers=headers
        )
        response.raise_for_status()
        self._paused_episode = None
        logger.info("Spotify episode resumed after conversation")


def _duck_percent_from_env() -> int:
    raw = os.getenv("SPOTIFY_DUCK_VOLUME_PERCENT", "").strip()
    try:
        value = int(raw) if raw else 20
    except ValueError:
        value = 20
    return value if 0 <= value <= 100 else 20


SPOTIFY_DUCKER = SpotifyDucker(_duck_percent_from_env())


class SpotifyControl(Tool):
    """Control a privately configured Spotify Connect device."""

    name = "spotify_control"
    description = (
        "Control Spotify on the configured device, only when the user asks. "
        "Actions: search, play, queue, resume, pause, next, status, volume. "
        "Search Spotify first, then use an exact returned URI. "
        "Search types: track, artist, album, episode, playlist. For podcasts search episodes. "
        "For decade requests use year:2000-2009 in the query. Play replaces current playback; queue appends a "
        "track or episode to the queue (not guaranteed immediately next). Ask only if matches are ambiguous. "
        "Volume sets the configured device to volume_percent (0-100); music is lowered automatically while "
        "you talk and restored afterwards, so only use volume when the user asks for a level. "
        "Cannot change devices. Never automatically retry failed playback or queue commands."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "play", "queue", "resume", "pause", "next", "status", "volume"],
            },
            "query": {"type": "string", "description": "Spotify search text, required for search."},
            "search_type": {"type": "string", "enum": ["track", "artist", "album", "episode", "playlist"]},
            "uri": {"type": "string", "description": "Exact URI from a recent search, required for play/queue."},
            "volume_percent": {"type": "integer", "description": "Device volume 0-100, required for volume."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        """Serialize commands and token refresh without connecting during startup."""
        self._lock = asyncio.Lock()
        self._search_results: dict[str, float] = {}
        # Track URI -> its album URI from search, so a track can start inside its album.
        self._track_albums: dict[str, str] = {}

    async def __call__(self, deps: ToolDependencies, **kwargs: object) -> dict[str, object]:
        """Execute a bounded request without replaying playback mutations."""
        action = kwargs.get("action")
        if not isinstance(action, str) or action not in {
            "search",
            "play",
            "queue",
            "resume",
            "pause",
            "next",
            "status",
            "volume",
        }:
            return {"error": "Choose search, play, queue, resume, pause, next, status, or volume."}
        volume_percent = kwargs.get("volume_percent")
        if action == "volume" and (
            not isinstance(volume_percent, int) or isinstance(volume_percent, bool) or not 0 <= volume_percent <= 100
        ):
            return {"error": "Provide volume_percent between 0 and 100."}
        query = kwargs.get("query", "")
        search_type = kwargs.get("search_type", "track")
        uri = kwargs.get("uri", "")
        if action == "search" and (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 250
            or not isinstance(search_type, str)
            or search_type not in {"track", "artist", "album", "episode", "playlist"}
        ):
            return {"error": "Provide a search query and a supported search type."}
        if action in {"play", "queue"} and (
            not isinstance(uri, str)
            or not re.fullmatch(r"spotify:(track|episode|album|artist|playlist):[A-Za-z0-9]{22}", uri)
        ):
            return {"error": "Use an exact Spotify URI returned by search."}
        if action == "queue" and isinstance(uri, str) and not uri.startswith(("spotify:track:", "spotify:episode:")):
            return {"error": "Queue accepts individual tracks or episodes only."}
        credentials_path = os.getenv("SPOTIFY_CREDENTIALS_FILE", "").strip()
        device_id = os.getenv("SPOTIFY_DEVICE_ID", "").strip()
        if not credentials_path or not device_id:
            return {"error": "Spotify is not configured."}
        if action in {"play", "resume"}:
            from reachy_mini_conversation_app.music_speaker import prepare_for_playback

            # The Mac's Spotify app plays through a Bluetooth speaker, when one is set up: open, connect, route.
            await asyncio.to_thread(prepare_for_playback)
        async with self._lock:
            try:
                self._search_results = {
                    key: expiry for key, expiry in self._search_results.items() if expiry > time.monotonic()
                }
                self._track_albums = {
                    key: album for key, album in self._track_albums.items() if key in self._search_results
                }
                if action in {"play", "queue"} and uri not in self._search_results:
                    return {"error": "Search again before playing or queueing this selection."}
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    access = await spotify_access_token(client, credentials_path)
                    headers = {"Authorization": "Bearer " + access}
                    if action == "search":
                        response = await client.get(
                            "https://api.spotify.com/v1/search",
                            params={
                                "q": str(query).strip(),
                                "type": str(search_type),
                                "limit": 5,
                            },
                            headers=headers,
                        )
                        response.raise_for_status()
                        search_payload: object = response.json()
                        if not isinstance(search_payload, dict):
                            raise ValueError("Invalid search response")
                        group = search_payload.get(str(search_type) + "s")
                        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
                            raise ValueError("Invalid search results")
                        results: list[dict[str, object]] = []
                        for item in group["items"][:5]:
                            if not isinstance(item, dict) or item.get("is_playable") is False:
                                continue
                            found_uri, title = item.get("uri"), item.get("name")
                            if not isinstance(found_uri, str) or not isinstance(title, str):
                                continue
                            if not re.fullmatch("spotify:" + str(search_type) + r":[A-Za-z0-9]{22}", found_uri):
                                continue
                            artists = item.get("artists", [])
                            creators = (
                                [
                                    artist["name"]
                                    for artist in artists
                                    if isinstance(artist, dict) and isinstance(artist.get("name"), str)
                                ]
                                if isinstance(artists, list)
                                else []
                            )
                            results.append({"name": title[:300], "creators": creators, "uri": found_uri})
                            self._search_results[found_uri] = time.monotonic() + 600
                            album = item.get("album")
                            album_uri = album.get("uri") if isinstance(album, dict) else None
                            if isinstance(album_uri, str) and re.fullmatch(
                                r"spotify:album:[A-Za-z0-9]{22}", album_uri
                            ):
                                self._track_albums[found_uri] = album_uri
                        self._search_results = dict(list(self._search_results.items())[-50:])
                        return {"source": "Spotify", "results": results}
                    response = await client.get("https://api.spotify.com/v1/me/player/devices", headers=headers)
                    response.raise_for_status()
                    payload: object = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("devices"), list):
                        raise ValueError("Invalid devices response")
                    matches = [
                        device
                        for device in payload["devices"]
                        if isinstance(device, dict) and device.get("id") == device_id
                    ]
                    if len(matches) != 1 or matches[0].get("is_restricted") is not False:
                        return {"error": "Open Spotify on the configured device; it is unavailable or restricted."}
                    if action == "status":
                        device_volume = matches[0].get("volume_percent")
                        volume_info = {"volume_percent": device_volume} if isinstance(device_volume, int) else {}
                        response = await client.get("https://api.spotify.com/v1/me/player", headers=headers)
                        response.raise_for_status()
                        if response.status_code == 204:
                            return {"status": "No current playback.", **volume_info}
                        playback: object = response.json()
                        if not isinstance(playback, dict):
                            raise ValueError("Invalid playback response")
                        device = playback.get("device")
                        if not isinstance(device, dict) or device.get("id") != device_id:
                            return {"status": "Spotify playback is not on the configured device."}
                        item = playback.get("item")
                        title = item.get("name") if isinstance(item, dict) else None
                        return {
                            "status": "playing" if playback.get("is_playing") else "paused",
                            "track": title if isinstance(title, str) else "Unknown track",
                            **volume_info,
                        }
                    if action == "volume":
                        assert isinstance(volume_percent, int)
                        response = await client.put(
                            "https://api.spotify.com/v1/me/player/volume",
                            params={"device_id": device_id, "volume_percent": volume_percent},
                            headers=headers,
                        )
                        response.raise_for_status()
                        # A level the user asked for is the one to return to after the conversation.
                        SPOTIFY_DUCKER.forget_restore_level()
                        return {"status": f"Spotify volume set to {volume_percent}%."}
                    if action in {"play", "resume"} and matches[0].get("is_active") is not True:
                        # An idle desktop app accepts play commands (HTTP 204) without playing; activate it first.
                        response = await client.put(
                            "https://api.spotify.com/v1/me/player",
                            json={"device_ids": [device_id], "play": False},
                            headers=headers,
                        )
                        response.raise_for_status()
                        await asyncio.sleep(SPOTIFY_TRANSFER_SETTLE_SECONDS)
                    if action in {"play", "queue"}:
                        assert isinstance(uri, str)
                        if action == "queue":
                            response = await client.post(
                                "https://api.spotify.com/v1/me/player/queue",
                                params={"device_id": device_id, "uri": uri},
                                headers=headers,
                            )
                        else:
                            album_uri = self._track_albums.get(uri)
                            body: dict[str, object]
                            if album_uri is not None:
                                # The Mac desktop app accepts a bare track (HTTP 204) but plays nothing, while
                                # album/artist/playlist contexts play; so start the track inside its album.
                                body = {"context_uri": album_uri, "offset": {"uri": uri}}
                            elif uri.startswith(("spotify:track:", "spotify:episode:")):
                                body = {"uris": [uri]}
                            else:
                                body = {"context_uri": uri}
                            response = await client.put(
                                "https://api.spotify.com/v1/me/player/play",
                                params={"device_id": device_id},
                                json=body,
                                headers=headers,
                            )
                        response.raise_for_status()
                        SPOTIFY_DUCKER.playback_changed()
                        if action == "play":
                            logger.info("Spotify play accepted kind=%s", uri.split(":")[1])
                            SPOTIFY_DUCKER.playback_started()
                            if SPOTIFY_DUCKER.in_conversation:
                                return {"status": MUSIC_STARTED_IN_CONVERSATION}
                        return {"status": "Spotify accepted the " + action + " command."}
                    method, endpoint = {
                        "resume": ("PUT", "play"),
                        "pause": ("PUT", "pause"),
                        "next": ("POST", "next"),
                    }[action]
                    response = await client.request(
                        method,
                        "https://api.spotify.com/v1/me/player/" + endpoint,
                        params={"device_id": device_id},
                        headers=headers,
                    )
                    response.raise_for_status()
                    SPOTIFY_DUCKER.playback_changed()
                    if action == "resume":
                        SPOTIFY_DUCKER.playback_started()
                    elif action == "pause":
                        SPOTIFY_DUCKER.playback_paused()
                    return {"status": "Spotify accepted the " + action + " command."}
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                logger.warning("Spotify request rejected: HTTP %d", code)
                if code == 401:
                    return {"error": "Reconnect Spotify authorization, then try again."}
                if code == 429:
                    return {"error": "Spotify rate limited the request. Wait before asking again."}
                if code == 404:
                    return {"error": "Start a song in Spotify on the configured device first."}
                return {"error": "Spotify rejected the request (HTTP " + str(code) + ")."}
            except httpx.RequestError as error:
                logger.warning("Spotify connection failed: %s", type(error).__name__)
                return {"error": "Spotify connection failed; the command may not have completed. Do not auto-retry."}
            except (OSError, ValueError, TypeError) as error:
                logger.warning("Spotify configuration or response failed: %s", type(error).__name__)
                return {"error": "Spotify credentials or response are invalid. Reconnect Spotify."}
