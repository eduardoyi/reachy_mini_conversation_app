"""Provider lifecycle and privacy boundary for the local/cloud runtime."""

import re
import math
import time
import asyncio
import inspect
import logging
import weakref
import threading
from enum import Enum
from typing import TypeAlias
from contextlib import aclosing
from collections import deque
from dataclasses import dataclass
from collections.abc import Callable, Awaitable, AsyncIterator

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.config import (
    MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
    DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
    config,
)
from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
from reachy_mini_conversation_app.streaming import AdditionalOutputs, audio_to_mono_int16
from reachy_mini_conversation_app.wake_word import AcousticWakeWordDetector, TranscriptWakeWordDetector
from reachy_mini_conversation_app.wake_clips import WakeClipRecorder
from reachy_mini_conversation_app.local_speech import SAMPLE_RATE as SPEECH_SAMPLE_RATE
from reachy_mini_conversation_app.local_speech import beep
from reachy_mini_conversation_app.local_actions import LocalActionStatus, LocalActionExecutor
from reachy_mini_conversation_app.ambient_context import (
    TranscriptSegment,
    AmbientContextStrategy,
    AmbientContextProcessor,
)
from reachy_mini_conversation_app.local_processing import (
    LocalProcessingBackend,
    NullLocalProcessingBackend,
)
from reachy_mini_conversation_app.local_intent_router import LocalIntent, LocalIntentRouter, IntentDecisionReason
from reachy_mini_conversation_app.conversation_handler import (
    AudioFrame,
    TrafficKind,
    HandlerOutput,
    ConversationHandler,
)
from reachy_mini_conversation_app.local_audio_backends import RmsEnergyVAD


logger = logging.getLogger(__name__)


class RuntimeMode(str, Enum):
    """Operating modes at the cloud privacy boundary."""

    LOCAL_AMBIENT = "LOCAL_AMBIENT"
    LOCAL_COMMAND = "LOCAL_COMMAND"
    CLOUD_CONVERSATION = "CLOUD_CONVERSATION"
    STOPPING = "STOPPING"


class LocalCommandOutcome(str, Enum):
    """Metadata-only outcome for the most recent local command attempt."""

    LOCAL_EXECUTED = "local_executed"
    ROUTER_REJECTED = "router_rejected"
    FOLLOWUP_TIMEOUT = "followup_timeout"
    LOCAL_EXECUTION_FAILED = "local_execution_failed"


CallbackResult: TypeAlias = None | Awaitable[None]
HandlerFactory: TypeAlias = Callable[[], ConversationHandler | Awaitable[ConversationHandler]]
AcknowledgementCallback: TypeAlias = Callable[[str], CallbackResult]
ActivityObserver: TypeAlias = Callable[[str], None]
TranscriptObserver: TypeAlias = Callable[[str, str, bool], None]
StateObserver: TypeAlias = Callable[[RuntimeMode, str, int], None]
InteractionBusyObserver: TypeAlias = Callable[[bool], None]
CaptureBarrierProvider: TypeAlias = Callable[[], int | None]
Clock: TypeAlias = Callable[[], float]
PostActivationFrame: TypeAlias = tuple[int | None, AudioFrame]
LocalProcessingItem: TypeAlias = tuple[AudioFrame, asyncio.Future[None], int]


def _merge_local_items(
    items: list[LocalProcessingItem],
) -> list[tuple[AudioFrame, int, list[asyncio.Future[None]]]]:
    """Join consecutive queued frames with the same sample rate and wake epoch into mono frames.

    A lone frame passes through unchanged, so merging only happens while the worker is catching up.
    """
    groups: list[tuple[int, int, list[AudioFrame], list[asyncio.Future[None]]]] = []
    for frame, completed, epoch in items:
        if groups and groups[-1][0] == frame[0] and groups[-1][1] == epoch:
            groups[-1][2].append(frame)
            groups[-1][3].append(completed)
        else:
            groups.append((frame[0], epoch, [frame], [completed]))
    merged: list[tuple[AudioFrame, int, list[asyncio.Future[None]]]] = []
    for sample_rate, epoch, frames, completions in groups:
        if len(frames) == 1:
            merged.append((frames[0], epoch, completions))
            continue
        audio = np.concatenate([audio_to_mono_int16(frame[1]) for frame in frames])
        merged.append(((sample_rate, audio), epoch, completions))
    return merged


LocalProcessingCloseCallback: TypeAlias = Callable[[], CallbackResult]
# (what Reachy last said, what people said after it) -> False when clearly not for him, None when unsure.
AddresseeChecker: TypeAlias = Callable[[str, str], Awaitable[bool | None]]


def _append_capped(parts: list[str], text: str, max_chars: int) -> list[str]:
    """Append ``text`` and drop the oldest parts beyond ``max_chars`` in total (the newest always stays)."""
    parts = [*parts, text]
    while len(parts) > 1 and sum(len(part) + 1 for part in parts) > max_chars:
        parts.pop(0)
    return parts


@dataclass(frozen=True)
class TrafficMetricsSnapshot:
    """Immutable privacy and provider traffic counters."""

    ambient_audio_seconds_buffered: float
    ambient_audio_bytes_sent_to_cloud: int
    cloud_audio_bytes_sent: int
    cloud_video_frames_sent: int
    gemini_session_duration: float


@dataclass(frozen=True)
class RuntimeStatusSnapshot:
    """Immutable state snapshot suitable for status/UI consumers."""

    mode: RuntimeMode
    generation: int
    active: bool
    closed: bool
    provider_task_running: bool
    inactivity_monitor_running: bool
    connected: bool
    interaction_busy: bool = False
    playback_active: bool = False
    shutdown_error: str | None = None
    ambient_audio_seconds_buffered: float = 0.0
    ambient_audio_bytes_sent_to_cloud: int = 0
    cloud_audio_bytes_sent: int = 0
    cloud_video_frames_sent: int = 0
    gemini_session_duration: float = 0.0
    postactivation_frames_dropped: int = 0
    local_context_chars: int = 0
    transcript_segments_accepted: int = 0
    acoustic_wake_enabled: bool = False
    wake_detections: int = 0
    wake_cooldown_suppressed: int = 0
    wake_final_update_count: int = 0
    local_commands_executed: int = 0
    local_command_failures: int = 0
    local_command_escalations: int = 0
    local_command_last_outcome: LocalCommandOutcome | None = None
    local_command_last_router_reason: IntentDecisionReason | None = None
    local_command_router_rejections: int = 0
    local_command_followup_timeouts: int = 0
    local_processing_frames_dropped: int = 0
    local_command_latency_seconds_total: float = 0.0
    local_command_latency_seconds_max: float = 0.0
    context_activation_latency_seconds_total: float = 0.0
    context_activation_latency_seconds_max: float = 0.0
    local_context_strategy: str | None = None
    local_audio_seconds_observed: float = 0.0
    local_speech_frames: int = 0
    local_stt_requests: int = 0
    local_stt_completed: int = 0
    local_stt_failures: int = 0
    local_stt_timeouts: int = 0
    local_stt_dropped: int = 0
    local_stt_latency_seconds_total: float = 0.0
    local_stt_latency_seconds_max: float = 0.0
    local_stt_inflight_peak: int = 0
    local_stt_pending_at_activation: int = 0

    @property
    def cloud_active(self) -> bool:
        """Return whether a cloud conversation is currently selected."""
        return self.mode is RuntimeMode.CLOUD_CONVERSATION

    @property
    def has_active_handler(self) -> bool:
        """Return whether a handler is selected for the current generation."""
        return self.active

    def __call__(self) -> dict[str, object]:
        """Return a metadata-only dictionary for status RPC consumers."""
        return {
            "mode": self.mode.value,
            "state": self.mode.value,
            "generation": self.generation,
            "active": self.active,
            "connected": self.connected,
            "closed": self.closed,
            "provider_task_running": self.provider_task_running,
            "inactivity_monitor_running": self.inactivity_monitor_running,
            "interaction_busy": self.interaction_busy,
            "playback_active": self.playback_active,
            "shutdown_error": self.shutdown_error,
            "ambient_audio_seconds_buffered": self.ambient_audio_seconds_buffered,
            "ambient_audio_bytes_sent_to_cloud": self.ambient_audio_bytes_sent_to_cloud,
            "cloud_audio_bytes_sent": self.cloud_audio_bytes_sent,
            "cloud_video_frames_sent": self.cloud_video_frames_sent,
            "gemini_session_duration": self.gemini_session_duration,
            "postactivation_frames_dropped": self.postactivation_frames_dropped,
            "local_context_chars": self.local_context_chars,
            "transcript_segments_accepted": self.transcript_segments_accepted,
            "acoustic_wake_enabled": self.acoustic_wake_enabled,
            "wake_detections": self.wake_detections,
            "wake_cooldown_suppressed": self.wake_cooldown_suppressed,
            "wake_final_update_count": self.wake_final_update_count,
            "local_commands_executed": self.local_commands_executed,
            "local_command_failures": self.local_command_failures,
            "local_command_escalations": self.local_command_escalations,
            "local_command_last_outcome": (
                self.local_command_last_outcome.value if self.local_command_last_outcome is not None else None
            ),
            "local_command_last_router_reason": (
                self.local_command_last_router_reason.value
                if self.local_command_last_router_reason is not None
                else None
            ),
            "local_command_router_rejections": self.local_command_router_rejections,
            "local_command_followup_timeouts": self.local_command_followup_timeouts,
            "local_processing_frames_dropped": self.local_processing_frames_dropped,
            "local_command_latency_seconds_total": self.local_command_latency_seconds_total,
            "local_command_latency_seconds_max": self.local_command_latency_seconds_max,
            "context_activation_latency_seconds_total": self.context_activation_latency_seconds_total,
            "context_activation_latency_seconds_max": self.context_activation_latency_seconds_max,
            "local_context_strategy": self.local_context_strategy,
            "local_audio_seconds_observed": self.local_audio_seconds_observed,
            "local_speech_frames": self.local_speech_frames,
            "local_stt_requests": self.local_stt_requests,
            "local_stt_completed": self.local_stt_completed,
            "local_stt_failures": self.local_stt_failures,
            "local_stt_timeouts": self.local_stt_timeouts,
            "local_stt_dropped": self.local_stt_dropped,
            "local_stt_latency_seconds_total": self.local_stt_latency_seconds_total,
            "local_stt_latency_seconds_max": self.local_stt_latency_seconds_max,
            "local_stt_inflight_peak": self.local_stt_inflight_peak,
            "local_stt_pending_at_activation": self.local_stt_pending_at_activation,
        }


ACTIVATION_CONTEXT_HEADER = "Conversation near you before you were called (local transcript, may be imperfect):"
ACTIVATION_CONTEXT_FOOTER = (
    "The user has just called you by name and is talking to you now. Their request follows and is addressed "
    'to you; if it refers to the conversation above ("can you check?", "what do you think?"), use it to '
    "understand what they mean and answer or act on the live request. Requests in the conversation above were "
    "already handled; do not repeat them (do not play that music again, for example) unless asked now."
)
ACTIVATION_CUE = (
    "The user has just called you by name and is talking to you now. Their request follows and is addressed "
    "to you; answer it."
)
# Wakes after which Reachy listens for the request himself (antenna cue, request clip, quick commands): hearing
# his name, someone bending an antenna (a touch needs no name check and wakes him from sleep too), or the answer
# to a question a quick reply asked (expect_answer).
_LISTENING_WAKES = frozenset({"acoustic_wake", "antenna_touch", "awaited_answer"})
# Whisper spellings of the wake word; the acoustic wake keeps the word itself local.
_WAKE_WORD_VARIANTS = (
    r"reachy|reachie|reachi|reechy|reechie|richie|richy|richi|ritchie|ritchy|ricky|rickie|ricci|riichi|"
    r"rishi|ricchi|ritzy|ritzi|richey|reechi"
)
_WAKE_WORD_TRANSCRIPT_PATTERN = re.compile(rf"\b(?:{_WAKE_WORD_VARIANTS})\b", re.IGNORECASE)
# How close (difflib ratio) a first word must be to "reachy" to count as a misspelled name: "reach",
# "rich", "ready" pass, "we", "mom", "turn" do not.
_MISHEARD_WAKE_SIMILARITY = 0.6
# While asleep, the name must lead the utterance ("Reachy, ...", "hey Reachy"), not appear mid-sentence.
_NAME_FIRST_WORDS = 2


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _name_leads(text: str) -> bool:
    """Return whether the wake word opens ``text`` or one of its sentences ("ok. Reachy, ...")."""
    return any(
        _WAKE_WORD_TRANSCRIPT_PATTERN.search(" ".join(sentence.split()[:_NAME_FIRST_WORDS])) is not None
        for sentence in _SENTENCE_BREAK.split(text)
    )


def _longest_loud_run_s(
    audio: tuple[int, NDArray[np.int16]], frame_s: float, *, over_floor: float, min_rms: float
) -> float:
    """Return the longest stretch (s) of frames louder than ``over_floor`` times the quiet floor and ``min_rms``."""
    rate, samples = audio
    frame = max(1, int(rate * frame_s))
    count = samples.size // frame
    if count < 2:
        return 0.0
    blocks = samples[: count * frame].astype(np.float64).reshape(count, frame)
    rms = np.sqrt((blocks**2).mean(axis=1))
    loud = rms > max(float(np.percentile(rms, 10)) * over_floor, min_rms)
    longest = run = 0
    for flag in loud:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return longest * frame / rate


def frame_activation_context(transcript: str, max_chars: int) -> str:
    """Frame activation context: background transcript (if any) plus a cue that the request is addressed."""
    text = transcript.strip()
    if not text:
        # Gemini Live always uses proactive audio and may ignore speech it does not think is addressed to it;
        # the wake word stays local, so say explicitly that the following request is for the robot.
        return ACTIVATION_CUE if len(ACTIVATION_CUE) <= max_chars else ""
    budget = max_chars - len(ACTIVATION_CONTEXT_HEADER) - len(ACTIVATION_CONTEXT_FOOTER) - 4
    if budget <= 0:
        return ""
    if len(text) > budget:
        text = text[-budget:]  # keep the newest lines, which the request most likely refers to
    return f"{ACTIVATION_CONTEXT_HEADER}\n{text}\n\n{ACTIVATION_CONTEXT_FOOTER}"


def request_audio_hint(text: str) -> str:
    """Tell Gemini what local speech recognition heard, as a hint to check against the request's audio."""
    heard = " ".join(text.split())
    return (
        f'Local speech recognition heard the request as: "{heard}". It can be wrong, especially over music or '
        "other noise. The recording of the request follows; go by what you hear."
    )


def request_after_misheard_wake(text: str) -> str:
    """Return the request when the first word sounds like "Reachy" but STT spelled it oddly ("Reach, lights on").

    Used only after an acoustic wake, which already heard the name: an overheard sentence that does not
    open with something like it still goes to the conversation.
    """
    import difflib

    words = text.split()
    if len(words) < 2:
        return ""
    first = re.sub(r"[^a-z]", "", words[0].casefold())
    if difflib.SequenceMatcher(None, first, "reachy").ratio() < _MISHEARD_WAKE_SIMILARITY:
        return ""
    return " ".join(words[1:]).lstrip(" ,.;:!?¡¿-").strip()


def request_after_wake(text: str) -> str:
    """Return what follows the last wake word in a transcript segment ("" when nothing does)."""
    matches = list(_WAKE_WORD_TRANSCRIPT_PATTERN.finditer(text))
    if not matches:
        return ""
    return text[matches[-1].end() :].lstrip(" ,.;:!?¡¿-").strip()


# Everyday commands answered without the cloud (quick_commands.QuickCommands.handle): returns a reply
# with ``text``, ``language``, ``kind``, ``sleep`` and ``after``, or None to open the conversation.
QuickCommandHandler: TypeAlias = Callable[[str], Awaitable[object | None]]
# Speaks reply text: yields (sample_rate, PCM16 frame) pieces (local_speech.LocalSpeech.stream).
ReplySpeaker: TypeAlias = Callable[[str, str], AsyncIterator[tuple[int, np.ndarray]]]


@dataclass(frozen=True)
class _PendingTurn:
    """Something Reachy does once he is free: speak an announcement, or listen for an awaited answer."""

    reason: str  # "announcement" or "awaited_answer"
    expires_at: float
    text: str = ""
    language: str = "en"
    answer: QuickCommandHandler | None = None


