"""Guided acoustic wake calibration on the robot microphone, keeping only scores in memory."""

import time
import logging
import argparse
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable, Sequence

from reachy_mini import ReachyMini
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.streaming import audio_to_mono_int16
from reachy_mini_conversation_app.mic_level_probe import _drain, _ProbeMedia
from reachy_mini_conversation_app.acoustic_wake_word import NEAR_MISS_FLOOR, OpenWakeWordDetector
from reachy_mini_conversation_app.local_audio_backends import pcm16_rms


logger = logging.getLogger(__name__)

CANDIDATE_THRESHOLDS = (0.5, 0.7, 0.8, 0.9, 0.95, 0.98)
SIMILAR_WORDS = ("ready", "really", "reach", "peachy", "Richie")
_FRAME_S = 0.08
# Score runs separated by less than this are one event, like the detector's own 3 s cooldown would treat them.
_EVENT_MERGE_S = 1.0
_POLL_INTERVAL_S = 0.005
# The model needs about 1.3 s of audio after a reset before it can score, so each step starts after a pre-roll.
_WARMUP_S = 1.5


class NoMicrophoneAudioError(RuntimeError):
    """Raised when a step receives no microphone frames, so later scores would be meaningless."""


@dataclass(frozen=True)
class CalibrationStep:
    """One guided window: background speech, a similar word, or a spoken wake word."""

    name: str
    instruction: str
    kind: str  # "background", "similar" or "wake"
    seconds: float


@dataclass(frozen=True)
class StepResult:
    """Scores observed in one step (metadata only)."""

    step: CalibrationStep
    peak: float
    event_peaks: tuple[float, ...]
    frames: int = 0
    peak_level: float = 0.0

    def events_at(self, threshold: float) -> int:
        """Return how many separate score events would have woken at ``threshold``."""
        return sum(1 for peak in self.event_peaks if peak >= threshold)


def default_steps(talk_seconds: float = 60.0) -> list[CalibrationStep]:
    """Return the guided calibration sequence."""
    steps = [
        CalibrationStep("quiet", "Stay silent and leave the room as it normally is.", "background", 15.0),
        CalibrationStep(
            "talk",
            "Talk normally (read something aloud or chat) WITHOUT saying Reachy or similar words.",
            "background",
            talk_seconds,
        ),
    ]
    for word in SIMILAR_WORDS:
        for attempt in (1, 2):
            steps.append(CalibrationStep(f"{word} #{attempt}", f'Say "{word}" once, then stay quiet.', "similar", 4.0))
    variations = ["normally"] * 6 + ["softly"] * 2 + ["from further away (2-3 m)"] * 2
    for trial, how in enumerate(variations, start=1):
        steps.append(CalibrationStep(f"Reachy #{trial}", f'Say "Reachy" once, {how}.', "wake", 4.0))
    return steps


def score_events(scores: Sequence[float], floor: float = NEAR_MISS_FLOOR) -> tuple[float, ...]:
    """Group per-frame scores into events (runs above ``floor``) and return each event's peak."""
    peaks: list[float] = []
    current: float | None = None
    gap_frames = 0
    max_gap = round(_EVENT_MERGE_S / _FRAME_S)
    for score in scores:
        if score >= floor:
            current = score if current is None else max(current, score)
            gap_frames = 0
        elif current is not None:
            gap_frames += 1
            if gap_frames > max_gap:
                peaks.append(current)
                current = None
    if current is not None:
        peaks.append(current)
    return tuple(peaks)


