"""The nightly wake-learning job (``reachy-wake-nightly``, run by a LaunchAgent during quiet hours).

1. Label: the local speech server checks each kept clip for the name and where it falls (booleans and times,
   never words). The household's answers on the phone overrule it.
2. Score: the live and shadow heads, next to the main model, over every hour of the feature log: how many calls
   each pair would have caught and how often it would have fired on everything else.
3. Offer a switch when the shadow pair caught clearly more calls and fired no more, or as many calls on a fifth
   fewer fires, over a full day it ran beside the live pair.
4. Train a challenger from the live head's weights on all labelled days but the newest two; its threshold is
   chosen on those two, and it becomes the next shadow head if it beats the current one there (more calls
   without more fires, or as many calls on clearly fewer fires), or catches more than a shadow that is behind
   the live pair.
5. Pick tomorrow's clips to check, and write the scoreboard.

Everything stays on this computer; training runs in a separate Python with PyTorch (``WAKE_TRAINER_PYTHON``).
"""

import os
import sys
import json
import time
import wave
import random
import asyncio
import hashlib
import logging
import importlib
import subprocess
from typing import Any, Final, Protocol
from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import field, dataclass
from collections.abc import Callable, Iterable, Awaitable

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from reachy_mini_conversation_app.utils import behind_privacy_prompt
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector, transcript_aliases
from reachy_mini_conversation_app.wake_clips import make_private_dir
from reachy_mini_conversation_app.admin.server import restart_app
from reachy_mini_conversation_app.wake_learning.store import (
    HeadRecord,
    ReviewItem,
    WakeLearningStore,
    write_private,
    wake_features_directory,
    wake_learning_directory,
)
from reachy_mini_conversation_app.local_audio_backends import OpenAICompatibleLocalSTT
from reachy_mini_conversation_app.wake_learning.feature_log import FRAME_S, FeatureRun, read_runs


logger = logging.getLogger(__name__)

# Repeats of one call ("Ricci! Ricci! Ricci!") and every clip of that moment fall within this.
CALL_GAP_S: Final[float] = 20.0
# A name located this long before the fire that woke him was an earlier call he missed.
EARLIER_CALL_S: Final[float] = 2.5
# Days of a typical household: fires are reported per 16 hours of listening.
LISTENING_DAY_H: Final[float] = 16.0
COOLDOWN_S: Final[float] = 3.0
# Main-head threshold at which the extra head's scaled score would count as a "trusted" wake (hybrid_runtime);
# the extra head's own threshold stays above main/0.8 so it never can.
TRUSTED_SCORE: Final[float] = 0.8
EXTRA_THRESHOLDS: Final[tuple[float, ...]] = (0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.96, 0.97, 0.98, 0.985, 0.99)
# A switch is offered only for a clear gain: this many more calls caught (and at least 5% of them) without
# firing more, or as many calls caught with at most this share of the fires.
SWITCH_MARGIN_CALLS: Final[int] = 2
SWITCH_FIRES_SHARE: Final[float] = 0.8
# A challenger becomes the shadow head when it catches more without firing more, or as many with this share.
SHADOW_FIRES_SHARE: Final[float] = 0.9
QUEUE_SIZE: Final[int] = 10
NEGATIVE_STRIDE: Final[int] = 8
NEGATIVES_PER_DAY: Final[int] = 60_000
# Room for a week of the house next to a base set (the Richie head's set and 250,000 ACAV100M windows): about
# 1.8 GB of float16 windows, which the trainer turns into float32 a batch at a time.
MAX_NEGATIVES: Final[int] = 600_000
RECORDING_COPIES: Final[int] = 3
# The name check finds where the name ends to within a quarter second; the main head reacts about 0.4 s
# after that (checked on 140 recordings of the name). Calls are labelled where the head can tell, not before.
NAME_LABEL_SHIFT_S: Final[float] = 0.4
# A threshold chosen on one day did not hold: house-20261104 matched the live pair's 22 calls on 2026-11-03,
# where it was chosen, and caught 15 to the live pair's 24 on 2026-11-04. Challengers are tuned on this many.
DEV_DAYS: Final[int] = 2
# Name checks: the whole clip in pieces of at most this, then 1.5 s windows every 0.25 s to find where it is.
CHECK_PIECE_S: Final[float] = 10.0
LOCATE_WINDOW_S: Final[float] = 1.5
LOCATE_STEP_S: Final[float] = 0.25
LABELLED_KINDS: Final[frozenset[str]] = frozenset({"wake", "shadow", "antenna", "missed", "name_heard"})
MAIN_HEAD: Final[str] = "reachy-official33.onnx"
# What the phone asks; a yes to any but the missed-call question then asks "to him or about him?".
NAME_QUESTION: Final[str] = "Did someone say his name?"
MISSED_QUESTION: Final[str] = "Is this the call he missed?"


class NameCheck(Protocol):
    """Whether a stretch of audio has Reachy's name in it."""

    async def __call__(self, rate: int, audio: NDArray[np.int16]) -> bool:
        """Check ``audio`` for the name."""


