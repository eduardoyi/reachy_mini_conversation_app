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
    QUEUE_SIZE,
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


@pytest.mark.asyncio
async def test_the_name_check_takes_the_spellings_the_live_check_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whisper writes a Spanish "Ri-chi" as "richi"; the night counts it as the name, as the live check does."""
    heard = iter(["oye richi, ven aquí", "Ricky, come here"])

    class FakeSTT:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def transcribe_request(self, _audio: Any) -> str:
            return next(heard)

    monkeypatch.setattr(nightly.config, "LOCAL_STT_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(nightly.config, "LOCAL_WAKE_WORD", "Reachy")
    monkeypatch.setattr(nightly, "OpenAICompatibleLocalSTT", FakeSTT)
    check = nightly.speech_server_check()

    silence = np.zeros(RATE, dtype=np.int16)
    assert await check(RATE, silence) and not await check(RATE, silence)


def test_models_where_macos_would_ask_first_are_caught(tmp_path: Path) -> None:
    """Models in Documents, Desktop or Downloads would hold the night on a privacy prompt; elsewhere is fine."""
    assert nightly.behind_privacy_prompt(tmp_path / "Documents" / "Reachy" / "models", home=tmp_path)
    assert nightly.behind_privacy_prompt(tmp_path / "Downloads", home=tmp_path)
    assert not nightly.behind_privacy_prompt(tmp_path / ".local" / "share" / "wake-models", home=tmp_path)
    assert not nightly.behind_privacy_prompt(tmp_path / "DocumentsOld", home=tmp_path)


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


def test_tomorrows_clips_put_missed_calls_first_skip_answers_and_stop_at_thirty() -> None:
    """Missed calls lead, then antenna touches, then rejected fires with a name; answered clips never return."""
    events = [_event("wake", DAY + n, heard=False, outcome="rejected") for n in range(40)]
    events.append(_event("missed", DAY + 100, event_at=60.0))
    events.append(_event("antenna", DAY + 200, event_at=15.0))
    names = {event["id"]: [8.0] for event in events}
    answers = {f"{events[0]['id']}@8.00": "call"}

    items = review_items(events, names, answers, random.Random(1))

    assert len(items) == QUEUE_SIZE == 30
    assert items[0].question == "Is this the call he missed?" and items[1].question == "Did someone say his name here?"
    assert not items[0].follow_up and items[1].follow_up  # only a name question asks "to him or about him?"
    assert all(item.id != f"{events[0]['id']}@8.00" for item in items)
    assert (items[0].start, items[0].end) == (5.0, 9.5)


def test_transcript_wakes_the_trial_check_doubted_are_asked_about_fifteen_a_day() -> None:
    """Whether they were calls is what switching the check on would cost; the whole sentence is played.

    One the trial heard the name in is not asked about; one told "not to you" is asked as before, after them.
    """
    doubted = [_event("name_heard", DAY + n, outcome="woke", transcript_check=False, speech_seconds=3.0)
               for n in range(20)]  # fmt: skip
    confirmed = _event("name_heard", DAY + 50, outcome="woke", transcript_check=True, speech_seconds=3.0)
    told_off = _event("name_heard", DAY + 60, outcome="woke", transcript_check=False, said="not_called")
    events = [*doubted, confirmed, told_off]

    items = review_items(events, {}, {}, random.Random(1))

    asked, rest = items[:15], items[15:]
    assert {item.id.split("@")[0] for item in asked} <= {event["id"] for event in doubted}
    assert all((item.start, item.end, item.id.split("@")[1]) == (6.0, 11.5, "10.00") for item in asked)
    assert all(item.question == "Did someone say his name?" and item.follow_up for item in asked)
    assert [item.id.split("@")[0] for item in rest] == [told_off["id"]]


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


def test_his_name_said_about_him_is_his_name_but_no_call_and_no_false_wake() -> None:
    """Told "not to you" over his name, he was right to wake and wrong to answer: a mention, not a false wake.

    Told off, sent away or asked nothing with no name found, the night can't tell noise from a name it missed:
    unknown, a false wake for now, and asked about. Sent to sleep with the name in it, he was called to go to
    sleep. A clip the household said was about him is a mention too, not a missed call.
    """
    told = _event("wake", DAY + 100, heard=True, request_chars=20, said="not_called", by="voice")
    told_unnamed = _event("wake", DAY + 200, heard=True, request_chars=20, said="not_called", by="app")
    sent_away = _event("wake", DAY + 300, heard=True, request_chars=8, slept_after_s=4.0)
    called_to_sleep = _event("wake", DAY + 500, heard=True, request_chars=8, slept_after_s=3.0)
    empty = _event("wake", DAY + 700, heard=True, request_chars=0)
    talked_about = _event("wake", DAY + 900, heard=False, outcome="rejected")
    events = [told, told_unnamed, sent_away, called_to_sleep, empty, talked_about]
    names = {told["id"]: [9.8], called_to_sleep["id"]: [9.8], talked_about["id"]: [9.5]}
    answers = {f"{talked_about['id']}@9.50": "mention"}

    moments = day_moments(events, names, answers)
    score = day_score(events, names, answers)
    items = review_items(events, names, answers, random.Random(1))

    assert (moments.calls, moments.mentions, moments.not_calls) == ([DAY + 500], [DAY + 100, DAY + 899.5], [])
    assert moments.named == [DAY + 100, DAY + 500, DAY + 899.5]  # what a head should catch
    assert moments.unsure == [(DAY + 198.5, DAY + 200.5), (DAY + 298.5, DAY + 300.5), (DAY + 698.5, DAY + 700.5)]
    assert (score["calls"], score["false_wakes"], score["mentions"], score["spoke_about"]) == (1, 3, 2, 1)
    assert [item.id.split("@")[0] for item in items] == [told_unnamed["id"], sent_away["id"], empty["id"]]
    assert all(item.follow_up and item.question == "Did someone say his name?" for item in items)


def test_a_transcript_wake_gives_his_name_only_where_the_night_found_it() -> None:
    """A transcript wake is logged when its sentence ends, seconds after the name: its time is never his name.

    With the name located in its clip, that time is the call; without, the whole sentence is unknown, so it is
    neither trained on as his name nor sampled as quiet.
    """
    located = _event("name_heard", DAY + 100, outcome="woke", speech_seconds=6.0)  # clip starts at DAY + 90
    unlocated = _event("name_heard", DAY + 300, outcome="woke", speech_seconds=4.0)
    events = [located, unlocated]

    moments = day_moments(events, {located["id"]: [3.5]}, {})

    assert moments.calls == [DAY + 93.5]
    assert moments.unsure == [(DAY + 295.0, DAY + 300.5)]
    score = day_score(events, {located["id"]: [3.5]}, {})
    assert (score["calls"], score["first_time"]) == (2, 2)  # the name in its own sentence is not a missed call
    assert day_score(events, {located["id"]: [0.5]}, {})["first_time"] == 1  # one before the sentence is


def test_a_saved_training_set_joins_the_night_without_its_uncertain_windows(tmp_path: Path) -> None:
    """The base set's calls and non-calls are used; uncertain windows (-1), a missing file or a bad one are not."""
    path = tmp_path / "features.npz"
    np.savez(path, X=np.ones((3, 16, 96), np.float16), Y=np.array([1.0, 0.0, -1.0], np.float32))

    windows, labels = nightly.base_examples(path)

    assert windows.shape == (2, 16, 96) and labels.tolist() == [1.0, 0.0]
    assert nightly.base_examples(None)[0].shape == (0, 16, 96)
    assert nightly.base_examples(tmp_path / "missing.npz")[0].shape == (0, 16, 96)
    np.savez(path, X=np.ones((2, 8, 96), np.float16), Y=np.ones(2, np.float32))
    assert nightly.base_examples(path)[0].shape == (0, 16, 96)


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


