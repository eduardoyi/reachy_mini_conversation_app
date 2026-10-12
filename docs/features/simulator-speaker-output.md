# Feature: simulator speaker output bypass

- Status: `in-progress`
- Last updated: 2026-09-13
- Plan: [Reachy hybrid household companion](../plans/reachy-hybrid-household-companion.md)

## Gemini tiny-fragment containment — 2026-09-20

September 19/20 Gemini SDK sessions reproduced the native mixer crash despite
prior GPT Live speaker acceptance. A synthetic Gemini counting response contained
a one-sample (two-byte) PCM fragment. Passing matching tiny buffers to the public
SDK reproduced SIGSEGV without a cloud session; the test is isolated in a child.

Gemini now uses `Pcm16ChunkBuffer` before emitting output: retain fewer than 240
int16 samples at 24 kHz, join them to the next chunk, and emit normal-sized chunks
immediately. At generation/turn completion, pad a remaining short tail to 10 ms
with silence. Queue clearing also clears held audio on interruption, reconnect,
fencing and shutdown. No sample is discarded in uninterrupted speech; pending
storage is below 480 bytes and only final-tail padding adds duration (under 10 ms).
The real Gemini wait for its next chunk is not bounded by that audio duration.

The SDK route, software echo-reference path and wobble callbacks remain intact.
No SDK internals, installed libraries, defaults or credentials were modified.
`tests/test_streaming.py` and `tests/test_gemini_live.py` cover sample ordering,
repeated tiny fragments, final-tail padding and interruption. The isolated native
`tests/native_sdk_gemini_chunks.py` replays synthetic observed chunk sizes through
the same buffer/resampler and SDK, with wobble enabled and three clear cycles.
`--raw` intentionally reproduces the unsafe input; do not use it in the app.
`REACHY_RUN_SDK_CHUNK_SMOKE=1 .venv/bin/pytest tests/test_native_sdk_gemini_chunks.py -q`
is opt-in because it opens local audio devices; stop the conversation app first.
This automated check uses the installed SDK. Candidate SDK evidence is recorded
separately in the audio comparison plan. Live Gemini speaker/echo/gesture acceptance
must still be repeated; native survival does not establish audible quality or AEC.

## Current Mac audio guidance

September 13 user trials accepted GPT Live with the patched SDK playback path,
speech wobble and experimental interruption flags using both headphones and laptop
speakers. Headphones are optional for that tested configuration. The candidate
keeps playback inside the SDK software echo-reference path; its flush fix preserves
capture while clearing the mixer output. The installed SDK and defaults remain
unchanged; see the [current setup and results](../plans/audio-dropout-comparison.md).

This document also records the older app-owned `gstreamer` bypass. That route still
bypasses the echo reference and speech wobble; keep headphone guidance for it.
Speaker success with the SDK candidate does not repair the bypass or establish
physical Reachy, all room/volume conditions, zero capture drops or upstream approval.
Dated observations below are historical unless explicitly marked current.

## Overview and behavior

`AUDIO_OUTPUT_BACKEND=gstreamer` explicitly routes assistant PCM through a
small app-owned GStreamer speaker pipeline instead of calling the Reachy Mini
SDK's `media.push_audio_sample()`. It exists to contain a repeatable native
macOS simulator crash while preserving the official SDK microphone capture,
providers, tools, camera, and movement boundaries.

The default remains `sdk`. Blank or unset configuration preserves upstream
behavior. Any other value stops startup rather than silently selecting a path
that may crash. Failure to construct or start the selected GStreamer pipeline
also stops startup; it never falls back to SDK playback.

This feature is not accepted as a live fix yet. Three crash reports identify
the same native arm64 null dereference on GStreamer's `audiomixer0:src` thread:
`do_convert_out+140` receives zero samples and null sample arrays before ORC
runs. The app's normalized output shape is valid. The dedicated output removes
assistant PCM from that SDK mixer input, but the SDK's shared capture pipeline
and mixer still exist.

## Architecture and data flow

```text
provider audio
  → LocalStream mono float32 conversion/resampling
  → selected speaker boundary
       ├─ sdk (default) → Reachy media.push_audio_sample → SDK audiomixer
       └─ gstreamer   → bounded app queue → appsrc
                          → audioconvert → audioresample → autoaudiosink
```

