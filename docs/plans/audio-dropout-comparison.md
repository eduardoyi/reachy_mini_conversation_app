# Local audio dropout comparison

- Status: Mac SDK-candidate headphones and laptop speakers accepted; upstream/hardware validation pending
- Owner: Reachy application
- Date: 2026-09-13

## Current guidance

The user accepted GPT Live conversation through both headphones and laptop speakers
on this Mac with the candidate SDK, SDK playback and both trial flags. Headphones
are no longer required for this tested configuration. Keep them as an optional
echo-control comparison if problems recur. The separate `gstreamer` output bypass
still lacks the SDK echo reference; its headphone guidance is unchanged.

Use the existing live trial command below for either output device. The candidate
remains selected through PYTHONPATH; no installed SDK or default flags changed.
Earlier dated pending statements below record the investigation at that time;
latest acceptance appears at the end of this plan. Physical Reachy and broader
room/volume/device conditions, residual capture drops and upstream validation remain
open. This is user-observed speaker acceptance, not a quantitative AEC benchmark.

## Scope and decision

Prepare the user's requested three-way comparison without starting audio capture,
a provider session, or changing their default output route. Measure where the app
falls behind before changing buffering or SDK behavior. No new dependencies,
Luna gesture calls, SDK patches, audio recording, or transcript logging are added.

Assumptions: current Mac simulator and wake model remain available; use headphones
with the same microphone/device/volume in all trials. This removes speaker echo
as a confounder but does not test echo cancellation. Existing household memory
settings remain unchanged, including their ordinary active-session cloud work.

## Test order and launch commands

Stop the running conversation app first. Leave the simulator daemon running.
Use the same Mac workload and headphones for each run. Paste this function into
zsh from the repository root; it only sets configuration for the launched process:

```bash
reachy_audio_test() {
  local output_backend speech_wobble
  case "$1" in
    A) output_backend=gstreamer; speech_wobble=1 ;;
    B) output_backend=sdk; speech_wobble=1 ;;
    C) output_backend=sdk; speech_wobble=0 ;;
    *) echo 'Choose A, B, or C'; return 2 ;;
  esac
  AUDIO_DIAGNOSTICS_ENABLED=1 \
  AUDIO_OUTPUT_BACKEND="$output_backend" \
  SPEECH_WOBBLE_ENABLED="$speech_wobble" \
  REALTIME_PROVIDER=openai_live \
  HOUSEHOLD_MEMORY_ENABLED=1 \
  HOUSEHOLD_MEMORY_CAPTURE_ENABLED=1 \
  HYBRID_RUNTIME_ENABLED=1 \
  LOCAL_ACOUSTIC_WAKE_ENABLED=1 \
  LOCAL_ACOUSTIC_WAKE_MODEL_DIR="$HOME/Documents/Reachy/private-wakeword/simulator-official33" \
  LOCAL_STT_BASE_URL= \
  HYBRID_CLOUD_INACTIVITY_SECONDS=30 \
  .venv/bin/reachy-mini-conversation-app --media-backend local --ui
}
```

Run `reachy_audio_test A`, then B, then C, stopping with Ctrl+C between runs.
A uses the known speaker bypass (its enabled SDK wobbler receives no assistant
PCM); B uses SDK playback and wobble; C explicitly disables SDK and daemon speech
wobble through the public API. Idle movement stays enabled in all three.

For each run:

1. Wait 15 seconds in standby for startup work to settle.
2. Wake Reachy and say: "Please repeat exactly: the blue bicycle is beside the yellow door."
3. Ask: "Count slowly from one to ten." Interrupt near four with "Stop counting and say pineapple."
4. Wait for the 30-second inactivity standby transition. Note whether it completes.
5. Repeat the wake/conversation/standby cycle once, then stop the app.

Stop a run if it crashes or hearing becomes unusable. A crash is a result, not a
reason to keep restarting that condition. Restore ordinary testing by running the
previous command with `AUDIO_OUTPUT_BACKEND=gstreamer`, diagnostics unset/0, and
speech wobble unset/1. No `.env` edits are needed.

