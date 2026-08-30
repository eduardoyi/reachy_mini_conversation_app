# Feature: simulator speaker output bypass

- Status: `in-progress`
- Last updated: 2026-08-31
- Plan: [Reachy hybrid household companion](../plans/reachy-hybrid-household-companion.md)

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
post-flush frames restart correctly. Audible acceptance remains pending a fresh
process restart and human headphone retest.

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
audible retest remains pending.
