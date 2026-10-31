"""The household admin app's server: a phone web app reached only through Tailscale Serve.

It listens on loopback only. Tailscale Serve publishes it to the tailnet and adds the signed-in user's login
to every request; anyone not in ``ADMIN_ALLOWED_LOGINS`` is refused, and so is any request without the
header (a Funnel request, or another program on this computer). Changes must come from the app's own page.

    reachy-admin            # serves 127.0.0.1:ADMIN_PORT (7870)
"""

import os
import re
import sys
import json
import time
import shutil
import asyncio
import logging
import ipaddress
from typing import Any, Final
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit
from collections.abc import AsyncIterator

import httpx
import uvicorn
from dotenv import find_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.whatsapp import WhatsAppLink
from reachy_mini_conversation_app.json_lines import JsonLines
from reachy_mini_conversation_app.admin.costs import PERIODS, breakdown
from reachy_mini_conversation_app.usage_costs import get_ledger
from reachy_mini_conversation_app.admin.people import (
    PeopleFiles,
    PersonError,
    validate,
    person_from_json,
    shared_spoken_names,
)
from reachy_mini_conversation_app.admin.gallery import Gallery, GalleryError
from reachy_mini_conversation_app.admin.records import RecordError, StaleVersion
from reachy_mini_conversation_app.admin.app_link import AppLink, AppUnavailable
from reachy_mini_conversation_app.admin.notifier import Watch, Notifier
from reachy_mini_conversation_app.admin.settings import SETTINGS, SettingError, read_settings, write_settings
from reachy_mini_conversation_app.admin.personality import read_personality, write_personality


logger = logging.getLogger(__name__)

LOGIN_HEADER: Final[str] = "tailscale-user-login"
NAME_HEADER: Final[str] = "tailscale-user-name"
MAX_BODY_BYTES: Final[int] = 64 * 1024
MAX_PHOTOS_BYTES: Final[int] = 12 * 1024 * 1024
MAX_GROUP_TITLE_CHARS: Final[int] = 100
MAX_PERSONALITY_BYTES: Final[int] = 256 * 1024
MAX_GROUP_PHONES: Final[int] = 20
# The TRMNL counts as checking in when it did so within this long.
DISPLAY_RECENT_S: Final[float] = 15 * 60
STATIC_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "admin"
_NO_STORE: Final[dict[str, str]] = {"Cache-Control": "no-store"}
# A restart stops the conversation app with SIGINT (its clean shutdown) and waits for it and its launcher
# scripts (which may stop the robot daemon on their way out) before its LaunchAgent starts it again.
APP_PROCESS: Final[str] = "bin/reachy-mini-conversation-app"
LAUNCHER_PROCESS: Final[str] = "start-reachy"
RESTART_STOP_S: Final[float] = 120
# No "Reachy stopped" notification this long after a restart from the app.
RESTART_QUIET_S: Final[float] = 300


@dataclass(frozen=True)
class AdminSettings:
    """Where the admin app finds the people files, the running app, WhatsApp and its own data."""

    allowed_logins: frozenset[str]
    data_dir: Path
    contacts_file: Path
    calls_file: Path
    env_file: Path
    control_url: str
    control_token_file: Path
    daemon_url: str
    app_service: str
    display_dir: Path
    activity_file: Path
    whatsapp_url: str | None = None
    whatsapp_auth: str | None = None
    photos_dir: Path | None = None

    @classmethod
    def from_env(cls) -> "AdminSettings":
        """Read the settings from the environment (the app's ``.env`` is already loaded by ``config``)."""
        logins = os.getenv("ADMIN_ALLOWED_LOGINS") or ""
        return cls(
            allowed_logins=frozenset(part.strip().lower() for part in logins.split(",") if part.strip()),
            data_dir=Path(os.path.expanduser(os.getenv("ADMIN_DATA_DIR") or "~/.local/share/reachy-admin")),
            contacts_file=Path(config.WHATSAPP_CONTACTS_FILE),
            calls_file=Path(config.WHATSAPP_CALLS_APPROVED_FILE),
            env_file=Path(os.getenv("ADMIN_ENV_FILE") or find_dotenv(usecwd=True) or ".env").expanduser(),
            control_url=f"http://127.0.0.1:{config.APP_CONTROL_PORT}",
            control_token_file=Path(config.APP_CONTROL_TOKEN_FILE),
            daemon_url=(os.getenv("REACHY_DAEMON_URL") or "http://127.0.0.1:8000").rstrip("/"),
            app_service=(os.getenv("ADMIN_APP_SERVICE") or "").strip(),
            display_dir=Path.home() / ".cache" / "reachy-mini-conversation-app" / "display" / "screen",
            activity_file=Path(config.ACTIVITY_LOG_FILE),
            whatsapp_url=config.WHATSAPP_API_URL,
            whatsapp_auth=config.WHATSAPP_API_AUTH,
            photos_dir=Path(config.PHOTO_ARCHIVE_DIR),
        )


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code, headers=_NO_STORE)