def speech_server_check() -> NameCheck:
    """Check for the name with the local speech server's accurate model; no words are kept."""
    if not config.LOCAL_STT_BASE_URL:
        raise RuntimeError("LOCAL_STT_BASE_URL is not set")
    stt = OpenAICompatibleLocalSTT(
        config.LOCAL_STT_BASE_URL, model=config.LOCAL_STT_MODEL, request_model=config.LOCAL_STT_REQUEST_MODEL
    )
    # The spellings the live check takes: Whisper writes a Spanish "Ri-chi" as "richi" far more often than "Reachy".
    names = TranscriptWakeWordDetector(config.LOCAL_WAKE_WORD, aliases=transcript_aliases(config.LOCAL_WAKE_WORD))

    async def check(rate: int, audio: NDArray[np.int16]) -> bool:
        return names.mentions_wake_word(await stt.transcribe_request((rate, audio)))

    return check


def _read_wav(path: Path) -> tuple[int, NDArray[np.int16]]:
    with wave.open(str(path), "rb") as clip:
        return clip.getframerate(), np.frombuffer(clip.readframes(clip.getnframes()), dtype="<i2").copy()


async def locate_names(check: NameCheck, rate: int, audio: NDArray[np.int16]) -> list[float]:
    """Return where each name in ``audio`` ends (seconds), or an empty list when there is none."""
    piece, step = int(CHECK_PIECE_S * rate), int((CHECK_PIECE_S - LOCATE_WINDOW_S) * rate)
    if not any([await check(rate, audio[start : start + piece]) for start in range(0, max(1, audio.size - 1), step)]):
        return []
    window, hop = int(LOCATE_WINDOW_S * rate), int(LOCATE_STEP_S * rate)
    ends: list[float] = []
    previous = False
    for end in range(window, audio.size + 1, hop):
        heard = await check(rate, audio[end - window : end])
        if heard and not previous:
            # The name ends inside the first window that holds all of it.
            ends.append(round(end / rate - LOCATE_STEP_S / 2, 2))
        previous = heard
    return ends


async def label_day(store: WakeLearningStore, day: str, check: NameCheck) -> dict[str, list[float]]:
    """Return where the name ends in each of the day's kept clips, checking only clips not seen before."""
    path = store.root / "labels" / f"{day}.json"
    try:
        labels: dict[str, list[float]] = json.loads(path.read_text())
    except FileNotFoundError:
        labels = {}
    for event in store.events(day):
        if event["kind"] not in LABELLED_KINDS or not event.get("clip") or event["id"] in labels:
            continue
        try:
            rate, audio = _read_wav(store.clips / event["clip"])
        except (OSError, wave.Error):
            continue
        try:
            labels[event["id"]] = await locate_names(check, rate, audio)
        except Exception as error:
            logger.warning("Name check failed for a %s clip: %s", event["kind"], type(error).__name__)
            continue
    write_private(path, json.dumps(labels).encode())
    logger.info("Labelled %s: the name is in %d of %d clips", day, sum(1 for ends in labels.values() if ends),
                len(labels))  # fmt: skip
    return labels


# ---- what happened when -------------------------------------------------------------------------------------
@dataclass
class DayMoments:
    """When his name was said to him (calls) or about him (mentions), when it surely wasn't, and unknown spans."""

    calls: list[float] = field(default_factory=list)
    not_calls: list[float] = field(default_factory=list)
    unsure: list[tuple[float, float]] = field(default_factory=list)
    mentions: list[float] = field(default_factory=list)

    @property
    def named(self) -> list[float]:
        """Every time his name was said, to him or about him: he should wake for both, so a head should catch both."""
        return sorted(self.calls + self.mentions)


def _woke(event: dict[str, Any]) -> bool:
    if event["kind"] == "name_heard":
        return event.get("outcome") == "woke"
    return (
        event["kind"] == "wake"
        and bool(event.get("heard") or event.get("trusted"))
        and event.get("outcome") != "rejected"
    )


def clip_start(event: dict[str, Any]) -> float:
    """Return when the event's clip starts (Unix time)."""
    return float(event["t"]) - float(event.get("event_at") or 0.0)


def _not_called(event: dict[str, Any]) -> bool:
    """Whether someone said this wake was not for him ("we weren't talking to you", or from the phone).

    That says he shouldn't have answered, not that nobody said his name: they may have been talking about him.
    """
    return event.get("said") == "not_called"


def _sent_away(event: dict[str, Any], located: list[float]) -> bool:
    """Whether he was sent to sleep within the minute after waking and no name was found: nothing called him."""
    return event.get("slept_after_s") is not None and not located


def _nothing_asked(event: dict[str, Any], located: list[float]) -> bool:
    """Whether he woke, nobody said anything and no name was found: unknown until someone answers."""
    return event.get("request_chars") == 0 and not located


def _located(event: dict[str, Any], names: dict[str, list[float]], said_not: list[float]) -> list[float]:
    """Return when the name ended in the event's clip, leaving out where the household said nobody said it."""
    times = (clip_start(event) + offset for offset in names.get(event["id"], []))
    return [time for time in times if not _near(time, said_not, 1.5)]


def human_answers(
    events: list[dict[str, Any]], answers: dict[str, str]
) -> tuple[list[float], list[float], list[float]]:
    """Return when, in this day's clips, the household said his name was said to him, about him, and not at all."""
    by_id = {event["id"]: event for event in events}
    said: dict[str, list[float]] = {"call": [], "mention": [], "not_call": []}
    for item_id, answer in answers.items():
        event_id, _, offset = item_id.rpartition("@")
        event = by_id.get(event_id)
        if event is not None and answer in said:
            said[answer].append(clip_start(event) + float(offset))
    return said["call"], said["mention"], said["not_call"]


