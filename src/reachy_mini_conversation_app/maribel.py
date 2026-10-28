"""Hand spoken requests to Maribel, the household's Grok Bot assistant, and take back what she says.

Her webhook only accepts a request and starts her in the background; she answers later by POSTing to
``/maribel/reply`` on a loopback server that Tailscale Funnel publishes. Reachy speaks only her ``speak``
text. Longer work she reports in the Grok Bot app, never here.

Logs keep request and conversation ids, reply types and delays; never the texts or the secrets.

    python -m reachy_mini_conversation_app.maribel fake-reply [--request-id ID --conversation-id ID]
"""

import sys
import hmac
import json
import time
import uuid
import asyncio
import logging
import argparse
import threading
from typing import Final, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import httpx


logger = logging.getLogger(__name__)

REPLY_PATH: Final[str] = "/maribel/reply"
# Reachy waits this long for her first reply before saying he passed the request on.
FIRST_REPLY_WAIT_S: Final[float] = 10.0
# A reply this long after its request is still spoken once Reachy is free; later ones are dropped.
LATE_REPLY_S: Final[float] = 120.0
# Her question stays open this long: a "tell Maribel..." meanwhile answers it.
QUESTION_OPEN_S: Final[float] = 120.0
POST_TIMEOUT_S: Final[float] = 5.0
RETRY_PAUSE_S: Final[float] = 2.0
MAX_BODY_BYTES: Final[int] = 16_384
REPLY_TYPES: Final[frozenset[str]] = frozenset({"answer", "accepted", "question", "error"})
HOME_TIMEZONE: Final[str] = "Europe/Lisbon"


@dataclass(frozen=True)
class MaribelReply:
    """One callback from Maribel."""

    request_id: str
    conversation_id: str
    type: str
    speak: str
    question_id: str | None = None


@dataclass
class _Request:
    conversation_id: str
    sent_at: float
    waiter: "asyncio.Future[MaribelReply] | None" = None
    loop: asyncio.AbstractEventLoop | None = None
    answered: bool = False


@dataclass(frozen=True)
class _Question:
    conversation_id: str
    question_id: str
    asked_at: float


