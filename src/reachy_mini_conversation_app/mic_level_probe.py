"""Measure robot microphone RMS levels against the local VAD threshold without keeping audio."""

import time
import logging
import argparse
from typing import Protocol
from dataclasses import dataclass
from collections.abc import Mapping, Callable, Sequence

import numpy as np
from numpy.typing import NDArray

from reachy_mini import ReachyMini
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.streaming import AudioArray, audio_to_int16, audio_to_mono_int16
from reachy_mini_conversation_app.local_audio_backends import (
    LOCAL_BARGE_IN_ONSET_MS,
    LOCAL_BARGE_IN_REARM_MS,
    RmsEnergyVAD,
    pcm16_rms,
)


logger = logging.getLogger(__name__)

SIGNALS = ("mixed", "channel0")
_PLAYBACK_CHUNK_S = 0.1
_PLAYBACK_LEAD_S = 0.3
_PLAYBACK_SETTLE_S = 1.0
_POLL_INTERVAL_S = 0.005
_MAX_DRAIN_FRAMES = 1000
_MIN_SEPARATION = 1.5


@dataclass(frozen=True)
class ProbePhase:
    """One guided measurement window."""

    name: str
    instruction: str
    speech: bool
    playback: bool


PROBE_PHASES = (
    ProbePhase("quiet", "Stay silent and leave the room as it normally is.", speech=False, playback=False),
    ProbePhase("speech", "Talk normally from where you usually sit.", speech=True, playback=False),
    ProbePhase("playback", "Stay silent while Reachy plays a test sound.", speech=False, playback=True),
    ProbePhase("speech_over_playback", "Talk normally while Reachy plays the test sound.", speech=True, playback=True),
)


class _ProbeMedia(Protocol):
    """The SDK media calls used by the probe."""

    def start_recording(self) -> None: ...
    def stop_recording(self) -> None: ...
    def get_audio_sample(self) -> NDArray[np.float32] | None: ...
    def get_input_audio_samplerate(self) -> int: ...
    def start_playing(self) -> None: ...
    def stop_playing(self) -> None: ...
    def push_audio_sample(self, data: NDArray[np.float32]) -> None: ...
    def get_output_audio_samplerate(self) -> int: ...


@dataclass(frozen=True)
class LevelSummary:
    """Distribution of per-frame RMS levels for one signal during one phase."""

    seconds: float
    p50: float
    p75: float
    p99: float
    peak: float
    fraction_at_threshold: float
    longest_run_ms: float
    onsets: int


class LevelAccumulator:
    """Collect per-frame RMS levels and count barge-in onsets with the providers' latch rule."""

    def __init__(self, threshold: float) -> None:
        """Track levels against an int16-scale RMS threshold."""
        self._vad = RmsEnergyVAD(threshold)
        self._levels: list[float] = []
        self._seconds = 0.0
        self._seconds_at_threshold = 0.0
        self._run_s = 0.0
        self._longest_run_s = 0.0
        self._quiet_s = 0.0
        self._latched = False
        self._onsets = 0

    def add(self, rms: float, duration_s: float) -> None:
        """Record one frame's level and duration."""
        self._levels.append(rms)
        self._seconds += duration_s
        if self._vad.is_speech_level(rms):
            self._seconds_at_threshold += duration_s
            self._quiet_s = 0.0
            self._run_s += duration_s
            self._longest_run_s = max(self._longest_run_s, self._run_s)
            if not self._latched and self._run_s * 1000 >= LOCAL_BARGE_IN_ONSET_MS:
                self._latched = True
                self._onsets += 1
            return
        self._run_s = 0.0
        self._quiet_s += duration_s
        if self._quiet_s * 1000 >= LOCAL_BARGE_IN_REARM_MS:
            self._latched = False

    def summary(self) -> LevelSummary:
        """Return the level distribution, or zeros when no frame arrived."""
        if not self._levels:
            return LevelSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
        p50, p75, p99 = (float(value) for value in np.percentile(self._levels, [50, 75, 99]))
        return LevelSummary(
            seconds=self._seconds,
            p50=p50,
            p75=p75,
            p99=p99,
            peak=max(self._levels),
            fraction_at_threshold=self._seconds_at_threshold / self._seconds if self._seconds else 0.0,
            longest_run_ms=self._longest_run_s * 1000,
            onsets=self._onsets,
        )


