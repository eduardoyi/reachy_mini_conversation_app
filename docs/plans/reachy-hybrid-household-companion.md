# Reachy Hybrid Household Companion — Implementation Plan

- Status: `in-progress`
- Active phase: `Phases 3–5 implementation; Phase 1 benchmark completion deferred`
- Owner: Reachy project
- Created: 2026-08-29
- Last updated: 2026-08-29
- Scope: Phases 0–5
- Required final report: [`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md) at the repository root

## Goal

Make Reachy feel like a small persistent household character while keeping the
robot locally awake without keeping Gemini connected. Local processing handles
ambient listening, activation, short context, simple commands, movement, and
privacy boundaries. Gemini Live is opened only for an addressed or genuinely
complex interaction.

## Non-goals for phases 0–5

Do not implement richer SQLite memory, Home Assistant, heartbeat/proactivity,
face or speaker recognition, a local LLM, local TTS, the complete Hugging Face
speech-to-speech conversation stack, an elaborate frontend, Hermes, LangChain,
multi-agent orchestration, or vector databases.

The existing remember/forget persistence remains local and is preserved. The
provider must not own persistent memory.

## Current repository versus target

### Current main (what exists now)

The official app is a realtime Hugging Face application. `main.py` builds one
`HuggingFaceRealtimeHandler`, injects it into `console.LocalStream`, and shares
`ToolDependencies` with the existing tools and `moves.MovementManager`.
`ConversationHandler` is the provider contract for startup, shutdown, audio
receiving, personality, voice, speech, activity, transcripts, and idle work.
Camera access is an on-demand tool; profiles are `profile.md` files; tools are
local Python tools or MCP Tool Spaces. The current app starts its selected
realtime backend rather than running an idle local ambient state.

The current architecture and diagram remain authoritative until the planned
changes land. The existing realtime backend must continue working throughout
the migration.

### Target for this plan

Use one existing local microphone/speaker plumbing path and one active provider
session, with an explicit privacy boundary around a small runtime state
machine:

```text
LocalStream — existing microphone/speaker loop
       │
       ▼
HybridRuntime — state machine and privacy boundary
       ├── LOCAL_AMBIENT
       │     RAM ring → local VAD/STT/wake only
       │
       ├── LOCAL_COMMAND
       │     deterministic allowlisted intents → existing local tools
       │
       └── CLOUD_CONVERSATION
             one GeminiLiveHandler
                    ↓
             existing tools
                    ↓
             existing MovementManager
```

The hybrid runtime composes one `ConversationHandler`; it does not wrap one
handler inside another. This avoids duplicate audio queues, tool managers,
idle timers, and observer chains. LLMs never issue raw motor commands.

## Operating modes and privacy invariants

| Mode | Cloud session | Local work | Allowed cloud traffic |
|------|---------------|------------|-----------------------|
| `LOCAL_AMBIENT` | Closed | VAD, wake detection, 20-second RAM ring, optional local STT, short local transcript | None; ambient audio/video bytes sent must be zero |
| `LOCAL_COMMAND` | Closed | Deterministic command classification and local acknowledgment | None unless classification escalates to cloud |
| `CLOUD_CONVERSATION` | One direct Gemini Live session | Current audio, context injection, local tools and movement | Current conversation audio and explicitly requested camera frames |

While idle, raw audio is continuously overwritten in RAM, never written to
disk, and never uploaded merely because it is available. The session closes
after configurable conversational inactivity. The runtime logs every mode
transition and tracks:

- `ambient_audio_seconds_buffered`
- `ambient_audio_bytes_sent_to_cloud`
- `cloud_audio_bytes_sent`
- `cloud_video_frames_sent`
- `gemini_session_duration`

The mandatory invariant is
`ambient_audio_bytes_sent_to_cloud == 0` throughout `LOCAL_AMBIENT`.
The RAM-only design does not control operating-system swapping; no audio files
or payloads are created by the application and audio is never logged.

## Cloud provider design

Gemini Live is the first cloud provider. Phase 1 now has a provider adapter
implementing the current `ConversationHandler` contract; it does not downgrade
or fork an old version of the whole app to recover tutorial code.

The direct rich-conversation path is:

```text
microphone → existing realtime audio plumbing → Gemini Live → speaker
                                      ↘ existing local tools/movement
```

Preserve realtime interruption/barge-in, transcripts, tool calls, camera tools,
speech-reactive movement, profiles, emotions, dances, head tracking, logging,
UI integration, and existing local memory behavior. Tools execute alongside the
audio path and do not become a second agent layer.

The adapter bounds only the Live setup handshake to 15 seconds; the ongoing
receive loop is not given that setup timeout. It exposes a bounded
`GeminiLatencySnapshot` with numeric monotonic-millisecond samples for
connection-ready, first-audio, tool-response, and interruption-flush metrics,
retaining at most 32 samples per metric. These are privacy-safe diagnostics, not
measured live benchmark results.

Parallel function calls run through the shared tool manager and return in the
provider's original call order in one function-response message. Cancellation
stops the cancelled local tool and flushes completed siblings so a finished
result is not stranded. Camera bytes are sent as an image input before the
function response, while transport-only base64 is removed from the
model-visible JSON result.

The Gemini adapter must not upload pre-activation ambient audio. It receives
only the locally-derived context that the runtime explicitly injects, followed
by live post-activation audio.

## Local processing design

Use small interfaces so VAD/STT implementations can be benchmarked and
replaced without changing the state machine. Initially local work is limited
to VAD, activation/wake word, short ambient transcription, and deterministic
command routing. The full Hugging Face speech-to-speech stack is a reference
for modular boundaries, not an immediate dependency or deployment target.

### Ambient context strategies

Benchmark both strategies before choosing the default:

- **Strategy A — continuous transcript:** VAD-scoped local STT maintains a
  short rolling transcript. Activation injects the bounded transcript into a
  new Gemini session.
- **Strategy B — buffer transcription:** only the RAM audio ring is retained;
  activation snapshots and locally transcribes recent audio before creating or
  informing the Gemini session.

Compare activation latency, time to Gemini connection, first response audio,
local STT CPU, memory, transcript quality, and stale/context ordering. Prefer A
if continuous local STT resource use is acceptable. Neither strategy uploads
raw pre-wake ambient audio.

### Activation and command policy

The initial wake phrase is “Reachy”. Keyboard activation is required first so
voice UX can be tested independently of wake-word quality. Activation must
locally acknowledge immediately, then open Gemini and provide context.

Simple commands should not start Gemini. V0 uses a deterministic
`LocalIntentRouter` for “stop”, “be quiet”, “look at me”, “look left”, and “look
right”. Successful local actions may use a nod, antenna movement, or another
short acknowledgment without TTS. Uncertain, malformed, unavailable, or
non-allowlisted requests always escalate to Gemini rather than guessing.

The exact cloud-opening decision window is a measured design point: activation
must feel immediate, while local-command recognition must have a brief chance
to run without opening Gemini. Buffer only the minimum post-activation audio
needed for this decision.

## File extension points

Extend existing abstractions first. The following are planned touch points, not
permission to refactor unrelated code:

| Existing file | Planned responsibility |
|---------------|------------------------|
| `src/reachy_mini_conversation_app/conversation_handler.py` | Add typed provider hooks for connection readiness, context injection, and interruption while preserving the handler contract. |
| `src/reachy_mini_conversation_app/main.py` | Provider factory and hybrid runtime wiring. Keep current startup and SDK lifecycle intact. |
| `src/reachy_mini_conversation_app/console.py` | Generalize HF-only startup/UI assumptions, activation controls, asynchronous interruption, and state/metrics reporting. |
| `src/reachy_mini_conversation_app/huggingface_realtime.py` | Implement new shared hooks without changing existing Hugging Face behavior. |
| `src/reachy_mini_conversation_app/moves.py` | Add only a small queue-driven local acknowledgment if existing moves cannot express it. |
| `src/reachy_mini_conversation_app/tools/core_tools.py` | Reuse the existing dispatcher and `ToolDependencies`; do not create a parallel tool framework. |
| `src/reachy_mini_conversation_app/prompts.py` and `memory.py` | Preserve current profile/memory injection; keep ambient context separate from persistent memory. |
| `.env.example` | Document the real `REALTIME_PROVIDER` and Gemini variables now; add hybrid, VAD/STT, wake, buffer, inactivity, and benchmark settings as they become real. |
| `README.md` and `docs/scheme.mmd` | Keep installation, CLI/configuration, and the architecture source synchronized. Regenerate the SVG only through the existing diagram workflow. |
| `pyproject.toml` and `uv.lock` | Add only the selected Gemini and local-processing dependencies; keep the lockfile synchronized. |

New focused modules, added only when their behavior is needed:

- `src/reachy_mini_conversation_app/gemini_live.py`
- `src/reachy_mini_conversation_app/hybrid_runtime.py`
- `src/reachy_mini_conversation_app/ambient.py`
- `src/reachy_mini_conversation_app/local_processing.py`
- `src/reachy_mini_conversation_app/local_intent_router.py`
- `profiles/household_companion/profile.md`
- matching tests under `tests/` mirroring the source layout

`LocalDecisionModel` is an interface only in these phases. It is not required,
and deterministic routing remains the default.

## Phases and acceptance criteria

### Phase 0 — unchanged baseline

Purpose: establish that the official app works before changing functionality.

- [x] Fork/clone the current official Conversation App into this repository.
- [x] Read the app and SDK `AGENTS.md` files completely.
- [x] Read the current README, architecture source, Gemini tutorial, and
  relevant HF speech-to-speech VAD/STT material.
- [x] Record current architecture and known provider assumptions in this plan.
- [x] Install exact locked dependencies using the documented setup. On native
  ARM Python 3.12.13, `uv sync --frozen --group dev` succeeded with 121 locked
  packages and did not change `uv.lock`.
- [x] Install the optional simulator group with
  `uv sync --frozen --group dev --group sim`; the locked `reachy-mini[mujoco]`
  extra supplied MuJoCo 3.3.0 for rendered simulator validation.
- [x] Run all existing tests and the full upstream gate. The initial pytest run
  exposed three avatar failures because Git LFS was absent and the SVG files
  were pointer text; after installing and initializing Git LFS 3.8.0 and
  pulling the tracked assets, the focused avatar suite passed 9 tests and the
  full gate passed 320 tests.
- [x] Start the official Reachy simulator and verify simulated movement. The
  mockup daemon API and SDK movement test changed body yaw from 0 to 0.349 rad
  and changed the antennas in 0.754 s. Real MuJoCo with media also started
  successfully through `mjpython` and its API was healthy.
- [x] Run the unchanged Conversation App with Mac microphone and speaker or
  headphones; verify audio, tools, camera path where available, and shutdown.
- [x] Record baseline startup latency and reliability evidence. The unchanged
  app connected to the daemon and Hugging Face, loaded all 17 tools, recorded
  from the Mac default microphone at 16 kHz, received response-audio delta and
  done events, took approximately 5.2 s from app launch to startup response,
  muted the microphone once the UI was available, and shut down cleanly.
- [x] Manual smoke confirmed that conversation, audio, and movement worked well
  until acoustic feedback caused the assistant's `Exacto!` to be heard by the
  Mac microphone as repeated user `Exacto.` turns. Twenty-five barge-in flushes
  occurred over approximately 86 s; this indicates speaker-to-microphone
  echo/AEC weakness, not a frozen process.
- [x] Safely renamed `.env.local` to the Git-ignored `.env` with permission mode
  `600`; the Gemini key value was not exposed or recorded.

**Phase 0 status: complete.** Baseline audible perception and end-of-user-speech
latency were not measured; those are explicit Phase 1/manual follow-up work.
The baseline does not prove hybrid ambient privacy: the unchanged app opens a
deployed Hugging Face session and the microphone was muted only after the UI
became ready, so no claim is made about pre-mute ambient bytes.

**Gate:** stop implementation if the unchanged upstream app cannot run
reliably in simulation and on the development machine.

### Phase 1 — direct Gemini Live benchmark (`in-progress`)

Purpose: establish cloud performance before adding local layers.

- [x] Add a validated `REALTIME_PROVIDER` selector with Hugging Face as the
  default, `gemini` as the opt-in value, and explicit provider-neutral readiness
  and missing-configuration helpers. Add the local Gemini model/voice defaults
  without recording credentials.
- [x] Refactor the main handler factory to select Hugging Face or Gemini
  explicitly, import only the selected adapter, preserve startup voice behavior,
  and reject unknown providers.
- [x] Complete the `GeminiLiveHandler(ConversationHandler)` adapter and direct
  runtime integration, including a 15-second setup-only timeout and bounded
  numeric latency diagnostics.
- [x] Keep the Mac microphone → Gemini Live → speaker path direct while
  preserving simulated Reachy motion, existing tools, transcripts,
  interruptions, and camera-on-demand behavior. Provider-neutral runtime
  handling and the shared linear resampler are complete.
- [x] Complete fake-session tests for every Gemini event and failure path,
  provider-neutral runtime tests, resampler tests, and the automated repository
  gates.
- [ ] Measure activation/start to connection, end-of-speech to first response
  audio, tool-call latency, and interruption latency.
- [ ] Run the benchmark with both Mac speakers and headphones, recording the
  input/output devices and condition for every sample; use headphones as the
  immediate control for the observed acoustic feedback.
- [ ] Observe and record assistant-to-microphone echo loops, repeated
  transcripts, and barge-in flush counts in each output condition. Verify
  process/event liveness separately so an echo/AEC failure is not classified as
  a frozen process.

**Acceptance (pending benchmark):** natural conversation should be compared with
the reference Google demo, and the existing HF provider must continue to pass
its tests. User-observed qualitative conversation evidence is not a substitute
for reproducible instrumented first-audio, interruption, or end-to-end latency
measurements; those remain required before acceptance.

**Phase 1 implementation status:** configuration, the provider factory, Gemini
adapter, provider-neutral runtime behavior, shared resampler, and automated
tests are complete. The real simulator/Gemini session benchmark remains in
progress; no reproducible quantitative first-audio, interruption, or end-to-end
latency claim is made.

**User-observed Phase 1 qualitative evidence (2026-08-29):** before the later
native-audio wedge, a user manually completed one long, responsive Gemini
conversation over the SDK LOCAL backend, including a successful
barge-in/interruption. This is qualitative user observation, not an instrumented
first-audio or interruption-latency result; reproducible quantitative benchmark
evidence remains incomplete. The Phase 0 `Exacto!` feedback loop is a separate
baseline speaker-to-microphone echo observation and must not be conflated with
this Phase 1 LOCAL success. WebRTC was a later fallback, and its
`not-negotiated (-4)` failure does not invalidate the earlier LOCAL conversation.

**Verified automated evidence (2026-08-29):** the focused configuration/main
suite passed 27 tests, the Gemini fake-session suite passed 26 tests, the
console suite passed 51 tests, and the combined console/streaming suites passed
61 tests. The exact full gate (`ruff check .
--fix`, `ruff format .`, strict mypy, and `pytest tests/ -v`) passed with Ruff
clean, format leaving 81 files unchanged, mypy clean across 46 source files,
and 392 tests passed in 7.64 s. `uv lock --check`, `node --check settings.js`,
`git diff --check`, and the secret-pattern scan over repository files excluding
the ignored `.env` also passed. Privacy sentinel tests cover console
`AdditionalOutputs`, Gemini, Hugging Face transcripts/tools/errors, camera,
memory, idle reason, and background progress. These are automated results only;
real simulator/Gemini audio, tool, interruption, and latency evidence remains
pending.

**Live validation evidence (2026-08-29):** the latest WebRTC attempt first hit a
producer-startup race (`KeyError`) until the producer registered. A retry chose
WebRTC and initialized the bidirectional client, reaching
`connection_ready_ms=640.8` and greeting-tool timings of 231.6 ms and 230.9 ms,
but the SDK reverse-audio send queue then failed with `not-negotiated (-4)`. No
complete WebRTC conversation, first-audio, or interruption benchmark was
obtained in that attempt.
The current Reachy SDK main still contains the same send-chain implementation,
so the WebRTC workaround is not validated and should not be presented as
expected to work on this Mac. Disk-pressure cleanup left 19 GiB free, and the
Python runtime was rebuilt for arm64. The earlier 545.7 ms and 270.8/274.2 ms
samples remain historical only. Per the baseline reliability rule, stop further
live work pending an explicit choice between a clean reboot/local retest and a
simulator-only native macOS audio adapter or upstream SDK fix. The bounded
daemon capture worker retains queue 8, 5 ms empty polling, oldest-frame dropping
on overflow, a 100 ms stop join, and metadata-only diagnostics covered by
privacy tests.

The current local forensic snapshot found one daemon and four conversation-app
processes in uninterruptible `UE` state, including two `PPID 1` orphans; four
clients still showed connections to the daemon, and PID 92880 still listened on
7860. This is likely dirty local runtime state that may explain the later LOCAL
wedge, not a proven root cause.

### Phase 2 — hybrid state machine

Purpose: make local ambient listening independent from cloud connectivity.

- [x] Add explicit `LOCAL_AMBIENT`, `LOCAL_COMMAND`, and
  `CLOUD_CONVERSATION` states with logged transitions.
- [x] Keep Gemini disconnected in `LOCAL_AMBIENT` and `LOCAL_COMMAND`.
- [x] Add the 20-second RAM PCM ring, local VAD boundary, keyboard activation,
  inactivity shutdown, privacy counters, and local physical acknowledgment.
- [x] Start cloud only after activation unless the deterministic local route
  succeeds.
- [x] Suppress automatic provider greetings on activation when they would make
  the robot unnecessarily chatty.
- [x] Reserve the capture epoch before each potentially blocking microphone
  pull and snapshot that epoch atomically at activation, so an in-flight
  preactivation frame cannot cross the cloud boundary; retain FIFO ordering for
  true postactivation frames.
- [x] Add a five-minute ambient soak test proving zero cloud audio/video
  traffic and a clean session close.

**Phase 2 status: complete (2026-08-29).** Configuration, environment
documentation, provider-neutral traffic/connection/greeting hooks, movement
acknowledgment primitive, browser activation binding, architecture source,
hybrid runtime, and deterministic lifecycle/privacy tests are implemented.
The integration suite covers the LocalStream loop boundary, capture-worker
activation barrier (including an in-flight pull), inactivity while frames
continue to arrive, provider failure/cancellation, delayed provider readiness,
owner-loop RPC scheduling, and provider-rate conversion. The exact repository
gate is clean: Ruff passed,
formatting left 87 files unchanged, strict mypy passed across 49 source files,
and pytest passed all 474 tests. `uv lock --check`, JavaScript syntax checks,
and `git diff --check` also passed. The simulator/LOCAL/Gemini acceptance run
then passed after the SDK stereo-input regression was fixed. User-provided
snapshots showed a full 20-second local ring and zero ambient/cloud traffic
before activation; during cloud conversation, ambient bytes remained zero
while cloud audio reached 1,489,600 bytes; after inactivity, state returned to
`LOCAL_AMBIENT` with 5,368,640 cloud-audio bytes, zero cloud-video frames,
zero postactivation drops, and 167.511 seconds of connected session time.
Activation-to-provider-ready was approximately 885 ms, including a 505.6 ms
Gemini connection. Local interruption flush samples were 1.0–1.3 ms. No
physical-hardware claim is made, and first-response-audio latency is still
missing from the benchmark instrumentation.

**Acceptance:** the robot can remain locally awake for five minutes with zero
Gemini traffic, then keyboard activation produces a normal Gemini conversation
with immediate local acknowledgment.

**Acceptance evidence:** passed. The user reported the five-minute ambient
soak, immediate acknowledgment, conversation, movement/tools, interruption,
and inactivity close all behaved as expected. The status snapshots and logs
confirm the privacy counters and state/session lifecycle above. One cumulative
capture-worker warning reached 100 dropped frames during the active session;
the experience remained responsive, but this is retained as a performance
follow-up rather than hidden.

### Phase 3 — dinner demo and ambient context

Purpose: answer using recent local context without prior cloud connectivity.

**Implementation note (2026-08-29):** Phases 3–5 are being integrated as one
milestone at the user's request. The Phase 1 saved-log rerun remains pending
manual evidence, but it is not blocking local-only implementation or automated
privacy tests. Acceptance statuses remain separate: no phase is marked complete
without its own live measurements.

- [x] Add bounded background local STT behind a replaceable interface.
- [x] Implement Strategy A continuous rolling transcript.
- [x] Implement Strategy B activation-time local buffer transcription.
- [x] Test bounded 2/5/10/20-second context windows, ordering, stale transcript
  rejection, and privacy-safe failure/cancellation behavior.
- [x] Inject relevant text context into a newly created Gemini session without
  sending the raw ambient buffer.
- [ ] Benchmark both strategies and record transcript quality, CPU, memory,
  activation, connection, and first-audio metrics.

**Acceptance:** “indoors tomorrow”, “aquarium”, “Reachy, what do you think?”
produces context-aware behavior; no pre-activation audio is uploaded; the
added latency feels conversational; resource usage is recorded.

**Phase 3 status: implementation complete; live acceptance pending.** Both
strategies, bounded context injection, local-only snapshot handling, and the
dinner-demo fake-provider path are tested. Real local-STT quality, latency, and
resource measurements remain required.

### Phase 4 — local wake word

Purpose: replace keyboard-only activation while retaining a reliable fallback.

- [x] Add a pluggable `WakeWordDetector`.
- [x] Implement the V0 local “Reachy” partial/final transcript detector; a
  local partial-transcript detector is acceptable for early development.
- [x] Gate runtime activation on final local STT segments so a partial wake
  cannot open cloud before its addressed command suffix arrives; retain
  partial/delta support in the standalone detector.
- [x] Keep keyboard activation as a fallback and test cooldown/debounce,
  boundary matching, bounded partial state, and first-word retention.
- [ ] Measure false positives, missed activations, activation latency, and
  first-word retention.

**Acceptance:** wake detection runs locally, does not depend on Gemini, and
  measured false-positive/missed-activation behavior is documented.

**Phase 4 status: implementation complete; live acceptance pending.** Wake
detection has no network/provider path and is integrated only with continuous
local STT. Microphone false-positive/miss and latency measurements remain
required before selecting this V0 approach over an acoustic detector.

### Phase 5 — first local commands

Purpose: make common physical requests instant and cloud-free.

- [x] Add deterministic allowlisted routing for stop, be quiet, look left, look
  right, and look at me (or the measured initial subset).
- [x] Route successful actions through existing local tool/movement APIs.
- [x] Use physical acknowledgment where spoken TTS is unnecessary.
- [x] Escalate uncertainty and local-action failure to Gemini; test that the
  router never confidently guesses.
- [x] Add the curious, playful, concise, warm, slightly mischievous household
  profile through the existing profile system.
- [x] Create the living benchmark report and next-phase recommendation.
- [ ] Fill the report with live local-command and Phase 3–4 measurements.

**Acceptance:** local commands execute without opening Gemini, uncertain input
escalates, and all required behavior, privacy, latency, and resource tests pass.

**Phase 5 status: implementation complete; live acceptance pending.** Exact
commands and failure escalation are covered through fake provider/tool/movement
boundaries. Simulator movement and measured local-command latency remain
required.

## Test and benchmark matrix

Tests ship with the implementation that creates the behavior. Test behavior at
the appropriate boundary; do not test private implementation details merely to
increase line coverage.

| Area | Required evidence |
|------|-------------------|
| Provider adapter | Unit/integration tests for connection lifecycle, event decoding, transcripts, audio output, tools, camera frames, errors, reconnect, and barge-in. |
| State machine | All valid transitions, invalid transitions, activation, inactivity close, cancellation, restart, and error recovery. |
| Ambient ring | Fixed 20-second bound, overwrite behavior, sample ordering, snapshot consistency, SDK float32 stereo-to-mono normalization, no file creation, and empty/short input. |
| VAD/STT interfaces | Fake backends for speech boundaries, partial/final transcripts, failures, delays, cancellation, and resource metrics. |
| Context strategies | Strategy A and B ordering, bounded context, stale transcript rejection, context injection, and proof that raw pre-wake audio is not passed to the provider. |
| Privacy | While `LOCAL_AMBIENT`, cloud session is closed and audio/video byte/frame counters remain zero; local raw audio is not persisted or logged. Verified sentinel coverage includes console `AdditionalOutputs`, Gemini, Hugging Face transcripts/tools/errors, camera, memory, idle reason, and background progress. |
| Local commands | Exact allowlisted commands, aliases, “stop” safety, no cloud session, physical acknowledgment, uncertain input escalation, and tool failures. |
| Metrics | Counter increments, session duration, transition logs, and no double counting across reconnects. |
| Movement | Acknowledgment is queued through `MovementManager`; no handler/tool emits raw motor commands; speech-reactive motion remains intact. |
| Personality and memory | Existing remember/forget behavior remains local; household profile loads through the current profile system; provider choice does not persist memory. |
| Simulator smoke | Baseline unchanged app, local acknowledgment, cloud conversation, interruption, tool call, camera tool, inactivity shutdown, and clean teardown. |
| Performance | P50/P95 activation-to-connected, end-of-speech-to-first-audio, tool-call, interruption, local-command, and context-injection latencies; local STT CPU/RAM; process RSS; cloud byte/frame counters. |

For every feature, cover success plus relevant invalid input, uncertainty,
exceptions, timeouts, cancellation, interruption, reconnect, and privacy
invariants. Bug fixes require regression tests. The feature documentation must
list exact test files and commands.

## Benchmark protocol

Use a timestamped monotonic clock at each boundary. Report sample count, median
(P50), P95, and environment rather than a single best-case number.

| Measurement | Start | End |
|-------------|-------|-----|
| Gemini connection latency | activation/start action | session ready for live audio |
| First response audio | end of user speech or activation input | first assistant audio frame |
| Tool-call latency | provider tool-call event | tool result accepted by provider |
| Interruption latency | user barge-in detection | assistant output stops / provider acknowledges |
| Local command latency | activation/input boundary | local action acknowledged |
| Context overhead | activation | context-ready live session |
| Session duration | Gemini session open | close event |

Record machine/OS, Python version, model/backend versions, audio sample rate,
buffer duration, network conditions, process RSS, local STT CPU, and cloud byte
counters. The final report must include the exact local VAD/STT backend, proof
that ambient bytes sent stayed zero before activation, problems encountered,
and a measured—not speculative—assessment of Raspberry Pi 5 8GB plausibility.

## Decisions and conflicts

| Topic | Decision or resolution |
|-------|------------------------|
| HF-centric current main | Keep current main and provider abstractions. Add Gemini as an adapter; do not restore an old whole-app tutorial version. |
| Provider setting | Prefer a new `REALTIME_PROVIDER` setting over silently reviving obsolete `BACKEND_PROVIDER`/`MODEL_NAME` names. Preserve HF support, normalize whitespace/case, and fail closed on an unknown value rather than silently selecting another cloud provider. |
| Audio ownership | Compose one handler with `LocalStream`/hybrid runtime; avoid duplicate queues, timers, observers, and tool managers. |
| Latency diagnostics | `GeminiLatencySnapshot` stores numeric monotonic-millisecond samples for connection-ready, first-audio, tool-response, and interruption-flush events, bounded to 32 entries per sample list. `first_audio_ms` is recorded only when an end-of-speech marker arrives before the first assistant audio; audio-first ordering increments bounded unavailable/reordered diagnostics because the exact interval is unknowable. A final transcript after a completed turn with no observed final transcript is ambiguous and cannot re-arm a second sample. It is diagnostic storage, not live benchmark evidence. |
| Gemini setup timeout | Bound only the Live session setup handshake to 15 seconds; do not apply that timeout to the ongoing receive loop. |
| Tool response ordering | Start parallel calls through the shared tool manager, then send one function-response message in the provider's original call order. On cancellation, cancel the local call and flush completed siblings. Send a valid camera image before its function response. |
| Gemini cloud boundary | With Gemini selected, the app connects directly to Gemini without an application gateway. Current live audio, provider transcripts, tool schemas/results, and explicitly requested camera frames cross that boundary; raw payloads are not logged. |
| Gemini SDK dependency | Use the official `google-genai==2.19.0` package. Dependency review records its bundled Apache-2.0 license, Python >=3.10 support, and direct network access to Gemini; this application currently declares Python >=3.11. |
| Linear resampler | The shared provider-rate conversion uses linear interpolation as a compatibility path. It is not a high-quality anti-aliased sample-rate converter; real-device quality review or a measured replacement remains open. |
| Simulator dependency | Keep MuJoCo optional in the `sim` dependency group. `uv sync --frozen --group dev --group sim` provides the locked `reachy-mini[mujoco]` extra with MuJoCo 3.3.0; ordinary runtime installs do not require it. |
| Daemon audio capture | Keep synchronous SDK `get_audio_sample()` off the asyncio loop in the bounded capture worker: queue 8, 5 ms empty polling, oldest-frame drop on overflow, 100 ms stop join, and metadata-only diagnostics with privacy tests. |
| Immediate activation versus local commands | Give deterministic local routing the smallest measured decision window; otherwise escalate/open Gemini. Buffer only the minimum live audio needed. |
| Ambient privacy | Gemini is closed while idle. Only derived bounded local text may be injected after activation; raw pre-wake audio is never uploaded. |
| Provider greeting | Existing handlers may greet on connect. Hybrid activation should suppress an unnecessary automatic greeting and let the user’s address drive the response. |
| Local VAD/STT reuse | Keep the protocol compatible with Hugging Face speech-to-speech's modular VAD/STT ideas rather than adopting its full VAD→STT→LLM→TTS stack. The first sidecar benchmark uses the official Transformers Serve transcription API. MLX Audio's current server is excluded because its STT inference path writes raw input to a temporary file. Preserve a CPU/Linux path and benchmark before selecting models. |
| Wake-word reuse | Hugging Face speech-to-speech does not provide a wake-word stage. Evaluate local openWakeWord and inspect the community Reachy Mini wake-word Space/model before training a custom “Reachy” ONNX model. Treat community artifacts as candidates until source, model, license, platform support, false accepts, and missed activations are measured. |
| Local AI scope | Use interfaces for VAD/STT and `LocalDecisionModel`, but do not install or run the complete HF stack, a local LLM, or another agent framework yet. |
| Tools and movement | Reuse `Tool`, `ToolDependencies`, and `MovementManager`; LLMs and local routers never issue raw motor commands. |
| Documentation location | The user explicitly overrides upstream’s no-Markdown-under-`docs/` preference. App-specific plans/features/testing docs stay here. Do not alter SDK instructions. |
| README convention | Keep the root README as the single operational README. Use `INDEX.md` files for navigation. |
| Architecture diagram | Keep `docs/scheme.mmd` as the source. The provider-choice source update is part of Phase 1; the generated SVG is not hand-edited and regeneration remains pending through the existing workflow. |
| Benchmark location | The requested `HYBRID_BENCHMARK.md` remains a future root artifact; do not create a duplicate under `docs/`. |
| Issue-first workflow | The fork has GitHub issues disabled. The `AGENTS.md` issue-first step was attempted but unavailable, so the agreed scope and decisions are recorded in this living plan instead. |
| Baseline acoustic feedback | The Phase 0 speaker smoke exposed a repeated `Exacto!` → `Exacto.` echo loop and 25 barge-in flushes in approximately 86 s. This baseline observation is separate from the later user-observed Phase 1 LOCAL conversation; Phase 1 benchmarks both speaker and headphone conditions, with headphones as the immediate control. |
| SDK/simulator constraints | MuJoCo cannot validate motor-disable or torque state. Rendered camera output requires non-headless simulation. App `--no-camera` disables the camera tool only; it is not a daemon capture/privacy switch. Simulator startup still depends on GStreamer WebRTC plugins and macOS audio permissions. |
| Movement evidence | `MovementManager.acknowledge_activation()` proves the worker-owned enqueue path and relative start/peak/end pose contract, not physical completion. Acceptance evidence must observe pose or trajectory. Preserve exactly one movement-loop owner; providers and tools never write raw motor commands. |
| Baseline motion item | The existing `MoveHead.__call__` defect is fixed: it now follows the SDK/current `SweepLook` contract by unpacking `(head_joints, antenna_joints)`, using `head_joints[0]` for body yaw and the antenna tuple for `start_antennas`. `tests/tools/test_move_head.py` exercises the real tool with a regression fixture. |
| RAM-only caveat | The app creates no audio files and logs no payloads, but the operating system may swap RAM. Record this limitation without weakening the no-upload invariant. |
| SDK plan convention | If SDK instructions require a root `plan.md`, reconcile it with the project rule that canonical plans live in `docs/plans/`; use a pointer only if an external gate truly requires one, not a second plan. |

## Risks and guardrails

- Local STT may consume too much CPU or add enough delay to harm perceived
  responsiveness; measure both strategies before making one the default.
- Wake-word quality may be poor on Apple Silicon or Raspberry Pi; retain the
  keyboard fallback and record false positives/misses.
- Provider lifecycle changes can break the existing HF UI and restart behavior;
  preserve the `ConversationHandler` contract and test handler rebuilds.
- A local-command classifier that guesses is worse than cloud escalation;
  use a strict allowlist and an explicit uncertain result.
- Context injection can leak stale or irrelevant ambient text; bound, order,
  expire, and test context before sending derived text to Gemini.
- Continuous ambient transcription may expose sensitive text locally; do not
  persist it, log it, or promote it to long-term memory in these phases.
- Cloud counters and state logs are privacy observability, not a substitute for
  tests that inspect the actual send boundary.
- New dependencies must remain cross-platform and be measured on the Mac
  before drawing conclusions about Raspberry Pi 5.

## Deviations from the approved plan

No implementation deviations have been recorded. Phase 1 provider selection,
adapter, runtime, resampler, and automated-test work follows the approved plan;
the real simulator/Gemini benchmark is the remaining Phase 1 validation. Add a
dated entry here whenever implementation, scope, provider behavior, dependency
choice, or acceptance criteria changes from the approved plan, with the reason
and evidence.

## Execution log

| Date | Phase | Evidence / result |
|------|-------|-------------------|
| 2026-08-29 | Planning | Official Conversation App and SDK instructions, current README/architecture, Gemini tutorial, and relevant HF speech-to-speech material reviewed. |
| 2026-08-29 | Planning | Repository fork/clone is checked out and the current HF-centric architecture is recorded. |
| 2026-08-29 | Phase 0 | Native ARM Python 3.12.13: `uv sync --frozen --group dev` succeeded with 121 locked packages; `uv.lock` was unchanged. |
| 2026-08-29 | Phase 0 | The first full pytest run reported 317 passed and 3 avatar failures because Git LFS was absent and tracked SVG files contained pointer text. Git LFS 3.8.0 was installed and initialized locally, the tracked assets were pulled, and the focused avatar suite passed 9 tests. |
| 2026-08-29 | Phase 0 | Exact full gate `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v` passed: Ruff clean, 79 files unchanged, mypy clean across 45 files, and pytest 320 passed. |
| 2026-08-29 | Phase 0 | Optional simulator dependencies were installed with `uv sync --frozen --group dev --group sim`; the locked `reachy-mini[mujoco]` extra supplied MuJoCo 3.3.0 for rendered simulator validation. |
| 2026-08-29 | Phase 0 | Mockup daemon API and SDK movement test changed body yaw from 0 to 0.349 rad and changed the antennas in 0.754 s. Real MuJoCo with media started successfully via `.venv/bin/mjpython -m reachy_mini.daemon.app.main --sim --scene empty --fastapi-host 127.0.0.1 --fastapi-port 8000`; its API was healthy. |
| 2026-08-29 | Phase 0 | Unchanged app connected to the daemon/Hugging Face, loaded all 17 tools, recorded Mac default audio at 16 kHz, received response-audio delta/done events, reached its startup response in approximately 5.2 s, muted the mic once the UI was ready, and shut down cleanly. |
| 2026-08-29 | Phase 0 | RAM-only Camera tool returned a 1280x720 JPEG (118716 bytes) in 0.017 s; SDK connection took 0.783 s. No camera persistence/upload was observed. This does not prove zero pre-mute ambient audio upload because the unchanged app had an active deployed HF session until the mic was muted. |
| 2026-08-29 | Phase 0 | Known baseline issues: no-media produced a warning flood; the ordinary macOS daemon CLI failed because `launch_passive` requires `mjpython`; expected missing Reachy USB, software AEC, and GStreamer warnings appeared; early launches while macOS was locked left stale uninterruptible `UE mjpython` processes that required logout/reboot to reap. |
| 2026-08-29 | Phase 0 | Baseline audible perception and end-of-user-speech latency were not measured; defer them to Phase 1/manual follow-up. |
| 2026-08-29 | Phase 0 | Source inspection confirmed the existing `MoveHead.__call__` discards the joint-position tuple and uses `current_antennas[0]` as `start_body_yaw`, unlike sibling `SweepLook`, which uses actual current body yaw. Runtime behavior was not verified and no fix was made; this is non-blocking for Phase 1 and requires a regression test plus fix before Phase 5 local head commands. |
| 2026-08-29 | Phase 0 | Manual smoke showed good conversation, audio, and movement until speaker-to-microphone acoustic feedback repeated the assistant's `Exacto!` as user `Exacto.` turns, causing 25 barge-in flushes over approximately 86 s. This is an echo/AEC weakness rather than a frozen process; headphones are the immediate Phase 1 benchmark control. |
| 2026-08-29 | Phase 0 | `.env.local` was safely renamed to the Git-ignored `.env` with permission mode `600`; no key value was exposed or recorded. |
| 2026-08-29 | Planning | The fork's GitHub issues are disabled. The `AGENTS.md` issue-first step was attempted but unavailable; the living plan is the durable scope record. |
| 2026-08-29 | Phase 1 | Provider configuration, explicit Hugging Face/Gemini handler factory, Gemini adapter, provider-neutral runtime, shared linear resampler, and automated tests are complete. The real simulator/Gemini audio, tool, interruption, and latency benchmark remains in progress. |
| 2026-08-29 | Phase 1 | Historical interim execution log (the historical 383-test gate and current 392-test gate are recorded below): focused config/main tests: 19 passed. Gemini fake-session tests: 26 passed. Console tests: 51 passed; streaming tests: 10 passed (61 combined). Exact full gate (`ruff check . --fix`, `ruff format .`, strict mypy, `pytest tests/ -v`): Ruff clean, format left 81 files unchanged, mypy was clean across 46 source files, and pytest passed 378 tests in 17.88 s. `uv lock --check`, `node --check settings.js`, `git diff --check`, and the repository secret-pattern scan excluding ignored `.env` passed. Privacy sentinel tests cover console `AdditionalOutputs`, Gemini, Hugging Face transcripts/tools/errors, camera, memory, idle reason, and background progress. |
| 2026-08-29 | Phase 1 | Historical earlier root gate passed 383 tests in 8.05 s; Ruff was clean, formatting left 81 files unchanged, and mypy was clean across 46 source files. The real MuJoCo-with-media daemon was healthy and an earlier Gemini run reached connection-ready in 634.4 ms, but the main asyncio loop then froze in `gst_app_sink_try_pull_sample` before user audio; `/usr/bin/sample` confirmed the stack. The bounded capture worker now uses queue 8, 5 ms polling, oldest-frame dropping, a 100 ms stop join, and privacy-tested metadata-only diagnostics. An attempted cleanup left PID 92880 wedged/listening on 7860; the later WebRTC workaround and current pending validation are recorded above. |
| 2026-08-29 | Phase 1 | Recorded user-observed qualitative evidence: one long, responsive Gemini conversation over SDK LOCAL with successful barge-in/interruption before the later native-audio wedge; first-audio and interruption latency remain uninstrumented. |
| 2026-08-29 | Phase 1 | After the host restart, the user reported that the direct Gemini benchmark run completed successfully over the explicit local media backend. Terminal evidence and numeric latency samples are still pending review, so Phase 1 remains in progress. |
| 2026-08-29 | Phase 1 | Recorded the WebRTC producer-registration `KeyError`, retry initialization with `connection_ready_ms=640.8` and greeting-tool timings of 231.6/230.9 ms, the SDK reverse-audio `not-negotiated (-4)` failure, the unchanged SDK send chain, and the stop recommendation pending a clean reboot/local retest versus a native macOS audio adapter/upstream SDK fix; retained Phase 1 as in progress. |
| 2026-08-29 | Phase 2 | Added opt-in hybrid configuration parsing/documentation, typed provider traffic and connection observers at successful media/session boundaries, startup-greeting suppression control, a queued movement-layer activation acknowledgment, browser Space/Enter activation wiring, the RAM-only runtime, and deterministic ambient/runtime tests. Added LocalStream-loop RPC forwarding, capture-worker barrier/inactivity integration coverage, finite-duration validation, and source-level static UI contracts. Focused Ruff/mypy/Node/diff checks are clean and `pytest -q tests/` passes 447 tests; no simulator, hardware, or live-provider claim is made. |
| 2026-08-29 | Phase 2 | Added owner-loop marshalling for `conversation.say`/`conversation.interrupt`, bounded capture-worker stop retries, delayed-readiness LocalStream coverage, provider-rate conversion at the Hugging Face send boundary, and the `postactivation_frames_dropped` status diagnostic. Focused Ruff/format/mypy/Node/diff checks are clean and `pytest -q tests/` passes 456 tests; final root-gate evidence and live/simulator/hardware validation remain pending. |
| 2026-08-29 | Phase 2 | Root verification after the host restart passed the exact repository gate: Ruff clean, formatting left 87 files unchanged, strict mypy clean across 49 source files, and all 456 tests passed in 14.20 s. `uv lock --check`, JavaScript syntax checks, and `git diff --check` also passed. The restart stopped only transient processes; repository changes and test evidence remained intact. Manual simulator/live-Gemini acceptance is still pending. |
| 2026-08-29 | Phase 2 | Adversarial review fixed two lifecycle gaps: ambient playback now waits on a state wakeup instead of spinning, and post-barrier frames captured during `LOCAL_COMMAND` acknowledgment are retained through provider readiness while pre-barrier frames remain local. Added deterministic regression coverage; focused runtime/console tests pass 89. The final root gate is clean: Ruff passed, formatting left 87 files unchanged, strict mypy passed across 49 source files, and all 459 tests passed in 8.77 s; lock, JavaScript syntax, and diff checks also passed. |
| 2026-08-29 | Phase 2 | An independent Luna-Max re-review found no P0/P1 blocker after the lifecycle fixes. It reran the 89-test focused suite, 9 asyncio-debug tests, and the race-sensitive subset for 20 consecutive passes. Manual simulator/live-Gemini acceptance remains the only Phase 2 acceptance evidence still pending. |
| 2026-08-29 | Phase 2 | Live hybrid smoke exposed repeated `Ambient frame rejected: ValueError` warnings because the Reachy Mini SDK returns float32 stereo frames shaped `(samples, 2)`. Added the shared `audio_to_mono_int16()` normalization boundary with clipping/scaling and defensive-copy regression tests, plus a hybrid no-warning routing test. The failure was observed during manual setup; no manual acceptance pass is claimed. |
| 2026-08-29 | Phase 2 | Follow-up review found averaged int16 stereo was being scaled as normalized float a second time. Corrected the shared downmixer and added channels-last/channels-first int16 regression coverage; no manual acceptance claim changed. |
| 2026-08-29 | Phase 2 | Independent re-review found SDK stereo frames were double-counted in the postactivation FIFO and `(1, 2)` frames were flattened instead of downmixed. Corrected time-axis accounting and the degenerate stereo case; exact SDK-shaped 20/21-second FIFO tests now pass. The final root gate is clean with all 463 tests passing in 9.01 s, and the re-review found no P0/P1 issue. Manual acceptance remains pending. |
| 2026-08-29 | Phase 2 | Adversarial review found postactivation duration counted both dimensions of SDK `(16000, 2)` frames, halving the intended FIFO bound. Corrected duration to use the time-sample axis and fixed `(1, 2)` stereo handling while preserving `(N, 1)` mono; added exact 1-second/20-second boundary regressions. |
| 2026-08-29 | Phase 2 | Manual simulator/LOCAL/Gemini acceptance passed. Two preactivation snapshots showed `LOCAL_AMBIENT`, a full 20-second ring, and zero ambient/cloud traffic. Activation reached connected Gemini in approximately 885 ms (`connection_ready_ms=505.6`); conversation, movement/tools, three interruption flushes (1.0–1.3 ms), and inactivity close worked. The final snapshot returned to ambient with ambient bytes still zero, 5,368,640 cloud-audio bytes, zero video frames/postactivation drops, and 167.511 seconds of connected time. Recorded the cumulative 100-frame capture-worker drop warning and missing `first_audio_ms` as follow-ups. |
| 2026-08-29 | Phase 1 | Diagnosed the missing `first_audio_ms`: official `google-genai` types document input transcription as independent of model-turn events, and live audio can therefore arrive before `ACTIVITY_END` or the final input transcript. Added marker timestamps, positive-only normal-order samples, and bounded `first_audio_unavailable_count`/`first_audio_reordered_count` diagnostics with regression coverage for both orders, repeated turns, interruption, shutdown, duplicate prevention, partial transcripts, and startup greeting audio. |
| 2026-08-29 | Phase 1 | Adversarial review added bounded ambiguity diagnostics and closed late/duplicate/final-only/same-text event-order races without fabricating latency. Independent re-review found no P0/P1 blocker; 41 focused Gemini tests and the full 474-test gate pass. A short saved-log direct rerun remains required for live Phase 1 evidence. |
| 2026-08-29 | Planning | Reverified current Hugging Face speech-to-speech reuse candidates: built-in Silero VAD v5; Parakeet TDT, Transformers Whisper, Faster Whisper, Lightning/MLX Audio Whisper, and Paraformer STT backends; realtime transcript and speech-padding/turn-completion controls. Confirmed wake-word detection is outside that pipeline, with openWakeWord and a community Reachy Mini wake-word Space as Phase 4 candidates requiring local benchmark and license/source review. |
| 2026-08-29 | Phases 3–5 | Added replaceable local VAD/STT protocols, Strategy A/B ambient context, bounded rolling transcripts, loopback-only OpenAI-compatible STT with proxy bypass, RMS VAD, partial/final `Reachy` transcript wake, exact fail-closed local intents, existing tool/movement execution, and the household companion profile. Integrated text-only Gemini context while retaining raw PCM locally; focused implementation suite passes 195 tests. Live local-model/wake/command/resource acceptance remains pending. |
| 2026-08-29 | Phases 3–5 | Added `.env.example`/README setup, four durable feature documents, architecture source changes, and the living [`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md). The report marks unmeasured data explicitly and makes no Raspberry Pi feasibility claim. |
| 2026-08-29 | Phase 3 | Privacy review found the current MLX Audio STT server writes each inference input through `/tmp`; removed it as the recommended ambient backend. Selected official Transformers Serve as the first candidate because its current handler reads request bytes and decodes from `BytesIO`, capped the client upload at 20 seconds, and retained live temporary-directory monitoring as required acceptance evidence. |
| 2026-08-29 | Phase 3 | Added bounded continuous-STT admission: at most two segments remain pending, overload drops the newest segment locally while advancing result ordering, and status exposes metadata-only drop/timing counters. |
| 2026-08-29 | Phase 3 follow-up | Closed a resource-boundary gap for blocking synchronous local VAD/STT: the processor now owns a bounded executor, keeps timed-out calls and defensive frames admitted until workers exit, and reports still-running work during close instead of claiming cancellation. Added deterministic repeated-timeout, bounded-retention, close, and cancellation regressions; live local-model/resource measurements remain pending. |
| 2026-08-29 | Phases 3–5 | Final root verification passed the exact repository gate: Ruff clean, formatting left 98 files unchanged, strict mypy clean across 54 source files, and all 649 tests passed in 11.39 seconds. `uv lock --check`, JavaScript syntax checks, and `git diff --check` also passed. Live microphone/local-model acceptance remains pending. |
| 2026-08-29 | Phase 2 | Privacy review fixed the capture-worker activation race: epochs are reserved before blocking SDK pulls and activation snapshots the same locked epoch, so a pull begun before activation is always local. Added deterministic LocalStream interleaving coverage proving the preactivation frame is not provider-bound and later frames retain FIFO order. |
| 2026-08-29 | Phases 4–5 follow-up | Adversarial review fixed the `MoveHead` body-yaw interpolation tuple bug and partial-wake lifecycle bug. Focused movement, wake-word, and hybrid-runtime tests pass; live simulator validation remains pending. |
| 2026-08-29 | Phase 4 follow-up | The runtime now waits for final local STT segments before activation so a partial `Reachy` cannot open cloud before its addressed suffix arrives. The standalone detector retains partial/delta support; a regression proves `Reachy` → `Reachy, look left` and the next final wake execute locally with zero provider construction. |
| 2026-08-29 | Phase 3 follow-up | Final adversarial re-review found all four release-blocking fixes resolved and no remaining P0/P1. The residual reordered-partial edge case was also closed so a late partial cannot reopen an already finalized transcript sequence. |