def _told_off_unnamed(event: dict[str, Any], located: list[float]) -> bool:
    """Whether a wake was told off, sent away or asked nothing, and the night found no name in its clip.

    The live check heard a name to wake him; with the night finding none, it was noise or a name the night
    missed, so only someone's answer settles it.
    """
    return not located and (_not_called(event) or _sent_away(event, located) or _nothing_asked(event, located))


def day_moments(events: list[dict[str, Any]], names: dict[str, list[float]], answers: dict[str, str]) -> DayMoments:
    """Turn a day's events, located names and the household's answers into calls, non-calls and unknowns.

    What the household answered always wins over the speech model and the logs.
    """
    said_call, said_about, said_not = human_answers(events, answers)
    moments = DayMoments()
    found: list[float] = []
    about: list[float] = []
    quiet: list[float] = []
    for event in events:
        located = _located(event, names, said_not)
        kind, at = event["kind"], float(event["t"])
        if kind in ("missed", "antenna"):
            # Somewhere in the minute (or seconds) before was a call; unless its name was found, nothing there is
            # known to be quiet.
            found.extend(located)
            if not located and not _near(at, said_call, CALL_GAP_S * 3):
                moments.unsure.append((clip_start(event), at))
        elif _woke(event) and kind == "name_heard":
            # A transcript wake is logged when its sentence ends, often seconds after the name: only a name the
            # night located gives a time. Without one, the whole sentence is unknown rather than a guessed call.
            target = about if _not_called(event) else found
            if not _near(at, said_call + said_about + said_not, 1.5):
                target.extend(located)
                if not located:
                    moments.unsure.append(_sentence_span(event))
        elif _woke(event):
            target = about if _not_called(event) else found  # told "not to you" over his name: said about him
            if _near(at, said_call + said_about + said_not, 1.5):
                pass  # the household answered for this wake; their answer is added below
            elif _told_off_unnamed(event, located):
                moments.unsure.append((at - 1.5, at + 0.5))
            else:
                target.append(at)
            target.extend(time for time in located if time < at - EARLIER_CALL_S)
        elif located:
            found.extend(located)  # the name was there: a call, or someone talking about him
        elif kind in ("wake", "shadow"):
            quiet.append(at)
    moments.calls = sorted([time for time in found if not _near(time, said_about, 1.5)] + said_call)
    moments.mentions = sorted([time for time in about if not _near(time, said_call, 1.5)] + said_about)
    # A name heard close by outweighs an automatic "no name" (a repeat the check missed); never a person's answer.
    moments.not_calls = sorted([time for time in quiet if not _near(time, moments.named, 3.0)] + said_not)
    return moments


def _sentence_span(event: dict[str, Any]) -> tuple[float, float]:
    """Return when a transcript wake's sentence was said: its speech, from a second before, to just after it was logged."""
    at = float(event["t"])
    return at - float(event.get("speech_seconds") or 0.0) - 1.0, at + 0.5


def _near(at: float, times: Iterable[float], within: float) -> bool:
    return any(abs(at - other) <= within for other in times)


# ---- scoring heads on the feature log -----------------------------------------------------------------------
class Head:
    """One ONNX wake head, scored over feature windows in batches when the file allows it."""

    def __init__(self, path: Path) -> None:
        """Load ``path``."""
        self.path = path
        self.name = path.stem
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0]
        self._batch = self._input.shape[0] if isinstance(self._input.shape[0], int) else 4096

    def scores(self, windows: NDArray[np.float16]) -> NDArray[np.float32]:
        """Return the head's score for each 16x96 window."""
        out = np.zeros(len(windows), dtype=np.float32)
        for start in range(0, len(windows), self._batch):
            batch = np.asarray(windows[start : start + self._batch], dtype=np.float32)
            out[start : start + len(batch)] = np.asarray(
                self._session.run(None, {self._input.name: batch})[0]
            ).reshape(-1)
        return out


@dataclass
class DayScores:
    """Every window's end time and score, for each head, on one day of the feature log."""

    ends: NDArray[np.float64]
    hours: float
    scores: dict[str, NDArray[np.float32]]


def score_day(runs: list[FeatureRun], heads: list[Head], cache: Path | None = None) -> DayScores:
    """Score every window of the day with each head, an hour at a time; scores already computed come from ``cache``."""
    ends = np.concatenate([run.windows()[1] for run in runs]) if runs else np.zeros(0)
    scores: dict[str, NDArray[np.float32]] = {}
    for head in heads:
        digest = hashlib.sha256(head.path.read_bytes()).hexdigest()[:16]
        cached = cache / f"{digest}.npy" if cache is not None else None
        if cached is not None and cached.is_file():
            kept = np.load(cached)
            if len(kept) == len(ends):
                scores[head.name] = kept
                continue
        parts = [head.scores(run.windows()[0]) for run in runs]
        scores[head.name] = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        if cached is not None:
            make_private_dir(cached.parent)
            np.save(cached, scores[head.name])
    hours = sum(len(run.frames) for run in runs) * FRAME_S / 3600
    return DayScores(ends, hours, scores)


def pair_scores(day: DayScores, main: str, extra: str | None, scale: float) -> NDArray[np.float32]:
    """Return what the detector sees: the main head's score or the extra head's scaled one, whichever is higher."""
    combined = day.scores[main].copy()
    if extra is not None:
        combined = np.maximum(combined, np.minimum(1.0, day.scores[extra] * scale))
    return combined


