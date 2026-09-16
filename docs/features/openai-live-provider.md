# Feature: GPT Live provider

- Status: `implemented; simulator conversation and standby accepted`
- Last updated: 2026-09-16
- Plan: [GPT Live migration](../plans/openai-live-migration.md)

## Overview

GPT Live provides speech while Luna performs delegated tasks through the existing
tool layer. The app retains one microphone/speaker loop and one selected speech
provider. Gemini remains available. Implementation and automated validation are
complete. Account access and user conversation are confirmed; the user confirmed automatic standby after the inactivity fix. Reactivation,
interruption and individual tool acceptance on GPT Live remain separate checks.

## Architecture and privacy

```text
SDK microphone → HybridRuntime → GPT Live WebSocket → local speaker
                     │                 │
              local standby      delegation request
              sends nothing            ↓
                                Luna Responses → existing tools
                                      ↑               │
                                      └─ requested camera image
                                short result → GPT Live
```

HybridRuntime constructs the handler only after activation. Ambient PCM stays
local; the existing activation barrier and bounded postactivation FIFO remain
the shared boundary. Explicitly derived local text can seed active-session
history. Active speech goes to GPT Live; bounded transcript context, tool
schemas/results, and requested camera images go to Luna. GPT Live receives no
images. No archived household recordings are used for provider validation.

## Contracts and integration

| Setting | Default / meaning |
|---|---|
| `REALTIME_PROVIDER` | Select `openai_live`; `openai-live` is normalized to the same provider. Unset still means Hugging Face. |
| `OPENAI_API_KEY` | Required local secret; missing configuration keeps the settings surface available. |
| `OPENAI_LIVE_MODEL` | `gpt-live-1` |
| `OPENAI_LIVE_BACKEND_MODEL` | `gpt-5.6-luna` |
| `OPENAI_LIVE_VOICE` | `marin` |
| `HYBRID_RUNTIME_ENABLED` | Set `1` for local standby; direct mode connects without a wake gate. |
| `HYBRID_CLOUD_INACTIVITY_SECONDS` | Default `60`; `0` disables it. `30` seconds suits the supervised simulator trial. |

For the already configured simulator, use the existing launch command with
`REALTIME_PROVIDER=openai_live`. Keep the wake model directory, local media
backend and speaker settings from the accepted trial. Selecting `gemini` restores
the earlier provider without changing models or Spotify credentials.

`openai_live.py` implements `ConversationHandler`. `openai_live_backend.py`
performs Responses delegation through the existing tool registry and shared
`BackgroundToolManager`. Configuration/factory/readiness live in `config.py`,
`main.py` and `console.py`; the Settings provider selector uses the same contract.
The adapter must report connection readiness only after the server handshake,
successful media sends through traffic observers, and transcript deltas without
claiming authoritative completed turns.

The public WebSocket endpoint is `wss://api.openai.com/v1/live/sessions`.
`session.start` precedes all other commands; wait for `session.started` before
sending PCM16 mono audio at 24 kHz. Audio arrives through
`session.output_audio.delta`. Client delegation is identified by
`session.delegation.created`; return a short `session.commentary.append` with
that delegation ID. Close the session and transport during standby/shutdown.

Available voices: `marin`, `quartz`, `ripple`, `vesper`, `willow`, `stone`,
`gleam`, `meridian`, `bossa`, `tempo`, `beacon`, `delta`, and `cinder`.
An unsupported startup voice resolves to `marin`; the voice-change method rejects
unsupported selections. A saved voice takes effect on the next session.

## Failure handling and limits

A session failure returns control to the existing supervisor; delegated actions
are never automatically replayed. Explicit standby/shutdown cancels delegated
work and closes the connection. Interrupting speech alone is not a request to
undo an already completed Spotify or movement action.

Timed-out local tools must stop before the backend asks Luna what to do next. If
a cancellation-resistant tool does not stop within the bounded wait, that
delegation aborts without another Responses request. Delegated tool results use
the shared manager without retaining completion notifications or camera bytes.
Backend and provider close failures remain retryable; the provider also bounds
backend shutdown to 15 seconds.

Luna requests use low reasoning effort, disabled response storage and encrypted
reasoning continuity held only in process memory. Voice context and backend
context have separate bounded lifetimes; neither is a persistent memory store.
Responses also exposes native `web_search` for current information, independently
of the profile-managed application tools, matching the existing Gemini managed
search capability. Search results stay in the backend; short findings go to Live.

Current bounds (all held in RAM):

