# Hybrid household companion benchmark

- Status: `in-progress`
- Last updated: 2026-08-30
- Machine: Apple Silicon Mac, simulator, SDK LOCAL media backend
- Scope: Phases 0–5

This report separates implemented/automated evidence from live measurements.
Phases 3–5 are implemented and comprehensively tested with fakes. Live local
STT and one configured wake path have now been exercised, but local-command
acceptance, microphone quality, latency, and resource measurements remain
incomplete.
Missing values are marked `not measured`; they are not estimated.

## 1. Architecture implemented

```text
Mac microphone → LocalStream → HybridRuntime
                              ├─ bounded sequential local-processing handoff
                              ├─ LOCAL_AMBIENT
                              │    20 s RAM PCM ring
                              │    RMS VAD → loopback local STT
                              │    bounded rolling transcript → “Reachy” wake
                              ├─ LOCAL_COMMAND
                              │    exact LocalIntentRouter
                              │    existing tools / MovementManager
                              └─ CLOUD_CONVERSATION
                                   bounded derived text context + addressed text turn
                                   direct Gemini Live audio
                                   existing tools / movement / memory
```

Strategy A continuously transcribes completed local speech segments. Activation
uses only already completed bounded text, so it does not wait for an in-flight
STT task. A wake-stripped question is sent as the first user text turn only
after the new provider is ready, so the dinner question actually receives a
response rather than existing only as background instructions. Strategy B keeps the raw ring in RAM and transcribes its defensive
snapshot locally after keyboard activation. Strategy B has no continuous text
stream, so voice wake/local voice commands are intentionally disabled.

When Strategy A emits a wake-only final followed by a separate command final,
the runtime acknowledges and waits for one next final for up to
`LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS` (two seconds by default). Exact local
commands stay local; uncertain text or timeout follows the normal Gemini path.
During that short window, postactivation frames are retained in the bounded
cloud FIFO while also being offered to local processing. This behavior is
covered by deterministic tests but has not yet been measured with live STT.

The local STT adapter accepts only explicit loopback hosts, disables proxy
environment discovery, sends no credentials, builds WAV input in memory, and
returns text only. Gemini is constructed only after activation. Existing
provider, tool, movement, profile, and local-memory boundaries are reused.

For the explicit macOS simulator workaround, `AUDIO_OUTPUT_BACKEND=gstreamer`
routes assistant PCM through an app-owned bounded pipeline. Its appsrc uses a
sample-derived timeline with explicit PTS/DTS/duration, first-cue running-time
anchoring, long-gap reanchoring, and start/flush resets. The default `sdk`
output remains unchanged. Native flush-event rejection fails closed and stops
the app-owned speaker worker.

## 2. How to run it

Use headphones to prevent speaker-to-microphone feedback. Configure the ignored
`.env` without committing its values:

```env
REALTIME_PROVIDER=gemini
HYBRID_RUNTIME_ENABLED=1
HYBRID_CLOUD_INACTIVITY_SECONDS=60
HYBRID_AMBIENT_BUFFER_SECONDS=20
LOCAL_CONTEXT_STRATEGY=continuous
LOCAL_STT_BASE_URL=http://127.0.0.1:8766/v1
LOCAL_STT_MODEL=openai/whisper-small
REACHY_MINI_CUSTOM_PROFILE=household_companion
```

One-time optional Apple Silicon local-STT install:

```bash
/opt/homebrew/bin/uv tool install --python 3.12 --force \
  "transformers[serving]" --with librosa --with python-multipart
```

Run three terminals:

```bash
# Terminal 1 — simulator
.venv/bin/mjpython -m reachy_mini.daemon.app.main \
  --sim --scene empty --fastapi-host 127.0.0.1 --fastapi-port 8000

# Terminal 2 — local STT
transformers serve --host 127.0.0.1 --port 8766 \
  openai/whisper-small

# Terminal 3 — app and UI
.venv/bin/reachy-mini-conversation-app --media-backend local --ui
```

Open http://127.0.0.1:7860/. Space/Enter remains the keyboard fallback. For
Strategy B, change `LOCAL_CONTEXT_STRATEGY=activation`; activate by keyboard.

## 3–8. Measurements

