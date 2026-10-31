"""Tests for the nightly wake-learning job: labels, scoring heads over whole days, switches, clips to check."""

import random
from typing import Any
from pathlib import Path
from datetime import date, datetime

import numpy as np
import pytest

from reachy_mini_conversation_app.wake_clips import WakeClipRecorder
from reachy_mini_conversation_app.wake_learning import nightly
from reachy_mini_conversation_app.wake_learning.store import HeadRecord, WakeLearningStore
from reachy_mini_conversation_app.wake_learning.nightly import (
    DayScores,
    DayMoments,
    NightSettings,
    caught,
    day_score,
    run_night,
    count_fires,
    day_moments,
    locate_names,
    review_items,
)
from reachy_mini_conversation_app.wake_learning.feature_log import FRAME_S, WakeFeatureLog


RATE = 16000
DAY = datetime(2026, 10, 30, 10, 0, 0).timestamp()
MARK = 9000  # where a test clip "says" the name


async def _hears_mark(_rate: int, audio: np.ndarray) -> bool:
    return bool(audio.size) and int(audio.max()) >= MARK


def _event(kind: str, at: float, **details: Any) -> dict[str, Any]:
    return {"id": f"2026-10-30/{at:.0f}-{kind}", "t": at, "kind": kind, "clip": f"2026-10-30/{at:.0f}.wav",
            "event_at": 10.0, **details}  # fmt: skip


@pytest.mark.asyncio
async def test_names_are_located_where_they_end() -> None:
    """Each name in a clip is found once, at about where it ends; a clip without one is not searched."""
    audio = np.zeros(8 * RATE, dtype=np.int16)
    audio[2 * RATE : int(2.4 * RATE)] = MARK
    audio[6 * RATE : int(6.4 * RATE)] = MARK

    ends = await locate_names(_hears_mark, RATE, audio)

    assert len(ends) == 2
    assert 2.0 <= ends[0] <= 2.6 and 6.0 <= ends[1] <= 6.6
    assert await locate_names(_hears_mark, RATE, np.zeros(4 * RATE, dtype=np.int16)) == []


def test_moments_come_from_wakes_names_and_answers_and_answers_win() -> None:
    """A woke fire is a call; a rejected one with no name is not; a name the check found is; people overrule."""
    woke = _event("wake", DAY + 100, heard=True)
    rejected = _event("wake", DAY + 300, heard=False, outcome="rejected")
    named = _event("wake", DAY + 500, heard=False, outcome="rejected")
    denied = _event("wake", DAY + 700, heard=False, outcome="rejected")
    missed = _event("missed", DAY + 900, event_at=60.0)
    names = {named["id"]: [9.5], denied["id"]: [9.5]}
    answers = {f"{denied['id']}@9.50": "not_call"}

    moments = day_moments([woke, rejected, named, denied, missed], names, answers)

    assert moments.calls == [DAY + 100, DAY + 499.5]
    assert moments.not_calls == [DAY + 300, DAY + 699.5, DAY + 700]
    assert moments.unsure == [(DAY + 840, DAY + 900)]


def test_fires_count_once_per_cooldown_and_never_around_a_call() -> None:
    """A long score over the threshold is one fire; fires near a call are its repeats, not false fires."""
    ends = np.arange(0, 100, FRAME_S)
    scores = np.zeros(len(ends), dtype=np.float32)
    scores[(ends > 10) & (ends < 12)] = 0.9  # one fire
    scores[(ends > 50) & (ends < 50.5)] = 0.9  # next to a call
    scores[(ends > 80) & (ends < 80.2)] = 0.9

    assert count_fires(ends, scores, 0.5, skip=[55.0]) == 2
    assert caught(ends, scores, 0.5, [50.2, 30.0, 500.0]) == (1, 2)


def test_tomorrows_clips_put_missed_calls_first_skip_answers_and_stop_at_ten() -> None:
    """Missed calls lead, then antenna touches, then rejected fires with a name; answered clips never return."""
    events = [_event("wake", DAY + n, heard=False, outcome="rejected") for n in range(20)]
    events.append(_event("missed", DAY + 100, event_at=60.0))
    events.append(_event("antenna", DAY + 200, event_at=15.0))
    names = {event["id"]: [8.0] for event in events}
    answers = {f"{events[0]['id']}@8.00": "call"}

    items = review_items(events, names, answers, random.Random(1))

    assert len(items) == 10
    assert items[0].question == "Is this the call he missed?" and items[1].question == "Did someone call Reachy here?"
    assert all(item.id != f"{events[0]['id']}@8.00" for item in items)
    assert (items[0].start, items[0].end) == (5.0, 9.5)


def test_the_score_counts_episodes_answered_at_once_and_false_wakes() -> None:
    """A wake that came first answers its episode; a missed mark or an earlier name means it did not.

    An empty wake nobody vouched for is a false one.
    """
    answered = _event("wake", DAY + 100, heard=True, request_chars=12)
    late = _event("wake", DAY + 300, heard=True, request_chars=9)
    empty = _event("wake", DAY + 500, heard=True, request_chars=0, tv=True)
    missed = _event("missed", DAY + 700, event_at=60.0)
    names = {late["id"]: [4.0], missed["id"]: [50.0]}  # a name 6 s before the wake that answered

    score = day_score([answered, late, empty, missed], names, {})

    assert (score["calls"], score["first_time"], score["false_wakes"]) == (3, 1, 1)
    assert score["by_condition"]["no_tv"] == {"calls": 3, "first_time": 1}