## Next steps

1. Repeat the short direct Phase 1 LOCAL benchmark with corrected first-audio
   instrumentation; retain unavailable/reordered counts instead of inventing a
   latency when provider events arrive out of order.
2. Start the loopback local STT endpoint and run Strategy A and Strategy B dinner
   demos with headphones, recording the report's latency, transcript quality,
   CPU, and RSS fields.
3. Run a labelled local wake set and all five local commands in the simulator;
   record false positives, misses, first-word retention, movement behavior, and
   local-command latency.
4. Update this plan, the feature documents, and
   [`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md) after each accepted live
   result. Regenerate the architecture SVG from [`docs/scheme.mmd`](../scheme.mmd)
   through the existing workflow when available.

## Changelog

| Date | Change |
|------|--------|
| 2026-08-29 | Created the living plan and documentation/test structure; status is `in-progress` because review/fork/clone are complete but baseline validation is pending. |
| 2026-08-29 | Recorded locked dependency installation and the passing full upstream gate; simulator, unchanged interactive audio, and baseline latency remain open Phase 0 work. |
| 2026-08-29 | Marked Phase 0 complete after simulator/API, unchanged-app, movement, camera, startup, and full-gate evidence; audible perception and end-of-user-speech latency remain Phase 1 follow-up measurements. |
| 2026-08-29 | Marked Phase 1 active and recorded the Phase 0 speaker echo/AEC observation, headphone control, and local `.env` handling without recording the key. |
| 2026-08-29 | Marked Phase 1 configuration and handler-factory work complete; adapter/runtime integration, focused/full validation, and benchmark evidence remain in progress. Recorded that GitHub issues are disabled and the issue-first step was unavailable. |
| 2026-08-29 | Marked the Gemini adapter, provider-neutral runtime, linear resampler, and automated tests complete after the verified focused/full gate; retained Phase 1 status because real simulator/Gemini audio, tool, interruption, quality, and latency benchmarking remains pending. |
| 2026-08-29 | Recorded the optional `sim` group and locked MuJoCo 3.3.0 evidence, added the final exact gate after the interim gate, and documented expanded privacy sentinel coverage; real Gemini benchmarking remains pending. |
| 2026-08-29 | Recorded the 545.7 ms Gemini connection-ready sample, 270.8/274.2 ms tool timings, the healthy event loop and daemon/Gemini sockets alongside the LOCAL capture-worker block, 19 GiB free disk, arm64 Python rebuild, and the `--media-backend webrtc` simulator target; retained Phase 1 as in progress pending complete WebRTC validation. |
| 2026-08-29 | Implemented Phases 3–5 as one milestone with replaceable local audio backends, both context strategies, local transcript wake, exact local commands, household profile, text-only provider context, and comprehensive focused tests. Each phase remains in progress until its live acceptance measurements are recorded. |
| 2026-08-29 | Created the living hybrid benchmark report with measured Phase 1/2 evidence and explicit `not measured` Phase 3–5 fields; no Raspberry Pi feasibility claim is made. |
