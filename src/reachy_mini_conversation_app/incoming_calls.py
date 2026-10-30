"""What Reachy does while a WhatsApp call to him rings (see docs/features/whatsapp-calls.md)."""

from __future__ import annotations
import time
import logging
import threading
from typing import Any, Callable

from reachy_mini_conversation_app.quick_commands import (
    INCOMING_CALL,
    CALL_RING_MAX_S,
    CALL_ANNOUNCE_AT_S,
    CALL_ANSWER_REQUEST,
    CALL_ANSWER_RETRY_S,
    switch_wobble,
    restore_wobble,
)
from reachy_mini_conversation_app.whatsapp_calls import Incoming


logger = logging.getLogger(__name__)

_TICK_S = 0.1
_WIGGLE_S = 1.2
_ANSWER_RETRY_STEP_S = 0.5


class IncomingRinger:
    """Ring for an incoming call: a pat on his (still) head answers; announced again, or wiggled if asleep.

    One ring at a time, on a thread of its own.
    """

    def __init__(
        self,
        calls: Any,
        runtime: Any,
        movement: Any,
        robot: Any,
        quick: Any,
        language: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Ring with these: the call manager, the hybrid runtime, the movement manager, the robot (wobble)."""
        self._calls = calls
        self._runtime = runtime
        self._movement = movement
        self._robot = robot
        self._quick = quick
        self._language = language
        self._clock = clock
        self._sleep = sleep
        self._ringing = threading.Event()

    def start(self, incoming: Incoming) -> None:
        """Begin ringing for ``incoming``."""
        self._ringing.set()
        threading.Thread(target=self.ring, args=(incoming,), daemon=True, name="call-ring").start()

    def stop(self) -> None:
        """Stop ringing (answered, declined, or the caller gave up)."""
        self._ringing.clear()

    def ring(self, incoming: Incoming) -> None:
        """Ring until stopped, a pat answers, or ``CALL_RING_MAX_S``; blocking."""
        asleep = self._runtime.asleep
        movement = self._movement
        movement.hold_still(True)  # a pat reads as a touch only on a still head
        switch_wobble(self._robot, on=False)  # his own voice would move it
        touches = movement.head_touches()
        started = self._clock()
        announcements = 0
        try:
            while self._ringing.is_set() and self._clock() - started < CALL_RING_MAX_S:
                elapsed = self._clock() - started
                if (
                    not asleep
                    and announcements < len(CALL_ANNOUNCE_AT_S)
                    and elapsed >= CALL_ANNOUNCE_AT_S[announcements]
                ):
                    announcements += 1
                    self._runtime.request_announcement(
                        INCOMING_CALL[self._language].format(name=incoming.name),
                        self._language,
                        answer=self._quick.answer_incoming if self._quick is not None else None,
                    )
                if asleep:
                    movement.set_listening(int(elapsed / _WIGGLE_S) % 2 == 0)  # perk, release: a silent ring
                if movement.head_touches() > touches:
                    logger.info("Head touched: answering the call")
                    self._answer()
                    return
                self._sleep(_TICK_S)
        finally:
            if asleep:
                movement.set_listening(False)
            movement.hold_still(False)
            restore_wobble(self._robot)

    def _answer(self) -> None:
        """Answer the ringing call, retrying while Reachy finishes what he is saying."""
        for _ in range(int(CALL_ANSWER_RETRY_S / _ANSWER_RETRY_STEP_S)):
            if self._calls.ringing is None:
                return
            self._runtime.request_activation("acoustic_wake", addressed_text=CALL_ANSWER_REQUEST)
            self._sleep(_ANSWER_RETRY_STEP_S)
