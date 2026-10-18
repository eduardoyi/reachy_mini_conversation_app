# Feature: hybrid ambient runtime

- Status: `in-progress`
- Last updated: 2026-10-17
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

The initial project `.env` load preserves values already supplied in the
process environment and fills only unset values. This makes one-off direct
benchmark commands such as `HYBRID_RUNTIME_ENABLED=0 ...` reliable while
allowing unset configuration, including a locally stored Gemini key, to load
from the ignored `.env`.

Set `LOCAL_TRANSCRIPT_PREVIEW_ENABLED=1` only for a short-lived local STT
diagnostic. The Talk view then polls the loopback-only
`GET /api/transcript-preview` endpoint with `Cache-Control: no-store` and shows
the current bounded continuous transcript with a diagnostic notice. The preview
is default-off, never appears in `conversation.status`, logs, browser storage,
or disk; preview retrieval itself adds no cloud traffic. Normal activation may
still send bounded derived context to the selected provider, and the preview
reports unavailable when local processing is absent or closed. Non-loopback
clients receive `403` without a runtime read.

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
call `conversation.activate`; the runtime queues a small movement
acknowledgment, then opens the selected provider. With continuous local STT,
“Reachy” takes the same local acknowledgment path. Exact `stop`, `be quiet`, `look
right`, and `look at me` suffixes use existing local movement/tool boundaries
and return to ambient without constructing a provider. The browser keyboard
action remains separate from microphone mute. Each cloud activation gets a
fresh provider handler.

When a completed local transcript contains both the wake phrase and a question,
the bounded wake-stripped suffix is submitted through `ConversationHandler.say`
only after the new provider reports ready. The current wake/question sequence is
excluded from seeded background history, so it appears once as the addressed
turn while earlier ambient transcript remains available. Failure is metadata-only
and does not crash the session; deactivation cancels a blocked submission.
Provider readiness now means any provider-owned initial-history seed has
completed. While the addressed text is being submitted, new microphone frames
enter the existing bounded postactivation FIFO without blocking capture; they
flush only after `say()` finishes. The resulting order is context history,
addressed text, then live audio.

If local STT emits a wake-only final followed by a separate final command, the
runtime enters `LOCAL_COMMAND`, acknowledges immediately, and waits for exactly
one next final segment for the configured bounded timeout (five seconds by
default). Sequence metadata prevents an older delayed final from being adopted
as the follow-up. Exact local commands execute without provider construction and
return to ambient mode. An uncertain follow-up is preserved as the addressed
text for the normal cloud activation; timeout also follows the normal cloud
path. While this waiter is active, post-barrier frames are both retained in the
bounded cloud FIFO and observed by the local processor; pre-wake frames never
cross the cloud boundary. Local success clears that FIFO; timeout or escalation
leaves it intact for provider readiness. Shutdown and cancellation release the
waiter without retaining its text.

## Shutdown containment and provider busy state

The [Gemini 3.8 migration](../plans/gemini-38-live-extended-thinking-migration.md)
adds a shared STOPPING state and a provider-neutral interaction-busy signal.
Provider integration and the full repository gate passed offline validation. The
conversation UI displays `Stopping…` or `Cleanup unavailable` and blocks activation
until cleanup is confirmed.

A stop request fences new provider work immediately, clears queued output, and
closes the old generation. LOCAL_AMBIENT is reached only when provider and auxiliary
cleanup completes. A 15-second wait bounds the caller; delayed or failed cleanup
leaves STOPPING visible and blocks reactivation. Confirmed late completion may
release that barrier. This distinguishes immediate admission shutdown from proven
connection closure; it cannot recall a request already accepted by a remote service.

Provider reasoning and playback are separate from user speech. An authoritative
busy signal suppresses ordinary inactivity without refreshing the provider's hard
interaction deadline. The local output path must track queued playback duration;
a successful SDK enqueue is not evidence that sound has finished playing. Interrupt
and shutdown clear that estimate. Hardware playback timing remains unmeasured.

Primary verification on 2026-09-16:
`REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_hybrid_runtime.py tests/test_hybrid_smoke.py tests/test_console.py tests/test_main.py tests/test_moves.py tests/audio/test_speaker_output.py -q`
passed 227 tests with one skipped opt-in native speaker smoke test. These fakes
cover cancellation-resistant activation/cleanup, late completion, blocked
reactivation, direct restart failure and playback estimates; they do not establish
real speaker timing or cloud connection behavior.

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