| Boundary | Limit |
|---|---|
| Live setup / close wait | 15 seconds each |
| Late transcript wait | 0.75 seconds |
| Transcript retention | 64 fragments and 12,000 characters |
| Live text appends | 500 UTF-8 bytes, conservatively below the API token limit |
| Delegation concurrency / ID retention | 4 / 128 |
| Busy-result overflow queue | 8 |
| Queued output | 256 items |
| Luna rounds / application tool calls | 4 / 8 |
| Request / tool / total delegation timeout | 20 / 10 / 30 seconds |
| Backend transcript context | 8 entries, 800 characters each |
| Task / tool arguments / tool result / final text | 2,000 / 8,000 / 16,000 / 2,000 characters |
| Camera JPEG | 8 MiB; base64 and JPEG boundary-marker checks |

These limits bound work and memory; they are not measured response latency or a
complete household budget. Native web search has its own per-request tool limit.
Stateless continuation resends earlier relevant tool/image context to Responses;
traffic accounting includes each successful image resend.

Locally recognized addressed text uses `say()` to run a backend task and return
its result through Live commentary. Sending the request itself as commentary
would make Live repeat the request. Cancellation also covers these injected
tasks. Initial local history is supplied to Live at startup; Luna gets bounded,
explicitly marked background context alongside relevant transcript groups.

Transcript grouping uses a three-second gap heuristic and is not an authoritative
turn boundary. Missing or late text may require the user to repeat a request.
Speech movement uses an audio-duration estimate because the protocol has no
audio-done event. Audible interruption quality still needs a human trial.

Output activity uses a -50 dBFS RMS quiet-packet guard (104 PCM16 counts).
GPT Live sends low-level output even during silence; those packets retain their
original playback bytes but do not refresh inactivity or speaking movement.
Non-whitespace transcripts and louder audio still count as activity. This is a
quiet-output heuristic, not a speech classifier.

## Constraints and extension points

- Keep all credentials environment-only. Never log raw protocol events or API errors.
- Keep tools in the existing registry; do not add a parallel Spotify integration.
- Preserve cancellation and bounded work when adding tools or backend capabilities.
- Do not infer completed speech or interruptions from undocumented events.
- Voice, model and initial instructions are startup configuration; changes need a fresh session.
- Add camera inputs only to Responses after an explicit tool request and successful capture.
- Live duration and backend token usage are separate charges. Inactivity close limits idle cost.

Dependencies: the public `websockets` transport is now declared directly;
OpenAI SDK 2.28.0 remains the Responses client. No SDK internals are modified.

## Validation inventory

Validated on macOS on 2026-09-10:

- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_openai_live.py tests/test_openai_live_hybrid.py tests/test_openai_live_backend.py tests/tools/test_background_tool_manager.py -q`: 71 passed. Covers transport/audio readiness, transcript delegation, Responses tool/history/image handling, cancellation-resistant tool containment, non-retained camera results, retryable shutdown, and the actual handler inside HybridRuntime.
- `.venv/bin/ruff check . --fix` and `.venv/bin/ruff format .`: passed; no formatting changes.
- `.venv/bin/mypy --pretty --show-error-codes`: passed, 64 source files.
- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`: 965 passed, 2 skipped.
- `uv lock --check`, JavaScript module syntax check for `static/js/views/settings.js`, and `git diff --check`: passed.

These are deterministic local checks with fake cloud services. A subsequent bounded account check completed one synthetic Luna response and
confirmed GPT Live session.started followed by session.closed. No household audio
was uploaded during this check. The user subsequently accepted conversation and automatic standby. Individual
Spotify/camera actions, interruption and reactivation still need explicit GPT Live
acceptance. Hardware, Pi performance and echo cancellation remain unverified. The Mermaid source is updated; its generated README SVG still needs
regeneration through the diagram workflow.

## Sources and changelog

