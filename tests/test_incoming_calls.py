"""Tests for what Reachy does while a call to him rings (incoming_calls.py)."""

from typing import Any
from unittest.mock import MagicMock

from reachy_mini_conversation_app.incoming_calls import IncomingRinger
from reachy_mini_conversation_app.whatsapp_calls import Incoming


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Movement:
    """Records holds and the antenna ring; a pat lands at ``pat_at`` seconds."""

    def __init__(self, clock: _Clock, pat_at: float | None = None) -> None:
        self.clock = clock
        self.pat_at = pat_at
        self.holds: list[bool] = []
        self.rings: list[bool] = []

    def hold_still(self, active: bool) -> None:
        self.holds.append(active)

    def head_touches(self) -> int:
        return 1 if self.pat_at is not None and self.clock.now >= self.pat_at else 0

    def ring_antennas(self, active: bool) -> None:
        self.rings.append(active)


class _Runtime:
    def __init__(self, asleep: bool, calls: Any) -> None:
        self.asleep = asleep
        self.calls = calls
        self.announced: list[tuple[str, str]] = []
        self.activations: list[str] = []

    def request_announcement(self, text: str, language: str, *, answer: Any = None) -> None:
        self.announced.append((text, language))

    def request_activation(self, reason: str, addressed_text: str = "") -> None:
        self.activations.append(addressed_text)
        if len(self.activations) == 3:
            self.calls.ringing = None  # answered on the third try (he was still talking)


def _ring(*, asleep: bool, pat_at: float | None) -> tuple[_Runtime, _Movement, Any]:
    clock = _Clock()
    calls = MagicMock(ringing=Incoming("C1", "Test Person"))
    runtime = _Runtime(asleep, calls)
    movement = _Movement(clock, pat_at)
    robot = MagicMock()
    ringer = IncomingRinger(calls, runtime, movement, robot, None, "es", clock=clock, sleep=clock.sleep)
    ringer._ringing.set()
    ringer.ring(Incoming("C1", "Test Person"))
    return runtime, movement, robot


def test_awake_he_announces_again_until_answered_and_a_pat_answers() -> None:
    """Announced at 0, 15 and 30 s; a pat at 35 s answers (retried until it takes); the head is released."""
    runtime, movement, robot = _ring(asleep=False, pat_at=35.0)
    assert runtime.announced == [("¡Te llama Test Person!", "es")] * 3
    assert runtime.activations == ["Reachy, answer"] * 3
    assert movement.holds == [True, False]
    robot.disable_wobbling.assert_called_once()
    assert movement.rings == [True, False]


def test_asleep_it_rings_silently_with_the_same_flutter_and_a_pat_still_answers() -> None:
    """Asleep: no voice, the antennas ring as when awake, and a pat answers."""
    runtime, movement, _ = _ring(asleep=True, pat_at=5.0)
    assert runtime.announced == []
    assert runtime.activations[0] == "Reachy, answer"
    assert movement.rings == [True, False]


def test_unanswered_it_stops_ringing_and_lets_go_of_the_head() -> None:
    """Nobody answers: it gives up after the ring time without activating anything."""
    runtime, movement, _ = _ring(asleep=False, pat_at=None)
    assert runtime.activations == [] and len(runtime.announced) == 3
    assert movement.holds == [True, False] and movement.rings == [True, False]