LocalStream hands copied local frames to a bounded 100-frame (about one second)
runtime FIFO and does not wait for the processor to finish. One runtime worker consumes that FIFO
sequentially, preserving the order presented to local VAD/STT while the capture
loop remains available to drain the SDK queue. A full local FIFO drops the newest
local frame, increments `local_processing_frames_dropped`, and logs one warning per
drop burst. A drop does not reset the acoustic wake detector's stream: a short gap
barely changes its score, while a reset blinds it for about 1.3 s; this is distinct
from capture-worker drops and from the provider handshake FIFO. Runtime close
cancels the worker and releases queued callers without retaining frame data;
frames arriving after close are dropped before any observation fallback.

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
| `HYBRID_FOLLOW_UP_SECONDS` | `15` | Keep listening for a follow-up this long after Reachy's reply; unanswered speech for this long also closes the session. `0` disables. |
| `HYBRID_ADDRESSEE_CHECK_ENABLED` | `1` | With `OPENAI_API_KEY`, ask the backend model whether unanswered speech after a reply was for Reachy, and close early when it clearly was not. |
| `HYBRID_AMBIENT_BUFFER_SECONDS` | `20` | Raw PCM RAM ring duration, capped at 20 seconds. |
| `LOCAL_CONTEXT_WINDOW_SECONDS` | `60` | Finalized local transcript retention in seconds; text-only and independent from the PCM ring. |
| `LOCAL_CONTEXT_STRATEGY` | `continuous` | Use a rolling local transcript, or `activation` to transcribe the RAM snapshot after keyboard activation. |
| `LOCAL_STT_BASE_URL` | unset | Loopback-only OpenAI-compatible local transcription endpoint. |
| `LOCAL_STT_MODEL` | `whisper-1` | Local endpoint model name. |
| `LOCAL_STT_TIMEOUT_SECONDS` | `5` | Bound each local transcription request. |
| `LOCAL_VAD_RMS_THRESHOLD` | `500` | Initial amplitude-based VAD threshold. |
| `LOCAL_VAD_SILENCE_SECONDS` | `0.6` | Silence used to close a speech segment. |
| `LOCAL_WAKE_WORD` | `Reachy` | Exact transcript wake phrase in continuous mode. |
| `LOCAL_WAKE_COOLDOWN_SECONDS` | `2` | Duplicate wake suppression window. |
| `LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS` | `5` (maximum `15`) | Bounded wait for one next final command when wake and command arrive as separate local STT segments. Values above 15 fall back to the 5-second default; timeout opens the normal cloud path. |
| `LOCAL_CONTEXT_MAX_CHARS` | `2000` | Bound retained/injected derived text. |
| `LOCAL_TRANSCRIPT_PREVIEW_ENABLED` | `0` | Opt into the loopback-only browser preview at `GET /api/transcript-preview`; retrieval/display adds no cloud traffic and is never included in `conversation.status`. Normal activation may still send bounded derived context to the provider. |

The `conversation.status` JSON-RPC result includes these exact counters:

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
context character count, `transcript_segments_accepted`, activation latency,
wake detections/suppression, `wake_final_update_count`, local command
execution/failure/escalation and latency, last local command outcome and router
reason, router-rejection/follow-up-timeout counters, and both capture/local
processing drop counters. `local_command_last_outcome` is one of
`local_executed`, `router_rejected`, `followup_timeout`, or
`local_execution_failed`; `local_command_last_router_reason` uses the existing
`IntentDecisionReason` values and is null when no router decision exists. These
fields contain counters/enums only, never addressed text.
The local STT request/completion/failure/timeout/drop, latency, in-flight, and
pending-at-activation counters are also returned at the top level. Gemini's
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
only path used for the physical activation acknowledgment; the runtime never calls robot
motor APIs. The manager's worker captures its last commanded full-body pose,
applies a relative nod, and returns to that exact pose.

Before transitioning out of `CLOUD_CONVERSATION`, the runtime synchronously calls
`ConversationHandler.stop_background_work()`. Providers with background cloud work
must stop admitting fragments/requests and invalidate pending results there,
without blocking or awaiting network cleanup. Async `shutdown()` then releases
resources. GPT Live uses this hook for optional
[household memory capture](household-memory.md); other providers inherit a no-op.
`tests/test_openai_live_memory.py` verifies that the fence runs while the runtime
is still in cloud mode, with separate real capture/store and late-result tests.

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
- The transcript preview is an explicitly opt-in diagnostic. Its loopback-only
  HTTP read is marshalled through LocalStream's owner loop, uses the existing
  bounded rolling store, and returns empty/unavailable metadata when disabled,
  closed, or not backed by a local continuous-transcript processor; non-loopback
  clients are denied before a runtime read; normal activation context remains a
  separate, bounded provider input.
- Strategy A never waits for an in-flight continuous STT result during
  activation. Strategy B waits only for its bounded local STT timeout and uses
  keyboard activation because it has no continuous transcript to detect wake.
- LocalStream no longer waits for local frame observation at the capture
  boundary. The runtime worker is sequential and bounded to 100 queued frames;
  overload is fail-closed by dropping newest local work and reporting
  `local_processing_frames_dropped`. This prevents a slow asynchronous VAD/STT
  call from holding the capture loop, but sustained overload can still drop
  local frames and requires target-microphone measurement.
- An addressed transcript suffix is capped at 512 characters and sent only
  after cloud activation; it is never logged. Exact local commands consume the
  suffix locally and never submit it to a provider.
- Strategy A keeps finalized context in text for 60 seconds by default, while
  the raw PCM ring remains capped at 20 seconds and each continuous STT request
  is independently capped at 20 seconds. Activation seeds only transcript
  entries with sequence numbers strictly older than the wake sequence, so later
  ambient text accepted before provider setup cannot become background history.
- The initial RMS VAD is amplitude-based and must be replaced or tuned from
  microphone measurements; it does not solve echo cancellation.
- The architecture SVG was regenerated on 2026-09-16 from `docs/scheme.mmd`.

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
- `tests/test_hybrid_smoke.py`
- `tests/audio/test_sample_timeline.py`
- `tests/audio/test_gstreamer_contract.py`

Transcript preview coverage also lives in `tests/test_console.py`,
`tests/test_hybrid_runtime.py`, `tests/test_config.py`, and
`tests/test_static_contract.py`: default-off parsing/refresh, loopback-only HTTP
access, owner-loop routing, non-loopback denial, empty/bounded/closed results,
payload-free status/logs, and the static Talk/API contract.
`tests/test_config.py` also verifies that explicit process values take
precedence over project `.env` values while unset settings still load.

