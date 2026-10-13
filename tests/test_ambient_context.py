"""Tests for dependency-free local ambient context processing."""

import asyncio
import logging
import threading

import numpy as np
import pytest

from reachy_mini_conversation_app.ambient_context import (
    RollingTranscript,
    TranscriptSegment,
    AmbientContextConfig,
    AmbientContextStrategy,
    AmbientContextProcessor,
)
from reachy_mini_conversation_app.conversation_handler import AudioFrame


class SequenceVAD:
    """Return deterministic speech labels for successive frames."""

    def __init__(self, labels: list[bool | BaseException]) -> None:
        """Initialize the speech-label sequence."""
        self.labels = labels
        self.frames: list[AudioFrame] = []

    def is_speech(self, frame: AudioFrame) -> bool:
        """Return the next label and retain only a defensive test copy."""
        self.frames.append((frame[0], frame[1].copy()))
        label = self.labels.pop(0)
        if isinstance(label, BaseException):
            raise label
        return label


class MappingSTT:
    """Return deterministic text while recording copied local inputs."""

    def __init__(self, texts: list[str]) -> None:
        """Initialize the text sequence."""
        self.texts = texts
        self.audio: list[AudioFrame] = []

    def transcribe(self, audio: AudioFrame) -> str:
        """Record a defensive copy and return the next text."""
        self.audio.append((audio[0], audio[1].copy()))
        return self.texts.pop(0)


class BlockingSTT:
    """Hold a transcription until the test explicitly releases it."""

    def __init__(self, texts: list[str]) -> None:
        """Initialize the blocking fake."""
        self.texts = texts
        self.started: list[asyncio.Event] = []
        self.release: list[asyncio.Event] = []

    async def transcribe(self, audio: AudioFrame) -> str:
        """Wait on the call-specific release event."""
        del audio
        index = len(self.started)
        started = asyncio.Event()
        release = asyncio.Event()
        self.started.append(started)
        self.release.append(release)
        started.set()
        await release.wait()
        return self.texts[index]


class BlockingSyncSTT:
    """Hold synchronous worker calls until the test explicitly releases them."""

    def __init__(self) -> None:
        """Initialize bounded-call diagnostics and release control."""
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.frame_lengths: list[int] = []

    def transcribe(self, audio: AudioFrame) -> str:
        """Block one sync call while retaining only its input-size metadata."""
        with self._lock:
            self.calls += 1
            call_number = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.frame_lengths.append(audio[1].size)
        self.started.set()
        self.release.wait(timeout=5)
        with self._lock:
            self.active -= 1
        return f"segment {call_number}"


class BlockingSyncVAD:
    """Hold synchronous VAD calls until the test explicitly releases them."""

    def __init__(self) -> None:
        """Initialize bounded-call diagnostics and release control."""
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def is_speech(self, frame: AudioFrame) -> bool:
        """Block one sync call and return speech after release."""
        del frame
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        self.release.wait(timeout=5)
        with self._lock:
            self.active -= 1
        return True


class FailingThenWorkingSTT:
    """Fail one local segment and successfully transcribe the next one."""

    def __init__(self) -> None:
        """Initialize the call counter."""
        self.calls = 0

    def transcribe(self, audio: AudioFrame) -> str:
        """Raise once, then return derived text."""
        del audio
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("stt sentinel")
        return "surviving segment"


