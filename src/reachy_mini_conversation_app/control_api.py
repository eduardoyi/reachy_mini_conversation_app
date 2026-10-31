"""The app's control API for the phone admin app (admin/): loopback only, behind a token both processes share.

It turns privacy on and off, streams the camera as MJPEG, turns the head (lifting it while he sleeps, without
waking him), puts him to sleep or wakes him, manages enrolled faces, marks a call he missed and records someone
calling him for the wake word to learn their voice.
"""

import io
import json
import asyncio
import logging
import secrets
import ipaddress
import threading
from typing import Final
from pathlib import Path
from collections.abc import AsyncIterator

import uvicorn
from PIL import Image
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from reachy_mini_conversation_app.privacy import PRIVACY, quiet_now
from reachy_mini_conversation_app.face_routes import register_face_routes
from reachy_mini_conversation_app.camera_frames import capture_jpeg
from reachy_mini_conversation_app.memory_routes import register_memory_routes
from reachy_mini_conversation_app.hybrid_runtime import HybridRuntime
from reachy_mini_conversation_app.tools.move_head import MoveHead
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.wake_learning.store import WakeLearningStore
from reachy_mini_conversation_app.wake_learning.takes import MAX_VOICE_S, level_db, voiced_span


logger = logging.getLogger(__name__)

STREAM_FRAME_S: Final[float] = 0.2
STREAM_MAX_SIDE: Final[int] = 640
STREAM_JPEG_QUALITY: Final[int] = 70
# How long a lifted head stays up while he sleeps, after the last turn.
PEEK_HOLD_S: Final[float] = 120.0
PEEK_YAW_STEP: Final[float] = 20.0
PEEK_PITCH_STEP: Final[float] = 15.0
_BOUNDARY: Final[str] = "frame"
_NO_STORE: Final[dict[str, str]] = {"Cache-Control": "no-store"}


def load_or_create_token(path: Path) -> str:
    """Return the shared control token in ``path``, creating it (owner-only) on first use."""
    try:
        token = path.read_text().strip()
    except FileNotFoundError:
        token = ""
    if token:
        return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(token)
    temporary.chmod(0o600)
    temporary.replace(path)
    return token


def _scaled_frame(jpeg: bytes) -> bytes:
    with Image.open(io.BytesIO(jpeg)) as image:
        image.thumbnail((STREAM_MAX_SIDE, STREAM_MAX_SIDE))
        scaled = io.BytesIO()
        image.convert("RGB").save(scaled, "JPEG", quality=STREAM_JPEG_QUALITY)
    return scaled.getvalue()