def count_fires(ends: NDArray[np.float64], scores: NDArray[np.float32], threshold: float, skip: list[float]) -> int:
    """Count wakes (one per 3 s) over ``threshold``, leaving out the repeats around each call in ``skip``."""
    fires, last = 0, -np.inf
    for at in ends[scores >= threshold]:
        if at - last < COOLDOWN_S:
            continue
        last = at
        if not _near(float(at), skip, CALL_GAP_S):
            fires += 1
    return fires


def caught(
    ends: NDArray[np.float64], scores: NDArray[np.float32], threshold: float, calls: list[float]
) -> tuple[int, int]:
    """Return how many ``calls`` have a window over ``threshold`` just around them, of those the log covers."""
    hit = covered = 0
    for at in calls:
        first, last = np.searchsorted(ends, [at - 0.6, at + 1.0], side="left")
        if last <= first:
            continue
        covered += 1
        hit += int(scores[first:last].max() >= threshold)
    return hit, covered


@dataclass(frozen=True)
class PairResult:
    """How a main + extra head pair did over some days: names caught of those covered, and fires per day.

    A name is any time his name was said, to him or about him; the JSON keeps the older "calls" keys.
    """

    caught: int
    calls: int
    fires_per_day: float

    def as_json(self) -> dict[str, float]:
        """Return the numbers the scoreboard shows."""
        return {"calls_caught": self.caught, "calls": self.calls, "fires_per_day": round(self.fires_per_day, 1)}


def evaluate(days: list[tuple[DayScores, DayMoments]], main: str, main_threshold: float, extra: str | None,
             extra_threshold: float) -> PairResult:  # fmt: skip
    """Score one main + extra pair over ``days``."""
    hit = covered = fires = 0
    hours = 0.0
    for day, moments in days:
        scores = pair_scores(day, main, extra, main_threshold / extra_threshold)
        day_hit, day_covered = caught(day.ends, scores, main_threshold, moments.named)
        hit, covered = hit + day_hit, covered + day_covered
        skip = moments.named + [time for span in moments.unsure for time in span]
        fires += count_fires(day.ends, scores, main_threshold, skip)
        hours += day.hours
    return PairResult(hit, covered, fires / hours * LISTENING_DAY_H if hours else 0.0)


def choose_threshold(days: list[tuple[DayScores, DayMoments]], main: str, main_threshold: float, extra: str,
                     fire_budget: float) -> tuple[float, PairResult] | None:  # fmt: skip
    """Pick the extra head's threshold that catches the most calls while firing no more than ``fire_budget``."""
    best: tuple[float, PairResult] | None = None
    for threshold in EXTRA_THRESHOLDS:
        if main_threshold / threshold >= TRUSTED_SCORE:
            continue
        result = evaluate(days, main, main_threshold, extra, threshold)
        if result.fires_per_day <= fire_budget and (
            best is None
            or result.caught > best[1].caught
            or (result.caught == best[1].caught and result.fires_per_day < best[1].fires_per_day)
        ):
            best = (threshold, result)
    return best


def dev_days(days: Iterable[str]) -> list[str]:
    """Return the newest ``DEV_DAYS`` days a challenger is tuned on, always leaving one to train on."""
    ordered = sorted(days)
    return ordered[-min(DEV_DAYS, len(ordered) - 1) :] if len(ordered) > 1 else []


def better(candidate: PairResult, current: PairResult, margin: int, fires_share: float) -> bool:
    """Whether ``candidate`` catches ``margin`` more calls without firing more, or as many on a share of the fires."""
    more = candidate.caught >= current.caught + margin and candidate.fires_per_day <= current.fires_per_day + 0.5
    quieter = candidate.caught >= current.caught and candidate.fires_per_day <= fires_share * current.fires_per_day
    return more or quieter


def closer_to_live(candidate: PairResult, shadow: PairResult, live: PairResult) -> bool:
    """Whether ``candidate`` catches more calls than a shadow behind the live pair, firing no more than live.

    A shadow that fires little but misses calls would otherwise keep out a challenger nearer to replacing the live
    pair (2026-11-05: 44 of 76 calls at 307 fires a day against the shadow's 37 at 231 and live's 46 at 352).
    """
    return (
        shadow.caught < live.caught
        and candidate.caught > shadow.caught
        and candidate.fires_per_day <= live.fires_per_day
    )


def clearly_better(candidate: PairResult, current: PairResult) -> bool:
    """Whether ``candidate`` is better by enough to offer a switch."""
    return better(candidate, current, max(SWITCH_MARGIN_CALLS, int(0.05 * current.calls + 0.999)), SWITCH_FIRES_SHARE)