def frame(value: int, *, sample_rate: int = 100) -> AudioFrame:
    """Return a deterministic mono frame with one tenth of a second of audio."""
    return sample_rate, np.full(sample_rate // 10, value, dtype=np.int16)


def processor_config(**overrides: object) -> AmbientContextConfig:
    """Return short deterministic bounds for unit tests."""
    values: dict[str, object] = {
        "transcript_window_seconds": 20.0,
        "max_transcript_chars": 100,
        "min_speech_seconds": 0.1,
        "min_silence_seconds": 0.2,
        "vad_timeout_seconds": 0.2,
        "stt_timeout_seconds": 0.2,
    }
    values.update(overrides)
    return AmbientContextConfig(**values)


def test_config_rejects_unbounded_or_invalid_local_processing_limits() -> None:
    """Local context limits fail closed before a processor accepts audio."""
    with pytest.raises(ValueError, match="transcript_window_seconds"):
        processor_config(transcript_window_seconds=0)
    with pytest.raises(ValueError, match="max_transcript_chars"):
        processor_config(max_transcript_chars=0)
    with pytest.raises(ValueError, match="stt_timeout_seconds"):
        processor_config(stt_timeout_seconds=float("inf"))
    with pytest.raises(ValueError, match="max_pending_stt_tasks"):
        processor_config(max_pending_stt_tasks=0)
    with pytest.raises(ValueError, match="max_pending_stt_tasks"):
        processor_config(max_pending_stt_tasks=1.5)


def test_rolling_transcript_normalizes_text_and_rejects_stale_results() -> None:
    """Transcript updates are ordered, deduplicated, and text-only."""
    transcript = RollingTranscript(max_seconds=10, max_chars=100)

    assert transcript.add(TranscriptSegment("  first\nthought  ", 0, 1, 1))
    assert not transcript.add(TranscriptSegment("duplicate", 0, 1, 1))
    assert not transcript.add(TranscriptSegment("old", 0, 0.5, 0))
    assert not transcript.add(TranscriptSegment("late timestamp", 0, 0.5, 2))
    assert transcript.text() == "first thought"
    assert transcript.segments[0].sequence == 1


def test_rolling_transcript_replaces_partial_and_bounds_time_and_text() -> None:
    """Partial text is not context until finalized, then old and long text are bounded."""
    transcript = RollingTranscript(max_seconds=2, max_chars=12)

    assert transcript.add(TranscriptSegment("partial", 0, 1, 1, final=False))
    assert transcript.text() == ""
    assert transcript.add(TranscriptSegment("first sentence", 0, 1, 1))
    assert transcript.add(TranscriptSegment("newest sentence", 2, 3, 2))
    assert len(transcript.text()) <= 12
    assert transcript.text(now=10) == ""


def test_rolling_transcript_rejects_partial_after_same_sequence_was_finalized() -> None:
    """A reordered partial cannot reopen or duplicate a finalized segment."""
    transcript = RollingTranscript(max_seconds=20, max_chars=100)

    assert transcript.add(TranscriptSegment("final once", 1, 2, 1))
    assert not transcript.add(TranscriptSegment("late partial", 1, 2.1, 1, final=False))
    assert not transcript.add(TranscriptSegment("duplicate final", 1, 2.2, 1))

    assert transcript.text() == "final once"


def test_rolling_transcript_uses_an_exclusive_activation_sequence_fence() -> None:
    """Activation context keeps only entries that predate the wake sequence."""
    transcript = RollingTranscript(max_seconds=20, max_chars=100)
    assert transcript.add(TranscriptSegment("prior context", 1, 2, 1))
    assert transcript.add(TranscriptSegment("Reachy", 3, 4, 2))
    assert transcript.add(TranscriptSegment("late ambient", 5, 6, 3))
    assert transcript.add(TranscriptSegment("later ambient", 7, 8, 4))

    assert transcript.text(before_sequence=2) == "prior context"


@pytest.mark.asyncio
async def test_context_sequence_fence_matches_filtered_source_metadata() -> None:
    """Later accepted ambient entries cannot leak into fenced text or metadata."""
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=MappingSTT([]),
        config=processor_config(),
        clock=lambda: 8.0,
    )
    assert processor.transcript.add(TranscriptSegment("Earlier dinner context.", 1, 2, 1))
    assert processor.transcript.add(TranscriptSegment("Reachy", 3, 4, 2))
    assert processor.transcript.add(TranscriptSegment("A later ambient sentence.", 5, 6, 3))
    assert processor.transcript.add(TranscriptSegment("Another later sentence.", 7, 8, 4))

    context = await processor.context_for_activation(before_sequence=2)

    assert context.text == "Earlier dinner context."
    assert context.source_sequence == 1
    assert context.source_ended_at == 2
    await processor.close()