`tests/test_console.py` exercises the JSON-RPC activation path, owner-loop
marshalling, capture worker, delayed provider readiness, the deterministic
in-flight capture-epoch fence, and LocalStream boundaries with fakes; it does
not exercise a live browser or cloud session. The regression
`test_hybrid_capture_epoch_fences_inflight_frame_and_preserves_fifo` blocks an
SDK pull across activation, asserts that frame stays local, and then asserts
that later provider-bound frames retain FIFO order.
The console regressions also verify that macOS LOCAL skips native playback
clearing while draining the app/provider queue, while WebRTC and non-macOS
LOCAL retain native flushing and older SDKs retain the fallback path.
The hermetic smoke harness composes the real runtime, wake detector, intent
router, and local action executor to exercise all five exact local commands,
split-final exact and delayed/non-exact follow-ups, uncertain addressed-text
escalation, inactivity teardown, repeated provider lifecycle, bounded burst
handling, cloud-byte privacy (including a real fake provider traffic callback),
payload-safe logging, and no-file behavior without credentials or hardware.
Its speaker checks use a fake public output seam; they do not claim native
GStreamer timing or SDK mixer coverage. The optional native child smoke is
documented in [simulator speaker output](simulator-speaker-output.md). The live
simulator/Gemini smoke now supplies process-survival evidence, while audible
quality still requires a human headphone check.
The GI-free sample-timeline tests cover the app-owned output's sample-derived
PTS/DTS/duration and reset contract, but do not replace native sink or live
audible validation.
The GI-free GStreamer contract tests also verify ordered flush events and
fail-closed native rejection handling.
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

On 2026-08-30, live local STT returned HTTP 200 and a configured
`LOCAL_WAKE_WORD=computer` reached the final-transcript detector and local
activation. The request then escalated to Gemini, whose `MoveHead` tool call
moved the simulator; this was not a live local-command pass. The former default
`Reachy` phrase produced no local action because no exact matching final
transcript token reached the detector; this remains historical evidence and is
not a `Tito` quality result.
the runtime status now exposes `local_stt_completed`,
`transcript_segments_accepted`, `wake_final_update_count`, and the other
metadata-only diagnostics needed to distinguish that boundary failure. No raw
transcript or audio is returned by the status RPC.

Three live app runs ended in native macOS GStreamer
`audiomixer0:src` `SIGSEGV` failures after cloud activation, without a Python
exception. As a containment workaround, `LocalStream` skips the SDK
`audio.clear_player()` call only when `sys.platform == "darwin"` and the public
media backend is `MediaBackend.LOCAL`; provider/app queues still drain. This
does not fix the SDK/GStreamer failure: the third run crashed after Gemini's
`MoveHead` tool call and likely first response audio without entering the native
clear path. All three reports share a zero-sample/null-array dereference at
GStreamer `do_convert_out+140` before ORC runs. The app's final public playback
shape is valid, so live SDK playback tests are paused while an explicit
simulator output bypass is implemented. Already-buffered native audio may remain
after barge-in. WebRTC and non-macOS backends retain their native flush behavior.

The explicit bypass is now implemented behind
`AUDIO_OUTPUT_BACKEND=gstreamer` and documented in
[`simulator-speaker-output.md`](simulator-speaker-output.md). It is still
awaiting repeated live Gemini validation; its automated tests do not prove that
a native process crash is resolved.

After adding the diagnostics and macOS containment on 2026-08-30, the full
repository gate passed: Ruff and formatting were clean, strict mypy found no
issues in 54 source files, and all 654 tests passed in 12.28 seconds.
`uv lock --check`, both JavaScript syntax checks, and `git diff --check` also
passed.

The split-final wake-follow-up regressions cover exact local success, uncertain
cloud escalation, timeout, cancellation/close, first-final races,
capture-boundary dual observation, FIFO preservation, and the zero ambient
cloud-byte privacy invariant. These are deterministic fake-boundary tests; no
live split-final latency or local-command acceptance is claimed.

The local-routing regressions distinguish same-final router rejection,
split-final follow-up timeout, and local executor failure using only enum,
reason, and counter metadata. The LocalStream pressure regression holds a local
processor, feeds a paced capture burst, and verifies capture-worker drops stay
zero while admitted local frames remain ordered. Separate runtime regressions
cover bounded local-FIFO drops, cancellation of an active observation, queued
caller release, zero ambient cloud bytes, and status-RPC enum serialization.
On 2026-08-30, the focused hybrid/runtime/console/router/action/main command
passed 205 tests; the dotenv-isolated full command passed 726 tests with one
explicit skip. Ruff, formatting, strict mypy across 59 source files, and
`git diff --check` also passed.

The final 2026-08-30 CI-equivalent gate passed Ruff, formatting, strict mypy
across 54 source files, and all 672 tests in 13.37 seconds. The command used
`REACHY_MINI_SKIP_DOTENV=1` so the ignored live `.env` could not select Gemini
or hybrid mode inside otherwise hermetic tests. Lock consistency, both
JavaScript syntax checks, and `git diff --check` also passed.

The 2026-08-31 ordered-history correction passed 108 focused Gemini/runtime
tests. Final root verification passed Ruff, formatting, strict mypy across 59
source files, 736 tests with one explicit native skip, lock consistency, both
JavaScript syntax checks, and `git diff --check`.

