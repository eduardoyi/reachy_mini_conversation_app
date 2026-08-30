import asyncio
from typing import TypeVar, TypeAlias
from collections.abc import Mapping, Callable

import numpy as np
from numpy.typing import NDArray


StreamMessage: TypeAlias = Mapping[str, object]
AudioArray: TypeAlias = NDArray[np.int16] | NDArray[np.float32]

QueueItem = TypeVar("QueueItem")


class AdditionalOutputs:
    """Text or metadata emitted alongside audio frames."""

    def __init__(self, *args: StreamMessage) -> None:
        """Initialize with one or more emitted messages."""
        self.args = args


class AsyncStreamHandler:
    """Minimal async stream handler state used by the local audio loop."""

    def __init__(self) -> None:
        """Initialize shared stream handler state."""
        self._clear_queue: Callable[[], None] | None = None


async def wait_for_item(queue: asyncio.Queue[QueueItem], timeout: float = 0.1) -> QueueItem | None:
    """Return the next queue item, or None when no item arrives before timeout."""
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def audio_to_int16(audio: AudioArray) -> NDArray[np.int16]:
    """Convert int16 or float32 audio data to int16 samples."""
    if audio.dtype == np.int16:
        return audio.astype(np.int16, copy=False)
    if audio.dtype == np.float32:
        return (audio * 32767.0).astype(np.int16)
    raise TypeError(f"Unsupported audio data type: {audio.dtype}")


def audio_to_float32(audio: AudioArray) -> NDArray[np.float32]:
    """Convert int16 or float32 audio data to float32 samples."""
    if audio.dtype == np.int16:
        return audio.astype(np.float32) / 32768.0
    if audio.dtype == np.float32:
        return audio.astype(np.float32, copy=False)
    raise TypeError(f"Unsupported audio data type: {audio.dtype}")


def audio_to_mono_int16(audio: AudioArray) -> NDArray[np.int16]:
    """Convert mono or interleaved multi-channel audio to copied mono PCM."""
    if audio.dtype not in (np.int16, np.float32):
        raise TypeError(f"Unsupported audio data type: {audio.dtype}")
    if audio.ndim == 0:
        raise ValueError("Audio must be a one-dimensional mono or two-dimensional multi-channel frame")
    if audio.ndim == 1:
        mono_audio = audio
    elif audio.ndim == 2:
        if audio.size == 0:
            return np.empty(0, dtype=np.int16)
        if audio.shape[1] == 1:
            mono_audio = audio.reshape(-1)
        elif audio.shape[0] == 1:
            mono_audio = np.mean(audio, axis=1, dtype=np.float32)
        else:
            # SDK capture is channels-last (samples, channels). Keep support
            # for channels-first frames when the sample axis is clearly longer.
            channel_axis = 1 if audio.shape[0] >= audio.shape[1] else 0
            mono_audio = np.mean(audio, axis=channel_axis, dtype=np.float32)
    else:
        raise ValueError("Audio must be a one-dimensional mono or two-dimensional multi-channel frame")

    if audio.dtype == np.int16:
        return np.array(
            np.clip(mono_audio, -32768, 32767),
            dtype=np.int16,
            copy=True,
        ).reshape(-1)
    return np.array(
        np.clip(mono_audio * 32767.0, -32768.0, 32767.0),
        dtype=np.int16,
        copy=True,
    ).reshape(-1)


def resample_audio(
    audio: NDArray[np.float32],
    input_sample_rate: int,
    output_sample_rate: int,
) -> NDArray[np.float32]:
    """Linearly resample a mono float32 frame to the requested sample rate."""
    if input_sample_rate <= 0 or output_sample_rate <= 0:
        raise ValueError("Audio sample rates must be positive")
    if audio.ndim != 1:
        raise ValueError("Audio resampling expects a one-dimensional frame")
    if input_sample_rate == output_sample_rate or audio.size == 0:
        return audio

    output_length = max(1, round(audio.size * output_sample_rate / input_sample_rate))
    source_positions = np.arange(output_length, dtype=np.float64) * input_sample_rate / output_sample_rate
    source_indices = np.arange(audio.size, dtype=np.float64)
    return np.interp(source_positions, source_indices, audio).astype(np.float32)