@pytest.mark.asyncio
async def test_context_metadata_matches_pruned_text() -> None:
    """Expired transcript entries cannot survive only in activation metadata."""
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=MappingSTT([]),
        config=processor_config(transcript_window_seconds=2.0),
        clock=lambda: 10.0,
    )
    assert processor.transcript.add(TranscriptSegment("expired", 1, 2, 1))

    context = await processor.context_for_activation()

    assert context.text == ""
    assert context.source_ended_at is None
    assert context.source_sequence is None
    await processor.close()


@pytest.mark.parametrize("window_seconds", [2.0, 5.0, 10.0, 20.0])
def test_rolling_transcript_honors_benchmark_window_sizes(window_seconds: float) -> None:
    """Every planned context window drops speech older than its own boundary."""
    transcript = RollingTranscript(max_seconds=window_seconds, max_chars=1_000)
    assert transcript.add(TranscriptSegment("too old", 0.0, 1.0, 1))
    newest_time = window_seconds + 1.1
    assert transcript.add(TranscriptSegment("still relevant", newest_time, newest_time, 2))

    assert transcript.text() == "still relevant"


@pytest.mark.asyncio
async def test_strategy_a_segments_speech_and_keeps_audio_local() -> None:
    """Continuous STT receives only a copied mono segment and yields context text."""
    vad = SequenceVAD([True, True, False, False])
    stt = MappingSTT(["  indoors tomorrow  "])
    observed: list[TranscriptSegment] = []
    processor = AmbientContextProcessor(
        vad=vad,
        stt=stt,
        config=processor_config(),
        transcript_observer=observed.append,
    )
    source = np.full(10, 1234, dtype=np.int16)

    await processor.observe_frame((100, source))
    await processor.observe_frame(frame(2))
    await processor.observe_frame(frame(3))
    await processor.observe_frame(frame(4))
    source[:] = -4321
    await processor.drain()

    context = await processor.context_for_activation()
    assert context.strategy is AmbientContextStrategy.CONTINUOUS_TRANSCRIPT
    assert context.text == "indoors tomorrow"
    assert len(stt.audio) == 1
    np.testing.assert_array_equal(stt.audio[0][1], [1234] * 10 + [2] * 10)
    assert observed[0].text == "indoors tomorrow"
    assert processor.metrics.continuous_stt_requests == 1
    assert processor.metrics.continuous_stt_completed == 1
    assert processor.metrics.transcript_segments_accepted == 1
    await processor.close()


@pytest.mark.asyncio
async def test_continuous_stt_caps_long_speech_even_with_sixty_second_text_context() -> None:
    """A long continuous segment sends at most twenty seconds of local PCM to STT."""
    vad = SequenceVAD([True] * 210 + [False, False])
    stt = MappingSTT(["long speech"])
    processor = AmbientContextProcessor(
        vad=vad,
        stt=stt,
        config=processor_config(transcript_window_seconds=60.0),
    )

    for value in range(212):
        await processor.observe_frame(frame(value, sample_rate=100))
    await processor.drain()

    assert len(stt.audio) == 1
    assert stt.audio[0][1].size == 20 * 100
    assert processor.transcript.text() == "long speech"
    await processor.close()


@pytest.mark.asyncio
async def test_continuous_stt_trims_an_oversized_single_frame_without_empty_concat() -> None:
    """One frame longer than the STT cap is trimmed safely before concatenation."""
    vad = SequenceVAD([True, False, False])
    stt = MappingSTT(["oversized frame"])
    processor = AmbientContextProcessor(
        vad=vad,
        stt=stt,
        config=processor_config(transcript_window_seconds=60.0),
    )

    await processor.observe_frame((100, np.arange(2_100, dtype=np.int16)))
    await processor.observe_frame(frame(0, sample_rate=100))
    await processor.observe_frame(frame(0, sample_rate=100))
    await processor.drain()

    assert len(stt.audio) == 1
    assert stt.audio[0][1].size == 20 * 100
    np.testing.assert_array_equal(stt.audio[0][1], np.arange(100, 2_100, dtype=np.int16))
    assert processor.transcript.text() == "oversized frame"
    await processor.close()