The app-owned path uses one bounded Python queue and bounded/leaky native
queues. Construction fails closed when appsrc lacks downstream-leak support.
The native stages retain up to 30 seconds by audio bytes/time rather
than by provider buffer count, because Gemini may deliver more than eight
small buffers in a sub-second burst. `push()` copies a one-dimensional finite
float32 frame and returns without waiting on the native sink. Beyond the bound,
the oldest queued frame is dropped. Audio samples are never logged or
persisted.

Every accepted frame carries the current flush generation. Barge-in increments
the generation, drains queued frames, and flushes appsrc. A frame accepted
before interruption therefore cannot be emitted afterward even if the worker
and flush race. Provider queues continue to use the existing interruption path.

## Contracts, configuration, and lifecycle

| Setting | Default | Contract |
|---|---|---|
| `AUDIO_OUTPUT_BACKEND` | `sdk` | Accepts only `sdk` or `gstreamer`, case-insensitively. Invalid nonblank values raise before media starts. |

Selection happens in `LocalStream.launch()` after any instance `.env` reload.
The SDK mode starts recording and playback normally. The app-owned mode starts
SDK recording, which makes the current shared LOCAL capture pipeline active,
but deliberately does not call SDK `start_playing()` or feed its appsrc.
Shutdown closes the dedicated pipeline and defensively stops both SDK recording
and playback. Construction/start failures clean up started media and do not
redirect assistant audio through the SDK.

The public `SpeakerOutput` contract is deliberately small:

- `sample_rate`: rate expected after `LocalStream` resampling;
- `start()`: start once or report a startup failure;
- `push(frame)`: validate, copy, and enqueue without blocking;
- `flush()`: discard pre-interruption queued/native audio;
- `close()`: idempotently stop the writer and native pipeline.

## Integration points and extension rules

| File | Responsibility |
|---|---|
| `src/reachy_mini_conversation_app/audio/speaker_output.py` | Output protocol, bounded worker, lazy native boundary, and explicit factory. |
| `src/reachy_mini_conversation_app/audio/gstreamer_contract.py` | GI-free native event contract and fail-closed flush helper. |
| `src/reachy_mini_conversation_app/audio/gstreamer_output.py` | Native appsrc-to-speaker GStreamer pipeline loaded only for the opt-in backend. |
| `src/reachy_mini_conversation_app/console.py` | Late selection, provider-audio routing, barge-in flush, and combined app/SDK lifecycle. |
| `src/reachy_mini_conversation_app/config.py` | Validated selector and constants. |
| `.env.example` | User-facing setting without secrets. |

Add a future speaker implementation only behind the same protocol and an
explicit validated selector. Do not add provider, tool, movement, memory, or
agent logic here. Do not monkey-patch SDK or GStreamer internals. A production
robot path should remain on the SDK unless separately designed and measured.

## Constraints and gotchas

- This bypasses only assistant PCM insertion into the SDK mixer; it does not
  remove the SDK mixer used by the shared LOCAL capture pipeline.
- SDK audio-reactive wobbling does not receive bypassed assistant PCM. Existing
  tool movement and app-level assistant activity signals remain available.
- The default output device is chosen by `autoaudiosink`; no UI device selector
  is added in this milestone.
- Use headphones during live tests because the separate speaker-to-microphone
  feedback problem remains.
- WebRTC is not a fallback on this Mac; its reverse-audio chain previously
  failed with `not-negotiated (-4)`.
- The dedicated native pipeline has passed a construction/push/flush/close
  smoke test, but only repeated simulator/Gemini turns can test whether it
  contains this process-level crash.
- If a native push holds the pipeline lock, interruption and shutdown return
  after a short bound. Queued pre-interruption frames are discarded; the
  native flush is deferred and runs before later-generation audio, and native
  close is completed later by a daemon cleanup worker after the push returns.
- Native queue limits must remain time/byte based and downstream-leaky. A
  blocking queue could delay interruption, while the former eight-buffer,
  500 ms limits silently deleted normal Gemini bursts.
- [`docs/scheme.mmd`](../scheme.mmd) contains the new output branch. The
  generated `docs/assets/conversation_app_arch.svg` remains pending regeneration
  through the repository's diagram workflow and is not hand-edited.

