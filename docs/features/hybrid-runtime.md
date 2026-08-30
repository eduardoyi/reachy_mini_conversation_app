# Feature: hybrid ambient runtime

- Status: `in-progress`
- Last updated: 2026-08-29
- Plan: [Reachy hybrid household companion](../plans/reachy-hybrid-household-companion.md)

## Overview

The opt-in hybrid runtime keeps the local microphone path awake while the
selected realtime provider is closed. It starts in `LOCAL_AMBIENT`, stores only
bounded mono PCM in a RAM ring, and runs a replaceable local processing
boundary. Optional local VAD/STT creates bounded conversational context and a
local `Reachy` transcript wake event. Exact allowlisted physical commands stay
local; anything uncertain opens one fresh provider handler in
`CLOUD_CONVERSATION`. Inactivity, cancellation, setup failure, and provider
failure return the runtime to ambient mode.

The existing direct provider path remains unchanged when
`HYBRID_RUNTIME_ENABLED=0` (the default).

## User-visible behavior

Enable the mode in the ignored instance `.env`:

```env
HYBRID_RUNTIME_ENABLED=1
HYBRID_CLOUD_INACTIVITY_SECONDS=60
LOCAL_CONTEXT_STRATEGY=continuous
LOCAL_STT_BASE_URL=http://127.0.0.1:8766/v1
```

Ambient capture does not require `GEMINI_API_KEY` and does not construct or
connect a cloud handler. Press Space or Enter in the web conversation view to
call `conversation.activate`; Reachy queues a small movement acknowledgment,
then opens the selected provider. With continuous local STT, “Reachy” takes the
same local acknowledgment path. Exact `stop`, `be quiet`, `look left`, `look
right`, and `look at me` suffixes use existing local movement/tool boundaries
and return to ambient without constructing a provider. The browser keyboard
action remains separate from microphone mute. Each cloud activation gets a
fresh provider handler.

When a completed local transcript contains both the wake phrase and a question,
the bounded wake-stripped suffix is submitted through `ConversationHandler.say`
only after the new provider reports ready. The rolling transcript remains
untrusted background context; the suffix is the actual user turn that asks the
provider to respond. Failure is metadata-only and does not crash the session;
deactivation cancels a blocked submission.

## Architecture and data flow

```text
LocalStream (one microphone/speaker loop)
  └─ HybridRuntime
       ├─ LOCAL_AMBIENT → RAM-only mono-int16 ring + local VAD/STT
       │                    └─ bounded transcript → local Reachy wake
       ├─ LOCAL_COMMAND → exact LocalIntentRouter
       │                    ├─ local action → existing tool/movement API
       │                    └─ uncertain/failure → cloud escalation
       └─ CLOUD_CONVERSATION → bounded text context + one ConversationHandler
                                  └─ existing tools + MovementManager
```

Ambient frames are copied as mono int16 PCM. SDK microphone frames are
float32 stereo with shape `(samples, channels)`; `audio_to_mono_int16()`
deterministically averages supported channels, preserves int16 PCM scale,
clips/scales normalized float samples, and returns a defensive mono copy before
the ring stores them. Channels-first frames are accepted when the sample axis
is clearly longer; ambiguous multi-channel shapes are interpreted as
channels-last. The ring adopts the first accepted sample rate and rejects later
mixed-rate frames; it does not resample ambient audio. The ring is bounded to
at most 20 seconds and is never replayed to a provider in Phase 2. Before each
potentially blocking SDK pull, the capture worker reserves a monotonic epoch
under the same lock used by the activation barrier snapshot; an in-flight pull
therefore retains its preactivation epoch even when the SDK returns its frame
after activation. Frames at or before that barrier are kept local. Frames
captured after activation but before provider readiness use a bounded 20-second
FIFO and may drop oldest frames if the provider handshake runs longer;
`postactivation_frames_dropped` exposes that diagnostic. This includes frames
captured during the local acknowledgment seam after the capture barrier. Only
successful provider sends increment cloud traffic counters; camera frames are
counted only after a successful explicitly requested camera send. Ambient
playback waits on the runtime state wakeup rather than polling while no cloud
handler exists.

The handshake FIFO measures time samples rather than scalar array elements, so
an SDK frame shaped `(16000, 2)` represents one second. A `(1, 2)` frame is one
stereo sample, while a `(N, 1)` frame remains `N` mono samples.

## Contracts and configuration

The runtime exposes the exact states `LOCAL_AMBIENT`, `LOCAL_COMMAND`, and
`CLOUD_CONVERSATION`, a metadata-only state observer, an activation operation,
provider lifecycle methods, status, and immutable metrics snapshots. The
provider factory is called only on activation and reads the current provider
configuration, so settings changes apply no later than the next activation.
Runtime coroutines are scheduled on the `LocalStream` asyncio loop; the UI RPC
transport only forwards activation requests to that loop.

Configuration is defined in `config.py`:

