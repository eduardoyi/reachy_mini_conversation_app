"""The TRMNL device server, run as its own process so Reachy can restart without the screen noticing.

The TRMNL firmware sleeps 5 minutes after any failed check-in, and a restarting app is down for about
20 s, so the server outlives the app. The app hands it screens through a spool folder: one PNG per
screen and ``current.json`` naming the screen and the resting card. The server writes ``device.json``
with its check-in times (so the app knows whether the screen is awake) and ``server.json`` with its pid.

Standard library only, so a replacement starts in a fraction of a second. A new server stops the old
one itself and binds the port as soon as it is free, so a takeover is not seen as a failed check-in.

    python -m reachy_mini_conversation_app.eink_server --spool DIR --port 8090 --refresh 1
    python -m reachy_mini_conversation_app.eink_server --stop
"""

import os
import json
import time
import signal
import hashlib
import logging
import argparse
import threading
from typing import Any, Callable
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


logger = logging.getLogger(__name__)

IDLE_AFTER_S = 30 * 60
# A check-in waits this long for a new screen before answering; the firmware gives up after 15 s.
CHECK_IN_HOLD_S = 10.0
# Between check-ins the TRMNL draws, naps for the refresh rate and rejoins Wi-Fi, well within this. A longer
# silence means it is asleep: one failed check-in sends it to sleep for 5 minutes.
AWAKE_GAP_S = 30.0
POLL_S = 0.2
BIND_TIMEOUT_S = 5.0
DEFAULT_SPOOL = Path.home() / ".cache" / "reachy-mini-conversation-app" / "display" / "screen"
CURRENT, DEVICE, SERVER = "current.json", "device.json", "server.json"


def fingerprint(port: int, refresh_s: int) -> str:
    """Identify this server's code and settings; the app replaces a running server whose differ."""
    source = Path(__file__).read_bytes()
    return hashlib.sha1(source + f"{port}:{refresh_s}".encode()).hexdigest()[:12]


def read_json(path: Path) -> dict[str, Any]:
    """Return a spool file's contents, or {} when it is missing or half-written."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Replace a spool file in one step, so a reader never sees half of it."""
    partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    partial.write_text(json.dumps(data))
    os.replace(partial, path)


def current_filename(spool: Path, now: float) -> str | None:
    """Return the screen the TRMNL should show now: the last one shown, or the resting card once it is old."""
    current = read_json(spool / CURRENT)
    screen, shown_at = current.get("screen"), current.get("shown_at", 0.0)
    if isinstance(screen, str) and isinstance(shown_at, (int, float)) and now - shown_at < IDLE_AFTER_S:
        return screen
    home = current.get("home")
    return home if isinstance(home, str) else None


def is_awake(spool: Path, now: float, refresh_s: int) -> bool:
    """Whether the TRMNL is checking in, so a new screen reaches it within seconds."""
    device = read_json(spool / DEVICE)
    started, ended = device.get("started"), device.get("ended")
    if isinstance(started, (int, float)) and (not isinstance(ended, (int, float)) or started > ended):
        return now - started <= CHECK_IN_HOLD_S + 5  # a held check-in, unless that server died mid-hold
    return isinstance(ended, (int, float)) and now - ended <= AWAKE_GAP_S + refresh_s