## Backlog: restore laptop-speaker/AEC simulator path

The app-owned GStreamer path was introduced after repeatable SDK LOCAL
`audiomixer` crashes. Because it bypasses SDK playback, it sits outside the
SDK echo-reference path; laptop-speaker output can return through the Mac
microphone and cause Gemini to interrupt its own response. Headphones are the
current reliable simulator control.

Future work must use a supported full-duplex/shared echo-reference path or an
upstream SDK solution, without monkey-patching SDK or GStreamer internals.
Acceptance requires automated contract coverage, the native pipeline smoke,
and live simulator/Gemini speaker and headphone echo/interruption tests with
clean process liveness and shutdown. Keep this bypass opt-in until that work
is complete.

## Test inventory and current evidence

`tests/audio/test_speaker_output.py` covers selector/factory failures, frame
validation, bounded nonblocking enqueue and oldest-drop behavior, start/close,
worker failure, flush generation races, and payload-safe logging.
`tests/test_console.py` covers late environment selection, no SDK audio push,
resampling, SDK/app shutdown, startup cleanup, interruption routing, and the
persisted-environment regression. `tests/test_config.py` covers defaults,
normalization, and fail-closed invalid values.
`tests/test_hybrid_smoke.py` covers the public output seam's burst and
interruption ordering alongside the hybrid privacy and local/cloud boundary.

## Validation tiers

The hermetic tier runs without a speaker, simulator, SDK daemon, or cloud
credentials. `tests/test_hybrid_smoke.py` composes the real runtime and local
command boundaries, checks split-final routing and provider traffic, and uses
a fake speaker pipeline for bounded bursts and interruption ordering. These
tests do not exercise native GStreamer timing or claim coverage of the SDK
`audiomixer`. `tests/audio/test_sample_timeline.py` separately verifies the
pure sample-clock contract and a GI-free buffer metadata fake: durations come
from sample counts, burst arrival cannot compress playback, and long gaps or
flush/start reset continuity.
`tests/audio/test_gstreamer_contract.py` verifies that the two native flush
events are ordered and that either rejected event fails closed.

The optional native tier runs a child process that constructs the real
app-owned GStreamer pipeline and performs burst, flush, and close cycles. It
is explicitly opt-in, has a hard parent timeout, and reports a child exit or
signal. Missing GI/GStreamer dependencies are a loud skip. It does not use
Gemini, the simulator, or the SDK `audiomixer`, so a passing result is only a
native app-output lifecycle smoke result.

The live tier uses the simulator and Gemini with
`AUDIO_OUTPUT_BACKEND=gstreamer`. A 2026-08-30 automated root smoke completed
two fresh sessions with the real app-owned sink selected without a native
crash, including a `move_head` tool call, two interruptions, and inactivity
return to ambient. Provider connection samples were 451.4 ms and 327.7 ms. A
separate startup/stop cycle shut down the app cleanly before the daemon. This is
process-level containment evidence; the status surface does not prove native
sink acceptance, and audible cadence and naturalness still need a human
headphone check.

The native pipeline now disables automatic arrival-time timestamps and assigns
explicit PTS, DTS, duration, and DISCONT metadata from that sample timeline.
It checks both native flush-event return values; a rejection raises into the
speaker worker so output is stopped rather than silently continuing.
The pure tests do not inspect a native `Gst.Buffer` or a real sink's cadence,
so repeated native/live audible validation remains required.

Root verification on 2026-08-30 passed the 126 focused tests in both forward
and reverse file order. The final repository gate passed Ruff, formatting,
strict mypy across 57 source files, and all 697 tests in 15.07 seconds. Lock
consistency, both JavaScript syntax checks, and `git diff --check` also passed.
The live simulator/Gemini result above is recorded separately from the
deterministic suite and does not turn subjective audio quality into an
automated claim.

The timing regression tests passed on 2026-08-30 (`7 passed`), the GI-free
flush contract passed (`3 passed`), and the combined speaker/timing subset
passed (`24 passed`). This validates the sample timeline, fake-buffer, and
flush-result contracts only; it does not establish native sink cadence or
audible quality.

