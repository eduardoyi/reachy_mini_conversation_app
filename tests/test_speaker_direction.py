import math
from typing import Any

import pytest

from reachy_mini_conversation_app.speaker_direction import (
    MAX_SPEAKER_YAW,
    SpeakerDirection,
    room_yaw,
    doa_angle_to_yaw,
)


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


def test_recent_speech_yaw_needs_three_samples() -> None:
    """One or two readings are too noisy to turn toward."""
    clock = _Clock()
    direction = _direction([{"angle": 0.0, "speech_detected": True}] * 3, clock)
    direction.poll_once()
    direction.poll_once()
    assert direction.recent_speech_yaw() is None

    direction.poll_once()
    assert direction.recent_speech_yaw() == pytest.approx(math.pi / 2)


def test_scattered_readings_are_no_direction() -> None:
    """Regression (2026-10-29): readings that disagree, as in a talking room, never turn him."""
    clock = _Clock()
    angles = [math.radians(90 - deg) for deg in (-89, 13, -51, -12, 60)]  # head-relative -89..+60 degrees
    direction = _direction([{"angle": angle, "speech_detected": True} for angle in angles], clock)
    for _ in angles:
        direction.poll_once()
        clock.now += 0.1

    assert direction.recent_speech_yaw() is None


def test_most_readings_agreeing_is_a_direction() -> None:
    """A real voice jitters a little and has the odd outlier; most readings close together still count."""
    clock = _Clock()
    angles = [math.radians(90 - deg) for deg in (-12, -10, -76, -18, -12)]
    direction = _direction([{"angle": angle, "speech_detected": True} for angle in angles], clock)
    for _ in angles:
        direction.poll_once()
        clock.now += 0.1

    assert direction.recent_speech_yaw() == pytest.approx(math.radians(-12.0))


def test_reachys_own_voice_is_not_a_speaker() -> None:
    """Readings while his speech plays, and half a second after, are dropped; later ones count."""
    clock = _Clock()
    direction = _direction([{"angle": 0.0, "speech_detected": True}] * 7, clock)
    direction.own_voice_until = lambda: 101.0
    for _ in range(3):
        direction.poll_once()  # at 100.0: he is talking
    clock.now = 101.4  # within the echo tail
    direction.poll_once()
    assert direction.recent_speech_yaw() is None

    clock.now = 101.6
    for _ in range(2):
        direction.poll_once()
    assert direction.recent_speech_yaw() is None  # only two after his voice ended
    direction.own_voice_until = None
    direction.poll_once()
    assert direction.recent_speech_yaw() == pytest.approx(math.pi / 2)


def test_fetch_failures_are_reported_and_recovered(caplog: pytest.LogCaptureFixture) -> None:
    """A daemon outage logs once, keeps no stale reading, and recovers on the next successful read."""
    clock = _Clock()
    direction = _direction(
        [
            OSError("refused"),
            OSError("refused"),
            {"angle": 0.0, "speech_detected": True},
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
        assert direction.poll_once()

    assert sum("unavailable" in record.message for record in caplog.records) == 1
    assert any("available again" in record.message for record in caplog.records)
    assert direction.recent_speech_yaw() == pytest.approx(math.pi / 2)


def test_readings_are_relative_to_where_the_head_faces() -> None:
    """Regression: after turning to the speaker, "straight ahead" means the speaker, not the room's front."""
    clock = _Clock()
    head = {"yaw": math.radians(-30.0)}
    direction = SpeakerDirection(
        "http://localhost:8000",
        head_yaw=lambda: head["yaw"],
        fetch=lambda: {"angle": math.pi / 2, "speech_detected": True},
        clock=clock,
    )
    for _ in range(3):
        direction.poll_once()
    assert direction.recent_speech_yaw() == pytest.approx(math.radians(-30.0))

    head["yaw"] = math.radians(20.0)
    clock.now += 2.0
    for _ in range(3):
        direction.poll_once()
    assert direction.recent_speech_yaw() == pytest.approx(math.radians(20.0))


@pytest.mark.parametrize(
    ("head_deg", "relative_deg", "expected"),
    [(0.0, 45.0, math.radians(45.0)), (120.0, 90.0, MAX_SPEAKER_YAW), (-120.0, -90.0, -MAX_SPEAKER_YAW)],
)
def test_room_yaw_adds_the_head_direction_and_stays_within_reach(
    head_deg: float, relative_deg: float, expected: float
) -> None:
    """Directions beyond the body's reach are clamped rather than wrapping to the other side."""
    assert room_yaw(math.radians(head_deg), math.radians(relative_deg)) == pytest.approx(expected)


def test_a_failing_head_pose_falls_back_to_forward() -> None:
    """If the head pose cannot be read, the reading is used as if the head faced forward."""

    def broken() -> float:
        raise RuntimeError("no pose")

    clock = _Clock()
    direction = SpeakerDirection(
        "http://localhost:8000", head_yaw=broken, fetch=lambda: {"angle": 0.0, "speech_detected": True}, clock=clock
    )
    for _ in range(3):
        direction.poll_once()
    assert direction.recent_speech_yaw() == pytest.approx(math.pi / 2)
