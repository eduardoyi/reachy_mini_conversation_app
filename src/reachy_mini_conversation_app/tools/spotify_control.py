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

import httpx

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class SpotifySearchLogFilter(logging.Filter):
    """Keep search text out of HTTP request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Suppress Spotify search URL logging while preserving other requests."""
        return "api.spotify.com/v1/search" not in record.getMessage()


logging.getLogger("httpx").addFilter(SpotifySearchLogFilter())


class SpotifyControl(Tool):
    """Control a privately configured Spotify Connect device."""

    name = "spotify_control"
    description = (
        "Control Spotify on the configured computer, only when the user asks. "
        "Actions: search, play, queue, resume, pause, next, status. Search Spotify first, then use an exact returned URI. "
        "Search types: track, artist, album, episode, playlist. For podcasts search episodes. "
        "For decade requests use year:2000-2009 in the query. Play replaces current playback; queue appends a "
        "track or episode to the queue (not guaranteed immediately next). Ask only if matches are ambiguous. "
        "Cannot change devices or volume. Never automatically retry failed playback or queue commands."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "play", "queue", "resume", "pause", "next", "status"]},
            "query": {"type": "string", "description": "Spotify search text, required for search."},
            "search_type": {"type": "string", "enum": ["track", "artist", "album", "episode", "playlist"]},
            "uri": {"type": "string", "description": "Exact URI from a recent search, required for play/queue."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        """Serialize commands and token refresh without connecting during startup."""
        self._lock = asyncio.Lock()
        self._search_results: dict[str, float] = {}

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
        }:
            return {"error": "Choose search, play, queue, resume, pause, next, or status."}
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
        async with self._lock:
            try:
                self._search_results = {
                    key: expiry for key, expiry in self._search_results.items() if expiry > time.monotonic()
                }
                if action in {"play", "queue"} and uri not in self._search_results:
                    return {"error": "Search again before playing or queueing this selection."}
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
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    if expiry <= time.time() + 60:
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
                        credentials.update(
                            access_token=access, refresh_token=refresh, expires_at=time.time() + lifetime
                        )
                        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".spotify-")
                        try:
                            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                                json.dump(credentials, output)
                            os.replace(temporary, path)
                        finally:
                            Path(temporary).unlink(missing_ok=True)
                    headers = {"Authorization": "Bearer " + access}
                    if action == "search":
                        response = await client.get(
                            "https://api.spotify.com/v1/search",
                            params={"q": str(query).strip(), "type": str(search_type), "limit": 5},
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
                        return {"error": "Open Spotify on the configured computer; it is unavailable or restricted."}
                    if action == "status":
                        response = await client.get("https://api.spotify.com/v1/me/player", headers=headers)
                        response.raise_for_status()
                        if response.status_code == 204:
                            return {"status": "No current playback."}
                        playback: object = response.json()
                        if not isinstance(playback, dict):
                            raise ValueError("Invalid playback response")
                        device = playback.get("device")
                        if not isinstance(device, dict) or device.get("id") != device_id:
                            return {"status": "Spotify playback is not on the configured computer."}
                        item = playback.get("item")
                        title = item.get("name") if isinstance(item, dict) else None
                        return {
                            "status": "playing" if playback.get("is_playing") else "paused",
                            "track": title if isinstance(title, str) else "Unknown track",
                        }
                    if action in {"play", "queue"}:
                        assert isinstance(uri, str)
                        if action == "queue":
                            response = await client.post(
                                "https://api.spotify.com/v1/me/player/queue",
                                params={"device_id": device_id, "uri": uri},
                                headers=headers,
                            )
                        else:
                            body = (
                                {"uris": [uri]}
                                if uri.startswith(("spotify:track:", "spotify:episode:"))
                                else {"context_uri": uri}
                            )
                            response = await client.put(
                                "https://api.spotify.com/v1/me/player/play",
                                params={"device_id": device_id},
                                json=body,
                                headers=headers,
                            )
                        response.raise_for_status()
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
                    return {"status": "Spotify accepted the " + action + " command."}
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                logger.warning("Spotify request rejected: HTTP %d", code)
                if code == 401:
                    return {"error": "Reconnect Spotify authorization, then try again."}
                if code == 429:
                    return {"error": "Spotify rate limited the request. Wait before asking again."}
                if code == 404:
                    return {"error": "Start a song in Spotify on the configured computer first."}
                return {"error": "Spotify rejected the request (HTTP " + str(code) + ")."}
            except httpx.RequestError as error:
                logger.warning("Spotify connection failed: %s", type(error).__name__)
                return {"error": "Spotify connection failed; the command may not have completed. Do not auto-retry."}
            except (OSError, ValueError, TypeError) as error:
                logger.warning("Spotify configuration or response failed: %s", type(error).__name__)
                return {"error": "Spotify credentials or response are invalid. Reconnect Spotify."}