| Metric | Evidence | Result |
|---|---|---:|
| Direct Gemini connection | Historical partial direct runs | 545.7–640.8 ms; not accepted as the final saved-log baseline |
| Direct conversation quality | User-observed long SDK LOCAL session | Responsive conversation and successful interruption; numeric first-audio/interruption samples not retained |
| Direct tool latency | Historical greeting-tool samples | 270.8/274.2 ms and 231.6/230.9 ms; not a final representative tool benchmark |
| Hybrid keyboard activation to connected | Accepted Phase 2 simulator/Gemini run | approximately 885 ms total, including 505.6 ms provider connection |
| Hybrid interruption flush | Accepted Phase 2 run | 1.0–1.3 ms local flush samples |
| Hybrid session duration | Accepted Phase 2 run | 167.511 s |
| First response audio | Provider instrumentation | not measured; event ordering can make a sample explicitly unavailable/reordered |
| Strategy A dinner context | Automated integration | correct ordered text injection; live latency/quality not measured |
| Strategy B dinner context | Automated integration | acknowledgment precedes local STT, which precedes provider construction; live latency not measured |
| Wake activation latency | Automated deterministic clock plus one live path | live numeric value not measured |
| Wake false positives / misses | One live configured wake-word check | `LOCAL_WAKE_WORD=computer` reached the final-STT detector; default `Reachy` produced no action because no exact matching token reached the detector; rates remain not measured |
| Local command latency | Automated deterministic clock plus one live simulator attempt | `computer, look left` triggered local wake but escalated to Gemini; Gemini called `MoveHead`, so live local-command acceptance and latency remain not measured |
| Local STT CPU / process RSS | — | not measured |
| Local STT backend | Loopback endpoint and live request | Transformers Serve with `openai/whisper-small` returned HTTP 200 for the live request; transcript quality and sustained resource use remain not measured |
| Split-final wake follow-up | Deterministic hybrid-runtime/capture tests | Exact follow-up stays cloud-free; uncertain follow-up and timeout preserve the FIFO for cloud; live latency/resource value not measured |
| Local capture pressure | Deterministic LocalStream/runtime burst test | Slow local observation no longer holds the capture handoff; admitted local frames remain ordered, capture-worker drops stay zero, and sustained live drop rate is not measured |
| App-owned speaker timing | Deterministic timing/flush tests, native child, and live Gemini smoke | 7 timeline tests and 3 flush-contract tests passed; the native child exited zero; two fresh live sessions completed without a native crash, but audible quality was not measured |

The direct Phase 1 saved-log rerun and all Phase 3–5 live measurements remain
required before this report can be marked complete.

Automated verification on 2026-08-29 passed the full 649-test repository suite,
Ruff, formatting, strict mypy, lock consistency, JavaScript syntax, and diff
checks. These results use fakes for cloud and local STT boundaries and do not
substitute for the live measurements above.

The 2026-08-30 diagnostics and macOS crash-containment update passed Ruff,
formatting, strict mypy across 54 source files, all 654 tests in 12.28 seconds,
lock consistency, both JavaScript syntax checks, and the diff check. Native
GStreamer stability still requires a live rerun; unit tests cannot prove that a
process-level mixer crash is resolved.

After the split-final follow-up fix, the clean CI-equivalent gate passed Ruff,
formatting, strict mypy across 54 source files, and all 672 tests in 13.37
seconds. `REACHY_MINI_SKIP_DOTENV=1` prevented the ignored live `.env` from
changing test provider selection. Lock consistency, JavaScript syntax, and the
diff check also passed.

After the explicit simulator speaker-output mitigation, the final root gate
passed Ruff, formatting, strict mypy across 57 source files, and all 697 tests
in 15.07 seconds. Lock consistency, both JavaScript syntax checks, and the diff
check also passed. This proves the app boundary and lifecycle behavior under
tests; it does not prove native crash containment until repeated live Gemini
audio succeeds.

The local-routing and capture-pressure regressions now pass in the focused
hybrid/runtime/console set. Runtime status reports only enum/reason/counter
metadata for router rejection, split-final timeout, executor failure, and
bounded local-processing drops; it does not expose transcript or audio content.
The new worker is a foundation for live testing, not evidence of microphone
quality, local STT latency, sustained overload capacity, or native process
stability.
The focused hybrid/runtime/console/router/action/main command passed 205 tests
and the dotenv-isolated `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q
--tb=short` command passed 726 tests with one explicit skip. Ruff, formatting,
strict mypy across 59 source files, and `git diff --check` also passed.

After the sample-clock timing fix, the focused timing/output suite passed 24
tests. The optional native app-owned construction/push/flush/close child also
exited zero after the change (three consecutive review runs and one final root
run). A live root smoke then completed two fresh Gemini sessions with the
app-owned sink selected: provider connection took 451.4 ms and 327.7 ms, a
`move_head` tool call completed, two interruptions completed, inactivity
returned the first session to `LOCAL_AMBIENT`, and the process did not crash. A
separate startup/stop cycle shut down the app cleanly before the daemon. This is
useful process-level containment evidence, but the current status surface does
not prove native sink acceptance and an automated agent cannot judge audible
cadence or naturalness.

## 9. Proof ambient audio was not sent to cloud

Automated tests assert the send boundary, not only counters:

- ambient and Strategy A/B pre-activation frames never reach the fake provider;
- Strategy B's RAM snapshot is supplied only to the local STT fake;
- exact local commands never construct a provider;
- public/LAN/DNS STT targets and environment proxy forwarding are rejected;
- raw PCM is not logged or written to an application file.

The `conversation.status` RPC now exposes metadata-only local processing
counters, including accepted transcript segments and final wake updates, so a
live no-action result can be distinguished from an STT or detector boundary
miss without returning transcript or audio content.