## What to save

Copy only `Audio diagnostics` summary lines, queue-full warnings, and runtime
transition lines, plus your A/B/C label and whether words, interruptions, and
head movement worked. Do not enable debug logging or save full transcripts/audio.
A native crash may lose the final summary; earlier five-second summaries still
help. Do not share full environment dumps, crash-report private paths, or memory
contents. This diagnostic does not itself write a log file.

| Run | Dropped counter start/end | Max queue age | Max forward time | Max push time | Max loop lag | Words / interruption / movement |
|---|---|---|---|---|---|---|
| A | pending | pending | pending | pending | pending | pending |
| B | pending | pending | pending | pending | pending | pending |
| C | pending | pending | pending | pending | pending | pending |

## Interpretation and acceptance

Counters are cumulative within each capture-worker lifetime; compare their changes
within settled active windows, not raw totals across differently sized runs.
Timings are per reporting window with count/mean/max in milliseconds:

- `capture_pull`: time inside SDK microphone retrieval, including empty polls.
- `capture_gap`: time between successful SDK frame returns, not hardware timestamps.
- `queue_age`: time from SDK return until app consumption (excluding SDK internal buffers).
- `input_forward`: awaited app input forwarding, including hybrid work and provider
  send when active; not isolated network latency or server receipt time.
- `playback_push`: synchronous SDK push or bypass enqueue call; not audible latency.
- `loop_lag`: delay beyond a scheduled 100 ms event-loop wake.

B-only drops/lag that improve in C implicate speech-wobble-related load. Similar
B/C degradation relative to A implicates the normal output path more broadly.
Large push times coincident with loop lag and queue growth support a blocking
playback hypothesis. Large forward time with low loop lag suggests awaited input
work is slow. These correlations guide follow-up instrumentation; they do not
alone prove causality. A long blocking SDK microphone read can be normal.

Acceptance for preparation: flags preserve default behavior, summaries contain
only timing/count metadata, both playback paths remain intact, monitor cancels at
shutdown, privacy and full repository checks pass. Human acceptance requires the
completed table; no fixed latency target or causal fix is invented in advance.

Risks: SDK mode previously crashed; existing macOS native flush skip can leave
buffered speech during interruption; diagnostics add small measurement overhead.
If native failure prevents comparison, retain bypass and report the failed stage.

## Implementation and documentation

Implementation: bounded shared timing accumulator, capture/forward/push hooks,
cancellable event-loop monitor, startup speech-wobble switch. Update README,
`.env.example`, speaker feature documentation and plan index. No architecture
boundary or generated diagram change. Tests cover aggregation, concurrency,
forwarding/privacy, both playback routes, delayed scheduler and lifecycle cleanup.

## Validation log

- 2026-09-13: Test prepared; no microphone/cloud/native experiment started.
- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/audio/test_diagnostics.py tests/test_console.py tests/test_main.py tests/test_config.py -q`: 191 passed.
- `.venv/bin/ruff check . --fix`: passed; `.venv/bin/ruff format .`: 133 files unchanged.
- `.venv/bin/mypy --pretty --show-error-codes`: passed, 68 source files.
- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v`: 1119 passed, 2 skipped in 23.13 seconds. Optional native speaker and acoustic-wake simulator checks remain skipped.
- Extracted launch function checked with `zsh -n`: passed. No app launched.
- `git diff --check`: passed.
- Self-review: no SDK internals/dependencies modified; existing dirty documentation preserved; diagnostics default off; no audio content enters the timing collector. Actual A/B/C and physical hardware evidence remain pending.

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

## Controlled SDK flush probe — 2026-09-13

User reports headphone SDK interruption trails by 1–2 seconds while bypass stops
promptly. Inspection found no GPT Live interruption-triggered playback flush;
its clear callbacks cover lifecycle/context invalidation. Independently, macOS
LOCAL skips native clearing as crash containment. Neither proves the missing-word
cause. No undocumented Live speech-boundary events are introduced.