# ---- training data -------------------------------------------------------------------------------------------
def day_examples(day: DayScores, runs: list[FeatureRun], moments: DayMoments, rng: random.Random
                 ) -> tuple[NDArray[np.float16], NDArray[np.float32]]:  # fmt: skip
    """Return a day's training windows: his name, to him or about him (1), sure non-calls and a sample of the rest (0)."""
    ends = day.ends
    labels = np.full(len(ends), -1.0, dtype=np.float32)
    for at in moments.named:
        first, last = np.searchsorted(ends, [at - 0.4 + NAME_LABEL_SHIFT_S, at + 0.25 + NAME_LABEL_SHIFT_S])
        labels[first:last] = 1.0
    for at in moments.not_calls:
        first, last = np.searchsorted(ends, [at - 1.5, at + 0.5])
        labels[first:last][labels[first:last] < 0] = 0.0
    quiet = labels < 0
    for at in moments.named:
        first, last = np.searchsorted(ends, [at - CALL_GAP_S, at + CALL_GAP_S])
        quiet[first:last] = False
    for start, end in moments.unsure:
        first, last = np.searchsorted(ends, [start - 3.0, end + 3.0])
        quiet[first:last] = False
    everything_else = np.flatnonzero(quiet)[::NEGATIVE_STRIDE]
    if len(everything_else) > NEGATIVES_PER_DAY:
        everything_else = np.array(sorted(rng.sample(list(everything_else), NEGATIVES_PER_DAY)))
    labels[everything_else] = 0.0
    chosen = np.flatnonzero(labels >= 0)
    offsets = np.cumsum([0] + [len(run.windows()[1]) for run in runs])
    parts = []
    for run, start, stop in zip(runs, offsets[:-1], offsets[1:]):
        local = chosen[(chosen >= start) & (chosen < stop)] - start
        parts.append(run.window_at(local))
    windows = np.concatenate(parts) if parts else np.zeros((0, 16, 96), np.float16)
    return windows, labels[chosen]


def recording_examples(store: WakeLearningStore, model_dir: Path, noise: list[NDArray[np.int16]], rng: random.Random
                       ) -> tuple[NDArray[np.float16], NDArray[np.float32]]:  # fmt: skip
    """Return windows from the household's recordings of the name, over room sound, as calls."""
    entries = store.all_recordings()
    if not entries or not noise:
        return np.zeros((0, 16, 96), np.float16), np.zeros(0, np.float32)
    features_module = importlib.import_module("openwakeword.utils")
    features = features_module.AudioFeatures(
        melspec_model_path=str(model_dir / "melspectrogram.onnx"),
        embedding_model_path=str(model_dir / "embedding_model.onnx"),
        inference_framework="onnx",
    )
    windows, labels = [], []
    for entry in entries:
        try:
            rate, voice = _read_wav(store.recordings / entry["file"])
        except (OSError, wave.Error):
            continue
        if rate != 16_000:
            continue
        for _ in range(RECORDING_COPIES):
            room = rng.choice(noise)
            lead = np.resize(room, int(rng.uniform(1.2, 2.0) * rate))
            tail = np.resize(room[::-1], int(0.6 * rate))
            gain = 10 ** (rng.uniform(-6, 2) / 20)
            clip = np.concatenate([lead, voice.astype(np.float32) * gain + np.resize(room, voice.size), tail])
            clip = np.clip(clip, -32768, 32767).astype(np.int16)
            voice_end = (lead.size + voice.size) / rate - 0.25  # the recording keeps 0.25 s after the voice
            clip_windows, clip_ends = _stream_windows(features, clip, rate)
            near = (clip_ends >= voice_end - 0.3 + NAME_LABEL_SHIFT_S) & (
                clip_ends <= voice_end + 0.3 + NAME_LABEL_SHIFT_S
            )
            windows.append(clip_windows[near])
            labels.append(np.ones(int(near.sum()), np.float32))
    if not windows:
        return np.zeros((0, 16, 96), np.float16), np.zeros(0, np.float32)
    return np.concatenate(windows), np.concatenate(labels)


def base_examples(path: Path | None) -> tuple[NDArray[np.float16], NDArray[np.float32]]:
    """Return a saved training set's windows (``X``, 16x96) labelled 1 or 0 (``Y``; -1, uncertain, is left out).

    The set the "Richie" head was trained on holds synthetic calls in several voices and sound-alikes like
    "Ricky" that three days of the house cannot teach; a challenger without them lost to that head.
    """
    empty = np.zeros((0, 16, 96), np.float16), np.zeros(0, np.float32)
    if path is None:
        return empty
    try:
        with np.load(path, allow_pickle=False) as saved:
            windows, labels = saved["X"], saved["Y"].astype(np.float32)
    except (OSError, KeyError, ValueError) as error:
        logger.warning("Base training set unreadable (%s); training without it", type(error).__name__)
        return empty
    if windows.ndim != 3 or windows.shape[1:] != (16, 96) or len(windows) != len(labels):
        logger.warning("Base training set has the wrong shape; training without it")
        return empty
    keep = (labels == 0) | (labels == 1)
    return windows[keep].astype(np.float16), labels[keep]


def _stream_windows(
    features: Any, audio: NDArray[np.int16], rate: int
) -> tuple[NDArray[np.float16], NDArray[np.float64]]:
    """Stream ``audio`` through openWakeWord's front end in 80 ms chunks, as the app does."""
    features.reset()
    padded = np.concatenate([np.zeros(2 * rate, dtype=np.int16), audio])
    chunk = 1280
    windows, ends = [], []
    for start in range(0, len(padded) - chunk + 1, chunk):
        features(padded[start : start + chunk])
        end = (start + chunk) / rate - 2.0
        if end > 1.28:
            windows.append(features.get_features(16)[0].astype(np.float16))
            ends.append(end)
    return np.asarray(windows, dtype=np.float16).reshape(-1, 16, 96), np.asarray(ends)