@pytest.mark.asyncio
async def test_transcript_observer_can_be_attached_after_runtime_construction() -> None:
    """The hybrid runtime can become the observer without a circular constructor."""
    observed: list[TranscriptSegment] = []
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False]),
        stt=MappingSTT(["Reachy stop"]),
        config=processor_config(),
    )
    processor.set_transcript_observer(observed.append)

    for value in range(3):
        await processor.observe_frame(frame(value))
    await processor.drain()

    assert [segment.text for segment in observed] == ["Reachy stop"]
    processor.set_transcript_observer(None)
    await processor.close()


@pytest.mark.asyncio
async def test_strategy_a_commits_out_of_order_stt_results_chronologically() -> None:
    """A slower earlier STT result is not lost or allowed to reorder context."""
    vad = SequenceVAD([True, False, False, True, False, False])
    stt = BlockingSTT(["first", "second"])
    processor = AmbientContextProcessor(vad=vad, stt=stt, config=processor_config())

    for value in range(6):
        await processor.observe_frame(frame(value))
    while len(stt.started) < 2:
        await asyncio.sleep(0)
    await asyncio.gather(stt.started[0].wait(), stt.started[1].wait())

    stt.release[1].set()
    await asyncio.sleep(0)
    assert processor.transcript.text() == ""
    stt.release[0].set()
    await processor.drain()

    assert processor.transcript.text() == "first second"
    assert [segment.sequence for segment in processor.transcript.segments] == [1, 2]
    await processor.close()


@pytest.mark.asyncio
async def test_strategy_a_bounds_pending_stt_backlog_and_advances_ordering(caplog) -> None:
    """Slow local STT cannot retain unbounded audio segments in memory."""
    stt = BlockingSTT(["first", "second"])
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False] * 3),
        stt=stt,
        config=processor_config(max_pending_stt_tasks=2),
    )

    with caplog.at_level(logging.WARNING):
        for value in range(9):
            await processor.observe_frame(frame(value))
    while len(stt.started) < 2:
        await asyncio.sleep(0)

    assert processor.metrics.continuous_stt_requests == 2
    assert processor.metrics.continuous_stt_dropped == 1
    assert processor.metrics.stt_inflight_peak == 2
    assert "backlog full" in caplog.text
    assert "private" not in caplog.text

    stt.release[0].set()
    stt.release[1].set()
    await processor.drain()
    assert processor.transcript.text() == "first second"
    await processor.close()


@pytest.mark.asyncio
async def test_strategy_a_activation_does_not_wait_for_inflight_stt() -> None:
    """Activation returns existing context immediately while continuous STT finishes later."""
    stt = BlockingSTT(["slow segment"])
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False]),
        stt=stt,
        config=processor_config(),
    )

    await processor.observe_frame(frame(1))
    await processor.observe_frame(frame(2))
    await processor.observe_frame(frame(3))
    while not stt.started:
        await asyncio.sleep(0)

    context = await asyncio.wait_for(processor.context_for_activation(), timeout=0.05)
    assert context.text == ""
    assert processor.metrics.continuous_stt_pending_at_activation == 1

    stt.release[0].set()
    await processor.drain()
    assert (await processor.context_for_activation()).text == "slow segment"
    await processor.close()


@pytest.mark.asyncio
async def test_strategy_b_transcribes_only_activation_snapshot_and_returns_text() -> None:
    """Activation-time STT sees a RAM snapshot and never exposes that snapshot."""
    vad = SequenceVAD([])
    stt = MappingSTT(["aquarium tomorrow"])
    processor = AmbientContextProcessor(
        vad=vad,
        stt=stt,
        config=processor_config(strategy=AmbientContextStrategy.ACTIVATION_BUFFER, max_transcript_chars=10),
    )
    source = np.array([[100, 300], [-100, -300]], dtype=np.int16)

    context = await processor.context_for_activation((100, source))
    source[:] = 0

    assert context.strategy is AmbientContextStrategy.ACTIVATION_BUFFER
    assert len(context.text) == 10
    assert context.text.endswith("tomorrow")
    assert len(processor.transcript.segments) == 0
    assert processor.metrics.activation_stt_requests == 1
    assert processor.metrics.activation_stt_completed == 1
    np.testing.assert_array_equal(stt.audio[0][1], [200, -200])
    await processor.close()


