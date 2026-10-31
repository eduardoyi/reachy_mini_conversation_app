"""Tests for the local wake clips and wake event records kept to measure and retrain the wake word."""

import json
import wave
import random
from pathlib import Path
from datetime import datetime

import numpy as np

from reachy_mini_conversation_app.wake_clips import EVENTS_FILE, BACKGROUND_EVERY_S, WakeClipRecorder


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


def _events(directory: Path, day: str = "2026-10-27") -> list[dict[str, object]]:
    return [json.loads(line) for line in (directory / day / EVENTS_FILE).read_text().splitlines()]


def _recorder(directory: Path, clock: Clock | None = None, keep_days: int = 14) -> WakeClipRecorder:
    return WakeClipRecorder(directory, keep_days=keep_days, clock=clock or Clock(), rng=random.Random(1))


def test_a_near_miss_saves_the_last_four_seconds_privately_and_logs_its_scores(tmp_path: Path) -> None:
    """The clip is the end of the buffer, readable only by its owner; the event line names it with its scores."""
    recorder = _recorder(tmp_path)
    audio = _ramp(6.0)
    recorder.feed(audio[: len(audio) // 2], RATE)
    recorder.feed(audio[len(audio) // 2 :], RATE)

    event_id = recorder.record("near_miss", score=0.27, head="reachy-official33")

    [clip] = _clips(tmp_path)
    assert f"{event_id}.wav" == f"{clip.parent.name}/{clip.name}"
    assert clip.name.endswith("-near_miss.wav")
    assert _read(clip) == (RATE, audio[-4 * RATE * 2 :])
    assert clip.stat().st_mode & 0o777 == 0o600
    [event] = _events(tmp_path)
    assert event == {
        "id": event_id,
        "t": START,
        "kind": "near_miss",
        "clip": f"{event_id}.wav",
        "event_at": 4.0,
        "score": 0.27,
        "head": "reachy-official33",
    }
    assert (tmp_path / "2026-10-27" / EVENTS_FILE).stat().st_mode & 0o777 == 0o600


def test_a_wake_keeps_ten_seconds_before_and_waits_for_the_one_and_a_half_after(tmp_path: Path) -> None:
    """The fire's clip is written once 1.5 s more has arrived, so it holds what was said after the name too."""
    recorder = _recorder(tmp_path)
    audio = _ramp(14.0)
    recorder.feed(audio[: 12 * RATE * 2], RATE)

    event_id = recorder.record("wake", score=0.93)
    recorder.feed(audio[12 * RATE * 2 : 13 * RATE * 2], RATE)
    assert _clips(tmp_path) == []
    recorder.feed(audio[13 * RATE * 2 :], RATE)

    [clip] = _clips(tmp_path)
    assert _read(clip)[1] == audio[2 * RATE * 2 : int(13.5 * RATE) * 2]
    assert _events(tmp_path)[0]["event_at"] == 10.0
    assert event_id.endswith("-wake")


def test_clearing_saves_a_waiting_clip_short_and_forgets_the_buffer(tmp_path: Path) -> None:
    """A conversation starting before the 1.5 s arrived still leaves the fire's clip; older audio is then gone."""
    recorder = _recorder(tmp_path)
    audio = _ramp(3.0)
    recorder.feed(audio, RATE)
    recorder.record("wake", score=0.9)

    recorder.clear()
    [clip] = _clips(tmp_path)
    assert _read(clip)[1] == audio

    recorder.record("near_miss", score=0.2)
    assert len(_clips(tmp_path)) == 1
    assert _events(tmp_path)[-1]["clip"] is None


def test_a_name_heard_clip_keeps_the_utterance_and_the_stt_delay_up_to_ten_seconds(tmp_path: Path) -> None:
    """Local STT reports the utterance late: its clip adds 3 s and never exceeds 10 s."""
    clock = Clock()
    recorder = _recorder(tmp_path, clock)
    recorder.feed(_ramp(12.0), RATE)

    recorder.save_name_heard(1.5)
    clock.now += 1
    recorder.save_name_heard(30.0)

    assert [len(_read(clip)[1]) / 2 / RATE for clip in _clips(tmp_path)] == [4.5, 10.0]
    assert [event["speech_seconds"] for event in _events(tmp_path)] == [1.5, 30.0]


def test_a_missed_call_keeps_the_minute_before_the_moment_it_names(tmp_path: Path) -> None:
    """Saying "you didn't hear me" after the wake keeps the minute that ended at the wake."""
    recorder = _recorder(tmp_path)
    audio = _ramp(70.0)
    recorder.feed(audio, RATE)

    recorder.record("missed", ended_ago_s=5.0, by="voice")

    [clip] = _clips(tmp_path)
    assert _read(clip)[1] == audio[5 * RATE * 2 : 65 * RATE * 2]
    [event] = _events(tmp_path)
    assert event["t"] == START - 5.0 and event["by"] == "voice" and event["event_at"] == 60.0


def test_near_miss_clips_are_rate_limited_but_every_near_miss_is_logged(tmp_path: Path) -> None:
    """A steady noise cannot fill the disk: one near-miss clip per 30 s, every event still in the record."""
    clock = Clock()
    recorder = _recorder(tmp_path, clock)
    recorder.feed(_ramp(5.0), RATE)

    for step in (0, 10, 31):
        clock.now = START + step
        recorder.record("near_miss", score=0.25)

    assert len(_clips(tmp_path)) == 2
    assert [event["clip"] is None for event in _events(tmp_path)] == [False, True, False]


def test_notes_join_their_event_by_id(tmp_path: Path) -> None:
    """What came of a fire goes into its day's record under its id."""
    clock = Clock()
    recorder = _recorder(tmp_path, clock)
    recorder.feed(_ramp(2.0), RATE)
    event_id = recorder.record("wake", score=0.5)
    clock.now += 2

    recorder.note(event_id, heard=False, outcome="rejected")

    assert _events(tmp_path)[-1] == {"id": event_id, "t": START + 2, "heard": False, "outcome": "rejected"}


def test_background_samples_come_only_when_due_and_while_waiting_for_the_name(tmp_path: Path) -> None:
    """Random background samples arrive within half to one and a half times the average gap, never in a request."""
    clock = Clock()
    recorder = _recorder(tmp_path, clock)

    clock.now = START + 0.5 * BACKGROUND_EVERY_S - 1
    recorder.feed(_ramp(5.0), RATE)
    assert _clips(tmp_path) == []

    clock.now = START + 1.5 * BACKGROUND_EVERY_S
    recorder.feed(_ramp(0.1), RATE, ambient=False)
    assert _clips(tmp_path) == []
    recorder.feed(_ramp(0.1), RATE)
    recorder.feed(_ramp(0.1), RATE)
    [clip] = _clips(tmp_path)
    assert clip.name.endswith("-background.wav")


def test_changing_sample_rate_forgets_earlier_audio(tmp_path: Path) -> None:
    """Audio at another rate never ends up in a clip."""
    recorder = _recorder(tmp_path)
    recorder.feed(_ramp(3.0), RATE)
    recorder.feed(_ramp(1.0, rate=48_000, start=7), 48_000)
    recorder.record("near_miss", score=0.2)

    [clip] = _clips(tmp_path)
    assert _read(clip) == (48_000, _ramp(1.0, rate=48_000, start=7))


def test_a_capture_collects_only_what_is_fed_while_it_runs(tmp_path: Path) -> None:
    """A recording of the name gets the chunks fed between its start and its end."""
    recorder = _recorder(tmp_path)
    recorder.feed(_ramp(1.0), RATE)
    recorder.begin_capture()
    recorder.feed(_ramp(0.5, start=3), RATE)
    rate, audio = recorder.captured()
    recorder.end_capture()
    recorder.feed(_ramp(0.5), RATE)

    assert (rate, audio) == (RATE, _ramp(0.5, start=3))
    assert recorder.captured() == (RATE, b"")


def test_day_folders_past_the_keep_period_are_deleted(tmp_path: Path) -> None:
    """Saving deletes older days; recent days and unrelated folders stay."""
    for name in ("2026-10-12", "2026-10-13", "2026-10-26", "notes"):
        (tmp_path / name).mkdir()
    recorder = _recorder(tmp_path, keep_days=14)
    recorder.feed(_ramp(1.0), RATE)

    recorder.record("near_miss", score=0.3)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["2026-10-13", "2026-10-26", "2026-10-27", "notes"]