def run_calibration(
    media: _ProbeMedia,
    make_detector: Callable[[Callable[[float], None]], OpenWakeWordDetector],
    *,
    steps: Sequence[CalibrationStep],
    before_step: Callable[[CalibrationStep], None],
    on_start: Callable[[CalibrationStep], None] = lambda _step: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[StepResult]:
    """Feed live robot audio through the wake model for each step and collect its scores and levels."""
    scores: list[float] = []
    detector = make_detector(scores.append)
    input_rate = int(media.get_input_audio_samplerate())
    results: list[StepResult] = []

    def feed(seconds: float) -> tuple[int, float]:
        frames = 0
        peak_level = 0.0
        started = clock()
        while clock() - started < seconds:
            frame = media.get_audio_sample()
            if frame is None:
                sleep(_POLL_INTERVAL_S)
                continue
            mono = audio_to_mono_int16(frame)
            if mono.size:
                frames += 1
                peak_level = max(peak_level, pcm16_rms(mono))
                detector.process_audio(mono.astype("<i2", copy=False).tobytes(), input_rate)
        return frames, peak_level

    media.start_recording()
    try:
        for step in steps:
            before_step(step)
            _drain(media)
            detector.reset()
            warmup_frames, _ = feed(_WARMUP_S)
            scores.clear()
            on_start(step)
            frames, peak_level = feed(step.seconds)
            if warmup_frames + frames == 0:
                raise NoMicrophoneAudioError(step.name)
            results.append(StepResult(step, max(scores, default=0.0), score_events(scores), frames, peak_level))
    finally:
        media.stop_recording()
    return results


def format_report(results: Sequence[StepResult]) -> str:
    """Render per-step peaks and, per candidate threshold, wake recall and false events."""
    header = f"{'step':<12}{'kind':<12}{'level':>7}{'peak':>6}" + "".join(
        f"{f'>={t:g}':>8}" for t in CANDIDATE_THRESHOLDS
    )
    lines = [
        "Events per step at each candidate threshold (score runs merged within 1 s); level = peak mic RMS",
        "",
        header,
    ]
    for result in results:
        counts = "".join(f"{result.events_at(t):>8d}" for t in CANDIDATE_THRESHOLDS)
        lines.append(
            f"{result.step.name:<12}{result.step.kind:<12}{result.peak_level:>7.0f}{result.peak:>6.2f}{counts}"
        )

    wake = [r for r in results if r.step.kind == "wake"]
    similar = [r for r in results if r.step.kind == "similar"]
    background = [r for r in results if r.step.kind == "background"]
    background_minutes = sum(r.step.seconds for r in background) / 60.0
    lines += ["", f"{'threshold':<12}{'wake recall':>12}{'similar hits':>14}{'background/min':>16}"]
    for threshold in CANDIDATE_THRESHOLDS:
        recall = sum(1 for r in wake if r.peak >= threshold)
        similar_hits = sum(1 for r in similar if r.peak >= threshold)
        false_events = sum(r.events_at(threshold) for r in background)
        per_minute = false_events / background_minutes if background_minutes else 0.0
        lines.append(
            f"{threshold:<12g}{f'{recall}/{len(wake)}':>12}{f'{similar_hits}/{len(similar)}':>14}{per_minute:>16.2f}"
        )
    lines.append("")
    lines.append("Pick the highest threshold that still wakes on (nearly) every 'Reachy' with no background events.")
    if results and all(r.peak_level == 0.0 for r in results):
        lines.append(
            "WARNING: the microphone delivered only exact silence, so these scores are meaningless. Allow the "
            "terminal app in System Settings > Privacy & Security > Microphone, or restart the Reachy daemon."
        )
    return "\n".join(lines)


def _prompt(step: CalibrationStep) -> None:
    input(f"\n[{step.name}] {step.instruction} ({step.seconds:.0f} s)\nPress Enter, then wait for 'Now'...")


def _announce(step: CalibrationStep) -> None:
    print(f"  Now! ({step.seconds:.0f} s)", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the guided wake calibration against the connected robot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        default=config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR or None,
        help="official33 model directory; defaults to LOCAL_ACOUSTIC_WAKE_MODEL_DIR.",
    )
    parser.add_argument("--talk-seconds", type=float, default=60.0, help="Length of the ordinary-speech phase.")
    parser.add_argument(
        "--media-backend",
        choices=["default", "local", "webrtc"],
        default="default",
        help="Media backend passed to ReachyMini, as in the app.",
    )
    args = parser.parse_args(argv)
    if not args.model_dir:
        parser.error("--model-dir is required when LOCAL_ACOUSTIC_WAKE_MODEL_DIR is not set")
    if args.talk_seconds <= 0:
        parser.error("--talk-seconds must be positive")
    model_dir = Path(args.model_dir).expanduser()

    def make_detector(observer: Callable[[float], None]) -> OpenWakeWordDetector:
        # Threshold 1 keeps the detector from logging wakes; the observer records every score.
        return OpenWakeWordDetector.from_directory(model_dir, threshold=1.0, score_observer=observer)

    try:
        with ReachyMini(media_backend=args.media_backend) as robot:
            results = run_calibration(
                robot.media,
                make_detector,
                steps=default_steps(args.talk_seconds),
                before_step=_prompt,
                on_start=_announce,
            )
    except NoMicrophoneAudioError as error:
        logger.error(
            "No microphone audio arrived during step %s. Restart the Reachy daemon (or the robot) and try again.",
            error,
        )
        return 3
    except (ConnectionError, TimeoutError) as error:
        logger.error("Could not connect to Reachy Mini: %s", error)
        return 2
    except KeyboardInterrupt:
        logger.warning("Wake calibration cancelled")
        return 130
    print()
    print(format_report(results))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