Human acceptance on 2026-08-31 confirmed that the first timestamp-only fix was
still choppy and sounded fast-forwarded. A native reproduction showed why: the
old 500 ms limits retained only 5 of 20 rapidly supplied 100 ms buffers while
`push-buffer` still returned success. The replacement keeps the pipeline
non-blocking and leaky but raises both native stages to a 30-second time/byte
bound. The revised native child retains all 20 uniquely marked buffers in
order, verifies contiguous 100 ms PTS/duration metadata, then confirms two
post-flush frames restart correctly. A fresh restarted Gemini session passed
human audible acceptance with headphones on 2026-08-31.

The app-owned simulator sink is outside the SDK playback path used as the echo
canceller's reverse reference. Laptop-speaker output can therefore return
through the Mac microphone and make Gemini interrupt its own response. Use
headphones for current simulator testing; this limitation does not establish
the behavior of Reachy Mini's physical audio hardware.

Commands run:

```bash
pytest tests/audio/test_speaker_output.py tests/test_console.py tests/test_config.py -q
pytest tests/test_config.py tests/test_console.py tests/audio/test_speaker_output.py -q
REACHY_MINI_SKIP_DOTENV=1 pytest tests/audio/test_sample_timeline.py tests/audio/test_gstreamer_contract.py tests/audio/test_speaker_output.py -q
ruff check . --fix
ruff format .
mypy --pretty --show-error-codes
pytest tests/ -v
uv lock --check
node --check src/reachy_mini_conversation_app/static/js/views/settings.js
node --check src/reachy_mini_conversation_app/static/js/views/talk.js
git diff --check
```

A real native construction/start/push/flush/close smoke check also exited zero
with finite silent float32 frames after the timing change; it did not use
Gemini or the simulator and therefore is not live acceptance evidence.

The current hermetic smoke command passed 11 tests and skipped the opt-in
native child because `REACHY_RUN_NATIVE_AUDIO_SMOKE` was unset:

```bash
REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_hybrid_smoke.py -q -rs
```

Run the native tier only when the machine may safely open its default audio
sink:

```bash
REACHY_MINI_SKIP_DOTENV=1 REACHY_RUN_NATIVE_AUDIO_SMOKE=1 .venv/bin/pytest tests/test_hybrid_smoke.py -q -rs
```

The explicit native child test passed three consecutive times on 2026-08-30
with the same opt-in environment and the test narrowed to
`tests/test_hybrid_smoke.py::test_optional_native_speaker_subprocess_smoke`.
The broader hermetic command above remains the repeatable default; native
hardware/sink availability can still vary by machine.

The 2026-08-31 burst-retention fix passed the focused output/console suite and
the revised opt-in native child passed independently. Ruff,
formatting, and strict mypy across 59 source files were clean. Final root
verification passed all 730 tests with one explicit native skip, lock
consistency, both JavaScript syntax checks, and `git diff --check`. Human
headphone playback then passed in a fresh live Gemini session.

## Speech movement, hearing, and echo findings — 2026-09-13

### Observed evidence

- August 30 crash records identify three native macOS SDK audio-mixer failures
  at `do_convert_out` with zero samples/null sample arrays. The dedicated speaker
  bypass was introduced as containment, not proof that the SDK defect was fixed.
- September 12: user confirmed the GPT Live listening-state correction removed
  the activation-time static behavior. This restores local movement eligibility;
  it does not restore speech-reactive wobble through the bypass.
- User launch used `AUDIO_OUTPUT_BACKEND=gstreamer`, local media, GPT Live,
  acoustic wake, household memory/capture, and a 30-second cloud idle timeout.
- Switching to `sdk` produced wobbler-enabled logs and an excerpt continuing
  through active conversation without a recorded crash. The excerpt showed
  capture drops reaching 100 and one local-processing drop. It did not prove
  visible speech bobbing, clean interruptions, or standby completion.
- User reported missing words/incomplete microphone input and noticeably better
  understanding with the dedicated `gstreamer` output. This is comparative human
  evidence, not a controlled diagnosis. Echo and queue overflow are distinct
  possible contributors; neither should be treated as the sole established cause.
- September 13: user explicitly confirmed the simulator also hears its own voice.

### Current architecture inspection