class MaribelLink:
    """Send requests to Maribel's webhook and match her callbacks to them; thread-safe."""

    on_late_reply: Callable[[MaribelReply], None] | None = None

    def __init__(
        self,
        webhook_url: str,
        webhook_key: str,
        callback_url: str,
        callback_secret: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        retry_pause_s: float = RETRY_PAUSE_S,
    ) -> None:
        """Talk to ``webhook_url`` with ``webhook_key``; accept callbacks bearing ``callback_secret``."""
        self._webhook_url = webhook_url
        self._webhook_key = webhook_key
        self._callback_url = callback_url
        self._callback_secret = callback_secret.encode()
        self._client = client
        self._clock = clock
        self._retry_pause_s = retry_pause_s
        self._lock = threading.Lock()
        self._requests: dict[str, _Request] = {}
        self._latest_request_id: str | None = None
        self._question: _Question | None = None

    async def send(self, text: str) -> str | None:
        """POST ``text`` to Maribel; return its request id once accepted, or None if she could not be reached."""
        request_id = str(uuid.uuid4())
        now = self._clock()
        with self._lock:
            question = self._question
            if question is not None and now - question.asked_at > QUESTION_OPEN_S:
                question = self._question = None
            conversation_id = question.conversation_id if question is not None else str(uuid.uuid4())
            loop = asyncio.get_running_loop()
            self._forget_old_requests(now)
            self._requests[request_id] = _Request(conversation_id, now, loop.create_future(), loop)
            self._latest_request_id = request_id
        body = {
            "version": 1,
            "request_id": request_id,
            "conversation_id": conversation_id,
            "in_reply_to": question.question_id if question is not None else None,
            "text": text,
            "sent_at": datetime.now(ZoneInfo(HOME_TIMEZONE)).isoformat(timespec="seconds"),
            "callback_url": self._callback_url,
        }
        for attempt in (1, 2):
            if attempt == 2:
                await asyncio.sleep(self._retry_pause_s)
            try:
                response = await self._post(body)
            except httpx.HTTPError as error:
                logger.warning("Maribel webhook attempt %d failed: %s", attempt, type(error).__name__)
                continue
            if response.is_success:
                logger.info(
                    "Maribel request sent request_id=%s conversation_id=%s answers_question=%s attempt=%d",
                    request_id,
                    conversation_id,
                    question is not None,
                    attempt,
                )
                return request_id
            logger.warning("Maribel webhook attempt %d answered HTTP %d", attempt, response.status_code)
        with self._lock:
            self._requests.pop(request_id, None)
        return None

    async def _post(self, body: dict[str, object]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._webhook_key}"}
        if self._client is not None:
            return await self._client.post(self._webhook_url, json=body, headers=headers, timeout=POST_TIMEOUT_S)
        async with httpx.AsyncClient(timeout=POST_TIMEOUT_S) as client:
            return await client.post(self._webhook_url, json=body, headers=headers)

    async def first_reply(self, request_id: str, timeout_s: float = FIRST_REPLY_WAIT_S) -> MaribelReply | None:
        """Wait for the first callback to ``request_id``; None after ``timeout_s`` (a later one is late)."""
        with self._lock:
            request = self._requests.get(request_id)
            waiter = request.waiter if request is not None else None
        if request is None or waiter is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout_s)
        except asyncio.TimeoutError:
            with self._lock:
                request.waiter = None
                if waiter.done():  # it arrived as the wait ended
                    return waiter.result()
            logger.info("Maribel has not replied within %.0f s request_id=%s", timeout_s, request_id)
            return None

    def has_open_question(self) -> bool:
        """Return whether Maribel's last question is still waiting for an answer."""
        with self._lock:
            question = self._question
            return question is not None and self._clock() - question.asked_at <= QUESTION_OPEN_S

    def receive(self, authorization: str | None, body: bytes) -> int:
        """Take one callback; return the HTTP status. Speech happens elsewhere, so this returns at once."""
        expected = b"Bearer " + self._callback_secret
        if authorization is None or not hmac.compare_digest(authorization.encode(), expected):
            logger.warning("Maribel callback rejected: bad or missing secret")
            return 401
        try:
            payload = json.loads(body)
            reply = MaribelReply(
                request_id=str(payload["request_id"]),
                conversation_id=str(payload["conversation_id"]),
                type=str(payload["type"]),
                speak=str(payload.get("speak") or "").strip(),
                question_id=str(payload["question_id"]) if payload.get("question_id") else None,
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("Maribel callback rejected: malformed body")
            return 400
        if reply.type not in REPLY_TYPES or (reply.type == "question" and reply.question_id is None):
            logger.warning("Maribel callback rejected: type=%s", reply.type)
            return 400
        now = self._clock()
        with self._lock:
            request = self._requests.get(reply.request_id)
            if request is None or request.conversation_id != reply.conversation_id:
                logger.warning("Maribel callback dropped: unknown request_id=%s", reply.request_id)
                return 404
            delay_s = now - request.sent_at
            logger.info(
                "Maribel replied request_id=%s conversation_id=%s type=%s after_s=%.1f",
                reply.request_id,
                reply.conversation_id,
                reply.type,
                delay_s,
            )
            if request.answered:
                logger.info("Maribel reply dropped: request_id=%s was already answered", reply.request_id)
                return 200
            request.answered = True
            if reply.type == "question" and reply.question_id is not None:
                self._question = _Question(reply.conversation_id, reply.question_id, now)
            elif self._question is not None and self._question.conversation_id == reply.conversation_id:
                self._question = None
            waiter, loop = request.waiter, request.loop
            late = waiter is None or waiter.done()
            if not late and waiter is not None and loop is not None:
                loop.call_soon_threadsafe(_resolve, waiter, reply)
            speak_late = (
                late and reply.request_id == self._latest_request_id and delay_s <= LATE_REPLY_S and bool(reply.speak)
            )
        if late and not speak_late:
            logger.info("Maribel reply not spoken: request_id=%s is too old or superseded", reply.request_id)
        if speak_late and self.on_late_reply is not None:
            self.on_late_reply(reply)
        return 200

    def _forget_old_requests(self, now: float) -> None:
        for request_id in [rid for rid, request in self._requests.items() if now - request.sent_at > LATE_REPLY_S]:
            del self._requests[request_id]


def _resolve(waiter: "asyncio.Future[MaribelReply]", reply: MaribelReply) -> None:
    if not waiter.done():
        waiter.set_result(reply)


class _ReplyHandler(BaseHTTPRequestHandler):
    server: "_ReplyServer"

    def do_POST(self) -> None:
        if self.path != REPLY_PATH:
            self._answer(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._answer(413 if length > MAX_BODY_BYTES else 400)
            return
        self._answer(self.server.link.receive(self.headers.get("Authorization"), self.rfile.read(length)))

    def do_GET(self) -> None:
        self._answer(404)

    def _answer(self, status: int) -> None:
        payload = json.dumps({"ok": status == 200}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("Maribel callback server: " + format, *args)


class _ReplyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, link: MaribelLink, port: int) -> None:
        super().__init__(("127.0.0.1", port), _ReplyHandler)
        self.link = link


def serve_replies(link: MaribelLink, port: int) -> ThreadingHTTPServer:
    """Serve Maribel's callbacks on 127.0.0.1:``port`` in a background thread; Funnel publishes the path."""
    server = _ReplyServer(link, port)
    threading.Thread(target=server.serve_forever, name="maribel-replies", daemon=True).start()
    logger.info("Maribel callbacks on http://127.0.0.1:%d%s", port, REPLY_PATH)
    return server


def _callback(request_id: str, conversation_id: str, reply_type: str, speak: str) -> dict[str, object]:
    body: dict[str, object] = {
        "version": 1,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "type": reply_type,
        "speak": speak,
    }
    if reply_type == "question":
        body["question_id"] = str(uuid.uuid4())
    return body


def main(argv: list[str] | None = None) -> int:
    """Send fake Maribel callbacks to Reachy's endpoint and print what came back."""
    parser = argparse.ArgumentParser(prog="python -m reachy_mini_conversation_app.maribel")
    commands = parser.add_subparsers(dest="command", required=True)
    fake = commands.add_parser(
        "fake-reply",
        help="without ids: check the endpoint turns strangers away; with the ids of a request Reachy "
        "logged (Maribel request sent ...): he speaks --speak",
    )
    fake.add_argument("--url", help="callback URL (default: MARIBEL_CALLBACK_URL)")
    fake.add_argument("--request-id")
    fake.add_argument("--conversation-id")
    fake.add_argument("--type", default="answer", choices=sorted(REPLY_TYPES))
    fake.add_argument("--speak", default="This is a test reply from Maribel.")
    args = parser.parse_args(argv)

    from reachy_mini_conversation_app.config import config

    url = args.url or config.MARIBEL_CALLBACK_URL
    secret = config.REACHY_CALLBACK_SECRET
    if not url or not secret:
        print("Set MARIBEL_CALLBACK_URL and REACHY_CALLBACK_SECRET (or pass --url).", file=sys.stderr)
        return 2
    signed = {"Authorization": f"Bearer {secret}"}
    if args.request_id and args.conversation_id:
        checks = [("reply", signed, 200, _callback(args.request_id, args.conversation_id, args.type, args.speak))]
    else:
        stranger = _callback(str(uuid.uuid4()), str(uuid.uuid4()), args.type, args.speak)
        checks = [
            ("no secret", {}, 401, stranger),
            ("wrong secret", {"Authorization": "Bearer wrong"}, 401, stranger),
            ("unknown request id", signed, 404, stranger),
        ]
    failed = False
    with httpx.Client(timeout=10) as client:
        for label, headers, expected, body in checks:
            status = client.post(url, json=body, headers=headers).status_code
            failed |= status != expected
            print(f"{label}: HTTP {status} (expected {expected})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