class DeviceServer(ThreadingHTTPServer):
    """Answers the TRMNL from the spool folder."""

    daemon_threads = True
    block_on_close = False  # a held check-in must not delay shutdown

    def __init__(
        self,
        spool: Path,
        port: int,
        refresh_s: int,
        *,
        host: str = "0.0.0.0",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind ``port``; waits up to BIND_TIMEOUT_S for a server being replaced to let go of it."""
        self.spool = spool
        self.refresh_s = refresh_s
        self.stopping = threading.Event()
        self._clock = clock
        self._lock = threading.Lock()
        self._open = 0
        self._last_sent: str | None = None
        deadline = time.monotonic() + BIND_TIMEOUT_S
        while True:
            try:
                super().__init__((host, port), _Handler)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def screen_for_check_in(self, hold_s: float) -> str | None:
        """Return the screen for a check-in, holding up to ``hold_s`` for one it has not been sent yet."""
        now = self._clock()
        last = read_json(self.spool / DEVICE).get("ended")
        if isinstance(last, (int, float)) and now - last > AWAKE_GAP_S + self.refresh_s:
            logger.info("TRMNL checking in again after %.0f s", now - last)
        self._record_check_in(opening=True)
        try:
            deadline = time.monotonic() + hold_s
            while True:
                filename = current_filename(self.spool, self._clock())
                if filename != self._last_sent or self.stopping.is_set() or time.monotonic() >= deadline:
                    self._last_sent = filename
                    return filename
                self.stopping.wait(min(POLL_S, max(0.0, deadline - time.monotonic())))
        finally:
            self._record_check_in(opening=False)

    def screen_png(self, filename: str) -> bytes | None:
        """Return the PNG of the current or resting screen with this file name, else None."""
        current = read_json(self.spool / CURRENT)
        if filename not in (current.get("screen"), current.get("home")):
            return None
        try:
            return (self.spool / f"{filename}.png").read_bytes()
        except OSError:
            return None

    def _record_check_in(self, *, opening: bool) -> None:
        with self._lock:
            self._open += 1 if opening else -1
            device = read_json(self.spool / DEVICE)
            device["started" if opening else "ended"] = self._clock()
            write_json(self.spool / DEVICE, device)

    def wait_for_check_ins(self, timeout_s: float) -> None:
        """Wait for open check-ins to be answered, so a replacement does not cut one off."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._open <= 0:
                    return
            time.sleep(0.02)


class _Handler(BaseHTTPRequestHandler):
    """The three endpoints the TRMNL firmware calls, plus the current screen's image."""

    server: DeviceServer

    def do_GET(self) -> None:
        server = self.server
        path = self.path.split("?", 1)[0]
        base = f"http://{self.headers.get('Host') or f'127.0.0.1:{server.server_address[1]}'}"
        if path == "/api/setup":
            logger.info(
                "TRMNL connected (model %s, firmware %s)", self.headers.get("Model"), self.headers.get("FW-Version")
            )
            self._send_json(
                {
                    "status": 200,
                    "api_key": "reachy",
                    "friendly_id": "REACHY",
                    "image_url": f"{base}/screens/{current_filename(server.spool, time.time())}.png",
                    "message": "Connected to Reachy",
                }
            )
        elif path == "/api/display":
            logger.debug(
                "TRMNL check-in: battery %s V, signal %s dBm",
                self.headers.get("Battery-Voltage"),
                self.headers.get("RSSI"),
            )
            # Answering as soon as a screen changes saves the device a sleep, a reboot and a Wi-Fi reconnect.
            filename = server.screen_for_check_in(CHECK_IN_HOLD_S)
            self._send_json(
                {
                    "status": 0,
                    "image_url": f"{base}/screens/{filename}.png",
                    "filename": filename,
                    "refresh_rate": server.refresh_s,  # an int: the firmware reads a string as 0
                    "reset_firmware": False,
                    "update_firmware": False,
                    "firmware_url": None,
                    "special_function": "none",
                }
            )
        elif path.startswith("/screens/") and path.endswith(".png"):
            png = server.screen_png(path[len("/screens/") : -len(".png")])
            if png is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/log":
            self.send_error(404)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 64_000)
        logger.info("TRMNL reported: %s", self.rfile.read(length).decode(errors="replace")[:500])
        self.send_response(204)
        self.end_headers()

    def _send_json(self, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug(format, *args)


def running_pid(spool: Path) -> int | None:
    """Return the pid of the server recorded in the spool, if that process is still alive."""
    pid = read_json(spool / SERVER).get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass  # alive, owned by someone else
    return pid


def main(argv: list[str] | None = None) -> None:
    """Replace any running server, then answer the TRMNL until stopped."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spool", type=Path, default=DEFAULT_SPOOL)
    parser.add_argument("--port", type=int)
    parser.add_argument("--refresh", type=int)
    parser.add_argument("--stop", action="store_true", help="stop the running server and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    if args.stop:
        pid = running_pid(args.spool)
        if pid is not None:
            os.kill(pid, signal.SIGTERM)
        print(f"Stopped e-ink display server pid {pid}" if pid else "No e-ink display server is running")
        return
    if args.port is None or args.refresh is None:
        parser.error("--port and --refresh are required")
    args.spool.mkdir(parents=True, exist_ok=True)

    old = running_pid(args.spool)
    if old is not None and old != os.getpid():
        logger.info("Replacing e-ink display server pid %d", old)
        try:
            os.kill(old, signal.SIGTERM)
        except OSError:
            pass
    try:
        server = DeviceServer(args.spool, args.port, args.refresh)
    except OSError as error:
        logger.error("E-ink display server could not start on port %d: %s", args.port, error)
        raise SystemExit(1) from error
    write_json(args.spool / SERVER, {"pid": os.getpid(), "fingerprint": fingerprint(args.port, args.refresh)})
    signal.signal(signal.SIGTERM, lambda *_: server.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: server.stopping.set())
    threading.Thread(target=server.serve_forever, name="eink-server", daemon=True).start()
    logger.info("E-ink display server listening on port %d (pid %d)", args.port, os.getpid())
    server.stopping.wait()
    # Held check-ins answer at once; the port is freed for the replacement before they finish sending.
    server.shutdown()
    server.server_close()
    server.wait_for_check_ins(2.0)
    logger.info("E-ink display server stopped")


if __name__ == "__main__":
    main()
