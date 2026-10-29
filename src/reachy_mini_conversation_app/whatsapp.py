"""Send photos and voice notes from Reachy's own WhatsApp number through GOWA, a WhatsApp server on this computer.

GOWA (go-whatsapp-web-multidevice, run as ``whatsapp rest``) is linked to the household's spare number as
a WhatsApp linked device and listens on loopback. Reachy only posts to it; it never reads chats.

Logs keep outcomes, HTTP statuses, GOWA's result codes, sizes and times; never chat ids, the
credentials or the images.

    python -m reachy_mini_conversation_app.whatsapp status | groups | pair PHONE
"""

import sys
import enum
import time
import asyncio
import logging
import argparse
from typing import Any, Final
from dataclasses import dataclass

import httpx


logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S: Final[float] = 3.0
# The whole send, retry included; one still running then may yet arrive (GOWA's own limit is 45 s).
SEND_DEADLINE_S: Final[float] = 15.0
STATUS_TIMEOUT_S: Final[float] = 5.0
RETRY_PAUSE_S: Final[float] = 1.0


class SendResult(enum.Enum):
    """How a send ended."""

    SENT = "sent"
    FAILED = "failed"
    # It timed out after leaving: it may still arrive.
    UNSURE = "unsure"


@dataclass(frozen=True)
class ServerStatus:
    """GOWA's link to WhatsApp, and its own message (e.g. that no device was paired yet)."""

    connected: bool
    logged_in: bool
    message: str


class WhatsAppLink:
    """Talk to the local GOWA server at ``url`` with its basic-auth ``user:password``."""

    def __init__(
        self,
        url: str,
        auth: str,
        *,
        client: httpx.AsyncClient | None = None,
        retry_pause_s: float = RETRY_PAUSE_S,
        deadline_s: float = SEND_DEADLINE_S,
    ) -> None:
        """Use ``client`` instead of a new client per call (tests)."""
        self._url = url.rstrip("/")
        user, _, password = auth.partition(":")
        self._auth = httpx.BasicAuth(user, password)
        self._client = client
        self._retry_pause_s = retry_pause_s
        self._deadline_s = deadline_s

    async def send_image(self, chat: str, jpeg: bytes) -> SendResult:
        """Send ``jpeg`` to ``chat`` (a WhatsApp id such as ``...@g.us``); retried only if it never left."""
        # Without hd, GOWA shrinks every image to 600 px wide.
        return await self._send(
            "photo", "/send/image", chat, {"hd": "true"}, ("image", "photo.jpg", jpeg, "image/jpeg")
        )

    async def send_audio(self, chat: str, ogg: bytes) -> SendResult:
        """Send ``ogg`` (OGG Opus) to ``chat`` as a voice note; retried only if it never left."""
        # GOWA sends an OGG file as is with ptt; anything else would need ffmpeg.
        return await self._send(
            "voice note", "/send/audio", chat, {"ptt": "true"}, ("audio", "message.ogg", ogg, "audio/ogg")
        )

    async def _send(
        self, what: str, path: str, chat: str, fields: dict[str, str], part: tuple[str, str, bytes, str]
    ) -> SendResult:
        started_at = time.perf_counter()
        try:
            async with asyncio.timeout(self._deadline_s):
                return await self._send_once_or_twice(what, path, chat, fields, part, started_at)
        except TimeoutError:
            reason = "deadline"
        except httpx.TimeoutException as error:  # read or write: the request may have left
            reason = type(error).__name__
        logger.warning(
            "WhatsApp %s send timed out after %.1f s (%s); it may still arrive",
            what,
            time.perf_counter() - started_at,
            reason,
        )
        return SendResult.UNSURE

    async def _send_once_or_twice(
        self,
        what: str,
        path: str,
        chat: str,
        fields: dict[str, str],
        part: tuple[str, str, bytes, str],
        started_at: float,
    ) -> SendResult:
        name, filename, content, content_type = part
        for attempt in (1, 2):
            if attempt == 2:
                await asyncio.sleep(self._retry_pause_s)
            try:
                response = await self._request(
                    "POST",
                    path,
                    self._deadline_s,
                    data={"phone": chat, **fields},
                    files={name: (filename, content, content_type)},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                logger.warning("WhatsApp %s attempt %d could not connect: %s", what, attempt, type(error).__name__)
                continue
            except httpx.TimeoutException:
                raise  # unsure, in _send
            except httpx.HTTPError as error:
                logger.warning("WhatsApp %s send failed: %s", what, type(error).__name__)
                return SendResult.FAILED
            code = _result_code(response)
            if response.is_success and code == "SUCCESS":
                logger.info(
                    "WhatsApp %s sent bytes=%d attempt=%d ms=%.0f",
                    what,
                    len(content),
                    attempt,
                    (time.perf_counter() - started_at) * 1000,
                )
                return SendResult.SENT
            logger.warning("WhatsApp %s refused: HTTP %d code=%s", what, response.status_code, code)
            return SendResult.FAILED
        return SendResult.FAILED

    async def status(self) -> ServerStatus | None:
        """Return whether GOWA is connected and logged in to WhatsApp, or None if it cannot be reached."""
        try:
            response = await self._request("GET", "/app/status", STATUS_TIMEOUT_S)
        except httpx.HTTPError as error:
            logger.warning("WhatsApp server unreachable: %s", type(error).__name__)
            return None
        body = _json(response)
        results = body.get("results")
        results = results if isinstance(results, dict) else {}
        return ServerStatus(
            connected=results.get("is_connected") is True,
            logged_in=results.get("is_logged_in") is True,
            message=f"HTTP {response.status_code} {body.get('code', '')}: {body.get('message', '')}",
        )

    async def groups(self) -> list[tuple[str, str]]:
        """Return ``(name, id)`` of every group Reachy's number is in."""
        response = await self._request("GET", "/user/my/groups", STATUS_TIMEOUT_S)
        response.raise_for_status()
        results = _json(response).get("results") or {}
        items = results.get("data") if isinstance(results, dict) else None
        return [
            (str(item.get("Name", "")), str(item.get("JID", ""))) for item in items or [] if isinstance(item, dict)
        ]

    async def pairing_code(self, phone: str) -> str | None:
        """Ask GOWA for the code that links it to ``phone`` (WhatsApp, Linked devices, Link with phone number)."""
        devices = await self._request("GET", "/devices", STATUS_TIMEOUT_S)
        devices.raise_for_status()
        if not _json(devices).get("results"):
            # A fresh server has no device slot; its single-device endpoints then answer 404.
            created = await self._request("POST", "/devices", STATUS_TIMEOUT_S, json={"device_id": "reachy"})
            created.raise_for_status()
        response = await self._request("GET", "/app/login-with-code", STATUS_TIMEOUT_S, params={"phone": phone})
        response.raise_for_status()
        results = _json(response).get("results") or {}
        code = results.get("pair_code") if isinstance(results, dict) else None
        return str(code) if code else None

    async def _request(self, method: str, path: str, timeout_s: float, **kwargs: Any) -> httpx.Response:
        timeout = httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S)
        if self._client is not None:
            return await self._client.request(method, f"{self._url}{path}", auth=self._auth, timeout=timeout, **kwargs)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, f"{self._url}{path}", auth=self._auth, **kwargs)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _result_code(response: httpx.Response) -> str:
    return str(_json(response).get("code", ""))