class FakeHead:
    """A head whose score is one feature of the window's last frame."""

    COLUMNS = {"reachy-official33": 0, "reachy-richi": 1, "house-20261029": 2}

    def __init__(self, path: Path) -> None:
        """Stand in for the head file at ``path``."""
        self.path = path
        self.name = path.stem

    def scores(self, windows: np.ndarray) -> np.ndarray:
        """Return the feature this head stands for."""
        return np.asarray(windows[:, -1, self.COLUMNS[self.name]], dtype=np.float32)


@pytest.mark.asyncio
async def test_a_night_seeds_the_live_head_scores_both_and_offers_the_shadow_that_caught_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shadow head that caught three more calls than the live pair, firing no more, is offered for a switch.

    The night also seeds the live head from the settings and writes the clips to check and the scoreboard.
    """
    monkeypatch.setattr(nightly, "Head", FakeHead)
    clips, features = tmp_path / "clips", tmp_path / "features"
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "reachy-official33.onnx").write_bytes(b"main")
    seed = tmp_path / "reachy-richi.onnx"
    seed.write_bytes(b"richi")
    store = WakeLearningStore(tmp_path / "learning", clips=clips)
    shadow = HeadRecord("house-20261029", 0.9, "sha", "2026-10-29T02:30:00", {})
    store.head_path(shadow).parent.mkdir(parents=True)
    store.head_path(shadow).write_bytes(b"shadow")
    store.set_shadow(shadow)

    now = [DAY]
    recorder = WakeClipRecorder(clips, keep_days=14, clock=lambda: now[0], rng=random.Random(1))
    log = WakeFeatureLog(features, keep_days=30, clock=lambda: now[0])
    calls = {DAY + 30: "woke", DAY + 90: "missed", DAY + 150: "missed", DAY + 210: "missed"}
    for step in range(int(240 / FRAME_S)):
        now[0] = DAY + step * FRAME_S
        audio = np.zeros(1280, dtype=np.int16)
        frame = np.zeros(96, dtype=np.float32)
        for at, how in calls.items():
            if at - 0.5 <= now[0] <= at + 0.3:
                audio[:] = MARK
                frame[2] = 0.95  # the shadow head hears every call
                if how == "woke":
                    frame[0] = 0.9
        recorder.feed(audio.tobytes(), RATE)
        log.add(frame)
        for at, how in calls.items():
            if abs(now[0] - (at + 0.4)) < FRAME_S / 2:
                fire = recorder.record("wake", score=0.9 if how == "woke" else 0.31)
                recorder.note(fire, heard=how == "woke", trusted=False)
                if how != "woke":
                    recorder.note(fire, outcome="rejected")
    for _ in range(40):
        now[0] += FRAME_S
        recorder.feed(np.zeros(1280, dtype=np.int16).tobytes(), RATE)

    settings = NightSettings(
        store=store, features_dir=features, model_dir=model_dir, main_threshold=0.3, trainer_python=None,
        seed_head=seed, seed_threshold=0.9, today=date(2026, 10, 31),
    )  # fmt: skip
    summary = await run_night(settings, _hears_mark)

    assert store.live() is not None and store.live().name == "reachy-richi"  # type: ignore[union-attr]
    assert summary["live"]["calls"] == 4 and summary["live"]["calls_caught"] == 1
    assert summary["shadow"]["calls_caught"] == 4
    proposal = store.proposal()
    assert proposal is not None and proposal["head"] == "house-20261029"
    assert proposal["summary"]["live_caught"] == 1 and proposal["summary"]["shadow_caught"] == 4
    assert len(store.queue()) == 3
    board = store.scoreboard()
    assert board["week"]["calls"] == 4 and board["week"]["first_time"] == 1
    assert board["shadow"]["name"] == "house-20261029"


def test_a_clear_gain_needs_more_than_noise() -> None:
    """One more call is not enough to switch; three more, with no more fires, is."""
    current = nightly.PairResult(caught=30, calls=41, fires_per_day=10.0)
    assert not nightly.clearly_better(nightly.PairResult(31, 41, 10.0), current)
    assert nightly.clearly_better(nightly.PairResult(33, 41, 10.2), current)
    assert not nightly.clearly_better(nightly.PairResult(35, 41, 14.0), current)


def test_a_head_threshold_keeps_it_out_of_the_trusted_shortcut() -> None:
    """With the main threshold at 0.3, an extra head below 0.375 could reach a 'sure' 0.8: never chosen."""
    ends = np.arange(0, 60, FRAME_S)
    scores = {"m": np.zeros(len(ends), np.float32), "x": np.full(len(ends), 0.3, np.float32)}
    day = (DayScores(ends, 16.0, scores), DayMoments(calls=[30.0]))
    choice = nightly.choose_threshold([day], "m", 0.3, "x", fire_budget=100.0)
    assert choice is None or choice[0] >= 0.4
