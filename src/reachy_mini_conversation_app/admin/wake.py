"""The Wake word card: how well Reachy hears his name, and what the household does to help him learn it.

It reads the wake-learning store the nightly job writes (the scoreboard, the clips to check, a proposed switch of
wake head) and keeps the household's answers there. Clips are cut on the Mac mini and played on the phones, never
kept there. A switch or an undo restarts Reachy once he is idle, so he loads the new head. Once a day, at the
reminder time and never in quiet hours, one push bundles whatever is waiting.
"""

import json
import wave
import asyncio
import logging
from typing import Any, Final
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from collections.abc import Callable

from reachy_mini_conversation_app.config import _quiet_hours
from reachy_mini_conversation_app.privacy import quiet_now
from reachy_mini_conversation_app.admin.records import RecordError, read_json, write_atomic
from reachy_mini_conversation_app.admin.app_link import AppLink
from reachy_mini_conversation_app.admin.notifier import Notifier
from reachy_mini_conversation_app.admin.settings import SETTINGS, SettingError, check, read_settings
from reachy_mini_conversation_app.wake_learning.store import ANSWERS, UNDO_DAYS, WakeLearningStore


logger = logging.getLogger(__name__)

# After a switch Reachy is restarted once he is idle, checked this often, for this long.
IDLE_CHECK_S: Final[float] = 60.0
IDLE_WAIT_S: Final[float] = 30 * 60.0
# A voice refresh is suggested when someone last recorded this long ago, and at most this often.
REFRESH_DAYS: Final[int] = 30
HISTORY_SHOWN: Final[int] = 10
WAKE_URL: Final[str] = "/#wake"
# What the phone is told after a switch and after an undo, for each way the new head gets loaded.
RESTART_NOTES: Final[dict[str, tuple[str, str]]] = {
    "now": ("Switching now. He's back in a minute.", "Going back now. He's back in a minute."),
    "when_free": ("Switching when he's free.", "Going back when he's free."),
    "on_start": ("Switched. He uses it when he next starts.", "Switched back. He uses it when he next starts."),
    "manual": ("Switched. Restart Reachy to use it.", "Switched back. Restart Reachy to use it."),
}


def _reminder_time(values: dict[str, str]) -> str:
    # "HH:MM" or "off"; the default when unset, or edited by hand into something else.
    default = SETTINGS["WAKE_REMINDER_TIME"].default
    try:
        return check("WAKE_REMINDER_TIME", values["WAKE_REMINDER_TIME"]) or default
    except SettingError:
        logger.warning("WAKE_REMINDER_TIME is not a time; reminding at %s", default)
        return default


def _idle(status: dict[str, Any]) -> bool:
    return str(status.get("mode", "")).lower() == "local_ambient" and not status.get("in_call")


class WakeWord:
    """What the Wake word card shows and does, over the wake-learning ``store``."""

    def __init__(
        self, store: WakeLearningStore, link: AppLink, env_file: Path, restart: Callable[[], object] | None
    ) -> None:
        """``restart`` restarts Reachy in the background; None where the admin app cannot restart him."""
        self.store = store
        self._link = link
        self._env_file = env_file
        self._restart = restart
        self._waiting: set[asyncio.Task[None]] = set()

    def summary(self) -> dict[str, Any]:
        """Return the scoreboard, what waits for the household, the wake heads, recordings and the reminder time."""
        live, shadow, undo = self.store.live(), self.store.shadow(), self.store.undoable()
        if undo is not None:
            until = datetime.fromisoformat(str(undo["at"])) + timedelta(days=UNDO_DAYS)
            undo = {**undo, "until": until.isoformat(timespec="seconds")}
        return {
            "scoreboard": self.store.scoreboard(),
            "to_check": len(self.store.queue()),
            "proposal": self.store.proposal(),
            "live": asdict(live) if live else None,
            "shadow": asdict(shadow) if shadow else None,
            "undo": undo,
            "history": self.store.history()[:HISTORY_SHOWN],
            "recordings": self.store.recordings_by_person(),
            "reminder": _reminder_time(read_settings(self._env_file)),
        }

    def to_check(self) -> list[dict[str, Any]]:
        """Return the clips waiting to be checked: id, question and length in seconds."""
        return [
            {"id": item.id, "question": item.question, "seconds": round(item.end - item.start, 1)}
            for item in self.store.queue()
        ]

    def clip(self, item_id: str) -> bytes:
        """Return a waiting clip as WAV; raises LookupError when it is not waiting or cannot be read."""
        item = next((item for item in self.store.queue() if item.id == item_id), None)
        if item is None:
            raise LookupError(item_id)
        try:
            return self.store.clip_audio(item)
        except (OSError, EOFError, wave.Error) as error:
            logger.warning("Wake clip %s unreadable: %s", item_id, type(error).__name__)
            raise LookupError(item_id) from error

    def answer(self, item_id: str, answer: str, who: str) -> None:
        """Keep ``who``'s answer to a waiting clip; ValueError for an unknown answer, LookupError for no such clip."""
        if answer not in ANSWERS:
            raise ValueError(answer)
        if all(item.id != item_id for item in self.store.queue()):
            raise LookupError(item_id)
        self.store.answer(item_id, answer, who)

    def keep(self, who: str) -> str:
        """Keep the live head and return the proposed one's name (it stays in shadow); ValueError when none."""
        proposal = self.store.proposal()
        if proposal is None:
            raise ValueError("Nothing proposed")
        self.store.decline(who)
        return str(proposal.get("head", ""))

    async def switch(self, who: str) -> dict[str, str]:
        """Make the proposed head live and restart Reachy for it; ValueError when nothing is proposed."""
        head = await asyncio.to_thread(self.store.switch, who)
        plan = await self._load_live_head()
        return {"head": head.name, "restart": plan, "message": RESTART_NOTES[plan][0]}

    async def undo(self, who: str) -> dict[str, str]:
        """Put back the head the last switch replaced and restart Reachy for it; ValueError when there is none."""
        head = await asyncio.to_thread(self.store.undo, who)
        plan = await self._load_live_head()
        return {"head": head.name, "restart": plan, "message": RESTART_NOTES[plan][1]}

    async def _load_live_head(self) -> str:
        if self._restart is None:
            return "manual"
        status = await self._link.status()
        if status is None:
            return "on_start"
        if _idle(status):
            self._restart()
            return "now"
        if not self._waiting:  # one wait serves a switch and a quick undo: either way he loads the live head
            task = asyncio.create_task(self._restart_when_idle(self._restart))
            self._waiting.add(task)
            task.add_done_callback(self._waiting.discard)
        return "when_free"

    async def _restart_when_idle(self, restart: Callable[[], object]) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + IDLE_WAIT_S
        while loop.time() < deadline:
            await asyncio.sleep(IDLE_CHECK_S)
            status = await self._link.status()
            if status is None:
                logger.info("Reachy stopped before he was free; he loads the live wake head when he starts")
                return
            if _idle(status):
                logger.info("Reachy is free: restarting him for the live wake head")
                restart()
                return
        logger.warning(
            "Reachy stayed busy for %.0f min; the live wake head loads at his next restart", IDLE_WAIT_S / 60
        )