Installed SDK version: `1.10.0rc5` (package metadata checked September 13).
Its `media/audio_gstreamer.py` already creates `webrtcdsp` and
`webrtcechoprobe` when no matching audio card is detected and both plugins exist.
The DSP processes microphone audio; the probe receives SDK mixer playback.
Actual runtime plugin activation was not established from the supplied excerpt.
Our `audio/gstreamer_output.py` creates a separate appsrc-to-speaker pipeline
without that probe, while `console.py` retains SDK microphone capture. Therefore
assistant playback on this bypass does not reach the SDK's echo reference or
speech wobbler. Successful sound output does not prove echo cancellation.

GStreamer's documented probe/DSP pairing requires the same top-level pipeline
and matching sample rates. Adding a probe to our separate speaker pipeline alone
is not a supported fix. Muting the microphone during playback would prevent
natural user interruption and is not equivalent to echo cancellation.

### External evidence and limits

- [SDK issue 1160](https://github.com/pollen-robotics/reachy_mini/issues/1160):
  historical simulator/no-audio-board feedback and self-interruption report.
- [SDK PR 1189](https://github.com/pollen-robotics/reachy_mini/pull/1189):
  merged June 19, adds software echo cancellation and a shared pipeline/mixer.
  Our installed SDK already contains this design; installing it again is not a fix.
- [GStreamer DSP contract](https://gstreamer.freedesktop.org/documentation/webrtcdsp/webrtcdsp.html):
  playback-reference, matching-rate, and same-pipeline requirements.
- [Wireless issue 1334](https://github.com/pollen-robotics/reachy_mini/issues/1334):
  an open user report of physical movement degrading microphone intelligibility.
  Physical motor noise cannot explain the simulator, and this is not proof that
  all physical units are affected. Hardware acceptance remains necessary.
- [Mac startup issue 983](https://github.com/pollen-robotics/reachy_mini/issues/983):
  a different library/startup failure, not an established match for our crash.

### Next validation

At this earlier investigation stage, the better-hearing speaker bypass was retained; headphones provided
a practical echo control. A bounded SDK-output comparison with headphones and
wobbling on/off should measure capture drops and sentence understanding first.
Inspect whether synchronous SDK playback blocks the shared app event loop; do
not assume that hypothesis is proven by queue warnings. If that is the cause,
repair the app boundary and retest native lifecycle before changing default output.
If SDK output cannot be stabilized, a supported unified capture/playback adapter
is a larger alternative requiring design, cancellation, privacy, and native tests.
No SDK internals may be patched. No extra Luna calls are needed for AEC or wobble.

This update changes documentation only. No dependency, runtime setting, microphone
capture, cloud session, or live audio test was started. Verification: source and
package metadata inspection, official source review, and `git diff --check`.

## Optional diagnostic comparison — 2026-09-13

[Local audio dropout comparison](../plans/audio-dropout-comparison.md) contains
the A/B/C commands, fixed spoken scenario, metrics, limitations and result table.
`AUDIO_DIAGNOSTICS_ENABLED` defaults off; `SPEECH_WOBBLE_ENABLED` defaults on.
Both use the existing boolean environment parser and require an app restart.

`audio/diagnostics.py` aggregates counts, sum and max under a short lock, retaining
no individual observations or payloads. `console.py` supplies SDK retrieval/arrival,
queue age, input forwarding, output push and event-loop lag timings. A monitor
reports every five seconds and is cancelled/joined when recording exits; the final
report includes cumulative worker capture/drop counters. An externally preinstalled
capture worker retains its own instrumentation; normal app construction passes
the shared accumulator into its worker. `main.py` uses public SDK enable/disable
wobbling calls; disabling speech wobble does not disable idle motion.

Tests: `tests/audio/test_diagnostics.py`, diagnostic cases in `tests/test_console.py`,
and startup wobble variants in `tests/test_main.py`. Metrics describe app boundaries,
not hardware timestamps, cloud receipt, exact network latency or audible playback.
No extra cloud request is made by diagnostics. Existing ambient/privacy boundaries
are unchanged. Native crashes can prevent the final summary.

Diagnostic preparation validation on September 13: 191 focused tests passed;
full `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v` passed 1119 tests with
2 optional native/simulator skips. Ruff check/format and strict mypy (68 source
files), launch-function `zsh -n`, and `git diff --check` passed. Exact commands and
remaining human/native acceptance are recorded in the comparison plan.

Update 2026-10-12: each report also carries `input_level` (forwarded microphone
frames, frames at or above `LOCAL_VAD_RMS_THRESHOLD`, and the window's maximum
channel-0 int16 RMS) and, in direct mode, `server_idle_s` (seconds since the handler
last recorded backend activity; `n/a` in hybrid mode). Together they separate a
silent microphone from a backend that stops answering live speech. Only these
aggregates are logged, never samples.

## First-use import stalls — 2026-09-13

User A/B/C trials all showed early loop stalls followed by stable processing.
After-startup drops: A 71, B 89, C 50; maximum loop lag: 441.6, 606.9,
331.27 ms respectively. User interruption succeeded immediately in A, needed
repetition in B, and stopped without the requested word in C. No transcript
evidence establishes whether C lost audio or failed to follow the instruction.
All excerpts ended by Ctrl+C, not verified inactivity standby.

A local synthetic diagnosis (no microphone, real credentials, or network requests)
measured Live module import after main/console imports at 413.03 ms. A profiled
first Responses call with httpx.MockTransport took 788.36 ms, including 677 ms
in the lazy Responses resource property; the second took 1.02 ms. Profiling
adds overhead and these are not live latency measurements. The import path is
a confirmed synchronous bottleneck, but cannot explain every hearing failure.

Correction: for selected GPT Live, import its module and public Responses resource
module before constructing/starting the microphone stream. Do not instantiate a
handler/client or connect a provider in standby. Other provider selection remains
unchanged. Startup may take longer before capture; first activation no longer
pays these module-import costs. No dependencies or SDK internals change.

Regression coverage: selected-provider warm imports and deferred handler factory
in tests/test_main.py; fake hybrid privacy and memory lifecycle tests retained.
Re-run the headphone comparison after restart to measure the remaining stalls.
This does not fix native playback flush, echo bypass, or prove hearing acceptance.

Import-stall correction validation: 44 focused main/hybrid/memory tests passed;
full `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v`: 1119 passed,
2 optional skips (27.37 s). Ruff check/format, strict mypy (68 files), and diff
whitespace checks passed. Removing just the two warm-import calls reproduced
4 failures in the selected OpenAI startup regressions; corrected file restored.
A fresh preloaded MockTransport request took 59.42 ms without profiling; do not
compare this directly as a speedup against the profiled cold measurement.
No live capture or real API requests were run.

## Controlled SDK clearing probe

The [comparison plan](../plans/audio-dropout-comparison.md#controlled-sdk-flush-probe--2026-09-13)
contains the opt-in synthetic-tone test and limitations. `tests/test_native_sdk_flush.py`
runs `tests/native_sdk_flush_smoke.py` in a child with a 30-second timeout. Only public
SDK audio methods are used; microphone samples are counted and discarded. This tests
native lifecycle and capture delivery, not exact queued duration or GPT Live turn
recognition. Normal playback settings and the Mac native-clear skip are unchanged.

Human flush acceptance failed: tones continued after the SDK reported clearing.
The comparison plan records the likely mixer flush-propagation gap and a discovered
mono/stereo error in the synthetic probe. Its reported 2000 ms duration must not be
used as timing evidence until corrected. The conversation app's MediaManager route
already adapts channels. No SDK upgrade or public playback-only reset fix has been
verified; stop_playing also stops capture on LOCAL.

The probe channel defect is corrected and regression-covered. Native tracing now
confirms installed flush events stop at the mixer's speech input. A separate SDK
candidate flushes both mixer inputs with matching sequence numbers and preserves
the shared clock/capture. The comparison plan records native evidence, checkout,
validation and remaining acceptance. Installed SDK and app interruption logic are
unchanged.

The synthetic probe accepts `--wobble` to exercise SDK wobble callbacks without
sending movement commands. Three candidate native cycles passed with callbacks
and ongoing capture. Audible acceptance and simulator movement remain separate;
see the comparison plan for results and the candidate environment.

Two default-off trial flags now connect the SDK candidate to local GPT Live:
`OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED` enables debounced microphone-energy clearing;
`MACOS_LOCAL_AUDIO_FLUSH_ENABLED` permits native clearing on Mac LOCAL. Use only
with the reviewed candidate SDK. Headphone and laptop-speaker trials are now accepted on this Mac. The provider feature document
specifies the heuristic and tests. Neither is a production interruption guarantee.

2026-09-13 live headphone trial: user confirmed expected stopping, requested reply
and speech head movement with the candidate SDK. Observed capture drops stayed
constant (60); no additional drops in the logged interval and orderly shutdown.
No local-onset clearing log appeared during conversation, so this is experience
acceptance for one trial, not proof the experimental onset detector triggered.
Defaults remain unchanged; see comparison plan for metrics and remaining limits.

A longer September 13 headphone log now confirms nine local onset events followed
by SDK clearing, inactivity standby, acoustic reactivation and clean shutdown.
User reported good behavior. The excerpt contains 33 additional capture drops,
so residual capture loss remains despite successful experience. This supersedes
the earlier lack of local-trigger evidence, not the remaining hardware/echo/CI
limits. Metrics and timestamps are recorded in the comparison plan.

September 13 laptop-speaker trial: user confirmed good operation without headphones.
Software AEC was enabled; three local detections were followed by native clearing.
The excerpt contains 21 additional capture drops and no recorded crash; it ends
mid-log, so shutdown is not established. This updates speaker acceptance for the
candidate SDK only. See the comparison plan for timing metadata and remaining limits.


September 20 final verification: Ruff/format/mypy passed; full suite **1251 passed,
4 skipped, 2 deliberate SDK fixture warnings**. The new opt-in SDK chunk test
passed separately (**1 passed**), candidate replay passed three clear cycles and
191 wobble callbacks, while raw input reproduced SIGSEGV. Exact commands and
limits are recorded in the audio comparison plan. Live user acceptance is pending.


### Live Gemini follow-up — 2026-09-21

User reports no further crashes and reasonably good laptop-speaker operation
with the SDK route after tiny-fragment containment. The supplied log excerpt
ends in orderly Ctrl+C shutdown, not a crash. Remaining user-reported issues are
slow interruption, silent first acoustic activation and an unsuccessful Spotify
request. The excerpt has no Spotify call and omits the activation; these require
separate diagnosis. Speaker acceptance is partial, not a measured AEC result.

For the follow-up trial, keep the simulator daemon running and restart only the
conversation app with the same SDK command. Add this environment variable before
rerunning that command (the new option is deliberately off by default):

```bash
export GEMINI_LIVE_LOCAL_BARGE_IN_ENABLED=1
```

Test one bare “Reachy” and wait for a brief response; then try a wake immediately
followed by a question. During a longer answer, interrupt naturally once. Finally
ask to play a named song on Spotify with the Mac Spotify app open. Record whether
the acknowledgment overlaps the question, interruption needs a second attempt,
or the Spotify request produces a tool call/error. The local interruption assist
can mistake speaker leakage/noise for speech; disable with the same export set to
`0` if that happens. This trial is still needed; offline tests cannot establish
speaker echo performance or subjective interruption latency.

## Paced SDK playback — 2026-10-12

On the SDK speaker path, `LocalStream.play_loop` no longer hands each provider
frame to `push_audio_sample` as soon as it arrives. Providers stream faster than
real time, so seconds of speech used to accumulate inside the native pipeline,
and on macOS LOCAL (where native `clear_player` stays skipped without the
reviewed SDK candidate) an interruption could not remove it. Frames are now
split evenly into pieces of at most `PLAYBACK_PIECE_SECONDS` (0.1 s) and pushed
only while less than `PLAYBACK_LEAD_SECONDS` (0.3 s) of pushed audio remains
unplayed. The remainder waits in an app-owned queue that `_clear_robot_playback`
empties immediately, bounding talk-over after an interruption to roughly the
lead plus one piece. The 0.3 s lead covers the ~230 ms event-loop stalls seen on
hardware. The app-owned GStreamer speaker path is unchanged because its flush
already clears. Tests: `test_play_loop_paces_long_replies_into_the_sdk`,
`test_interruption_drops_paced_audio_not_yet_in_the_sdk`,
`test_play_loop_splits_frames_evenly_without_tiny_pieces`. Audible acceptance on
the Reachy Mini Lite is pending.