def main(argv: list[str] | None = None) -> int:
    """Check, pair or list the groups of the local WhatsApp server."""
    parser = argparse.ArgumentParser(prog="python -m reachy_mini_conversation_app.whatsapp")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="whether the WhatsApp server answers and is logged in")
    commands.add_parser("groups", help="the groups Reachy's number is in, with the id for WHATSAPP_PHOTOS_CHAT")
    pair = commands.add_parser("pair", help="print the code that links the server to Reachy's phone number")
    pair.add_argument("phone", help="Reachy's number with country code, digits only (e.g. 351912345678)")
    args = parser.parse_args(argv)

    from reachy_mini_conversation_app.config import config

    if not config.WHATSAPP_API_URL or not config.WHATSAPP_API_AUTH:
        print("Set WHATSAPP_API_URL and WHATSAPP_API_AUTH in .env.", file=sys.stderr)
        return 2
    link = WhatsAppLink(config.WHATSAPP_API_URL, config.WHATSAPP_API_AUTH)
    try:
        if args.command == "status":
            status = asyncio.run(link.status())
            if status is None:
                print("The WhatsApp server does not answer.")
                return 1
            print(f"connected={status.connected} logged_in={status.logged_in} ({status.message})")
            return 0 if status.logged_in else 1
        if args.command == "groups":
            groups = asyncio.run(link.groups())
            for name, chat in groups:
                print(f"{name}\t{chat}")
            if not groups:
                print("Reachy's number is in no group yet.", file=sys.stderr)
            return 0
        code = asyncio.run(link.pairing_code(args.phone))
    except httpx.HTTPError as error:
        print(f"The WhatsApp server failed: {type(error).__name__} {error}", file=sys.stderr)
        return 1
    if code is None:
        print("The WhatsApp server gave no pairing code (already linked?).", file=sys.stderr)
        return 1
    print(f"On Reachy's phone: WhatsApp > Linked devices > Link a device > Link with phone number: {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
