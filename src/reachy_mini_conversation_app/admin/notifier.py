"""Push notifications to the installed admin app: subscriptions, and a watch on Reachy that sends them.

Every minute the watch looks at the app, WhatsApp, the call service, the privacy switch and new missed calls,
and notifies each subscribed phone when something changes for the worse.
"""

import json
import time
import asyncio
import logging
from typing import Any, Final
from pathlib import Path
from collections.abc import Callable, Awaitable

import httpx

from reachy_mini_conversation_app.json_lines import JsonLines
from reachy_mini_conversation_app.admin.records import RecordError, StaleVersion, read_json, write_json
from reachy_mini_conversation_app.admin.web_push import send, unb64url, load_or_create_key, application_server_key


logger = logging.getLogger(__name__)

WATCH_INTERVAL_S: Final[float] = 60.0
# Privacy left on this long earns a reminder (once each time it is turned on).
PRIVACY_REMINDER_S: Final[float] = 4 * 3600
MAX_SUBSCRIPTIONS: Final[int] = 10
SUBSCRIPTIONS_FILE: Final[str] = "push.json"
KEY_FILE: Final[str] = "vapid.pem"
# Push services answer 404 or 410 for a subscription the phone dropped.
_GONE: Final[frozenset[int]] = frozenset({404, 410})


class Notifier:
    """Subscribed phones (``directory``/push.json) and sending to them."""

    def __init__(self, directory: Path, client: httpx.AsyncClient | None = None) -> None:
        """Keep subscriptions and the VAPID key in ``directory``."""
        self._file = directory / SUBSCRIPTIONS_FILE
        self._key = load_or_create_key(directory / KEY_FILE)
        self._client = client or httpx.AsyncClient()
        self._lock = asyncio.Lock()
        # The endpoints the last notification found expired, so a phone can tell its own was dropped.
        self.last_gone: set[str] = set()

    @property
    def public_key(self) -> str:
        """Return the key the browser subscribes with."""
        return application_server_key(self._key)

    def _subscriptions(self) -> tuple[list[dict[str, Any]], str]:
        document, version = read_json(self._file)
        entries = document.get("subscriptions")
        return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else [], version

    async def subscribe(self, subscription: dict[str, Any], who: str, subject: str) -> None:
        """Keep ``subscription`` (a browser PushSubscription) for ``who``; ``subject`` is the admin app's URL."""
        endpoint = subscription.get("endpoint")
        keys = subscription.get("keys")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://") or not isinstance(keys, dict):
            raise ValueError("not a push subscription")
        p256dh, auth = keys.get("p256dh"), keys.get("auth")
        try:
            valid = (
                isinstance(p256dh, str) and isinstance(auth, str) and len(unb64url(p256dh)) == 65
                and len(unb64url(auth)) == 16
            )  # fmt: skip
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("not a push subscription")
        entry = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}, "who": who,
                 "subject": subject}  # fmt: skip
        async with self._lock:
            entries, version = self._subscriptions()
            kept = [e for e in entries if e.get("endpoint") != endpoint][-(MAX_SUBSCRIPTIONS - 1) :]
            write_json(self._file, {"subscriptions": [*kept, entry]}, version)

    async def unsubscribe(self, endpoint: str) -> None:
        """Forget the subscription for ``endpoint``."""
        async with self._lock:
            entries, version = self._subscriptions()
            write_json(self._file, {"subscriptions": [e for e in entries if e.get("endpoint") != endpoint]}, version)

    async def notify(self, title: str, body: str) -> int:
        """Send to every subscribed phone; returns how many accepted it. Dropped subscriptions are forgotten."""
        async with self._lock:
            try:
                entries, version = self._subscriptions()
            except RecordError as error:
                logger.warning("Push subscriptions unreadable: %s", error)
                return 0
            delivered, gone = 0, set()
            for entry in entries:
                try:
                    status = await send(self._client, entry, {"title": title, "body": body}, self._key,
                                        str(entry.get("subject") or "mailto:reachy@localhost"))  # fmt: skip
                except ValueError as error:  # a damaged subscription must not block the other phones
                    logger.warning("Push subscription skipped: %s", error)
                    continue
                if 200 <= status < 300:
                    delivered += 1
                elif status in _GONE:
                    gone.add(entry.get("endpoint"))
            if gone:
                try:
                    write_json(
                        self._file, {"subscriptions": [e for e in entries if e.get("endpoint") not in gone]}, version
                    )
                except (StaleVersion, RecordError) as error:
                    logger.warning("Dropped push subscriptions not forgotten: %s", error)
            self.last_gone = {str(endpoint) for endpoint in gone}
        logger.info("Notification sent to %d of %d phones", delivered, len(entries))
        return delivered


class Watch:
    """Notice what is worth a notification, from successive health checks."""

    def __init__(
        self,
        notifier: Notifier,
        health: Callable[[], Awaitable[dict[str, Any]]],
        activity: JsonLines,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Notify through ``notifier`` from ``health()`` checks and new entries in ``activity``."""
        self._notifier = notifier
        self._health = health
        self._activity = activity
        self._clock = clock
        self._last: dict[str, Any] | None = None
        self._seen_activity: set[str] | None = None
        self._reminded_privacy: str | None = None

    async def check(self) -> None:
        """Run one check and send what changed for the worse since the last."""
        health = await self._health()
        last = self._last
        self._last = health
        for title, body in self._changes(last, health):
            await self._notifier.notify(title, body)
        for entry in self._new_activity():
            if entry.get("kind") == "call_in" and entry.get("outcome") == "missed":
                await self._notifier.notify("Missed call", f"{entry.get('who') or 'Someone'} called Reachy.")

    def _changes(self, last: dict[str, Any] | None, now: dict[str, Any]) -> list[tuple[str, str]]:
        changes = []
        if last is not None:
            if last["app"]["running"] and not now["app"]["running"] and not now["app"].get("restarting"):
                changes.append(("Reachy stopped", "The conversation app is not running."))
            if last["whatsapp"]["ok"] and now["whatsapp"]["ok"] is False:
                changes.append(("WhatsApp unlinked", now["whatsapp"]["detail"] or "Reachy's WhatsApp is not linked."))
            if last["calls"]["ok"] and now["calls"]["ok"] is False:
                changes.append(("Calls unavailable", "The call service is not connected."))
        since = now["privacy"]["since"]
        if now["privacy"]["on"] and since and since != self._reminded_privacy:
            on_for = now["privacy"].get("on_for_s")
            if isinstance(on_for, (int, float)) and on_for >= PRIVACY_REMINDER_S:
                self._reminded_privacy = since
                changes.append(("Privacy is still on", "Reachy's mic and camera have been off for hours."))
        return changes

    def _new_activity(self) -> list[dict[str, Any]]:
        entries = self._activity.recent(50)
        keys = [json.dumps(entry, sort_keys=True) for entry in entries]
        seen, self._seen_activity = self._seen_activity, set(keys)
        if seen is None:
            return []
        return [entry for entry, key in zip(reversed(entries), reversed(keys)) if key not in seen]

    async def run(self) -> None:
        """Check every WATCH_INTERVAL_S until cancelled."""
        while True:
            try:
                await self.check()
            except Exception as error:  # the watch must outlive one bad check
                logger.warning("Watch check failed: %s", error)
            await asyncio.sleep(WATCH_INTERVAL_S)
