# Hybrid household companion benchmark

- Status: `in-progress`
- Last updated: 2026-09-10
- Machine: Apple Silicon Mac, simulator, SDK LOCAL media backend
- Scope: Phases 0–5

The companion loop is implemented in the local checkout. Prior simulator
acceptance covers baseline SDK behavior, headphone Gemini conversations,
local/cloud transitions and ambient privacy, both English context strategies,
and five exact local commands. Remaining gaps include first-audio/speech-to-action
latency, sustained resources, speaker echo, multilingual context quality and
all real robot/Pi measurements.

Official33 is now the user-approved, off-by-default supervised acoustic wake MVP.
The native app adapter reproduced17/20 recorded calls, and an opt-in headless
MuJoCo/SDK/movement test passed with a fake provider and zero wake audio sent.
Human-spoken graphical microphone/conversation acceptance remains pending.
Training is paused. See [retained wake models](docs/plans/reachy-wake-model-catalog.md)
for all promising options and [current phase status](docs/plans/reachy-hybrid-household-companion.md#current-status--2026-09-09).
Missing values are marked `not measured`; they are not estimated.

## Supervised headless MVP accepted — 2026-09-10

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
`LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS` (five seconds by default, capped at 15).
Exact local
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
LOCAL_STT_MODEL=openai/whisper-small.en
LOCAL_VAD_RMS_THRESHOLD=100
LOCAL_STT_TIMEOUT_SECONDS=15
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
  openai/whisper-small.en

# Terminal 3 — app and UI
AUDIO_OUTPUT_BACKEND=gstreamer \
  .venv/bin/reachy-mini-conversation-app --media-backend local --ui
```

Open http://127.0.0.1:7860/. Space/Enter remains the keyboard fallback. For
Strategy B, change `LOCAL_CONTEXT_STRATEGY=activation`; activate by keyboard.

## 3–8. Measurements

| Metric | Evidence | Result |
|---|---|---:|
| Direct Gemini connection | Accepted saved-log headphone and speaker runs | 637.5 ms with headphones; 455.6 ms with Mac speakers; two-run P50 546.6 ms, with no meaningful P95 from this sample count |
| Direct conversation quality | Accepted saved-log headphone run plus user observation | Five requested turns sounded smooth; both barge-ins worked, Reachy moved left, and the process exited without crash or traceback |
| Direct tool latency | Accepted saved-log headphone run | `move_head(left)` result was accepted in 2.6 ms; local emotion results were 0.6–317.7 ms, and one remote search result took 3056.2 ms |
| Hybrid keyboard activation to connected | Accepted Phase 2 simulator/Gemini run | approximately 885 ms total, including 505.6 ms provider connection |
| Hybrid interruption flush | Accepted Phase 2 run | 1.0–1.3 ms local flush samples |
| Hybrid session duration | Accepted Phase 2 run | 167.511 s |
| First response audio | Accepted saved-log headphone run | No usable speech-end marker or first-audio sample was emitted for the five turns; audible response was prompt and smooth, but exact latency remains unavailable rather than fabricated |
| Direct interruption flush | Accepted saved-log headphone run | 1.4 ms and 3.8 ms; P50 2.6 ms; both audible interruptions succeeded |
| Direct speaker feedback | Accepted saved-log Mac-speaker control | Failed immediately: six self-triggered interruption flushes in about 18 seconds while the user stayed silent. The process remained live and exited without traceback; headphones are required |
| Strategy A dinner context | Automated integration plus accepted live English run | Accurate bounded local context produced a contextual Gemini answer. Two STT segments completed, 68 context characters were retained, ambient cloud bytes remained zero, cloud video remained zero, and the runtime returned to `LOCAL_AMBIENT` with Gemini closed |
| Strategy B dinner context | Automated integration plus accepted live English run | A 20-second RAM snapshot was transcribed locally after keyboard activation and produced 68 characters of useful context; Gemini answered contextually. Local STT took 6.015 s, context preparation took 6.017 s, and activation to Gemini ready took approximately 7.228 s |
| Wake activation latency | Automated deterministic clock plus one live path | live numeric value not measured |
| Wake false positives / misses | One historical live configured wake-word check, deterministic tests, and 30 real positive offline fixtures | `LOCAL_WAKE_WORD=computer` reached the final-STT detector. The Spanish sherpa checkpoint missed 30/30 private Reachy-family recordings at documented settings and 28/30 at an overly aggressive setting. Negative speech was not recorded after this failed positive screen, so false positives remain not measured |
| Local command latency | Automated deterministic clock plus live simulator acceptance | All five allowlisted commands have executed locally without constructing Gemini. `look left` followed the STT response by about 14 ms; the final three-command run had 3 executions, 0 failures/escalations, 0 cloud audio bytes, and 12 dropped local-processing frames. End-to-end speech latency remains unmeasured |
| Local STT latency | Accepted Strategy A English run | 11.171 s total across two requests; 9.227 s maximum request latency; no failures, timeouts, or drops |
| Local STT CPU / memory | Post-run macOS process inspection | Peak CPU not measured. Transformers Serve measured 3.4 GB current/peak physical footprint after Strategy A and 2.5 GB current/peak after Strategy B; the app measured 185.7 MB after Strategy B. These are macOS snapshots, not Raspberry Pi measurements |
| Local STT backend | Loopback endpoint and accepted live request | `openai/whisper-small.en` accurately retained the tested English dinner context. The multilingual `openai/whisper-small` run materially misrecognized both key terms; Spanish, Portuguese, and Italian quality remain a new benchmark requirement |
| Acoustic sherpa fixture benchmark | Deterministic fake-engine tests for the optional `wake-benchmark` CLI | 74 tests passed for caller-owned model/fixture validation, explicit Reachy/Richie/Ritchie/Richi/Ricci candidate generation and deduplication, exact caller-vocabulary validation, canonical-tag collision and marker-like OOV rejection, unsafe tokenizer-token rejection, one exact `reachy`/`@reachy` result boundary, TP/FN/TN/FP counts, negative exposure, event-based false positives per hour, fixture recall, first-detection-only offline timing, real-time factor, bounds, cleanup, privacy, reproducibility metadata, errors, and CLI behavior. No model weights were bundled or downloaded, no microphone was opened, and no live quality/resource result was measured |
| Spanish sherpa real-positive screen | 30 private Mac-microphone recordings replayed through the offline fixture CLI | 0/30 detected at threshold 0.20/boost 1.0; best result 2/30 (6.7%) at threshold 0.02/boost 8.0. Individual spelling runs did not materially improve recall. About 0.015 real-time factor, 167 MiB peak RSS, and 350% multi-core process CPU on this Mac. Candidate rejected; no negative, production, onset-latency, or Pi claim |
| Existing Reachy openWakeWord positive screen | Same 30 private recordings, upstream commit and model hash pinned, ONNX CPU inference in 80 ms frames | 6/30 detected (20%) at the documented 0.5 threshold; threshold 0.2 reached only 7/30. Gain 2x, gain 4x, and -12 dBFS peak normalization all stayed at 6/30. About 0.036 real-time factor, 98% process CPU, 188.9 MiB peak RSS, and 0.112 s load time on this Mac. Candidate rejected; no negative, production, onset-latency, or Pi claim |
| Split-final wake follow-up | Deterministic hybrid-runtime/capture tests | Exact follow-up stays cloud-free; uncertain follow-up and timeout preserve the FIFO for cloud; live latency/resource value not measured |
| Local capture pressure | Deterministic LocalStream/runtime burst test | Slow local observation no longer holds the capture handoff; admitted local frames remain ordered, capture-worker drops stay zero, and sustained live drop rate is not measured |
| App-owned speaker timing | Deterministic timing/flush tests, native child, and live Gemini smoke | The old 500 ms queues retained only 5/20 burst buffers; the bounded 30-second replacement retains 20/20 marked buffers in order, and fresh headphone playback passed. Laptop-speaker playback can feed back through the Mac microphone and trigger Gemini interruptions because the bypass is outside the SDK echo-cancellation reference |

Multilingual STT, peak CPU, and remaining quantitative Phase 4–5 measurements
are still required before this report can be marked complete. The direct Phase
1 benchmark and both Phase 3 dinner-context strategies are accepted.

The acoustic benchmark is deliberately a deterministic foundation only. Install
the optional `wake-benchmark` group (`sherpa-onnx==1.13.6` plus its required
`sherpa-onnx-core==1.13.6`, `sentencepiece==0.2.2`, and `pypinyin==0.55.0`
tokenizer dependencies) and supply
an external extracted checkpoint plus positive/negative WAV or raw PCM16
fixtures for a real run. The CLI has no network, simulator, robot, or live
microphone mode; it never downloads or bundles weights, removes its temporary
keyword file, accepts only the exact normalized `reachy`/`@reachy` tag, and emits
metadata without paths, samples, result tags, or transcripts. It reports
negative audio exposure, event-based FP/hour, fixture-level recall,
path-free model content fingerprint, installed package versions, UTC start,
sample rate, and portable platform identity; it does not report queue/drop
metrics or collect hostname, username, or CPU-model identity. Its offline
fixture-start-to-result timing is not acoustic onset/activation/online latency;
only the first exact detection per fixture contributes a timing sample, while
duplicate exact events remain counts for false-positive metrics. Positive
fixtures must contain exactly one intended wake. Invalid fixtures abort
without a partial report. The reviewed source revision
`b55eae785857eba4af1e24b200104354be23a975` historically produced
`▁ti to @tito` with the BPE seam; the current Reachy candidate generator is
covered deterministically, while content fingerprinting remains authoritative.
The required 30-wake positive screen ran on 2026-09-07. The reviewed Spanish
checkpoint failed it, so the negative/near-miss recording was intentionally
skipped. The same private positive fixtures should screen the next candidate;
collect negative exposure only after a candidate has useful positive recall.

On 2026-09-03, the generalized Reachy benchmark's 74 focused tests passed. The
complete dotenv-isolated repository gate passed Ruff, formatting, strict mypy,
863 tests with one explicit native-audio skip, lock consistency, both
JavaScript syntax checks, and `git diff --check`.

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

The 2026-08-30 live `computer, look left` acceptance produced a local STT 200,
then `LOCAL_AMBIENT` → `LOCAL_COMMAND` → local `look_left` →
`LOCAL_AMBIENT`. No Gemini handler construction or connection appeared in the
run. This proves the command stayed local; it does not by itself measure raw
ambient byte counters or end-to-end speech latency.

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
  local-command acceptance pass. The former default `Reachy` phrase produced
  no action because no exact matching final transcript token reached the
  detector; no fuzzy transcript alias was added, and this is not an acoustic
  Reachy quality result.
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
- The first sample-clock correction did not fix audible quality. Human
  acceptance still sounded choppy/fast-forwarded. Native probing then proved
  the two downstream-leaky 500 ms queues silently discarded normal burst
  audio: only 5 of 20 100 ms frames survived even though `push-buffer` returned
  success. The queues now remain non-blocking and bounded but retain 30 seconds
  by bytes/time; incompatible appsrc versions fail closed rather than buffering
  without a bound. The revised native regression retains 20/20 uniquely marked
  buffers in order and verifies playback after a flush. The full repository
  gate passes 730 tests with one explicit native skip. A fresh restarted Gemini
  session passed human audible acceptance with headphones. Without headphones,
  the app-owned simulator sink is outside the SDK echo-cancellation reference,
  so laptop-speaker feedback can cause Gemini to interrupt its own response.
- MLX Audio's current STT server explicitly bridges inference through a file in
  `/tmp`, so it was rejected for this RAM-only ambient path. The selected
  Transformers candidate decodes uploaded bytes through `BytesIO`; the live run
  must still monitor temporary directories before accepting that guarantee.

## 11. Raspberry Pi 5 8GB assessment

Not yet assessable. The accepted Mac run measured a 3.4 GB current/peak physical
footprint for Transformers Serve with `openai/whisper-small.en` after two STT
requests. That is useful evidence but not a Pi result: macOS memory accounting,
model execution, and performance differ from Raspberry Pi OS. Peak CPU and
real-time factor were not measured. The report therefore makes no Raspberry Pi
feasibility claim until the multilingual candidate is selected and the same
workload is run on a Pi 5 8GB.

## 12. Recommendation

Keep the accepted English Strategy A run as the default regression baseline:
it has context ready before activation, whereas Strategy B added about 6.0
seconds of local transcription and about 7.2 seconds to Gemini readiness. Next
measure peak CPU, wake false positives/misses, and sustained local-processing
drops. Run the Spanish/English/Portuguese/Italian model matrix as separate new
scope through the same local preview and privacy counters.
Reject both the tested Spanish sherpa checkpoint and the existing community
Reachy openWakeWord model for the household pronunciation. Reuse the same 30
private positive fixtures as an initial screen for the approved custom
multilingual openWakeWord training plan, keeping them out of training and
threshold tuning. Build a final multi-speaker/multi-session positive set before
negative testing; only collect and run the negative/near-miss set after a
candidate clears both positive gates. Preserve the transcript detector and
keyboard fallback. Keep Porcupine as a separately reviewed proprietary fallback
rather than the next required step. The historical threshold/gain sweeps on the
30 clips are exploratory rejection evidence only and do not set the custom
model's threshold.
Prefer Strategy A only
if its always-on local cost and wake quality are acceptable. Then benchmark a
Pi-compatible VAD/STT backend through the existing protocols before selecting
production models or starting later memory, Home Assistant, face recognition,
heartbeat, or local-LLM work.

## 13. Custom wake training preflight — 2026-09-07

The Mac ARM64 versus Docker Linux ARM64 reference check compared 668 scores
across six public/synthetic fixture runs and two reference classifiers. Maximum
absolute score difference was 0.0 (pre-registered absolute/relative tolerance
0.0001); positive, unrelated-speech, silence/noise, and reset/repeat checks all
passed at threshold 0.5. Python 3.12.14 and ONNX Runtime 1.27.0 matched across
both environments. The community Mac scoring discrepancy was not reproduced;
this does not establish why the existing Reachy model missed household clips.

An English-only CPU pipeline smoke generated six positives and six negatives,
kept synthetic validation speakers separate, ran augmentation/feature extraction
and eight training steps, and exported a valid single-file ONNX model. Its
203,311-byte artifact passed independent Mac/Linux loading and produced the
same fixed-feature score. This is pipeline evidence only, not wake accuracy,
false-positive exposure, online latency, or production integration evidence.
No household audio was replayed or uploaded and no GPU was rented ($0 reported
Runpod usage). The [training plan](docs/plans/custom-reachy-openwakeword-training.md#execution-evidence--2026-09-07)
records model/source hashes, environment, commands, and pending prerequisites.
User metadata corrected the private screen to two speakers (25/5 clips);
pronunciation variants remain unlabeled and cannot support per-variant claims.

## 14. Accepted phonetic recipe preparation (2026-09-07)

The user accepted synthetic pronunciation samples 1, 2 and 5, rejecting 3 and 4.
The isolated CPU recipe generated 48 positives and 128 negatives, applied two
augmentation rounds, validated 352 feature rows, trained eight steps and
exported ONNX. Fresh Mac/Linux ONNX Runtime 1.27.0 processes produced the same
0.1442318856716156 probe score. PyTorch/export maximum difference was 7.45e-8.
The tiny smoke model had zero synthetic recall and was correctly rejected by
the synthetic streaming gate; no private evaluation followed. Nine external
feature-recovery/provider-stop tests passed with fake provider calls, including
a detached supervisor surviving its launcher. Full app gate: 863 passed, one
optional native-speaker skip. A paid shutdown smoke is in progress; no real
CUDA, provider-stop, full-training or household-acceptance result is claimed.


Latest custom-training outcome (2026-09-07): resolved the missing CUDA library
lookup and verified actual CUDA mel/embedding execution and Torch backward.
Full GPU generation/training completed; all 23,800 synthetic WAV hashes and
model/feature artifacts were verified after download. All Runpod Pods/storage
are deleted: $8.8352740152 remains, $0/hour, approximately $1.16 total spent.
Candidate 1 detected 400/400 synthetic positives but activated on 313/3,168
negatives (9.88%). Local candidate 2 improved this to 397/400 and 106/3,168
(3.35%), still above the unchanged 1% gate. Candidate 3, using 600 additional
accented near-word negatives from training voices only, detected 400/400
positives but activated on 36/3,168 negatives
(1.136%). It also fails the 1% gate. The initial three full attempts are used.
The user authorized a fourth local CPU attempt. It completed with 400/400
positives and 106/3,168 negative activations (3.35%), again failing the 1% gate.
All four attempts are used; candidate 3 remains the best rejected experiment.
No further rental occurred. Review training-voice coverage before proposing
another model; no new recordings are requested while the synthetic gate fails.
Candidates 1, 2 and 4 have not evaluated household recordings; none is integrated.
The current full app gate passed 863 tests with one optional native-speaker
skip (26.09 seconds); context-feature and training split/label checks passed.
Earlier cache/shutdown checks passed ten tests. Prior entries are historical
milestones; see
the custom training plan for current decisions and remaining acceptance gates.


Exploratory private screen (2026-09-07): the user explicitly permitted candidate
3 to bypass the synthetic quality gate for one local screen only. The original
threshold 0.5 and model hash were retained. All 3,568 synthetic Mac/Linux event
counts matched, maximum score difference 1.55e-6; numerical agreement passed
while the synthetic failure stayed recorded. Under macOS OS-denied networking,
the model detected 19/30 private positives (63.3%): 17/25 from speaker 1 and
2/5 from speaker 2, with zero duplicate events. It fails the 27/30 screen.
Surrounding-language counts were EN 6/7, ES 4/7, PT 5/7, IT 3/8, standalone
unknown 1/1; exact pronunciation groups remain unknown, and speaker/language
coverage is confounded. No threshold sweep, negative collection, or integration
followed. Audio stayed local and out of training; private results have mode 0600.

Offline processing: 6.42 seconds for 186.09 seconds of audio (RTF 0.0345),
0.123-second load, 277% CPU relative to one core, 186.0 MiB peak RSS. Zero drops
refers to offline processing, not live queues; acoustic latency, household false
activations per hour and Pi performance remain unmeasured. Synthetic-only
regression checks verified the hash-bound exception, retained numerical gate,
unchanged synthetic failure, private permissions and duplicate-run refusal.

Post-screen repository Ruff, format, mypy and pytest gate passed: 863 passed,
one optional native-speaker skip, 13.00 seconds. No application code changed.


Local miss/hit diagnosis (2026-09-07): user review judged all 11 missed clips
clear and correctly pronounced. Original-WAV measurements were joined to saved
scores locally under network denial, without another private detector run.
Every miss is a name-first phrase: 6/17 detected, versus 12/12 with preceding
speech and 1/1 standalone. Missed clips have active-frame medians about 2.8 dB
quieter, but level ranges strongly overlap; quiet-frame level medians are
-58.68/-58.70 dBFS and no saturation was found. These energy proxies do not
measure name-specific SNR or phonetic clarity. Ten misses scored below 0.1.

A paired synthetic check preserved the same 60 name waveforms and voice-matched
speech, with an 80 ms join: 60/60 alone, 59/60 name-first, 60/60 name-last,
0/60 donor-only controls. Baselines matched saved scores within 1e-5. This
concatenation check does not reproduce the real connected-speech deficit;
natural transitions, voices and acoustics remain possible factors. It supports
reviewing naturally spoken full-phrase coverage before another model, without
establishing a cause. No private audio entered training, no new original
synthetic samples or rental were added, and no model or threshold changed.
Two acoustic-measurement tests passed; detailed limitations and verification
are recorded in the custom training plan.


Name-isolation diagnostic (2026-09-07): user authorized diagnostic replay of the
11 misses with trailing speech removed on temporary copies. Existing offline
Whisper-small supplied approximate boundaries, matched to the next supplied
word; clip 14 needed a prompt from the user's sentence. Boundaries were fixed
before scoring. Original timing, prefix samples, duration, gain and detector
threshold remained unchanged, with the suffix replaced by zeros. The primary
cut detected 3/11 versus 0/11 full sentences. Cuts ±40 ms detected 3/11 and 2/11;
a 5 ms fade detected 4/11. No clip detected in all four isolation variants.
These automatic cuts are not manually verified, so the result does not establish
following speech as the main cause or justify a simple spacing fix. Do not
select favorable cuts or count edited-audio detections as acceptance: the
original result remains 19/30. No training, threshold change or integration.

All full-sentence controls reproduced saved events/scores within 1e-5. Original
WAV and evaluation hashes remain unchanged; temporary audio was deleted and
private diagnostic files have mode 0600. All processing was network-denied.
Two editing-preservation tests passed; repository Ruff/format/mypy/pytest passed
863 tests with one optional native-speaker skip (17.90 seconds). No app code
changed and no new rental, dependency or model download was needed.


### Custom training development target passed — 2026-09-07

Following the authorized ElevenLabs expansion, candidate 18 passed the frozen
development checks at threshold 0.5: 27/30 original recordings, 400/400 older
synthetic positives, and 1/3,168 negative activations (0.032%). Four separate
ElevenLabs validation speakers yielded 178/180 positive detections and 0/60
negative activations. The ordered temporal classifier is selected and training
has stopped. See the [training plan](docs/plans/custom-reachy-openwakeword-training.md)
for all recipes, failures and artifact hashes.

Original recordings remain unchanged, outside training, and network-denied
during evaluation. Repeated use makes them development data; fresh household
positive and long-negative acceptance remains required. The short synthetic
negative fraction is not a real-world hourly false-wake rate. No app model
is installed, and no new hardware/simulator/full candidate platform-equivalence
claim is made. Repository gate: 863 passed, one optional skip, 19.55 seconds;
external feature/export/streaming checks also passed. Since Starter upgrade:
900 ElevenLabs and 150 local Italian originals added; $0 additional Runpod
spend, $8.7798144967 balance, $0/hour and no pods/volumes. ElevenLabs has
16,061 credits remaining.


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

2026-09-08: private wake candidate23 focused-negative refinement rejected.
Training fit80/80; synthetic positives unchanged, tempo false activations1/72,
older false activations25/3168 versus21's23. Prefer21 for investigation only;
no fresh acceptance or runtime change. Docker verified after user restart,
local network isolation, zero provider spending. Full gate863 passed,1 skipped.

2026-09-08: candidate24 passes fixed synthetic gates after harder training-side
negative mining. Old false activations14/3168 (21:23); tempo0/72, connected0/24,
diverse0/60. Positives unchanged865/868; household80/80 training fit, not fresh
acceptance. Freeze24 at0.5 for new household sessions; not enabled. Local/private,
zero provider spending; full gate863 passed,1 skipped. See custom training plan.

2026-09-08: frozen wake candidate24 fresh Eduardo Spanish batch9/10, zero
duplicates; clip02 miss. Positive acceptance unmet; lunch20m56s audited but
unscored. New batches remain private/evaluation-only; no runtime change.

2026-09-08: candidate24 fresh miss is timing-sensitive:40ms silence before
unchanged clip02 rescues it, as do tempo0.8/0.9; gain2/4 fails. Diagnostic-only
transformed batch10/10 does not supersede original9/10. No runtime change.

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

Parallel35 diagnostic completed (2026-09-09), existing20 latest calls and49.50min
conversation only. All individual detector counts reproduced their archived
results. Event-level OR with a shared3s cooldown: official33+official34 catches
17/20 with8 false events; official33+baseline27+verifier32 catches19/20 with4
false events; official33+raw baseline27 catches19/20 with15 false events. Here
"+verifier32" filters only baseline27 events; official33 remains an independent
branch. The19/20 pair yields Eduardo10/10, Adriana9/10 (still misses clip04),
Spanish7/8 and EN/PT/IT4/4 each. No positive duplicate events for any pair.
Thus complementary detectors recover calls but increase false wakes: official33
alone17/20 and1 false event, baseline27+verifier32 alone18/20 and3 false events.
No runtime integration, training, threshold sweep or paid action. This diagnostic
is not full-corpus acceptance or hardware/latency validation. Receipts and sample
timings: private parallel35/{protocol,official33,official34,verifier32,results}.json.
Merger edge assertions passed; full gate Ruff/format/mypy passed, pytest863 passed
and1 skipped in12.89s; git diff --check passed.


2026-09-09 microphone integration check: media-enabled headless SDK capture fed
472 nonzero microphone frames into the actual acoustic adapter with a fake
provider, zero ambient cloud bytes and no persisted audio. Human-spoken wake and
graphical conversation acceptance remain pending.


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