- [Live WebSocket protocol](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)
- [Client delegation](https://developers.openai.com/api/docs/guides/live-delegation)
- [Session and transcript semantics](https://developers.openai.com/api/docs/guides/live-conversations)
- 2026-09-10: Recorded architecture and validation boundaries before integration.
- 2026-09-10: Completed adversarial backend/provider review and added regressions for cancellation-resistant tools, sensitive result retention, structured untrusted transcript context, and retryable shutdown.

- 2026-09-10: User confirmed live conversation worked, but standby failed.
  Fixed quiet output resetting inactivity. A real API test using synthetic
  silence and the actual HybridRuntime returned to LOCAL_AMBIENT with the
  connection closed after 3.14 seconds for a three-second timeout. Human
  standby/reawakening acceptance after this fix remains pending.

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


## Optional household memory capture

With both `HOUSEHOLD_MEMORY_ENABLED` and `HOUSEHOLD_MEMORY_CAPTURE_ENABLED`, the
provider starts a bounded silent extraction worker only after `session.started`.
It receives active transcript deltas, not injected pre-wake context or audio.
Standby/shutdown cancels it and clears its buffer without a final flush. Speech
and action delegation remain separate from this tool-free Responses request.

External destructive changes invalidate old Live/Luna context and end that conversation.
Spoken corrections remain in the current conversation; spoken forget deletes saved
content and pauses capture/review for that activation. Routine additions enter
the next activation's memory snapshot. The
[household memory feature](household-memory.md) owns storage, configuration, costs,
inspection and validation details. The architecture Mermaid source is updated;
the generated SVG was regenerated on 2026-09-16. The
[Gemini migration](../plans/gemini-38-live-extended-thinking-migration.md) extracted
the shared memory lifecycle and strengthened shutdown containment. Offline
regressions passed; its validation log separates these from pending Gemini live acceptance.

## Local movement — 2026-09-12

Session readiness clears listening mode rather than holding it for the entire
connection. This leaves local antenna motion, idle breathing, and idle-policy
eligibility available; their existing activity guards still apply. GPT Live does
not currently provide mapped user speech boundaries, so listening-specific
antenna freezing is unavailable. Output speaking state still controls the existing
face-tracking handoff; it does not generate animation.

Speech-reactive head wobble remains the SDK's local playback behavior. Normal
SDK output feeds it through `media.push_audio_sample`; the explicit simulator
`AUDIO_OUTPUT_BACKEND=gstreamer` bypass does not. This repair does not switch
audio backends or add Luna calls. Human movement and active-route acceptance
remain pending; use the normal SDK route in a supervised trial where supported.
Do not remove the simulator crash workaround without separately validating it.

Regression: `tests/test_openai_live.py::test_connected_session_does_not_freeze_local_movement`
failed before the correction (`set_listening(True)` at readiness). It covers
readiness, user transcript, output speech, and shutdown with a fake transport.

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

## Experimental local interruption clearing

`OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED=1` enables an opt-in trial of local
energy-based onset detection. The existing `RmsEnergyVAD` uses
`LOCAL_VAD_RMS_THRESHOLD`. 120 ms of consecutive detected energy latches one onset;
400 ms quiet rearms. A latched onset clears provider/native queued audio only if
nonquiet assistant output arrived within the last second. This recent-output guard
is a heuristic, not a measurement of speaker completion; it can miss longer tails.
It does not reuse the movement timer, which can expire while sound remains queued.

All microphone bytes continue through the same ready-session resampling/send path.
No transcript, network speech-start event, server cancellation command, output mute,
model, STT request or durable audio store is added. New server audio is still played;
if GPT Live keeps generating old speech, local clearing alone cannot stop that.
Loud noise/echo can trigger clearing, and quiet speech may be missed. This must not
be enabled as a general speaker-mode fix. State resets on fresh session startup;
failed native clears are logged and input forwarding continues.

On macOS LOCAL, `MACOS_LOCAL_AUDIO_FLUSH_ENABLED=1` separately bypasses the old
native-clear skip. Use it only with the reviewed SDK candidate described in
[the comparison plan](../plans/audio-dropout-comparison.md). Neither flag changes
the installed SDK; both default off and require a restarted process. The SDK
candidate is selected for the trial by PYTHONPATH, not by patching SDK internals.

Tests: local barge-in tests in tests/test_openai_live.py exercise sustained onset,
brief noise, quiet rearm, default-off, readiness/privacy, no-output behavior,
unchanged input bytes and clear failure. tests/test_console.py covers public native
routing with the Mac override; tests/test_config.py covers opt-in defaults/refresh.
Mac interruption and requested-word behavior passed the recorded user trials.
Broader speech-understanding reliability, long-run crash behavior, physical head
movement and quantitative AEC performance remain unverified.

## September 13 Mac speaker acceptance

User accepted the candidate SDK configuration with headphones and subsequently
without headphones through laptop speakers. Logs show software AEC enabled and
three local-onset events followed by native clear confirmations in the speaker
trial. Headphones are no longer required for this tested configuration; they remain
an optional diagnostic control and guidance for the separate GStreamer bypass.
Earlier pending speaker/movement statements describe prior checkpoints.

This is qualitative acceptance on one Mac, not quantified echo cancellation,
physical robot validation or permission to enable trial flags by default. The
speaker excerpt still has 21 additional capture drops and ends before shutdown.
See [the comparison plan](../plans/audio-dropout-comparison.md) for exact results.

## Shared migration validation — 2026-09-16

The final full repository gate passed: **1,247 passed, 3 native opt-in checks
skipped**, with two expected synthetic SDK warnings. Ruff, formatting, strict
mypy, lock validation, JavaScript syntax and diff checks passed. The
[final validation record](../plans/gemini-38-live-extended-thinking-migration.md#final-offline-validation--2026-09-16)
lists exact commands, test inventory, skipped checks and the reproducible next
live-validation step. Gemini spoken-memory parity and real audio/hardware
acceptance remain unverified; the active provider was not switched.
