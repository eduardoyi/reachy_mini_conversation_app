"""Tests for the wake-learning records shared by the app, the nightly job and the admin app."""

import wave
import random
from pathlib import Path
from datetime import datetime

import pytest

from reachy_mini_conversation_app.wake_clips import WakeClipRecorder
from reachy_mini_conversation_app.wake_learning.store import HeadRecord, ReviewItem, WakeLearningStore


def _store(tmp_path: Path) -> WakeLearningStore:
    return WakeLearningStore(tmp_path / "learning", clips=tmp_path / "clips")


def _head(store: WakeLearningStore, name: str, threshold: float = 0.9) -> HeadRecord:
    record = HeadRecord(name, threshold, "sha", "2026-10-30T02:00:00", {"calls_caught": 3})
    store.head_path(record).parent.mkdir(parents=True, exist_ok=True)
    store.head_path(record).write_bytes(name.encode())
    return record


def test_answered_clips_leave_the_queue_and_the_latest_answer_counts(tmp_path: Path) -> None:
    """The phone sees only clips nobody answered; changing an answer keeps the newest."""
    store = _store(tmp_path)
    store.write_queue([ReviewItem(f"d/{n}@1.00", "d/x.wav", 0.0, 4.5, "Did someone call Reachy?") for n in "abc"])

    store.answer("d/a@1.00", "call", "eduardo")
    store.answer("d/a@1.00", "not_call", "adriana")

    assert [item.id for item in store.queue()] == ["d/b@1.00", "d/c@1.00"]
    assert store.answers() == {"d/a@1.00": "not_call"}
    with pytest.raises(ValueError):
        store.answer("d/b@1.00", "maybe", "eduardo")
    assert (store.root / "answers.jsonl").stat().st_mode & 0o777 == 0o600


def test_a_clip_to_check_is_cut_to_its_seconds_and_stays_inside_the_clips(tmp_path: Path) -> None:
    """Only the item's seconds are sent to the phone, and only from the clip folder."""
    store = _store(tmp_path)
    clip = tmp_path / "clips" / "2026-10-30" / "100000000-wake.wav"
    clip.parent.mkdir(parents=True)
    with wave.open(str(clip), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(range(256)) * 250)  # 2 s

    wav = store.clip_audio(ReviewItem("i", "2026-10-30/100000000-wake.wav", 0.5, 1.5, "q"))
    assert len(wav) == 44 + 16000 * 2

    with pytest.raises(FileNotFoundError):
        store.clip_audio(ReviewItem("i", "../learning/answers.jsonl", 0.0, 1.0, "q"))


def test_a_switch_makes_the_shadow_live_and_undo_puts_the_old_head_back(tmp_path: Path) -> None:
    """Switching needs a proposal for the current shadow; undo restores the head it replaced."""
    store = _store(tmp_path)
    store.set_live(_head(store, "reachy-richi"))
    with pytest.raises(ValueError):
        store.switch("eduardo")
    store.set_shadow(_head(store, "house-20261030", 0.7))
    store.propose({"calls": 41, "live_caught": 30, "shadow_caught": 35})

    switched = store.switch("eduardo")

    assert switched.name == "house-20261030" and store.live() == switched
    assert store.shadow() is None and store.proposal() is None
    assert store.undoable() is not None
    restored = store.undo("adriana")
    assert restored.name == "reachy-richi" and store.live() == restored
    assert store.undoable() is None
    assert [entry.get("undo", False) for entry in store.history()] == [True, False]


def test_not_now_stands_until_the_numbers_change(tmp_path: Path) -> None:
    """A declined proposal is not offered again with the same numbers, but new numbers ask again."""
    store = _store(tmp_path)
    store.set_shadow(_head(store, "house-20261030"))
    store.propose({"calls": 41, "shadow_caught": 35})
    store.mark_proposal_notified()
    store.decline("eduardo")

    store.propose({"calls": 41, "shadow_caught": 35})
    assert store.proposal() is not None and store.proposal()["declined"] is True  # type: ignore[index]

    store.propose({"calls": 50, "shadow_caught": 44})
    assert store.proposal() is not None and not store.proposal().get("declined")  # type: ignore[union-attr]
    assert store.proposal()["notified"] is False  # type: ignore[index]


def test_recordings_are_kept_per_person_and_counted(tmp_path: Path) -> None:
    """Each take is an owner-only WAV under the person's folder, listed with its prompt."""
    store = _store(tmp_path)
    store.save_recording("Lucía", "from the kitchen", 16000, b"\x01\x00" * 8000)
    store.save_recording("Lucía", "louder", 16000, b"\x01\x00" * 8000)
    store.save_recording("Adriana", "quietly", 16000, b"\x01\x00" * 4000)

    counts = store.recordings_by_person()
    assert {person: summary["count"] for person, summary in counts.items()} == {"Lucía": 2, "Adriana": 1}
    [first, *_] = store.all_recordings()
    assert first["prompt"] == "from the kitchen" and first["seconds"] == 0.5
    path = store.recordings / first["file"]
    assert path.parent.name == "lucía" and path.stat().st_mode & 0o777 == 0o600


def test_events_come_back_with_their_notes_merged(tmp_path: Path) -> None:
    """A fire's later notes (name check, outcome) join it; the fire keeps its own time."""
    clock_now = [datetime(2026, 10, 30, 10, 0, 0).timestamp()]
    recorder = WakeClipRecorder(tmp_path / "clips", keep_days=14, clock=lambda: clock_now[0], rng=random.Random(1))
    recorder.feed(b"\x00\x01" * 16000, 16000)
    fire = recorder.record("wake", score=0.9)
    clock_now[0] += 5
    recorder.note(fire, heard=False, outcome="rejected")
    recorder.record("near_miss", score=0.2)

    store = _store(tmp_path)
    assert store.days() == ["2026-10-30"]
    first, second = store.events("2026-10-30")
    assert (first["kind"], first["heard"], first["outcome"]) == ("wake", False, "rejected")
    assert first["t"] < second["t"]