The accepted five-minute Phase 2 live soak retained a full 20-second RAM ring
while `ambient_audio_bytes_sent_to_cloud`, cloud audio, and cloud video remained
zero before activation. Its final snapshot still reported zero ambient-cloud
bytes. Phase 3–5 must repeat this proof with the real local STT endpoint running.

## 10. Problems encountered

- Open-speaker Phase 0 testing caused acoustic feedback; headphones are the
  repeatable control because software echo cancellation is not established.
- macOS simulator rendering requires `mjpython` and a native arm64 environment.
- The SDK WebRTC reverse-audio chain failed with `not-negotiated (-4)`; accepted
  simulator conversations use the SDK LOCAL backend.
- Gemini input-transcript and audio events are independent, so first-audio
  latency is reported as unavailable/reordered when a defensible speech-end
  marker does not precede audio.
- The initial RMS VAD is amplitude-dependent and does not solve echo or noisy
  rooms. Its threshold must be measured with the actual microphone.
- A 2026-08-30 macOS rerun buffered 98.08 seconds of local audio but classified
  only one frame as speech at the default threshold of 500, so it made zero STT
  requests. Ambient cloud bytes remained zero. A lower temporary threshold must
  be measured before changing the cross-platform default.
- With a process-local threshold of 100, 65 frames were classified as speech
  and one STT request was created, but that live request exceeded the five-second
  timeout. A subsequent synthetic `computer look left` probe against the same
  warmed loopback server returned the correct text in 1.25 seconds. Live
  command latency and a suitable cold-request timeout remain unaccepted.
- LocalStream now hands local frames to a bounded sequential runtime worker, so
  a slow asynchronous VAD/STT observation no longer blocks the capture loop.
  Local-worker overflow drops newest local work and is counted separately from
  capture-worker drops. Sustained local VAD/STT CPU and drop-rate measurements
  remain required on the target microphone.
- A transcript-based wake word depends on local STT completing a segment. It is
  a V0 implementation and must be compared with an acoustic detector.
- The final-STT → detector → local-router → movement path worked with a
  configured `computer` wake word only as far as local activation. The command
  then escalated to Gemini, whose tool call moved the simulator; this is not a
  local-command acceptance pass. The default `Reachy` phrase produced no action
  because no exact matching final transcript token reached the detector; no
  alias was added.
- Split wake/command finals are handled by a bounded one-shot local follow-up
  window, but the real local STT segmentation and its activation latency have
  not been measured.
- Three app runs ended in native macOS `SIGSEGV` crashes on GStreamer's
  `audiomixer0:src` thread after cloud audio activation. There was no Python
  traceback or clean shutdown. The app now contains this path by skipping
  native `audio.clear_player()` only for macOS SDK `MediaBackend.LOCAL` while
  continuing to drain provider/app queues, but the third run crashed without
  that call and proved the workaround insufficient. Every report faults at
  `do_convert_out+140` with zero samples and null sample arrays before ORC runs;
  the app's public playback shape is valid. Live SDK playback tests are paused
  pending validation of the new explicit
  `AUDIO_OUTPUT_BACKEND=gstreamer` simulator bypass. The first bypass run
  survived several turns but sounded choppy/accelerated; arrival-time
  timestamping was the strongest suspected cause. The app-owned pipeline now
  uses explicit sample-derived PTS/DTS/duration and is covered by a GI-free
  timeline regression. It survived two fresh live Gemini sessions after the
  timing change, including a tool call, two interruptions, and inactivity
  close, without the prior native crash. Audible cadence still needs a human
  headphone check. Barge-in on the SDK path may leave already-buffered native
  audio.
- MLX Audio's current STT server explicitly bridges inference through a file in
  `/tmp`, so it was rejected for this RAM-only ambient path. The selected
  Transformers candidate decodes uploaded bytes through `BytesIO`; the live run
  must still monitor temporary directories before accepting that guarantee.

## 11. Raspberry Pi 5 8GB assessment

Not yet assessable. No Phase 3 local-model CPU/RAM measurement exists, and the
first candidate backend has not been measured on the Pi. The core interfaces,
Transformers candidate, and RMS VAD are cross-platform, but this report will not claim Raspberry Pi feasibility
until a Pi-compatible STT model's known requirements are compared with measured
Mac and Pi workload data.

## 12. Recommendation

First complete the short human headphone check for audible cadence on the
explicit macOS simulator playback bypass; retain the SDK playback path as the
default outside this explicit simulator workaround. Then run both
dinner-demo strategies on the Mac with the same microphone and
local model, recording activation/context/connection/first-audio latency,
process CPU/RSS, transcript quality, wake false positives/misses, and local
command latency. Repeat the wake test with the intended `Reachy` phrase and
investigate a proper-name-capable local STT or acoustic detector rather than
adding command aliases. Prefer Strategy A only if its always-on local cost and
wake quality are acceptable. Then benchmark a Pi-compatible VAD/STT backend
through the existing protocols before selecting production models or starting
later memory, Home Assistant, face recognition, heartbeat, or local-LLM work.
