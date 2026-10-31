"""Find the call in a recording of someone saying Reachy's name: when it is over, and which samples hold it."""

import math
from typing import Final

import numpy as np
from numpy.typing import NDArray


FRAME_S: Final[float] = 0.03
# A voice is this much louder than the quietest moments of the recording, and loud enough to be speech at all
# (a call from across the room is quieter than one nearby).
OVER_FLOOR: Final[float] = 3.0
MIN_RMS: Final[float] = 150.0
QUIET_AFTER_S: Final[float] = 0.7
MARGIN_S: Final[float] = 0.25
MAX_VOICE_S: Final[float] = 4.0


def _voiced_frames(audio: NDArray[np.int16], rate: int) -> NDArray[np.bool_]:
    frame = max(1, int(FRAME_S * rate))
    count = audio.size // frame
    if count == 0:
        return np.zeros(0, dtype=bool)
    frames = audio[: count * frame].astype(np.float64).reshape(count, frame)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    floor = float(np.percentile(rms, 5))
    voiced: NDArray[np.bool_] = rms >= max(floor * OVER_FLOOR, MIN_RMS)
    return voiced


def take_finished(audio: NDArray[np.int16], rate: int) -> bool:
    """Whether a voice was heard and has stopped for a moment (or has gone on too long)."""
    frames = _voiced_frames(audio, rate)
    voiced = np.flatnonzero(frames)
    if voiced.size == 0:
        return False
    quiet_after = (len(frames) - 1 - int(voiced[-1])) * FRAME_S
    return quiet_after >= QUIET_AFTER_S or int(voiced[-1] - voiced[0]) * FRAME_S > MAX_VOICE_S


def voiced_span(audio: NDArray[np.int16], rate: int) -> tuple[int, int] | None:
    """Return the samples from just before the voice to just after it, or None when nobody spoke."""
    voiced = np.flatnonzero(_voiced_frames(audio, rate))
    if voiced.size == 0:
        return None
    frame = max(1, int(FRAME_S * rate))
    margin = int(MARGIN_S * rate)
    return max(0, int(voiced[0]) * frame - margin), min(audio.size, (int(voiced[-1]) + 1) * frame + margin)


def level_db(audio: NDArray[np.int16]) -> float:
    """Return the loudness of ``audio`` in dB below full scale."""
    if audio.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    return 20 * math.log10(max(rms, 1.0) / 32768)