class ControlApi:
    """The routes the admin app drives, built over the running app's parts."""

    def __init__(
        self,
        deps: ToolDependencies,
        runtime: HybridRuntime | None,
        token: str,
        wake_store: WakeLearningStore | None = None,
    ) -> None:
        """Serve ``deps``' robot, movement, faces and call service, the hybrid ``runtime`` and wake learning."""
        self._deps = deps
        self._runtime = runtime
        self._wake_store = wake_store
        self._token = token
        self._peek = (0.0, 0.0)
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self._add_routes()
        register_face_routes(self.app, deps.face_recognition, self._denied)
        register_memory_routes(self.app, deps.instance_path, self._denied)

    def _denied(self, request: Request, *, mutation: bool = False) -> Response | None:
        """Refuse anything not from this computer with the shared token."""
        client_host = request.client.host if request.client is not None else ""
        try:
            local = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            local = False
        offered = request.headers.get("authorization", "").encode("utf-8", "surrogateescape")
        if not local or not secrets.compare_digest(offered, f"Bearer {self._token}".encode()):
            return Response(status_code=403, headers=_NO_STORE)
        return None

    async def _json_body(self, request: Request) -> dict[str, object] | Response:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return Response(status_code=415, headers=_NO_STORE)
        body = await request.body()
        if len(body) > 1024:
            return Response(status_code=413, headers=_NO_STORE)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400, headers=_NO_STORE)
        return payload if isinstance(payload, dict) else Response(status_code=400, headers=_NO_STORE)

    def status(self) -> dict[str, object]:
        """Return what the admin app's status page shows about the running app."""
        movement = self._deps.movement_manager
        return {
            "mode": self._runtime.mode.value if self._runtime is not None else "conversation",
            "sleeping": movement.sleeping,
            "privacy": {"on": PRIVACY.on, "since": PRIVACY.since},
            "quiet_hours": quiet_now(),
            "calls_linked": self._deps.calls.linked if self._deps.calls is not None else None,
            # A call ringing or under way: the admin app waits for it to end before restarting him.
            "in_call": bool(getattr(self._deps.calls, "busy", False)),
            "faces": self._deps.face_recognition is not None,
            "camera": self._deps.camera_enabled,
            "game": games.status() if (games := getattr(self._deps, "games", None)) is not None else None,
        }

    async def game_action(self, payload: dict[str, object]) -> tuple[str, int] | None:
        """Start, pause, resume or end a game from the phone; return (why not, status code) when refused.

        A game started here needs no spoken yes: the tap is the yes. He wakes up for it.
        """
        host = getattr(self._deps, "games", None)
        loop = self._runtime.loop if self._runtime is not None else None
        if host is None or loop is None:
            return "Games are not available.", 404
        action = payload.get("action")
        if action not in {"start", "pause", "resume", "end"}:
            return "action must be start, pause, resume or end.", 400
        if action == "start" and PRIVACY.on:
            return "Privacy is on.", 409
        game = str(payload.get("game") or "")
        language = "en" if payload.get("language") == "en" else "es"

        def act() -> str | None:
            if action == "start":
                if self._deps.movement_manager.sleeping:
                    self._deps.movement_manager.wake_up()
                    if self._runtime is not None:
                        self._runtime.set_asleep(False)
                problem: str | None = host.start(game, language)
                return problem
            if action == "pause":
                host.pause()
            elif action == "resume":
                host.resume(say=True)
            else:
                host.end("admin app")
            return None

        async def on_runtime_loop() -> str | None:
            return act()

        problem = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(on_runtime_loop(), loop))
        return (problem, 400) if problem is not None else None

    def go_to_sleep(self) -> None:
        """Sleep pose, any conversation closed after its reply; his name or an antenna wakes him."""
        self._deps.movement_manager.go_to_sleep()
        if self._runtime is not None:
            self._runtime.request_close("go_to_sleep")
            self._runtime.set_asleep(True)

    def peek_toward(self, direction: str) -> None:
        """While he sleeps, lift the head and turn it a step toward ``direction`` ("center" looks ahead)."""
        yaw, pitch = self._peek
        yaw += {"left": PEEK_YAW_STEP, "right": -PEEK_YAW_STEP}.get(direction, 0.0)
        pitch += {"up": -PEEK_PITCH_STEP, "down": PEEK_PITCH_STEP}.get(direction, 0.0)
        if direction == "center":
            yaw, pitch = 0.0, 0.0
        self._peek = (max(-60.0, min(60.0, yaw)), max(-30.0, min(30.0, pitch)))
        self._deps.movement_manager.peek(*self._peek, PEEK_HOLD_S)

    def _add_routes(self) -> None:
        app = self.app

        @app.get("/status")
        async def _status(request: Request) -> Response:
            denied = self._denied(request)
            return denied or JSONResponse(self.status(), headers=_NO_STORE)

        @app.post("/privacy")
        async def _privacy(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            payload = await self._json_body(request)
            if isinstance(payload, Response):
                return payload
            if not isinstance(payload.get("on"), bool):
                return Response(status_code=400, headers=_NO_STORE)
            await asyncio.to_thread(PRIVACY.set, bool(payload["on"]))
            return JSONResponse(self.status(), headers=_NO_STORE)

        @app.post("/sleep")
        async def _sleep(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            await asyncio.to_thread(self.go_to_sleep)
            return JSONResponse(self.status(), headers=_NO_STORE)

        @app.post("/wake")
        async def _wake(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            if PRIVACY.on:
                return JSONResponse({"error": "Privacy is on."}, status_code=409, headers=_NO_STORE)
            self._deps.movement_manager.wake_up()
            if self._runtime is not None:
                self._runtime.set_asleep(False)
            return JSONResponse(self.status(), headers=_NO_STORE)

        @app.post("/head")
        async def _head(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            payload = await self._json_body(request)
            if isinstance(payload, Response):
                return payload
            direction = payload.get("direction")
            if direction not in {"left", "right", "up", "down", "center"}:
                return Response(status_code=400, headers=_NO_STORE)
            if self._deps.movement_manager.sleeping:
                self.peek_toward(str(direction))
                return JSONResponse({"status": "peeking"}, headers=_NO_STORE)
            result = await MoveHead()(self._deps, direction="front" if direction == "center" else direction)
            status_code = 503 if "error" in result else 200
            return JSONResponse(result, status_code=status_code, headers=_NO_STORE)

        @app.post("/game")
        async def _game(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            payload = await self._json_body(request)
            if isinstance(payload, Response):
                return payload
            problem = await self.game_action(payload)
            if problem is not None:
                message, status_code = problem
                return JSONResponse({"error": message}, status_code=status_code, headers=_NO_STORE)
            return JSONResponse(self.status(), headers=_NO_STORE)

        @app.post("/look-up")
        async def _look_up(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            if not self._deps.movement_manager.sleeping:
                return JSONResponse({"error": "He is awake."}, status_code=409, headers=_NO_STORE)
            self.peek_toward("center")
            return JSONResponse({"status": "peeking"}, headers=_NO_STORE)

        @app.post("/wake/missed")
        async def _missed(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            payload = await self._json_body(request)
            if isinstance(payload, Response):
                return payload
            who = payload.get("who", "")
            if not isinstance(who, str) or len(who) > 200:
                return Response(status_code=400, headers=_NO_STORE)
            if PRIVACY.on or quiet_now() or self._runtime is None or not self._runtime.mark_missed(by="app", who=who):
                return JSONResponse({"error": "He isn't listening now."}, status_code=409, headers=_NO_STORE)
            return JSONResponse({"ok": True}, headers=_NO_STORE)

        @app.post("/wake/take")
        async def _take(request: Request) -> Response:
            denied = self._denied(request, mutation=True)
            if denied is not None:
                return denied
            payload = await self._json_body(request)
            if isinstance(payload, Response):
                return payload
            person, prompt = payload.get("person"), payload.get("prompt", "")
            if not isinstance(person, str) or not person.strip() or len(person) > 80:
                return Response(status_code=400, headers=_NO_STORE)
            if not isinstance(prompt, str) or len(prompt) > 200:
                return Response(status_code=400, headers=_NO_STORE)
            store, runtime = self._wake_store, self._runtime
            take = None
            if store is not None and runtime is not None and not PRIVACY.on and not quiet_now():
                take = await runtime.record_name()
            if take is None or store is None:
                return JSONResponse({"error": "He can't record now."}, status_code=409, headers=_NO_STORE)
            rate, audio = take
            span = voiced_span(audio, rate)
            if span is None:
                return JSONResponse(
                    {"ok": False, "reason": "quiet", "seconds": 0.0, "level_db": None}, headers=_NO_STORE
                )
            voice = audio[span[0] : span[1]]
            seconds = voice.size / rate
            loudness = round(level_db(voice), 1)
            if seconds > MAX_VOICE_S + 1.0:
                return JSONResponse(
                    {"ok": False, "reason": "too_long", "seconds": round(seconds, 2), "level_db": loudness},
                    headers=_NO_STORE,
                )
            await asyncio.to_thread(store.save_recording, person.strip(), prompt, rate, voice.astype("<i2").tobytes())
            return JSONResponse(
                {"ok": True, "reason": None, "seconds": round(seconds, 2), "level_db": loudness}, headers=_NO_STORE
            )

        @app.get("/camera")
        async def _camera(request: Request) -> Response:
            denied = self._denied(request)
            if denied is not None:
                return denied
            if PRIVACY.on:
                return JSONResponse({"error": "Privacy is on."}, status_code=409, headers=_NO_STORE)
            if not self._deps.camera_enabled:
                return JSONResponse({"error": "The camera is off."}, status_code=404, headers=_NO_STORE)
            return StreamingResponse(
                self._frames(request),
                media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
                headers=_NO_STORE,
            )

    async def _frames(self, request: Request) -> AsyncIterator[bytes]:
        media = self._deps.reachy_mini.media
        logger.info("Camera view opened")
        try:
            while not PRIVACY.on and not await request.is_disconnected():
                jpeg = await asyncio.to_thread(capture_jpeg, media)
                if jpeg:
                    try:
                        frame = await asyncio.to_thread(_scaled_frame, jpeg)
                    except OSError as error:
                        logger.debug("Camera frame unreadable: %s", error)
                    else:
                        yield (
                            f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                            + frame
                            + b"\r\n"
                        )
                await asyncio.sleep(STREAM_FRAME_S)
        finally:
            logger.info("Camera view closed")


def start_control_server(api: ControlApi, port: int) -> uvicorn.Server:
    """Serve ``api`` on 127.0.0.1:``port`` in a background thread; returns the server to stop at exit."""
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="control-api").start()
    logger.info("Control API on 127.0.0.1:%d", port)
    return server