Next milestone: isolate the SDK public clear_player operation before wiring any
new interruption trigger. tests/native_sdk_flush_smoke.py opens only local audio,
submits two seconds of quiet synthetic tone, waits 400 ms, announces FLUSH NOW,
and clears playback three times. Microphone frames are discarded after counting;
three half-second windows must each deliver frames after clearing. It uses no
provider, daemon, movement, credentials or recording. Speech wobble is not enabled
in this baseline. Passing capture checks does not prove fresh samples, audible
flush timing, AEC, wobble compatibility or live conversation acceptance.

There is no public SDK exact queue-duration counter. Submitted duration and elapsed
wall time are reported separately: their difference is not an actual native queue
measurement. Human listening establishes whether the tone stops at FLUSH NOW;
native dropped samples or pipeline latency cannot be inferred from push success.

Stop the conversation app, wear headphones, keep volume low, then run:

```bash
REACHY_MINI_SKIP_DOTENV=1 REACHY_RUN_SDK_FLUSH_SMOKE=1 .venv/bin/pytest tests/test_native_sdk_flush.py -q -s
```

The parent imposes a 30-second deadline and reports native exit/signal. The normal
test suite skips device access. Default playback and the Mac workaround remain
unchanged. No dependency or architecture changes. After native acceptance, design
a separate supported interruption trigger; clearing on late transcript fragments
could discard the next answer. Do not turn this probe into automatic flushing.

Probe validation: the first native attempt exited 255 during libgstpython plugin
loading, before audio testing. Matching the existing native smoke's child-only
plugin environment isolation resolved startup. The retry passed in 12.04 s:
flush returns 1.6/1.0/0.9 ms; post-flush half-second capture counts
[91,51,50], [91,51,51], [91,50,51]. SDK reported software AEC enabled and
pipeline minimum latency 294 ms / maximum 1204 ms; these are negotiated latency
bounds, not measured audible delay. No human audible-stop confirmation collected.

Commands: opt-in pytest command above passed; Ruff check/fix and format passed;
strict mypy passed (69 source files); git diff --check passed. Full
`REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` yielded 1165 passed,
3 skipped, 2 failures in 30.24 s. Both failures are in existing separate
`tests/test_openai_live_review.py`: successful merge watcher condition timeout
and review flag refresh calling a missing Config method. Those unrelated files
were not repaired as part of this probe. Default skip and native success are
separate evidence; no GPT Live interruption behavior has changed.

## Audible flush failure investigation — 2026-09-13

User confirmed tones continued after FLUSH NOW (native run passed in 11.22 s).
This rejects audible-stop acceptance despite fast clear returns and capture counts.
Investigation also found a probe defect: it passes mono directly to GStreamerAudio,
which declares two output channels. Unlike MediaManager, this lower-level class
does not duplicate channels. Thus the claimed 2000 ms submission is invalid (bytes
represent 1000 ms of stereo); previous timing interpretation is withdrawn. Fix
channel adaptation before another measured trial. The normal app uses MediaManager
and already gets this conversion. No application change is indicated by this flaw.

Installed SDK and upstream main clear only appsrc, ignore flush-event return values,
and log success unconditionally. Playback includes appsrc queue, a mixer with a
second continuous-silence input, then echo reference and separate speaker/wobbler
queues. GStreamer aggregator source forwards a flush start downstream only after
matching flush starts on all input pads. This is a strong source-level explanation
for downstream audio surviving a speech-input-only flush, not a measured location
of every remaining sample. Native event tracing is still needed for confirmation.

Sources checked: upstream reachy_mini media/audio_gstreamer.py and GStreamer
subprojects/gstreamer/libs/gst/base/gstaggregator.c on GitHub main. Upstream LOCAL
clear currently has the same relevant implementation; no verified upgrade fix.
Public set_max_output_buffers limits appsrc only, drops old buffers, and cannot
clear downstream audio. Public stop_playing stops the shared microphone pipeline
as well: unsuitable as a routine conversational interruption substitute.