# ---- the clips to check tomorrow ------------------------------------------------------------------------------
def review_items(events: list[dict[str, Any]], names: dict[str, list[float]], answers: dict[str, str],
                 rng: random.Random) -> list[ReviewItem]:  # fmt: skip
    """Pick at most ten clips for the household to check, where the automatic signals are least sure."""
    ranked: list[tuple[int, ReviewItem]] = []

    def offer(rank: int, event: dict[str, Any], at: float, question: str = NAME_QUESTION) -> None:
        item_id = f"{event['id']}@{at:.2f}"
        if item_id in answers or event["id"] in answers:
            return
        clip = ReviewItem(item_id, event["clip"], max(0.0, at - 3.0), at + 1.5, question,
                          follow_up=question != MISSED_QUESTION)  # fmt: skip
        ranked.append((rank, clip))

    for event in events:
        if not event.get("clip"):
            continue
        located = names.get(event["id"], [])
        kind, fired_at = event["kind"], float(event.get("event_at") or 0.0)
        if kind == "missed":
            for at in located[-2:]:
                offer(0, event, at, MISSED_QUESTION)
        elif kind == "antenna":
            for at in located[-1:]:
                offer(1, event, at, "Did someone say his name here?")
        elif kind == "wake" and event.get("outcome") == "rejected" and located:
            offer(2, event, located[-1])
        elif _woke(event) and _told_off_unnamed(event, located):
            offer(3, event, fired_at)  # the live check heard a name, the night none
        elif kind == "shadow" and located:
            offer(4, event, located[-1])
    rejected = [event for event in events if event["kind"] == "wake" and event.get("outcome") == "rejected"]
    for event in rng.sample(rejected, min(2, len(rejected))):  # a check on the "no name" verdicts
        if event.get("clip") and not names.get(event["id"]):
            offer(5, event, float(event.get("event_at") or 0.0))
    ranked.sort(key=lambda pair: pair[0])
    seen: set[str] = set()
    items = []
    for _, item in ranked:
        if item.id not in seen:
            seen.add(item.id)
            items.append(item)
    return items[:QUEUE_SIZE]


# ---- the scoreboard --------------------------------------------------------------------------------------------
def day_score(events: list[dict[str, Any]], names: dict[str, list[float]], answers: dict[str, str]) -> dict[str, Any]:
    """Count the day's call episodes, those answered the first time, false wakes and mentions, overall and by TV.

    A false wake is one where nobody said his name. Waking for his name said about him is right; answering it
    is what counts against him, as ``spoke_about``.
    """
    said_call, said_about, said_not = human_answers(events, answers)
    seeds: list[tuple[float, str, bool]] = []  # (when, "woke" / "failed" / "missed", TV on)
    false_wakes = spoke_about = 0
    mentions = len(said_about)
    for event in events:
        at, tv = float(event["t"]), bool(event.get("tv"))
        located = [time for time in _located(event, names, said_not) if not _near(time, said_about, 1.5)]
        if _woke(event):
            answered_for = _near(at, said_call, 1.5)
            told_about = _not_called(event) and bool(located) and not answered_for
            if _near(at, said_about, 1.5) or told_about:
                mentions += int(told_about)
                spoke_about += int(event.get("request_chars") != 0)
                continue
            if _near(at, said_not, 1.5) or (not answered_for and _told_off_unnamed(event, located)):
                false_wakes += 1
                continue
            seeds.append((at, "woke", tv))
            # A transcript wake's own name sits anywhere in its sentence; only one before the sentence was missed.
            before = _sentence_span(event)[0] if event["kind"] == "name_heard" else at - EARLIER_CALL_S
            seeds.extend((time, "failed", tv) for time in located if time < before)
        elif event["kind"] == "missed":
            seeds.append((at, "missed", tv))
            seeds.extend((time, "failed", tv) for time in located)
        elif event["kind"] == "wake":
            seeds.extend((time, "failed", tv) for time in located)
    answered_at = [seed[0] for seed in seeds if seed[1] == "woke"]
    seeds.extend((time, "failed", False) for time in said_call if not _near(time, answered_at, 1.5))
    seeds.sort()
    episodes: list[list[tuple[float, str, bool]]] = []
    for seed in seeds:
        if episodes and seed[0] - episodes[-1][-1][0] <= CALL_GAP_S:
            episodes[-1].append(seed)
        else:
            episodes.append([seed])
    by_tv: dict[str, dict[str, int]] = {"tv": {"calls": 0, "first_time": 0}, "no_tv": {"calls": 0, "first_time": 0}}
    first_time = 0
    for episode in episodes:
        statuses = [seed[1] for seed in episode]
        answered = statuses[0] == "woke" and "missed" not in statuses and "failed" not in statuses
        group = by_tv["tv" if any(seed[2] for seed in episode) else "no_tv"]
        group["calls"] += 1
        group["first_time"] += int(answered)
        first_time += int(answered)
    return {"calls": len(episodes), "first_time": first_time, "false_wakes": false_wakes, "mentions": mentions,
            "spoke_about": spoke_about, "by_condition": by_tv}  # fmt: skip


# ---- the night --------------------------------------------------------------------------------------------------
@dataclass
class NightSettings:
    """Where things are and how to train."""

    store: WakeLearningStore
    features_dir: Path
    model_dir: Path
    main_threshold: float
    trainer_python: str | None
    seed_head: Path | None = None
    seed_threshold: float = 0.8
    restart: Callable[[], Awaitable[None]] | None = None
    base_set: Path | None = None  # a saved training set mixed into every night's (WAKE_BASE_SET)
    today: date = field(default_factory=date.today)