@pytest.mark.asyncio
async def test_short_speech_is_closed_at_silence_boundary_before_next_utterance() -> None:
    """A sub-threshold noise burst cannot merge into a later valid utterance."""
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False, True, True, False, False]),
        stt=MappingSTT(["valid utterance"]),
        config=processor_config(min_speech_seconds=0.2),
    )

    for value in range(7):
        await processor.observe_frame(frame(value))
    await processor.drain()

    assert processor.transcript.text() == "valid utterance"
    assert processor.metrics.continuous_stt_requests == 1
    await processor.close()


@pytest.mark.asyncio
async def test_sample_rate_change_discards_old_sequence_without_blocking_new_one() -> None:
    """A mixed-rate speech segment is discarded and the next rate can commit."""
    stt = MappingSTT(["new rate utterance"])
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, True, False, False]),
        stt=stt,
        config=processor_config(),
    )

    await processor.observe_frame(frame(1, sample_rate=100))
    await processor.observe_frame(frame(2, sample_rate=200))
    await processor.observe_frame(frame(3, sample_rate=200))
    await processor.observe_frame(frame(4, sample_rate=200))
    await processor.drain()

    assert processor.transcript.text() == "new rate utterance"
    assert processor.metrics.continuous_stt_requests == 1
    await processor.close()


@pytest.mark.asyncio
async def test_activation_transcription_rejects_stale_concurrent_result() -> None:
    """Only the newest activation request may provide context to a cloud session."""
    stt = BlockingSTT(["old", "new"])
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=stt,
        config=processor_config(strategy=AmbientContextStrategy.ACTIVATION_BUFFER),
    )
    first_task = asyncio.create_task(processor.context_for_activation(frame(1)))
    second_task = asyncio.create_task(processor.context_for_activation(frame(2)))
    while len(stt.started) < 2:
        await asyncio.sleep(0)

    stt.release[1].set()
    second_context = await second_task
    stt.release[0].set()
    first_context = await first_task

    assert second_context.text == "new"
    assert first_context.text == ""
    assert processor.metrics.stale_activation_contexts_rejected == 1
    await processor.close()


@pytest.mark.asyncio
async def test_activation_cancellation_propagates_and_releases_local_resources() -> None:
    """Cancelling activation does not become a fake context or leaked task."""

    class CancellableSTT:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def transcribe(self, audio: AudioFrame) -> str:
            del audio
            self.started.set()
            await asyncio.sleep(10)
            return "never"

    stt = CancellableSTT()
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=stt,
        config=processor_config(strategy=AmbientContextStrategy.ACTIVATION_BUFFER),
    )
    activation_task = asyncio.create_task(processor.context_for_activation(frame(1)))
    await stt.started.wait()
    activation_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await activation_task
    assert processor.metrics.activation_stt_requests == 1
    await processor.close()


@pytest.mark.asyncio
async def test_sync_activation_timeouts_have_bounded_admission_and_honest_close() -> None:
    """Timed-out sync STT keeps its slot until the worker really exits."""
    stt = BlockingSyncSTT()
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=stt,
        config=processor_config(
            strategy=AmbientContextStrategy.ACTIVATION_BUFFER,
            stt_timeout_seconds=0.01,
            max_pending_stt_tasks=2,
        ),
    )

    try:
        for value in range(5):
            context = await processor.context_for_activation(frame(value))
            assert context.text == ""

        for _ in range(100):
            if (
                stt.calls == 2
                and processor.metrics.local_work_pending == 2
                and processor.metrics.activation_stt_timeouts == 2
            ):
                break
            await asyncio.sleep(0.001)
        assert stt.calls == 2
        assert stt.max_active == 2
        assert len(stt.frame_lengths) == 2
        assert processor.metrics.activation_stt_requests == 5
        assert processor.metrics.activation_stt_timeouts == 2
        assert processor.metrics.activation_stt_failures == 3
        assert processor.metrics.local_work_pending == 2

        await asyncio.wait_for(processor.close(), timeout=0.1)
        assert processor.metrics.local_work_pending == 2

        stt.release.set()
        for _ in range(100):
            if processor.metrics.local_work_pending == 0:
                break
            await asyncio.sleep(0.001)
        assert processor.metrics.local_work_pending == 0
        assert stt.active == 0
    finally:
        stt.release.set()
        await processor.close()