def test_as_many_calls_on_clearly_fewer_fires_also_counts() -> None:
    """A switch takes a fifth fewer fires for the same calls; a shadow, a tenth. Fewer calls never do."""
    current = nightly.PairResult(caught=22, calls=39, fires_per_day=416.0)
    assert nightly.clearly_better(nightly.PairResult(22, 39, 332.0), current)
    assert not nightly.clearly_better(nightly.PairResult(22, 39, 360.0), current)
    assert nightly.better(nightly.PairResult(22, 39, 360.0), current, 1, nightly.SHADOW_FIRES_SHARE)
    assert not nightly.better(nightly.PairResult(21, 39, 100.0), current, 1, nightly.SHADOW_FIRES_SHARE)


def test_a_tie_on_calls_takes_the_threshold_that_fires_less() -> None:
    """Two thresholds that catch the same call: the stricter, quieter one is chosen."""
    ends = np.arange(0, 600, FRAME_S)
    extra = np.zeros(len(ends), np.float32)
    extra[(ends > 100) & (ends < 101)] = 0.97  # a false fire only the looser thresholds pass
    extra[(ends > 300) & (ends < 300.5)] = 0.99  # the call
    scores = {"m": np.zeros(len(ends), np.float32), "x": extra}
    day = (DayScores(ends, 16.0, scores), DayMoments(calls=[300.0]))
    choice = nightly.choose_threshold([day], "m", 0.3, "x", fire_budget=100.0)
    assert choice is not None and choice[0] == 0.98 and choice[1].caught == 1


def test_a_head_threshold_keeps_it_out_of_the_trusted_shortcut() -> None:
    """With the main threshold at 0.3, an extra head below 0.375 could reach a 'sure' 0.8: never chosen."""
    ends = np.arange(0, 60, FRAME_S)
    scores = {"m": np.zeros(len(ends), np.float32), "x": np.full(len(ends), 0.3, np.float32)}
    day = (DayScores(ends, 16.0, scores), DayMoments(calls=[30.0]))
    choice = nightly.choose_threshold([day], "m", 0.3, "x", fire_budget=100.0)
    assert choice is None or choice[0] >= 0.4


def test_a_challenger_is_tuned_on_the_newest_two_days_and_trained_on_the_rest() -> None:
    """One day chose a threshold that did not hold the next (2026-11-04); two days, and one is always left to train."""
    week = ["2026-11-03", "2026-10-31", "2026-11-04", "2026-11-01"]
    assert nightly.dev_days(week) == ["2026-11-03", "2026-11-04"]
    assert nightly.dev_days(["2026-11-01", "2026-11-02"]) == ["2026-11-02"]
    assert nightly.dev_days(["2026-11-01"]) == []


def test_a_challenger_nearer_the_live_pair_replaces_a_shadow_that_misses_calls() -> None:
    """A quiet shadow behind the live pair gives way to more calls within the live pair's fires (2026-11-05)."""
    live, shadow = nightly.PairResult(46, 76, 352.2), nightly.PairResult(37, 76, 230.7)
    assert nightly.closer_to_live(nightly.PairResult(44, 76, 307.3), shadow, live)
    assert not nightly.closer_to_live(nightly.PairResult(44, 76, 400.0), shadow, live)  # fires more than live
    assert not nightly.closer_to_live(nightly.PairResult(36, 76, 200.0), shadow, live)  # fewer calls than the shadow
    assert not nightly.closer_to_live(nightly.PairResult(47, 76, 300.0), nightly.PairResult(46, 76, 200.0), live)
