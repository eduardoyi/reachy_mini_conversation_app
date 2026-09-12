# GPT Live migration

- Status: `implemented; simulator conversation and standby accepted`
- Owner: Reachy project
- Created: 2026-09-10
- Last updated: 2026-09-10

## Scope and decisions

Add an explicit GPT Live provider: `gpt-live-1` handles live speech, and
`gpt-5.6-luna` handles delegated reasoning, existing tools, and requested camera
images through Responses. Preserve Gemini and Hugging Face. Keep the accepted
Official33 wake model and the local ambient privacy boundary unchanged.

User authorized implementation after checkpoint commit `54bd16a`. This is local
implementation; no upstream issue, PR, or push is part of the task. Luna coding
agents execute bounded modules; the primary agent integrates and reviews them.

Use client delegation so the app owns tool lifetime, cancellation and image
routing. Live cannot accept images. Its delegation event contains an ID and
timeline offset, so the app must retain bounded transcript fragments for Luna.
Do not treat transcript fragments as authoritative completed turns.

Use the documented public Live WebSocket protocol with `websockets>=15,<16`
(15.0.1 was already installed transitively), retaining OpenAI SDK 2.28.0 for
Responses. This is an explicit dependency declaration: BSD-licensed transport,
no independent telemetry, cross-platform, connects only when the provider starts.
No SDK internals are patched. Regenerate the lock and run its consistency check.

## Implementation order

1. Add provider configuration, readiness and UI selection without changing defaults.
2. Implement bounded Responses tool delegation and camera image routing.
3. Implement Live handshake, audio, transcripts, delegation and shutdown.
4. Review protocol, cancellation, privacy and compatibility; run focused/full gates.
5. Check account/model access with bounded synthetic requests if a key exists.
6. Run a fresh human simulator conversation, Spotify tools, interruption and standby.

## Acceptance and test strategy

- Fake transport proves no microphone send before readiness and no late sends after shutdown.
- Hybrid runtime retains zero cloud construction/traffic in local ambient mode.
- Fragmented input audio converts consistently to mono PCM16 at 24 kHz.
- Delegation routes existing tool calls and images to Luna; Live receives short text results.
- Tool budgets, errors, timeouts and cancellation cannot replay side effects.
- Existing Gemini/Hugging Face tests stay green; settings never accept or expose keys.
- Full Ruff, mypy, pytest and lock checks pass; JavaScript syntax checks pass.
- Live account access and human audio acceptance are reported separately from fake tests.

## Risks and limits

Live is billed for connected duration ($0.05/minute in the launch documentation),
with Responses billed separately. Keep inactivity shutdown enabled. There is no
claim that local tests establish robot/Pi performance, echo cancellation or
perceived interruption quality. Live output audio has no done/timing marker;
do not invent a Realtime-style voice turn or interruption protocol.

## Documentation

Update README, environment template, feature/index documents, main companion
plan and architecture source. The generated architecture SVG needs regeneration
through the diagram workflow; never hand-edit it.

## Progress

- 2026-09-10: Verified official Live WebSocket, delegation and conversation docs;
  split implementation into provider, backend and configuration/UI tasks.
  Implementation and validation are in progress; no live acceptance claimed.
- 2026-09-10: Completed implementation and two adversarial review passes. Fixed
  cancellation-resistant tool continuation, sensitive camera-result retention,
  transcript/task trust separation and retryable bounded backend shutdown.
  Focused OpenAI/provider/manager validation passed 71 tests; the full local suite
  passed 965 tests with 2 explicit skips. Live account, simulator audio and
  hardware acceptance remain pending.
- 2026-09-10: Local key-presence check found no `OPENAI_API_KEY`; asked the user
  to add it to the ignored environment file. This blocks only live account and
  conversation checks. Configuration/UI focused tests passed (251 tests).
  Primary review requested stateless tool-history continuity, bounded cleanup,
  transcript/backchannel handling and actual handler/runtime integration tests.

- 2026-09-10: Implementation and primary review completed. Luna agents delivered
  configuration/UI, Live transport and Responses delegation. Review corrections
  covered stateless tool history, truthful failed responses, cancellation despite
  dispatcher cancellation handling, exact PCM passthrough, bounded delegation,
  and transcript context. Focused provider/backend/hybrid suites: 28 passed.
  Full gate: Ruff passed, formatting unchanged, mypy passed (64 source files),
  pytest 956 passed / 2 skipped, lock consistency and JavaScript syntax passed.
  No API key was present; no paid calls or live acceptance were performed.