def seed_live_head(settings: NightSettings) -> HeadRecord | None:
    """On the first night, make the configured extra head the live one (so it can be compared and replaced)."""
    store = settings.store
    live = store.live()
    if live is not None or settings.seed_head is None or not settings.seed_head.is_file():
        return live
    raw = settings.seed_head.read_bytes()
    record = HeadRecord(settings.seed_head.stem, settings.seed_threshold, hashlib.sha256(raw).hexdigest(),
                        datetime.now().isoformat(timespec="seconds"), {})  # fmt: skip
    write_private(store.head_path(record), raw)
    store.set_live(record)
    logger.info("Live wake head seeded from %s", settings.seed_head.name)
    return record


def train_challenger(
    settings: NightSettings, windows: NDArray[np.float16], labels: NDArray[np.float32], start: Path | None = None
) -> Path | None:
    """Fine-tune ``start`` (the main head when None) on ``windows`` in the trainer's Python; return the ONNX file."""
    if settings.trainer_python is None or not labels.size or not (labels == 1).any():
        logger.info("No challenger trained tonight (no trainer or no calls to learn from)")
        return None
    work = settings.store.root / "train"
    data, out = work / "dataset.npz", work / "challenger.onnx"
    make_private_dir(work)
    np.savez(data, X=windows, Y=labels)
    trainer = Path(__file__).resolve().parents[3] / "scripts" / "wake_train.py"
    command = [settings.trainer_python, str(trainer), "--data", str(data), "--init", str(start or settings.model_dir / MAIN_HEAD),
               "--out", str(out)]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, timeout=3 * 3600, check=False)
    data.unlink(missing_ok=True)
    if result.returncode != 0 or not out.is_file():
        logger.warning("Wake trainer failed (exit %s): %s", result.returncode, result.stderr[-500:])
        return None
    return out


async def run_night(settings: NightSettings, check: NameCheck) -> dict[str, Any]:
    """Run one night: label, score, offer a switch, train, pick clips to check and write the scoreboard."""
    store = settings.store
    rng = random.Random(settings.today.toordinal())
    answers = store.answers()
    live = seed_live_head(settings)
    shadow = store.shadow()
    days = [day for day in store.days() if day < settings.today.isoformat()]
    labelled: dict[str, tuple[list[dict[str, Any]], dict[str, list[float]], DayMoments]] = {}
    for day in days:
        events = store.events(day)
        names = await label_day(store, day, check)
        labelled[day] = (events, names, day_moments(events, names, answers))

    heads = [Head(settings.model_dir / MAIN_HEAD)]
    main = heads[0].name
    for record in (live, shadow):
        if record is not None and store.head_path(record).is_file():
            heads.append(Head(store.head_path(record)))
    scored: dict[str, DayScores] = {}
    runs_by_day: dict[str, list[FeatureRun]] = {}
    for day in days:
        runs = read_runs(settings.features_dir, day)
        if runs:
            runs_by_day[day] = runs
            scored[day] = score_day(runs, heads, store.root / "scores" / day)
    evaluated = [(scored[day], labelled[day][2]) for day in days if day in scored]
    summary: dict[str, Any] = {"days_scored": len(evaluated)}

    live_result = evaluate(evaluated, main, settings.main_threshold, live.name if live else None,
                           live.threshold if live else 1.0)  # fmt: skip
    summary["live"] = {"name": live.name if live else None, **live_result.as_json()}
    if shadow is not None and store.head_path(shadow).is_file():
        shadow_days = [(scored[day], labelled[day][2]) for day in days if day in scored and day >= shadow.created[:10]]
        shadow_result = evaluate(shadow_days, main, settings.main_threshold, shadow.name, shadow.threshold)
        live_on_same = evaluate(shadow_days, main, settings.main_threshold, live.name if live else None,
                                live.threshold if live else 1.0)  # fmt: skip
        summary["shadow"] = {"name": shadow.name, "threshold": shadow.threshold, **shadow_result.as_json()}
        if shadow_days and clearly_better(shadow_result, live_on_same):
            store.propose({"calls": live_on_same.calls, "live_caught": live_on_same.caught,
                           "shadow_caught": shadow_result.caught,
                           "live_fires_per_day": round(live_on_same.fires_per_day, 1),
                           "shadow_fires_per_day": round(shadow_result.fires_per_day, 1),
                           "days": len(shadow_days)})  # fmt: skip
        elif store.proposal() is not None:
            store.withdraw_proposal()

    if len(evaluated) >= 2:
        tuning = dev_days(scored)
        train_days = [day for day in scored if day not in tuning]
        examples = [day_examples(scored[day], runs_by_day[day], labelled[day][2], rng) for day in train_days]
        noise = [_read_wav(store.clips / event["clip"])[1] for day in days for event in labelled[day][0]
                 if event["kind"] == "background" and event.get("clip")][:200]  # fmt: skip
        examples.append(recording_examples(store, settings.model_dir, noise, rng))
        examples.append(base_examples(settings.base_set))
        windows = np.concatenate([pair[0] for pair in examples])
        labels = np.concatenate([pair[1] for pair in examples])
        negatives = np.flatnonzero(labels == 0)
        if len(negatives) > MAX_NEGATIVES:
            drop = np.array(rng.sample(list(negatives), len(negatives) - MAX_NEGATIVES))
            keep = np.ones(len(labels), dtype=bool)
            keep[drop] = False
            windows, labels = windows[keep], labels[keep]
        summary["trained_on"] = {"calls": int((labels == 1).sum()), "other": int((labels == 0).sum())}
        # From the live head's weights: on 2026-11-03/04 that caught 44 of 76 calls (live 46), the main head's 35.
        start = store.head_path(live) if live is not None and store.head_path(live).is_file() else None
        challenger_file = train_challenger(settings, windows, labels, start)
        if challenger_file is not None:
            name = f"house-{settings.today:%Y%m%d}"
            path = store.heads / f"{name}.onnx"
            write_private(path, challenger_file.read_bytes())
            challenger_file.unlink()
            dev = [(score_day(runs_by_day[day], [*heads, Head(path)], store.root / "scores" / day), labelled[day][2])
                   for day in tuning]  # fmt: skip
            live_dev = evaluate(dev, main, settings.main_threshold, live.name if live else None,
                                live.threshold if live else 1.0)  # fmt: skip
            choice = choose_threshold(dev, main, settings.main_threshold, name, max(live_dev.fires_per_day, 1.0))
            has_shadow = shadow is not None and store.head_path(shadow).is_file()
            current = (
                evaluate(dev, main, settings.main_threshold, shadow.name, shadow.threshold)
                if shadow is not None and has_shadow
                else live_dev
            )
            summary["challenger"] = {"name": name, "threshold": choice[0] if choice else None,
                                     **(choice[1].as_json() if choice else {})}  # fmt: skip
            if choice is not None and (
                better(choice[1], current, 1, SHADOW_FIRES_SHARE) or closer_to_live(choice[1], current, live_dev)
            ):
                record = HeadRecord(name, choice[0], hashlib.sha256(path.read_bytes()).hexdigest(),
                                    datetime.now().isoformat(timespec="seconds"), choice[1].as_json())  # fmt: skip
                store.set_shadow(record)
                store.withdraw_proposal()
                summary["new_shadow"] = name
                if settings.restart is not None:
                    await settings.restart()
            else:
                path.unlink(missing_ok=True)

    newest_day = max(days) if days else None
    if newest_day is not None:
        events, names, _ = labelled[newest_day]
        store.write_queue(review_items(events, names, answers, rng))
    week = [day for day in days if day >= (settings.today - timedelta(days=7)).isoformat()]
    per_day = [{"day": day, **day_score(labelled[day][0], labelled[day][1], answers)} for day in week]
    counted = ("calls", "first_time", "false_wakes", "mentions", "spoke_about")
    totals = {key: sum(day[key] for day in per_day) for key in counted}
    by_condition = {
        group: {key: sum(day["by_condition"][group][key] for day in per_day) for key in ("calls", "first_time")}
        for group in ("tv", "no_tv")
    }
    store.write_scoreboard({
        "week": {**totals, "days": len(per_day),
                 "false_wakes_per_day": round(totals["false_wakes"] / len(per_day), 1) if per_day else 0.0},
        "days": [{key: day[key] for key in ("day", *counted)} for day in per_day],
        "by_condition": by_condition,
        "targets": {"first_time": 0.95, "false_wakes_per_day": 1.0},
        "live": {**summary["live"], "threshold": live.threshold} if live else None,
        "shadow": summary.get("shadow"),
    })  # fmt: skip
    _prune(store.root / "labels", settings.today, keep_days=60)
    _prune(store.root / "scores", settings.today, keep_days=35)
    logger.info("Wake learning night done: %s", json.dumps(summary))
    return summary


