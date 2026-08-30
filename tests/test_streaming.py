import asyncio

import numpy as np
import pytest

from reachy_mini_conversation_app.streaming import (
    AdditionalOutputs,
    wait_for_item,
    audio_to_int16,
    resample_audio,
    audio_to_float32,
)


def test_additional_outputs_stores_messages() -> None:
    """Additional outputs should keep the emitted role/content payloads."""
    message = {"role": "assistant", "content": "hello"}

    outputs = AdditionalOutputs(message)

    assert outputs.args == (message,)


@pytest.mark.asyncio
async def test_wait_for_item_returns_none_on_timeout() -> None:
    """Queue waits should time out without raising."""
    queue: asyncio.Queue[int] = asyncio.Queue()

    assert await wait_for_item(queue, timeout=0.01) is None


def test_audio_to_int16_scales_float32() -> None:
    """Float audio should scale into the int16 sample range."""
    audio = np.array([-1.0, 0.0, 1.0], dtype=np.float32)

    np.testing.assert_array_equal(audio_to_int16(audio), np.array([-32767, 0, 32767], dtype=np.int16))


def test_audio_to_float32_scales_int16() -> None:
    """Int16 audio should scale into the float sample range."""
    audio = np.array([-32768, 0, 32767], dtype=np.int16)

    np.testing.assert_allclose(audio_to_float32(audio), np.array([-1.0, 0.0, 32767 / 32768], dtype=np.float32))


def test_audio_converters_reject_unsupported_dtype() -> None:
    """Unsupported audio dtypes should fail explicitly."""
    audio = np.array([1.0], dtype=np.float64)

    with pytest.raises(TypeError, match="Unsupported audio data type"):
        audio_to_int16(audio)


def test_resample_audio_24khz_to_16khz_preserves_duration_and_content() -> None:
    """Provider output should be linearly converted to the SDK playback rate."""
    audio = np.arange(6, dtype=np.float32)

    resampled = resample_audio(audio, 24_000, 16_000)

    assert resampled.shape == (4,)
    np.testing.assert_allclose(resampled, np.array([0.0, 1.5, 3.0, 4.5], dtype=np.float32))


def test_resample_audio_same_rate_returns_original_frame() -> None:
    """Frames already at the SDK rate should avoid an unnecessary copy."""
    audio = np.arange(4, dtype=np.float32)

    assert resample_audio(audio, 16_000, 16_000) is audio


@pytest.mark.parametrize("input_rate, output_rate", [(0, 16_000), (16_000, 0), (-1, 16_000)])
def test_resample_audio_rejects_invalid_sample_rates(input_rate: int, output_rate: int) -> None:
    """Invalid device/provider rates must fail before interpolation."""
    audio = np.zeros(4, dtype=np.float32)

    with pytest.raises(ValueError, match="sample rates must be positive"):
        resample_audio(audio, input_rate, output_rate)
