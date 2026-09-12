# Reachy Hybrid Household Companion — Implementation Plan

- Status: `in-progress`
- Active phase: `Supervised headless MVP accepted; hardware and broader validation pending`
- Owner: Reachy project
- Created: 2026-08-29
- Last updated: 2026-09-10
- Scope: Phases 0–5
- Required final report: [`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md) at the repository root

## Supervised headless MVP accepted — 2026-09-10

Follow-on work: [GPT Live migration](openai-live-migration.md) adds GPT Live speech
with Luna tool/image delegation while preserving the accepted Gemini path.
That provider has separate validation gates; it does not reopen wake training.

User reports ordinary conversation did not wake Reachy, saying Reachy activated
it, silence returned it to standby, further ordinary speech did not activate it,
and a later addressed question woke it again. Together with the earlier live
conversation/shutdown log, this completes the agreed practical supervised
headless MVP checks. This is user-reported acceptance, not a quantified false-wake
rate or a new independent model benchmark. No further recording or training is
required for this milestone; Official33 remains frozen and off by default.

The user also reports a relevant answer about speech preceding the later wake.
The prescribed acoustic trial disables local STT and does not replay prewake
audio. Without the new session log/configuration, the source of that apparent
context is unverified; do not count it as validation of ambient context capture
or change the documented privacy contract.

The broader plan remains in progress for hardware/Pi, graphical viewer reliability,
audio drops, echo and outstanding resource/quality measurements. These do not
reopen the accepted supervised headless milestone. Validation for this update:
user report and documentation diff check; no code/model changes or tests rerun.


## Current status — 2026-09-09

The local checkout has the main companion loop implemented. Hardware has not
arrived, so production/Pi claims remain out of scope. Earlier live simulator
acceptance is recorded below; this update does not repeat or expand those claims.

| Area | Current evidence | Remaining work |
|---|---|---|
| Phase 0: SDK/simulator baseline | Prior movement, audio, tools/camera and lifecycle acceptance | Real robot bring-up |
| Phase 1: Gemini Live | Prior headphone conversation, tools and interruption accepted | Exact first-audio timing; laptop-speaker echo/AEC |
| Phase 2: local/cloud privacy runtime | Prior ambient soak, manual activation, interruption, timeout-close; automated byte/cancellation gates | Sustained target-hardware resource measurements |
| Phase 3: recent local context | Both English dinner-context strategies accepted; continuous local STT preferred | Multilingual quality and CPU/RAM/latency characterization |
| Phase 4: Reachy wake | Official33 integrated as an off-by-default supervised simulator MVP; real adapter parity 17/20; headless SDK/movement and real microphone wiring pass | Headless human conversation accepted Sept 10; inactivity rearm, viewer and hardware checks remain |
| Phase 5: local commands | Five exact commands previously accepted with no Gemini construction/cloud audio | Full speech-to-action latency and sustained drop metrics |

Stop wake training for now. Keep official33 as the low-false-wake MVP and retain
baseline27+verifier32/parallel35 as documented alternatives. The
[model catalog](reachy-wake-model-catalog.md) records artifacts and trade-offs.
The acoustic MVP deliberately uses wake → normal cloud conversation; existing
transcript-based exact local commands remain a separate available mode. The app
has manual activation and inactivity return to listening. No automatic parallel
ensemble is installed. A failed offline quality gate is not erased by trial
selection; this is a user-approved supervised exception.

Next: finish a human-spoken simulator session, then use it normally before
spending on more training. Hardware arrival adds a separate microphone/Pi/AEC
and resource-validation step. Richer memory, home automation, recognition and
proactivity remain outside the original phases 0–5.

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

### Starting baseline (2026-08-29)

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

The wake phrase is “Reachy”. Keyboard activation remains available so
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

### Phase 1 — direct Gemini Live benchmark

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
- [x] Enable Gemini Live's managed `google_search` alongside application
  function declarations. Retain the dedicated Pollen MCP search, weather, and
  time tools for profile-managed lookups.
- [x] Complete fake-session tests for every Gemini event and failure path,
  provider-neutral runtime tests, resampler tests, and the automated repository
  gates.
- [x] Benchmark activation/start to connection, end-of-speech to first response
  audio, tool-call latency, and interruption latency. Gemini supplied no usable
  speech-end marker, so exact first-audio latency is explicitly unavailable.
- [x] Run the benchmark with both Mac speakers and headphones, recording the
  input/output devices and condition for every sample; use headphones as the
  immediate control for the observed acoustic feedback.
- [x] Observe and record assistant-to-microphone echo loops, repeated
  transcripts, and barge-in flush counts in each output condition. Verify
  process/event liveness separately so an echo/AEC failure is not classified as
  a frozen process.

**Acceptance:** direct headphone conversation was natural and responsive,
movement/tool use worked, and both requested interruptions stopped output. The
Mac-speaker control immediately fed assistant output back into the microphone
and is not accepted for simulator use. First-audio latency is explicitly
unavailable because the provider emitted no usable speech-end marker; no value
is fabricated.

**Phase 1 status: complete with a measured simulator limitation.** The accepted
headphone run connected in 637.5 ms, executed visible head movement with a
2.6 ms provider tool-result interval, and flushed two successful interruptions
in 1.4 ms and 3.8 ms. The Mac-speaker control connected in 455.6 ms but produced
six self-triggered interruption flushes in about 18 seconds. Both processes
remained live and exited without traceback; headphones are required for this
app-owned simulator output path.

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
milestone at the user's request. Phase 1 was later accepted through saved-log
headphone and speaker runs. Acceptance statuses remain separate: no phase is
marked complete without its own live measurements.

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

**Phase 3 status: both dinner-demo strategies passed; final quantitative gaps
remain.** Strategy A retained accurate local context before activation and
produced a contextual answer. Strategy B transcribed the 20-second RAM snapshot
only after activation and also produced a contextual answer, but added about
6.0 seconds of local STT and reached Gemini ready after about 7.2 seconds.
Ambient cloud bytes remained zero in both runs. Peak CPU and first-audio timing
remain unavailable; multilingual household STT is separate new scope.

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

**Phase 4 status: implementation complete; live acceptance partial.** Wake
detection has no network/provider path and is integrated only with continuous
local STT. `LOCAL_WAKE_WORD=computer` successfully activated the accepted
dinner demo and cloud-free local commands. An earlier transcript-based
`Reachy` attempt did not pass because no exact matching final transcript token
reached the detector; this is historical STT evidence, not an acoustic Reachy
quality result. Microphone false-positive/miss and latency measurements remain
required before selecting a production acoustic detector; no fuzzy transcript
alias was added.

The 16 kHz ONNX model in
[`andyjmorgan/reachy-wake-word`](https://github.com/andyjmorgan/reachy-wake-word)
matches the existing normalized audio seam, but it targets phonetic `reechy`
and its pretrained model is CC BY-NC-SA 4.0. The
[`Hey Reachy` Edge Impulse candidate](https://huggingface.co/spaces/luisomoreau/hey_reachy_wake_word_detection)
targets a longer phrase. Its Mac ARM64/Linux AARCH64 packaging is useful
reference material, but the example owns its microphone and the Space's
application license is unclear. The open-vocabulary sherpa fixture benchmark
now compares explicit `Reachy`, `Richie`, `Ritchie`, `Richi`, and `Ricci`
acoustic spellings under one canonical `@reachy` event. No spelling becomes a
production wake candidate until live recordings establish that it improves
recall without unacceptable false activations, and redistribution terms are
cleared. The first real positive-fixture screen rejected the reviewed Spanish
checkpoint: it detected 0/30 wakes at documented settings and only 2/30 at an
overly aggressive setting. The negative set was therefore not collected.
The existing community `reechy` openWakeWord model was the next positive
screen. It detected only 6/30 at its documented threshold, and gain
normalization did not help, so it is also rejected before negative testing.
The approved next step is the
[custom multilingual openWakeWord training plan](custom-reachy-openwakeword-training.md):
use synthetic multilingual speech for training, preserve the 30 private clips
as an untouched holdout, and collect negative exposure only after a candidate
reaches the positive gate. The public/synthetic Mac/Linux ARM64 check now passes with 668 identical
scores, and an isolated English CPU pipeline smoke exports and loads correctly.
Neither result establishes household quality or Linux x86/GPU behavior. Adapt the saintpete toolkit only in an isolated trainer, with
multilingual voice review, verified ONNX export, and tested budget/Pod-stop
controls. The report does not establish the cause of the previous misses.

### Phase 5 — first local commands

Purpose: make common physical requests instant and cloud-free.

- [x] Add deterministic allowlisted routing for stop, be quiet, look left, look
  right, and look at me (or the measured initial subset).
- [x] Route successful actions through existing local tool/movement APIs.
- [x] Use physical acknowledgment where spoken TTS is unnecessary.
- [x] Escalate uncertainty and local-action failure to Gemini; test that the
  router never confidently guesses.
- [x] Handle wake-only and command-as-separate-final STT segments with one
  bounded local follow-up window; preserve postactivation FIFO for cloud
  escalation and release the waiter on timeout/cancellation.
- [x] Add the curious, playful, concise, warm, slightly mischievous household
  profile through the existing profile system.
- [x] Create the living benchmark report and next-phase recommendation.
- [ ] Fill the report with live local-command and Phase 3–4 measurements.

**Acceptance:** local commands execute without opening Gemini, uncertain input
escalates, and all required behavior, privacy, latency, and resource tests pass.

**Phase 5 status: functional live acceptance passed; quantitative completion
partial.** Exact commands, failure escalation, and split-final wake follow-up
behavior are covered through fake provider/tool/movement and capture-boundary
tests. All five allowlisted commands then executed locally in live simulator
runs using `LOCAL_WAKE_WORD=computer`, with no Gemini provider construction or
cloud audio for the accepted actions. End-to-end speech latency, sustained
local-processing drops, and the current `Reachy` acoustic wake remain benchmark
follow-ups; the earlier transcript and `computer` runs are retained as
historical controls.

## Test and benchmark matrix

Tests ship with the implementation that creates the behavior. Test behavior at
the appropriate boundary; do not test private implementation details merely to
increase line coverage.

| Area | Required evidence |
|------|-------------------|
| Provider adapter | Unit/integration tests for connection lifecycle, managed Google Search and application-tool configuration, event decoding, transcripts, audio output, tools, camera frames, errors, reconnect, and barge-in. |
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
| Product wake name | Use written/default `Reachy`. Keep transcript matching exact and preserve keyboard activation. Benchmark only the explicit acoustic spellings `Reachy`, `Richie`, `Ritchie`, `Richi`, and `Ricci`, all mapped to one canonical event; enable only variants that pass live quality testing. Retain `Tito` and `computer` runs as historical evidence, not aliases. |
| Local VAD/STT reuse | Keep the protocol compatible with Hugging Face speech-to-speech's modular VAD/STT ideas rather than adopting its full VAD→STT→LLM→TTS stack. The first sidecar benchmark uses the official Transformers Serve transcription API. MLX Audio's current server is excluded because its STT inference path writes raw input to a temporary file. Preserve a CPU/Linux path and benchmark before selecting models. |
| Wake-word reuse | Hugging Face speech-to-speech does not provide a wake-word stage, and its tutorial demonstrates fixed-label classification rather than an arbitrary multilingual name. Use the established sherpa open-vocabulary engine only as a fixture benchmark for Reachy spellings. Existing Reachy models remain references with phrase/license caveats; no production acoustic adapter is selected until live quality and licensing gates pass. |
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

The approved plan retained the SDK speaker path. On 2026-08-30, three native
macOS simulator crashes established that assistant PCM entering the SDK LOCAL
`audiomixer` is unsafe on this machine. An explicit app-owned GStreamer speaker
adapter was added as a simulator containment path. It changes only local
playback, adds no dependency or agent layer, leaves `sdk` as the default, and
does not monkey-patch the SDK. The deviation remains provisional until repeated
live validation; the durable fix still belongs upstream.

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
| 2026-08-30 | Phase 3 live acceptance | Confirmed hybrid ambient startup and local audio capture, then found the documented Transformers Serve flag was invalid. `FORCE_MODEL` is positional, so the command now passes `openai/whisper-small` without `--force-model`; live STT acceptance remains pending. |
| 2026-08-30 | Phase 3 live acceptance | Confirmed local WAV requests reach `/v1/audio/transcriptions`; the server returned HTTP 500 because `python-multipart` was absent from the serving environment. Added it explicitly to the isolated install command; live transcript quality and wake acceptance remain pending. |
| 2026-08-30 | Phases 3–5 diagnostics | Exposed the complete metadata-only `RuntimeStatusSnapshot` through `conversation.status`, including `transcript_segments_accepted` and `wake_final_update_count`; added an RPC regression covering every status field without raw transcript/audio payloads. |
| 2026-08-30 | Phases 4–5 live acceptance | Transformers STT returned HTTP 200. With `LOCAL_WAKE_WORD=computer`, `look left` completed final-STT → wake detection → local activation, then escalated to Gemini; Gemini called `MoveHead`, so Phase 5 live acceptance did not pass. The default `Reachy` phrase produced no action because no exact matching final transcript token reached the detector; no alias was added and wake-quality/latency measurements remain pending. |
| 2026-08-30 | Native audio crash | Two app runs ended with macOS `SIGSEGV` reports on GStreamer's `audiomixer0:src` thread (`do_convert_out` → `do_quantize` → audio aggregator) after cloud activation. There was no Python traceback or clean shutdown. Live cloud-audio testing is paused pending a safe SDK/GStreamer mitigation or upstream fix. |
| 2026-08-30 | Native audio crash containment | Added an app-level macOS + SDK `MediaBackend.LOCAL` workaround that skips native `clear_player()` while continuing to drain provider/app queues. This is containment, not a proven root fix; already-buffered native audio may remain after barge-in, and hardware/live validation is pending. |
| 2026-08-30 | Phase 4 research | Reviewed ready-made acoustic wake candidates and official Reachy status. The 16 kHz `andyjmorgan` ONNX model is the closest seam fit but targets phonetic `reechy` and is CC BY-NC-SA; the 24 kHz/1.5-second Edge Impulse `Hey Reachy` candidate needs a shared-stream adapter and has an unclear Space license; openWakeWord has no Reachy model. Official PR #514 and SDK issue #1253 remain unresolved. No model or dependency was added; benchmark evidence is still required. |
| 2026-08-30 | Diagnostics/containment gate | Root verification passed Ruff, formatting, strict mypy across 54 source files, all 654 tests in 12.28 seconds, `uv lock --check`, both JavaScript syntax checks, and `git diff --check`. Live validation of the macOS containment remains pending. |
| 2026-08-30 | Phase 5 split-final follow-up | Added a configurable two-second, one-shot waiter for a wake-only final transcript, allowing for the existing silence boundary and local STT delay while staying bounded. The first next final is routed through the existing exact local-intent boundary; local success clears the postactivation FIFO, while uncertainty/timeout preserves it for cloud activation. Capture-boundary, success, escalation, timeout, cancellation, race, and privacy regressions pass; no live split-final measurement is claimed. |
| 2026-08-30 | Phase 3 performance follow-up | Review confirmed that the capture loop currently awaits each local VAD observation; a slow VAD can delay capture and increase capture-worker drops. The limitation is bounded and counted but intentionally not refactored in the split-final change; measure it with the local backend before changing concurrency. |
| 2026-08-30 | Phases 3–5 live acceptance | The macOS crash-containment rerun remained alive, but the metadata-only status snapshot showed 98.08 seconds buffered, one speech frame, zero STT requests, and zero ambient bytes sent to cloud. The default RMS threshold of 500 did not admit the spoken command in this run; benchmark a lower process-local threshold before changing the shared default. |
| 2026-08-30 | Phase 3 live STT tuning | A process-local RMS threshold of 100 admitted 65 speech frames and created one STT request, but the request exceeded the five-second app timeout. A following synthetic `computer look left` request to the same warmed Transformers endpoint returned the correct text in 1.25 seconds. Repeat live testing with a temporary longer timeout before changing the model or shared defaults. |
| 2026-08-30 | Split-final gate | Final CI-equivalent verification passed Ruff, formatting, strict mypy across 54 source files, all 672 tests in 13.37 seconds, lock consistency, both JavaScript syntax checks, and `git diff --check`. `REACHY_MINI_SKIP_DOTENV=1` isolated tests from the ignored live `.env`; live split-final and native-crash validation remain pending. |
| 2026-08-30 | Native audio crash root cause | A third live run crashed about 2.25 seconds after Gemini called `MoveHead` and response playback began, despite the Darwin `clear_player()` skip. All three macOS reports have the same native arm64 null dereference on `audiomixer0:src` at GStreamer `do_convert_out+140`, with zero samples and null sample arrays before ORC runs. The app's public audio shape is valid. The skip is therefore insufficient, Phase 5 local-command acceptance is still pending, and repeated live playback tests remain paused. |
| 2026-08-30 | Native audio mitigation plan | Selected an explicit, reversible simulator output adapter that routes assistant PCM through a small app-owned GStreamer pipeline without `audiomixer`, while retaining SDK capture, providers, tools, and movement. It will not monkey-patch SDK internals or add a dependency. The SDK mixer still exists for capture, audio-reactive wobbling will not receive assistant PCM, and live repeated-command validation is required before treating the bypass as effective. GStreamer 1.28.6 is not the primary path because its relevant converter/aggregator sources match installed 1.28.4. |
| 2026-08-30 | Native audio mitigation implementation | Added the explicit `AUDIO_OUTPUT_BACKEND=gstreamer` path with bounded/leaky queues, generation-safe interruption, fail-closed configuration/startup, and combined SDK/app shutdown. Adversarial review rejected earlier passes for lifecycle, flush-race, and test-isolation defects, then approved the corrected implementation with no remaining P0/P1 findings. The 126-test focused set passed in both file orders. Final root verification passed Ruff, formatting, strict mypy across 57 source files, all 697 tests in 15.07 seconds, lock consistency, both JavaScript syntax checks, and `git diff --check`. Live containment evidence remains pending. |
| 2026-08-30 | Automated smoke milestone | Added `tests/test_hybrid_smoke.py`, an 11-test hermetic harness composing the real hybrid runtime, wake detector, deterministic intent router, local action executor, and public speaker seam. It covers all five exact local commands without provider construction, split-final exact and delayed/non-exact follow-ups, real fake-provider traffic with zero ambient cloud bytes, inactivity/shutdown cleanup, repeated provider lifecycles, bounded burst/interruption pressure, payload-safe logging, and no-file behavior. `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_hybrid_smoke.py -q -rs` passes 11 and skips the explicitly opt-in native child; live/native and subjective audio acceptance remain pending. |
| 2026-08-30 | Native audio mitigation live probe | The explicit bypass survived several Gemini turns, tool calls, two interruptions, inactivity return, and continued ambient STT without the previous native crash. Its audio was audibly choppy/accelerated. Review traced the strongest cause to arrival-time GStreamer timestamps without sample-derived PTS/duration; sample-rate conversion was correct. The mitigation is not accepted until timing is corrected and automatically exercised. |
| 2026-08-30 | Local-command live probe | The current wake reached cloud in 759 ms and never invoked the local `MoveHead` path, proving the addressed suffix failed the exact local router rather than timing out or failing during movement. The local capture queue also dropped frames during this window. Transcript content remains intentionally unlogged, so metadata-only route reasons and automated delayed/non-exact cases are required before correction. |
| 2026-08-30 | Native audio timing regression | Added a GI-free sample timeline and buffer contract test first; it failed before implementation with the expected missing-module error. The green implementation now anchors the first cue to pipeline running time, derives PTS/duration from cumulative sample positions without fractional-rate drift, keeps burst arrivals contiguous, reanchors only when running time exceeds the expected next PTS by over 200 ms, marks discontinuities, and resets on start/flush. The native appsrc uses `do-timestamp=False` and explicit PTS/DTS/duration; rejected flush events now fail closed. The timing subset passed 7 tests, the flush contract passed 3, and the native sink cadence/live audible quality remain unmeasured. |
| 2026-08-30 | Native audio timing review follow-up | Corrected the long-buffer gap comparison and cumulative 44.1 kHz rounding drift, and added native flush-result rejection regressions. The timing/flush/output subset passed 24 tests in forward and reverse order; the opt-in native lifecycle child passed three consecutive runs. The dotenv-isolated repository suite now passes 726 tests with one explicit skip; native sink cadence and live audible quality remain unmeasured. |
| 2026-08-30 | Phase 3 performance follow-up | Added metadata-only local-command outcome/reason counters to distinguish same-final router rejection, split-final timeout, and local execution failure without transcript retention. Reproduced capture pressure at the LocalStream → HybridRuntime seam with a blocked local processor; moved LocalStream handoff to a bounded eight-frame sequential runtime worker, with newest-local-frame drops counted separately. The focused hybrid/runtime/console/router/action/main command passed 205 tests; the dotenv-isolated full command passed 719 tests with one explicit skip, plus Ruff, strict mypy across 58 source files, and `git diff --check`. Live microphone/STT timing and sustained-overload rates remain pending. |
| 2026-08-30 | Automated live playback smoke | Root launched the real simulator, Transformers Serve, and the app-owned GStreamer/Gemini path with the microphone muted to prevent acoustic feedback. Two fresh cloud sessions connected in 451.4 ms and 327.7 ms with the app-owned sink selected, completed a `move_head` tool call and two interruptions, and stayed alive; the first returned to `LOCAL_AMBIENT` after inactivity. A separate startup/stop cycle shut down the app cleanly before the daemon. Ambient-cloud bytes, postactivation drops, and local-processing drops remained zero. The status surface does not prove sink acceptance, and a Mac `say` wake probe did not reach VAD/STT, so human acoustic wake/command and audible-cadence acceptance remain pending. |
| 2026-08-30 | Final automated gate | The exact gate passed Ruff, formatting, strict mypy across 59 source files, all 726 tests with one explicit native skip, lock consistency, both JavaScript syntax checks, and `git diff --check`. The opt-in native speaker child passed separately in 1.15 seconds. A post-close frame race found during root review was fixed with a regression so closed runtimes no longer perform local observation. |
| 2026-08-30 | Phase 5 live acceptance | Human speech `computer, look left` reached local Whisper (HTTP 200), transitioned `LOCAL_AMBIENT` → `LOCAL_COMMAND`, executed the existing `move_head`/`look_left` action, and returned to `LOCAL_AMBIENT`. No Gemini handler was constructed or connected, so the command-path privacy acceptance passed. Local execution followed the STT response by about 14 ms. End-to-end speech latency, the other four commands, and sustained local-processing drop rates remain pending. |
| 2026-08-31 | Native playback quality blocker | Human headphone acceptance remained choppy/fast-forwarded after the sample-clock correction. Native reproduction proved the two former eight-buffer/500 ms downstream-leaky queues silently deleted burst audio: only 5 of 20 100 ms buffers survived while `push-buffer` still returned success. Replaced buffer-count limits with a bounded 30-second byte/time contract while preserving non-blocking pushes, downstream leaking beyond the bound, and immediate generation-safe flush; incompatible appsrc versions now fail closed instead of silently becoming unbounded. The revised native child retains 20/20 uniquely marked buffers in order with contiguous timing and verifies a two-frame post-flush restart; the focused suite and opt-in native child pass. Final root verification passed Ruff, formatting, strict mypy across 59 source files, all 730 tests with one explicit native skip, lock consistency, both JavaScript syntax checks, and `git diff --check`. Fresh human audible retest remains pending. |
| 2026-08-31 | Native playback live acceptance | A fresh restarted run with headphones produced normal Gemini playback. The same run proved `computer, look right` executed locally and silently, returning to `LOCAL_AMBIENT` without constructing Gemini; a later unsupported addressed utterance separately escalated and connected Gemini in 516.6 ms. Ambient cloud bytes remained zero. Speaker playback without headphones can feed the app-owned output back into the Mac microphone because that bypass is outside the SDK echo-cancellation reference, causing Gemini to interrupt its own output; headphones are required for current simulator acceptance. |
| 2026-08-31 | Phase 5 live command acceptance | Live simulator coverage now includes all five allowlisted actions. A clean run executed `computer, stop`, `computer, be quiet`, and `computer, look at me` locally with three executions, zero failures/escalations, zero cloud audio, and no provider connection; prior accepted runs cover `look left` and `look right`. The run counted 12 dropped local-processing frames, so sustained local overload and full speech-to-action latency remain performance follow-ups. |
| 2026-08-31 | Phase 3 live dinner failure | The first live Strategy A dinner run failed context-aware response: Gemini asked what specifically the user meant. Local evidence isolated the provider handoff: all three STT segments completed and were accepted, 96 context characters were ready with no pending/failing STT, Gemini connected in 421.5 ms, and ambient cloud audio remained zero. Review found the derived transcript was embedded in `system_instruction` instead of Gemini Live's ordered initial-history protocol, while addressed text and postactivation PCM were scheduled independently. Fix initial-history seeding and question-before-audio ordering before repeating acceptance. |
| 2026-08-31 | Phase 3 provider-handoff correction | Moved bounded derived context into Gemini Live's official initial-history client content, retained static untrusted-context guidance, and delayed the connected callback until seeding succeeds. The runtime now queues new microphone frames without blocking capture and releases them only after the addressed text send completes. Regressions cover context bounds, empty sessions, transport failure, cancellation, concurrent shutdown, payload-free logs, and context → question → audio ordering. Independent re-review found no remaining P0/P1 issue. The 108-test focused suite and final gate pass: Ruff, formatting, strict mypy across 59 source files, 736 tests with one explicit native skip, lock consistency, both JavaScript syntax checks, and `git diff --check`. Live dinner response-quality retest remains pending. |
| 2026-08-31 | Phase 3 live-failure hardening | Fenced seeded context strictly before the wake sequence, preserving earlier ambient text while excluding all post-wake finals accepted before provider setup. Decoupled the 60-second text-only `LOCAL_CONTEXT_WINDOW_SECONDS` from the fixed 20-second continuous-STT PCM cap, including safe oversized-frame tail trimming. Split-final follow-up remains five seconds by default and is bounded to 15 seconds for both environment and direct runtime configuration. Hardened intentional Gemini voice/profile reconnects across setup and initial-history races and drained stale output. Added deterministic context metadata, long/oversized STT, processor-backed delayed-final, post-deadline, reconnect, cancellation, and configuration regressions. The 217-test focused integration suite passed; independent adversarial review found no remaining P0/P1 blocker. The final dotenv-isolated gate passed Ruff, formatting, strict mypy across 59 source files, and 761 tests with one explicit native-audio skip. Live response-quality retest remains pending. |
| 2026-08-31 | Phase 3 transcript-quality diagnostic | A second live dinner run completed four local STT segments and retained 105 characters with no STT failure, timeout, drop, or pending result; ordered context seeding completed and ambient cloud bytes remained zero, but the spoken reply was unrelated. An isolated live Gemini history probe using the same model and history protocol correctly recalled the synthetic `aquarium` fact, making local transcript quality the leading unverified boundary. Add an explicitly opt-in, default-off RAM-only transcript preview through a dedicated loopback-only HTTP endpoint and local UI. The preview must never enter `conversation.status`, logs, disk, or persistent browser storage; preview retrieval/display must add no cloud traffic, while normal activation may send bounded derived context to the provider. |
| 2026-08-31 | Phase 3 transcript preview diagnostic | Added `LOCAL_TRANSCRIPT_PREVIEW_ENABLED=0`, a loopback-only no-store `GET /api/transcript-preview` endpoint, and a small non-persistent Talk-view panel. Disabled, non-loopback, missing-processor, closed, empty, Strategy B, and bounded behavior is covered without exposing text through `conversation.status` or logs. The focused config/console/runtime/static suite passed 212 tests. Final root verification passed Ruff, formatting, strict mypy across 59 source files, 782 tests with one explicit opt-in native-audio skip, lock consistency, both JavaScript syntax checks, and `git diff --check`. Independent adversarial review found no remaining P0/P1 blocker. |
| 2026-08-31 | Phase 3 live preview diagnosis | The first live preview run exposed no text because VAD classified every frame as silence: after 207 seconds of local audio it reported zero speech frames, zero STT requests, and zero accepted transcript segments while the loopback preview endpoint remained enabled and available. A payload-free 20-second level probe measured a displayed peak of `0.043`; after the UI's `6x` meter gain this is about 235 PCM RMS, below the configured default threshold of 500. Retest this Mac microphone with `LOCAL_VAD_RMS_THRESHOLD=100`, then use the preview to evaluate transcript quality. Ambient cloud bytes remained zero. |
| 2026-08-31 | Phase 3 live preview STT timeout | Retesting at `LOCAL_VAD_RMS_THRESHOLD=100` detected 257 speech frames and started local STT, proving the earlier VAD diagnosis, but the first request hit the configured timeout at exactly 5.00 seconds before producing a segment. The loopback Whisper server remained healthy, and a synthetic one-second silent WAV probe completed successfully in 2.06 seconds after warm-up. Use `LOCAL_STT_TIMEOUT_SECONDS=15` for the next Mac simulator run and measure the real utterance latency before changing the cross-platform default. |
| 2026-08-31 | Phase 3 live transcript-quality result | With the calibrated VAD and 15-second timeout, the preview displayed a completed local transcript. It materially misrecognized both context-bearing terms in the dinner sentence, explaining why Gemini's answer was unrelated even though the ordered context handoff worked. Do not retain the sensitive transcript payload. Next compare the same spoken sentence with the drop-in English-only `openai/whisper-small.en` model before increasing model size; record accuracy and latency for each backend. |
| 2026-08-31 | Phase 3 Strategy A live acceptance | Repeating the dinner demo with `openai/whisper-small.en`, `LOCAL_VAD_RMS_THRESHOLD=100`, and a 15-second STT timeout produced an accurate bounded local transcript and a context-aware Gemini answer. The post-run runtime was back in `LOCAL_AMBIENT` with Gemini closed, two accepted/completed STT segments, 68 context characters, zero STT failures/timeouts/drops, zero ambient audio bytes sent to cloud, zero cloud video frames, and a completed 86.445-second Gemini session. Local STT totaled 11.171 seconds across the two requests with a 9.227-second maximum; context snapshot construction took 0.058 ms. The Transformers Serve process had a measured 3.4 GB current/peak macOS physical footprint after the run; peak CPU and full activation-to-first-audio remain unmeasured. Phase 3 Strategy A dinner-context acceptance passes. |
| 2026-08-31 | Multilingual household STT follow-up | The household needs Spanish, English, Portuguese, and Italian. English-only Whisper is accepted for the Phase 3 architecture proof but is not the final product backend. Add a separate multilingual model/backend benchmark using the same preview, sentence-level accuracy, latency, memory, and privacy tests; do not regress the accepted English baseline or make cloud wake detection a fallback. |
| 2026-08-31 | Phase 1 benchmark setup | The first direct benchmark utterance received no reply. The sanitized startup log showed one local-processing queue drop and repeated `LocalSTTError` warnings; the one-off `HYBRID_RUNTIME_ENABLED=0` was overwritten by project `.env` loading with `override=True`, so the app entered hybrid/local-STT mode. Project dotenv loading now preserves explicit process values and fills only unset values, with a regression for both paths. No transcript, audio, or credential payload was retained; the direct benchmark must be rerun. |
| 2026-08-31 | Phase 1 direct headphone acceptance | The corrected direct Gemini run connected in 637.5 ms. Five turns sounded smooth; both requested barge-ins worked; `move_head(left)` executed visibly and its provider result was accepted in 2.6 ms. Interruption flushes measured 1.4 ms and 3.8 ms (P50 2.6 ms). The process exited without crash or traceback. No usable speech-end marker or first-audio sample was emitted, so exact first-audio latency remains explicitly unavailable. The controlled Mac-speaker condition and repeated connection samples remain. |
| 2026-08-31 | Phase 1 direct speaker acceptance | The controlled Mac-speaker run connected in 455.6 ms and immediately entered acoustic feedback, producing six self-triggered interruption flushes between 0.3 ms and 1.1 ms in about 18 seconds while the user stayed silent. The process remained live and exited without traceback. Combined with the smooth headphone run, this closes Phase 1 with headphones required and exact first-audio latency unavailable rather than fabricated. |
| 2026-08-31 | Phase 3 Strategy B live acceptance | With `openai/whisper-small.en`, keyboard activation transcribed the 20-second RAM-only snapshot once and produced 68 context characters; Gemini answered the dinner question contextually. Local STT took 6.015 seconds, context preparation took 6.017 seconds, Gemini connection took 472.6 ms, and activation to Gemini ready was approximately 7.228 seconds. The runtime returned to `LOCAL_AMBIENT` after a 70.574-second session with Gemini closed, zero pre-activation ambient cloud bytes, zero cloud video frames, and no STT failure, timeout, or drop. Post-run macOS physical footprints were 2.5 GB for Transformers Serve and 185.7 MB for the app; peak CPU and first-audio remain unmeasured. Capture-worker warnings reached 200 dropped frames during the run and remain a performance limitation to investigate. |

| 2026-09-01 | Phase 1 native search | Added managed `google_search` to every Gemini Live session alongside profile-selected application functions. The focused fake-session suite passed 52 tests; the full repository gate passed Ruff, formatting, strict mypy across 60 source files, all 833 tests with one explicit native-audio skip, lock consistency, JavaScript syntax checks, and `git diff --check`. No live search, flight-data quality, or billing measurement was claimed. |

## Backlog

### Restore laptop-speaker simulator testing and AEC

The app-owned GStreamer output is a containment workaround introduced after
repeatable SDK LOCAL `audiomixer` crashes. It bypasses the SDK playback path and
therefore sits outside the SDK echo-reference path; laptop-speaker output can
return through the Mac microphone and make Gemini interrupt its own response.
Headphones are the current reliable simulator control.

Scope the future fix to a supported full-duplex/shared echo-reference path or
an upstream SDK solution. Do not monkey-patch SDK or GStreamer internals. The
acceptance plan must include automated contract tests, the opt-in native
pipeline smoke, and live simulator/Gemini speaker and headphone echo/
interruption tests, including process liveness and clean shutdown. Keep the
app-owned bypass opt-in until that evidence exists.

## Next steps

1. Follow the [custom multilingual openWakeWord training
   plan](custom-reachy-openwakeword-training.md). Reuse the 30 private positive
   fixtures as an untouched holdout, use synthetic multilingual training data,
   and keep the initial rented-GPU budget at $10 unless the user explicitly
   approves more. Do not make the user rerecord the current positive screen,
   and collect negative exposure only after a model clears the positive-recall
   gate.
2. If a standalone result passes, add a replaceable acoustic detector adapter
   that consumes the existing normalized 16 kHz stream without owning the
   microphone. Preserve the transcript detector as fallback, keyboard
   activation, zero ambient cloud traffic, and comprehensive unit/integration/
   privacy/error tests. Keep Picovoice Porcupine as a separately reviewed
   proprietary fallback, not a prerequisite for the custom-model experiment.
3. Complete Phase 5 quantitative evidence with ten trials per local command and
   a 15–30-minute paced run. Record end-to-end speech-to-action P50/P95, action
   success, provider construction, cloud bytes, return to ambient, and local
   processing drops; preserve the already accepted functional result.
4. Treat Spanish/English/Portuguese/Italian STT as the next scoped requirement,
   not a blocker for closing the original English Phase 0–5 experiment.
5. Update this plan, the feature documents, and
   [`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md) after each accepted live
   result, run the complete gate, perform adversarial review, commit the finished
   milestone, and give a measured Pi 5 assessment without claiming unmeasured
   performance. Regenerate the architecture SVG from
   [`docs/scheme.mmd`](../scheme.mmd) through the existing workflow when
   available.