def frame_levels(audio: AudioArray) -> tuple[float, float, int]:
    """Return channel-averaged RMS, channel-0 RMS and sample count, as the app's providers see them."""
    mixed = audio_to_mono_int16(audio)
    first_channel = audio
    if audio.ndim == 2:
        if audio.shape[1] > audio.shape[0]:
            first_channel = audio.T
        first_channel = first_channel[:, 0]
    channel0 = audio_to_int16(first_channel).reshape(-1)
    return pcm16_rms(mixed), pcm16_rms(channel0), int(mixed.size)


def probe_sound(sample_rate: int, start_s: float, duration_s: float, level: float) -> NDArray[np.float32]:
    """Return a voice-like buzz with a syllable-rate envelope, phase-continuous across chunks."""
    times = start_s + np.arange(int(sample_rate * duration_s)) / sample_rate
    harmonics = np.arange(1, 21)
    tone = np.sin(2 * np.pi * 150.0 * np.outer(times, harmonics)) @ (1.0 / harmonics)
    envelope = 0.5 * (1.0 - np.cos(2 * np.pi * 4.0 * times))
    return (level * envelope * tone / np.sum(1.0 / harmonics)).astype(np.float32)


def suggest_threshold(results: Mapping[str, Mapping[str, LevelSummary]], signal: str) -> float | None:
    """Suggest a threshold between non-speech noise and speech, or None without clear separation."""
    noise = [results[phase.name][signal].p99 for phase in PROBE_PHASES if not phase.speech and phase.name in results]
    speech = [results[phase.name][signal].p75 for phase in PROBE_PHASES if phase.speech and phase.name in results]
    if not noise or not speech:
        return None
    noise_ceiling = max(noise)
    speech_level = min(speech)
    if noise_ceiling <= 0.0 or speech_level < noise_ceiling * _MIN_SEPARATION:
        return None
    return float(np.sqrt(noise_ceiling * speech_level))


def _drain(media: _ProbeMedia) -> None:
    for _ in range(_MAX_DRAIN_FRAMES):
        if media.get_audio_sample() is None:
            return


def run_probe(
    media: _ProbeMedia,
    *,
    phases: Sequence[ProbePhase],
    seconds: float,
    threshold: float,
    playback_level: float,
    before_phase: Callable[[ProbePhase], None],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, dict[str, LevelSummary]]:
    """Measure each phase and return level summaries keyed by phase and signal."""
    input_rate = int(media.get_input_audio_samplerate())
    output_rate = int(media.get_output_audio_samplerate())
    uses_playback = any(phase.playback for phase in phases)
    results: dict[str, dict[str, LevelSummary]] = {}
    media.start_recording()
    if uses_playback:
        media.start_playing()
    try:
        for phase in phases:
            before_phase(phase)
            _drain(media)
            accumulators = {signal: LevelAccumulator(threshold) for signal in SIGNALS}
            started = clock()
            pushed_s = 0.0
            while (elapsed := clock() - started) < seconds:
                while phase.playback and pushed_s < elapsed + _PLAYBACK_LEAD_S:
                    media.push_audio_sample(probe_sound(output_rate, pushed_s, _PLAYBACK_CHUNK_S, playback_level))
                    pushed_s += _PLAYBACK_CHUNK_S
                frame = media.get_audio_sample()
                if frame is None:
                    sleep(_POLL_INTERVAL_S)
                    continue
                mixed_rms, channel0_rms, samples = frame_levels(frame)
                duration_s = samples / input_rate
                accumulators["mixed"].add(mixed_rms, duration_s)
                accumulators["channel0"].add(channel0_rms, duration_s)
            results[phase.name] = {signal: accumulator.summary() for signal, accumulator in accumulators.items()}
            if phase.playback:
                sleep(_PLAYBACK_SETTLE_S)
    finally:
        media.stop_recording()
        if uses_playback:
            media.stop_playing()
    return results