| Variable | Default | Meaning |
|---|---:|---|
| `HYBRID_RUNTIME_ENABLED` | `0` | Opt into the local ambient runtime. |
| `HYBRID_CLOUD_INACTIVITY_SECONDS` | `60` | Close an inactive cloud session; `0` disables this close. |
| `HYBRID_AMBIENT_BUFFER_SECONDS` | `20` | RAM ring duration, capped at 20 seconds. |
| `LOCAL_CONTEXT_STRATEGY` | `continuous` | Use a rolling local transcript, or `activation` to transcribe the RAM snapshot after keyboard activation. |
| `LOCAL_STT_BASE_URL` | unset | Loopback-only OpenAI-compatible local transcription endpoint. |
| `LOCAL_STT_MODEL` | `whisper-1` | Local endpoint model name. |
| `LOCAL_STT_TIMEOUT_SECONDS` | `5` | Bound each local transcription request. |
| `LOCAL_VAD_RMS_THRESHOLD` | `500` | Initial amplitude-based VAD threshold. |
| `LOCAL_VAD_SILENCE_SECONDS` | `0.6` | Silence used to close a speech segment. |
| `LOCAL_WAKE_WORD` | `Reachy` | Transcript wake phrase in continuous mode. |
| `LOCAL_WAKE_COOLDOWN_SECONDS` | `2` | Duplicate wake suppression window. |
| `LOCAL_CONTEXT_MAX_CHARS` | `2000` | Bound retained/injected derived text. |

Status includes these exact counters:

- `ambient_audio_seconds_buffered`
- `ambient_audio_bytes_sent_to_cloud`
- `cloud_audio_bytes_sent`
- `cloud_video_frames_sent`
- `gemini_session_duration`

The first ambient counter is current ring occupancy. Ambient cloud bytes remain
zero because no ambient ring data is sent. Gemini session duration uses a
monotonic connection boundary and accumulates across fresh sessions. The
diagnostic `postactivation_frames_dropped` is separate from these five
required traffic/session counters. Additional metadata-only diagnostics include
context character count and activation latency, wake detections/suppression,
local command execution/failure/escalation and latency, and
`postactivation_frames_dropped`. Gemini's
`first_audio_unavailable_count`, `first_audio_reordered_count`, and
`first_audio_ambiguous_count` remain provider latency diagnostics; they are not
privacy counters and contain no audio or transcript data. An audio-first event
ordering leaves exact end-of-speech latency unknowable, so no fabricated zero
or negative value is reported. A final transcript arriving after a completed
turn without a previously observed final transcript is recorded as ambiguous
instead of rearming a second sample.

## Integration points and extension points

`LocalStream` remains the sole owner of SDK media capture, playback, and its
single output queue. `HybridRuntime` is a coordinator, not a
`ConversationHandler`; it delegates cloud audio/output to the one active
provider. `ConversationHandler` supplies typed traffic, connection, and
startup-greeting controls. `MovementManager.acknowledge_activation()` is the
only path used for activation acknowledgment; the runtime never calls robot
motor APIs. The manager's worker captures its last commanded full-body pose,
applies a relative nod, and returns to that exact pose.

`AmbientContextProcessor` is the replaceable local VAD/STT seam.
`RmsEnergyVAD` and the loopback-only `OpenAICompatibleLocalSTT` are benchmark
baselines, not permanent backend choices. Future local implementations must
retain the same no-persistence/no-cloud contract. `TranscriptWakeWordDetector`
can later be replaced by an acoustic detector without changing the runtime.
`LocalIntentRouter` remains exact and fail-closed; do not introduce fuzzy or
LLM routing into this boundary.

## Constraints and failure modes

- Ambient mode must work with no cloud credentials and with the provider factory
  never called.
- Pre-activation samples are not sent or injected into a cloud session.
- Provider setup/send failures, cancellation, stale generations, and repeated
  shutdown are handled without leaving a live cloud session.
- Cloud inactivity timing starts after provider connection readiness, and is
  paused while the provider reports that the user is speaking. Captured frames
  alone do not refresh the cloud inactivity timer.
- The global app sleep timer remains separate from cloud conversational
  inactivity.
- The postactivation handshake queue is bounded to 20 seconds; overflow drops
  oldest frames and increments `postactivation_frames_dropped`.
- RAM-only application storage does not prevent operating-system swapping; no
  application audio files or raw payload logs are created.
- Strategy A never waits for an in-flight continuous STT result during
  activation. Strategy B waits only for its bounded local STT timeout and uses
  keyboard activation because it has no continuous transcript to detect wake.
- An addressed transcript suffix is capped at 512 characters and sent only
  after cloud activation; it is never logged. Exact local commands consume the
  suffix locally and never submit it to a provider.
- The initial RMS VAD is amplitude-based and must be replaced or tuned from
  microphone measurements; it does not solve echo cancellation.
- Generated architecture SVG regeneration is pending; `docs/scheme.mmd` is the
  source of truth.

## Test inventory

Deterministic ring, state-machine, lifecycle, timeout, activation,
privacy-boundary, provider-hook, and no-cloud soak coverage is distributed
across:

- `tests/test_ambient.py`
- `tests/test_hybrid_runtime.py`
- `tests/test_config.py`
- `tests/test_console.py`
- `tests/test_main.py`
- `tests/test_gemini_live.py`
- `tests/test_huggingface_realtime.py`
- `tests/test_moves.py`
- `tests/test_static_contract.py` (source-level browser binding contracts)
- `tests/test_ambient_context.py`
- `tests/test_local_audio_backends.py`
- `tests/test_wake_word.py`
- `tests/test_local_intent_router.py`
- `tests/test_local_actions.py`

`tests/test_console.py` exercises the JSON-RPC activation path, owner-loop
marshalling, capture worker, delayed provider readiness, the deterministic
in-flight capture-epoch fence, and LocalStream boundaries with fakes; it does
not exercise a live browser or cloud session. The regression
`test_hybrid_capture_epoch_fences_inflight_frame_and_preserves_fifo` blocks an
SDK pull across activation, asserts that frame stays local, and then asserts
that later provider-bound frames retain FIFO order.
The browser contract checks are source-level because the repository has no
JavaScript test harness. The final repository gate passed on 2026-08-29: Ruff
clean, formatting left 87 files unchanged, strict mypy clean across 49 source
files, and all 474 tests passed in 9.63 s. `uv lock --check`, JavaScript syntax
checks, and `git diff --check` also passed. Manual simulator/LOCAL/Gemini
acceptance subsequently passed: a five-minute ambient soak retained 20 seconds
of RAM audio while all cloud counters remained zero; activation produced a
normal tool-using, interruptible conversation; and inactivity returned the
runtime to ambient. Activation-to-connected was approximately 885 ms, including
`connection_ready_ms=505.6`. The final status retained zero ambient-cloud bytes,
zero video frames and postactivation drops, 5,368,640 cloud-audio bytes, and
167.511 seconds of connected time. Physical hardware and first-response-audio
latency remain unverified.

The Phase 3–5 focused implementation suite passed 195 tests on 2026-08-29,
covering both context strategies, addressed-turn submission and cancellation,
local endpoint privacy/timeout/cancellation,
partial/final wake behavior, exact/ambiguous intent routing, action failures,
application wiring, and the dinner-demo privacy boundary. Live local STT,
wake-quality, command-movement, CPU/RAM, and dinner-demo acceptance remain
pending and are not implied by the automated result.

After the final integration and hardening changes, the exact repository gate
passed on 2026-08-29: Ruff was clean, formatting left 98 files unchanged,
strict mypy was clean across 54 source files, and all 649 tests passed in
11.39 seconds. `uv lock --check`, both JavaScript syntax checks, and
`git diff --check` also passed.

## Changelog

| Date | Change |
|---|---|
| 2026-08-29 | Added Phase 2 runtime contract, privacy boundary, configuration, and validation inventory. |
| 2026-08-29 | Recorded the clean 456-test repository gate and retained manual simulator/live-provider acceptance as pending. |
| 2026-08-29 | Added activation-seam FIFO retention and event-driven ambient playback wakeups with regression coverage; the focused/full suites now pass 89/459 tests. |
| 2026-08-29 | Independent adversarial re-review found no P0/P1 blocker; focused, asyncio-debug, and repeated race-sensitive checks passed. |
| 2026-08-29 | Live Phase 2 smoke exposed that the SDK supplies float32 stereo `(samples, 2)` frames; the ambient ring previously rejected every frame. Added shared deterministic stereo-to-mono normalization and regression coverage. Manual acceptance remains pending. |
| 2026-08-29 | Corrected int16 downmix scaling, one-sample stereo handling, and stereo postactivation duration accounting. Independent re-review found no P0/P1 issue; the final root gate passes all 463 tests. |
| 2026-08-29 | Follow-up review corrected handshake duration for multi-channel frames and the single-row stereo normalization case; SDK-shaped 20-second FIFO/drop-boundary and shape regressions pass. |
| 2026-08-29 | Manual simulator/LOCAL/Gemini acceptance passed with zero preactivation cloud traffic, successful activation/conversation/tools/interruption, and inactivity close. Recorded the missing first-audio metric and cumulative capture-worker drops as follow-ups. |
| 2026-08-29 | First-audio diagnostics now report valid positive samples or explicit reordered/ambiguous unavailability; independent review found no blocker and the full gate passes 474 tests. |
| 2026-08-29 | Documented the honest first-audio metric boundary: independent Gemini marker/audio ordering may make end-of-speech latency unknowable, so the provider reports bounded unavailable/reordered diagnostics instead of fabricated values. |
| 2026-08-29 | Integrated Phase 3–5 local context strategies, loopback STT/RMS VAD, transcript wake, exact local commands, text-only Gemini context, and deterministic privacy/lifecycle tests. Live acceptance remains pending. |
| 2026-08-29 | Final Phase 3–5 integration gate passed all 649 tests plus Ruff, formatting, strict mypy, lock, JavaScript syntax, and diff checks. Live microphone/local-model acceptance remains pending. |
| 2026-08-29 | Capture epochs are now reserved before blocking SDK pulls and included in the activation barrier snapshot, closing the in-flight preactivation privacy race; LocalStream regression coverage verifies the later postactivation FIFO. |