def _who(request: Request) -> str:
    return request.headers.get(LOGIN_HEADER, "").strip().lower()


def refusal(request: Request, allowed: frozenset[str]) -> Response | None:
    """Return a 403 unless Tailscale Serve brought it from an allowed login (a change: from the app's own page)."""
    client_host = request.client.host if request.client is not None else ""
    try:
        local = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        local = False
    if not local or _who(request) not in allowed:
        return Response(status_code=403, headers=_NO_STORE)
    if request.method not in {"GET", "HEAD"}:
        origin = urlsplit(request.headers.get("origin", "")).netloc
        hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")} - {""}
        if not origin or origin not in hosts:
            return Response(status_code=403, headers=_NO_STORE)
    return None


async def _json_body(request: Request, max_bytes: int = MAX_BODY_BYTES) -> dict[str, Any] | Response:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        return Response(status_code=415, headers=_NO_STORE)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            return Response(status_code=413, headers=_NO_STORE)
    try:
        payload = json.loads(body)
    except ValueError:
        return Response(status_code=400, headers=_NO_STORE)
    return payload if isinstance(payload, dict) else Response(status_code=400, headers=_NO_STORE)


async def run_command(*args: str) -> tuple[int, str]:
    """Run ``args`` and return its exit code and error output."""
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    return process.returncode or 0, stderr.decode(errors="replace").strip()


async def restart_app(service: str) -> None:
    """Stop the conversation app, wait for it and its launcher to finish, then start its LaunchAgent."""
    await run_command("pkill", "-INT", "-f", APP_PROCESS)
    deadline = asyncio.get_running_loop().time() + RESTART_STOP_S
    while (await run_command("pgrep", "-f", f"{APP_PROCESS}|{LAUNCHER_PROCESS}"))[0] == 0:
        if asyncio.get_running_loop().time() > deadline:
            logger.warning("Restart skipped: Reachy or its launcher still running after %.0fs", RESTART_STOP_S)
            return
        await asyncio.sleep(1)
    code, error = await run_command("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{service}")
    if code != 0:
        logger.warning("Restart failed: launchctl exited %s: %s", code, error)


def _relay(response: httpx.Response) -> Response:
    """Pass the control API's JSON answer through."""
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
        headers=_NO_STORE,
    )


