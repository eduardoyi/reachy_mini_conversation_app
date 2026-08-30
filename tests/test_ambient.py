"""Tests for the RAM-only ambient audio buffer."""

import logging
from pathlib import Path

import numpy as np
import pytest

from reachy_mini_conversation_app.ambient import AmbientAudioBuffer


def test_buffer_overwrites_oldest_samples_and_defensively_copies() -> None:
    """The fixed-size buffer retains only the newest chronological samples."""
    buffer = AmbientAudioBuffer(max_seconds=0.002, sample_rate=1_000)
    first = np.array([1], dtype=np.int16)
    second = np.array([2, 3], dtype=np.int16)

    buffer.append((1_000, first))
    buffer.append((1_000, second))
    first[0] = 99
    second[0] = 98

    sample_rate, snapshot = buffer.snapshot()
    assert sample_rate == 1_000
    assert snapshot.dtype == np.int16
    np.testing.assert_array_equal(snapshot, [2, 3])
    snapshot[0] = 77
    np.testing.assert_array_equal(buffer.snapshot()[1], [2, 3])
    assert buffer.seconds_buffered == pytest.approx(0.002)


def test_buffer_accepts_empty_and_mono_float_frames() -> None:
    """Empty input is a no-op and float PCM is normalized to copied int16."""
    buffer = AmbientAudioBuffer(max_seconds=1, sample_rate=10)
    buffer.append((10, np.empty(0, dtype=np.int16)))
    buffer.append((10, np.array([[0.0], [1.0], [-1.0]], dtype=np.float32)))

    assert buffer.sample_count == 3
    assert buffer.snapshot()[1].dtype == np.int16
    np.testing.assert_array_equal(buffer.snapshot()[1], [0, 32767, -32767])


def test_buffer_rejects_mixed_sample_rate_without_mutating() -> None:
    """A rate mismatch fails closed instead of silently resampling or lying."""
    buffer = AmbientAudioBuffer(max_seconds=1, sample_rate=10)
    buffer.append((10, np.array([1, 2], dtype=np.int16)))

    with pytest.raises(ValueError, match="accepts 10 Hz"):
        buffer.append((20, np.array([3], dtype=np.int16)))

    np.testing.assert_array_equal(buffer.snapshot()[1], [1, 2])


def test_buffer_duration_is_capped_at_twenty_seconds() -> None:
    """Configuration cannot expand the privacy buffer beyond its fixed bound."""
    buffer = AmbientAudioBuffer(max_seconds=60, sample_rate=10)

    assert buffer.max_seconds == 20
    assert buffer.max_samples == 200


def test_buffer_downmixes_sdk_stereo_float_and_rejects_unsupported_input() -> None:
    """SDK float32 stereo is downmixed, clipped, scaled, and copied to int16."""
    buffer = AmbientAudioBuffer(max_seconds=1, sample_rate=10)
    sdk_frame = np.array(
        [[0.25, 0.5], [2.0, 2.0], [-2.0, -2.0]],
        dtype=np.float32,
    )

    buffer.append((10, sdk_frame))
    sdk_frame[0, 0] = -1.0

    assert buffer.snapshot()[1].dtype == np.int16
    np.testing.assert_array_equal(buffer.snapshot()[1], [12287, 32767, -32768])

    with pytest.raises(ValueError, match="one-dimensional mono or two-dimensional"):
        buffer.append((10, np.zeros((2, 2, 1), dtype=np.int16)))
    with pytest.raises(TypeError, match="Unsupported"):
        buffer.append((10, np.zeros(2, dtype=np.int32)))  # type: ignore[arg-type]
    assert buffer.sample_count == 3


def test_buffer_downmixes_single_stereo_sample_and_preserves_column_mono() -> None:
    """A row with two channels is one sample; a column remains a mono frame."""
    buffer = AmbientAudioBuffer(max_seconds=1, sample_rate=10)

    buffer.append((10, np.array([[0.25, 0.5]], dtype=np.float32)))
    buffer.append((10, np.array([[0.25], [0.5]], dtype=np.float32)))

    np.testing.assert_array_equal(buffer.snapshot()[1], [12_287, 8_191, 16_383])


@pytest.mark.parametrize("channels_first", [False, True])
def test_buffer_downmixes_int16_stereo_layouts_without_rescaling(channels_first: bool) -> None:
    """Int16 channels are averaged in PCM units for either supported layout."""
    channels_last = np.array(
        [[1_000, 3_000], [-1_000, -3_000], [10_000, 20_000]],
        dtype=np.int16,
    )
    audio = channels_last.T.copy() if channels_first else channels_last.copy()
    buffer = AmbientAudioBuffer(max_seconds=1, sample_rate=10)

    buffer.append((10, audio))
    audio[...] = 0

    np.testing.assert_array_equal(buffer.snapshot()[1], [2_000, -2_000, 15_000])


def test_buffer_has_no_file_or_payload_logging_side_effects(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Buffering stays in memory and does not log sensitive PCM payloads."""
    sentinel = np.array([1234, -4321], dtype=np.int16)
    caplog.set_level(logging.DEBUG)
    buffer = AmbientAudioBuffer(max_seconds=1, sample_rate=10)
    buffer.append((10, sentinel))

    assert list(tmp_path.iterdir()) == []
    assert "1234" not in caplog.text
    assert "-4321" not in caplog.text


def test_buffer_adapts_to_first_captured_rate_and_bounds_twenty_seconds() -> None:
    """A real 48 kHz capture rate gets a full twenty-second capacity."""
    buffer = AmbientAudioBuffer()
    first = np.zeros(48_000, dtype=np.int16)
    second = np.ones(48_000, dtype=np.int16)

    buffer.append((48_000, first))
    buffer.append((48_000, second))

    assert buffer.sample_rate == 48_000
    assert buffer.max_samples == 960_000
    assert buffer.seconds_buffered == pytest.approx(2)


def test_adaptive_buffer_rate_change_rejects_without_mutating() -> None:
    """Once selected, the ring rejects a changed capture rate without mutation."""
    buffer = AmbientAudioBuffer(max_seconds=1)
    buffer.append((48_000, np.array([1, 2], dtype=np.int16)))

    with pytest.raises(ValueError, match="accepts 48000 Hz"):
        buffer.append((16_000, np.array([3], dtype=np.int16)))

    np.testing.assert_array_equal(buffer.snapshot()[1], [1, 2])


def test_clear_resets_adaptive_rate_for_a_fresh_capture() -> None:
    """Clearing the adaptive ring permits the next capture rate to select anew."""
    buffer = AmbientAudioBuffer(max_seconds=1)
    buffer.append((48_000, np.array([1], dtype=np.int16)))
    buffer.clear()
    buffer.append((16_000, np.array([2], dtype=np.int16)))

    assert buffer.sample_rate == 16_000
    np.testing.assert_array_equal(buffer.snapshot()[1], [2])