The activation-fence, delayed-local-STT, bounded follow-up, and Gemini reconnect
hardening passed a 217-test focused suite. Final root verification passed Ruff,
formatting, strict mypy across 59 source files, and 761 tests with one explicit
native-audio skip. Independent adversarial review found no remaining P0/P1
blocker. Live microphone dinner-context quality remains pending.

The default-off transcript preview follow-up adds deterministic configuration,
HTTP/runtime, lifecycle, payload-safety, and static UI coverage. The focused
config/console/runtime/static suite passed 212 tests; Ruff, formatting, strict
mypy for the four changed Python modules, both JavaScript syntax checks, and
`git diff --check` also passed. Final root verification passed Ruff,
formatting, strict mypy across 59 source files, and 782 tests with one explicit
opt-in native-audio skip; `uv lock --check` also passed. Independent
adversarial review found no remaining P0/P1 blocker.

## Changelog

| Date | Change |
|---|---|
| 2026-08-31 | Project `.env` loading now preserves explicit process/terminal values and fills only unset values; added regression coverage after the first direct Phase 1 benchmark command was unintentionally routed through hybrid mode. |
| 2026-08-31 | Fenced seeded context strictly before the wake sequence, increased the bounded split-final wait to five seconds, kept text retention independent from raw audio, and hardened Gemini configuration reconnects. |
| 2026-08-31 | Added the opt-in RAM-only transcript preview loopback-only HTTP endpoint and Talk-view panel, routed reads through the LocalStream owner loop, and kept status/log/cloud/persistence payloads clean. |
| 2026-08-31 | Fixed the failed live dinner handoff: Gemini context now uses ordered initial history, and the runtime serializes addressed text before releasing bounded postactivation PCM. Added seed error/cancellation/shutdown and nonblocking ordering regressions; live context-quality retest remains pending. |
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
| 2026-08-30 | `conversation.status` now forwards the complete metadata-only `RuntimeStatusSnapshot`, including accepted transcript-segment and final wake-update counters; RPC regression coverage verifies every field and rejects raw payload keys. Live `computer` wake reached local activation while no exact `Reachy` final transcript token reached the detector. |
| 2026-08-30 | Corrected the live record after logs showed `computer, look left` escalated to Gemini and Gemini performed the movement. Two app runs then ended in native GStreamer `audiomixer0:src` segmentation faults after cloud activation; no Python exception was emitted. |
| 2026-08-30 | Added app-level crash containment for macOS SDK `MediaBackend.LOCAL`: skip native `audio.clear_player()` while preserving provider/app queue draining; WebRTC and non-macOS flushes are unchanged. This is not a proven root fix, may leave already-buffered native audio after barge-in, and still needs live/hardware validation. |
| 2026-08-30 | Root verification after diagnostics and crash containment passed Ruff, formatting, strict mypy, all 654 tests, lock consistency, JavaScript syntax, and diff checks. |
| 2026-08-30 | Added the bounded two-second one-shot wake-follow-up window for split final STT segments, allowing for the existing silence boundary and local STT delay. Exact local success clears the postactivation FIFO; uncertain/timeout cloud paths preserve it. Deterministic lifecycle, capture-boundary, race, cancellation, and privacy regressions pass; live split-final measurements remain pending. |
| 2026-08-30 | Final split-follow-up verification passed Ruff, formatting, strict mypy, all 672 tests, lock consistency, JavaScript syntax, and diff checks with live dotenv loading disabled for test isolation. |
| 2026-08-30 | A third macOS crash proved the native `clear_player()` skip insufficient. All three reports fault before ORC in GStreamer `do_convert_out` with zero samples and null sample arrays. The visible `look left` movement was Gemini's tool call, not local routing, so Phase 5 live acceptance remains pending. Selected an explicit app-owned simulator playback adapter for implementation; no live fix is claimed yet. |
| 2026-08-30 | Implemented the explicit app-owned GStreamer speaker adapter with bounded/leaky queues, generation-safe interruption, fail-closed startup, combined SDK/app shutdown, and order-independent regression tests. The default SDK path is unchanged and live native-crash containment remains unproven. |
| 2026-08-30 | Added metadata-only local routing outcomes/reasons and counters, plus a bounded sequential local-processing FIFO. LocalStream now hands frames off without awaiting slow local observation; deterministic pressure, backpressure, cancellation, close, ordering, and privacy tests cover the new seam. Live microphone/STT timing and sustained-overload rates remain unmeasured. |
| 2026-08-30 | Live `computer, look left` acceptance stayed local: Whisper returned HTTP 200, the runtime transitioned `LOCAL_AMBIENT` → `LOCAL_COMMAND`, executed the existing local movement action, and returned to `LOCAL_AMBIENT` with no Gemini handler construction or connection. One local-processing frame was dropped earlier in the run, so sustained-overload measurement remains pending. |
| 2026-08-30 | Closed a lifecycle race where a late frame could bypass the cleared local queue and still reach the processor; late frames now drop without observation, with regression coverage. Open-runtime direct observation behavior remains covered. |
| 2026-08-31 | Selected `Tito` as the product/default wake phrase. Historical `Reachy` and `computer` live checks remain recorded as prior evidence; the exact-match and no-prewake-cloud boundaries are unchanged. |

### Optional acoustic activation — 2026-09-09