class WakeReminder:
    """The daily wake-word push: at the reminder time, never in quiet hours, only when something is waiting."""

    def __init__(
        self,
        store: WakeLearningStore,
        notifier: Notifier,
        env_file: Path,
        state_file: Path,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        """Read the reminder time and quiet hours from ``env_file``; remember what was sent in ``state_file``."""
        self._store = store
        self._notifier = notifier
        self._env_file = env_file
        self._state_file = state_file
        self._now = now
        try:
            self._state, _ = read_json(state_file)
        except RecordError as error:
            logger.warning("Wake reminder record unreadable, starting afresh: %s", error)
            self._state = {}

    async def check(self) -> None:
        """Send today's reminder once it is due, bundling the new model, the clips to check and a voice refresh."""
        now = self._now()
        values = read_settings(self._env_file)
        at, today = _reminder_time(values), now.date().isoformat()
        if at == "off" or now.strftime("%H:%M") < at or self._state.get("day") == today:
            return
        if quiet_now(now, _quiet_hours(values["QUIET_HOURS"])):
            return
        self._state["day"] = today
        lines: list[str] = []
        actions: list[dict[str, str]] = []
        proposal = self._store.proposal() or {}
        new_model = bool(proposal) and not proposal.get("notified") and not proposal.get("declined")
        if new_model:
            summary = proposal.get("summary") or {}
            gained = int(summary.get("shadow_caught", 0)) - int(summary.get("live_caught", 0))
            days = int(summary.get("days", 1))
            more_fires = float(summary.get("shadow_fires_per_day", 0)) - float(summary.get("live_fires_per_day", 0))
            fires = "no more false fires" if more_fires < 0.05 else f"{more_fires:.1f} more false fires a day"
            period = "yesterday's" if days == 1 else f"the last {days} days'"
            lines.append(
                f"A better wake model is ready: it caught {gained} more of {period} {summary.get('calls', 0)} calls, "
                f"with {fires}. Switch?"
            )
            actions = [{"action": "switch", "title": "Switch"}, {"action": "later", "title": "Not now"}]
        clips = len(self._store.queue())
        if clips:
            lines.append(f"{clips} clip{'' if clips == 1 else 's'} to check, about a minute.")
            actions = actions or [{"action": "check", "title": "Check now"}]
        month_ago = now - timedelta(days=REFRESH_DAYS)
        cutoff = month_ago.astimezone(timezone.utc).isoformat(timespec="seconds")
        people = self._store.recordings_by_person()
        stale = sorted(person for person, made in people.items() if str(made["last"]) < cutoff)
        refresh = (not people or bool(stale)) and str(self._state.get("refresh", "")) <= month_ago.date().isoformat()
        if refresh and stale:
            names = f"{', '.join(stale[:-1])} and {stale[-1]}" if len(stale) > 1 else stale[0]
            verb = "hasn't" if len(stale) == 1 else "haven't"
            lines.append(f"2-minute voice refresh? {names} {verb} recorded in a month.")
        elif refresh:
            lines.append("2-minute voice refresh? Nobody has recorded their call yet.")
        if lines and await self._notifier.notify("Wake word", "\n".join(lines), actions=actions, url=WAKE_URL):
            if new_model:
                self._store.mark_proposal_notified()
            if refresh:
                self._state["refresh"] = today
        try:
            write_atomic(self._state_file, json.dumps(self._state).encode())
        except OSError as error:
            logger.warning("Wake reminder record not saved: %s", type(error).__name__)
