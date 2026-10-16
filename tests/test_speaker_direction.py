import math
from typing import Any

import pytest

from reachy_mini_conversation_app.speaker_direction import SpeakerDirection, doa_angle_to_yaw


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _direction(readings: list[dict[str, Any] | None | Exception], clock: _Clock) -> SpeakerDirection:
    pending = list(readings)

    def fetch() -> dict[str, Any] | None:
        reading = pending.pop(0)
        if isinstance(reading, Exception):
            raise reading
        return reading

    return SpeakerDirection("http://localhost:8000", fetch=fetch, clock=clock)


@pytest.mark.parametrize(
    ("angle", "yaw_deg"),
    [(0.0, 90.0), (math.pi / 4, 45.0), (math.pi / 2, 0.0), (math.pi, -90.0), (-0.2, 90.0)],
)
def test_doa_angle_maps_left_front_right_to_robot_yaw(angle: float, yaw_deg: float) -> None:
    """DoA 0 = left, pi/2 = front, pi = right; robot yaw is positive to the left and clamped to the front half."""
    assert doa_angle_to_yaw(angle) == pytest.approx(math.radians(yaw_deg))


def test_recent_speech_yaw_is_the_median_of_recent_speech_only() -> None:
    """Only readings with speech count, an outlier does not move the median, and old speech expires."""
    clock = _Clock()
    readings: list[dict[str, Any] | None | Exception] = [
        {"angle": math.pi / 2, "speech_detected": False},
        {"angle": math.pi / 4, "speech_detected": True},
        None,
        {"angle": math.pi / 4 + 0.05, "speech_detected": True},
        {"angle": math.pi, "speech_detected": True},
    ]
    direction = _direction(readings, clock)
    for _ in readings:
        assert direction.poll_once()
        clock.now += 0.1

    assert direction.recent_speech_yaw() == pytest.approx(math.radians(45.0) - 0.05)

    clock.now += 2.0
    assert direction.recent_speech_yaw() is None


def test_recent_speech_yaw_needs_two_samples() -> None:
    """A single reading is too noisy to turn toward."""
    clock = _Clock()
    direction = _direction([{"angle": 0.0, "speech_detected": True}], clock)
    direction.poll_once()

    assert direction.recent_speech_yaw() is None


def test_fetch_failures_are_reported_and_recovered(caplog: pytest.LogCaptureFixture) -> None:
    """A daemon outage logs once, keeps no stale reading, and recovers on the next successful read."""
    clock = _Clock()
    direction = _direction(
        [
            OSError("refused"),
            OSError("refused"),
            {"angle": 0.0, "speech_detected": True},
            {"angle": 0.0, "speech_detected": True},
        ],
        clock,
    )

    with caplog.at_level("INFO"):
        assert not direction.poll_once()
        assert not direction.poll_once()
        assert direction.poll_once()
        assert direction.poll_once()

    assert sum("unavailable" in record.message for record in caplog.records) == 1
    assert any("available again" in record.message for record in caplog.records)
    assert direction.recent_speech_yaw() == pytest.approx(math.pi / 2)