Recommended next work: correct probe channel shape, reproduce with synthetic-only
native event/queue metadata, and prepare an upstream SDK playback-wide flush fix
that preserves capture and clock continuity. Do not patch installed SDK internals
or enable app flushing on transcript arrival. No runtime code changed in this
investigation. Documentation checked with git diff --check; no native retest run.

## SDK candidate prepared — 2026-09-13

The synthetic probe now expands mono to get_output_channels(), with a hermetic
regression asserting 60 buffers of (1600,2) float32, each representing 100 ms.
The installed SDK is unchanged. Candidate checkout:
`/Users/eduardoyi/Coding/reachy-sdk-reviews/playback-flush`, branch
`codex/local-playback-flush`, upstream base 9d364df0d8b6c92fbda7a9252fc378e23d4ab47c.

Native observation confirmed old flush events reach appsrc -> queue -> mixer
sink_0 and stop there: no mixer source or speaker event. Candidate keeps a reference
to the continuous-silence source and sends matching-sequence flush-start/stop to
both sources, with reset_time=False and no shared pipeline state changes. Event
rejections are surfaced after releasing both inputs. New native trace reaches the
speaker and wobbler sink, never the capture sink. 50 fresh capture buffers had
strictly increasing PTS after clearing; pipeline base time was unchanged.

Three corrected candidate tone cycles completed without crash: clears returned
1.4/0.7/1.9 ms, with capture in all post-flush windows. This establishes event
propagation and capture continuity, not audible stop latency, AEC quality, actual
wobble movement, concurrent push/clear safety or Linux/Windows/hardware acceptance.
No new microphone contents were persisted or logged. The trace uses synthetic
silence and only event names/timestamps; the tone probe uses quiet synthetic PCM.

Six focused SDK tests passed (test_local_playback_flush.py and
test_audio_gstreamer.py). Both new regressions failed against the original SDK,
then passed after restoring the candidate. SDK broader non-hardware suite could
not collect: av and semver are missing in the reused app environment. No dependency
was installed into the user's venv. Source Ruff passed. Full upstream CI and typing
remain required before publication. Patch prepared locally; no issue/PR sent.

Reproducible candidate commands use the app venv Python, PYTHONPATH pointing to
checkout/src, and clear inherited GI_TYPELIB_PATH, GST_PLUGIN_PATH_1_0,
GST_PLUGIN_SYSTEM_PATH_1_0, GST_PLUGIN_SCANNER_1_0, GST_REGISTRY_1_0 and
GST_PYTHONPATH_1_0 as in the existing native probe. Run tests/flush_trace.py in a
bounded subprocess; run the corrected app tests/native_sdk_flush_smoke.py with the
same candidate environment for audible acceptance. Do not change installed SDK
or wire GPT Live interruption until that acceptance and upstream review.

Final app gate: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
`.venv/bin/mypy --pretty --show-error-codes` passed; `REACHY_MINI_SKIP_DOTENV=1
.venv/bin/pytest tests/ -q` passed 1170 tests, 3 optional skips (32.09 s).
`git diff --check` passed in both checkouts. Exported candidate patch:
`/Users/eduardoyi/Coding/reachy-sdk-reviews/playback-flush.patch`.

## Wobble-enabled probe — 2026-09-13

User tentatively heard prompt stops in the patched baseline. Next bounded step adds
`--wobble` to tests/native_sdk_flush_smoke.py. It enables public SDK wobbling with
a count-only callback, asserting callbacks during each playback window. No robot
connection or motion commands occur; this validates callback/flush coexistence,
not visible movement, speech-derived offset quality or live interruptions.

Candidate native run passed three cycles: eight callbacks before each flush,
1.1/1.2/2.7 ms clear returns and capture in all post-flush windows. No crash.
Hermetic channel/callback variants passed (2 passed, 1 native skip). Human audible
acceptance with wobble remains pending. Repeat the prior candidate launch command
with `--wobble` appended; the installed SDK and app remain unchanged.