@pytest.mark.asyncio
async def test_sync_continuous_stt_timeouts_do_not_queue_unbounded_audio() -> None:
    """Repeated sync STT timeouts retain no more than admitted segment copies."""
    stt = BlockingSyncSTT()
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False] * 3),
        stt=stt,
        config=processor_config(stt_timeout_seconds=0.01, max_pending_stt_tasks=2),
    )

    try:
        for value in range(9):
            await processor.observe_frame(frame(value))
        for _ in range(100):
            if (
                stt.calls == 2
                and processor.metrics.local_work_pending == 2
                and processor.metrics.continuous_stt_timeouts == 2
            ):
                break
            await asyncio.sleep(0.001)

        assert stt.calls == 2
        assert stt.max_active == 2
        assert len(stt.frame_lengths) == 2
        assert processor.metrics.continuous_stt_requests == 2
        assert processor.metrics.continuous_stt_dropped == 1
        assert processor.metrics.continuous_stt_timeouts == 2
        assert processor.metrics.stt_inflight_peak == 2
        assert processor.metrics.local_work_pending == 2

        await asyncio.wait_for(processor.close(), timeout=0.1)
        assert processor.metrics.local_work_pending == 2

        stt.release.set()
        for _ in range(100):
            if processor.metrics.local_work_pending == 0:
                break
            await asyncio.sleep(0.001)
        assert processor.metrics.local_work_pending == 0
        assert stt.active == 0
    finally:
        stt.release.set()
        await processor.close()


@pytest.mark.asyncio
async def test_sync_vad_timeout_drops_following_frames_until_worker_exits() -> None:
    """A blocking sync VAD cannot accumulate uncancellable frame work."""
    vad = BlockingSyncVAD()
    processor = AmbientContextProcessor(
        vad=vad,
        stt=MappingSTT([]),
        config=processor_config(vad_timeout_seconds=0.01),
    )

    try:
        await processor.observe_frame(frame(1))
        for value in range(2, 8):
            await processor.observe_frame(frame(value))

        for _ in range(100):
            if vad.calls == 1 and processor.metrics.local_work_pending == 1:
                break
            await asyncio.sleep(0.001)
        assert vad.calls == 1
        assert vad.max_active == 1
        assert processor.metrics.vad_timeouts == 1
        assert processor.metrics.vad_dropped == 6
        assert processor.metrics.local_work_pending == 1

        await asyncio.wait_for(processor.close(), timeout=0.1)
        assert processor.metrics.local_work_pending == 1

        vad.release.set()
        for _ in range(100):
            if processor.metrics.local_work_pending == 0:
                break
            await asyncio.sleep(0.001)
        assert processor.metrics.local_work_pending == 0
        assert vad.active == 0
    finally:
        vad.release.set()
        await processor.close()


