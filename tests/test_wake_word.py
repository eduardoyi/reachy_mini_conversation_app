"""Tests for local transcript wake-word detection."""

import pytest

from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector


class FakeClock:
    """Controllable monotonic clock for cooldown and latency tests."""

    def __init__(self) -> None:
        """Start at a deterministic timestamp."""
        self.value = 100.0

    def __call__(self) -> float:
        """Return the current timestamp."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Advance the timestamp."""
        self.value += seconds


def test_detects_case_insensitive_punctuation_and_retains_command_text() -> None:
    """Punctuation around the phrase does not discard the following command."""
    detector = TranscriptWakeWordDetector()

    detection = detector.process_transcript("  rEaChY, look left!  ", is_final=True)

    assert detection is not None
    assert detection.wake_word == "reachy"
    assert detection.remaining_text == "look left!  "
    assert detection.command_text == "look left!  "
    assert detection.first_word_retained
    assert detection.wake_word_is_first_word
    assert detection.source == "final"


@pytest.mark.parametrize(
    "transcript",
    ["reachyo", "Reachy2", "xReachy", "Reachying", "outreachy"],
)
def test_requires_word_boundaries(transcript: str) -> None:
    """A substring inside another word cannot activate the robot."""
    detector = TranscriptWakeWordDetector()

    assert detector.process_transcript(transcript, is_final=True) is None


def test_reports_when_wake_word_is_not_the_first_word() -> None:
    """The event preserves whether an earlier spoken word preceded the wake phrase."""
    detector = TranscriptWakeWordDetector()

    detection = detector.process_transcript("hey Reachy, hello", is_final=True)

    assert detection is not None
    assert detection.remaining_text == "hello"
    assert detection.first_word_retained
    assert not detection.wake_word_is_first_word


def test_detects_phrase_split_across_delta_partials() -> None:
    """Short delta partials can form the wake word without cloud transcription."""
    detector = TranscriptWakeWordDetector()

    assert detector.process_transcript("Rea") is None
    detection = detector.process_transcript("chy stop")

    assert detection is not None
    assert detection.remaining_text == "stop"
    assert detection.source == "partial"


def test_detects_cumulative_partial_without_duplicate_final_event() -> None:
    """Cumulative STT updates emit once and do not repeat when finalized."""
    detector = TranscriptWakeWordDetector()

    assert detector.process_transcript("Rea") is None
    detection = detector.process_transcript("Reachy look")
    final_detection = detector.process_transcript("Reachy look", is_final=True)

    assert detection is not None
    assert detection.remaining_text == "look"
    assert final_detection is None
    assert detector.metrics_snapshot().detection_count == 1


def test_consume_detection_clears_partial_state_but_preserves_cooldown() -> None:
    """An accepted partial wake can finish without weakening duplicate suppression."""
    clock = FakeClock()
    detector = TranscriptWakeWordDetector(cooldown_seconds=1.0, clock=clock)

    assert detector.process_transcript("Reachy", observed_at=100.0) is not None
    detector.consume_detection()

    clock.advance(0.5)
    assert detector.process_transcript("Reachy", is_final=True) is None
    clock.advance(0.5)
    assert detector.process_transcript("Reachy") is not None

    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 2
    assert metrics.cooldown_suppressed_count == 1


def test_bounds_direct_updates_but_keeps_wake_near_the_retained_tail() -> None:
    """Oversized updates inspect only the newest bounded text and keep its suffix."""
    detector = TranscriptWakeWordDetector()

    detection = detector.process_transcript("x" * 600 + " Reachy " + "y" * 500, is_final=True)

    assert detection is not None
    assert detection.remaining_text == "y" * 500
    assert len(detection.remaining_text) <= 512


def test_drops_a_wake_word_outside_the_retained_tail() -> None:
    """A phrase older than the bounded update is not recovered from discarded text."""
    detector = TranscriptWakeWordDetector()

    detection = detector.process_transcript("Reachy " + "x" * 600, is_final=True)

    assert detection is None


def test_final_update_resets_utterance_for_next_detection() -> None:
    """A final transcript starts a fresh debounce segment."""
    detector = TranscriptWakeWordDetector(cooldown_seconds=0)

    assert detector.process_transcript("Reachy", is_final=True) is not None
    assert detector.process_transcript("Reachy", is_final=True) is not None


def test_cooldown_suppresses_repeated_final_detections() -> None:
    """Separate wake phrases inside the cooldown do not reopen a session."""
    clock = FakeClock()
    detector = TranscriptWakeWordDetector(cooldown_seconds=1.0, clock=clock)

    assert detector.process_transcript("Reachy", is_final=True) is not None
    clock.advance(0.5)
    assert detector.process_transcript("Reachy", is_final=True) is None
    clock.advance(0.5)
    assert detector.process_transcript("Reachy", is_final=True) is not None

    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 2
    assert metrics.cooldown_suppressed_count == 1