Wobble probe final gate: Ruff check and format check passed; strict mypy passed
(69 files); `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` passed
1171 tests, 3 optional skips in 28.49 s. git diff --check passed.

## Opt-in local interruption integration

User confirmed prompt audible stops with wobble enabled (9/8/8 callbacks and
ongoing capture). Next milestone: experimental local GPT Live queue clearing.
Reuse RmsEnergyVAD at the configured local RMS threshold: 120 ms of consecutive
above-threshold input latches one onset; 400 ms of quiet rearms it. Only clear when
nonquiet assistant output arrived within the last second. Keep input bytes, server output and
transcripts unchanged; this does not cancel server generation. No late-transcript
clearing, provider commands, new models, downloads or additional cloud calls.

Separate default-off OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED and
MACOS_LOCAL_AUDIO_FLUSH_ENABLED allow supervised headphone testing with the
candidate SDK via PYTHONPATH. Native override must only be used with the tested
SDK candidate; it does not inspect/patch SDK internals. Normal defaults retain
containment. Energy detection can mistake noise/echo for speech and cannot solve
continued server generation. Silence rearming may allow another clear during a
long utterance; headphone acceptance is required before broader use.

Tests must cover default-off, brief noise, sustained onset, one clear per speaking
stretch, quiet rearm, no output activity, unchanged microphone transfer, disconnected
privacy, and macOS native override routing. Document results and limitations; no
claim of live interruption or missing-word acceptance without a user trial.

### Live trial command

Start one simulator normally. Stop any other conversation instance, then run from
johnny with headphones or the accepted laptop-speaker setup. The command retains the previously tested memory/wake
settings; ordinary active-session cloud work still occurs. No new service or
recording is introduced. This command deliberately enables experimental clearing
only in the child process. Return to ordinary behavior by omitting both new flags
and the candidate PYTHONPATH on the next launch.

```bash
env -u GI_TYPELIB_PATH -u GST_PLUGIN_PATH_1_0 \
-u GST_PLUGIN_SYSTEM_PATH_1_0 -u GST_PLUGIN_SCANNER_1_0 \
-u GST_REGISTRY_1_0 -u GST_PYTHONPATH_1_0 \
PYTHONPATH=/Users/eduardoyi/Coding/reachy-sdk-reviews/playback-flush/src \
OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED=1 MACOS_LOCAL_AUDIO_FLUSH_ENABLED=1 \
AUDIO_OUTPUT_BACKEND=sdk SPEECH_WOBBLE_ENABLED=1 AUDIO_DIAGNOSTICS_ENABLED=1 \
REALTIME_PROVIDER=openai_live HYBRID_RUNTIME_ENABLED=1 \
HOUSEHOLD_MEMORY_ENABLED=1 HOUSEHOLD_MEMORY_CAPTURE_ENABLED=1 \
LOCAL_ACOUSTIC_WAKE_ENABLED=1 \
LOCAL_ACOUSTIC_WAKE_MODEL_DIR="$HOME/Documents/Reachy/private-wakeword/simulator-official33" \
LOCAL_STT_BASE_URL= HYBRID_CLOUD_INACTIVITY_SECONDS=30 \
.venv/bin/reachy-mini-conversation-app --media-backend local --ui
```

Wake Reachy, ask him to count slowly to twenty, then interrupt near four with
"Stop counting and say pineapple." Report audible stopping, whether pineapple is
said, head movement and crashes. `Local microphone onset: clearing queued GPT Live
playback` indicates the local signal fired; it does not establish that the model
heard every word. A continued old answer can still come from server generation.

Validation: launcher `--help` passed with candidate environment and trial flags;
no live microphone, simulator, provider or cloud request was started for this
integration. Earlier native tone/capture evidence remains separate.

Integration focused validation: 190 tests passed across the existing blocked-speaker
regression, provider, console and config suites (5.61 s). One full-suite run during
host slowdown failed only the existing blocked-speaker 200 ms timing assertion
(measured 229 ms; suite took 325.17 s). It passed on focused rerun; no threshold or
speaker implementation was changed to hide it. Ruff check/format and mypy passed.
The live command extracted from this plan passes zsh -n.