@pytest.mark.asyncio
async def test_local_stt_timeout_degrades_to_empty_context_without_raw_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hanging activation backend times out, logs metadata only, and returns empty text."""

    class HangingSTT:
        async def transcribe(self, audio: AudioFrame) -> str:
            del audio
            await asyncio.sleep(10)
            return "never"

    caplog.set_level(logging.WARNING)
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=HangingSTT(),
        config=processor_config(strategy=AmbientContextStrategy.ACTIVATION_BUFFER, stt_timeout_seconds=0.01),
    )

    context = await processor.context_for_activation(frame(4321))

    assert context.text == ""
    assert processor.metrics.activation_stt_timeouts == 1
    assert "4321" not in caplog.text
    await processor.close()


@pytest.mark.asyncio
async def test_vad_failure_is_local_and_does_not_start_stt() -> None:
    """A VAD backend error is contained and leaves the processor usable."""
    vad = SequenceVAD([RuntimeError("vad sentinel"), False])
    stt = MappingSTT([])
    processor = AmbientContextProcessor(vad=vad, stt=stt, config=processor_config())

    await processor.observe_frame(frame(1))
    await processor.observe_frame(frame(2))

    assert processor.metrics.vad_errors == 1
    assert processor.metrics.continuous_stt_requests == 0
    await processor.close()


@pytest.mark.asyncio
async def test_vad_timeout_is_bounded_and_does_not_block_following_frames() -> None:
    """A hanging VAD call is cancelled at its boundary and later frames still run."""

    class HangingVAD:
        async def is_speech(self, frame: AudioFrame) -> bool:
            del frame
            await asyncio.sleep(10)
            return True

    processor = AmbientContextProcessor(
        vad=HangingVAD(),
        stt=MappingSTT([]),
        config=processor_config(vad_timeout_seconds=0.01),
    )

    await processor.observe_frame(frame(1))

    assert processor.metrics.vad_timeouts == 1
    assert processor.metrics.frames_observed == 1
    await processor.close()


@pytest.mark.asyncio
async def test_failed_continuous_stt_does_not_block_later_ordered_results() -> None:
    """A failed segment advances the ordering barrier so later context survives."""
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False, True, False, False]),
        stt=FailingThenWorkingSTT(),
        config=processor_config(),
    )

    for value in range(6):
        await processor.observe_frame(frame(value))
    await processor.drain()

    assert processor.transcript.text() == "surviving segment"
    assert processor.metrics.continuous_stt_failures == 1
    assert processor.metrics.transcript_segments_accepted == 1
    await processor.close()


@pytest.mark.asyncio
async def test_cancellation_and_close_release_pending_local_work() -> None:
    """Cancellation propagates and close cancels scheduled STT without leaking tasks."""
    stt = BlockingSTT(["unused"])
    processor = AmbientContextProcessor(vad=SequenceVAD([True, False, False]), stt=stt, config=processor_config())

    await processor.observe_frame(frame(1))
    await processor.observe_frame(frame(2))
    await processor.observe_frame(frame(3))
    while not stt.started:
        await asyncio.sleep(0)
    await processor.close()

    await processor.close()


@pytest.mark.asyncio
async def test_empty_activation_snapshot_does_not_call_stt() -> None:
    """No raw samples means no local transcription request."""
    stt = MappingSTT([])
    processor = AmbientContextProcessor(
        vad=SequenceVAD([]),
        stt=stt,
        config=processor_config(strategy=AmbientContextStrategy.ACTIVATION_BUFFER),
    )

    context = await processor.context_for_activation((100, np.empty(0, dtype=np.int16)))

    assert context.text == ""
    assert processor.metrics.activation_stt_requests == 0
    assert stt.audio == []
    await processor.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I think Real Madrid might play this weekend", True),
        ("¿Qué hora es? Está lloviendo", True),
        ("Che ore sono? Não sei", True),
        ("你好，今天怎么样", False),
        ("안녕하세요 반갑습니다", False),
        ("...", True),
        ("ok 你好你好你好", False),
    ],
)
def test_is_mostly_latin_accepts_household_languages_and_rejects_cjk(text: str, expected: bool) -> None:
    """English, Spanish, Italian and Portuguese pass; Chinese and Korean text does not."""
    from reachy_mini_conversation_app.ambient_context import is_mostly_latin

    assert is_mostly_latin(text) is expected


@pytest.mark.asyncio
async def test_latin_only_drops_non_latin_stt_hallucinations() -> None:
    """With latin_script_only, a CJK transcript is discarded and later ordered text still commits."""
    processor = AmbientContextProcessor(
        vad=SequenceVAD([True, False, False, True, False, False]),
        stt=MappingSTT(["你好你好", "can you check?"]),
        config=processor_config(latin_script_only=True),
    )

    for value in range(6):
        await processor.observe_frame(frame(value))
    await processor.drain()

    assert processor.transcript.text() == "can you check?"
    await processor.close()
