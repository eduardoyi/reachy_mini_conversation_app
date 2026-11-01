"""The phones' side of Big Brain: a small web server on the home Wi-Fi while a game runs.

A phone opens ``http://<the Mac mini on the Wi-Fi>:<port>/?c=<code>`` (the QR code on the TRMNL), gets the page
``static/big_brain/index.html`` and keeps one WebSocket open. Every request needs the game's four-letter code,
and the server runs only during a game, so the link dies with it; it has none of the admin app's powers. Joining
(a name and a team) is kept here; everything else a phone sends goes to the game, and after every change each
phone gets the game's state as it should see it (``snapshot``). Buzzes are ordered by when they reach the Mac
mini, which is fair on one Wi-Fi.
"""

import uuid
import random
import socket
import string
import asyncio
import logging
import contextlib
from typing import Any, Final, Callable, Iterator
from pathlib import Path
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse


logger = logging.getLogger(__name__)

PAGE: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "big_brain" / "index.html"
TEAMS: Final[tuple[str, ...]] = ("red", "blue", "green", "yellow")
NAME_MAX: Final[int] = 16
GONE: Final[str] = "<!doctype html><meta name=viewport content='width=device-width'><h2 style='font-family:sans-serif'>Este juego ya terminó · This game is over</h2>"  # fmt: skip


@dataclass
class Player:
    """Someone playing on a phone."""

    id: str
    name: str
    team: str
    joined_at: float


def join_code() -> str:
    """Return a four-letter code easy to read off a screen (no I, O or Q)."""
    letters = [letter for letter in string.ascii_uppercase if letter not in "IOQ"]
    return "".join(random.choice(letters) for _ in range(4))


def wifi_address() -> str:
    """Return this computer's address on the local network (no packet is sent)."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as probe:
        try:
            probe.connect(("192.168.0.1", 9))
            return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"


class _Server(uvicorn.Server):
    """A uvicorn server that leaves the app's signal handling alone (it runs inside Reachy's event loop)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class Phones:
    """The players and their phones for one game."""

    def __init__(
        self,
        port: int,
        snapshot: Callable[[Player | None], dict[str, Any]],
        on_message: Callable[[Player, dict[str, Any]], None],
        may_join: Callable[[], bool],
        *,
        code: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Serve the page on ``port``; ``snapshot`` gives each phone its state, ``on_message`` gets the rest."""
        self.port = port
        self.code = code or join_code()
        self.players: dict[str, Player] = {}
        self._snapshot = snapshot
        self._on_message = on_message
        self._may_join = may_join
        self._clock = clock or asyncio.get_event_loop().time
        self._sockets: dict[WebSocket, str | None] = {}
        self._server: _Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._push: asyncio.Task[None] | None = None
        self.app = self._make_app()

    def url(self, host: str | None = None) -> str:
        """Return the address phones open (the QR code)."""
        return f"http://{host or wifi_address()}:{self.port}/?c={self.code}"

    def team_players(self, team: str) -> list[Player]:
        """Return a team's players in the order they joined."""
        return sorted((p for p in self.players.values() if p.team == team), key=lambda p: p.joined_at)

    @property
    def connected(self) -> int:
        """How many players have a phone connected now."""
        return len({player for player in self._sockets.values() if player is not None})

    # Serving -------------------------------------------------------------------------------------

    async def start(self) -> None:
        """Start serving on every address of this computer."""
        config = uvicorn.Config(self.app, host="0.0.0.0", port=self.port, log_level="warning", ws_ping_interval=10)
        self._server = _Server(config)
        self._task = asyncio.get_running_loop().create_task(self._server.serve(), name="big-brain-phones")
        logger.info("Big Brain phones on port %d", self.port)

    async def stop(self) -> None:
        """Close every phone and stop serving."""
        for websocket in list(self._sockets):
            with contextlib.suppress(Exception):
                await websocket.close(code=1001)
        self._sockets.clear()
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 5)
        self._server = self._task = None

    def changed(self) -> None:
        """Send every phone its state soon (changes in the same moment go out together)."""
        if self._push is None or self._push.done():
            self._push = asyncio.get_running_loop().create_task(self._send_all(), name="big-brain-push")

    async def _send_all(self) -> None:
        await asyncio.sleep(0)
        for websocket, player_id in list(self._sockets.items()):
            await self._send_state(websocket, self.players.get(player_id) if player_id else None)

    async def _send_state(self, websocket: WebSocket, player: Player | None) -> None:
        try:
            await websocket.send_json({"type": "state", **self._snapshot(player)})
        except Exception:
            self._sockets.pop(websocket, None)

    def _make_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.get("/")
        async def page(c: str = "") -> Any:
            if c.upper() != self.code or not PAGE.exists():
                return HTMLResponse(GONE, status_code=404)
            return FileResponse(PAGE, headers={"Cache-Control": "no-store"})

        @app.websocket("/ws")
        async def phone(websocket: WebSocket, code: str = "") -> None:
            if code.upper() != self.code:
                await websocket.close(code=4403)
                return
            await websocket.accept()
            self._sockets[websocket] = None
            await self._send_state(websocket, None)
            try:
                while True:
                    message = await websocket.receive_json()
                    if isinstance(message, dict):
                        await self._handle(websocket, message)
            except (WebSocketDisconnect, RuntimeError, ValueError):
                pass
            finally:
                self._sockets.pop(websocket, None)
                self.changed()

        return app

    async def _handle(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        kind = message.get("type")
        player_id = self._sockets.get(websocket)
        if kind == "join":
            known = self.players.get(str(message.get("player") or ""))
            if known is not None:
                self._sockets[websocket] = known.id
                await websocket.send_json({"type": "welcome", "player": known.id})
                self.changed()
                return
            if message.get("name") is None:  # a stored id from another game: show the form
                await websocket.send_json({"type": "forget"})
                await self._send_state(websocket, None)
                return
            name = " ".join(str(message.get("name") or "").split())[:NAME_MAX]
            team = str(message.get("team") or "")
            if not self._may_join():
                await websocket.send_json({"type": "error", "message": "closed"})
                return
            if not name or team not in TEAMS:
                await websocket.send_json({"type": "error", "message": "name"})
                return
            player = Player(id=uuid.uuid4().hex[:12], name=name, team=team, joined_at=self._clock())
            self.players[player.id] = player
            self._sockets[websocket] = player.id
            logger.info("Big Brain: a player joined team %s (%d players)", team, len(self.players))
            await websocket.send_json({"type": "welcome", "player": player.id})
            self._on_message(player, {"type": "joined"})
            self.changed()
            return
        sender = self.players.get(player_id) if player_id else None
        if sender is None:
            await websocket.send_json({"type": "error", "message": "join"})
            return
        self._on_message(sender, message)