## Remaining acceptance

1. Account access verified: local key configured, synthetic Luna response completed,
   and GPT Live handshake and graceful close confirmed on 2026-09-10.
2. Conversation and inactivity standby are user-accepted. Verify individual
   Spotify search/play/queue and camera actions, interruption and wake
   reactivation on GPT Live separately.
3. Regenerate the architecture SVG from `docs/scheme.mmd` through the diagram
   workflow. Hardware and Raspberry Pi performance require the physical robot.

Gemini remains available and local provider selection has not been overwritten.
Simulator conversation and standby are accepted; hardware acceptance remains
outstanding.

- 2026-09-10: After the user configured the key, a bounded real account check
  completed a synthetic `gpt-5.6-luna` Responses request and received
  `session.started` then `session.closed` from `gpt-live-1`. No microphone or
  household media was sent. Human simulator acceptance remains pending.

- 2026-09-10: User reported a successful live conversation but no inactivity
  return to standby. A five-second synthetic-silence account probe received 44
  output audio packets and no transcripts (maximum PCM peak 46, maximum packet
  RMS 19.02, about -64.7 dBFS). Every packet refreshed the activity timer,
  including this quiet output. Fix and regression validation are in progress.
  No microphone or household audio was used for this diagnostic.

- 2026-09-10: Quiet output now preserves playback without refreshing activity
  or speaking movement; whitespace-only transcript fragments also do not reset
  inactivity. Primary real API test with actual HybridRuntime and synthetic
  silence reached LOCAL_AMBIENT with connection closed after 3.14 seconds
  (configured timeout three seconds). Human retest remains pending.

- 2026-09-10 inactivity fix validation: regression tests failed with the former
  packet-based activity behavior and pass with the quiet-output guard.
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_openai_live.py tests/test_openai_live_hybrid.py -q`:
  18 passed. Full `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`:
  967 passed, 2 skipped. Ruff check/format, strict mypy (64 source files), and
  diff whitespace checks passed. No dependency or configuration changes.

## Simulator acceptance checkpoint — 2026-09-10

The user confirmed the GPT Live conversation worked well, then confirmed that
return to standby worked after the quiet-output fix. Account access, live
conversation and automatic inactivity standby are accepted on the Mac simulator.
The latest automated gate passed 967 tests with 2 skips, Ruff and strict mypy.
Do not infer individual Spotify/camera tool, interruption, reactivation, physical
robot, Raspberry Pi performance or echo-cancellation acceptance from this report.
Generated architecture SVG regeneration remains outstanding. Gemini remains
available as fallback; no private environment values or media belong in git.

## Local movement repair — 2026-09-12

User approved restoring/checking existing local movement after read-only diagnosis.
Scope: remove the session-wide listening freeze; verify existing SDK audio routing.
No extra Luna calls, dependencies, gesture policy changes, or audio-backend changes.
The documented Live events do not establish user speech start/end, so readiness
must not assert listening. Preserve speaking tracking handoff and shutdown cleanup.

Implementation order: add a failing fake-session regression, correct readiness,
run provider/movement/audio tests and the full repository gate, update provider
documentation and README. Acceptance: session startup and output audio never hold
listening on; SDK output still receives provider audio; standby privacy tests pass.
Risks: listening-specific antenna freeze remains unavailable without reliable user
speech boundaries; the simulator GStreamer bypass still cannot drive SDK wobble.
Human movement acceptance and active launch-route verification remain pending.
Existing uncommitted household-memory work is preserved in place. No PR is planned.

Status: code and automated verification complete; human movement check pending.

Validation on 2026-09-12:

- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_openai_live.py tests/test_openai_live_hybrid.py tests/test_moves.py tests/test_console.py tests/audio/test_speaker_output.py -q`: 120 passed.
- `.venv/bin/ruff check . --fix`: passed.
- `.venv/bin/ruff format .`: 131 files unchanged.
- `.venv/bin/mypy --pretty --show-error-codes`: passed, 67 source files.
- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v`: 1106 passed, 2 skipped.
- `git diff --check`: passed.

No live cloud, graphical simulator, physical hardware, latency, or human movement
trial was run. The source/audio boundary tests use fakes; they do not establish
visible speech wobble. Architecture and dependencies are unchanged.

The two skipped checks were
`test_official33_activates_simulator_without_ambient_cloud_audio` and
`test_optional_native_speaker_subprocess_smoke`; optional native acceptance was
not enabled for this deterministic gate.
