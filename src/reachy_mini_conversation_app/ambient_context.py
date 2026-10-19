"""Local ambient VAD and STT boundaries for pre-activation context."""

import math
import time
import asyncio
import inspect
import logging
import unicodedata
from enum import Enum
from typing import Literal, Protocol, TypeAlias
from collections import deque
from dataclasses import fields, dataclass
from collections.abc import Callable, Awaitable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import audio_to_mono_int16
from reachy_mini_conversation_app.conversation_handler import AudioFrame


logger = logging.getLogger(__name__)


Clock: TypeAlias = Callable[[], float]
VADResult: TypeAlias = bool | Awaitable[bool]
STTResult: TypeAlias = str | Awaitable[str]
TranscriptObserver: TypeAlias = Callable[["TranscriptSegment"], None]
LocalBackendCall: TypeAlias = Callable[[AudioFrame], object]
LocalBackendKind: TypeAlias = Literal["stt", "vad"]
MAX_CONTINUOUS_STT_SECONDS = 20.0


class _LocalWorkBusy(RuntimeError):
    """Signal that all bounded local backend slots are occupied."""


class LocalVAD(Protocol):
    """Detect speech locally without contacting a provider."""

    def is_speech(self, frame: AudioFrame) -> VADResult:
        """Return whether the frame contains speech."""


class LocalSTT(Protocol):
    """Transcribe locally-owned audio without returning the audio itself."""

    def transcribe(self, audio: AudioFrame) -> STTResult:
        """Return derived text for one in-memory audio segment."""


def is_mostly_latin(text: str, minimum_share: float = 0.8) -> bool:
    """Return whether at least ``minimum_share`` of the letters in ``text`` are Latin script (True if none)."""
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return True
    latin = sum(1 for character in letters if "LATIN" in unicodedata.name(character, ""))
    return latin / len(letters) >= minimum_share


class AmbientContextStrategy(str, Enum):
    """Select how pre-activation context is produced."""

    CONTINUOUS_TRANSCRIPT = "continuous_transcript"
    ACTIVATION_BUFFER = "activation_buffer"


@dataclass(frozen=True, slots=True)
class AmbientContextConfig:
    """Bound local processing latency, transcript size, and memory."""

    strategy: AmbientContextStrategy = AmbientContextStrategy.CONTINUOUS_TRANSCRIPT
    transcript_window_seconds: float = 20.0
    max_transcript_chars: int = 2_000
    min_speech_seconds: float = 0.08
    min_silence_seconds: float = 0.35
    vad_timeout_seconds: float = 1.0
    stt_timeout_seconds: float = 5.0
    max_pending_stt_tasks: int = 2
    # Drop transcripts mostly in non-Latin script: multilingual Whisper invents Chinese or Korean text for
    # short or noisy segments, which a Latin-script household never speaks.
    latin_script_only: bool = False

    def __post_init__(self) -> None:
        """Reject invalid processing bounds before audio is accepted."""
        if self.transcript_window_seconds <= 0 or not math.isfinite(self.transcript_window_seconds):
            raise ValueError("transcript_window_seconds must be finite and positive")
        if self.max_transcript_chars <= 0:
            raise ValueError("max_transcript_chars must be positive")
        if self.min_speech_seconds < 0 or not math.isfinite(self.min_speech_seconds):
            raise ValueError("min_speech_seconds must be finite and non-negative")
        if self.min_silence_seconds < 0 or not math.isfinite(self.min_silence_seconds):
            raise ValueError("min_silence_seconds must be finite and non-negative")
        if self.vad_timeout_seconds <= 0 or not math.isfinite(self.vad_timeout_seconds):
            raise ValueError("vad_timeout_seconds must be finite and positive")
        if self.stt_timeout_seconds <= 0 or not math.isfinite(self.stt_timeout_seconds):
            raise ValueError("stt_timeout_seconds must be finite and positive")
        if not isinstance(self.max_pending_stt_tasks, int) or isinstance(self.max_pending_stt_tasks, bool):
            raise ValueError("max_pending_stt_tasks must be a positive integer")
        if self.max_pending_stt_tasks <= 0:
            raise ValueError("max_pending_stt_tasks must be a positive integer")


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """Represent one locally-derived transcript segment and its ordering metadata."""

    text: str
    started_at: float
    ended_at: float
    sequence: int
    final: bool = True


