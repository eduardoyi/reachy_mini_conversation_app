"""Local transcript-based wake-word detection for the hybrid runtime."""

import re
import math
import time
import logging
from typing import Literal, Protocol, TypeAlias
from dataclasses import dataclass
from collections.abc import Callable


logger = logging.getLogger(__name__)

WakeWordSource: TypeAlias = Literal["partial", "final", "acoustic"]
MonotonicClock: TypeAlias = Callable[[], float]

DEFAULT_LOCAL_WAKE_WORD = "Reachy"
_DEFAULT_COOLDOWN_SECONDS = 1.0
_MAX_PARTIAL_TEXT_CHARS = 512
_LEADING_COMMAND_PUNCTUATION = " \t\r\n,.;:!?-—–()[]{}\"'"


@dataclass(frozen=True, slots=True)
class WakeWordDetection:
    """Describe one local detection without retaining its source transcript."""

    wake_word: str
    remaining_text: str
    first_word_retained: bool
    wake_word_is_first_word: bool
    source: WakeWordSource
    detected_at: float
    latency_seconds: float | None

    @property
    def command_text(self) -> str:
        """Return text after the wake word for local-command classification."""
        return self.remaining_text


@dataclass(frozen=True, slots=True)
class WakeWordMetricsSnapshot:
    """Expose bounded, transcript-free detector counters."""

    detection_count: int
    false_positive_count: int
    missed_activation_count: int
    cooldown_suppressed_count: int
    partial_update_count: int
    final_update_count: int
    latency_sample_count: int
    total_latency_seconds: float
    average_latency_seconds: float | None


class WakeWordDetector(Protocol):
    """Define the local detector lifecycle and transcript boundary."""

    def process_transcript(
        self,
        text: str,
        *,
        is_final: bool = False,
        speech_started_at: float | None = None,
        observed_at: float | None = None,
    ) -> WakeWordDetection | None:
        """Inspect a local transcript update and return a detection if present."""

    def reset(self) -> None:
        """Reset the current utterance and cooldown state."""

    def consume_detection(self) -> None:
        """Clear the consumed utterance without resetting cooldown or metrics."""

    def metrics_snapshot(self) -> WakeWordMetricsSnapshot:
        """Return transcript-free counters for benchmark reporting."""


class AcousticWakeWordDetector(Protocol):
    """Define the local acoustic detector lifecycle and PCM boundary."""

    def process_audio(
        self,
        audio: bytes,
        sample_rate: int,
        *,
        observed_at: float | None = None,
    ) -> WakeWordDetection | None:
        """Inspect local audio and return a detection without contacting cloud services."""

    def reset(self) -> None:
        """Reset detector state."""

    def metrics_snapshot(self) -> WakeWordMetricsSnapshot:
        """Return transcript-free counters for benchmark reporting."""


# Spellings local STT commonly produces for "Reachy" ("Richie" is accepted as a wake word by the household).
REACHY_TRANSCRIPT_ALIASES: tuple[str, ...] = ("reachie", "reechy", "richie", "richy", "ritchie")