Final integration rerun: `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`
passed 1180 tests, 3 optional skips in 27.79 s. The earlier timing failure is retained
above. `git diff --check` passed. Live headphone acceptance remains pending.

## Live headphone result — 2026-09-13, 15:56–15:57

User reported "everything went as expected" for the requested prompt-stop,
pineapple-response and speech-head-movement scenario. Launch excerpt selected the
candidate SDK, SDK playback, wobble and both experimental flags. This is human
acceptance of this single simulator/headphone conversation experience.

Diagnostics: capture dropped count stayed at 60 from first summary through final
4558 captured frames: no additional drops in the observed interval. Maximum loop
lag 24.82 ms; maximum playback push 1.13 ms. Wobbler enabled; excerpt includes orderly
runtime_closed shutdown and wobble disabling, with no traceback/native crash shown.
It does not establish inactivity timeout acceptance (shutdown reason runtime_closed).

Important attribution limit: no "Local microphone onset: clearing queued GPT Live
playback" event appears. Native clear logs occur at activation and shutdown, not
inside the conversation. Therefore this successful experience does not verify the
new local onset trigger fired or caused the successful interruption. Do not promote
it to a proven local-barge-in fix or enable it by default. Repeat/measure only if
needed to establish that mechanism; user-visible behavior worked in this trial.
Physical hardware, loudspeaker echo, long-run reliability and upstream CI remain
pending. Documentation-only update; git diff --check passed.

## Longer headphone interaction — 2026-09-13

User reported a long interaction working well. Supplied excerpt's complete
summaries span 16:09:54–16:14:31; it begins mid-session, so this is not the whole
interaction. Capture counter rose 51873 -> 79546; dropped counter 111 -> 144
(33 additional drops in the excerpt). Peak loop lag 139.92 ms, playback push
4.90 ms, input forward 34.21 ms and queue age 85.28 ms. Small residual capture
loss remains; do not describe this run as zero-drop.

Nine local microphone-onset clear messages each precede SDK clear confirmation.
This now verifies the local detector-to-native-clear path ran during conversation,
unlike the initial short trial. Metadata alone cannot classify every onset as
correct or measure audible stop latency; the user's positive experience supplies
the qualitative result. Closely spaced detections can reflect the quiet-rearm
heuristic and are not by themselves proof of false triggers.

The runtime returned to LOCAL_AMBIENT on inactivity_timeout at 16:11:34, then
acoustic wake reactivated generation 2 at 16:12:06. Final runtime_closed shutdown
and wobble disabling completed without a recorded error/crash in the excerpt.
This supplies live inactivity/reactivation evidence as well as repeated clearing.
Physical hardware, loudspeaker echo, upstream CI and broader reliability remain
pending. No runtime change from this review; git diff --check passed.

## Laptop-speaker acceptance — 2026-09-13, 16:17–16:21

User explicitly confirmed good operation without headphones. Launch selected the
candidate SDK via PYTHONPATH, `AUDIO_OUTPUT_BACKEND=sdk`, speech wobble, diagnostics,
GPT Live, and both local interruption/native-clear trial flags. SDK logs show
software WebRTC echo cancellation enabled. This supports practical speaker-mode
acceptance on this Mac, with no reported disruptive self-echo. The log alone cannot
measure echo suppression or classify every detector event.

Three local-onset events at 16:19:38.597, 16:19:52.268 and 16:19:53.046 were followed
by native clear confirmations. Complete summaries span 16:17:01.376–16:21:23.104:
captured 565 -> 26737, dropped 54 -> 75 (21 additional drops). Maxima: loop lag
238.91 ms, playback push 2.97 ms, input forward 83.01 ms, queue age 76.50 ms.
Startup reported capture/local-processing overflow and absent physical audio board;
it selected the default source/sink and software AEC. No native crash or traceback
appears in the supplied excerpt, but it ends mid-log and does not establish clean
shutdown or another inactivity/reactivation cycle for this run.

