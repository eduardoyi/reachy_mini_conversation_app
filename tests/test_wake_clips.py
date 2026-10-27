"""Tests for the local wake clips saved to retrain the wake word."""

import wave
import random
from pathlib import Path
from datetime import datetime

import numpy as np

from reachy_mini_conversation_app.wake_clips import BACKGROUND_EVERY_S, WakeClipRecorder


START = datetime(2026, 10, 27, 14, 0, 0).timestamp()
RATE = 16_000


class Clock:
    """A settable wall clock."""

    def __init__(self) -> None:
        """Start at a fixed afternoon."""
        self.now = START

    def __call__(self) -> float:
        """Return the current time."""
        return self.now


def _ramp(seconds: float, rate: int = RATE, start: int = 0) -> bytes:
    return (np.arange(start, start + int(seconds * rate)) % 30_000).astype("<i2").tobytes()


def _clips(directory: Path) -> list[Path]:
    return sorted(directory.glob("*/*.wav"))


def _read(path: Path) -> tuple[int, bytes]:
    with wave.open(str(path), "rb") as clip:
        assert clip.getnchannels() == 1 and clip.getsampwidth() == 2
        return clip.getframerate(), clip.readframes(clip.getnframes())


def _recorder(directory: Path, clock: Clock | None = None, keep_days: int = 14) -> WakeClipRecorder:
    return WakeClipRecorder(directory, keep_days=keep_days, clock=clock or Clock(), rng=random.Random(1))


def test_a_near_miss_saves_the_last_four_seconds_privately(tmp_path: Path) -> None:
    """The clip is the end of the buffer, named by kind and score, readable only by its owner."""
    recorder = _recorder(tmp_path)
    audio = _ramp(6.0)
    recorder.feed(audio[: len(audio) // 2], RATE)
    recorder.feed(audio[len(audio) // 2 :], RATE)

    recorder.save_wake_event(0.57, False)

    [clip] = _clips(tmp_path)
    assert clip.parent.name == "2026-10-27"
    assert clip.name.endswith("-near_miss-0.57.wav")
    assert _read(clip) == (RATE, audio[-4 * RATE * 2 :])
    assert clip.stat().st_mode & 0o777 == 0o600


def test_a_wake_and_a_name_heard_clip_have_their_own_kinds_and_lengths(tmp_path: Path) -> None:
    """A wake keeps 4 s; a name heard by local STT keeps the utterance plus the STT delay, at most 10 s."""
    clock = Clock()
    recorder = _recorder(tmp_path, clock)
    recorder.feed(_ramp(12.0), RATE)

    for save in (lambda: recorder.save_wake_event(0.99, True), lambda: recorder.save_name_heard(1.5)):
        save()
        clock.now += 1
    recorder.save_name_heard(30.0)

    saved = [(clip.name.split("-", 1)[1], len(_read(clip)[1]) / 2 / RATE) for clip in _clips(tmp_path)]
    assert saved == [("wake-0.99.wav", 4.0), ("name_heard.wav", 4.5), ("name_heard.wav", 10.0)]


def test_background_samples_come_only_when_due(tmp_path: Path) -> None:
    """Random background samples arrive within half to one and a half times the average gap."""
    clock = Clock()
    recorder = _recorder(tmp_path, clock)

    clock.now = START + 0.5 * BACKGROUND_EVERY_S - 1
    recorder.feed(_ramp(5.0), RATE)
    assert _clips(tmp_path) == []

    clock.now = START + 1.5 * BACKGROUND_EVERY_S
    recorder.feed(_ramp(0.1), RATE)
    recorder.feed(_ramp(0.1), RATE)
    [clip] = _clips(tmp_path)
    assert clip.name.endswith("-background.wav")


def test_clearing_or_changing_sample_rate_forgets_earlier_audio(tmp_path: Path) -> None:
    """Audio from before a new listening stretch, or at another rate, never ends up in a clip."""
    recorder = _recorder(tmp_path)
    recorder.feed(_ramp(3.0), RATE)
    recorder.clear()
    recorder.save_wake_event(0.9, True)
    assert _clips(tmp_path) == []

    recorder.feed(_ramp(3.0), RATE)
    recorder.feed(_ramp(1.0, rate=48_000, start=7), 48_000)
    recorder.save_wake_event(0.9, True)
    [clip] = _clips(tmp_path)
    assert _read(clip) == (48_000, _ramp(1.0, rate=48_000, start=7))


def test_day_folders_past_the_keep_period_are_deleted(tmp_path: Path) -> None:
    """Saving deletes older days; recent days and unrelated folders stay."""
    for name in ("2026-10-12", "2026-10-13", "2026-10-26", "notes"):
        (tmp_path / name).mkdir()
    recorder = _recorder(tmp_path, keep_days=14)
    recorder.feed(_ramp(1.0), RATE)

    recorder.save_wake_event(0.3, False)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["2026-10-13", "2026-10-26", "2026-10-27", "notes"]