def test_reset_clears_cooldown_and_partial_state_but_preserves_metrics() -> None:
    """A reset allows immediate activation without erasing benchmark history."""
    clock = FakeClock()
    detector = TranscriptWakeWordDetector(cooldown_seconds=10, clock=clock)

    assert detector.process_transcript("Rea") is None
    assert detector.process_transcript("chy", is_final=True) is not None
    detector.reset()

    assert detector.process_transcript("Reachy", is_final=True) is not None
    assert detector.metrics_snapshot().detection_count == 2


def test_metrics_record_labels_and_latency_without_transcript_payload() -> None:
    """Counters and latency are retained while source transcript text is not."""
    clock = FakeClock()
    detector = TranscriptWakeWordDetector(clock=clock)

    assert detector.process_transcript("Reachy", speech_started_at=99.25) is not None
    detector.record_false_positive()
    detector.record_missed_activation()
    detector.record_activation_latency(0.25)

    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 1
    assert metrics.false_positive_count == 1
    assert metrics.missed_activation_count == 1
    assert metrics.latency_sample_count == 2
    assert metrics.total_latency_seconds == pytest.approx(1.0)
    assert metrics.average_latency_seconds == pytest.approx(0.5)
    assert "Reachy" not in repr(metrics)


def test_invalid_latency_is_rejected() -> None:
    """Benchmark data must not contain negative or non-finite samples."""
    detector = TranscriptWakeWordDetector()

    with pytest.raises(ValueError):
        detector.record_activation_latency(-0.1)
    with pytest.raises(ValueError):
        detector.record_activation_latency(float("nan"))


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_observation_timestamp_is_rejected(timestamp: float) -> None:
    """Invalid observation timestamps cannot enter detection or cooldown state."""
    detector = TranscriptWakeWordDetector()

    with pytest.raises(ValueError, match="timestamp"):
        detector.process_transcript("Reachy", is_final=True, observed_at=timestamp)

    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 0
    assert metrics.final_update_count == 0


def test_backward_observation_timestamp_is_rejected_without_state_mutation() -> None:
    """Observation time must be nondecreasing so cooldown comparisons stay valid."""
    detector = TranscriptWakeWordDetector(cooldown_seconds=0)

    assert detector.process_transcript("Reachy", is_final=True, observed_at=10.0) is not None
    with pytest.raises(ValueError, match="backwards"):
        detector.process_transcript("Reachy", is_final=True, observed_at=9.0)

    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 1
    assert metrics.final_update_count == 1
    assert detector.process_transcript("Reachy", is_final=True, observed_at=11.0) is not None


def test_invalid_configuration_is_rejected() -> None:
    """Invalid configuration fails explicitly before local state changes."""
    with pytest.raises(ValueError):
        TranscriptWakeWordDetector(wake_word="...")
    with pytest.raises(ValueError):
        TranscriptWakeWordDetector(cooldown_seconds=-1)


def test_default_does_not_accept_historical_tito_transcript() -> None:
    """The written Reachy default does not add a fuzzy or legacy transcript alias."""
    detector = TranscriptWakeWordDetector()

    assert detector.process_transcript("Tito", is_final=True) is None


def test_explicit_historical_tito_override_remains_supported() -> None:
    """A caller can intentionally retain the historical Tito configuration."""
    detector = TranscriptWakeWordDetector(wake_word=" Tito ")

    assert detector.wake_word == "tito"
    assert detector.process_transcript("Tito", is_final=True) is not None


def test_metrics_can_be_cleared_independently_of_detector_state() -> None:
    """Benchmark runs can start fresh without changing the current cooldown policy."""
    detector = TranscriptWakeWordDetector(cooldown_seconds=0)

    assert detector.process_transcript("Reachy", is_final=True) is not None
    detector.record_false_positive()
    detector.reset_metrics()

    metrics = detector.metrics_snapshot()
    assert metrics.detection_count == 0
    assert metrics.false_positive_count == 0
    assert metrics.partial_update_count == 0
    assert metrics.final_update_count == 0


def test_non_monotonic_transcript_timestamp_does_not_record_negative_latency() -> None:
    """An out-of-order local timestamp is ignored instead of creating fake latency."""
    detector = TranscriptWakeWordDetector()

    assert detector.process_transcript("Reachy", speech_started_at=10, observed_at=9) is not None
    assert detector.metrics_snapshot().latency_sample_count == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Ricci! Ricci!", True), ("hola Ritzi", True), ("Richi, apaga la luz", True), ("Ricky came over", False)],
)
def test_mentions_wake_word_knows_the_spanish_spellings_but_not_real_names(text: str, expected: bool) -> None:
    """How a Spanish "Ri-chi" comes out of local STT counts as the name; a real name such as Ricky does not."""
    from reachy_mini_conversation_app.wake_word import REACHY_TRANSCRIPT_ALIASES

    detector = TranscriptWakeWordDetector("Reachy", aliases=REACHY_TRANSCRIPT_ALIASES)
    assert detector.mentions_wake_word(text) is expected