def format_report(results: Mapping[str, Mapping[str, LevelSummary]], threshold: float) -> str:
    """Render summaries and threshold suggestions as a plain-text table."""
    lines = [
        f"Threshold {threshold:.0f} (int16 RMS); barge-in onset {LOCAL_BARGE_IN_ONSET_MS} ms, "
        f"rearm {LOCAL_BARGE_IN_REARM_MS} ms",
        "mixed = channel average (hybrid VAD, OpenAI Live); channel0 = Gemini Live and Hugging Face",
        "",
        f"{'phase':<22}{'signal':<10}{'secs':>6}{'p50':>8}{'p75':>8}{'p99':>8}{'peak':>8}"
        f"{'>=thr':>8}{'run ms':>8}{'onsets':>8}",
    ]
    for phase_name, by_signal in results.items():
        for signal, summary in by_signal.items():
            lines.append(
                f"{phase_name:<22}{signal:<10}{summary.seconds:>6.1f}{summary.p50:>8.0f}{summary.p75:>8.0f}"
                f"{summary.p99:>8.0f}{summary.peak:>8.0f}{summary.fraction_at_threshold:>8.0%}"
                f"{summary.longest_run_ms:>8.0f}{summary.onsets:>8d}"
            )
    lines.append("")
    for signal in SIGNALS:
        suggestion = suggest_threshold(results, signal)
        if suggestion is None:
            lines.append(f"{signal}: no clear separation between speech and non-speech levels.")
        else:
            lines.append(f"{signal}: candidate threshold about {suggestion:.0f}.")
    lines.append("Onsets in non-speech phases would be false barge-ins at the current threshold.")
    if all(summary.peak == 0.0 for by_signal in results.values() for summary in by_signal.values()):
        lines.append(
            "WARNING: every sample was exactly 0. A live microphone always picks up some noise, so this is "
            "usually blocked input: allow the app running this terminal in System Settings > Privacy & "
            "Security > Microphone, then restart that app."
        )
    return "\n".join(lines)


def _prompt(phase: ProbePhase) -> None:
    input(f"\n[{phase.name}] {phase.instruction}\nPress Enter to start...")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the guided microphone level probe against the connected robot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10.0, help="Measurement time per phase.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="RMS threshold to evaluate; defaults to LOCAL_VAD_RMS_THRESHOLD.",
    )
    parser.add_argument(
        "--playback-level", type=float, default=0.3, help="Peak amplitude (0-1) of the robot test sound."
    )
    parser.add_argument("--no-playback", action="store_true", help="Skip the phases that use the robot speaker.")
    parser.add_argument(
        "--media-backend",
        choices=["default", "local", "webrtc"],
        default="default",
        help="Media backend passed to ReachyMini, as in the app.",
    )
    args = parser.parse_args(argv)
    if args.seconds <= 0 or not 0.0 < args.playback_level <= 1.0:
        parser.error("--seconds must be positive and --playback-level must be in (0, 1]")
    threshold = config.LOCAL_VAD_RMS_THRESHOLD if args.threshold is None else args.threshold
    phases = [phase for phase in PROBE_PHASES if not (args.no_playback and phase.playback)]

    try:
        with ReachyMini(media_backend=args.media_backend) as robot:
            results = run_probe(
                robot.media,
                phases=phases,
                seconds=args.seconds,
                threshold=threshold,
                playback_level=args.playback_level,
                before_phase=_prompt,
            )
    except (ConnectionError, TimeoutError) as error:
        logger.error("Could not connect to Reachy Mini: %s", error)
        return 2
    except KeyboardInterrupt:
        logger.warning("Microphone level probe cancelled")
        return 130
    print()
    print(format_report(results, threshold))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