6. Revisit the [laptop-speaker/AEC backlog](#restore-laptop-speaker-simulator-testing-and-aec)
   only after a supported full-duplex/shared echo-reference path or upstream
   SDK solution is available.

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
| 2026-08-30 | Recorded ready-made wake-word research and kept acoustic model selection pending measured quality, platform/resource results, and licensing review. |
| 2026-08-30 | Added bounded split-final wake follow-up handling, config, docs, and deterministic lifecycle/privacy coverage; live wake/command acceptance and latency remain pending. |
| 2026-08-30 | Adjusted the split-final default from one to two seconds after review of the 0.6-second silence boundary and local STT delay; the wait remains bounded and no live latency claim is made. |
| 2026-08-30 | Added the explicit simulator speaker-output containment after three repeatable SDK mixer crashes; default SDK behavior is unchanged, adversarial review and the complete automated gate passed, and live effectiveness is not yet claimed. |
| 2026-08-30 | Prioritized an autonomous smoke harness before correcting the newly observed non-exact local route and app-owned playback clock, reducing future human testing to microphone acoustics, perceived quality, and hardware acceptance. |
| 2026-08-30 | Closed the HybridRuntime post-close frame race: late frames are dropped before local observation, while open-runtime direct processing remains covered. Focused hybrid tests and the full dotenv-isolated suite pass. |
| 2026-08-31 | Selected `Tito` (Spanish pronunciation) as the product name and default wake phrase; updated the household profile identity and exact-match default coverage, retained `Reachy`/`computer` as historical controls, and recorded that the reviewed acoustic models target Reachy rather than Tito. Added a scoped laptop-speaker/AEC restoration backlog requiring a supported full-duplex/shared echo-reference or upstream SDK solution plus automated/native/live echo and interruption tests. |
| 2026-09-01 | Phase 4 Tito transcript-wake baseline | The first controlled `Tito, look left` run produced three accepted final local transcripts from four STT requests but zero exact wake detections, zero local commands, and zero cloud activation/audio. One STT request timed out; the three completed requests totaled 54.045 seconds with a 15.084-second maximum, while 861 local-processing frames were dropped and the SDK reported downstream capture pressure. Rapid repeated utterances amplified the backlog. This is a failed English-Whisper transcript-wake baseline, not a microphone or privacy failure. Enable the default-off loopback RAM preview for one paced utterance to identify the model's spelling without logging or persisting it before choosing the next detector/model step. |
| 2026-09-01 | Phase 4 Tito paced-preview result | The single-utterance preview diagnostic observed 93.384 seconds of local audio and 80 speech frames, but both STT requests timed out at the temporary 20-second limit. No transcript segment or wake event existed to preview. The runtime remained in `LOCAL_AMBIENT`, Gemini stayed closed, and ambient cloud bytes remained zero, while local-processing drops reached 3,269 and the SDK reported downstream capture pressure. Continuous Whisper is therefore rejected as the product wake detector on this Mac baseline; retain it for ambient-context experiments and move Phase 4 to a purpose-built local acoustic Tito detector after model/training-data/license review. |
| 2026-09-01 | Phase 4 acoustic-wake research | Official Hugging Face guidance provides fixed-label Speech Commands classification rather than arbitrary custom wake phrases, and HF speech-to-speech provides VAD/STT but no wake stage. Selected the community Spanish `jguerrisi/sherpa-onnx-kws-zipformer-es-3M-2026-08-08` checkpoint for a standalone first benchmark with the established sherpa-onnx open-vocabulary engine: it accepts runtime-configured `Tito`, 16 kHz shared-stream audio, and needs no phrase-model training. No dependency/model was added. Quality, CPU/RSS, false positives/misses, Mac/Pi deployment, and model-data/voice redistribution terms remain unverified. Picovoice Porcupine is the proprietary AccessKey-based fallback; openWakeWord requires custom training. |
| 2026-09-01 | Phase 4 fixture benchmark foundation | Added the optional `wake-benchmark` group with exact `sherpa-onnx==1.13.6`, required `sherpa-onnx-core==1.13.6`, and undeclared tokenizer runtimes `sentencepiece==0.2.2` and `pypinyin==0.55.0`, plus a standalone CLI for caller-owned model assets and WAV/raw PCM16 fixtures. Deterministic tests cover model/token validation, exact `tito`/`@tito` filtering, TP/FN/TN/FP classification, bounds, per-fixture latency, real-time factor, trailing blanks, cleanup, privacy, errors, and CLI behavior. No model is bundled or downloaded by the tool, no production adapter was added, and the required live quality/resource run remains pending. No `click` pin was needed because the CLI uses standard `argparse`; tokenizer assets from reviewed revision `b55eae785857eba4af1e24b200104354be23a975` generated `▁ti to @tito`, but no model-weight decoder or live acoustic benchmark was run. |
| 2026-09-01 | Phase 4 benchmark hardening | Added a total decode-step bound across input and tail draining, explicit offline fixture-start-to-result timing fields with positive-only aggregate timing, negative audio exposure/event-based false positives per hour, fixture-level recall, path-free model SHA-256 fingerprint, installed sherpa/tokenizer versions, UTC start time, sample rate, and privacy-safe platform identity. Invalid fixtures abort; offline mode reports no queue/drop metric and collects no hostname, username, CPU model, or paths. The reviewed source revision `b55eae785857eba4af1e24b200104354be23a975` produced `▁ti to @tito`; no live model quality/resource claim follows. |
| 2026-09-01 | Phase 4 benchmark clean-failure hardening | Pinned `sherpa-onnx-core==1.13.6` because clean-install metadata requires it; bounded detection inside input/tail decode loops, wrapped tokenizer/model `AssertionError` setup failures without path leakage, rejected empty/truncated decoded WAV payloads and boolean integer knobs, and expanded the focused deterministic suite to 47 passing tests. No live quality/resource claim follows. |
| 2026-09-01 | Phase 4 benchmark timing protocol | Per-fixture and positive-only aggregate offline timing now use only the first exact `tito`/`@tito` detection; duplicate exact events remain detection/false-positive counts without event-weighting latency. Added deterministic duplicate-result coverage; the focused suite passes 48 tests. Positive fixtures must contain exactly one intended wake, and no live quality/resource claim follows. |
| 2026-09-01 | Phase 1 native search | Added managed `google_search` to every Gemini Live session alongside profile-selected application functions. The focused fake-session suite passed 52 tests; the full repository gate passed Ruff, formatting, strict mypy across 60 source files, all 833 tests with one explicit native-audio skip, lock consistency, JavaScript syntax checks, and `git diff --check`. No live search, flight-data quality, or billing measurement was claimed. |
| 2026-09-03 | Restored `Reachy` as the product/default name while preserving the exact transcript boundary. Generalized the fixture-only sherpa benchmark so the explicit spellings `Reachy`, `Richie`, `Ritchie`, `Richi`, and `Ricci` can map to one canonical `@reachy` event with token-sequence deduplication, exact caller-vocabulary validation, canonical-tag collision/marker-like OOV rejection, and unsafe tokenizer-token rejection. The focused benchmark suite passes 74 deterministic tests and the complete repository gate passes 863 tests with one explicit native-audio skip; no model weights, microphone, production detector, or live quality claim is included. Historical Tito records and the explicit app override remain, while the new Reachy benchmark intentionally rejects Tito. |
| 2026-09-07 | Ran the Spanish sherpa checkpoint against 30 private, technically valid Mac-microphone positive fixtures. Documented settings detected 0/30; the best aggressive setting detected only 2/30, and individual spelling candidates did not rescue recall. Rejected the checkpoint before collecting the negative set. Offline processing measured about 0.015 real-time factor, 167 MiB peak process RSS, and 350% multi-core process CPU on this Mac; these are not production activation or Pi measurements. The private fixtures remain outside the repository for the next candidate. |
| 2026-09-07 | Ran the existing `andyjmorgan` `reechy` openWakeWord ONNX model against the same 30 private fixtures at its pinned upstream commit/hash. It detected 6/30 at the documented 0.5 threshold and only 7/30 at 0.2; 2x/4x gain and peak normalization remained 6/30. Rejected it before negative testing. Offline Mac measurements were 0.036 real-time factor, about 98% process CPU, 188.9 MiB peak RSS, and 0.112-second model load; these are not production activation or Pi evidence. Next action is the linked custom multilingual openWakeWord training plan; Porcupine remains a separately reviewed proprietary fallback. |
| 2026-09-07 | Custom wake training preparation: backed up all 30 private positives outside the repository at the approved location, verified SHA-256 equality and WAV headers, and made the backup read-only. Per-clip pronunciation/language labels remain unknown. Runpod connection verified without creating compute; training, licensing review, CPU dry run, and automatic-stop validation remain pending. No application tests were run for this backup/documentation-only milestone. |
| 2026-09-07 | Updated the custom training plan with saintpete toolkit reference review, a no-cost Mac/Linux public/synthetic inference preflight, multilingual pronunciation checks, isolated compatibility handling, feature/export recovery checks, and Runpod budget/stop verification. No runtime behavior or acceptance thresholds changed; all new execution evidence remains pending. |
| 2026-09-07 | Custom wake preparation: completed local Mac/Linux ARM64 reference comparison (668 identical scores), an English synthetic CPU training/export smoke, and independent exported-model loading on both systems. User corrected the private set to two speakers; pronunciation groups remain unknown. No household evaluation or GPU rental occurred. Full gate passed 863 tests with one optional native-speaker skip; focused wake suites passed 100. Multilingual assets/recipe, CLI authentication, and budget/stop validation remain pending. |

| 2026-09-07 | Runpod CLI setup verified: $10 balance and $0/hour spend; user confirmed automatic top-up disabled. Paid training still awaits completed asset/recipe checks and tested automatic Pod stop. |

| 2026-09-07 | User accepted wake pronunciation previews 1, 2 and 5 and rejected 3 and 4. Added the isolated phonetic candidate recipe, 48-positive/128-negative CPU smoke, independent Mac/Linux export loading, synthetic streaming rejection gate, and nine recovery/stop tests. App gate: 863 passed, one skipped. First paid RTX 4090 shutdown smoke is downloading its image under a 30-minute cutoff; full training and acoustic acceptance remain pending. |

| 2026-09-07 | Custom wake GPU verification blocked after successful dependency installation and verified shutdown controls; stopped hosts refused restart. All experiment Pods/storage terminated, $9.53 remains and $0/hour. Accepted sounds 1/2/5 retained; no full candidate or private screen. Ten guard/cache tests passed; app gate 863 passed, one skipped. See custom training plan for the next isolated GPU diagnostic. |

| 2026-09-07 | Resumed custom wake work: fixed CUDA library lookup, verified GPU feature execution, completed full training and downloaded/hash-checked the synthetic corpus. All Pods/storage deleted; approximately $1.16 spent, $8.84 remains, $0/hour. Candidates 1 and 2 failed synthetic false-activation gates (9.88% and 3.35%); third bounded candidate is training locally with missing accented near-word negatives. Private audio remains unevaluated by custom models; app integration stays gated. Full app gate: 863 passed, one optional skip. |

| 2026-09-07 | Custom candidate 3 detected 400/400 synthetic positives but activated on 36/3,168 negatives (1.136%, above the unchanged 1% gate). All three full attempts are exhausted; further local training requires scope extension. No custom model has screened household audio or entered the app. All rental resources remain deleted; approximately $1.16 spent, $0/hour. No fresh user recordings are needed until the synthetic gate passes. |

| 2026-09-07 | User authorized a fourth local custom-wake attempt, requiring no new rental or originals. It completed 24,000 CPU steps but failed the unchanged synthetic gate: 400/400 positives, 106/3,168 negative activations (3.35%), all Italian-voice recipe phrases. Candidate 3 remains best but rejected; four attempts and 88,000 full steps are used. No private screen or integration. Current app gate: 863 passed, one optional skip, 26.09 seconds. Review training-voice coverage before proposing further models; no new user recordings or Runpod funds are needed now. |

| 2026-09-07 | User authorized an exploratory synthetic-gate exception for custom candidate 3. Frozen Mac/Linux synthetic event counts matched on 3,568 fixtures (maximum score difference 1.55e-6). One network-denied local private screen detected 19/30 positives (17/25 and 2/5 by speaker), failing 27/30; no duplicates. Audio stayed local and out of training. No threshold sweep, negative collection or app integration. Candidate remains rejected; no new rental cost. |

| 2026-09-07 | Authorized local miss/hit diagnosis: all 11 private misses are name-first phrases (6/17 detected versus 13/13 other placements). Loudness ranges overlap; quiet-frame levels match. No private detector replay or training. Paired synthetic concatenation detected 59/60 name-first versus 60/60 alone/name-last, so simple placement does not reproduce the real deficit. Natural connected-phrase coverage is a development hypothesis; a fifth model remains unauthorized and future acceptance needs fresh recordings. No additional rental. |

| 2026-09-07 | Authorized name-isolation diagnostic on 11 private misses: estimated name-only cuts detect 3/11 versus 0/11 full sentences; ±40 ms cuts/fade detect 2–4/11 with zero detections stable across every variant. Automatic-boundary uncertainty prevents a clear causal claim. Original recall remains 19/30; no training or threshold change. Originals/evaluation hashes unchanged, temporary audio deleted, networking denied. Two focused tests and full gate passed (863 passed, one optional skip, 17.90 seconds). |


### 2026-09-07 — Custom wake training target passed

Selected candidate 18 detects 27/30 original recordings, all 400 older
synthetic positives, and only 1/3,168 older negatives (0.032%). New
speaker-disjoint checks are 178/180 positive and 0/60 negative. Training stopped
at the unchanged threshold of 0.5. The original 30 remain local and excluded
from training but are now development data; fresh household positive and
long-negative acceptance still gates installation. No app integration or new
hardware validation was performed.

Starter credits remaining: 16,061. Runpod balance: $8.7798144967, $0/hour, no
pods/volumes and no rental added in this continuation. Full gate passed with
863 tests and one optional skip. The [custom training plan](custom-reachy-openwakeword-training.md)
records every recipe, failure and the selected artifact.


Fresh household screen update (2026-09-07): frozen candidate 18 detected 27/30
new recordings (speakers 13/15 and 14/15; EN 7/8, ES 6/8, PT 7/7, IT 7/7),
with zero duplicates and networking denied. The initial screen passes, but
final household acceptance remains unmet. Review missed clips 01/08/23 before
further collection. New recordings remain evaluation-only; no training,
threshold change, long-negative collection or app enablement was performed.


Corrected fresh-screen update (2026-09-07): the user identified original clips
01/08 as inaudible and supplied two replacements, both detected by frozen
candidate 18. Originals and the original report remain preserved. Corrected
result: 29/30 (96.7%), zero duplicates; speakers 15/15 and 14/15; EN 8/8,
ES 7/8, PT 7/7, IT 7/7. Clip 23 remains a genuine connected-speech miss.
Final acceptance still needs the prescribed multi-session, labeled-stratum
coverage and improved Spanish evidence (currently 87.5%). No negative run,
training, threshold adjustment or app enablement followed. Replacement
evaluation ran with OS networking denied; app tests were not rerun for this
recording-only check. The custom training plan contains the audit details.


Spanish follow-up (2026-09-07): candidate 18 detected 8/10 new speaker-1
recordings with a delayed speaking cue and playback confirmation, zero
duplicates. Missed clips 01/03 were name-first (3/5 name-first, 5/5 name-later).
Audio format/duration/sample checks passed; missed-clip levels were comparable
to detected clips. The descriptive pattern does not establish causation. This
80% session fails the 90% stratum target and stays separate from the earlier
corrected 29/30 result. Final acceptance and enablement remain unmet; investigate
the misses before more household collection. Frozen threshold/model, local
network-denied evaluation, no audio edits or training. Application tests were
not rerun for this recording-only check; see the training plan for evidence.


Connected-speech diagnosis (2026-09-08): inserting a 200 ms gap after the
same wake audio robustly rescues Spanish clip 03 and earlier fresh clip 23,
supporting interference from following speech. Clip 01 remains unresolved;
gain/startup controls did not rescue either Spanish miss. Passing clip 09
fails under some 20–60 ms onset shifts, exposing timing sensitivity. These
are temporary local diagnostics, not acceptance improvements: Spanish remains
8/10 and corrected earlier fresh remains 29/30. No training or enablement.
See the custom training plan for controls, private reports, and full gate
results (863 passed, 1 skipped; lint/format/type checks passed).


Connected-speech refinement (2026-09-08): candidate 19, trained locally on
synthetic-only additions, detects 30/30 original and 30/30 corrected later
development recordings. Spanish remains 8/10 (01/03 missed); clip 09 now
survives all four timing phases and earlier clip 23 detects. Synthetic gates
pass, but older negative activations increase from 1 to 7/3,168 and prior
speaker-validation positives drop from 178 to 177/180. Candidate 19 is a
promising development alternative, not accepted or enabled; candidate 18
remains preserved. Cost: 4,269 ElevenLabs credits, $0 additional Runpod.
No more recording needed for this experiment. See the custom training plan
for exact protocols, caveats, reports and validation (863 passed, 1 skipped;
lint/format/type checks passed).


Timing diagnosis (2026-09-08): both remaining candidate-19 Spanish misses
pass when locally time-stretched to 80% or 90% speed across all four tested
onset phases. Clip 01 also passes at 110%, so a simple speech-speed limit
is not supported. Gain controls do not rescue either miss; synthetic Spanish
stays 36/36 under all tested speed/gain controls. This points to temporal/
context sensitivity and an insufficiently challenging synthetic test set.
Actual Spanish remains 8/10; diagnostic transforms do not improve acceptance.
Next proposed experiment is pitch-preserving timing augmentation with synthetic
stress cases that fail the frozen candidate. No training, spending or runtime
change occurred. Full gate: 863 passed, 1 skipped; details in the training plan.


Pitch-preserving refinement (2026-09-08): candidate 20 improves Spanish
from 8/10 to 9/10 at original timing and all three shifted phases; clip 01
now detects, clip 03 still misses. Both earlier development sets remain
30/30. Timing stress validation is 216/216 positives, 0/72 negatives;
older synthetic negatives rise to 13/3,168 (0.410%), an explicit regression.
Candidate 20 is a preferred development alternative, not enabled or accepted.
Fresh independent household acceptance is the next stage; reused development
sets do not establish it. No provider spending or app code change. Full gate:
863 passed, 1 skipped; detailed commands and evidence in the training plan.


Fresh Spanish acceptance (2026-09-08): frozen candidate 20 detected 8/10
new speaker-1 recordings at approximately 40 cm, with zero duplicates.
Misses 02/09; format/hash/duration checks passed, no clipping, missed-file
levels within the detected range. This fails the 90% Spanish target; keep
separate from development results. No training, threshold change, negative
acceptance or enablement. Local network-denied evaluation; reports stay private.
See the custom training plan for evidence and next investigation step.


Acceptance-miss diagnosis (2026-09-08): candidate 20 detects both missed
clips after only a 20 ms leading-silence shift, but that shift loses clip 07.
This confirms streaming phase sensitivity; a fixed delay is not a general fix.
Gain does not rescue either miss, and slower tempo rescues only clip 09.
Next proposed synthetic-only experiment: multiple timing alignments with shared
cooldown, testing false activations and CPU/latency before runtime changes.
Original fresh acceptance remains 8/10; diagnostic union is not acceptance.
No spending, training or enablement; full evidence in the custom training plan.


Dual-alignment result (2026-09-08): reject the offline 0/20 ms maximum-score
combination. Synthetic negative activations increase from 13 to 48/3,168
(1.515%), and tempo negatives from 0 to 2/72 (2.778%), exceeding the 1% gate.
Positive detections are unchanged and shared cooldown prevents duplicate
positive events in this corpus. Predict cost roughly doubles on this Mac,
but stays below real time in the small synthetic benchmark. No private dual
screen, app integration, spending or enablement. Single-lane fresh acceptance
remains 8/10. See the custom training plan for exact protocol and limitations.


Stricter alignment rules (2026-09-08): all four fixed confidence/persistence
rules rejected on synthetic-only evidence. The best negative count is 24
with >=0.9, versus the current single lane's 13; it also loses one positive
and exceeds 1% on tempo negatives. Same-lane consecutive evidence still
produces 42 negative activations. No rule selected or tested on private audio;
no app integration, spending or enablement. Fresh acceptance remains 8/10.
Details, delay tradeoffs and full gate results are in the custom training plan.


Explicitly authorized household adaptation (2026-09-08):80 existing usable
recordings now belong to training, with all variants kept together; previous
reports remain historical and new independent validation is required.
Candidates21/22 both detect80/80 training originals, including all former
misses, but both fail the tempo-negative gate at1/72. Synthetic positive
counts remain400/400,216/216,72/72 and177/180 across existing sets. Prefer21
only for further investigation (23 vs24 old negative activations); neither
is accepted or enabled. Models/features stay in private household-adaptation21,
never the cloud-training directory. No provider spending. Docker hit a
read-only filesystem error; correction finished natively without restarting
Docker. See the training plan for exact evidence and dependency/runtime details.

2026-09-08: candidate23's focused training-negative refinement did not meet
the synthetic gate (tempo1/72, old25/3168); household training fit80/80 is not
acceptance. Candidate21 remains the investigation artifact, not enabled.
Need harder training-side negative coverage before fresh acceptance. Docker
restarted by user and verified; isolated local work, zero provider spending.
Full gate863 passed,1 skipped; details in custom wake-word training plan.

2026-09-08: harder acoustic training negatives produced candidate24, passing
all fixed synthetic gates with unchanged positive detections and reduced false
activations (old14/3168, all three other sets0). Household80/80 remains training
fit. Freeze24 at0.5 for fresh multi-speaker/session acceptance, not runtime
activation. Private local training, no spending; full gate863 passed,1 skipped.

2026-09-08: frozen wake candidate24 fresh Eduardo Spanish batch9/10, zero
duplicates; clip02 miss. Positive acceptance unmet; lunch20m56s audited but
unscored. New batches remain private/evaluation-only; no runtime change.

2026-09-08: candidate24 fresh miss diagnosis confirms streaming timing
sensitivity (40ms leading silence rescues unchanged audio). Keep original9/10
acceptance result, no fixed-delay workaround or negative scoring. Next proposed
work: training-side positive timing coverage while retaining hard negatives.

2026-09-08: candidate25 completed after disk recovery. Synthetic gates pass;
Spanish development10/10, training80/80. Tradeoffs: old false activations14 to25/3168
and one training duplicate. Review duplicate before fresh acceptance; not enabled.
Local/private, zero spending; full gate863 passed,1 skipped. See training plan.

2026-09-08: candidate25 duplicate localized to quiet opening audio at1.40s;
first2s alone reproduces it, digital silence does not. Main event follows after
cooldown. No runtime fix or retraining; verified training-negative segments
are the next proposed refinement. Exact sound identity remains unverified.

2026-09-08: candidate26 fixes duplicate, retains training80/80 and development10/10.
Synthetic gates pass: old negatives24/3168, others0. Freeze26 at0.5/3s cooldown
for new independent acceptance; not enabled. Private/local, zero spending;
full gate863 passed,1 skipped. See custom training plan for labeling caveats.

2026-09-08: frozen candidate26 passes genuinely fresh Eduardo Spanish10/10,
zero duplicates at about40cm. Remaining speaker/session/language acceptance
pending; lunch unscored, model not enabled. No training or spending.

2026-09-08: candidate26 fresh Adriana Spanish10/10, zero duplicates; combined
fresh adults20/20. One session each; remaining coverage pending, not enabled.
Optional child benchmark separate and non-gating. Speaker override documented.

2026-09-08: Eduardo multilingual session2:11/12 (EN3/4,PT4/4,IT4/4), no
duplicates. Miss01; combined fresh adults31/32. English stratum/coverage
not accepted; model frozen, no tuning/enablement/lunch scoring.

2026-09-08: English miss diagnosis:tempo0.8 rescues; gain/small phase changes
do not. Preserve original3/4 English, continue planned independent coverage
with26 frozen; no immediate retraining or acceptance claim.

2026-09-08: Adriana session2 passes12/12 (EN/PT/IT4/4 each), zero duplicates.
Combined fresh adults43/44; one earlier Eduardo English miss. Two sessions each,
remaining coverage pending;26 unchanged, no spending or enablement.

2026-09-08: authorized provisional lunch screen found22 candidate false wakes
in20m56s (~63/hour observed, pending accidental-wake-phrase review). Do not
enable26; private excerpts prepared, lunch remains outside training.

2026-09-08: lunch confirmed entirely non-wake. Model20/24/25/26 false events
13/12/28/22. Confidence0.99 still8 under26; persistence3 still12. No candidate
ready for enablement. Need conversational training negatives and independent
validation; no lunch fitting or runtime change in this diagnosis.

2026-09-08: authorized lunch-trained27:training replay22->1 false wake; synthetic
negatives24->7, adult development43/44 maintained, older Spanish development
10/10->9/10. Freeze for untouched family-negative test, not enabled. Lunch now
training-only; all private/local, zero spend. Full gate863 passed,1 skipped.

2026-09-08: candidate27 untouched family26m17s yields1 trigger at7:07.4.
Review excerpt pending label; no training/tuning or enablement. Local/private,
zero spending; full gate863 passed,1 skipped.

2026-09-08:27 untouched dinner26m38s yields11 triggers pending labels; prior
family single event confirmed false.12 events/52m55s across independent
recordings; no enablement, no fitting/tuning. Private excerpts saved.

2026-09-08:28 rejected: lunch/dinner training0 false wakes, excluded family
still1; adult development43/44->41/44 and older Spanish9/10->8/10. Preserve27
baseline, none enabled. Stop fine-tuning loop; assess separate local verifier
on existing material before more recordings. Full gate863 passed,1 skipped.

2026-09-08: cached local Whisper-small verifier rejected:blocks13/13 false
triggers but positive52/54->31/54, median1.536s compute+1s audio wait. Full-clip
control rescues4/21 rejects only. No integration, spending or further recording
request for this recipe. Full gate863 passed,1 skipped.

Pipeline review (2026-09-08): no accepted acoustic model. Confirmed missing
keyword-location supervision/scoring and limited independent negative coverage.
Bounded streaming extraction comparison matched335 frames exactly; original
feature extraction settings reproduced the checked bag. Next design is
localized supervision and timing-aware evaluation before another training run;
see the custom openWakeWord plan. No recordings, provider spend or runtime
changes. Repository gate863 passed,1 skipped; Ruff/format/mypy passed.

Localization implementation (2026-09-08): timing scorer passes16 focused tests;
134 draft alignment records produced locally (88 proposals,46 unresolved). No
independently reviewed intervals exist, so no localized training ran and no
new model was enabled. Boundary review of existing audio is the next dependency.
Full gate863 passed,1 skipped; see custom training plan for private artifacts
and the local-review browser verification limitation.

Automatic localization (2026-09-08):73/80 training clips pass consistency
heuristics;7 excluded. Bulk manual-labeling handoff replaced by eight short
listening checks covering four languages/two speakers. Automatic candidates
remain unverified; no training or enablement. See custom plan for evidence.

Four-arm localization comparison completed (2026-09-08): user accepted the eight
alignment spot-checks;73 automatic pseudo-label sources used equally across all
arms. Twelve checkpoints evaluated locally against baseline27. Three retain
52/54 development detections with zero duplicates, but every checkpoint retains
the excluded-family false wake. Best recall-preserving results: lunch1, family1,
dinner3 versus baseline1/1/11. Lunch/dinner are training replays, so this is not
proof of generalization. All checkpoints rejected by the predeclared rule; no
further synthetic screen or runtime enablement. No provider cost or audio upload.
Full gate863 passed/1 skipped;20 private timing/mask/aggregation tests passed.
Detailed matrix, artifacts, memory fix and limitations are in the active custom
wake-word training plan. No new independent acceptance or hardware test.

Diagnostic30 complete (2026-09-08): current features can reject the persistent
family frame, but the prototype is not acceptable. Exploratory past-only1s
confirmation keeps52/54 development detections and reduces conversation events
13→1 (remaining lunch training replay). Full4192 synthetic regression fails:
diverse-voice detections177/180→134/180; other positive groups unchanged and old
negative activations7→5/3168. All4192 baseline fixture counts reproduce exactly.
No runtime enablement or spending. Next hypothesis, not started: broaden the
confirmation classifier TRAINING coverage using existing synthetic training
voices and training negatives; keep all development/validation sources excluded.
Full gate863 passed/1 skipped;4 causal tests and54 per-clip streaming parity checks
passed. Full artifacts, metrics and limitations are in the custom training plan.

Broadened confirmation31 completed (2026-09-08): one fixed fit with existing
synthetic training voices and broader lunch/dinner negatives. Household49/54
versus baseline52/54; lunch/family/dinner false events1/1/0. Diverse synthetic
voices improve134/180→150/180 versus prototype30, but remain below baseline
177/180; older/tempo/connected positives regress to375/400,197/216,66/72.
Rejected: no runtime enablement or further automatic fit. All57 household and
4192 synthetic baseline counts reproduce; all4192 validation WAV hashes checked
with zero training intersection. Full gate863 passed/1 skipped and4 private
causal tests passed. No spending, uploads or fresh acceptance. Full matrix and
provenance caveats are in the active custom wake-word training plan.

Exact-event verifier32 completed (2026-09-08): scores only the current baseline
trigger features, with no independent verifier history or timer. Reused household
calls 51/54 versus baseline 52/54; lunch/family/dinner false events all zero versus
1/1/11. Older/tempo/connected synthetic positives unchanged; diverse voices
176/180 versus 177/180, with Portuguese variant2 at 13/15 below the 90% gate.
Not promoted: one extra household miss and failed synthetic stratum. Negative
fit examples represent only two recordings. All 57 household and 4,192 synthetic
baseline event counts reproduce; all validation WAVs rehashed with zero fitting
overlap. Full gate 863 passed/1 skipped; 2 private event tests passed. No runtime
change, spending or independent acceptance. Full evidence is in the custom plan.

Fresh32 Eduardo screen (2026-09-09): 9/10 detections with no duplicates at roughly
40 cm. Baseline27 also 9/10; clip02 had no baseline event. ES3/4, EN/PT/IT2/2 each.
Accepted sources validated and scored with frozen hashes under OS network denial;
retakes excluded, no fitting or runtime enablement. Adriana and fresh negative
conversation remain pending. Full receipt and status are in the custom plan.


Fresh32 Adriana screen (2026-09-09): baseline27 and frozen verifier32 both detect
9/10, zero duplicates, at approximately 40 cm. Clip04 produced no baseline event.
ES 3/4; EN, PT and IT each 2/2. Combined fresh adult batches: 18/20, zero
duplicates; Spanish 6/8 and other languages each 4/4. Network denied during local
evaluation, no training overlap or parameter changes. Model remains disabled;
earlier preservation failures remain. Only the planned fresh conversation remains
for this assessment. No additional call batches or automatic retakes requested.


Frozen32 fresh assessment complete (2026-09-09): the confirmed no-Reachy
fresh32-conversation-z4o6tgyc recording lasts 2969.9093125s (49.50 minutes).
Baseline27 produces 14 false activation events; verifier32 accepts 3, rejecting
11/14. Observed final false activation rate: 3.6365/hour, above the provisional
maximum 1/hour. This single exposure is not a long-term rate guarantee.
Fresh adult calls remain 18/20 (90%), Spanish 6/8 (75%), each speaker 9/10,
EN/PT/IT each 4/4, zero duplicate positive triggers. The 95% aggregate and 90%
language recall targets are unmet, and earlier preservation failures remain.
Decision: reject frozen32 for runtime admission; keep disabled and stop this
assessment. No further recordings requested, no automatic fits or threshold
sweeps. Any different design is a separate experiment with scope agreed upfront.
All scoring used frozen hashes, threshold 0.5 and baseline cooldown 3s under OS
network denial. Conversation hash has no verifier training-source overlap;
private frozen32-started.json and frozen32-results.json preserve the receipts.
No uploads, provider spend, training, or runtime changes.


2026-09-09 frozen32 error diagnosis: both fresh misses have extremely low baseline
maxima (Eduardo 0.00754, Adriana 0.000151 against 0.5); modest sensitivity changes
cannot recover them. False-event verifier scores are 0.735–0.862. Training negative
coverage is only two conversations. Recommend separate-data main-detector coverage
and negative-diversity improvements, not threshold tuning on these evaluation
sources or independent use of the current event-only verifier. No changes to
models/runtime; diagnosis and limitations recorded in the custom training plan.

2026-09-09: official33 stock DNN/auto_train baseline completed locally without a
verifier. Fresh replay: Eduardo 10/10, Adriana 7/10; latest 49.5-minute conversation
1 false wake. Older calls 50/54 and synthetic preservation gates fail. Rejected
for runtime; no new recordings, tuning or spend. Uses cached features, not a full
stock synthesis/augmentation rerun. Full provenance and limits are in the custom
training plan; official baseline is complete and the architecture comparison deferred.

### Official34 aligned ElevenLabs baseline — 2026-09-09

Completed and rejected. One stock openWakeWord DNN/auto_train fit added locally
aligned ElevenLabs TRAIN speech with upstream augmentation; no verifier. Latest
call replay: Eduardo 10/10, Adriana 6/10, combined 16/20, zero duplicates. Spanish
6/8, English 2/4, Portuguese and Italian 4/4 each. The 49.50-minute confirmed
negative conversation produced 7 false events (8.4851/hour observed), versus 1
for official33. Older household calls: 46/54, versus official33's 50/54 and
candidate27's 52/54. Lunch/first-family/dinner false events: 8/1/8.

Synthetic recall improved over official33: old 400/400, tempo 204/216, connected
71/72, diverse 164/180. Negative events: old 138, diverse 1, tempo/connected 0.
This does not compensate for household recall and false-wake regressions. No
runtime admission, extra fit, threshold sweep, new recording request or paid call.
Protected evaluation audio remained outside training; replayed sources are now
design-informed regression evidence, not independent acceptance. No hardware,
SDK integration or latency validation occurred.

Private `official34` preserves alignment/source/augmentation receipts, input
checks, training/export hashes, complete replay scores, per-language/voice/variant
strata, decision and artifact checksums. Export parity passed 256 examples
(max absolute error 1.228e-5; identical threshold decisions). Adapter tests: 2
passed. Full gate: Ruff check/format and mypy passed; pytest 863 passed, 1 skipped
in 13.19s. See the custom training plan for preparation counts and limitations.


### Human headless conversation acceptance — 2026-09-10

User reports the headless trial worked well: a conversation followed by an
explicit request to shut down. The supplied log confirms acoustic activation
at 17:04:44, Gemini readiness, responses/tool calls and interruption flushes,
then go_to_sleep at 17:06:21 and Shutdown complete at 17:06:27. The daemon
stop-current-app request returned HTTP 400, but local cleanup completed and
the shell prompt returned. Capture drops still reached 100 in this run; do not
claim drop-free performance or that headless mode fully resolves overload.
This accepts the supervised headless wake/conversation/shutdown path. It does
not verify inactivity rearm or ordinary-chatter false wakes in this session,
visible viewer reliability, or physical hardware. Training remains paused.
Validation: user report and supplied log review only; documentation-only update,
no code or model changes and no test suite rerun.

## Pre-GPT-Live checkpoint — 2026-09-10

User requested a local commit of the accepted Gemini/headless companion and
Spotify milestones before any GPT-Live migration. Full gate passed: Ruff check
and format, mypy (62 source files), pytest (916 passed, 2 native opt-in skips,
19.85s), uv lock check, JavaScript syntax checks and git diff check. Credential
value scan of 48 changed/new files found no matches; no private media/models
are included. Existing graphical/AEC/hardware limits and pending generated
architecture SVG remain documented. No GPT-Live implementation or default
provider switch is part of this checkpoint.


## GPT Live simulator checkpoint — 2026-09-10

Implemented `openai_live`: GPT Live speech with Luna Responses delegation for
existing tools and requested camera images. Preserved local acoustic wake and
ambient privacy, with Gemini available as fallback. The user accepted live
conversation and automatic return to standby after the quiet-output timer fix.
Real API synthetic silence also verified automatic close; full local gate passed
967 tests with 2 skips. Individual GPT Live tool/interruption/reactivation checks,
physical hardware performance and generated architecture SVG remain outstanding.
See [GPT Live migration](openai-live-migration.md) and the
[provider feature record](../features/openai-live-provider.md) for evidence.


## Household memory follow-on — 2026-09-10

The [household memory lifecycle plan](household-memory-lifecycle.md) records the
next companion improvement: invisible notes, person attribution, temporal
relevance and bounded daily consolidation. Milestone 1 was implemented on
2026-09-11, with local storage/tools behind a default-off flag and 1013 tests
passing (2 native opt-in skips). The user accepted M1 and authorized M2 invisible
capture and local review on 2026-09-11. M2's spoken capture, Settings inspection
and recall after standby passed a user simulator trial later that day. Correction
and Settings deletion also passed subsequent user checks; the revised quiet-skip policy passed a subsequent spoken trial. Corrections now
preserve the active conversation. Spoken forgetting also keeps it running, pausing
automatic capture until the next wake; Settings Forget still resets it. Continuity
has automated coverage and awaits a spoken check. Daily review remains gated; this does not expand
phases 0–5 or enable background cloud work. Voice/face recognition remains deferred.