class HybridRuntime:
    """Coordinate local ambient capture with one on-demand cloud handler."""

    _MAX_ADDRESSED_TEXT_CHARS = 512
    _ACOUSTIC_WAKE_ACK_TEXT = "Acknowledge the wake word briefly, then listen."
    # About one second of 10 ms frames, so short event-loop stalls do not drop wake-word audio.
    _LOCAL_PROCESSING_QUEUE_SIZE = 100
    _PROVIDER_ERROR_DETAIL_CHARS = 200
    # At most this many queued frames are merged into one local processing pass.
    _LOCAL_PROCESSING_BATCH_FRAMES = 25
    # Bound on waiting for Whisper to finish the sentence in progress when an acoustic wake fires.
    _ACOUSTIC_CONTEXT_SETTLE_S = 3.0
    # A quick command (device action plus the start of its reply) must finish within this, or the
    # conversation takes over.
    _QUICK_COMMAND_TIMEOUT_S = 8.0
    # Margin after a quick reply's audio before listening for the wake word again.
    _QUICK_REPLY_TAIL_S = 0.4
    # Cap on one quick reply (synthesis plus playback): about 0.07 s of speech per character.
    _QUICK_REPLY_BASE_S = 6.0
    _QUICK_REPLY_PER_CHAR_S = 0.12
    # A wake segment this short is taken as the wake word alone; the request is the next segment.
    _WAKE_ONLY_MAX_WORDS = 2
    # The request clip after an acoustic wake: wait this long for speech to start, end it after this much
    # silence, and never let it run longer than the cap.
    _REQUEST_CLIP_START_S = 3.0
    _REQUEST_CLIP_SILENCE_S = 0.8
    _REQUEST_CLIP_MAX_S = 8.0
    _REQUEST_CLIP_STT_TIMEOUT_S = 6.0
    # A voice message (record_message): wait this long for speech after the beep, end it after this much
    # silence (children pause mid-thought), never let it run longer than the cap; keep this much silence.
    # A touch on his head ends it sooner, so the silence can be long enough for a child's pause.
    _MESSAGE_START_S = 8.0
    _MESSAGE_SILENCE_S = 5.0
    _MESSAGE_MAX_S = 60.0
    _MESSAGE_MARGIN_S = 0.5
    # Ended by a touch: drop this much from the end (the hand on his head, near the microphones), and send
    # nothing when less than _MESSAGE_MIN_S remains (only the touch was heard).
    _MESSAGE_TOUCH_TRIM_S = 0.6
    _MESSAGE_MIN_S = 0.8
    # After a question (expect_answer), Reachy waits this long for the answer to start, without his name.
    _ANSWER_START_S = 15.0
    # An awaited answer not started within this (Reachy still speaking or busy) is dropped.
    _ANSWER_PENDING_S = 30.0
    # An announcement waits at most this long for Reachy to be free (no conversation or command).
    _ANNOUNCEMENT_PENDING_S = 120.0
    _PENDING_TURN_POLL_S = 0.1
    # A segment cut at an acoustic wake, with no name in it, this short is the misheard name plus the start
    # of the request.
    _UNNAMED_WAKE_MAX_WORDS = 4
    # The last this many seconds the wake model heard stay in memory so Whisper can check the name in them
    # before Reachy reacts. On 2026-10-28, 59 of 86 wakes had no name (breakfast clinks) and real calls
    # scored 0.3-0.93; checking the name let a 0.3 threshold wake 34 of 38 named calls with no false wake.
    _WAKE_NAME_WINDOW_S = 2.5
    # Bound on each name check (whisper-small takes about 0.4 s on the mini, turbo about 1.6 s).
    _WAKE_NAME_CHECK_TIMEOUT_S = 4.0
    # Whisper can mishear a real call ("Reggie.", "Preciso.", "What did she do?" for three "Richie, lights
    # off" on 2026-10-28). Awake, a wake model this sure still counts when the window holds a voice: a loud run
    # as long as a word, where kitchen clatter is clinks (word runs 0.33-0.45 s at 21-30 dB over the room;
    # about 60 clatter candidates at most 0.12 s and 14 dB).
    _TRUSTED_WAKE_SCORE = 0.8
    _VOICE_FRAME_S = 0.03
    _VOICE_RUN_S = 0.25
    _VOICE_OVER_FLOOR = 4.0
    _VOICE_MIN_RMS = 300.0
    # While music plays (it was playing at the wake, or Reachy started it), close the conversation once
    # Reachy has answered the user's latest turn and the user stays quiet this long. Gemini hears music as
    # ongoing speech, so the usual idle rule never fires while music plays.
    _MUSIC_WIND_DOWN_SILENCE_S = 3.0
    # A reply that ends in a question gets longer to answer.
    _MUSIC_WIND_DOWN_QUESTION_SILENCE_S = 10.0
    # Close anyway if Reachy has not answered within this long after the user's last words, or this long
    # after the unanswered turn began (music lyrics transcribed as speech would otherwise hold it open).
    _MUSIC_WIND_DOWN_REPLY_CAP_S = 20.0
    _MUSIC_WIND_DOWN_TURN_CAP_S = 60.0
    # User speech that starts after Reachy's reply holds the close this long, until its transcript arrives.
    _MUSIC_WIND_DOWN_SPEECH_HOLD_S = 3.0
    # Follow-up window (outside music): people speaking since Reachy's reply without him answering for
    # the whole window closes the conversation at the next pause this long; twice the window closes it
    # regardless (a side conversation or a TV can talk without pauses). Nobody is cut off mid-sentence:
    # while Gemini hears speech that began after his reply, the close waits, up to twice the window.
    _FOLLOW_UP_SPEECH_GAP_S = 2.0
    # A close Reachy asked for (end_conversation, go_to_sleep) waits for his goodbye to finish playing:
    # at least this long, and at most the cap if the provider stays busy.
    _CLOSE_REQUEST_GRACE_S = 1.0
    _CLOSE_REQUEST_CAP_S = 12.0
    # The addressee check runs on unanswered speech after a reply once it has this many words and the
    # room pauses this long; it re-checks when this many new words arrive, a few times per stretch.
    _ADDRESSEE_MIN_WORDS = 4
    _ADDRESSEE_PAUSE_S = 1.5
    _ADDRESSEE_MAX_CHECKS = 3
    _ADDRESSEE_TEXT_CHARS = 800
    _SHUTDOWN_TIMEOUT_S = 15.0
    # Keep enough live PCM for the bounded provider setup handshake (15 s) and
    # leave a small margin for scheduling jitter, while still bounding memory.
    _POSTACTIVATION_BUFFER_SECONDS = 20.0
    _SPEECH_STARTED_REASONS = frozenset({"user_speech_started"})
    _SPEECH_STOPPED_REASONS = frozenset(
        {
            "user_speech_stopped",
            "user_transcription_completed",
            "user_transcription_complete",
            "user_turn_complete",
            "input_audio_transcription_completed",
            "conversation_turn_complete",
        }
    )

    _VALID_TRANSITIONS: dict[RuntimeMode, frozenset[RuntimeMode]] = {
        RuntimeMode.LOCAL_AMBIENT: frozenset({RuntimeMode.LOCAL_COMMAND}),
        RuntimeMode.LOCAL_COMMAND: frozenset(
            {RuntimeMode.LOCAL_AMBIENT, RuntimeMode.CLOUD_CONVERSATION, RuntimeMode.STOPPING}
        ),
        RuntimeMode.CLOUD_CONVERSATION: frozenset({RuntimeMode.STOPPING}),
        RuntimeMode.STOPPING: frozenset({RuntimeMode.LOCAL_AMBIENT}),
    }

    def __init__(
        self,
        handler_factory: HandlerFactory,
        *,
        ambient_buffer: AmbientAudioBuffer | None = None,
        local_processor: LocalProcessingBackend | None = None,
        context_processor: AmbientContextProcessor | None = None,
        wake_detector: TranscriptWakeWordDetector | None = None,
        acoustic_wake_detector: AcousticWakeWordDetector | None = None,
        wake_clips: WakeClipRecorder | None = None,
        intent_router: LocalIntentRouter | None = None,
        action_executor: LocalActionExecutor | None = None,
        local_processing_close_callback: LocalProcessingCloseCallback | None = None,
        acknowledgement_callback: AcknowledgementCallback | None = None,
        cloud_session_observer: Callable[[bool], Awaitable[None]] | None = None,
        state_observer: StateObserver | None = None,
        inactivity_timeout_s: float = 0.0,
        follow_up_window_s: float = 0.0,
        follow_up_window_for: Callable[[], float] | None = None,
        addressee_check: AddresseeChecker | None = None,
        quick_commands: QuickCommandHandler | None = None,
        reply_speaker: ReplySpeaker | None = None,
        speaking_callback: Callable[[bool], None] | None = None,
        request_transcriber: Callable[[AudioFrame], Awaitable[str]] | None = None,
        name_check_transcriber: Callable[[AudioFrame], Awaitable[str]] | None = None,
        listening_observer: Callable[[bool], Awaitable[None]] | None = None,
        wake_followup_timeout_s: float = DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
        start_asleep: bool = False,
        clock: Clock = time.monotonic,
        monitor_interval_s: float = 0.1,
    ) -> None:
        """Initialize an idle runtime without constructing a provider handler."""
        if inactivity_timeout_s < 0:
            raise ValueError("Inactivity timeout must not be negative")
        if follow_up_window_s < 0 or not math.isfinite(follow_up_window_s):
            raise ValueError("Follow-up window must be finite and not negative")
        if (
            wake_followup_timeout_s <= 0
            or not math.isfinite(wake_followup_timeout_s)
            or wake_followup_timeout_s > MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "Wake follow-up timeout must be finite, positive, and no greater than "
                f"{MAX_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS:g} seconds"
            )
        if monitor_interval_s <= 0:
            raise ValueError("Inactivity monitor interval must be positive")

        self._handler_factory = handler_factory
        self._ambient_buffer = ambient_buffer if ambient_buffer is not None else AmbientAudioBuffer()
        self._local_processor = local_processor if local_processor is not None else NullLocalProcessingBackend()
        self._context_processor = context_processor
        # Both may run: acoustic wake is the fast path, and the transcript detector catches a wake word
        # spoken mid-sentence that the acoustic model scored below threshold. activate() admits only one.
        self._wake_detector = wake_detector
        self._acoustic_wake_detector = acoustic_wake_detector
        # Fed only the audio the wake model hears while listening locally; saves clips to retrain it.
        self._wake_clips = wake_clips
        self._acoustic_epoch = 0
        self._acoustic_stream_epoch: int | None = None
        self._wake_window = bytearray()
        self._wake_window_rate = 0
        self._wake_name_audio: tuple[int, NDArray[np.int16]] | None = None
        self._wake_score: float | None = None  # the wake model's score for that audio
        self._intent_router = intent_router
        self._action_executor = action_executor
        self._quick_commands = quick_commands
        self._reply_speaker = reply_speaker
        self._speaking_callback = speaking_callback
        # Transcribes the whole request after an acoustic wake as one clip (the accurate local model).
        self._request_transcriber = request_transcriber
        # Checks the name in the wake audio first (the fast ambient model); the request model is the second try.
        self._name_check_transcriber = name_check_transcriber
        self._request_clip: list[AudioFrame] | None = None
        # Audio after the wake that was still waiting for local processing when the clip began: on a busy Mac
        # the wake is detected late, so the start of the request ("Prende" in "prende la luz") is already
        # past the activation barrier. queued = in the local worker at detection, late = captured but routed
        # locally after activation. The clip is queued + late + post-activation frames.
        self._request_clip_queued: list[AudioFrame] | None = None
        self._request_clip_queued_epoch = -1
        self._request_clip_late: list[AudioFrame] | None = None
        # The segment holding a transcript wake's request: if whisper-small's text is not a quick command, its
        # audio is transcribed again with the accurate model before the conversation opens.
        self._transcript_wake_segment_sequence: int | None = None
        self._request_clip_vad = RmsEnergyVAD(threshold=config.LOCAL_VAD_RMS_THRESHOLD)
        # The last request clip stopped at its length cap while the speaker was still talking.
        self._request_clip_cut_off = False
        # A voice message is being recorded into _request_clip: post-activation audio is not kept for Gemini.
        self._recording_message = False
        # Told True when Reachy starts listening to a request (e.g. lower the music) and False when he is done
        # without a conversation; a conversation reports through cloud_session_observer instead.
        self._listening_observer = listening_observer
        self._observer_tasks: set[asyncio.Task[None]] = set()
        # Spoken replies to quick commands, played while no cloud handler exists.
        self._local_output: deque[AudioFrame | AdditionalOutputs] = deque()
        self._local_processing_close_callback = local_processing_close_callback
        self._acknowledgement_callback = acknowledgement_callback
        # Told True when a cloud conversation starts and False when it has ended (e.g. to lower music).
        self._cloud_session_observer = cloud_session_observer
        self._cloud_session_notified = False
        self._cloud_session_tasks: set[asyncio.Future[None]] = set()
        self._music_wind_down_requested_at: float | None = None
        self._last_user_turn_at = 0.0
        self._user_turn_started_at = 0.0
        self._last_user_speech_start_at = -math.inf
        self._last_reply_was_question = False
        self._follow_up_window_s = float(follow_up_window_s)
        # Optional per-conversation window (e.g. longer while a young child is talking); falls back to the fixed one.
        self._follow_up_window_for = follow_up_window_for
        # (reason, requested_at) for a close Reachy asked for; it happens once his goodbye has played.
        self._close_requested: tuple[str, float] | None = None
        # Told to go to sleep: until a wake is confirmed by local STT hearing the name first, acoustic
        # detections and mid-sentence mentions do not wake him (they fire on ordinary conversation).
        self._asleep = bool(start_asleep)
        self._addressee_check = addressee_check
        self._addressee_task: asyncio.Task[None] | None = None
        self._reply_parts: list[str] = []
        self._last_assistant_text_at = -math.inf
        self._unanswered_parts: list[str] = []
        self._unanswered_reply_marker = -math.inf
        self._addressee_checked_words = 0
        self._addressee_checks = 0
        # (generation, unanswered marker) the addressee check judged not meant for Reachy.
        self._not_addressed: tuple[int, float] | None = None
        self._state_observer = state_observer
        self._activity_observer: ActivityObserver | None = None
        self._transcript_observer: TranscriptObserver | None = None
        # Sees each final room transcript before wake detection while Reachy waits for his name; returns True
        # when it acted on it (writing practice's "¡ya!", said without the name).
        self._room_listener: Callable[[str], bool] | None = None
        self._inactivity_timeout_s = float(inactivity_timeout_s)
        self._wake_followup_timeout_s = float(wake_followup_timeout_s)
        self._clock = clock
        self._monitor_interval_s = monitor_interval_s

        self._state_lock = asyncio.Lock()
        self._provider_call_lock = asyncio.Lock()
        self._lifecycle_lock = threading.RLock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._mode: RuntimeMode = RuntimeMode.LOCAL_AMBIENT
        self._generation = 0
        self._active_handler: ConversationHandler | None = None
        self._provider_task: asyncio.Task[None] | None = None
        self._inactivity_task: asyncio.Task[None] | None = None
        self._activation_task: asyncio.Task[bool] | None = None
        self._wake_followup_future: asyncio.Future[TranscriptSegment] | None = None
        self._wake_followup_after_sequence: int | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False
        self._shutdown_error: str | None = None
        self._stopping_handler: ConversationHandler | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_deadline: float | None = None
        self._close_deadline: float | None = None
        self._last_conversation_activity = self._clock()
        self._user_speaking = False
        self._interaction_busy = False
        self._playback_until = 0.0
        self._connected_since: float | None = None
        self._connection_ready = False
        self._shutdown_handler_tasks: dict[
            int, tuple[weakref.ReferenceType[ConversationHandler], asyncio.Task[None]]
        ] = {}
        self._capture_barrier_provider: CaptureBarrierProvider | None = None
        self._activation_capture_barrier: int | None = None
        self._postactivation_frames: deque[PostActivationFrame] = deque()
        self._postactivation_seconds = 0.0
        self._postactivation_flush_task: asyncio.Task[None] | None = None
        self._activation_text_task: asyncio.Task[None] | None = None
        self._pending_activation_text: tuple[int, str] | None = None
        self._postactivation_frames_dropped = 0
        # Frames dropped since the queue was last cleared: one warning each time it fills, a total when cleared.
        self._postactivation_drop_burst = 0
        self._playback_clearer: Callable[[], None] | None = None
        self._local_start_event = asyncio.Event()
        self._output_wakeup_event = asyncio.Event()
        self._local_event_tasks: set[asyncio.Task[bool]] = set()
        self._pending_turns: deque[_PendingTurn] = deque()
        self._pending_turns_task: asyncio.Task[bool] | None = None
        self._next_turn: _PendingTurn | None = None  # handed from the pending-turn loop to its activation
        self._local_processing_queue: asyncio.Queue[LocalProcessingItem] | None = None
        self._local_processing_task: asyncio.Task[None] | None = None
        self._postactivation_speech_observed = False
        # Per-cloud-session diagnostics, logged once when the session ends (metadata only).
        self._session_audio_bytes_start = 0
        self._local_processing_drop_burst = False
        self._session_video_frames_start = 0
        self._session_queued_frames = 0
        self._session_ack_state = "not_needed"
        self._acoustic_ack_vad = RmsEnergyVAD()
        self._pending_acoustic_ack_generation: int | None = None
        self._acoustic_ack_task: asyncio.Task[None] | None = None

        self._metrics_lock = threading.Lock()
        self._ambient_audio_bytes_sent_to_cloud = 0
        self._cloud_audio_bytes_sent = 0
        self._cloud_video_frames_sent = 0
        self._gemini_session_duration = 0.0
        self._local_context_chars = 0
        self._local_commands_executed = 0
        self._local_command_failures = 0
        self._local_command_escalations = 0
        self._local_command_last_outcome: LocalCommandOutcome | None = None
        self._local_command_last_router_reason: IntentDecisionReason | None = None
        self._local_command_router_rejections = 0
        self._local_command_followup_timeouts = 0
        self._local_processing_frames_dropped = 0
        self._local_command_latency_seconds_total = 0.0
        self._local_command_latency_seconds_max = 0.0
        self._context_activation_latency_seconds_total = 0.0
        self._context_activation_latency_seconds_max = 0.0

    @property
    def mode(self) -> RuntimeMode:
        """Return the current runtime mode."""
        with self._lifecycle_lock:
            return self._mode

    @property
    def active_handler(self) -> ConversationHandler | None:
        """Return the selected cloud handler, if any."""
        with self._lifecycle_lock:
            return self._active_handler

    @property
    def generation(self) -> int:
        """Return the current activation generation."""
        with self._lifecycle_lock:
            return self._generation

    @property
    def ambient_buffer(self) -> AmbientAudioBuffer:
        """Return the bounded local ambient buffer."""
        return self._ambient_buffer

    @property
    def inactivity_timeout_s(self) -> float:
        """Return the configured cloud inactivity threshold."""
        return self._inactivity_timeout_s

    @property
    def interaction_busy(self) -> bool:
        """Return the provider's authoritative interaction busy state."""
        with self._lifecycle_lock:
            return self._interaction_busy

    @property
    def playback_active(self) -> bool:
        """Return whether estimated queued playback is still active."""
        with self._lifecycle_lock:
            return self._playback_is_active_locked(self._clock())

    async def get_transcript_preview(self) -> str | None:
        """Return the bounded local transcript, or ``None`` when unavailable."""
        self._assert_owner_loop()
        with self._lifecycle_lock:
            if self._closed:
                return None
        processor = self._context_processor
        if processor is None:
            return None
        return processor.get_transcript_preview(now=self._clock())

    @property
    def status(self) -> RuntimeStatusSnapshot:
        """Return an immutable runtime status snapshot."""
        return self.status_snapshot()

    @property
    def metrics(self) -> TrafficMetricsSnapshot:
        """Return an immutable traffic and duration snapshot."""
        return self.metrics_snapshot()

    @property
    def postactivation_frames_dropped(self) -> int:
        """Return the number of handshake frames dropped at the queue bound."""
        with self._lifecycle_lock:
            return self._postactivation_frames_dropped

    def status_snapshot(self) -> RuntimeStatusSnapshot:
        """Return an immutable runtime status snapshot."""
        with self._lifecycle_lock:
            mode = self._mode
            generation = self._generation
            active = self._active_handler is not None
            closed = self._closed
            provider_task = self._provider_task
            inactivity_task = self._inactivity_task
            connected = self._connection_ready
            interaction_busy = self._interaction_busy
            playback_active = self._playback_is_active_locked(self._clock())
            shutdown_error = self._shutdown_error
            postactivation_frames_dropped = self._postactivation_frames_dropped
        metrics = self.metrics_snapshot()
        wake_metrics = self._wake_detector.metrics_snapshot() if self._wake_detector is not None else None
        if self._acoustic_wake_detector is not None:
            wake_metrics = self._acoustic_wake_detector.metrics_snapshot()
        context_metrics = self._context_processor.metrics if self._context_processor is not None else None
        with self._metrics_lock:
            local_context_chars = self._local_context_chars
            local_commands_executed = self._local_commands_executed
            local_command_failures = self._local_command_failures
            local_command_escalations = self._local_command_escalations
            local_command_last_outcome = self._local_command_last_outcome
            local_command_last_router_reason = self._local_command_last_router_reason
            local_command_router_rejections = self._local_command_router_rejections
            local_command_followup_timeouts = self._local_command_followup_timeouts
            local_processing_frames_dropped = self._local_processing_frames_dropped
            local_command_latency_seconds_total = self._local_command_latency_seconds_total
            local_command_latency_seconds_max = self._local_command_latency_seconds_max
            context_activation_latency_seconds_total = self._context_activation_latency_seconds_total
            context_activation_latency_seconds_max = self._context_activation_latency_seconds_max
        return RuntimeStatusSnapshot(
            mode=mode,
            generation=generation,
            active=active,
            closed=closed,
            provider_task_running=provider_task is not None and not provider_task.done(),
            inactivity_monitor_running=inactivity_task is not None and not inactivity_task.done(),
            connected=connected,
            interaction_busy=interaction_busy,
            playback_active=playback_active,
            shutdown_error=shutdown_error,
            ambient_audio_seconds_buffered=metrics.ambient_audio_seconds_buffered,
            ambient_audio_bytes_sent_to_cloud=metrics.ambient_audio_bytes_sent_to_cloud,
            cloud_audio_bytes_sent=metrics.cloud_audio_bytes_sent,
            cloud_video_frames_sent=metrics.cloud_video_frames_sent,
            gemini_session_duration=metrics.gemini_session_duration,
            postactivation_frames_dropped=postactivation_frames_dropped,
            local_context_chars=local_context_chars,
            transcript_segments_accepted=(
                context_metrics.transcript_segments_accepted if context_metrics is not None else 0
            ),
            acoustic_wake_enabled=self._acoustic_wake_detector is not None,
            wake_detections=wake_metrics.detection_count if wake_metrics is not None else 0,
            wake_cooldown_suppressed=wake_metrics.cooldown_suppressed_count if wake_metrics is not None else 0,
            wake_final_update_count=wake_metrics.final_update_count if wake_metrics is not None else 0,
            local_commands_executed=local_commands_executed,
            local_command_failures=local_command_failures,
            local_command_escalations=local_command_escalations,
            local_command_last_outcome=local_command_last_outcome,
            local_command_last_router_reason=local_command_last_router_reason,
            local_command_router_rejections=local_command_router_rejections,
            local_command_followup_timeouts=local_command_followup_timeouts,
            local_processing_frames_dropped=local_processing_frames_dropped,
            local_command_latency_seconds_total=local_command_latency_seconds_total,
            local_command_latency_seconds_max=local_command_latency_seconds_max,
            context_activation_latency_seconds_total=context_activation_latency_seconds_total,
            context_activation_latency_seconds_max=context_activation_latency_seconds_max,
            local_context_strategy=(
                self._context_processor.config.strategy.value if self._context_processor is not None else None
            ),
            local_audio_seconds_observed=context_metrics.audio_seconds_observed
            if context_metrics is not None
            else 0.0,
            local_speech_frames=context_metrics.speech_frames if context_metrics is not None else 0,
            local_stt_requests=(
                context_metrics.continuous_stt_requests + context_metrics.activation_stt_requests
                if context_metrics is not None
                else 0
            ),
            local_stt_completed=(
                context_metrics.continuous_stt_completed + context_metrics.activation_stt_completed
                if context_metrics is not None
                else 0
            ),
            local_stt_failures=(
                context_metrics.continuous_stt_failures + context_metrics.activation_stt_failures
                if context_metrics is not None
                else 0
            ),
            local_stt_timeouts=(
                context_metrics.continuous_stt_timeouts + context_metrics.activation_stt_timeouts
                if context_metrics is not None
                else 0
            ),
            local_stt_dropped=context_metrics.continuous_stt_dropped if context_metrics is not None else 0,
            local_stt_latency_seconds_total=(
                context_metrics.continuous_stt_latency_seconds_total
                + context_metrics.activation_stt_latency_seconds_total
                if context_metrics is not None
                else 0.0
            ),
            local_stt_latency_seconds_max=(
                max(
                    context_metrics.continuous_stt_latency_seconds_max,
                    context_metrics.activation_stt_latency_seconds_max,
                )
                if context_metrics is not None
                else 0.0
            ),
            local_stt_inflight_peak=context_metrics.stt_inflight_peak if context_metrics is not None else 0,
            local_stt_pending_at_activation=(
                context_metrics.continuous_stt_pending_at_activation if context_metrics is not None else 0
            ),
        )

    def metrics_snapshot(self) -> TrafficMetricsSnapshot:
        """Return an immutable traffic and duration snapshot."""
        with self._metrics_lock:
            return TrafficMetricsSnapshot(
                ambient_audio_seconds_buffered=self._ambient_buffer.seconds_buffered,
                ambient_audio_bytes_sent_to_cloud=self._ambient_audio_bytes_sent_to_cloud,
                cloud_audio_bytes_sent=self._cloud_audio_bytes_sent,
                cloud_video_frames_sent=self._cloud_video_frames_sent,
                gemini_session_duration=self._gemini_session_duration,
            )

    def set_state_observer(self, observer: StateObserver | None) -> None:
        """Attach an observer for metadata-only runtime transitions."""
        self._state_observer = observer

    def set_capture_barrier_provider(self, provider: CaptureBarrierProvider | None) -> None:
        """Set a capture-sequence source used to fence queued pre-activation frames."""
        self._capture_barrier_provider = provider

    def set_playback_clearer(self, clearer: Callable[[], None] | None) -> None:
        """Set the LocalStream-owned playback flush callback."""
        self._playback_clearer = clearer

    def set_playback_active(self, active: bool) -> None:
        """Set or clear an explicit playback boundary."""
        with self._lifecycle_lock:
            if active:
                self._playback_until = math.inf
            else:
                self._playback_until = 0.0

    def note_playback_queued(self, duration_s: float) -> None:
        """Extend the estimated playback boundary by one queued audio duration."""
        if duration_s <= 0 or not math.isfinite(duration_s):
            return
        with self._lifecycle_lock:
            if self._mode is not RuntimeMode.CLOUD_CONVERSATION or self._active_handler is None:
                return
            now = self._clock()
            self._playback_until = max(self._playback_until, now) + duration_s

    def _playback_is_active_locked(self, now: float) -> bool:
        return self._mode is RuntimeMode.CLOUD_CONVERSATION and self._playback_until > now

    def clear_provider_output(self) -> None:
        """Drain only the active provider output queue; playback remains LocalStream-owned."""
        with self._lifecycle_lock:
            handler = self._active_handler
        if handler is None:
            return
        self._drain_handler_output(handler)

    @staticmethod
    def _drain_handler_output(handler: ConversationHandler) -> None:
        """Drain one provider queue without replacing it."""
        while not handler.output_queue.empty():
            try:
                handler.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _is_connected(self) -> bool:
        """Return whether the selected provider reports a live connection."""
        with self._lifecycle_lock:
            return self._active_handler is not None and self._connection_ready

    def seconds_since_activity(self) -> float:
        """Return seconds since the active cloud handler's last activity."""
        with self._lifecycle_lock:
            last_activity = self._last_conversation_activity
        return max(0.0, self._clock() - last_activity)

    def set_activity_observer(self, observer: ActivityObserver | None) -> None:
        """Attach an observer for active provider conversation activity."""
        self._activity_observer = observer

    def set_transcript_observer(self, observer: TranscriptObserver | None) -> None:
        """Attach an observer for active provider transcript updates."""
        self._transcript_observer = observer

    def set_room_listener(self, listener: Callable[[str], bool] | None) -> None:
        """Offer each final room transcript to ``listener`` first while waiting for the name; True consumes it."""
        self._room_listener = listener

    def record_provider_traffic(self, kind: TrafficKind, byte_count: int) -> None:
        """Record media only after a provider confirms a successful send."""
        if byte_count <= 0:
            return
        if kind not in ("audio", "video"):
            raise ValueError(f"Unsupported provider traffic kind: {kind!r}")
        with self._metrics_lock:
            if kind == "audio":
                self._cloud_audio_bytes_sent += byte_count
            else:
                self._cloud_video_frames_sent += 1

    def _assert_owner_loop(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lifecycle_lock:
            owner_loop = self._owner_loop
            if owner_loop is None:
                self._owner_loop = loop
            elif owner_loop is not loop:
                raise RuntimeError("HybridRuntime async operation called from a foreign event loop")

    def _log_cloud_session_summary(self, reason: str) -> None:
        """Log what one cloud session forwarded, to diagnose sessions that never hear the user."""
        with self._metrics_lock:
            audio_bytes = self._cloud_audio_bytes_sent - self._session_audio_bytes_start
            video_frames = self._cloud_video_frames_sent - self._session_video_frames_start
        with self._lifecycle_lock:
            ack_task = self._acoustic_ack_task
            logger.info(
                "Cloud session summary reason=%s audio_kb_sent=%.0f video_frames=%d queued_frames=%d "
                "still_queued=%d ack=%s ack_task_running=%s connection_ready=%s",
                reason,
                audio_bytes / 1024,
                video_frames,
                self._session_queued_frames,
                len(self._postactivation_frames),
                self._session_ack_state,
                ack_task is not None and not ack_task.done(),
                self._connection_ready,
            )

    def _transition(self, new_mode: RuntimeMode, reason: str) -> None:
        with self._lifecycle_lock:
            old_mode = self._mode
            if new_mode not in self._VALID_TRANSITIONS[old_mode]:
                raise ValueError(f"Invalid runtime transition: {old_mode.value} -> {new_mode.value}")
            self._mode = new_mode
            self._acoustic_epoch += 1
            generation = self._generation
            self._output_wakeup_event.set()
        logger.info(
            "Runtime transition old=%s new=%s reason=%s generation=%d",
            old_mode.value,
            new_mode.value,
            reason,
            generation,
        )
        self._notify_state_observer(new_mode, reason, generation)

    def _notify_state_observer(self, state: RuntimeMode, reason: str, generation: int) -> None:
        observer = self._state_observer
        if observer is not None:
            try:
                observer(state, reason, generation)
            except Exception as error:
                logger.warning("Runtime state observer failed: %s", type(error).__name__)

    async def activate(
        self,
        reason: str = "keyboard",
        *,
        addressed_text: str = "",
        wake_sequence: int | None = None,
    ) -> bool:
        """Acknowledge locally, then create and start one cloud generation."""
        self._assert_owner_loop()
        addressed_text = addressed_text.strip()[: self._MAX_ADDRESSED_TEXT_CHARS]
        with self._lifecycle_lock:
            closed = self._closed
            mode = self._mode
            activation_task = self._activation_task
        if closed or mode in (RuntimeMode.CLOUD_CONVERSATION, RuntimeMode.STOPPING):
            return mode is RuntimeMode.CLOUD_CONVERSATION and not closed

        if activation_task is None or activation_task.done():
            activation_task = asyncio.create_task(
                self._run_activation(
                    reason,
                    addressed_text=addressed_text,
                    wake_sequence=wake_sequence,
                ),
                name="hybrid-activation",
            )
            with self._lifecycle_lock:
                self._activation_task = activation_task
        return await activation_task

    def observe_local_transcript(self, segment: TranscriptSegment) -> None:
        """Route one final local transcript through wake or its bounded follow-up."""
        # The current local STT boundary emits complete final segments. Waiting
        # for one preserves the addressed suffix for local commands; acting on
        # a partial "Reachy" would start cloud setup before the command arrives.
        if not segment.final:
            return
        detector = self._wake_detector
        with self._lifecycle_lock:
            mode = self._mode
            followup = self._wake_followup_future
            if (
                followup is not None
                and not followup.done()
                and not self._closed
                and mode is not RuntimeMode.CLOUD_CONVERSATION
                and (
                    self._wake_followup_after_sequence is None or segment.sequence > self._wake_followup_after_sequence
                )
            ):
                followup.set_result(
                    TranscriptSegment(
                        text=segment.text[: self._MAX_ADDRESSED_TEXT_CHARS],
                        started_at=segment.started_at,
                        ended_at=segment.ended_at,
                        sequence=segment.sequence,
                        final=segment.final,
                    )
                )
                return
            if self._closed or mode is not RuntimeMode.LOCAL_AMBIENT:
                return
        listener = self._room_listener
        if listener is not None and listener(segment.text):
            return
        if detector is None:
            return
        try:
            detection = detector.process_transcript(
                segment.text,
                is_final=segment.final,
                speech_started_at=segment.started_at,
                observed_at=segment.ended_at,
            )
        except (TypeError, ValueError) as error:
            logger.warning("Local wake detection failed: %s", type(error).__name__)
            return
        if detection is None:
            return
        detector.consume_detection()
        if self._wake_clips is not None and self._acoustic_wake_detector is not None:
            # Still listening locally, so the wake model missed a name that local STT heard.
            self._wake_clips.save_name_heard(segment.ended_at - segment.started_at)
        with self._lifecycle_lock:
            asleep = self._asleep
        if asleep and not (detection.wake_word_is_first_word or _name_leads(segment.text)):
            logger.info("Asleep; ignoring the name mentioned mid-sentence")
            return
        followup_waiter: asyncio.Future[TranscriptSegment] | None = None
        if not detection.remaining_text:
            try:
                followup_waiter = asyncio.get_running_loop().create_future()
            except RuntimeError:
                logger.warning("Local wake follow-up skipped: runtime loop unavailable")
                return
            with self._lifecycle_lock:
                if self._closed or self._mode is not RuntimeMode.LOCAL_AMBIENT:
                    return
                self._wake_followup_future = followup_waiter
                self._wake_followup_after_sequence = segment.sequence
        addressed_text = detection.remaining_text
        context_fence = segment.sequence
        self._transcript_wake_segment_sequence = segment.sequence
        if not detection.wake_word_is_first_word:
            if addressed_text:
                # Mid-sentence wake ("…this weekend, Reachy, can you check?"): the whole sentence is the request,
                # so what the request refers to is not lost from the seeded context.
                addressed_text = segment.text[: self._MAX_ADDRESSED_TEXT_CHARS]
            else:
                # Wake at the end of a sentence: keep that sentence as context; the follow-up is the request.
                context_fence = segment.sequence + 1
        try:
            task = asyncio.create_task(
                self.activate(
                    "wake_word",
                    addressed_text=addressed_text,
                    wake_sequence=context_fence,
                ),
                name="hybrid-wake-activation",
            )
        except RuntimeError:
            if followup_waiter is not None:
                self._clear_wake_followup_waiter(followup_waiter)
            logger.warning("Local wake activation skipped: runtime loop unavailable")
            return
        self._local_event_tasks.add(task)
        task.add_done_callback(self._local_event_tasks.discard)

    def _clear_wake_followup_waiter(self, waiter: asyncio.Future[TranscriptSegment] | None = None) -> None:
        """Release the pending one-shot wake follow-up without retaining text."""
        with self._lifecycle_lock:
            current = self._wake_followup_future
            if current is None or (waiter is not None and current is not waiter):
                return
            self._wake_followup_future = None
            self._wake_followup_after_sequence = None
            if not current.done():
                current.cancel()

    async def _wait_for_wake_followup(self) -> tuple[TranscriptSegment | None, int | None]:
        """Wait briefly for one final transcript after a wake-only segment."""
        with self._lifecycle_lock:
            waiter = self._wake_followup_future
            wake_sequence = self._wake_followup_after_sequence
        if waiter is None:
            return None, wake_sequence
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout=self._wake_followup_timeout_s), wake_sequence
        except asyncio.TimeoutError:
            return None, wake_sequence
        finally:
            self._clear_wake_followup_waiter(waiter)

    async def start_up(self) -> None:
        """Keep the local ambient runtime alive without opening a provider."""
        self._assert_owner_loop()
        with self._lifecycle_lock:
            closed = self._closed
        if closed:
            return
        self._local_start_event.clear()
        try:
            await self._local_start_event.wait()
        except asyncio.CancelledError:
            raise

    async def shutdown(self) -> None:
        """Close local processing and any active cloud generation."""
        await self.close()

    async def _run_activation(
        self,
        reason: str,
        *,
        addressed_text: str,
        wake_sequence: int | None,
    ) -> bool:
        handler: ConversationHandler | None = None
        generation = 0
        clip_task: asyncio.Task[str] | None = None
        listening_notified = False
        # Gemini hears the request itself (the clip's audio) with the local text only as a hint.
        request_in_audio = False
        turn, self._next_turn = self._next_turn, None
        try:
            async with self._state_lock:
                with self._lifecycle_lock:
                    if self._closed or self._mode is not RuntimeMode.LOCAL_AMBIENT:
                        return self._mode is RuntimeMode.CLOUD_CONVERSATION and not self._closed
                    self._generation += 1
                    generation = self._generation
                    self._local_output.clear()
                    barrier_provider = self._capture_barrier_provider
                    self._activation_capture_barrier = barrier_provider() if barrier_provider is not None else None
                    self._clear_postactivation_queue()
                    self._postactivation_speech_observed = False
                    self._pending_acoustic_ack_generation = (
                        generation if reason in _LISTENING_WAKES and not addressed_text else None
                    )
                    processor = self._context_processor
                    clip_request = (
                        reason in _LISTENING_WAKES and not addressed_text and self._request_transcriber is not None
                    )
                    self._request_clip = [] if clip_request else None
                    self._request_clip_late = [] if clip_request else None
                    if not clip_request:
                        self._request_clip_queued = None
                    set_request_mode = getattr(processor, "set_request_mode", None)
                    if set_request_mode is not None and not clip_request:
                        set_request_mode(True)  # the request is transcribed with the accurate model
                    transcribe_request = (
                        reason in _LISTENING_WAKES
                        and not addressed_text
                        and processor is not None
                        and processor.config.strategy is AmbientContextStrategy.CONTINUOUS_TRANSCRIPT
                    )
                    wake_segment_sequence: int | None = None
                    if transcribe_request and processor is not None:
                        open_at_wake = processor.has_open_segment
                        # The sentence in progress (ending in "Reachy") becomes background context; the next
                        # final local segment is the request, sent as text because Gemini's proactive audio
                        # can ignore a short request heard without the wake word.
                        processor.close_open_segment()
                        self._clear_wake_followup_waiter()
                        self._wake_followup_future = asyncio.get_running_loop().create_future()
                        self._wake_followup_after_sequence = processor.latest_sequence
                        if open_at_wake and reason == "acoustic_wake":
                            # A touch can come mid-sentence: that sentence is context, never the request.
                            wake_segment_sequence = processor.latest_sequence
                    asleep = self._asleep
                    # The audio the wake model fired on: Whisper checks the name in it before Reachy reacts.
                    wake_audio = self._wake_name_audio if reason == "acoustic_wake" else None
                    wake_score = self._wake_score if wake_audio is not None else None
                    self._wake_name_audio = None
                    self._wake_score = None
                    # Without that audio, asleep, an acoustic wake counts once the transcript has the name first.
                    confirm_wake = asleep and transcribe_request and wake_audio is None and reason == "acoustic_wake"
                    if asleep and not confirm_wake and wake_audio is None:
                        self._asleep = False
                self._transition(RuntimeMode.LOCAL_COMMAND, reason)
            # Recording starts now, so the request is kept while the name is checked; the cue waits for it.
            clip_task = (
                asyncio.create_task(
                    self._transcribe_request_clip(
                        generation,
                        lead_in=wake_audio,
                        start_s=self._ANSWER_START_S if reason == "awaited_answer" else self._REQUEST_CLIP_START_S,
                    ),
                    name=f"hybrid-request-clip-{generation}",
                )
                if clip_request
                else None
            )
            # A sure wake model and a voice stood in for the name Whisper missed: Reachy stays still until the
            # request shows it was for him. On 2026-10-29 a TV programme opened two conversations this way.
            trusted_unnamed = False
            if wake_audio is not None:
                if not await self._name_in_wake_audio(wake_audio, asleep=asleep):
                    if not self._trusted_voice_wake(wake_audio, wake_score, asleep=asleep):
                        return await self._drop_unconfirmed_wake(generation, asleep=asleep)
                    trusted_unnamed = True
                with self._lifecycle_lock:
                    self._asleep = False
            if not confirm_wake and not trusted_unnamed:
                self._notify_listening(True)
                listening_notified = True
                await self._invoke_acknowledgement(reason)
            with self._lifecycle_lock:
                closed = self._closed
            if closed:
                raise asyncio.CancelledError
            if reason == "announcement":
                if turn is not None:
                    await self._speak_locally(turn.text, turn.language, generation)
                    if turn.answer is not None:
                        self.expect_answer(turn.answer)
                await self._return_to_ambient(generation, "announcement_spoken")
                return True

            request_sequence: int | None = None  # the transcript-wake segment that holds the request
            request_is_followup = False
            if reason == "wake_word":
                with self._lifecycle_lock:
                    request_sequence = self._transcript_wake_segment_sequence
                    self._transcript_wake_segment_sequence = None
            if reason == "wake_word" and not addressed_text:
                followup_segment, waited_wake_sequence = await self._wait_for_wake_followup()
                if waited_wake_sequence is not None:
                    wake_sequence = max(wake_sequence or 0, waited_wake_sequence)
                request_sequence = None
                if followup_segment is not None:
                    addressed_text = followup_segment.text[: self._MAX_ADDRESSED_TEXT_CHARS]
                    request_sequence = followup_segment.sequence
                    request_is_followup = True
                else:
                    self._record_local_command_outcome(LocalCommandOutcome.FOLLOWUP_TIMEOUT)

            if addressed_text and await self._try_local_command(addressed_text):
                async with self._state_lock:
                    with self._lifecycle_lock:
                        local_action_mode: RuntimeMode = self.mode
                        still_command = (
                            local_action_mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
                        )
                    if still_command:
                        self._clear_postactivation_queue()
                        self._transition(RuntimeMode.LOCAL_AMBIENT, "local_command_completed")
                return True

            # What quick commands may act on: only words addressed to Reachy, never an overheard sentence.
            quick_text = (request_after_wake(addressed_text) or addressed_text) if addressed_text else ""
            segment_quick_text = ""
            name_in_request = False  # the name in the fast transcript or the request clip
            if reason in _LISTENING_WAKES and self._context_processor is not None:
                quick_text = ""
                await self._settle_local_transcript()
                wake_text = self._segment_text(wake_segment_sequence)
                wake_word_found = _WAKE_WORD_TRANSCRIPT_PATTERN.search(wake_text) is not None
                followup_text = ""
                # A segment of a word or two at the wake is the wake word itself (or a fragment of it): the
                # request follows, so wait for it rather than sending the fragment as the request.
                wake_text_is_request = len(wake_text.split()) > self._WAKE_ONLY_MAX_WORDS
                # A short segment without a recognizable name, cut at the wake while the person kept talking,
                # is the name misheard ("Reachy, can..." as "which you can't"): the request is still coming.
                processor = self._context_processor
                cut_mid_request = (
                    not wake_word_found
                    and wake_text_is_request
                    and len(wake_text.split()) <= self._UNNAMED_WAKE_MAX_WORDS
                    and processor is not None
                    and (processor.has_open_segment or processor.latest_sequence > (wake_segment_sequence or -1))
                )
                if cut_mid_request:
                    wake_text_is_request = False
                if not wake_text_is_request or (wake_word_found and not request_after_wake(wake_text)):
                    followup_segment, _ = await self._wait_for_wake_followup()
                    followup_text = followup_segment.text if followup_segment is not None else ""
                else:
                    self._clear_wake_followup_waiter()
                # Send the whole utterance around the wake as the user's turn: it carries the request (and
                # what it refers to) without depending on how local STT spelled the wake word.
                parts = (
                    (wake_text, followup_text)
                    if followup_text
                    else ((wake_text,) if wake_text_is_request or cut_mid_request else ())
                )
                request_text = " ".join(part for part in parts if part).strip()
                if wake_word_found:
                    quick_text = " ".join(part for part in (request_after_wake(wake_text), followup_text) if part)
                elif followup_text and not wake_text_is_request:
                    quick_text = followup_text  # the next sentence after "Reachy" alone
                elif wake_text_is_request:
                    quick_text = request_after_misheard_wake(wake_text)
                if request_text:
                    addressed_text = request_text[: self._MAX_ADDRESSED_TEXT_CHARS]
                    with self._lifecycle_lock:
                        self._pending_acoustic_ack_generation = None
                    if (wake_word_found or followup_text) and clip_task is None:
                        # The request is in the text; drop its queued audio so it is not heard twice.
                        self._clear_postactivation_queue()
                    if wake_segment_sequence is not None:
                        wake_sequence = wake_segment_sequence  # context = what came before this utterance
                logger.info(
                    "Acoustic wake request wake_segment_chars=%d wake_word_found=%s followup_chars=%d",
                    len(wake_text),
                    wake_word_found,
                    len(followup_text),
                )
                clip_text = await self._await_request_clip(clip_task)
                clip_task = None
                name_in_request = wake_word_found or bool(
                    clip_text and _WAKE_WORD_TRANSCRIPT_PATTERN.search(clip_text)
                )
                if clip_text and wake_audio is not None:
                    # The clip opens with the audio the wake model fired on, so the name leads it; the request
                    # is what follows the name (or the whole text when the name was spelled past recognizing).
                    clip_text = request_after_wake(clip_text) or request_after_misheard_wake(clip_text) or clip_text
                if clip_text and quick_text and quick_text.casefold() != clip_text.casefold():
                    segment_quick_text = quick_text
                if clip_text:
                    # One clip of everything said after the name, transcribed accurately, beats the fragments.
                    quick_text = clip_text
                    addressed_text = clip_text[: self._MAX_ADDRESSED_TEXT_CHARS]
                    with self._lifecycle_lock:
                        self._pending_acoustic_ack_generation = None
                    # If it goes to the conversation, Gemini hears the clip's audio rather than this text:
                    # on 2026-10-19 it played music on "Start the music." when the user had said "stop".
                    request_in_audio = self._queue_request_clip_audio()
                elif addressed_text and (wake_word_found or followup_text):
                    self._clear_postactivation_queue()  # the fast transcript's text carries the request
                    if wake_segment_sequence is not None:
                        wake_sequence = wake_segment_sequence
                if confirm_wake:
                    leading_text = wake_text if wake_word_found else followup_text
                    if not _name_leads(leading_text):
                        return await self._drop_unconfirmed_wake(generation, asleep=True)
                    with self._lifecycle_lock:
                        self._asleep = False
                    self._notify_listening(True)
                    listening_notified = True
                    await self._invoke_acknowledgement(reason)
            if reason == "awaited_answer":
                # Whatever was said is the answer; silence means none, and never opens the conversation.
                answer = turn.answer if turn is not None else None
                logger.info("Awaited answer heard=%s", bool(quick_text))
                if quick_text and answer is not None and await self._try_quick_command(quick_text, generation, answer):
                    return True
                await self._return_to_ambient(generation, "no_answer")
                return True
            # Still talking when the clip stopped: commands that need every word leave it to the conversation.
            cut_off = bool(clip_request) and self._request_clip_cut_off
            if quick_text and await self._try_quick_command(quick_text, generation, cut_off=cut_off):
                return True
            # The clip can still miss words the fast transcript caught; try that before the conversation.
            if segment_quick_text and await self._try_quick_command(segment_quick_text, generation, cut_off=cut_off):
                return True
            # A transcript wake heard by whisper-small ("prendela los" for "prende las luces"): listen again
            # with the accurate model before handing a misheard request to the conversation.
            if quick_text and request_sequence is not None:
                accurate = await self._transcribe_segment_accurately(request_sequence)
                accurate_quick = (
                    accurate
                    if request_is_followup
                    else (request_after_wake(accurate) or request_after_misheard_wake(accurate))
                )
                if accurate_quick and accurate_quick.casefold() != quick_text.casefold():
                    if await self._try_quick_command(accurate_quick, generation):
                        return True
                if accurate:
                    addressed_text = accurate[: self._MAX_ADDRESSED_TEXT_CHARS]
            if (
                not quick_text
                and addressed_text
                and self._quick_commands is not None
                and config.QUICK_COMMANDS_LOG_TEXT
            ):
                logger.info("Quick commands skipped (no name heard before the request): %r", addressed_text)
            if trusted_unnamed:
                if not name_in_request:
                    logger.info("Sure wake without the name, and neither a name nor a command followed; dropped")
                    return await self._drop_unconfirmed_wake(generation, asleep=False)
                self._notify_listening(True)
                listening_notified = True
                await self._invoke_acknowledgement(reason)

            ambient_text = await self._context_for_activation(before_sequence=wake_sequence)
            hint = request_audio_hint(addressed_text) if request_in_audio and addressed_text else ""
            # Gemini cuts the context at LOCAL_CONTEXT_MAX_CHARS; keep room for the hint at its end.
            initial_context = frame_activation_context(
                ambient_text, config.LOCAL_CONTEXT_MAX_CHARS - (len(hint) + 2 if hint else 0)
            )
            if hint:
                initial_context = f"{initial_context}\n\n{hint}".strip()

            produced_handler = self._handler_factory()
            handler = await produced_handler if inspect.isawaitable(produced_handler) else produced_handler
            with self._lifecycle_lock:
                closed = self._closed
            if closed:
                raise asyncio.CancelledError

            async with self._state_lock:
                with self._lifecycle_lock:
                    activation_mode: RuntimeMode = self.mode
                    if (
                        self._closed
                        or activation_mode is not RuntimeMode.LOCAL_COMMAND
                        or generation != self._generation
                    ):
                        raise asyncio.CancelledError
                self._configure_handler(handler, generation)
                handler.set_initial_context(initial_context)
                with self._metrics_lock:
                    self._session_audio_bytes_start = self._cloud_audio_bytes_sent
                    self._session_video_frames_start = self._cloud_video_frames_sent
                with self._lifecycle_lock:
                    self._session_queued_frames = 0
                    self._session_ack_state = "not_needed"
                    self._music_wind_down_requested_at = None
                    self._close_requested = None
                    self._reset_addressee_state_locked()
                    self._last_user_turn_at = self._clock()  # the wake request itself is the first turn
                    self._user_turn_started_at = self._last_user_turn_at
                    self._last_user_speech_start_at = -math.inf
                    self._last_reply_was_question = False
                    self._active_handler = handler
                    self._connection_ready = False
                    self._connected_since = None
                    self._last_conversation_activity = self._clock()
                    self._user_speaking = False
                    self._interaction_busy = False
                    self._playback_until = 0.0
                    self._pending_activation_text = (
                        (generation, addressed_text) if addressed_text and not request_in_audio else None
                    )
                self._transition(RuntimeMode.CLOUD_CONVERSATION, "activation_ready")
                self._notify_cloud_session(True)
                provider_task = asyncio.create_task(
                    self._run_provider(handler, generation), name=f"hybrid-provider-{generation}"
                )
                with self._lifecycle_lock:
                    self._provider_task = provider_task
                    # Always monitored: closes Reachy asks for (goodbye, sleep) need it even without timeouts.
                    self._inactivity_task = asyncio.create_task(
                        self._monitor_inactivity(handler, generation),
                        name=f"hybrid-inactivity-{generation}",
                    )

            await asyncio.sleep(0)
            return True
        except asyncio.CancelledError:
            with self._lifecycle_lock:
                active_handler = self._active_handler
            if handler is not None and handler is active_handler:
                await self._deactivate_cloud("activation_cancelled")
            elif handler is not None:
                await self._shutdown_handler_strict(handler, "activation_cancelled")
            with self._lifecycle_lock:
                still_command = self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
            if generation and still_command:
                with self._lifecycle_lock:
                    self._pending_acoustic_ack_generation = None
                    self._postactivation_speech_observed = False
                self._clear_postactivation_queue()
                self._transition(RuntimeMode.LOCAL_AMBIENT, "activation_cancelled")
            raise
        except Exception as error:
            logger.warning("Hybrid cloud activation failed: %s", type(error).__name__)
            with self._lifecycle_lock:
                active_handler = self._active_handler
            if handler is not None and handler is not active_handler:
                await self._contain_uncommitted_handler(handler, "activation_failed", generation)
            with self._lifecycle_lock:
                still_command = self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
            if generation and still_command:
                with self._lifecycle_lock:
                    self._pending_acoustic_ack_generation = None
                    self._postactivation_speech_observed = False
                self._clear_postactivation_queue()
                self._transition(RuntimeMode.LOCAL_AMBIENT, "provider_setup_failed")
            return False
        finally:
            set_request_mode = getattr(self._context_processor, "set_request_mode", None)
            if set_request_mode is not None:
                set_request_mode(False)
            self._request_clip = None
            self._request_clip_queued = None
            self._request_clip_late = None
            if clip_task is not None and not clip_task.done():
                clip_task.cancel()
            with self._lifecycle_lock:
                conversation_open = self._mode is RuntimeMode.CLOUD_CONVERSATION and self._generation == generation
            if listening_notified and not conversation_open:
                self._notify_listening(False)
            with self._lifecycle_lock:
                activation_is_current = self._activation_task is asyncio.current_task()
            if activation_is_current:
                self._clear_wake_followup_waiter()
            with self._lifecycle_lock:
                if self._activation_task is asyncio.current_task():
                    self._activation_task = None

    def _notify_listening(self, listening: bool) -> None:
        observer = self._listening_observer
        if observer is None:
            return

        async def notify() -> None:
            try:
                await observer(listening)
            except Exception as error:
                logger.warning("Listening observer failed: %s", type(error).__name__)

        task = asyncio.create_task(notify(), name=f"hybrid-listening-{listening}")
        self._observer_tasks.add(task)
        task.add_done_callback(self._observer_tasks.discard)

    async def _await_request_clip(self, clip_task: asyncio.Task[str] | None) -> str:
        if clip_task is None:
            return ""
        try:
            return (await clip_task).strip()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Request clip failed: %s", type(error).__name__)
            return ""

    async def _transcribe_request_clip(
        self,
        generation: int,
        *,
        lead_in: tuple[int, NDArray[np.int16]] | None = None,
        start_s: float = _REQUEST_CLIP_START_S,
    ) -> str:
        """Record what is said after an acoustic wake until the speaker stops, and transcribe it as one clip.

        ``lead_in`` is the audio the wake model fired on. It goes in front of the clip for local transcription
        only: the wake fires a moment after the name, so the request's first word ("Richie, lights | out") can
        fall before the clip; one clip keeps that word whole (2026-10-28: the clip alone heard "off"). Gemini
        never gets the lead-in audio.
        """
        transcriber = self._request_transcriber
        self._request_clip_cut_off = False
        if transcriber is None:
            return ""
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        speech_started_at: float | None = None
        silence_s = 0.0
        index = 0
        vad = self._request_clip_vad
        while True:
            await asyncio.sleep(0.05)
            if self._request_clip is None or not self._quick_generation_current(generation):
                return ""
            clip = self._request_clip_frames()
            for frame in clip[index:]:
                try:
                    speech = vad.is_speech(frame)
                except (TypeError, ValueError):
                    speech = False
                duration = self._frame_duration(frame)
                if speech:
                    silence_s = 0.0
                    if speech_started_at is None:
                        speech_started_at = loop.time()
                elif speech_started_at is not None:
                    silence_s += duration
            index = len(clip)
            now = loop.time()
            if speech_started_at is None:
                if now - started_at > start_s:
                    return ""
                continue
            if silence_s >= self._REQUEST_CLIP_SILENCE_S:
                break
            if now - speech_started_at > self._REQUEST_CLIP_MAX_S:
                self._request_clip_cut_off = True
                break
        frames = self._request_clip_frames()
        if not frames:
            return ""
        sample_rate = frames[0][0]
        parts = [audio_to_mono_int16(frame[1]) for frame in frames if frame[0] == sample_rate]
        if lead_in is not None and lead_in[0] == sample_rate and lead_in[1].size:
            parts.insert(0, lead_in[1])
        audio = np.concatenate(parts)
        text = await asyncio.wait_for(transcriber((sample_rate, audio)), self._REQUEST_CLIP_STT_TIMEOUT_S)
        logger.info(
            "Request clip transcribed audio_s=%.1f chars=%d cut_off=%s",
            audio.size / sample_rate,
            len(text.strip()),
            self._request_clip_cut_off,
        )
        if config.QUICK_COMMANDS_LOG_TEXT:
            logger.info("Request clip text: %r", text)
        return text if isinstance(text, str) else ""

    async def _transcribe_segment_accurately(self, sequence: int) -> str:
        """Transcribe one local segment's audio again with the request model ("" when unavailable)."""
        transcriber = self._request_transcriber
        segment_audio = getattr(self._context_processor, "segment_audio", None)
        if transcriber is None or self._quick_commands is None or segment_audio is None:
            return ""
        audio = segment_audio(sequence)
        if audio is None:
            return ""
        try:
            text = await asyncio.wait_for(transcriber(audio), self._REQUEST_CLIP_STT_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Accurate request transcription failed: %s", type(error).__name__)
            return ""
        text = text.strip() if isinstance(text, str) else ""
        if config.QUICK_COMMANDS_LOG_TEXT:
            logger.info("Request heard again accurately: %r", text)
        return text

    def _queue_request_clip_audio(self) -> bool:
        """Put the clip's audio from before the activation barrier ahead of the queued post-wake audio.

        The post-wake queue already holds the rest of the clip and what followed it, so Gemini hears the
        whole request. Returns whether any request audio is queued for Gemini.
        """
        with self._lifecycle_lock:
            early = [*(self._request_clip_queued or ()), *(self._request_clip_late or ())]
            for frame in reversed(early):
                self._postactivation_frames.appendleft((None, frame))
                self._postactivation_seconds += self._frame_duration(frame)
            return bool(self._postactivation_frames)

    def _request_clip_frames(self) -> list[AudioFrame]:
        with self._lifecycle_lock:
            return [
                *(self._request_clip_queued or ()),
                *(self._request_clip_late or ()),
                *(self._request_clip or ()),
            ]

    def _keep_for_request_clip(self, frames: list[AudioFrame], frame: AudioFrame) -> None:
        """Append ``frame`` unless ``frames`` already holds the longest request a clip records."""
        held = sum(self._frame_duration(kept) for kept in frames)
        if held < self._REQUEST_CLIP_START_S + self._REQUEST_CLIP_MAX_S:
            frames.append(frame)

    def _keep_wake_window(self, pcm: bytes, sample_rate: int) -> None:
        """Keep the last ``_WAKE_NAME_WINDOW_S`` of the audio the wake model hears, in memory only."""
        if sample_rate != self._wake_window_rate:
            self._wake_window.clear()
            self._wake_window_rate = sample_rate
        self._wake_window += pcm
        excess = len(self._wake_window) - int(sample_rate * self._WAKE_NAME_WINDOW_S) * 2
        if excess > 0:
            del self._wake_window[:excess]

    async def _name_in_wake_audio(self, audio: tuple[int, NDArray[np.int16]], *, asleep: bool) -> bool:
        """Return whether Whisper hears the name in the audio the wake model fired on.

        The fast model is asked first and the request model only when it did not hear the name. Awake, the
        name anywhere counts; asleep, it must lead a sentence. If no check could run at all (Whisper down),
        an awake Reachy wakes as he did before the check and an asleep one stays asleep.
        """
        transcribers = [
            (label, transcriber)
            for label, transcriber in (("fast", self._name_check_transcriber), ("accurate", self._request_transcriber))
            if transcriber is not None
        ]
        failed = 0
        for label, transcriber in transcribers:
            try:
                text = await asyncio.wait_for(transcriber(audio), self._WAKE_NAME_CHECK_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Wake name check failed model=%s: %s", label, type(error).__name__)
                failed += 1
                continue
            if isinstance(text, str) and (
                _name_leads(text) if asleep else _WAKE_WORD_TRANSCRIPT_PATTERN.search(text) is not None
            ):
                logger.info("Wake name check heard=True model=%s", label)
                return True
        if transcribers and failed == len(transcribers):
            logger.info("Wake name check could not run; %s", "staying asleep" if asleep else "waking anyway")
            return not asleep
        logger.info("Wake name check heard=False")
        return False

    def _trusted_voice_wake(self, audio: tuple[int, NDArray[np.int16]], score: float | None, *, asleep: bool) -> bool:
        """Whether a sure wake model and a voice in the window outweigh Whisper missing the name (awake only)."""
        if asleep or score is None or score < self._TRUSTED_WAKE_SCORE:
            return False
        run_s = _longest_loud_run_s(
            audio, self._VOICE_FRAME_S, over_floor=self._VOICE_OVER_FLOOR, min_rms=self._VOICE_MIN_RMS
        )
        trusted = run_s >= self._VOICE_RUN_S
        logger.info("Wake trusted without the name: %s score=%.2f loud_run=%.2fs", trusted, score, run_s)
        return trusted

    async def _drop_unconfirmed_wake(self, generation: int, *, asleep: bool) -> bool:
        """Drop an acoustic wake whose name Whisper did not hear, without moving or opening the cloud."""
        async with self._state_lock:
            with self._lifecycle_lock:
                if self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation:
                    self._pending_acoustic_ack_generation = None
                    self._clear_wake_followup_waiter()
                    self._clear_postactivation_queue()
                    self._transition(
                        RuntimeMode.LOCAL_AMBIENT, "unconfirmed_wake_while_asleep" if asleep else "unconfirmed_wake"
                    )
        if asleep:
            logger.info("Asleep; staying asleep because the name was not heard first")
        else:
            logger.info("Wake model fired but the name was not heard; still listening")
        return False

    async def _invoke_acknowledgement(self, reason: str) -> None:
        callback = self._acknowledgement_callback
        if callback is None:
            return
        try:
            result = callback(reason)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local activation acknowledgement failed: %s", type(error).__name__)

    async def _try_local_command(self, addressed_text: str) -> bool:
        """Execute one exact local command, returning whether cloud can stay closed."""
        router = self._intent_router
        executor = self._action_executor
        if router is None or executor is None:
            with self._metrics_lock:
                self._local_command_escalations += 1
            return False
        decision = router.route_wake_stripped(addressed_text)
        if decision.action is LocalIntent.STOP and self._quick_commands is not None:
            # The quick "stop" pauses music that plays, and stops moving otherwise; this one only stops
            # moving (2026-10-30: "Richie, stop" left the music playing).
            return False
        if decision.should_escalate:
            with self._metrics_lock:
                self._local_command_escalations += 1
            self._record_local_command_outcome(LocalCommandOutcome.ROUTER_REJECTED, decision.reason)
            return False

        started_at = self._clock()
        result = await executor.execute(decision)
        duration = max(0.0, self._clock() - started_at)
        executed = result.status is LocalActionStatus.EXECUTED
        with self._metrics_lock:
            self._local_command_latency_seconds_total += duration
            self._local_command_latency_seconds_max = max(self._local_command_latency_seconds_max, duration)
            if executed:
                self._local_commands_executed += 1
            else:
                self._local_command_failures += 1
                self._local_command_escalations += 1
        self._record_local_command_outcome(
            LocalCommandOutcome.LOCAL_EXECUTED if executed else LocalCommandOutcome.LOCAL_EXECUTION_FAILED
        )
        return executed

    def _quick_generation_current(self, generation: int) -> bool:
        with self._lifecycle_lock:
            return not self._closed and self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation

    async def _try_quick_command(
        self,
        addressed_text: str,
        generation: int,
        quick: QuickCommandHandler | None = None,
        *,
        cut_off: bool = False,
    ) -> bool:
        """Answer an everyday command locally; returns whether the cloud conversation can stay closed.

        ``addressed_text`` is only what was said to Reachy (after his name, or the next sentence). ``quick``
        replaces the quick commands, e.g. with the handler of an awaited answer. ``cut_off`` means the speaker
        was still talking when the clip stopped: a reply that needs the whole request goes to the conversation,
        which hears all of it (2026-10-28: "...qué tengo en mi agenda para el" would have gone to Maribel).
        """
        quick = quick or self._quick_commands
        if quick is None:
            return False
        request = addressed_text
        started_at = self._clock()
        try:
            reply = await asyncio.wait_for(quick(request), self._QUICK_COMMAND_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            # The action may have half run (TV keys, a skipped song); the conversation would repeat it.
            logger.warning("Quick command timed out; not repeating it in the conversation")
            await self._finish_quick_command(generation, sleep=False)
            return True
        except Exception as error:
            logger.warning("Quick command failed: %s", type(error).__name__)
            return False
        if reply is None:
            if config.QUICK_COMMANDS_LOG_TEXT:
                logger.info("Quick command not recognized; to the conversation: %r", request)
            return False
        kind = str(getattr(reply, "kind", "command"))
        if cut_off and getattr(reply, "whole_request", False):
            logger.info(
                "Quick command kind=%s needs the whole request; the clip was cut off, to the conversation", kind
            )
            return False
        if not self._quick_generation_current(generation):
            return True  # it ran; the activation was superseded meanwhile
        language = str(getattr(reply, "language", "en"))
        logger.info(
            "Quick command kind=%s language=%s handled_ms=%.0f", kind, language, (self._clock() - started_at) * 1000
        )
        with self._lifecycle_lock:
            self._pending_acoustic_ack_generation = None
        self._clear_postactivation_queue()
        self._local_output.append(AdditionalOutputs({"role": "user", "content": request}))
        before = getattr(reply, "before", None)
        if before is not None:
            try:
                before()
            except Exception as error:
                logger.warning("Quick command preparation failed: %s", type(error).__name__)
        await self._speak_locally(getattr(reply, "text", None), language, generation)
        # A countdown: beat k starts beat_s * k after the first (at once if the one before ran long), or
        # beat_pause_s after the one before was heard.
        beat_s = float(getattr(reply, "beat_s", 1.0))
        beat_pause_s = getattr(reply, "beat_pause_s", None)
        on_beat = getattr(reply, "on_beat", None)
        countdown_started_at = self._clock()
        for index, beat in enumerate(getattr(reply, "beats", ()), start=1):
            if not self._quick_generation_current(generation):
                break
            await self._speak_locally(beat, language, generation)
            if on_beat is not None:
                try:
                    on_beat(index - 1)
                except Exception as error:
                    logger.warning("Quick command beat hook failed: %s", type(error).__name__)
            if beat_pause_s is not None:
                await asyncio.sleep(float(beat_pause_s))
            else:
                await asyncio.sleep(max(0.0, countdown_started_at + index * beat_s - self._clock()))
        after = getattr(reply, "after", None)
        if after is not None and self._quick_generation_current(generation):
            after_timeout_s = getattr(reply, "after_timeout_s", None) or self._QUICK_COMMAND_TIMEOUT_S
            try:
                follow_up = await asyncio.wait_for(after(), after_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Quick command follow-up failed: %s", type(error).__name__)
                follow_up = None
            await self._speak_locally(follow_up, language, generation)
        await self._finish_quick_command(generation, sleep=bool(getattr(reply, "sleep", False)))
        logger.info("Quick command kind=%s done_ms=%.0f", kind, (self._clock() - started_at) * 1000)
        return True

    async def _finish_quick_command(self, generation: int, *, sleep: bool) -> None:
        """Return to listening for the wake word after a quick command."""
        if sleep:
            with self._lifecycle_lock:
                if self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation:
                    self._asleep = True
        await self._return_to_ambient(generation, "quick_command_completed")
        with self._metrics_lock:
            self._local_commands_executed += 1

    async def _return_to_ambient(self, generation: int, reason: str) -> None:
        """Listen for the wake word again, unless this generation was already superseded."""
        async with self._state_lock:
            with self._lifecycle_lock:
                still_command = self._mode is RuntimeMode.LOCAL_COMMAND and self._generation == generation
            if still_command:
                self._clear_postactivation_queue()
                self._transition(RuntimeMode.LOCAL_AMBIENT, reason)

    async def record_message(self, touches: Callable[[], int] | None = None) -> tuple[int, NDArray[np.int16]] | None:
        """During a quick command's follow-up: beep, then record until the speaker stops; None if nobody spoke.

        The message stays in memory; it ends after ``_MESSAGE_SILENCE_S`` of silence, at ``_MESSAGE_MAX_S``,
        or once ``touches`` (a count of touches on his head) grows after the speaker has started.
        """
        with self._lifecycle_lock:
            generation = self._generation
        speaker_ready = self._reply_speaker is not None
        if not speaker_ready or not self._quick_generation_current(generation):
            return None

        async def beep_audio() -> AsyncIterator[tuple[int, NDArray[np.int16]]]:
            yield SPEECH_SAMPLE_RATE, beep()

        await self._queue_reply_audio(beep_audio(), generation, self._clock())
        with self._lifecycle_lock:
            self._clear_postactivation_queue()
            self._request_clip = []
            self._recording_message = True
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        speech_started_at: float | None = None
        speech_start_s = 0.0  # audio before the first speech
        heard_s = 0.0
        silence_s = 0.0
        index = 0
        touched = False
        touches_at_speech: int | None = None  # touches before anyone spoke do not end it
        try:
            while True:
                await asyncio.sleep(0.05)
                if not self._quick_generation_current(generation):
                    return None
                with self._lifecycle_lock:
                    clip = list(self._request_clip or ())
                for frame in clip[index:]:
                    try:
                        speech = self._request_clip_vad.is_speech(frame)
                    except (TypeError, ValueError):
                        speech = False
                    duration = self._frame_duration(frame)
                    if speech:
                        silence_s = 0.0
                        if speech_started_at is None:
                            speech_started_at = loop.time()
                            speech_start_s = heard_s
                    elif speech_started_at is not None:
                        silence_s += duration
                    heard_s += duration
                index = len(clip)
                now = loop.time()
                if speech_started_at is None:
                    if now - started_at > self._MESSAGE_START_S:
                        logger.info("Voice message: nothing said after the beep")
                        return None
                    continue
                if touches is not None:
                    count = self._read_touches(touches)
                    if touches_at_speech is None:
                        touches_at_speech = count
                    elif count > touches_at_speech:
                        touched = True
                        break
                if silence_s >= self._MESSAGE_SILENCE_S or now - speech_started_at > self._MESSAGE_MAX_S:
                    break
        finally:
            with self._lifecycle_lock:
                self._recording_message = False
                self._request_clip = None
        sample_rate = clip[0][0]
        audio = np.concatenate([audio_to_mono_int16(frame[1]) for frame in clip if frame[0] == sample_rate])
        first = max(0, int((speech_start_s - self._MESSAGE_MARGIN_S) * sample_rate))
        trailing_s = max(0.0, silence_s - self._MESSAGE_MARGIN_S)
        if touched:
            trailing_s = max(trailing_s, self._MESSAGE_TOUCH_TRIM_S)
        last = audio.size - int(trailing_s * sample_rate)
        audio = audio[first:last]
        ended = "touch" if touched else "silence" if silence_s >= self._MESSAGE_SILENCE_S else "length cap"
        if touched and audio.size < self._MESSAGE_MIN_S * sample_rate:
            logger.info("Voice message: only the touch was heard; nothing to send")
            return None
        logger.info("Voice message recorded seconds=%.1f ended=%s", audio.size / sample_rate, ended)
        return sample_rate, audio

    @staticmethod
    def _read_touches(touches: Callable[[], int]) -> int:
        try:
            return int(touches())
        except Exception as error:
            logger.debug("Head touches unavailable: %s", type(error).__name__)
            return 0

    def expect_answer(self, answer: QuickCommandHandler) -> None:
        """Once the current reply has played, listen once without the name and hand what is said to ``answer``.

        Thread-safe. Silence for ``_ANSWER_START_S`` ends it without opening the conversation.
        """
        self._queue_turn(_PendingTurn("awaited_answer", self._clock() + self._ANSWER_PENDING_S, answer=answer))

    def request_announcement(self, text: str, language: str, *, answer: QuickCommandHandler | None = None) -> None:
        """Say ``text`` once Reachy is free (no conversation or command), then await ``answer`` if given.

        Thread-safe; dropped if he stays busy for ``_ANNOUNCEMENT_PENDING_S``.
        """
        self._queue_turn(
            _PendingTurn(
                "announcement",
                self._clock() + self._ANNOUNCEMENT_PENDING_S,
                text=text,
                language=language,
                answer=answer,
            )
        )

    def _queue_turn(self, turn: _PendingTurn) -> None:
        with self._lifecycle_lock:
            loop = self._owner_loop
            closed = self._closed
        if loop is None or closed or loop.is_closed():
            logger.info("Pending %s dropped: runtime not running", turn.reason)
            return

        def queue() -> None:
            if turn.reason == "awaited_answer":
                self._pending_turns.appendleft(turn)  # it belongs to the reply being spoken now
            else:
                self._pending_turns.append(turn)
            if self._pending_turns_task is None or self._pending_turns_task.done():
                task = asyncio.create_task(self._run_pending_turns(), name="hybrid-pending-turns")
                self._pending_turns_task = task
                self._local_event_tasks.add(task)
                task.add_done_callback(self._local_event_tasks.discard)

        try:
            loop.call_soon_threadsafe(queue)
        except RuntimeError:  # the loop closed between the check and the call
            logger.debug("Pending %s dropped: runtime loop closed", turn.reason)

    async def _run_pending_turns(self) -> bool:
        """Start each pending turn as its own activation once Reachy is free; returns whether any ran."""
        ran = False
        while self._pending_turns:
            turn = self._pending_turns[0]
            if self._clock() > turn.expires_at:
                self._pending_turns.popleft()
                logger.info("Pending %s dropped: Reachy stayed busy", turn.reason)
                continue
            with self._lifecycle_lock:
                closed = self._closed
                busy = self._mode is not RuntimeMode.LOCAL_AMBIENT or (
                    self._activation_task is not None and not self._activation_task.done()
                )
            if closed:
                self._pending_turns.clear()
                break
            if busy:
                await asyncio.sleep(self._PENDING_TURN_POLL_S)
                continue
            self._pending_turns.popleft()
            self._next_turn = turn
            await self.activate(turn.reason)
            ran = True
        return ran

    async def _speak_locally(self, text: object, language: str, generation: int) -> None:
        """Queue a spoken reply for playback and wait until it has been heard."""
        speaker = self._reply_speaker
        if not isinstance(text, str) or not text.strip() or speaker is None:
            return
        self._local_output.append(AdditionalOutputs({"role": "assistant", "content": text}))
        started_at = self._clock()
        try:
            async with asyncio.timeout(self._QUICK_REPLY_BASE_S + len(text) * self._QUICK_REPLY_PER_CHAR_S):
                await self._queue_reply_audio(speaker(text, language), generation, started_at)
        except TimeoutError:
            logger.warning("Quick reply speech took too long; listening again")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Quick reply speech failed: %s", type(error).__name__)

    async def _say_cannot_connect(self, request: str) -> None:
        """Tell the room Reachy could not reach the cloud, in the request's language, then keep listening."""
        speaker = self._reply_speaker
        if speaker is None:
            return
        from reachy_mini_conversation_app.quick_commands import CANNOT_CONNECT, guess_language

        language = guess_language(request, default="es")
        text = CANNOT_CONNECT.get(language, CANNOT_CONNECT["en"])
        with self._lifecycle_lock:
            generation = self._generation

        def still_listening() -> bool:
            with self._lifecycle_lock:
                return not self._closed and self._mode is RuntimeMode.LOCAL_AMBIENT and self._generation == generation

        logger.info("Cloud conversation unreachable; telling the room (language=%s)", language)
        self._local_output.append(AdditionalOutputs({"role": "assistant", "content": text}))
        try:
            async with asyncio.timeout(self._QUICK_REPLY_BASE_S + len(text) * self._QUICK_REPLY_PER_CHAR_S):
                await self._queue_reply_audio(speaker(text, language), generation, self._clock(), still_listening)
        except TimeoutError:
            logger.warning("Cannot-connect speech took too long")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Cannot-connect speech failed: %s", type(error).__name__)

    async def _queue_reply_audio(
        self,
        stream: AsyncIterator[tuple[int, np.ndarray]],
        generation: int,
        started_at: float,
        still_current: Callable[[], bool] | None = None,
    ) -> None:
        """Queue reply audio as it arrives, then wait until it has played."""
        if still_current is None:

            def still_current() -> bool:
                return self._quick_generation_current(generation)

        queued_s = 0.0
        speaking = False
        try:
            async with aclosing(stream) as pieces:  # type: ignore[type-var]
                async for sample_rate, frame in pieces:
                    if not still_current():
                        break
                    if not speaking:
                        started_at = self._clock()  # playback starts with the first piece
                        if self._speaking_callback is not None:
                            self._speaking_callback(True)
                    speaking = True
                    self._local_output.append((sample_rate, frame))
                    self._output_wakeup_event.set()
                    queued_s += frame.shape[-1] / sample_rate
            # Stay in the command state (no new wake from Reachy's own voice) until the reply has played.
            remaining = started_at + queued_s + self._QUICK_REPLY_TAIL_S - self._clock()
            if remaining > 0:
                await asyncio.sleep(remaining)
        finally:
            if speaking and self._speaking_callback is not None:
                self._speaking_callback(False)

    def _record_local_command_outcome(
        self,
        outcome: LocalCommandOutcome,
        router_reason: IntentDecisionReason | None = None,
    ) -> None:
        """Record local routing metadata without retaining addressed text."""
        with self._metrics_lock:
            self._local_command_last_outcome = outcome
            self._local_command_last_router_reason = router_reason
            if outcome is LocalCommandOutcome.ROUTER_REJECTED:
                self._local_command_router_rejections += 1
            elif outcome is LocalCommandOutcome.FOLLOWUP_TIMEOUT:
                self._local_command_followup_timeouts += 1

    def _segment_text(self, sequence: int | None) -> str:
        """Return the final local transcript text of one segment, or "" if it has none."""
        processor = self._context_processor
        if processor is None or sequence is None:
            return ""
        for segment in processor.transcript.segments:
            if segment.sequence == sequence:
                return segment.text.strip()
        return ""

    async def _settle_local_transcript(self) -> None:
        """Wait, within a short bound, for the sentence closed at an acoustic wake to finish transcribing.

        Activation closes the open segment (ending in "Reachy") so it can become background context; this
        waits for local STT without closing the request segment that may already be in progress.
        """
        processor = self._context_processor
        if processor is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(processor.drain()), self._ACOUSTIC_CONTEXT_SETTLE_S)
        except asyncio.TimeoutError:
            logger.info(
                "Local transcript not ready within %.1f s of the wake; activating without it",
                self._ACOUSTIC_CONTEXT_SETTLE_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local transcript settle failed: %s", type(error).__name__)

    async def _context_for_activation(self, *, before_sequence: int | None = None) -> str:
        """Build bounded text context while keeping the ambient PCM snapshot local."""
        processor = self._context_processor
        if processor is None:
            return ""
        snapshot = self._ambient_buffer.snapshot()
        started_at = self._clock()
        try:
            context = await processor.context_for_activation(snapshot, before_sequence=before_sequence)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local activation context failed: %s", type(error).__name__)
            return ""
        finally:
            duration = max(0.0, self._clock() - started_at)
            with self._metrics_lock:
                self._context_activation_latency_seconds_total += duration
                self._context_activation_latency_seconds_max = max(
                    self._context_activation_latency_seconds_max,
                    duration,
                )
        with self._metrics_lock:
            self._local_context_chars = len(context.text)
        return context.text

    def _configure_handler(self, handler: ConversationHandler, generation: int) -> None:
        playback_clearer = self._playback_clearer
        if playback_clearer is not None:
            handler._clear_queue = lambda: self._clear_handler_playback(handler, playback_clearer)
        handler.set_activity_observer(lambda reason: self._on_handler_activity(handler, generation, reason))
        handler.set_transcript_observer(
            lambda role, text, final: self._on_handler_transcript(handler, generation, role, text, final)
        )
        handler.set_traffic_observer(
            lambda kind, byte_count: self._on_handler_traffic(handler, generation, kind, byte_count)
        )
        handler.set_connection_observer(lambda connected: self._on_handler_connection(handler, generation, connected))
        busy_setter = getattr(handler, "set_interaction_busy_observer", None)
        if callable(busy_setter):
            busy_setter(lambda busy: self._on_handler_interaction_busy(handler, generation, busy))
        handler.set_startup_greeting_enabled(False)

    def _clear_handler_playback(self, handler: ConversationHandler, clearer: Callable[[], None]) -> None:
        """Flush provider output and delegate robot playback flushing to LocalStream."""
        self._drain_handler_output(handler)
        try:
            clearer()
        except Exception as error:
            logger.warning("Local playback clearer failed: %s", type(error).__name__)

    def _is_current_handler(self, handler: ConversationHandler, generation: int) -> bool:
        with self._lifecycle_lock:
            return (
                self._mode is RuntimeMode.CLOUD_CONVERSATION
                and self._active_handler is handler
                and self._generation == generation
            )

    def _on_handler_activity(self, handler: ConversationHandler, generation: int, reason: str) -> None:
        if not self._is_current_handler(handler, generation):
            return
        with self._lifecycle_lock:
            if reason in self._SPEECH_STARTED_REASONS:
                self._user_speaking = True
                self._last_user_speech_start_at = self._clock()
            elif reason in self._SPEECH_STOPPED_REASONS:
                self._user_speaking = False
            self._last_conversation_activity = self._clock()
        observer = self._activity_observer
        if observer is not None:
            try:
                observer(reason)
            except Exception as error:
                logger.warning("Runtime activity observer failed: %s", type(error).__name__)

    def _on_handler_transcript(
        self,
        handler: ConversationHandler,
        generation: int,
        role: str,
        text: str,
        final: bool,
    ) -> None:
        if not self._is_current_handler(handler, generation):
            return
        if role == "user" and text.strip():
            # Partial transcripts count: the user is mid-sentence long before Gemini finalizes the turn.
            with self._lifecycle_lock:
                now = self._clock()
                self._note_user_turn_locked(now)
                replied_at = self._replied_at_locked()
                if replied_at > self._unanswered_reply_marker:
                    # First speech since Reachy's latest reply: a new stretch for the addressee check.
                    self._unanswered_reply_marker = replied_at
                    self._unanswered_parts = []
                    self._addressee_checked_words = 0
                    self._addressee_checks = 0
                self._unanswered_parts = _append_capped(
                    self._unanswered_parts, text.strip(), self._ADDRESSEE_TEXT_CHARS
                )
        elif role == "assistant" and text.strip():
            with self._lifecycle_lock:
                self._last_reply_was_question = text.rstrip().endswith(("?", "？"))
                now = self._clock()
                if self._last_assistant_text_at < self._last_user_turn_at:
                    self._reply_parts = []  # a new reply after the user spoke
                self._last_assistant_text_at = now
                self._reply_parts = _append_capped(self._reply_parts, text.strip(), self._ADDRESSEE_TEXT_CHARS)
        observer = self._transcript_observer
        if observer is not None:
            try:
                observer(role, text, final)
            except Exception as error:
                logger.warning("Runtime transcript observer failed: %s", type(error).__name__)

    def _on_handler_traffic(
        self,
        handler: ConversationHandler,
        generation: int,
        kind: TrafficKind,
        byte_count: int,
    ) -> None:
        if self._is_current_handler(handler, generation):
            self.record_provider_traffic(kind, byte_count)

    def _on_handler_interaction_busy(self, handler: ConversationHandler, generation: int, busy: bool) -> None:
        """Track provider busy state without changing the inactivity clock."""
        if not self._is_current_handler(handler, generation):
            return
        with self._lifecycle_lock:
            self._interaction_busy = bool(busy)

    def _on_handler_connection(self, handler: ConversationHandler, generation: int, connected: bool) -> None:
        if not self._is_current_handler(handler, generation):
            return
        now = self._clock()
        if connected:
            with self._lifecycle_lock:
                if self._connected_since is None:
                    self._connected_since = now
                self._connection_ready = True
                self._last_conversation_activity = now
                self._interaction_busy = False
                should_flush = bool(self._postactivation_frames)
                pending_activation_text = self._pending_activation_text
                pending_acoustic_ack = (
                    self._pending_acoustic_ack_generation == generation and self._acoustic_ack_task is None
                )
                if pending_activation_text is not None and pending_activation_text[0] == generation:
                    self._pending_activation_text = None
                    pending_acoustic_ack = False
                else:
                    pending_activation_text = None
            if pending_activation_text is not None:
                task = asyncio.create_task(
                    self._send_activation_text(handler, generation, pending_activation_text[1]),
                    name=f"hybrid-activation-text-{generation}",
                )
                with self._lifecycle_lock:
                    self._activation_text_task = task
            elif pending_acoustic_ack:
                task = asyncio.create_task(
                    self._send_acoustic_ack(handler, generation),
                    name=f"hybrid-acoustic-ack-{generation}",
                )
                with self._lifecycle_lock:
                    self._acoustic_ack_task = task
            elif should_flush:
                self._schedule_postactivation_flush(handler, generation)
            return
        with self._lifecycle_lock:
            self._connection_ready = False
            self._interaction_busy = False
            self._user_speaking = False
            connected_since = self._connected_since
            self._connected_since = None
        if connected_since is not None:
            self._record_session_segment(now - connected_since)

    async def _send_activation_text(self, handler: ConversationHandler, generation: int, text: str) -> None:
        """Submit the locally transcribed addressed turn after provider readiness."""
        current_task = asyncio.current_task()
        try:
            async with self._provider_call_lock:
                if not self._is_current_handler(handler, generation):
                    return
                try:
                    await handler.say(text)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("Hybrid activation text send failed: %s", type(error).__name__)
                if self._is_current_handler(handler, generation):
                    await self._flush_postactivation_locked(handler, generation)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lifecycle_lock:
                if self._activation_text_task is current_task:
                    self._activation_text_task = None

    async def _send_acoustic_ack(self, handler: ConversationHandler, generation: int) -> None:
        """Acknowledge a bare acoustic wake unless speech already resumed."""
        current_task = asyncio.current_task()
        try:
            async with self._provider_call_lock:
                with self._lifecycle_lock:
                    connection_ready = self._connection_ready
                if not self._is_current_handler(handler, generation) or not connection_ready:
                    with self._lifecycle_lock:
                        self._session_ack_state = "skipped_not_ready"
                    return
                with self._lifecycle_lock:
                    speech_resumed = self._user_speaking or self._postactivation_speech_observed
                    self._session_ack_state = "skipped_speech_resumed" if speech_resumed else "sending"
                if not speech_resumed:
                    try:
                        await handler.say(self._ACOUSTIC_WAKE_ACK_TEXT)
                        with self._lifecycle_lock:
                            self._session_ack_state = "sent"
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        with self._lifecycle_lock:
                            self._session_ack_state = "failed"
                        logger.warning("Acoustic wake acknowledgement failed: %s", type(error).__name__)
                if self._is_current_handler(handler, generation):
                    await self._flush_postactivation_locked(handler, generation)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lifecycle_lock:
                if self._acoustic_ack_task is current_task:
                    self._acoustic_ack_task = None
                if self._pending_acoustic_ack_generation == generation:
                    self._pending_acoustic_ack_generation = None

    def _schedule_postactivation_flush(self, handler: ConversationHandler, generation: int) -> None:
        with self._lifecycle_lock:
            task = self._postactivation_flush_task
            if task is not None and not task.done():
                return
            task = asyncio.create_task(
                self._flush_postactivation(handler, generation),
                name=f"hybrid-postactivation-{generation}",
            )
            self._postactivation_flush_task = task

    async def _flush_postactivation(self, handler: ConversationHandler, generation: int) -> None:
        current_task = asyncio.current_task()
        try:
            async with self._provider_call_lock:
                await self._flush_postactivation_locked(handler, generation)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lifecycle_lock:
                if self._postactivation_flush_task is current_task:
                    self._postactivation_flush_task = None

    async def _flush_postactivation_locked(self, handler: ConversationHandler, generation: int) -> None:
        while True:
            with self._lifecycle_lock:
                if not self._is_current_handler(handler, generation) or not self._connection_ready:
                    return
                if not self._postactivation_frames:
                    return
                _, queued_frame = self._postactivation_frames.popleft()
                self._postactivation_seconds -= self._frame_duration(queued_frame)
            try:
                await handler.receive(queued_frame)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Hybrid queued provider receive failed: %s", type(error).__name__)
                self._clear_postactivation_queue()
                await self._deactivate_cloud("provider_receive_failed")
                return

    def _observe_acoustic_ack_frame(self, frame: AudioFrame, generation: int) -> None:
        """Record local speech evidence while a bare acoustic acknowledgment is pending."""
        with self._lifecycle_lock:
            if self._pending_acoustic_ack_generation != generation or self._closed:
                return
        try:
            is_speech = self._acoustic_ack_vad.is_speech(frame)
        except (TypeError, ValueError) as error:
            logger.warning("Acoustic acknowledgment VAD rejected a frame: %s", type(error).__name__)
            return
        if is_speech:
            with self._lifecycle_lock:
                if self._pending_acoustic_ack_generation == generation and not self._closed:
                    self._postactivation_speech_observed = True

    def _enqueue_postactivation(self, frame: AudioFrame, capture_sequence: int | None) -> None:
        duration = self._frame_duration(frame)
        if duration <= 0:
            return
        with self._lifecycle_lock:
            if self._request_clip is not None:
                self._request_clip.append(frame)
            if self._recording_message:
                return  # a minute of message would overflow the queue Gemini never reads here
            self._postactivation_frames.append((capture_sequence, frame))
            self._postactivation_seconds += duration
            self._session_queued_frames += 1
            while self._postactivation_seconds > self._POSTACTIVATION_BUFFER_SECONDS:
                _, oldest = self._postactivation_frames.popleft()
                self._postactivation_seconds -= self._frame_duration(oldest)
                self._postactivation_frames_dropped += 1
                self._postactivation_drop_burst += 1
                # On 2026-10-29 a 90 s head test logged this 6,912 times; once per filling is enough.
                if self._postactivation_drop_burst == 1:
                    logger.warning(
                        "Postactivation audio queue full; dropping the oldest frames count=%d",
                        self._postactivation_frames_dropped,
                    )

    def _clear_postactivation_queue(self) -> None:
        with self._lifecycle_lock:
            self._postactivation_frames.clear()
            self._postactivation_seconds = 0.0
            dropped, self._postactivation_drop_burst = self._postactivation_drop_burst, 0
        if dropped > 1:
            logger.info("Postactivation audio queue dropped %d frames while full", dropped)

    @staticmethod
    def _frame_duration(frame: AudioFrame) -> float:
        sample_rate, audio = frame
        if sample_rate <= 0 or audio.size == 0:
            return 0.0
        if audio.ndim == 1:
            sample_count = int(audio.shape[0])
        elif audio.ndim == 2:
            if audio.shape[1] == 1:
                sample_count = int(audio.shape[0])
            elif audio.shape[0] == 1:
                sample_count = int(audio.shape[0])
            else:
                sample_count = max(int(dimension) for dimension in audio.shape)
        else:
            return 0.0
        return sample_count / sample_rate

    async def _run_provider(self, handler: ConversationHandler, generation: int) -> None:
        try:
            await handler.start_up()
        except asyncio.CancelledError:
            with self._lifecycle_lock:
                closed = self._closed
            if not closed and self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_cancelled")
            raise
        except Exception as error:
            # Provider errors carry a status code and reason (never conversation content); keep them bounded.
            detail = " ".join(str(error).split())[: self._PROVIDER_ERROR_DETAIL_CHARS]
            logger.warning("Hybrid provider task failed: %s %s", type(error).__name__, detail)
            if self._is_current_handler(handler, generation):
                with self._lifecycle_lock:
                    never_connected = not self._connection_ready
                    pending = self._pending_activation_text
                await self._deactivate_cloud("provider_failed")
                if never_connected:
                    # Without this the room hears nothing at all after asking (no credits, no internet).
                    await self._say_cannot_connect(pending[1] if pending is not None else "")
        else:
            if self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_stopped")

    async def handle_frame(
        self,
        frame: AudioFrame,
        *,
        capture_sequence: int | None = None,
        wait_for_local_processing: bool = True,
    ) -> None:
        """Route one captured frame locally or to the active cloud generation."""
        self._assert_owner_loop()
        copied_frame = self._copy_frame(frame)
        local_frame = False
        postactivation_frame = False
        observe_postactivation_locally = False
        observe_acoustic_ack_speech = False
        handler: ConversationHandler | None = None
        generation = 0
        async with self._state_lock:
            with self._lifecycle_lock:
                active_handler = self._active_handler
                mode = self._mode
                barrier = self._activation_capture_barrier
                generation = self._generation
                if self._closed or mode is RuntimeMode.STOPPING:
                    return
            if mode is RuntimeMode.CLOUD_CONVERSATION and active_handler is not None:
                if barrier is not None and capture_sequence is not None and capture_sequence <= barrier:
                    local_frame = True
                    try:
                        self._ambient_buffer.append(copied_frame)
                    except (TypeError, ValueError) as error:
                        logger.warning("Ambient frame rejected: %s", type(error).__name__)
                else:
                    handler = active_handler
                    observe_acoustic_ack_speech = self._pending_acoustic_ack_generation == generation
            elif mode is RuntimeMode.LOCAL_COMMAND:
                with self._lifecycle_lock:
                    observe_postactivation_locally = (
                        self._wake_followup_future is not None and not self._wake_followup_future.done()
                    )
                    observe_acoustic_ack_speech = self._pending_acoustic_ack_generation == generation
                if barrier is not None and capture_sequence is not None and capture_sequence > barrier:
                    postactivation_frame = True
                else:
                    local_frame = True
                    with self._lifecycle_lock:
                        if self._request_clip_late is not None:
                            self._keep_for_request_clip(self._request_clip_late, copied_frame)
                    try:
                        self._ambient_buffer.append(copied_frame)
                    except (TypeError, ValueError) as error:
                        logger.warning("Ambient frame rejected: %s", type(error).__name__)
            else:
                local_frame = True
                try:
                    self._ambient_buffer.append(copied_frame)
                except (TypeError, ValueError) as error:
                    logger.warning("Ambient frame rejected: %s", type(error).__name__)

        if local_frame:
            await self._enqueue_local_frame(copied_frame, wait_for_completion=wait_for_local_processing)
            return
        if postactivation_frame:
            self._enqueue_postactivation(copied_frame, capture_sequence)
            if observe_acoustic_ack_speech:
                self._observe_acoustic_ack_frame(copied_frame, generation)
            if observe_postactivation_locally:
                await self._enqueue_local_frame(copied_frame, wait_for_completion=wait_for_local_processing)
            return
        if handler is None:
            return

        with self._lifecycle_lock:
            activation_text_task = self._activation_text_task
            acoustic_ack_task = self._acoustic_ack_task
            queue_behind_provider_turn = (
                (
                    (activation_text_task is not None and not activation_text_task.done())
                    or (acoustic_ack_task is not None and not acoustic_ack_task.done())
                )
                and self._active_handler is handler
                and self._generation == generation
            )
        if observe_acoustic_ack_speech:
            self._observe_acoustic_ack_frame(copied_frame, generation)

        if queue_behind_provider_turn:
            self._enqueue_postactivation(copied_frame, capture_sequence)
            return

        async with self._provider_call_lock:
            if not self._is_current_handler(handler, generation):
                return
            with self._lifecycle_lock:
                connection_ready = self._connection_ready
            if not connection_ready:
                self._enqueue_postactivation(copied_frame, capture_sequence)
                return
            await self._flush_postactivation_locked(handler, generation)
            if not self._is_current_handler(handler, generation):
                return
            with self._lifecycle_lock:
                closing = self._close_requested is not None
            if closing:
                # He has said goodbye: the room answering "bye!" would start another goodbye (2026-10-19 he
                # said goodbye over and over). Gemini hears silence until the conversation closes.
                copied_frame = (copied_frame[0], np.zeros_like(copied_frame[1]))
            try:
                await handler.receive(copied_frame)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Hybrid provider receive failed: %s", type(error).__name__)
                await self._deactivate_cloud("provider_receive_failed")

    async def _enqueue_local_frame(self, frame: AudioFrame, *, wait_for_completion: bool) -> None:
        """Queue one copied frame for ordered local processing without blocking capture."""
        with self._lifecycle_lock:
            if self._closed or self._mode is RuntimeMode.STOPPING:
                return
            queue = self._local_processing_queue
            task = self._local_processing_task
            if queue is None:
                queue = asyncio.Queue(maxsize=self._LOCAL_PROCESSING_QUEUE_SIZE)
                self._local_processing_queue = queue
            if task is None or task.done():
                self._local_processing_task = asyncio.create_task(
                    self._run_local_processing(queue), name="hybrid-local-processing"
                )
        completed = asyncio.get_running_loop().create_future()
        try:
            queue.put_nowait((frame, completed, self._acoustic_epoch))
        except asyncio.QueueFull:
            # The wake detector keeps its stream state across a dropped frame: a short gap barely changes
            # its score, while a reset would blind it for ~1.3 s and miss a wake word spoken meanwhile.
            completed.cancel()
            with self._metrics_lock:
                self._local_processing_frames_dropped += 1
                dropped_frames = self._local_processing_frames_dropped
                burst_start = not self._local_processing_drop_burst
                self._local_processing_drop_burst = True
            if burst_start:
                logger.warning("Local processing queue full; dropped_frames=%d", dropped_frames)
            return
        with self._metrics_lock:
            self._local_processing_drop_burst = False
        if wait_for_completion:
            await asyncio.shield(completed)

    async def _run_local_processing(self, queue: asyncio.Queue[LocalProcessingItem]) -> None:
        """Run local observation sequentially so VAD/STT retains capture order."""
        while True:
            items = [await queue.get()]
            # Take any backlog in one pass: each frame costs two thread hops (wake model and VAD), and on a
            # loaded Mac that overhead alone exceeds real time, so per-frame processing never catches up.
            while len(items) < self._LOCAL_PROCESSING_BATCH_FRAMES and not queue.empty():
                items.append(queue.get_nowait())
            try:
                for frame, acoustic_epoch, completions in _merge_local_items(items):
                    with self._lifecycle_lock:
                        queued = self._request_clip_queued
                        if queued is not None and acoustic_epoch == self._request_clip_queued_epoch:
                            self._keep_for_request_clip(queued, frame)
                    try:
                        await self._observe_acoustic_frame(frame, acoustic_epoch)
                        result = self._local_processor.observe_frame(frame)
                        if inspect.isawaitable(result):
                            await result
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        logger.warning("Local ambient processing failed: %s", type(error).__name__)
                    for completed in completions:
                        if not completed.done():
                            completed.set_result(None)
            except asyncio.CancelledError:
                for _, completed, _ in items:
                    if not completed.done():
                        completed.cancel()
                raise
            finally:
                for _ in items:
                    queue.task_done()

    async def _observe_acoustic_frame(self, frame: AudioFrame, epoch: int) -> None:
        detector = self._acoustic_wake_detector
        if detector is None or self._closed or self._mode is not RuntimeMode.LOCAL_AMBIENT:
            return
        if epoch != self._acoustic_epoch:
            return
        try:
            if self._acoustic_stream_epoch != epoch:
                await asyncio.to_thread(detector.reset)
                if self._wake_clips is not None:
                    self._wake_clips.clear()
                self._wake_window.clear()
                self._acoustic_stream_epoch = epoch
            pcm = audio_to_mono_int16(frame[1]).astype("<i2", copy=False).tobytes()
            self._keep_wake_window(pcm, frame[0])
            if self._wake_clips is not None:
                self._wake_clips.feed(pcm, frame[0])
            detection = await asyncio.to_thread(detector.process_audio, pcm, frame[0])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._acoustic_wake_detector = None
            logger.warning(
                "Acoustic wake disabled after inference failure: %s; manual activation remains available",
                type(error).__name__,
            )
            return
        if (
            detection is not None
            and not self._closed
            and self._mode is RuntimeMode.LOCAL_AMBIENT
            and epoch == self._acoustic_epoch
        ):
            if self._request_transcriber is not None:
                with self._lifecycle_lock:
                    self._request_clip_queued = []
                    self._request_clip_queued_epoch = epoch
                    window = np.frombuffer(bytes(self._wake_window), dtype="<i2").copy()
                    self._wake_name_audio = (self._wake_window_rate, window) if window.size else None
                    self._wake_score = detection.score
            # Activate in its own task: awaiting it here would stall this worker, so frames after the wake
            # (the spoken request) would not reach local STT until activation finished.
            task = asyncio.create_task(self.activate("acoustic_wake"), name="hybrid-acoustic-activation")
            self._local_event_tasks.add(task)
            task.add_done_callback(self._local_event_tasks.discard)

    async def _stop_local_processing(self) -> None:
        """Cancel the local worker and release queued frame waiters during close."""
        with self._lifecycle_lock:
            task = self._local_processing_task
            queue = self._local_processing_queue
            self._local_processing_task = None
            self._local_processing_queue = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            completed_tasks, _ = await asyncio.wait({task}, timeout=self._remaining_cleanup_seconds())
            if completed_tasks:
                await asyncio.gather(*completed_tasks, return_exceptions=True)
            else:
                logger.warning("Hybrid local processing cleanup exceeded the cleanup deadline")
        if queue is None:
            return
        while True:
            try:
                _, completed_future, _ = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            queue.task_done()
            if not completed_future.done():
                completed_future.cancel()

    async def _observe_local(self, frame: AudioFrame) -> None:
        try:
            result = self._local_processor.observe_frame(frame)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Local ambient processing failed: %s", type(error).__name__)

    async def emit(self) -> HandlerOutput:
        """Emit output from the currently active handler without a second queue."""
        self._assert_owner_loop()
        async with self._state_lock:
            with self._lifecycle_lock:
                handler = self._active_handler
                generation = self._generation
        if handler is None:
            if self._local_output:
                return self._local_output.popleft()
            await self._wait_for_output_wakeup()
            if self._local_output:
                return self._local_output.popleft()
            async with self._state_lock:
                with self._lifecycle_lock:
                    handler = self._active_handler
                    generation = self._generation
            if handler is None:
                return None
        try:
            output = await handler.emit()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Hybrid provider output failed: %s", type(error).__name__)
            if self._is_current_handler(handler, generation):
                await self._deactivate_cloud("provider_emit_failed")
            return None
        if not self._is_current_handler(handler, generation):
            return None
        return output

    def request_music_wind_down(self) -> None:
        """Use quick closing for the rest of this conversation because music is playing."""
        with self._lifecycle_lock:
            if self._mode is not RuntimeMode.CLOUD_CONVERSATION or self._active_handler is None:
                return
            now = self._clock()
            # Music Reachy just started (even a new song over music already playing) still gets its short
            # reply ("Enjoy!") before the countdown begins.
            self._note_user_turn_locked(now)
            if self._music_wind_down_requested_at is not None:
                return
            self._music_wind_down_requested_at = now
        logger.info(
            "Music playing; conversation will close %.0f s after Reachy's reply if the user stays quiet",
            self._MUSIC_WIND_DOWN_SILENCE_S,
        )

    def cancel_music_wind_down(self) -> None:
        """Use the normal inactivity rule for the rest of this conversation, because the user paused the music."""
        with self._lifecycle_lock:
            if (
                self._mode is not RuntimeMode.CLOUD_CONVERSATION
                or self._active_handler is None
                or self._music_wind_down_requested_at is None
            ):
                return
            self._music_wind_down_requested_at = None
            self._last_conversation_activity = self._clock()
        logger.info("Music paused; conversation uses the normal inactivity timeout again")

    def request_activation(self, reason: str) -> None:
        """Start an activation from another thread (an antenna touch); ignored before the runtime has started."""
        with self._lifecycle_lock:
            loop = self._owner_loop
            closed = self._closed
        if loop is None or closed or loop.is_closed():
            return

        def start() -> None:
            task = asyncio.create_task(self.activate(reason), name=f"hybrid-{reason}-activation")
            self._local_event_tasks.add(task)
            task.add_done_callback(self._local_event_tasks.discard)

        try:
            loop.call_soon_threadsafe(start)
        except RuntimeError:  # the loop closed between the check and the call
            logger.debug("Activation request dropped: runtime loop closed")

    def request_close(self, reason: str) -> bool:
        """Close the conversation once Reachy's reply has finished playing; thread-safe.

        Used when Reachy decides the conversation is over or is told to go to sleep. Returns whether a
        conversation was open to close.
        """
        with self._lifecycle_lock:
            if self._mode is not RuntimeMode.CLOUD_CONVERSATION or self._active_handler is None:
                return False
            if self._close_requested is None:
                self._close_requested = (reason, self._clock())
            if reason == "go_to_sleep":
                self._asleep = True
        logger.info("Conversation will close after Reachy's reply reason=%s", reason)
        return True

    def _close_requested_due_locked(self, now: float) -> bool:
        if self._close_requested is None or self._active_handler is None:
            return False
        waited = now - self._close_requested[1]
        if waited >= self._CLOSE_REQUEST_CAP_S:
            return True
        if waited < self._CLOSE_REQUEST_GRACE_S or self._interaction_busy:
            return False
        return not self._playback_is_active_locked(now) and self._active_handler.output_queue.empty()

    def _follow_up_due_locked(self, now: float) -> bool:
        """Close once nobody has been talking with Reachy for the follow-up window."""
        window = self._follow_up_window_s
        if window <= 0 or not self._connection_ready or self._active_handler is None:
            return False
        if self._follow_up_window_for is not None:
            try:
                window = max(window, float(self._follow_up_window_for()))
            except Exception as error:  # a broken hint must not stop conversations from closing
                logger.debug("Follow-up window hint failed: %s", type(error).__name__)
        if self._playback_is_active_locked(now) or not self._active_handler.output_queue.empty():
            return False
        replied_at = self._replied_at_locked()
        if replied_at <= self._last_user_turn_at:
            # People have spoken since his last reply (or since the wake) and he has not answered.
            unanswered_for = now - self._user_turn_started_at
            if unanswered_for >= 2 * window:
                return True  # busy and transcripts can both stick while a room keeps talking
            if self._interaction_busy or self._user_speaking:
                return False  # thinking, running a tool, or someone is mid-sentence
            return unanswered_for >= window and now - self._last_user_turn_at >= self._FOLLOW_UP_SPEECH_GAP_S
        silent_for = now - replied_at
        if self._interaction_busy and silent_for < 2 * window:
            return False  # he said "let me check" and is still working
        speech_start = self._last_user_speech_start_at
        if speech_start > replied_at and silent_for < 2 * window:
            # Someone started talking after his reply: wait while they speak, since the transcript can
            # arrive only at the end of a long sentence, then a moment for it to land.
            if self._user_speaking or now - speech_start < self._MUSIC_WIND_DOWN_SPEECH_HOLD_S:
                return False
        return silent_for >= window

    def _reset_addressee_state_locked(self) -> None:
        self._reply_parts = []
        self._last_assistant_text_at = -math.inf
        self._unanswered_parts = []
        self._unanswered_reply_marker = -math.inf
        self._addressee_checked_words = 0
        self._addressee_checks = 0
        self._not_addressed = None

    def _not_addressed_due_locked(self, now: float) -> bool:
        verdict = self._not_addressed
        if verdict is None:
            return False
        if verdict != (self._generation, self._unanswered_reply_marker):
            self._not_addressed = None  # Reachy has replied since; the verdict is stale
            return False
        replied_at = self._replied_at_locked()
        # Only while he is still silent: if he started answering, Gemini judged it was for him.
        return replied_at <= self._last_user_turn_at and not self._playback_is_active_locked(now)

    def _maybe_start_addressee_check_locked(self, now: float) -> None:
        check = self._addressee_check
        if check is None or self._addressee_checks >= self._ADDRESSEE_MAX_CHECKS:
            return
        if self._addressee_task is not None and not self._addressee_task.done():
            return
        replied_at = self._replied_at_locked()
        # Judge only what followed a real reply, once the room pauses and nobody hears him speaking.
        if replied_at <= 0 or replied_at > self._last_user_turn_at or not self._reply_parts:
            return
        if self._playback_is_active_locked(now) or now - self._last_user_turn_at < self._ADDRESSEE_PAUSE_S:
            return
        people_said = " ".join(self._unanswered_parts)
        words = len(people_said.split())
        if words < self._ADDRESSEE_MIN_WORDS or words - self._addressee_checked_words < self._ADDRESSEE_MIN_WORDS:
            return
        self._addressee_checked_words = words
        self._addressee_checks += 1
        stretch = (self._generation, self._unanswered_reply_marker)
        self._addressee_task = asyncio.create_task(
            self._run_addressee_check(check, " ".join(self._reply_parts), people_said, stretch),
            name="hybrid-addressee-check",
        )

    async def _run_addressee_check(
        self, check: AddresseeChecker, reachy_said: str, people_said: str, stretch: tuple[int, float]
    ) -> None:
        verdict = await check(reachy_said, people_said)
        if verdict is not False:
            return
        with self._lifecycle_lock:
            if stretch == (self._generation, self._unanswered_reply_marker):
                self._not_addressed = stretch
                logger.info("People are talking among themselves; closing the conversation")

    def _replied_at_locked(self) -> float:
        return self._playback_until if math.isfinite(self._playback_until) else 0.0

    def _note_user_turn_locked(self, now: float) -> None:
        """Record something Reachy should answer before a music wind-down countdown starts."""
        if self._last_user_turn_at < self._replied_at_locked():
            self._user_turn_started_at = now  # first input since Reachy's latest reply
        self._last_user_turn_at = max(self._last_user_turn_at, now)

    def _music_wind_down_due_locked(self, now: float) -> bool:
        if self._music_wind_down_requested_at is None or not self._connection_ready or self._active_handler is None:
            return False
        if self._playback_is_active_locked(now) or not self._active_handler.output_queue.empty():
            return False
        # Gemini's speech flag can stay set while it hears music, so it is ignored; busy only holds the
        # close up to a cap. Reachy's speech and user transcripts move the countdown.
        replied_at = self._replied_at_locked()
        if replied_at <= self._last_user_turn_at:
            # Reachy has not answered the latest turn yet (thinking or using a tool): wait, but not forever.
            return (
                now - self._last_user_turn_at >= self._MUSIC_WIND_DOWN_REPLY_CAP_S
                or now - self._user_turn_started_at >= self._MUSIC_WIND_DOWN_TURN_CAP_S
            )
        speech_start = self._last_user_speech_start_at
        if speech_start > replied_at and now - speech_start < self._MUSIC_WIND_DOWN_SPEECH_HOLD_S:
            return False  # the user may be starting a follow-up; its transcript is on the way
        if self._interaction_busy and now - replied_at < self._MUSIC_WIND_DOWN_REPLY_CAP_S:
            # Reachy said something ("let me check") but is still working on the answer (a tool, or more
            # thinking). Busy can stick while it hears music, so it only holds the close up to the cap.
            return False
        window = (
            self._MUSIC_WIND_DOWN_QUESTION_SILENCE_S
            if self._last_reply_was_question
            else self._MUSIC_WIND_DOWN_SILENCE_S
        )
        return now - replied_at >= window

    async def check_inactivity(self) -> bool:
        """Close an inactive cloud session when its configured threshold is reached."""
        self._assert_owner_loop()
        reason: str | None = None
        async with self._state_lock:
            with self._lifecycle_lock:
                if self._mode is not RuntimeMode.CLOUD_CONVERSATION or self._active_handler is None:
                    return False
                now = self._clock()
                if self._close_requested_due_locked(now):
                    assert self._close_requested is not None
                    reason = self._close_requested[0]
                elif self._not_addressed_due_locked(now):
                    reason = "not_addressed"
                elif self._music_wind_down_due_locked(now):
                    reason = "music_wind_down"
                elif self._music_wind_down_requested_at is None and self._follow_up_due_locked(now):
                    reason = "follow_up_window"
                elif (
                    self._inactivity_timeout_s > 0
                    and self._connection_ready
                    and not self._user_speaking
                    and not self._interaction_busy
                    and not self._playback_is_active_locked(now)
                    and self._active_handler.output_queue.empty()
                    and now - self._last_conversation_activity >= self._inactivity_timeout_s
                ):
                    reason = "inactivity_timeout"
                else:
                    self._maybe_start_addressee_check_locked(now)
        if reason is not None:
            return await self._deactivate_cloud(reason)
        return False

    async def _monitor_inactivity(self, handler: ConversationHandler, generation: int) -> None:
        try:
            while self._is_current_handler(handler, generation):
                await asyncio.sleep(self._monitor_interval_s)
                if self._is_current_handler(handler, generation):
                    await self.check_inactivity()
        except asyncio.CancelledError:
            raise

    async def _wait_for_output_wakeup(self) -> None:
        """Wait until cloud output can exist or shutdown has started."""
        while True:
            with self._lifecycle_lock:
                if self._active_handler is not None or self._closed or self._mode is RuntimeMode.STOPPING:
                    return
            if self._local_output:
                return
            await self._output_wakeup_event.wait()
            self._output_wakeup_event.clear()

    async def deactivate_cloud(self, reason: str = "requested") -> bool:
        """Close the active provider and return to local ambient mode."""
        self._assert_owner_loop()
        return await self._deactivate_cloud(reason)

    async def _contain_uncommitted_handler(self, handler: ConversationHandler, reason: str, generation: int) -> bool:
        """Fence a constructed handler whose activation failed before cloud ownership."""
        shutdown_task: asyncio.Task[None]
        async with self._state_lock:
            with self._lifecycle_lock:
                current_mode = self._mode
                if current_mode is RuntimeMode.LOCAL_AMBIENT:
                    return self._shutdown_error is None
                if current_mode is not RuntimeMode.LOCAL_COMMAND or self._generation != generation:
                    return False
                self._generation += 1
                self._active_handler = None
                self._stopping_handler = handler
                self._provider_task = None
                self._inactivity_task = None
                self._postactivation_flush_task = None
                self._activation_text_task = None
                self._acoustic_ack_task = None
                self._pending_activation_text = None
                self._pending_acoustic_ack_generation = None
                self._postactivation_speech_observed = False
                self._connection_ready = False
                self._user_speaking = False
                self._interaction_busy = False
                self._playback_until = 0.0
                self._clear_wake_followup_waiter()
                self._clear_postactivation_queue()
                self._shutdown_error = None
                shutdown_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                if self._close_deadline is not None:
                    shutdown_deadline = min(shutdown_deadline, self._close_deadline)
                self._shutdown_deadline = shutdown_deadline
                self._transition(RuntimeMode.STOPPING, reason)
                try:
                    handler.stop_background_work()
                except Exception as error:
                    self._shutdown_error = "provider_stop_failed"
                    logger.warning("Provider background-work stop failed: %s", type(error).__name__)
                self._clear_handler_state(handler)
                shutdown_task = asyncio.create_task(
                    self._run_shutdown(handler, reason, ()),
                    name="hybrid-shutdown",
                )
                self._shutdown_task = shutdown_task
                shutdown_task.add_done_callback(self._shutdown_task_done)

        await self._wait_for_shutdown(shutdown_task)
        with self._lifecycle_lock:
            return self._mode is RuntimeMode.LOCAL_AMBIENT and self._shutdown_error is None

    async def _deactivate_cloud(self, reason: str) -> bool:
        current_task = asyncio.current_task()
        shutdown_task: asyncio.Task[None] | None = None
        cleanup_tasks: list[asyncio.Task[bool] | asyncio.Task[None]] = []
        handler: ConversationHandler | None = None

        async with self._state_lock:
            with self._lifecycle_lock:
                mode = self._mode
                active_handler = self._active_handler
                if mode is RuntimeMode.LOCAL_AMBIENT and active_handler is None:
                    self._clear_wake_followup_waiter()
                    return False

                if mode is RuntimeMode.LOCAL_COMMAND:
                    pending_activation = self._activation_task
                    self._generation += 1
                    self._clear_postactivation_queue()
                    self._clear_wake_followup_waiter()
                    if pending_activation is None or pending_activation.done():
                        self._transition(RuntimeMode.LOCAL_AMBIENT, reason)
                    else:
                        self._shutdown_error = None
                        shutdown_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                        if self._close_deadline is not None:
                            shutdown_deadline = min(shutdown_deadline, self._close_deadline)
                        self._shutdown_deadline = shutdown_deadline
                        self._transition(RuntimeMode.STOPPING, reason)
                        if pending_activation is not current_task:
                            pending_activation.cancel()
                            cleanup_tasks.append(pending_activation)
                        shutdown_task = asyncio.create_task(
                            self._run_shutdown(None, reason, tuple(cleanup_tasks)),
                            name="hybrid-shutdown",
                        )
                        self._shutdown_task = shutdown_task
                        shutdown_task.add_done_callback(self._shutdown_task_done)
                elif mode is RuntimeMode.STOPPING:
                    shutdown_task = self._shutdown_task
                elif mode is RuntimeMode.CLOUD_CONVERSATION:
                    handler = active_handler
                    self._log_cloud_session_summary(reason)
                    tasks_to_cancel = (
                        self._activation_task,
                        self._provider_task,
                        self._inactivity_task,
                        self._postactivation_flush_task,
                        self._activation_text_task,
                        self._acoustic_ack_task,
                        self._addressee_task,
                    )
                    self._generation += 1
                    self._active_handler = None
                    self._stopping_handler = handler
                    self._provider_task = None
                    self._inactivity_task = None
                    self._postactivation_flush_task = None
                    self._activation_text_task = None
                    self._acoustic_ack_task = None
                    self._addressee_task = None
                    self._close_requested = None
                    self._pending_activation_text = None
                    self._pending_acoustic_ack_generation = None
                    self._postactivation_speech_observed = False
                    self._connection_ready = False
                    self._user_speaking = False
                    self._interaction_busy = False
                    self._playback_until = 0.0
                    self._clear_wake_followup_waiter()
                    self._clear_postactivation_queue()
                    self._accumulate_session_duration(self._clock())
                    self._shutdown_error = None
                    shutdown_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                    if self._close_deadline is not None:
                        shutdown_deadline = min(shutdown_deadline, self._close_deadline)
                    self._shutdown_deadline = shutdown_deadline
                    self._transition(RuntimeMode.STOPPING, reason)
                    if handler is not None and isinstance(handler, ConversationHandler):
                        try:
                            handler.stop_background_work()
                        except Exception as error:
                            self._shutdown_error = "provider_stop_failed"
                            logger.warning("Provider background-work stop failed: %s", type(error).__name__)
                        self._clear_handler_state(handler)
                    for task in tasks_to_cancel:
                        if task is None or task is current_task:
                            continue
                        if task is not self._activation_task and not task.done():
                            task.cancel()
                        cleanup_tasks.append(task)
                    shutdown_task = asyncio.create_task(
                        self._run_shutdown(handler, reason, tuple(cleanup_tasks)),
                        name="hybrid-shutdown",
                    )
                    self._shutdown_task = shutdown_task
                    shutdown_task.add_done_callback(self._shutdown_task_done)
                else:
                    raise ValueError(f"Unsupported runtime mode: {mode.value}")

        if shutdown_task is None:
            return mode is RuntimeMode.LOCAL_COMMAND
        await self._wait_for_shutdown(shutdown_task)
        with self._lifecycle_lock:
            return self._mode is RuntimeMode.LOCAL_AMBIENT and self._shutdown_error is None

    def _clear_handler_state(self, handler: ConversationHandler) -> None:
        """Fence queued provider output and movement state before teardown."""
        self._drain_handler_output(handler)
        if not isinstance(handler, ConversationHandler):
            return
        clear_queue = handler._clear_queue
        if clear_queue is not None:
            try:
                clear_queue()
            except Exception as error:
                logger.warning("Provider playback fence failed: %s", type(error).__name__)
        try:
            movement_manager = handler.deps.movement_manager
            movement_manager.clear_move_queue()
            movement_manager.set_listening(False)
            movement_manager.set_speaking(False)
        except (AttributeError, TypeError) as error:
            logger.debug("Provider movement fence unavailable: %s", type(error).__name__)
        except Exception as error:
            logger.warning("Provider movement fence failed: %s", type(error).__name__)

    async def _run_shutdown(
        self,
        handler: ConversationHandler | None,
        reason: str,
        cleanup_tasks: tuple[asyncio.Task[bool] | asyncio.Task[None], ...],
    ) -> None:
        """Await provider and canceled auxiliary work as one bounded operation."""
        cleanup_operations: list[Awaitable[object]] = [self._await_shutdown_task(task) for task in cleanup_tasks]
        if handler is not None:
            cleanup_operations.insert(0, self._shutdown_handler_strict(handler, reason))
        if not cleanup_operations:
            return
        results = await asyncio.gather(*cleanup_operations, return_exceptions=True)
        shutdown_result = results[0]
        if handler is not None and isinstance(shutdown_result, BaseException):
            raise RuntimeError("provider shutdown did not complete") from shutdown_result
        auxiliary_results = results[1:] if handler is not None else results
        for result in auxiliary_results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise RuntimeError("hybrid cleanup did not complete") from result

    @staticmethod
    async def _await_shutdown_task(task: asyncio.Task[bool] | asyncio.Task[None]) -> object:
        """Await one task so cancellation or failure is included in containment."""
        return await task

    def _remaining_cleanup_seconds(self) -> float:
        """Return the remaining fixed cleanup budget for provider-adjacent work."""
        with self._lifecycle_lock:
            deadlines = tuple(
                deadline for deadline in (self._close_deadline, self._shutdown_deadline) if deadline is not None
            )
        if not deadlines:
            return self._SHUTDOWN_TIMEOUT_S
        return max(0.0, min(deadlines) - asyncio.get_running_loop().time())

    async def _wait_for_shutdown(self, task: asyncio.Task[None]) -> None:
        """Wait for shutdown until its fixed wall-clock containment deadline."""
        if task.done():
            self._finalize_shutdown(task)
            return
        with self._lifecycle_lock:
            deadline = self._shutdown_deadline
        remaining = self._SHUTDOWN_TIMEOUT_S
        if deadline is not None:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        completed, _ = await asyncio.wait({task}, timeout=remaining)
        if completed:
            self._finalize_shutdown(task)
        else:
            self._mark_shutdown_timeout(task)

    def _mark_shutdown_timeout(self, task: asyncio.Task[None]) -> None:
        event: tuple[RuntimeMode, str, int] | None = None
        with self._lifecycle_lock:
            if self._shutdown_task is not task or self._mode is not RuntimeMode.STOPPING:
                return
            if task.done():
                self._finalize_shutdown(task)
                return
            if self._shutdown_error is None:
                self._shutdown_error = "provider_cleanup_timeout"
                event = (RuntimeMode.STOPPING, "shutdown_timeout", self._generation)
        logger.warning("Hybrid provider cleanup exceeded the bounded shutdown deadline")
        if event is not None:
            self._notify_state_observer(*event)

    def _shutdown_task_done(self, task: asyncio.Task[None]) -> None:
        self._finalize_shutdown(task)

    def _notify_cloud_session(self, active: bool) -> None:
        """Tell the observer that a cloud conversation started or ended, once per session, without blocking."""
        observer = self._cloud_session_observer
        if observer is None or active == self._cloud_session_notified:
            return
        self._cloud_session_notified = active
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        task: asyncio.Future[None] = asyncio.ensure_future(observer(active))
        self._cloud_session_tasks.add(task)
        task.add_done_callback(self._cloud_session_tasks.discard)

    def _finalize_shutdown(self, task: asyncio.Task[None]) -> None:
        event: tuple[RuntimeMode, str, int] | None = None
        failed = False
        ended = False
        try:
            task.result()
        except BaseException as error:
            failed = True
            logger.warning("Hybrid provider cleanup failed: %s", type(error).__name__)
        with self._lifecycle_lock:
            if self._shutdown_task is not task or self._mode is not RuntimeMode.STOPPING:
                return
            if failed:
                if self._shutdown_error is None:
                    self._shutdown_error = "provider_cleanup_failed"
                    event = (RuntimeMode.STOPPING, "shutdown_failed", self._generation)
                self._shutdown_task = None
                self._shutdown_deadline = None
                self._stopping_handler = None
            else:
                self._shutdown_task = None
                self._shutdown_deadline = None
                self._stopping_handler = None
                self._shutdown_error = None
                self._transition(RuntimeMode.LOCAL_AMBIENT, "shutdown_complete")
                ended = True
        if ended:
            self._notify_cloud_session(False)
        if event is not None:
            self._notify_state_observer(*event)

    async def close(self) -> None:
        """Idempotently stop the provider and leave subsequent frames local."""
        self._assert_owner_loop()
        with self._lifecycle_lock:
            if self._closed and self._active_handler is None and self._activation_task is None:
                return
            close_task = self._close_task
            if close_task is None or close_task.done():
                self._closed = True
                self._close_deadline = asyncio.get_running_loop().time() + self._SHUTDOWN_TIMEOUT_S
                close_task = asyncio.create_task(self._finish_close(), name="hybrid-close")
                self._close_task = close_task
        self._local_start_event.set()
        self._local_output.clear()
        self._output_wakeup_event.set()

        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await asyncio.shield(close_task)
            raise

    async def _finish_close(self) -> None:
        self._clear_wake_followup_waiter()
        await self._deactivate_cloud("runtime_closed")
        current_task = asyncio.current_task()
        local_event_tasks = tuple(task for task in self._local_event_tasks if task is not current_task)
        for task in local_event_tasks:
            task.cancel()
        if local_event_tasks:
            completed, _ = await asyncio.wait(local_event_tasks, timeout=self._remaining_cleanup_seconds())
            if completed:
                await asyncio.gather(*completed, return_exceptions=True)
            if len(completed) != len(local_event_tasks):
                logger.warning("Hybrid local event cleanup exceeded the cleanup deadline")
        self._local_event_tasks.clear()
        with self._lifecycle_lock:
            activation_task = self._activation_task
        if (
            activation_task is not None
            and activation_task is not asyncio.current_task()
            and not activation_task.done()
        ):
            activation_task.cancel()
            completed, _ = await asyncio.wait({activation_task}, timeout=self._remaining_cleanup_seconds())
            if not completed:
                logger.warning("Hybrid activation cleanup exceeded the cleanup deadline")
        await self._stop_local_processing()
        callback = self._local_processing_close_callback
        if callback is not None:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    callback_task: asyncio.Future[None] = asyncio.ensure_future(result)
                    completed_tasks, _ = await asyncio.wait({callback_task}, timeout=self._remaining_cleanup_seconds())
                    if completed_tasks:
                        await asyncio.gather(*completed_tasks, return_exceptions=True)
                    else:
                        callback_task.cancel()
                        logger.warning("Hybrid local processing cleanup exceeded the cleanup deadline")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Local processing shutdown failed: %s", type(error).__name__)

    async def _await_cancelled_task(self, task: asyncio.Task[object]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.warning("Hybrid task cleanup failed: %s", type(error).__name__)

    async def _shutdown_handler(self, handler: ConversationHandler, reason: str) -> None:
        try:
            await self._shutdown_handler_strict(handler, reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Provider shutdown failed reason=%s error=%s", reason, type(error).__name__)

    def _prune_shutdown_handler_tasks(self) -> None:
        """Drop completed shutdown records whose handlers are no longer live."""
        stale_handler_ids = [
            handler_id
            for handler_id, (handler_ref, task) in self._shutdown_handler_tasks.items()
            if handler_ref() is None and task.done()
        ]
        for handler_id in stale_handler_ids:
            self._shutdown_handler_tasks.pop(handler_id, None)

    async def _shutdown_handler_strict(self, handler: ConversationHandler, reason: str) -> None:
        """Shutdown one handler once and preserve failures for containment."""
        del reason
        self._prune_shutdown_handler_tasks()
        handler_id = id(handler)
        tracked = self._shutdown_handler_tasks.get(handler_id)
        if tracked is not None:
            target, task = tracked
            if target() is handler:
                await asyncio.shield(task)
                return
        task = asyncio.create_task(handler.shutdown(), name="hybrid-provider-shutdown")
        self._shutdown_handler_tasks[handler_id] = (weakref.ref(handler), task)
        await asyncio.shield(task)

    def _record_session_segment(self, duration: float) -> None:
        if duration <= 0:
            return
        with self._metrics_lock:
            self._gemini_session_duration += duration

    def _accumulate_session_duration(self, now: float) -> None:
        with self._lifecycle_lock:
            connected_since = self._connected_since
            self._connected_since = None
        if connected_since is not None:
            self._record_session_segment(max(0.0, now - connected_since))

    @staticmethod
    def _copy_frame(frame: AudioFrame) -> AudioFrame:
        sample_rate, audio = frame
        return sample_rate, audio.copy()


__all__ = [
    "AcknowledgementCallback",
    "HandlerFactory",
    "HybridRuntime",
    "InteractionBusyObserver",
    "LocalCommandOutcome",
    "RuntimeMode",
    "RuntimeStatusSnapshot",
    "TrafficMetricsSnapshot",
]