def _prune(folder: Path, today: date, *, keep_days: int) -> None:
    """Delete per-day caches older than ``keep_days``."""
    oldest = (today - timedelta(days=keep_days)).isoformat()
    for path in folder.glob("*"):
        if path.name[:10] < oldest:
            if path.is_dir():
                for child in path.glob("*"):
                    child.unlink(missing_ok=True)
                path.rmdir()
            else:
                path.unlink(missing_ok=True)


def main() -> None:
    """Run tonight's wake learning (the LaunchAgent's entry point)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not config.WAKE_LEARNING_ENABLED or not config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR:
        logger.info("Wake learning is off (WAKE_LEARNING_ENABLED, LOCAL_ACOUSTIC_WAKE_MODEL_DIR)")
        return
    service = os.getenv("ADMIN_APP_SERVICE", "").strip()

    async def restart() -> None:
        await restart_app(service)

    settings = NightSettings(
        store=WakeLearningStore(wake_learning_directory()),
        features_dir=wake_features_directory(),
        model_dir=Path(config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR).expanduser(),
        main_threshold=config.LOCAL_ACOUSTIC_WAKE_THRESHOLD,
        trainer_python=os.getenv("WAKE_TRAINER_PYTHON", "").strip() or None,
        seed_head=Path(config.LOCAL_ACOUSTIC_WAKE_EXTRA_HEAD).expanduser()
        if config.LOCAL_ACOUSTIC_WAKE_EXTRA_HEAD
        else None,
        seed_threshold=config.LOCAL_ACOUSTIC_WAKE_EXTRA_THRESHOLD,
        restart=restart if service else None,
        base_set=Path(base).expanduser() if (base := os.getenv("WAKE_BASE_SET", "").strip()) else None,
    )
    if behind_privacy_prompt(settings.model_dir):
        logger.error("Wake models are in %s, where macOS would hold the night on a privacy prompt; move them out",
                     settings.model_dir)  # fmt: skip
        sys.exit(1)
    started = time.monotonic()
    try:
        asyncio.run(run_night(settings, speech_server_check()))
    except Exception:
        logger.exception("Wake learning night failed")
        sys.exit(1)
    logger.info("Wake learning night took %.0f s", time.monotonic() - started)


if __name__ == "__main__":
    main()