def create_app(settings: AdminSettings, link: AppLink | None = None, notifier: Notifier | None = None) -> FastAPI:
    """Return the admin app for ``settings``; ``link`` and ``notifier`` are injectable for tests."""
    link = link or AppLink(settings.control_url, settings.control_token_file)
    people_files = PeopleFiles(settings.contacts_file, settings.calls_file)
    audit = JsonLines(settings.data_dir / "audit.jsonl")
    activity = JsonLines(settings.activity_file)
    notifier = notifier or Notifier(settings.data_dir)
    whatsapp = (
        WhatsAppLink(settings.whatsapp_url, settings.whatsapp_auth)
        if settings.whatsapp_url and settings.whatsapp_auth
        else None
    )
    http = httpx.AsyncClient(timeout=5.0)
    gallery = Gallery(settings.photos_dir or Path(config.PHOTO_ARCHIVE_DIR), settings.display_dir.parent)
    restarts: set[asyncio.Task[None]] = set()
    restarted_at: list[float] = []

    async def faces() -> list[dict[str, Any]] | None:
        try:
            response = await link.request("GET", "/api/faces")
        except AppUnavailable:
            return None
        people = response.json().get("people") if response.status_code == 200 else None
        return [p for p in people if isinstance(p, dict)] if isinstance(people, list) else None

    async def health() -> dict[str, Any]:
        app_status = await link.status()
        whatsapp_state: dict[str, Any] = {"ok": None, "detail": "Not set up"}
        if whatsapp is not None:
            server = await whatsapp.status()
            if server is None:
                whatsapp_state = {"ok": False, "detail": "WhatsApp server not running"}
            else:
                whatsapp_state = {"ok": server.logged_in, "detail": "" if server.logged_in else server.message}
        calls_linked = app_status.get("calls_linked") if app_status else None
        privacy = dict(app_status["privacy"]) if app_status else {"on": False, "since": None}
        if privacy.get("on") and privacy.get("since"):
            started = datetime.fromisoformat(str(privacy["since"]))
            privacy["on_for_s"] = (datetime.now(timezone.utc) - started).total_seconds()
        device: dict[str, Any] = {}
        try:
            device = json.loads((settings.display_dir / "device.json").read_text())
        except (OSError, ValueError):
            pass
        last_check_in = device.get("ended") if isinstance(device.get("ended"), (int, float)) else None
        volume = None
        try:
            answer = await http.get(f"{settings.daemon_url}/api/volume/current")
            volume = answer.json().get("volume") if answer.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            pass
        free = shutil.disk_usage(Path.home()).free
        return {
            "app": {
                "running": app_status is not None,
                "restarting": bool(restarted_at) and time.monotonic() - restarted_at[-1] < RESTART_QUIET_S,
                **(app_status or {}),
            },
            "privacy": privacy,
            "whatsapp": whatsapp_state,
            "calls": {"ok": calls_linked, "detail": "" if calls_linked else "Call service not connected"},
            "display": {
                "last_check_in": last_check_in,
                "recent": last_check_in is not None
                and datetime.now(timezone.utc).timestamp() - last_check_in < DISPLAY_RECENT_S,
                "battery_v": device.get("battery_v"),
            },
            "volume": volume,
            "disk_free_gb": round(free / 1e9, 1),
            "can_restart": bool(settings.app_service) and sys.platform == "darwin",
        }

    watch = Watch(notifier, health, activity)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(watch.run())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await link.aclose()
            await http.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if not settings.allowed_logins:
        logger.error("ADMIN_ALLOWED_LOGINS is empty: every request will be refused")

    @app.middleware("http")
    async def _guard(request: Request, call_next: Any) -> Response:
        refused = refusal(request, settings.allowed_logins)
        if refused is not None:
            logger.warning("Admin request refused path=%s", request.url.path)
            return refused
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-cache"  # an update reaches the installed app at once
        return response

    app.mount("/static/admin", StaticFiles(directory=STATIC_DIR), name="admin-static")

    @app.get("/")
    async def _index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=_NO_STORE)

    @app.get("/manifest.webmanifest")
    async def _manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    async def _service_worker() -> FileResponse:
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers=_NO_STORE)

    @app.get("/api/me")
    async def _me(request: Request) -> JSONResponse:
        return JSONResponse({"login": _who(request), "name": request.headers.get(NAME_HEADER, "")})

    # People ---------------------------------------------------------------------------------------

    async def people_payload() -> JSONResponse:
        enrolled = await faces()
        names = None if enrolled is None else {str(f.get("id")): str(f.get("name")) for f in enrolled}
        try:
            people, versions = await asyncio.to_thread(people_files.load, names)
        except RecordError as error:
            return _error(str(error), 503)
        return JSONResponse(
            {"people": [p.as_json() for p in people], "versions": versions, "shared": shared_spoken_names(people),
             "faces": enrolled}
        )  # fmt: skip

    @app.get("/api/people")
    async def _people() -> JSONResponse:
        return await people_payload()

    @app.put("/api/people")
    async def _save_people(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        raw_people, versions = payload.get("people"), payload.get("versions")
        if not isinstance(raw_people, list) or not isinstance(versions, dict):
            return _error("people and versions are required", 400)
        try:
            people = [person_from_json(raw) for raw in raw_people]
            validate(people)
            await asyncio.to_thread(people_files.save, people, {k: str(v) for k, v in versions.items()})
        except PersonError as error:
            return _error(str(error), 422)
        except StaleVersion:
            return _error("Someone else changed people meanwhile. Reload and try again.", 409)
        except RecordError as error:
            return _error(str(error), 503)
        audit.append({"who": _who(request), "action": "people saved", "target": f"{len(people)} people"})
        return await people_payload()

    @app.get("/api/groups")
    async def _groups() -> Response:
        if whatsapp is None:
            return _error("Reachy's WhatsApp is not set up.", 404)
        try:
            groups = await whatsapp.groups()
        except (httpx.HTTPError, ValueError, KeyError):
            return _error("Reachy's WhatsApp did not answer.", 503)
        server = await whatsapp.status()
        number = f"+{server.number}" if server is not None and server.number.isdigit() else ""
        return JSONResponse(
            {"groups": [{"id": chat, "name": name or "Unnamed group"} for name, chat in groups], "number": number}
        )

    @app.post("/api/groups")
    async def _create_group(request: Request) -> Response:
        if whatsapp is None:
            return _error("Reachy's WhatsApp is not set up.", 404)
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        title, phones = payload.get("title"), payload.get("phones")
        if not isinstance(title, str) or not 0 < len(title.strip()) <= MAX_GROUP_TITLE_CHARS or "\n" in title:
            return _error(f"The group name must be 1 to {MAX_GROUP_TITLE_CHARS} characters.", 400)
        if not isinstance(phones, list) or not 0 < len(phones) <= MAX_GROUP_PHONES:
            return _error("Pick who is in the group.", 400)
        digits = [re.sub(r"\D", "", phone) for phone in phones if isinstance(phone, str)]
        if len(digits) != len(phones) or not all(7 <= len(number) <= 15 for number in digits):
            return _error("Every phone number needs its country code.", 400)
        try:
            group = await whatsapp.create_group(title.strip(), digits)
        except httpx.HTTPError:
            group = None
        if group is None:
            return _error("WhatsApp did not create the group.", 502)
        audit.append({"who": _who(request), "action": "group created", "target": title.strip()})
        return JSONResponse({"id": group, "name": title.strip()})

    # Faces (through the running app, which owns the face store) ---------------------------------

    @app.get("/api/faces/photo")
    async def _face_photo(person_id: str, photo: str) -> Response:
        try:
            response = await link.request("GET", "/api/faces/photo", params={"person_id": person_id, "photo": photo})
        except AppUnavailable:
            return _error("Reachy is not running.", 503)
        if response.status_code != 200:
            return Response(status_code=response.status_code, headers=_NO_STORE)
        return Response(content=response.content, media_type="image/jpeg", headers=_NO_STORE)

    async def face_change(request: Request, path: str, action: str, max_bytes: int = MAX_BODY_BYTES) -> Response:
        payload = await _json_body(request, max_bytes)
        if isinstance(payload, Response):
            return payload
        try:
            response = await link.request("POST", path, json=payload)
        except AppUnavailable:
            return _error("Reachy is not running.", 503)
        if response.status_code == 200:
            target = str(payload.get("name") or payload.get("person_id") or "")
            audit.append({"who": _who(request), "action": action, "target": target})
        return _relay(response)

    @app.post("/api/faces/upload")
    async def _face_upload(request: Request) -> Response:
        return await face_change(request, "/api/faces/upload", "face photos added", MAX_PHOTOS_BYTES)

    @app.post("/api/faces/check")
    async def _face_check(request: Request) -> Response:
        payload = await _json_body(request, MAX_PHOTOS_BYTES)
        if isinstance(payload, Response):
            return payload
        try:
            return _relay(await link.request("POST", "/api/faces/check", json=payload))
        except AppUnavailable:
            return _error("Reachy is not running.", 503)

    @app.post("/api/faces/enroll")
    async def _face_enroll(request: Request) -> Response:
        return await face_change(request, "/api/faces/enroll", "face enrolled from the camera")

    @app.post("/api/faces/forget-photo")
    async def _face_forget_photo(request: Request) -> Response:
        return await face_change(request, "/api/faces/forget-photo", "face photo deleted")

    @app.post("/api/faces/forget")
    async def _face_forget(request: Request) -> Response:
        return await face_change(request, "/api/faces/forget", "face forgotten")

    # Status and controls ---------------------------------------------------------------------------

    @app.get("/api/status")
    async def _status() -> JSONResponse:
        return JSONResponse(await health())

    async def control(request: Request, path: str, action: str, body: dict[str, Any] | None = None) -> Response:
        try:
            response = await link.request("POST", path, json=body)
        except AppUnavailable:
            return _error("Reachy is not running.", 503)
        if response.status_code == 200 and action:
            audit.append({"who": _who(request), "action": action})
        return _relay(response)

    @app.post("/api/privacy")
    async def _privacy(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        if not isinstance(payload.get("on"), bool):
            return _error("on must be true or false", 400)
        on = bool(payload["on"])
        return await control(request, "/privacy", "privacy on" if on else "privacy off", {"on": on})

    @app.post("/api/sleep")
    async def _sleep(request: Request) -> Response:
        return await control(request, "/sleep", "put to sleep")

    @app.post("/api/wake")
    async def _wake(request: Request) -> Response:
        return await control(request, "/wake", "woken up")

    @app.post("/api/head")
    async def _head(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        return await control(request, "/head", "", {"direction": payload.get("direction")})

    @app.post("/api/game")
    async def _game(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        action, game = payload.get("action"), payload.get("game")
        body = {"action": action, "game": game, "language": payload.get("language")}
        return await control(request, "/game", f"game {action}" + (f" {game}" if action == "start" else ""), body)

    @app.post("/api/look-up")
    async def _look_up(request: Request) -> Response:
        return await control(request, "/look-up", "")

    @app.get("/api/camera")
    async def _camera(request: Request) -> Response:
        try:
            upstream = await link.open_stream("/camera")
        except AppUnavailable:
            return _error("Reachy is not running.", 503)
        if upstream.status_code != 200:
            await upstream.aread()
            await upstream.aclose()
            return _relay(upstream)
        logger.info("Camera view opened by %s", _who(request))
        return StreamingResponse(
            upstream.aiter_raw(),
            media_type=upstream.headers.get("content-type"),
            headers=_NO_STORE,
            background=BackgroundTask(upstream.aclose),
        )

    @app.post("/api/volume")
    async def _volume(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        level = payload.get("level")
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
            return _error("level must be 0 to 100", 400)
        try:
            response = await http.post(f"{settings.daemon_url}/api/volume/set", json={"volume": level})
        except httpx.HTTPError:
            return _error("Reachy's daemon is not running.", 503)
        if response.status_code != 200:
            return _error("The volume was not changed.", 502)
        audit.append({"who": _who(request), "action": "volume set", "target": str(level)})
        return JSONResponse({"volume": level})

    @app.post("/api/restart")
    async def _restart(request: Request) -> Response:
        if not settings.app_service or sys.platform != "darwin":
            return _error("Restarting is not set up (ADMIN_APP_SERVICE).", 404)
        if restarts:
            return _error("Reachy is already restarting.", 409)
        restarted_at.append(time.monotonic())
        task = asyncio.create_task(restart_app(settings.app_service))
        restarts.add(task)
        task.add_done_callback(restarts.discard)
        audit.append({"who": _who(request), "action": "restarted Reachy"})
        return JSONResponse({"restarted": True})

    # Activity ---------------------------------------------------------------------------------------

    @app.get("/api/activity")
    async def _activity() -> JSONResponse:
        summary = await asyncio.to_thread(get_ledger().summary)
        return JSONResponse(
            {
                "activity": activity.recent(100),
                "changes": audit.recent(50),
                "usage": {"totals": summary.get("totals"), "by_feature": summary.get("by_feature")},
            }
        )

    @app.get("/api/costs")
    async def _costs(days: int = 7) -> Response:
        if days not in PERIODS:
            return _error(f"days must be one of {', '.join(map(str, PERIODS))}", 400)
        entries = await asyncio.to_thread(get_ledger().entries)
        return JSONResponse(breakdown(entries, datetime.now().astimezone(), days))

    # Memory ----------------------------------------------------------------------------------------

    @app.get("/api/memory")
    async def _memory() -> Response:
        try:
            return _relay(await link.request("GET", "/memory"))
        except AppUnavailable:
            return _error("Reachy is not running, so his memory can't be read.", 503)

    async def memory_change(request: Request, path: str, action: str) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        try:
            response = await link.request("POST", path, json=payload)
        except AppUnavailable:
            return _error("Reachy is not running.", 503)
        if response.status_code == 200:
            answer = response.json()
            subject = (answer.get("entry") or {}).get("subject") if "entry" in answer else answer.get("subject")
            audit.append({"who": _who(request), "action": action, "target": str(subject or "")})
        return _relay(response)

    @app.post("/api/memory/update")
    async def _memory_update(request: Request) -> Response:
        return await memory_change(request, "/memory/update", "memory corrected")

    @app.post("/api/memory/forget")
    async def _memory_forget(request: Request) -> Response:
        return await memory_change(request, "/memory/forget", "memory forgotten")

    # Personality -------------------------------------------------------------------------------------

    @app.get("/api/personality")
    async def _personality() -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(read_personality))
        except RecordError as error:
            return _error(str(error), 503)

    @app.put("/api/personality")
    async def _save_personality(request: Request) -> Response:
        payload = await _json_body(request, MAX_PERSONALITY_BYTES)
        if isinstance(payload, Response):
            return payload
        text, version = payload.get("text"), payload.get("version")
        if not isinstance(text, str) or not isinstance(version, str):
            return _error("text and version are required", 400)
        try:
            saved = await asyncio.to_thread(write_personality, text, version)
        except StaleVersion:
            return _error("Someone else changed the personality meanwhile. Reload and try again.", 409)
        except ValueError as error:
            return _error(str(error).capitalize() + ".", 422)
        except (RecordError, OSError):
            return _error("The personality could not be saved.", 503)
        action = "personality changed" if saved["edited"] else "personality reset"
        audit.append({"who": _who(request), "action": action, "target": saved["profile"]})
        return JSONResponse(saved)

    # Settings ---------------------------------------------------------------------------------------

    @app.get("/api/settings")
    async def _settings() -> JSONResponse:
        values = await asyncio.to_thread(read_settings, settings.env_file)
        return JSONResponse(
            {
                "settings": [
                    {"key": key, "label": s.label, "kind": s.kind, "choices": list(s.choices), "low": s.low,
                     "high": s.high, "default": s.default, "value": values[key]}
                    for key, s in SETTINGS.items()
                ]
            }
        )  # fmt: skip

    @app.put("/api/settings")
    async def _save_settings(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        changes = payload.get("changes")
        if not isinstance(changes, dict) or not all(isinstance(v, str) for v in changes.values()):
            return _error("changes must map settings to text", 400)
        try:
            await asyncio.to_thread(write_settings, settings.env_file, changes)
        except SettingError as error:
            return _error(str(error), 422)
        except OSError:
            return _error("The settings file cannot be written.", 503)
        audit.append({"who": _who(request), "action": "settings changed", "target": ", ".join(sorted(changes))})
        return await _settings()

    # Gallery ---------------------------------------------------------------------------------------

    @app.get("/api/gallery")
    async def _gallery() -> JSONResponse:
        return JSONResponse({"items": await asyncio.to_thread(gallery.items)})

    @app.get("/api/gallery/file")
    async def _gallery_file(id: str, thumb: bool = False) -> Response:
        try:
            if thumb:
                return Response(await asyncio.to_thread(gallery.thumbnail, id), media_type="image/jpeg")
            path = gallery.path(id)
        except (GalleryError, OSError, ValueError):
            return Response(status_code=404)
        return FileResponse(path, media_type="image/jpeg" if path.suffix == ".jpg" else "image/png")

    @app.post("/api/gallery/delete")
    async def _gallery_delete(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        item_id = str(payload.get("id") or "")
        try:
            await asyncio.to_thread(gallery.delete, item_id)
        except GalleryError:
            return _error("That picture is gone.", 404)
        except OSError:
            return _error("The picture could not be deleted.", 503)
        audit.append({"who": _who(request), "action": "picture deleted", "target": item_id.split("/", 1)[0]})
        return JSONResponse({"deleted": True})

    # Notifications ---------------------------------------------------------------------------------

    @app.get("/api/push/key")
    async def _push_key() -> JSONResponse:
        return JSONResponse({"key": notifier.public_key})

    @app.post("/api/push/subscribe")
    async def _push_subscribe(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        subject = request.headers.get("origin", "")
        try:
            await notifier.subscribe(payload, _who(request), subject)
        except ValueError as error:
            return _error(str(error), 400)
        return JSONResponse({"subscribed": True})

    @app.post("/api/push/unsubscribe")
    async def _push_unsubscribe(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        await notifier.unsubscribe(str(payload.get("endpoint") or ""))
        return JSONResponse({"subscribed": False})

    @app.post("/api/push/test")
    async def _push_test(request: Request) -> Response:
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        delivered = await notifier.notify("Reachy", "Notifications work.")
        return JSONResponse({"delivered": delivered, "gone": str(payload.get("endpoint") or "") in notifier.last_gone})

    return app


def main() -> None:
    """Serve the admin app on 127.0.0.1:ADMIN_PORT."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a status check every minute is not news
    port = int(os.getenv("ADMIN_PORT") or 7870)
    settings = AdminSettings.from_env()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