@dataclass(frozen=True, slots=True)
class AmbientContext:
    """Text-only context safe for an integration to inject into a cloud session."""

    text: str
    strategy: AmbientContextStrategy
    generated_at: float
    source_ended_at: float | None = None
    source_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class AmbientContextMetrics:
    """Metadata-only counters and timings for local context processing."""

    frames_observed: int = 0
    speech_frames: int = 0
    vad_dropped: int = 0
    audio_seconds_observed: float = 0.0
    vad_errors: int = 0
    vad_timeouts: int = 0
    continuous_stt_requests: int = 0
    continuous_stt_completed: int = 0
    continuous_stt_failures: int = 0
    continuous_stt_timeouts: int = 0
    continuous_stt_dropped: int = 0
    activation_stt_requests: int = 0
    activation_stt_completed: int = 0
    activation_stt_failures: int = 0
    activation_stt_timeouts: int = 0
    transcript_segments_accepted: int = 0
    stale_segments_rejected: int = 0
    stale_activation_contexts_rejected: int = 0
    continuous_stt_latency_seconds_total: float = 0.0
    continuous_stt_latency_seconds_max: float = 0.0
    activation_stt_latency_seconds_total: float = 0.0
    activation_stt_latency_seconds_max: float = 0.0
    stt_inflight_peak: int = 0
    local_work_pending: int = 0
    continuous_stt_pending_at_activation: int = 0


@dataclass(frozen=True, slots=True)
class _TranscriptEntry:
    """Store normalized final text with its bounded time span."""

    text: str
    started_at: float
    ended_at: float
    sequence: int


class RollingTranscript:
    """Keep ordered, bounded final transcript segments in memory."""

    def __init__(self, *, max_seconds: float, max_chars: int) -> None:
        """Initialize a transcript with time and text limits."""
        if max_seconds <= 0 or not math.isfinite(max_seconds):
            raise ValueError("max_seconds must be finite and positive")
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_seconds = max_seconds
        self.max_chars = max_chars
        self._entries: deque[_TranscriptEntry] = deque()
        self._highest_sequence = 0
        self._latest_final_sequence = 0
        self._latest_final_ended_at = float("-inf")
        self._pending_partial: TranscriptSegment | None = None

    @property
    def segments(self) -> tuple[TranscriptSegment, ...]:
        """Return an immutable copy of accepted final transcript segments."""
        return tuple(
            TranscriptSegment(
                text=entry.text,
                started_at=entry.started_at,
                ended_at=entry.ended_at,
                sequence=entry.sequence,
            )
            for entry in self._entries
        )

    def add(self, segment: TranscriptSegment) -> bool:
        """Accept one ordered segment, rejecting stale or duplicate results."""
        normalized_text = _normalize_text(segment.text)
        if not normalized_text:
            return False
        if segment.sequence <= 0:
            return False
        if (
            not math.isfinite(segment.started_at)
            or not math.isfinite(segment.ended_at)
            or segment.ended_at < segment.started_at
        ):
            return False
        if segment.sequence < self._highest_sequence:
            return False
        if not segment.final and segment.sequence <= self._latest_final_sequence:
            return False
        if segment.sequence == self._highest_sequence and segment.final and self._pending_partial is None:
            return False
        if segment.ended_at < self._latest_final_ended_at:
            return False

        if not segment.final:
            self._highest_sequence = max(self._highest_sequence, segment.sequence)
            self._pending_partial = TranscriptSegment(
                text=normalized_text,
                started_at=segment.started_at,
                ended_at=segment.ended_at,
                sequence=segment.sequence,
                final=False,
            )
            return True

        if self._pending_partial is not None and segment.sequence == self._pending_partial.sequence:
            self._pending_partial = None
        else:
            self._pending_partial = None
        self._highest_sequence = max(self._highest_sequence, segment.sequence)
        self._latest_final_sequence = max(self._latest_final_sequence, segment.sequence)
        self._latest_final_ended_at = segment.ended_at
        self._entries.append(
            _TranscriptEntry(
                text=normalized_text,
                started_at=segment.started_at,
                ended_at=segment.ended_at,
                sequence=segment.sequence,
            )
        )
        self._trim()
        return True

    def text(
        self,
        *,
        now: float | None = None,
        before_sequence: int | None = None,
    ) -> str:
        """Return bounded chronological final text before an optional sequence fence."""
        if now is not None and not math.isfinite(now):
            return ""
        if now is not None:
            cutoff = now - self.max_seconds
            while self._entries and self._entries[0].ended_at < cutoff:
                self._entries.popleft()
        if before_sequence is not None and before_sequence <= 0:
            return ""
        return _bound_text(
            "\n".join(
                entry.text for entry in self._entries if before_sequence is None or entry.sequence < before_sequence
            ),
            self.max_chars,
        )

    def clear(self) -> None:
        """Discard all retained transcript text and ordering state."""
        self._entries.clear()
        self._highest_sequence = 0
        self._latest_final_sequence = 0
        self._latest_final_ended_at = float("-inf")
        self._pending_partial = None

    def _trim(self) -> None:
        """Trim by the newest segment's time and then by text size."""
        if not self._entries:
            return
        cutoff = self._entries[-1].ended_at - self.max_seconds
        while self._entries and self._entries[0].ended_at < cutoff:
            self._entries.popleft()
        while self._text_length() > self.max_chars and self._entries:
            oldest = self._entries[0]
            excess = self._text_length() - self.max_chars
            if excess >= len(oldest.text) + (1 if len(self._entries) > 1 else 0):
                self._entries.popleft()
                continue
            self._entries[0] = _TranscriptEntry(
                text=oldest.text[excess + (1 if len(self._entries) > 1 else 0) :].lstrip(),
                started_at=oldest.started_at,
                ended_at=oldest.ended_at,
                sequence=oldest.sequence,
            )
            break

    def _text_length(self) -> int:
        """Return the in-memory text size including separators."""
        return sum(len(entry.text) for entry in self._entries) + max(0, len(self._entries) - 1)