Documentation now distinguishes accepted candidate SDK laptop-speaker operation
from the still-unreferenced GStreamer bypass. Runtime/defaults/dependencies remain
unchanged. Verification: supplied metadata review, cross-document consistency and
`git diff --check`; no tests rerun for this documentation-only update.


### Gemini SDK mixer crash investigation — 2026-09-20

Scope: preserve SDK playback, speech wobble and echo-reference routing while
reproducing the September 19/20 native mixer crashes. Do not change household
memory, credentials, active configuration or installed SDK internals. Existing
OpenAI speaker acceptance does not establish Gemini stability.

Both macOS reports identify `audiomixer0:src`, `do_convert_out`, `do_quantize`,
`converter_generic` and `gst_audio_aggregator_convert_pad_convert_buffer`, with a
null-address SIGSEGV. The second run completed local memory review without any
review model request; memory timing alone is not evidence of causation.

Controlled public-SDK subprocess tests, with synthetic zero-valued float32 audio
and no cloud connection, reproduced SIGSEGV for 1, 2, 3 and 10 sample buffers at
16 kHz stereo. Tested 80, 160, 240, 320 and 512 sample buffers survived eight
bursts and gaps each. The runtime reports GStreamer 1.28.4. Microphone frames were
not stored or sent; no household audio was used.

A bounded Gemini provider probe using only a made-up counting request observed
PCM byte lengths `{2: 1, 9120: 1, 7680: 2, 36480: 1, 13440: 1}`. The 2-byte
fragment is one mono int16 sample at 24 kHz, resampled by the shared app boundary
to one sample at the SDK's 16 kHz. This connects real provider output to the
reproduced native failure; it does not establish that Gemini alone can trigger
it. A first probe with minimal configuration returned APIError; a matching
medium-thinking/voice configuration succeeded. No provider PCM was persisted.

Implementation order: normalize tiny output fragments without discarding speech;
regression-test ordering, final tails and interruption fences; replay synthetic
fragment sizes through SDK in a bounded child; run focused and full gates.
Acceptance: native child survives while preserving SDK path, no lost/reordered
samples, no old tail after interruption; later user checks audible speech,
speech movement and speaker interruption. No hardware acceptance is implied.


### Tiny-fragment fix validation — 2026-09-20

- Added `Pcm16ChunkBuffer` to the Gemini output path, retaining fewer than 240
  PCM16 samples, joining next chunks and padding only a final tail. All existing
  queue clears discard pending samples, including shutdown/reconnect/interruption.
- Candidate SDK child replay of observed synthetic sizes with `--raw`: **exit -11**.
  The same child with buffering: **exit 0**, three playback/clear cycles. Repeated
  with wobble enabled: **exit 0**, 191 SDK wobble callbacks. Commands ran through
  `subprocess.run(..., timeout=30)` with the documented candidate PYTHONPATH and
  native environment cleanup; native probe is `tests/native_sdk_gemini_chunks.py`.
- Installed SDK opt-in command:
  `REACHY_RUN_SDK_CHUNK_SMOKE=1 REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_native_sdk_gemini_chunks.py -q`:
  **1 passed in 6.38 s**. No cloud connection or stored microphone data.
- Focused streaming/Gemini/console tests: **172 passed** before the additional
  shutdown regression; final Gemini subset **71 passed**.
- Final gate: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .` (146
  files), `.venv/bin/mypy --pretty --show-error-codes` (71 source files), and
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`: **1251 passed, 4 skipped,
  2 deliberate invalid-status SDK warnings in 39.86 s**. Four skips are opt-in
  native tests; the new SDK chunk case was separately run above. Diff check passed.
- Unrelated TypeSafe exploration files were preserved. No dependencies, installed
  SDK code, .env or active local provider defaults changed. No commit/push made.
- Remaining acceptance: user-led Gemini SDK conversation, speaker echo handling,
  interruptions and visible speech movement. Native callbacks establish the route,
  not subjective motion quality or an AEC measurement. Physical hardware untested.
