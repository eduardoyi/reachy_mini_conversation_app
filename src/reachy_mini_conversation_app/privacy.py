"""The privacy switch and quiet hours.

While privacy is on, Reachy's microphone audio is dropped and his camera is not read. Only the admin app turns
it off (voice cannot: the mic is off). The state is kept in an owner-only file so a restart comes back the way
it was left. During quiet hours (``QUIET_HOURS``) he does not listen and incoming calls are declined.
"""

import os
import json
import logging
import threading
from typing import Callable
from pathlib import Path
from datetime import time, datetime, timezone

from reachy_mini_conversation_app.config import config


logger = logging.getLogger(__name__)


class PrivacySwitch:
    """Whether privacy is on, saved in ``path``; listeners hear each change."""

    def __init__(self, path: Path) -> None:
        """Load the saved state (off when there is none or it cannot be read)."""
        self._path = path
        self._lock = threading.Lock()
        self._listeners: list[Callable[[bool], None]] = []
        self._on = False
        self._since: str | None = None
        try:
            saved = json.loads(path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            logger.warning("Privacy state unreadable, starting with privacy off: %s", type(error).__name__)
            return
        if isinstance(saved, dict) and saved.get("on") is True:
            self._on = True
            self._since = str(saved.get("since") or "") or None
            logger.info("Privacy is on: mic and camera stay off")

    @property
    def on(self) -> bool:
        """Return whether the mic and camera are off."""
        return self._on

    @property
    def since(self) -> str | None:
        """Return when privacy was last turned on (ISO time), while it is on."""
        return self._since if self._on else None

    def add_listener(self, listener: Callable[[bool], None]) -> None:
        """Call ``listener(on)`` after every change."""
        self._listeners.append(listener)

    def set(self, on: bool) -> bool:
        """Turn privacy on or off and save it; returns whether it changed."""
        with self._lock:
            if on == self._on:
                return False
            self._on = on
            self._since = datetime.now(timezone.utc).isoformat(timespec="seconds") if on else None
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = self._path.with_name(f".{self._path.name}.tmp")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as file:
                    json.dump({"on": on, "since": self._since}, file)
                os.replace(temporary, self._path)
            except OSError as error:
                logger.warning("Privacy state not saved: %s", type(error).__name__)
        logger.info("Privacy turned %s", "on" if on else "off")
        for listener in self._listeners:
            try:
                listener(on)
            except Exception as error:  # a listener's failure must not undo the switch
                logger.warning("Privacy listener failed: %s", error)
        return True


def quiet_now(now: datetime | None = None, hours: tuple[time, time] | None = None) -> bool:
    """Return whether local time ``now`` falls in quiet hours (``config.QUIET_HOURS`` by default)."""
    start, end = hours or config.QUIET_HOURS or (None, None)
    if start is None or end is None:
        return False
    clock = (now or datetime.now()).time()
    return start <= clock < end if start < end else clock >= start or clock < end


def listening_allowed() -> bool:
    """Return whether the mic may be used now: privacy off and outside quiet hours."""
    return not PRIVACY.on and not quiet_now()


PRIVACY = PrivacySwitch(Path(config.PRIVACY_STATE_FILE))