HybridRuntime accepts one optional AcousticWakeWordDetector instead of transcript
wake. Its ordered local worker performs inference off-loop and accepts results
only in the matching ambient epoch. Queue drops and all mode transitions
invalidate acoustic stream history. Accepted events call the existing activation
path with reason acoustic_wake; the current capture barrier excludes wake/prior
audio and normal postactivation FIFO/acknowledgment/inactivity/close applies.
Inference failure disables acoustic wake while preserving manual activation.
This MVP opens the normal conversation directly; it does not reuse the transcript
local-command follow-up path. Status adds acoustic_wake_enabled and selected
detector counters. See wake-word feature docs for config, privacy boundaries,
model provenance and the892-pass/2-skip gate plus opt-in headless SDK test.

## Shared migration validation — 2026-09-16

The final full repository gate passed: **1,247 passed, 3 native opt-in checks
skipped**, with two expected synthetic SDK warnings. Ruff, formatting, strict
mypy, lock validation, JavaScript syntax and diff checks passed. The
[final validation record](../plans/gemini-38-live-extended-thinking-migration.md#final-offline-validation--2026-09-16)
lists exact commands, test inventory, skipped checks and the reproducible next
live-validation step. Gemini spoken-memory parity and real audio/hardware
acceptance remain unverified; the active provider was not switched.


### Bare acoustic wake acknowledgment — 2026-09-21

A bare acoustic wake now requests a brief spoken acknowledgment after the provider
is ready. Local post-barrier speech energy suppresses this request when the user
has already continued speaking; this does not require local STT. Addressed text
retains its existing single-submission path. Silence in the setup FIFO does not
suppress the acknowledgment. The physical nod remains immediate, and startup
greetings remain disabled for other hybrid activation paths.

The acknowledgment uses the existing provider `say()` boundary with a short
synthetic instruction, not a new speech service. It is generation-fenced and
canceled during shutdown. It does not upload pre-wake PCM or alter ambient
privacy. Detection is an energy heuristic, not semantic recognition: noise or the
tail of a wake word may suppress the acknowledgment, and speech starting after
the instruction was submitted may still interrupt it through normal barge-in.
Live acceptance must check bare wake and immediately continued questions.

Validation for the 2026-09-21 follow-up: full `REACHY_MINI_SKIP_DOTENV=1
.venv/bin/pytest tests/ -q` passed (1,263 passed, four opt-in skips, two expected
SDK enum warnings). `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
`.venv/bin/mypy --pretty --show-error-codes` and `git diff --check` passed.
Relevant regression inventory: `tests/test_gemini_live.py`, `tests/test_config.py`,
`tests/test_hybrid_runtime.py`, `tests/test_hybrid_smoke.py`, with existing
`tests/test_openai_live.py`, `tests/test_gemini_sdk_contract.py` and
`tests/tools/test_spotify_control.py`. Live speaker acceptance remains pending.

### Wake reliability and cloud session diagnostics — 2026-10-13

On the Reachy Mini Lite, real "Reachy" attempts often scored lower in the app (0.44,
0.97) than in the guided calibration (0.99 in 9 of 10), which feeds the detector
directly. The local FIFO held only eight 10 ms frames, so an event-loop stall over
80 ms dropped a frame, and every drop advanced the acoustic epoch: queued frames were
discarded and the next frame reset the detector, which then scored nothing for about
1.3 s. Drops were logged only at 1, 100, 200, …, so most went unseen. The FIFO now
holds about one second, drops no longer reset the detector, and each drop burst logs
one warning. Mode transitions still advance the epoch and reset it.

The same run showed cloud sessions after a bare wake (the user paused after
"Reachy") that registered no user speech for 26 s, or not at all before the 60 s
inactivity close, while sessions where speech followed the wake directly worked. Each
cloud session now ends with one metadata-only `Cloud session summary` line: reason,
audio KiB and video frames the provider confirmed sending, frames queued behind
provider setup or the acknowledgement and still queued, the acknowledgement state
(`not_needed`, `skipped_speech_resumed`, `skipped_not_ready`, `sending`, `sent`,
`failed`), whether the acknowledgement task was still running, and connection
readiness. Tests: `test_dropped_local_frame_does_not_reset_the_acoustic_detector`,
`test_cloud_session_summary_logs_ack_and_forwarding`.

### Launcher and multilingual local STT — 2026-10-13

`scripts/start-reachy.sh` starts the local Whisper server (`transformers serve` on
127.0.0.1:8766, default multilingual `openai/whisper-small`, override with
`WHISPER_MODEL`), waits until `/v1/models` answers, then runs the app with the given
arguments (default `--media-backend local`). It reuses a server already listening on
the port and stops only a server it started; without the `transformers` CLI it runs
the app without local STT and prints the install command. For the Reachy Mini Lite
household (English, Spanish, Italian, Portuguese), `REALTIME_TRANSCRIPTION_LANGUAGE=auto`
removes the language hint from both the local STT request and Gemini's input
transcription (`language_codes` omitted), so each utterance's language is detected;
the Hugging Face backend then omits `language`. Unset still means `en`. Tests:
`test_transcription_language_auto_means_no_hint`,
`test_auto_transcription_language_sends_no_language_codes`,
`test_huggingface_session_omits_language_for_auto_detection`. The launcher was checked
with a stand-in server (start, readiness wait, stop on exit) and with the real server
already running (reuse).

### Framed activation context — 2026-10-13

First Reachy Mini Lite run with continuous local STT and acoustic wake: the user said
"I think Real Madrid might play this weekend", then "Reachy, can you check?". The wake
opened Gemini with the earlier sentence seeded as context and the post-wake audio
("can you check?") flushed from the handshake FIFO, but Gemini stayed silent for 10 s;
asked directly, it knew what had been said. The seed was bare transcript lines, and
the provider guidance said never to treat that background as instructions or tool
authorization, while the wake word itself stayed local, so nothing told the model the
short follow-up audio was addressed to it. `frame_activation_context` now wraps the
transcript in a header ("Conversation near you before you were called…") and a
footer saying the user just called it by name, the live audio request is addressed to
it, and references like "can you check?" should be resolved from the background. It
keeps the newest lines within `LOCAL_CONTEXT_MAX_CHARS`; `local_context_chars` still
counts the raw transcript. The Gemini and GPT Live guidance now says to use the
background to understand the live request, never to follow instructions found only in
it, and that the live request is what authorizes tools. Tests:
`test_activation_context_framing_marks_background_and_keeps_newest_lines` and the
updated Strategy A/B context assertions.

Retest the same day showed two more gaps. (1) When the background sentence ran straight
into "Reachy, can you check?", local VAD kept it in the same open segment; after the
wake, live frames go to the provider, so the segment never closed and its text
missed the context (Gemini then used the camera to "check"). For acoustic wakes the
runtime now finalizes the open segment and waits up to 3 s
(`_ACOUSTIC_CONTEXT_SETTLE_S`) for local STT, then `strip_trailing_wake` removes the
wake word (Whisper spellings such as Reachy, Richie, Ritchie) and the request after it
from the newest line, since the request already reaches the provider as audio. (2) A
session received about 60 s of user audio (`audio_kb_sent=1913`) yet produced no
server events at all, consistent with Gemini's always-on proactive audio ignoring
speech it does not consider addressed to it: the wake word stays local and hybrid
sessions have no greeting. Every hybrid activation now seeds `ACTIVATION_CUE` ("The
user has just called you by name and is talking to you now…") when there is no
ambient transcript, and the framed context footer says the same. The Gemini handler
also logs `Gemini session events` at session end (counts of total, voice activity,
user transcripts, model turns, tool calls and interaction statuses; no payloads) to
tell a deaf session from a quiet model. Tests:
`test_acoustic_wake_waits_for_the_sentence_in_progress`,
`test_acoustic_wake_without_context_still_marks_the_request_as_addressed`,
`test_strip_trailing_wake_keeps_only_the_background`,
`test_session_end_logs_server_event_counts`.

Second retest: the context arrived, but Gemini still ignored the post-wake audio
("can you check?") for 16 s. With continuous local STT, an acoustic activation now
closes the open speech segment synchronously (`AmbientContextProcessor.close_open_segment`,
the sentence ending in "Reachy" becomes background), arms the existing one-shot wake
follow-up waiter after `latest_sequence`, waits up to 3 s for that segment's STT
(`drain`, no longer `flush`, so the request segment is not cut), then waits up to
`LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS` for the next final local segment. That segment,
trimmed of a leading wake word or its tail (`strip_leading_wake`), becomes the
addressed text submitted with `say()` after readiness; the wake acknowledgement is
cancelled and the queued post-wake audio is discarded so the request is not heard
twice. If no segment arrives in time, the previous audio path (acknowledgement, then
the queued audio) is unchanged. The transcript observer is now registered whenever a
context processor exists, not only with transcript wake. Tests:
`test_acoustic_wake_sends_the_transcribed_request_as_text`,
`test_strip_leading_wake_cleans_the_request`.

Third retest: the wake fires about a second after "Reachy", so "can you check?" was
already inside the segment closed at the wake; the runtime waited 5 s for a separate
follow-up that never came. The request is now taken from that segment first
(`request_after_wake`: text after the last wake-word match, keeping its punctuation),
and the follow-up wait is used only when nothing followed the wake. The same run's
provider setup failed with a `RuntimeError` that was the setup cleanup masking the
original error; `_connect_with_timeout` now logs `Gemini setup failed error=…
detail=…` (bounded) before cleanup. Tests:
`test_acoustic_wake_takes_the_request_from_the_wake_segment_without_waiting`,
`test_request_after_wake_returns_the_suffix`.

Fourth retest: provider setup failed with `APIError 1011 … prepayment credits are
depleted` (the account's Gemini API credit), which the new setup log exposed; the
previous run's masked `RuntimeError` was likely the same. The run also waited the full
follow-up timeout although the wake segment was transcribed 0.4 s after the wake,
suggesting local STT spelled the wake word in a way the pattern missed. The pattern
now also accepts reachi, reechie, richy, richi, ritchy, rickie, ricci and riichi, and
each acoustic activation logs `Acoustic wake request source=wake_segment|followup|none
request_chars=… wake_word_in_segment=…` (metadata only) to show where the request came
from.

Fifth retest (credits restored): `source=none wake_word_in_segment=False`, and a
"Local processing queue full" warning exactly one second after every wake. Root cause:
the local processing worker awaited `activate("acoustic_wake")` itself, so during the
whole activation (STT settle, follow-up wait, provider construction) no frame reached
local VAD/STT; the follow-up could never be transcribed and the 100-frame FIFO
overflowed after one second. Acoustic activation now runs in its own task
(`hybrid-acoustic-activation`). Request selection is also simpler and no longer depends
on how Whisper spells the wake word: if a segment was open at the wake
(`has_open_segment`), its whole text (for example "I think Real Madrid might play this
weekend. Reachy, can you check?") is the addressed text, and the ambient context is
fenced before it; when a recognized wake word ends that segment, or no segment was
open, the next final segment is waited for and appended. Queued post-wake audio is
dropped only when the request is known to be in the text (wake word recognized or a
follow-up used). Each acoustic activation logs `Acoustic wake request
wake_segment_chars=… wake_word_found=… followup_chars=…`. The earlier wake-stripping
helpers were removed. Tests: `test_acoustic_activation_does_not_stall_local_processing`
(fails with the old awaited activation),
`test_acoustic_wake_sends_the_utterance_with_the_request_at_once`,
`test_acoustic_wake_appends_the_followup_when_nothing_followed_the_wake`,
`test_acoustic_wake_sends_the_utterance_even_if_stt_misspelled_the_wake`.

Robot retest of the above: the first wake answered with context. On the second, the
context sentence had already closed (a short pause before "Reachy"), and the segment
open at the wake held only two characters, a fragment of the wake word, which was
sent alone as the request. A wake segment of at most `_WAKE_ONLY_MAX_WORDS` (2) words
is now treated as the wake word: the runtime waits for the next segment and sends both;
if none arrives, the fragment is not sent and the acknowledgement plus queued audio
path is used. Tests:
`test_acoustic_wake_treats_a_short_segment_as_the_wake_word_and_waits_for_the_request`,
`test_acoustic_wake_with_only_a_fragment_and_no_request_falls_back_to_audio`.

Next retest: "…this weekend, Reachy, can you check?" never woke the robot (acoustic
peaks 0.80 and below 0.2): official33 scores "Reachy" lower inside running speech than
spoken alone, and ordinary talk already reaches 0.84–0.88, so the threshold cannot
drop. With continuous local STT the transcript wake detector now runs alongside
acoustic wake as a backup (the "select one wake detector" restriction is removed;
`activate()` admits only one activation). It accepts `REACHY_TRANSCRIPT_ALIASES`
(reachie, reechy, richie, richy, ritchie) as whole words when the wake word is Reachy.
For a mid-sentence transcript wake, the whole sentence is the addressed text; when
the wake ends the sentence, that sentence stays in the context (fence after it) and
the follow-up is the request; a sentence starting with the wake word keeps the
existing suffix behaviour, so exact local commands are unchanged. Tests:
`test_transcript_wake_mid_sentence_sends_the_whole_sentence_alongside_acoustic_wake`,
`test_transcript_wake_aliases_match_whole_words_only`.

Non-Latin replies (2026-10-13): with `REALTIME_TRANSCRIPTION_LANGUAGE=auto`, multilingual
Whisper sometimes turns a short or noisy segment into invented Chinese or Korean text.
After a wake that text became the addressed request, and Gemini (told to match the
user's language) answered in Chinese or Korean. `LOCAL_STT_LATIN_ONLY=1`
(`AmbientContextConfig.latin_script_only`) drops a continuous STT result when less than
80% of its letters are Latin script (`is_mostly_latin`), logging only that it was
dropped. The household profile now names English, Spanish, Italian and Portuguese and
tells the model to treat other languages as mishearing. Tests:
`test_is_mostly_latin_accepts_household_languages_and_rejects_cjk`,
`test_latin_only_drops_non_latin_stt_hallucinations`.

### Long conversations and local backlog — 2026-10-14

A nine-minute conversation on the Reachy Mini Lite ended with `Hybrid provider task
failed: APIError` (`provider_failed`): the Gemini handler's nine-minute connection
limit was final, so the conversation stopped. The handler now rotates the connection
and resumes it from the session handle (see the Gemini Live provider doc), so the
hybrid runtime only sees a short disconnect: frames go to the post-activation buffer
and flush on reconnect, and the activation text is not sent again. Provider failures
now log the error's type and its bounded status text (at most 200 characters), not
just the type.

Right after that session ended, the local worker dropped more than 1,000 frames over
20 seconds, so a wake word in that window was likely missed. With the Mac heavily
loaded (load average around 45 on 10 cores), the wake model took about 8 ms per
16 ms frame and each frame needed two thread hops (wake model, then VAD), which
together exceeded real time. The worker now takes any queued backlog in one pass
(up to 25 frames) and merges consecutive frames with the same sample rate and wake
epoch into one mono frame (`_merge_local_items`), so the wake model and VAD each run
once per batch; a lone frame passes through unchanged. Tests:
`test_local_processing_backlog_is_merged_into_one_pass`,
`test_merge_local_items_keeps_lone_frames_and_splits_on_rate_or_epoch`,
`test_provider_setup_failure_returns_to_ambient_and_shutdowns_once`.

### Ending conversations and standby — 2026-10-17

Reachy kept joining side conversations because any speech reset the 60-second
inactivity close, so a cloud session stayed open while people talked among
themselves and only Gemini's proactive audio kept him quiet. Research on the same
problem: Alexa Follow-Up Mode listens about 5 s after a reply, Google Assistant
about 8 s, and Gemini on Google speakers up to about 15 s. Apple's follow-up
device-directed speech detection judges the next utterance against the previous
exchange. Google Look and Talk and Alexa Conversation Mode add camera head
orientation.

A conversation now ends in one of these ways, checked in this order by
`check_inactivity`:

1. **Reachy asks to close it.** The `end_conversation` tool (a goodbye, or people
   clearly talking among themselves) and `go_to_sleep` call `request_close`. The close
   waits at least 1 s and until his reply has played and the provider is no longer
   busy, at most 12 s.
2. **The addressee check says it was not for him.** After a reply, user transcript
   pieces since that reply are joined. Once there are at least 4 words and a 1.5 s
   pause, `AddresseeCheck` sends Reachy's last reply and that text to
   `OPENAI_LIVE_BACKEND_MODEL`, re-checking after 4 more words and at most 3 times per
   stretch. A clear `no` closes the session at once, unless Reachy has started
   answering by then. `yes`, `unsure` and errors change nothing. The check cannot veto
   Gemini's own replies, because Gemini answers straight from the audio. The same text
   already reaches OpenAI when memory capture is enabled.
3. **Music wind-down** (unchanged).
4. **The follow-up window** (`HYBRID_FOLLOW_UP_SECONDS`, 15 s), outside music:
   - The session closes 15 s after Reachy's reply ends if nobody spoke.
   - When people speak and he does not answer for 15 s, it closes at the next 2 s
     pause, or at 30 s regardless.
   - A busy provider (thinking, a tool) holds the close up to 30 s.
   - Each reply restarts the window.
5. **The inactivity timeout** (unchanged) is the backstop.

The monitor task now always runs, so requested closes work even with both timeouts
disabled.

`go_to_sleep` is now standby. With the hybrid runtime, `ToolDependencies.standby`
calls `MovementManager.go_to_sleep()` and `request_close("go_to_sleep")`. The movement
manager stops tracking and other moves and eases into the SDK sleep pose
(`PoseTransitionMove`, held until cleared). While asleep it ignores queued moves,
speaker turns, listening and tracking, and closing the conversation keeps the pose.
An activation acknowledgement, a new conversation or `wake_up()` raises the head to
neutral first. Without the hybrid runtime, `go_to_sleep` still stops the app.

Asleep, waking is stricter. The first live test woke him repeatedly on ordinary
conversation: the acoustic model scored that talk 0.95–0.99, as high as real wakes,
and the transcript detector caught people talking about him. `request_close("go_to_sleep")`
sets `_asleep`. While it is set:
- An acoustic wake does not acknowledge, so the head stays down, until local STT
  confirms the name opened the utterance or one of its sentences (`_name_leads`:
  "Reachy, ...", "hey Reachy", "ok. Reachy, ...").
- Without that confirmation the runtime returns to ambient
  (`unconfirmed_wake_while_asleep`) without moving or opening the cloud.
- A transcript wake needs the name first in the same way.
- Any confirmed wake, or a keyboard activation, clears `_asleep`. After that chat
  he stays awake in the idle pose.

The new `turn_off` tool keeps the old sleep-and-stop behavior, and the shutdown path
(`run_go_to_sleep_tool`) now uses it. With the hybrid runtime, `end_conversation`
and `turn_off` load next to `go_to_sleep` unless the profile's tools were
customized. The household profile tells Reachy to stay out of side conversations
and when to use each tool.

Tests:
- `test_follow_up_window_closes_15_s_after_reachys_reply`
- `test_side_conversation_closes_the_follow_up_window_at_a_pause`
- `test_follow_up_window_waits_while_reachy_is_working_and_restarts_after_his_answer`
- `test_a_requested_close_waits_for_the_goodbye_to_play`
- `test_addressee_check_closes_early_when_people_talk_among_themselves`
- `test_addressee_verdicts_that_keep_him_or_come_too_late_do_not_close`
- `test_sleep_holds_the_pose_until_an_activation_wakes_him`
- the `asleep` wake tests and `test_name_leads_only_when_the_name_opens_a_sentence`
- `tests/test_addressee_check.py`
- the standby tool tests in `tests/tools/test_go_to_sleep.py`

### Whisper jammed by background chatter — 2026-10-18

With the kids playing nearby, "Reachy" stopped waking him: the log showed `Continuous local
STT backlog full; dropped newest segment`, `timed out`, and many `not in Latin script`
results. `transformers serve` lets Whisper generate up to 448 tokens per clip and works on one
clip at a time; on noise or overlapping voices Whisper invents long text (8 s of noise took
26 s), so clips queued behind it and the one with the name was dropped or timed out. The client
timeout does not stop work the server has already queued.

The launcher now runs `scripts/whisper_server.py` (same endpoint and port, with the
`transformers` uv tool's Python; `WHISPER_SERVER=transformers` restores `transformers serve`).
It transcribes everything, kids talking to Reachy included, but bounds the work per clip:
- a token budget of 16 + 10 per second of audio (fast speech uses about 4–6 a second);
- a clip that hits the budget, usually Whisper looping on one voice, is decoded once more with
  `repetition_penalty=1.3`, which recovered a request said over kid chatter ("I want the red one.
  Can you turn on lights in a dining area, please?") that greedy decoding had turned into one
  sentence repeated;
- the language is chosen among `WHISPER_LANGUAGES` (default `en,es,it,pt`; `auto` for any)
  unless the request gives one;
- a clip whose client disconnected while queued is skipped.

Measured on the same machine: 8 s of noise 26 s → 0.3 s; English and Spanish commands unchanged
(0.3–0.5 s); overlapped speech 1.6 s with the retry. Each clip logs one line
(`Transcribed audio_s=… language=… tokens=… capped=… took_s=… waited_s=…`, no text).

### Quick commands before the cloud — 2026-10-18

Everyday requests (lights, music, TV, time, date, weather, sleep) are answered in `LOCAL_COMMAND`
without building the cloud handler; see [quick-commands.md](quick-commands.md). `_try_quick_command`
runs once the request text is known, plays the reply through `emit()` while no handler exists, and
stays in `LOCAL_COMMAND` until it has played. Unmatched requests open Gemini as before.