class TranscriptWakeWordDetector:
    """Detect a bounded wake phrase from local partial and final transcripts."""

    def __init__(
        self,
        wake_word: str = DEFAULT_LOCAL_WAKE_WORD,
        *,
        aliases: tuple[str, ...] = (),
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        """Initialize a detector with a monotonic cooldown clock; ``aliases`` are extra accepted spellings."""
        self._wake_word, self._wake_word_pattern = self._compile_wake_word(wake_word, aliases)
        if not math.isfinite(cooldown_seconds) or cooldown_seconds < 0:
            raise ValueError("Wake-word cooldown must be a finite non-negative number")
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._partial_text = ""
        self._segment_detected = False
        self._last_detection_at: float | None = None
        self._last_observed_at: float | None = None
        self._detection_count = 0
        self._false_positive_count = 0
        self._missed_activation_count = 0
        self._cooldown_suppressed_count = 0
        self._partial_update_count = 0
        self._final_update_count = 0
        self._latency_sample_count = 0
        self._total_latency_seconds = 0.0

    @property
    def wake_word(self) -> str:
        """Return the configured display form of the wake word."""
        return self._wake_word

    @property
    def cooldown_seconds(self) -> float:
        """Return the configured duplicate-detection cooldown."""
        return self._cooldown_seconds

    def process_transcript(
        self,
        text: str,
        *,
        is_final: bool = False,
        speech_started_at: float | None = None,
        observed_at: float | None = None,
    ) -> WakeWordDetection | None:
        """Inspect one local transcript update and preserve any command suffix."""
        if not isinstance(text, str):
            raise TypeError("Wake-word transcript must be a string")

        now = self._clock() if observed_at is None else observed_at
        self._validate_observation_timestamp(now)
        self._last_observed_at = now

        if is_final:
            self._final_update_count += 1
        else:
            self._partial_update_count += 1

        bounded_text = self._bounded_text(text)
        direct_match = self._wake_word_pattern.search(bounded_text)
        candidate = (
            bounded_text
            if direct_match is not None
            else self._bounded_text(self._merge_partial_text(self._partial_text, bounded_text))
        )
        match = direct_match if direct_match is not None else self._wake_word_pattern.search(candidate)
        detection: WakeWordDetection | None = None

        if match is not None and not self._segment_detected:
            self._segment_detected = True
            if self._is_in_cooldown(now):
                self._cooldown_suppressed_count += 1
            else:
                latency_seconds = self._latency_seconds(speech_started_at, now)
                if latency_seconds is not None:
                    self._record_latency(latency_seconds)
                remaining_text = candidate[match.end() :].lstrip(_LEADING_COMMAND_PUNCTUATION)
                prefix = candidate[: match.start()]
                wake_word_is_first_word = not bool(re.search(r"\w", prefix))
                detection = WakeWordDetection(
                    wake_word=self._wake_word,
                    remaining_text=remaining_text,
                    first_word_retained=bool(remaining_text),
                    wake_word_is_first_word=wake_word_is_first_word,
                    source="final" if is_final else "partial",
                    detected_at=now,
                    latency_seconds=latency_seconds,
                )
                self._detection_count += 1
                self._last_detection_at = now
                logger.debug("Local wake word detected from %s transcript", detection.source)

        self._partial_text = "" if is_final else self._bounded_text(candidate)
        if is_final:
            self._segment_detected = False
        return detection

    def reset(self) -> None:
        """Reset the current utterance and allow an immediate new detection."""
        self._partial_text = ""
        self._segment_detected = False
        self._last_detection_at = None
        self._last_observed_at = None

    def consume_detection(self) -> None:
        """Clear consumed partial state while preserving cooldown and metrics."""
        self._partial_text = ""
        self._segment_detected = False

    def metrics_snapshot(self) -> WakeWordMetricsSnapshot:
        """Return transcript-free counters for benchmark reporting."""
        average_latency_seconds = (
            self._total_latency_seconds / self._latency_sample_count if self._latency_sample_count else None
        )
        return WakeWordMetricsSnapshot(
            detection_count=self._detection_count,
            false_positive_count=self._false_positive_count,
            missed_activation_count=self._missed_activation_count,
            cooldown_suppressed_count=self._cooldown_suppressed_count,
            partial_update_count=self._partial_update_count,
            final_update_count=self._final_update_count,
            latency_sample_count=self._latency_sample_count,
            total_latency_seconds=self._total_latency_seconds,
            average_latency_seconds=average_latency_seconds,
        )

    def record_false_positive(self) -> None:
        """Record an externally labelled false activation without storing its transcript."""
        self._false_positive_count += 1

    def record_missed_activation(self) -> None:
        """Record an externally labelled missed activation without storing its transcript."""
        self._missed_activation_count += 1

    def record_activation_latency(self, latency_seconds: float) -> None:
        """Record one externally measured non-negative activation latency."""
        if not math.isfinite(latency_seconds) or latency_seconds < 0:
            raise ValueError("Wake-word latency must be a finite non-negative number")
        self._record_latency(latency_seconds)

    def reset_metrics(self) -> None:
        """Clear benchmark counters while leaving detector configuration unchanged."""
        self._detection_count = 0
        self._false_positive_count = 0
        self._missed_activation_count = 0
        self._cooldown_suppressed_count = 0
        self._partial_update_count = 0
        self._final_update_count = 0
        self._latency_sample_count = 0
        self._total_latency_seconds = 0.0

    def _is_in_cooldown(self, observed_at: float) -> bool:
        last_detection_at = self._last_detection_at
        return last_detection_at is not None and observed_at - last_detection_at < self._cooldown_seconds

    def _validate_observation_timestamp(self, observed_at: float) -> None:
        if not math.isfinite(observed_at):
            raise ValueError("Wake-word observation timestamp must be finite")
        if self._last_observed_at is not None and observed_at < self._last_observed_at:
            raise ValueError("Wake-word observation timestamp cannot move backwards")

    @staticmethod
    def _latency_seconds(started_at: float | None, observed_at: float) -> float | None:
        if started_at is None:
            return None
        latency_seconds = observed_at - started_at
        if not math.isfinite(latency_seconds) or latency_seconds < 0:
            return None
        return latency_seconds

    def _record_latency(self, latency_seconds: float) -> None:
        self._latency_sample_count += 1
        self._total_latency_seconds += latency_seconds

    @staticmethod
    def _compile_wake_word(wake_word: str, aliases: tuple[str, ...] = ()) -> tuple[str, re.Pattern[str]]:
        if not isinstance(wake_word, str) or not all(isinstance(alias, str) for alias in aliases):
            raise TypeError("Wake word must be a string")
        display_word = ""
        alternatives: list[str] = []
        for index, phrase in enumerate((wake_word, *aliases)):
            tokens = tuple(re.findall(r"\w+", phrase.casefold()))
            if not tokens:
                if index == 0:
                    raise ValueError("Wake word must contain at least one word")
                continue
            if index == 0:
                display_word = " ".join(tokens)
            alternatives.append(r"[\W_]+".join(re.escape(token) for token in tokens))
        pattern = re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)", re.IGNORECASE)
        return display_word, pattern

    @staticmethod
    def _bounded_text(text: str) -> str:
        return text[-_MAX_PARTIAL_TEXT_CHARS:]

    @staticmethod
    def _merge_partial_text(previous: str, incoming: str) -> str:
        if not previous:
            return incoming
        previous_folded = previous.casefold()
        incoming_folded = incoming.casefold()
        if incoming_folded.startswith(previous_folded):
            return incoming
        if previous_folded.startswith(incoming_folded):
            return previous
        if previous[-1:].isalnum() and incoming[:1].isalnum():
            return previous + incoming
        return f"{previous} {incoming}"


__all__ = [
    "AcousticWakeWordDetector",
    "MonotonicClock",
    "TranscriptWakeWordDetector",
    "WakeWordDetection",
    "WakeWordDetector",
    "WakeWordMetricsSnapshot",
    "WakeWordSource",
]
