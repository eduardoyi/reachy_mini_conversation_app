"""Tests for finding the call in a recording of someone saying Reachy's name."""

import numpy as np

from reachy_mini_conversation_app.wake_learning.takes import level_db, voiced_span, take_finished


RATE = 16000


def _audio(*parts: tuple[float, int]) -> np.ndarray:
    return np.concatenate([np.full(int(seconds * RATE), value, dtype=np.int16) for seconds, value in parts])


def test_a_take_ends_after_a_short_pause_once_a_voice_was_heard() -> None:
    """Silence alone never ends it; a voice followed by 0.7 s of quiet does, and so does a voice that runs on."""
    assert not take_finished(_audio((2.0, 3)), RATE)
    assert not take_finished(_audio((0.3, 3), (0.6, 2500), (0.3, 3)), RATE)
    assert take_finished(_audio((0.3, 3), (0.6, 2500), (0.8, 3)), RATE)
    assert take_finished(_audio((0.3, 3), (4.5, 2500)), RATE)


def test_the_voice_is_cut_with_a_quarter_second_either_side() -> None:
    """The kept part is the voice plus margins; a quiet recording has none."""
    span = voiced_span(_audio((1.0, 3), (0.5, 2500), (1.0, 3)), RATE)
    assert span is not None
    start, end = span
    assert abs(start / RATE - 0.75) < 0.04 and abs(end / RATE - 1.75) < 0.04
    assert voiced_span(_audio((2.0, 3)), RATE) is None


def test_a_far_quiet_call_still_counts_as_a_voice() -> None:
    """A call from across the room is far quieter than one nearby, but well over the room's own level."""
    assert voiced_span(_audio((1.0, 20), (0.5, 200), (1.0, 20)), RATE) is not None


def test_level_is_in_db_below_full_scale() -> None:
    """Full scale is 0 dB; a tenth of it is -20 dB."""
    assert round(level_db(np.full(100, 3277, dtype=np.int16))) == -20