@dataclass(slots=True)
class _SpeechAccumulator:
    """Hold one bounded in-memory speech segment until local STT runs."""

    frames: list[AudioFrame]
    sample_count: int
    sample_rate: int
    started_at: float
    ended_at: float
    speech_seconds: float
    silence_seconds: float
    sequence: int


class AmbientContextProcessor:
    """Build local ambient context with continuous or activation-time STT."""

    def __init__(
        self,
        *,
        vad: LocalVAD,
        stt: LocalSTT,
        config: AmbientContextConfig | None = None,
        clock: Clock = time.monotonic,
        transcript_observer: TranscriptObserver | None = None,
    ) -> None:
        """Initialize a dependency-free local VAD/STT coordinator."""
        self.config = config or AmbientContextConfig()
        self._vad = vad
        self._stt = stt
        self._clock = clock
        self._transcript_observer = transcript_observer
        self._transcript = RollingTranscript(
            max_seconds=self.config.transcript_window_seconds,
            max_chars=self.config.max_transcript_chars,
        )
        self._speech: _SpeechAccumulator | None = None
        self._next_sequence = 1
        self._next_commit_sequence = 1
        self._pending_results: dict[int, TranscriptSegment | None] = {}
        self._transcription_tasks: set[asyncio.Task[None]] = set()
        self._activation_generation = 0
        self._latest_activation_generation = 0
        self._metrics = AmbientContextMetrics()
        self._stt_inflight = 0
        # Set while Reachy is taking a request: segments go to the STT's accurate model, with one extra slot.
        self._request_mode = False
        # The last few segments' audio (in memory only), so a request can be transcribed again accurately.
        self._recent_segment_audio: deque[tuple[int, AudioFrame]] = deque(maxlen=3)
        self._vad_inflight = 0
        self._local_backend_tasks: set[asyncio.Task[object]] = set()
        self._local_executor = ThreadPoolExecutor(
            max_workers=self.config.max_pending_stt_tasks + 2,
            thread_name_prefix="reachy-local",
        )
        self._local_executor_shutdown = False
        self._closed = False
        self._audio_cursor_end: float | None = None

    @property
    def transcript(self) -> RollingTranscript:
        """Return the local rolling transcript store."""
        return self._transcript

    def get_transcript_preview(self, *, now: float | None = None) -> str | None:
        """Return bounded continuous transcript text while this processor is open."""
        if self._closed or self.config.strategy is not AmbientContextStrategy.CONTINUOUS_TRANSCRIPT:
            return None
        return self._transcript.text(now=now)

    @property
    def metrics(self) -> AmbientContextMetrics:
        """Return immutable local processing metrics."""
        return self._metrics

    def set_transcript_observer(self, observer: TranscriptObserver | None) -> None:
        """Attach or clear the observer for finalized local transcript segments."""
        self._transcript_observer = observer

    async def observe_frame(self, frame: AudioFrame) -> None:
        """Run local VAD over one copied frame and schedule continuous STT."""
        if self._closed:
            raise RuntimeError("Ambient context processor is closed")
        sample_rate, normalized_audio = self._normalize_frame(frame)
        frame_duration = normalized_audio.size / sample_rate
        self._increment_metrics(
            frames_observed=self._metrics.frames_observed + 1,
            audio_seconds_observed=self._metrics.audio_seconds_observed + frame_duration,
        )
        try:
            is_speech = await asyncio.wait_for(
                self._detect_speech((sample_rate, normalized_audio)),
                timeout=self.config.vad_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._increment_metrics(vad_timeouts=self._metrics.vad_timeouts + 1)
            logger.warning("Local VAD timed out")
            return
        except _LocalWorkBusy:
            self._increment_metrics(vad_dropped=self._metrics.vad_dropped + 1)
            logger.warning("Local VAD backlog full; dropped frame")
            return
        except Exception as error:
            self._increment_metrics(vad_errors=self._metrics.vad_errors + 1)
            logger.warning("Local VAD failed: %s", type(error).__name__)
            return

        if is_speech:
            self._increment_metrics(speech_frames=self._metrics.speech_frames + 1)
            self._append_speech_frame(sample_rate, normalized_audio, frame_duration)
            return

        speech = self._speech
        if speech is None:
            return
        speech.silence_seconds += frame_duration
        speech.ended_at = self._frame_end_time(frame_duration)
        if speech.silence_seconds >= self.config.min_silence_seconds:
            self._speech = None
            if speech.speech_seconds >= self.config.min_speech_seconds:
                self._schedule_continuous_transcription(speech)
            else:
                self._discard_continuous_sequence(speech.sequence)

    @property
    def has_open_segment(self) -> bool:
        """Return whether speech is currently being accumulated into an unfinished segment."""
        return self._speech is not None

    @property
    def latest_sequence(self) -> int:
        """Return the highest speech-segment sequence assigned so far, including an open segment."""
        return self._next_sequence - 1

    def segment_audio(self, sequence: int) -> AudioFrame | None:
        """Return the audio of a recent segment by sequence, or None once it has been dropped."""
        for kept_sequence, audio in self._recent_segment_audio:
            if kept_sequence == sequence:
                return audio
        return None

    def set_request_mode(self, active: bool) -> None:
        """Transcribe what follows with the accurate model while Reachy takes a request."""
        self._request_mode = active

    def _stt_limit(self) -> int:
        return self.config.max_pending_stt_tasks + (1 if self._request_mode else 0)

    def close_open_segment(self) -> None:
        """Finalize the open speech segment now (if any) and schedule its transcription without waiting."""
        if self._closed:
            return
        speech = self._speech
        self._speech = None
        if speech is not None and speech.speech_seconds >= self.config.min_speech_seconds:
            self._schedule_continuous_transcription(speech)
        elif speech is not None:
            self._discard_continuous_sequence(speech.sequence)

    async def flush(self) -> None:
        """Finalize a pending speech segment and wait for local STT tasks."""
        if self._closed:
            return
        self.close_open_segment()
        await self.drain()

    async def drain(self) -> None:
        """Wait for currently scheduled local STT work to finish."""
        while self._transcription_tasks:
            await asyncio.gather(*tuple(self._transcription_tasks))

    async def context_for_activation(
        self,
        snapshot: AudioFrame | None = None,
        *,
        strategy: AmbientContextStrategy | None = None,
        before_sequence: int | None = None,
    ) -> AmbientContext:
        """Return bounded derived text before an optional wake sequence fence."""
        if self._closed:
            raise RuntimeError("Ambient context processor is closed")
        selected_strategy = strategy or self.config.strategy
        generated_at = self._clock()
        if selected_strategy is AmbientContextStrategy.CONTINUOUS_TRANSCRIPT:
            if self._transcription_tasks:
                self._increment_metrics(
                    continuous_stt_pending_at_activation=self._metrics.continuous_stt_pending_at_activation
                    + len(self._transcription_tasks)
                )
            text = self._transcript.text(now=generated_at, before_sequence=before_sequence)
            segments = (
                segment
                for segment in self._transcript.segments
                if before_sequence is None or segment.sequence < before_sequence
            )
            retained_segments = tuple(segments)
            return AmbientContext(
                text=text,
                strategy=selected_strategy,
                generated_at=generated_at,
                source_ended_at=retained_segments[-1].ended_at if retained_segments else None,
                source_sequence=retained_segments[-1].sequence if retained_segments else None,
            )
        if snapshot is None:
            return AmbientContext(text="", strategy=selected_strategy, generated_at=generated_at)

        normalized_snapshot = self._normalize_frame(snapshot)
        if normalized_snapshot[1].size == 0:
            return AmbientContext(text="", strategy=selected_strategy, generated_at=generated_at)
        self._activation_generation += 1
        generation = self._activation_generation
        self._latest_activation_generation = generation
        self._increment_metrics(activation_stt_requests=self._metrics.activation_stt_requests + 1)
        started_at = self._clock()
        try:
            text = await self._transcribe(normalized_snapshot)
            self._increment_metrics(activation_stt_completed=self._metrics.activation_stt_completed + 1)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._increment_metrics(activation_stt_timeouts=self._metrics.activation_stt_timeouts + 1)
            logger.warning("Activation-time local STT timed out")
            return AmbientContext(text="", strategy=selected_strategy, generated_at=self._clock())
        except Exception as error:
            self._increment_metrics(activation_stt_failures=self._metrics.activation_stt_failures + 1)
            logger.warning("Activation-time local STT failed: %s", type(error).__name__)
            return AmbientContext(text="", strategy=selected_strategy, generated_at=self._clock())
        finally:
            self._record_activation_latency(max(0.0, self._clock() - started_at))

        if generation != self._latest_activation_generation:
            self._increment_metrics(
                stale_activation_contexts_rejected=self._metrics.stale_activation_contexts_rejected + 1
            )
            return AmbientContext(text="", strategy=selected_strategy, generated_at=self._clock())
        return AmbientContext(
            text=_bound_text(text, self.config.max_transcript_chars),
            strategy=selected_strategy,
            generated_at=self._clock(),
        )

    async def close(self) -> None:
        """Stop accepting work and release frames when backend calls exit."""
        if self._closed:
            return
        self._closed = True
        self._transcript.clear()
        self._speech = None
        transcription_tasks = tuple(self._transcription_tasks)
        for task in transcription_tasks:
            task.cancel()
        if transcription_tasks:
            await asyncio.gather(*transcription_tasks, return_exceptions=True)
        backend_tasks = tuple(self._local_backend_tasks)
        for backend_task in backend_tasks:
            backend_task.cancel()
        if not backend_tasks:
            self._shutdown_local_executor()
        else:
            logger.warning("Local processing closed with %d backend call(s) still running", len(backend_tasks))
        self._pending_results.clear()

    def _append_speech_frame(self, sample_rate: int, audio: NDArray[np.int16], duration: float) -> None:
        """Append a defensive mono frame to the bounded current segment."""
        frame_start = self._audio_cursor_end if self._audio_cursor_end is not None else self._clock()
        frame_end = self._frame_end_time(duration)
        max_samples = max(1, round(MAX_CONTINUOUS_STT_SECONDS * sample_rate))
        if audio.size > max_samples:
            audio = audio[-max_samples:]
            frame_start = frame_end - audio.size / sample_rate
        retained_duration = audio.size / sample_rate
        speech = self._speech
        if speech is None or speech.sample_rate != sample_rate:
            if speech is not None:
                logger.warning("Local speech segment discarded after sample-rate change")
                self._discard_continuous_sequence(speech.sequence)
            speech = _SpeechAccumulator(
                frames=[],
                sample_count=0,
                sample_rate=sample_rate,
                started_at=frame_start,
                ended_at=frame_end,
                speech_seconds=0.0,
                silence_seconds=0.0,
                sequence=self._next_sequence,
            )
            self._next_sequence += 1
            self._speech = speech
        speech.frames.append((sample_rate, audio.copy()))
        speech.sample_count += audio.size
        speech.speech_seconds += retained_duration
        speech.silence_seconds = 0.0
        speech.ended_at = frame_end
        while speech.sample_count > max_samples and speech.frames:
            excess_samples = speech.sample_count - max_samples
            oldest_rate, oldest_audio = speech.frames[0]
            if oldest_audio.size <= excess_samples:
                speech.frames.pop(0)
                speech.sample_count -= oldest_audio.size
                speech.speech_seconds -= oldest_audio.size / oldest_rate
                speech.started_at += oldest_audio.size / oldest_rate
                continue
            speech.frames[0] = (oldest_rate, oldest_audio[excess_samples:].copy())
            speech.sample_count -= excess_samples
            speech.speech_seconds -= excess_samples / oldest_rate
            speech.started_at += excess_samples / oldest_rate

    def _schedule_continuous_transcription(self, speech: _SpeechAccumulator) -> None:
        """Schedule one ordered local STT operation without exposing PCM."""
        if self.config.strategy is not AmbientContextStrategy.CONTINUOUS_TRANSCRIPT:
            return
        for completed_task in tuple(self._transcription_tasks):
            if completed_task.done():
                self._transcription_tasks.discard(completed_task)
        limit = self._stt_limit()
        if len(self._transcription_tasks) >= limit:
            self._increment_metrics(continuous_stt_dropped=self._metrics.continuous_stt_dropped + 1)
            self._discard_continuous_sequence(speech.sequence)
            logger.warning("Continuous local STT backlog full; dropped newest segment")
            return
        if self._stt_inflight >= limit:
            self._increment_metrics(continuous_stt_dropped=self._metrics.continuous_stt_dropped + 1)
            self._discard_continuous_sequence(speech.sequence)
            logger.warning("Continuous local STT backend busy; dropped newest segment")
            return
        audio_parts = tuple(frame[1] for frame in speech.frames if frame[1].size)
        if not audio_parts:
            self._discard_continuous_sequence(speech.sequence)
            return
        audio = np.concatenate(audio_parts).astype(np.int16, copy=True)
        segment = (speech.sample_rate, audio)
        self._recent_segment_audio.append((speech.sequence, segment))
        transcribe = self._stt.transcribe
        request_transcribe = getattr(self._stt, "transcribe_request", None)
        if self._request_mode and request_transcribe is not None:
            transcribe = request_transcribe
        backend_task = self._start_local_work(transcribe, segment, "stt")
        if backend_task is None:
            self._increment_metrics(continuous_stt_dropped=self._metrics.continuous_stt_dropped + 1)
            self._discard_continuous_sequence(speech.sequence)
            logger.warning("Continuous local STT backend busy; dropped newest segment")
            return
        self._increment_metrics(continuous_stt_requests=self._metrics.continuous_stt_requests + 1)
        task = asyncio.create_task(self._run_continuous_transcription(speech, backend_task))
        self._transcription_tasks.add(task)
        task.add_done_callback(self._transcription_tasks.discard)

    def _discard_continuous_sequence(self, sequence: int) -> None:
        """Advance ordered result commits when a segment needs no STT call."""
        if self.config.strategy is AmbientContextStrategy.CONTINUOUS_TRANSCRIPT:
            self._complete_continuous_result(sequence, None)

    async def _run_continuous_transcription(
        self,
        speech: _SpeechAccumulator,
        backend_task: asyncio.Task[object],
    ) -> None:
        """Run one bounded continuous transcription and commit in sequence order."""
        started_at = self._clock()
        result: TranscriptSegment | None = None
        try:
            text = _normalize_text(await self._await_backend_text(backend_task))
            self._increment_metrics(continuous_stt_completed=self._metrics.continuous_stt_completed + 1)
            if self.config.latin_script_only and not is_mostly_latin(text):
                logger.info("Local STT result dropped: not in Latin script (likely a hallucination)")
            else:
                result = TranscriptSegment(
                    text=text,
                    started_at=speech.started_at,
                    ended_at=speech.ended_at,
                    sequence=speech.sequence,
                )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._increment_metrics(continuous_stt_timeouts=self._metrics.continuous_stt_timeouts + 1)
            logger.warning("Continuous local STT timed out")
        except Exception as error:
            self._increment_metrics(continuous_stt_failures=self._metrics.continuous_stt_failures + 1)
            logger.warning("Continuous local STT failed: %s", type(error).__name__)
        finally:
            self._record_continuous_latency(max(0.0, self._clock() - started_at))
            self._complete_continuous_result(speech.sequence, result)

    async def _transcribe(self, audio: AudioFrame) -> str:
        """Invoke the replaceable STT backend under its configured timeout."""
        backend_task = self._start_local_work(self._stt.transcribe, audio, "stt")
        if backend_task is None:
            raise _LocalWorkBusy("Local STT backend busy")
        return await self._await_backend_text(backend_task)

    async def _detect_speech(self, frame: AudioFrame) -> bool:
        """Invoke the replaceable VAD backend and validate its boolean result."""
        backend_task = self._start_local_work(self._vad.is_speech, frame, "vad")
        if backend_task is None:
            raise _LocalWorkBusy("Local VAD backend busy")
        resolved = await asyncio.wait_for(asyncio.shield(backend_task), timeout=self.config.vad_timeout_seconds)
        if not isinstance(resolved, bool):
            raise TypeError("Local VAD must return a boolean")
        return resolved

    async def _await_backend_text(self, backend_task: asyncio.Task[object]) -> str:
        """Await one admitted STT call without cancelling its underlying work."""
        result = await asyncio.wait_for(
            asyncio.shield(backend_task),
            timeout=self.config.stt_timeout_seconds,
        )
        if not isinstance(result, str):
            raise TypeError("Local STT must return text")
        return result

    def _start_local_work(
        self,
        operation: LocalBackendCall,
        frame: AudioFrame,
        kind: LocalBackendKind,
    ) -> asyncio.Task[object] | None:
        """Admit one bounded backend call and retain its frame until it exits."""
        if kind == "stt":
            if self._stt_inflight >= self._stt_limit():
                return None
            self._stt_inflight += 1
            self._increment_metrics(stt_inflight_peak=max(self._metrics.stt_inflight_peak, self._stt_inflight))
        elif self._vad_inflight >= 1:
            return None
        else:
            self._vad_inflight += 1

        copied_frame = (frame[0], frame[1].copy())
        try:
            task = asyncio.create_task(self._execute_local_work(operation, copied_frame))
        except RuntimeError:
            self._release_local_work(kind)
            raise
        self._local_backend_tasks.add(task)
        self._increment_metrics(local_work_pending=len(self._local_backend_tasks))
        task.add_done_callback(lambda completed: self._finish_local_work(completed, kind))
        return task

    async def _execute_local_work(self, operation: LocalBackendCall, frame: AudioFrame) -> object:
        """Run a sync backend in the owned executor and await async results locally."""
        invocation = asyncio.wrap_future(self._local_executor.submit(operation, frame))
        try:
            result = await asyncio.shield(invocation)
        except asyncio.CancelledError:
            while not invocation.done():
                try:
                    await asyncio.shield(invocation)
                except asyncio.CancelledError:
                    continue
            if not invocation.cancelled() and invocation.exception() is None:
                completed_result = invocation.result()
                if inspect.iscoroutine(completed_result):
                    completed_result.close()
            raise
        if inspect.isawaitable(result):
            return await result
        return result

    def _finish_local_work(self, task: asyncio.Task[object], kind: LocalBackendKind) -> None:
        """Release admission only after the backend and its frame have exited."""
        self._local_backend_tasks.discard(task)
        self._release_local_work(kind)
        self._increment_metrics(local_work_pending=len(self._local_backend_tasks))
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.warning("Local %s backend failed: %s", kind, type(error).__name__)
        if self._closed and not self._local_backend_tasks:
            self._shutdown_local_executor()

    def _release_local_work(self, kind: LocalBackendKind) -> None:
        """Release one local backend admission slot."""
        if kind == "stt":
            self._stt_inflight = max(0, self._stt_inflight - 1)
        else:
            self._vad_inflight = max(0, self._vad_inflight - 1)

    def _shutdown_local_executor(self) -> None:
        """Stop accepting local work while allowing running calls to finish."""
        if self._local_executor_shutdown:
            return
        self._local_executor_shutdown = True
        self._local_executor.shutdown(wait=False, cancel_futures=True)

    def _complete_continuous_result(self, sequence: int, result: TranscriptSegment | None) -> None:
        """Commit ready results chronologically and reject stale completions."""
        self._pending_results[sequence] = result
        while self._next_commit_sequence in self._pending_results:
            current = self._pending_results.pop(self._next_commit_sequence)
            self._next_commit_sequence += 1
            if current is None:
                continue
            accepted = self._transcript.add(current)
            if accepted:
                self._increment_metrics(transcript_segments_accepted=self._metrics.transcript_segments_accepted + 1)
                observer = self._transcript_observer
                if observer is not None:
                    try:
                        observer(current)
                    except Exception as error:
                        logger.warning("Ambient transcript observer failed: %s", type(error).__name__)
            else:
                self._increment_metrics(stale_segments_rejected=self._metrics.stale_segments_rejected + 1)

    def _normalize_frame(self, frame: AudioFrame) -> AudioFrame:
        """Return a defensive mono-int16 copy for local processing only."""
        sample_rate, audio = frame
        if sample_rate <= 0:
            raise ValueError("Local audio sample rate must be positive")
        return sample_rate, audio_to_mono_int16(audio)

    def _frame_end_time(self, duration: float) -> float:
        """Advance a monotonic audio timeline even when test clocks do not move."""
        candidate = self._clock() + duration
        if self._audio_cursor_end is None:
            self._audio_cursor_end = candidate
        else:
            self._audio_cursor_end = max(candidate, self._audio_cursor_end + duration)
        return self._audio_cursor_end

    def _record_continuous_latency(self, duration: float) -> None:
        """Record aggregate continuous-STT timing metadata."""
        self._increment_metrics(
            continuous_stt_latency_seconds_total=self._metrics.continuous_stt_latency_seconds_total + duration,
            continuous_stt_latency_seconds_max=max(self._metrics.continuous_stt_latency_seconds_max, duration),
        )

    def _record_activation_latency(self, duration: float) -> None:
        """Record aggregate activation-STT timing metadata."""
        self._increment_metrics(
            activation_stt_latency_seconds_total=self._metrics.activation_stt_latency_seconds_total + duration,
            activation_stt_latency_seconds_max=max(self._metrics.activation_stt_latency_seconds_max, duration),
        )

    def _increment_metrics(self, **changes: int | float) -> None:
        """Replace the immutable metrics snapshot with selected changed fields."""
        values = {field.name: getattr(self._metrics, field.name) for field in fields(self._metrics)}
        values.update(changes)
        self._metrics = AmbientContextMetrics(**values)


def _normalize_text(text: str) -> str:
    """Collapse whitespace without logging or retaining raw audio."""
    return " ".join(text.split())


def _bound_text(text: str, max_chars: int) -> str:
    """Keep the newest derived text within the configured character bound."""
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[-max_chars:].lstrip()


__all__ = [
    "AmbientContext",
    "AmbientContextConfig",
    "AmbientContextMetrics",
    "AmbientContextProcessor",
    "AmbientContextStrategy",
    "LocalSTT",
    "LocalVAD",
    "MAX_CONTINUOUS_STT_SECONDS",
    "RollingTranscript",
    "TranscriptSegment",
]
