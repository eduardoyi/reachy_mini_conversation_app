# Custom Multilingual Reachy openWakeWord Training — Living Plan

> Current experiment (2026-09-08): the user explicitly authorized local training on the 80 existing usable household recordings. Those sources are training-only now; fresh independent validation is required. Household-derived features/models stay private and outside cloud-training mounts. Earlier evaluation-only statements below describe the prior protocol.

- Status: `Official33 supervised headless MVP accepted; training paused`
- Owner: Reachy project
- Created: 2026-09-07
- Last updated: 2026-09-10
- Scope: Phase 4 acoustic wake-word follow-up
- Related plan: [`reachy-hybrid-household-companion.md`](reachy-hybrid-household-companion.md)

Current retained candidates and exact private artifact hashes are summarized in
[the wake model catalog](reachy-wake-model-catalog.md). Training is paused;
official33 is available only through an explicit supervised-trial opt-in.

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


## Goal

Train and evaluate a local openWakeWord detector for the household's
pronunciations of “Reachy”, “Richie”, “Ritchie”, “Richi”, and “Ricci”. All
variants activate the same internal `reachy` event. The first training attempt used user-reviewed synthetic pronunciations and a
rented Nvidia GPU. Later iterations use ElevenLabs and local synthetic speech
with local classifier training. The 30 real household recordings remain
unchanged and excluded from training, but repeated evaluation makes them a
two-speaker development benchmark, not independent household acceptance.

The detector must consume the app's existing normalized 16 kHz audio stream. It
must not open a second microphone, persist ambient audio, or send preactivation
audio to the cloud.

## Current evidence and decisions

- Selected result (2026-09-07): **candidate 18 passes the development training
  target** at threshold 0.5: 27/30 original recordings, all 400 older synthetic
  positives, and 1/3,168 old negative activations (0.032%). The new-speaker check
  detected 178/180 positives with 0/60 negative activations. Training stopped.
  The original recordings are development data because of repeated evaluation;
  fresh household acceptance and long negative listening remain pending.
  No detector is installed. ElevenLabs has 16,061 credits remaining; Runpod
  has $8.7798144967, $0/hour, no pods or volumes. No new rental was needed.

- Earlier outcome (2026-09-07): the user authorized an exploratory synthetic-gate
  exception for candidate 3. Its frozen Mac/Linux synthetic detections matched;
  the one-time, network-denied private screen detected 19/30 (63.3%), below
  the required 27/30. Speaker results: 17/25 and 2/5. Candidate 3 is rejected
  for household use; no threshold sweep, negative collection or app integration.
  Four full training attempts are used. All rental resources were deleted;
  about $1.16 was spent, and the local screen added no provider cost.

- The 30 private Mac-microphone positive recordings are technically valid and
  remain outside the repository. They are a two-speaker initial
  evaluation screen, not training data or final household acceptance.
- The reviewed Spanish sherpa checkpoint detected 0/30 at documented settings
  and 2/30 only with an overly aggressive setting; it was rejected before
  negative testing.
- The existing community Reachy openWakeWord model detected 6/30 at its
  documented threshold and was also rejected before negative testing; the
  earlier 7/30 threshold sweep is exploratory rejection evidence only, not a
  tuned holdout result. A community report of Mac/Linux scoring differences
  prompted the completed reference preflight below: all 668 public/synthetic
  scores matched on Mac ARM64 and local Linux ARM64. This does not establish
  the cause of household misses or validate Linux x86/GPU inference.
- No household recording may be uploaded to a GPU provider, model trainer, or
  external benchmark.
- The initial rented-GPU budget is capped at **$10**. Stop the instance and ask
  for approval before exceeding it.
- The 30 clips now have a user-approved private backup outside the repository,
  verified by SHA-256 and made read-only. Speaker and surrounding-language labels
  were recovered from user metadata. Exact pronunciation groups remain unknown
  and cannot support per-variant claims; `/tmp` is not the durable copy.
- No model, training dependency, or production adapter has been added to the app.
  Integration starts only after the acceptance gates below pass.

## Training reference and adoption boundary

Use [saintpete/openwakeword-colab-toolkit](https://github.com/saintpete/openwakeword-colab-toolkit)
as a reference for the isolated Runpod training environment. Review and pin its
commit and file hashes before reuse. The reviewed sources are the
[notebook](https://github.com/saintpete/openwakeword-colab-toolkit/blob/main/hey_clem_colab.ipynb),
[configuration](https://github.com/saintpete/openwakeword-colab-toolkit/blob/main/hey_clem.yaml),
[compatibility shim](https://github.com/saintpete/openwakeword-colab-toolkit/blob/main/oww_train_shim.py),
[evaluator](https://github.com/saintpete/openwakeword-colab-toolkit/blob/main/check_model.py),
and [findings](https://github.com/saintpete/openwakeword-colab-toolkit/blob/main/FINDINGS.md).
Reference checkout pinned to `2e7379fd0980e83332d637c2f2b7496de48b2b41`;
its notebook and shim have not been executed. The local preflight uses the
previously downloaded openWakeWord fork at
`2631663f1fff1274ca01a5ee33db877a37b83f41` to check the existing inference stack.

The author reports 91% recall and zero false activations across 75 minutes on a
Reachy Mini, plus unexpectedly low Mac scores compared with Linux. Both are
external reports, not reproduced project evidence. The toolkit provides useful
modern-Python dependency, GPU feature-extraction, and ONNX-export guidance; it
does not establish multilingual accuracy or explain our previous misses.

Its defaults use English Piper speech and 75,000 training positives plus 7,500
validation positives. Do not copy those defaults into the bounded experiment.
Its threshold sweep/recommendation is a development tool, not our holdout
acceptance procedure. Its Unlicense declaration covers its own code only;
voices, models, and datasets still require separate review. Keep compatibility
changes in the isolated trainer; do not patch the Reachy SDK or application.
Prefer compatible pinned dependencies over global monkey-patches. Review any
necessary shim and trusted checkpoint hash before allowing pickle-based loading;
do not copy its global `torch.load(weights_only=False)` override blindly.

## Assumptions

- Synthetic speech can provide enough multilingual pronunciation diversity for
  a first model, but real-household results decide whether it is usable.
- Existing openWakeWord training tooling can produce a compatible ONNX model;
  the exact command and model format must be verified before spending GPU time.
- Candidate voices, speech/noise corpora, openWakeWord code, and generated
  artifacts will each have recorded source, version/hash, and license terms.
- The feature extractor, base models, TTS engines, and all training assets are
  separate licensing surfaces and must each be audited.
- A single multilingual model is the first experiment. Separate phonetic
  models are allowed only if per-variant results show that one model is the
  limiting factor and the added runtime complexity is measured and justified.

## Non-goals

- Do not train on the 30 household clips or upload them anywhere.
- Do not add wake-word code, dependencies, model files, or app configuration
  during the planning step.
- Do not replace the existing transcript detector, keyboard fallback, VAD,
  ambient STT, or Gemini provider before acoustic acceptance.
- Do not claim false-positive quality, online latency, Mac resource use, or
  Raspberry Pi feasibility from synthetic data or fake tests.
- Do not publish or redistribute a model until every code, voice, corpus, and
  generated-artifact license has been reviewed for the intended use.

## Implementation order

### 0. Freeze the evaluation boundary

The approved private backup is complete and verified. Before training, retain
the corrected private metadata: clips 1–25 are speaker 1 and 26–30 speaker 2.
The user-provided utterance order establishes surrounding-language counts of
7 English, 7 Spanish, 7 Portuguese, 8 Italian, and one standalone name with
unknown language; it does not establish
name pronunciation. Session grouping remains provisional. Unknown variants stay
unknown, are reported explicitly in the initial aggregate screen, and must be
covered by labeled recordings in final acceptance. Do not infer pronunciation
from filenames or the spelling in the utterance list.
Record only in that private manifest
the SHA-256, sample rate, channels, duration, variant, language, speaker,
session, and recording condition for every clip. Keep the backup read-only and
never put its path or audio in public docs. `/tmp` may be a working copy only.

The 30 clips are a two-speaker initial screen. Do not tune a
threshold, cooldown, augmentation, or model on them. Pre-register threshold,
score window, cooldown, and the negative-test decision rule using synthetic
development/validation data before opening the private fixture tier. Any
earlier threshold sweep on these clips is exploratory rejection evidence only,
not a tuning or acceptance result.

### 1. Clear provenance and licensing

Choose only synthetic voices and positive/negative corpora whose terms allow
the intended private use and any future distribution. Audit the openWakeWord
code, feature extractor, base/pretrained models, TTS engines/voices, noise and
speech corpora, augmentation tools, and generated artifacts separately. Record
the exact voice, corpus, tool, revision, license, and intended-use decision in a
metadata manifest. Stop if a required license is unclear. Do not assume an
Apache code tag covers MLS, MUSAN, Piper, a feature extractor, or model-
training data.

### 1a. Check inference before renting a GPU

Use only licensed public or synthetic fixtures and a known-good reference model
for a no-cost Mac/Linux comparison. Run Linux locally if available; if not,
record the unavailable check and arrange a no-cost Linux environment before
GPU rental. Never upload or replay the 30 private clips for this diagnostic.

Pin identical wake-model, melspectrogram, embedding, and fixture hashes. Compare
CPU ONNX inference first with matched package versions where available; record
architecture/version differences explicitly. Use identical mono 16 kHz PCM16,
1,280-sample (80 ms) chunks, scaling, padding, state resets, and score windows.
Pre-register a numerical score tolerance and fixed reference detection threshold
before comparing per-frame scores and detection outcomes. Include known positive,
negative, silence, and reset/repeat fixtures; a pair of all-zero outputs is not
a successful preflight. Keep detailed outputs limited to public/synthetic data.

If results diverge, isolate decoding, preprocessing, feature extraction, and
classifier output before changing training data. Record the cause or unresolved
boundary and do not start paid training until the intended Mac inference path
passes the reference checks. Linux success cannot replace Mac acceptance.
Preserve previous rejection results; any later private re-evaluation after a
proven inference fix needs a documented protocol decision and must not become
another threshold sweep.

### 2. Generate a synthetic multilingual corpus

The user accepted preview sounds 1, 2, and 5: English Reachy, English
Richie/Ritchie, and Italian Ricci. They rejected Spanish/Portuguese previews
3 and 4. Use those three accepted phonetic targets with multiple voices, rates,
volumes, silences, room impulse responses, distances, and household-like
background noise. Generate hard negatives such as ordinary conversation,
“Ricky”, “really”, “recipe”, and “reaching”, with at most 20,000 generated
hard-negative examples in the initial milestone. Keep a manifest with stable
sample IDs, labels, language/voice metadata, hashes, and source licenses; any
large precomputed feature corpus has its own manifest and remains inside the
same 20 GB total storage cap.

Before bulk generation, inspect a small synthetic pronunciation sample for each
intended language and variant using selected voices and phoneme/duration tools.
The toolkit's English generator does not become multilingual by accepting foreign
spellings. Record the voice/language mapping and pronunciation decisions. Short
names may be harder to separate from ordinary speech; keep the requested bare
Reachy-family names as targets. Adding “hey” or “hello” is a separately approved
product change, not an automatic workaround.

The first recipe uses LibriTTS speaker IDs 0–79 for training and 80–99
for validation; the Italian Riccardo voice is validation-only. The three
accepted phoneme sequences are generated across training voices. This is
phonetic training, not evidence of native Spanish/Portuguese TTS coverage;
household language acceptance still requires every intended language.

Keep speakers split between training and validation.
Never use the 30 real clips for tuning, augmentation, threshold selection, or
training. Record all RNG seeds and the exact TTS, dependency, GPU, export, and
ONNX/runtime versions.

### 3. Run a bounded first training experiment

First complete a no-cost CPU/dry-run of the data manifest, command, export, and
model-load path. Then use one reproducible GPU run with a fixed seed and a
documented configuration. The initial milestone is bounded cumulatively across
all three attempts to at most six GPU hours, 5,000 synthetic positive samples
per phonetic model, 100,000 steps, 20 GB provider storage, and 5 GB egress.
The positive-sample allowance includes training and synthetic validation clips
and is shared across target spellings, not multiplied by spelling or language.
Record original clip counts and augmentation rounds separately. Inventory peak
storage, including archives, extracted corpora, feature arrays, caches, container
and volume disks; the toolkit's large negative-feature download is not exempt
from the 20 GB total. Stop for approval if a useful recipe cannot fit these limits.

Use one Runpod On-Demand GPU Pod, with one 24 GB RTX 4090 as the initial hardware
choice. Confirm the live price and pin the image digest, Python, PyTorch, CUDA,
ONNX Runtime, openWakeWord, TTS revisions, and explicit dependency lock before
provisioning. Adapt Colab paths and download cells to the Pod; no Colab purchase
is needed. Verify actual CUDA execution for both training and ONNX feature
extraction with a small real operation. Stop on CPU fallback rather than using
the remaining budget on an unexpectedly slow run.

Before provisioning, document and test budget enforcement. Runpod's hourly
account limit is not a $10 job cap. Verify auto-pay is disabled, account/resource
charges are understood, and prepaid funds provide a bounded backstop. Use a
verified provider hard cap if available; otherwise require documented evidence
that the prepaid backstop plus automatic stop enforce the same $10 ceiling.
Do not claim a per-job hard cap without verification. Schedule a Pod stop using
the remaining cumulative six-hour allowance and a cost deadline with a storage
margin below $10, whichever comes first. The stop must survive training failure
or SSH disconnection; test timeout/error paths with fakes locally, then verify
the real stop mechanism during the first bounded paid smoke run. A process exit
is not a Pod stop. Do not start full training without that evidence. Download and
verify outputs before termination; remove experiment storage when finished to
end storage charges. Ask for approval before expanding any limit. Record wall
time, actual spend, GPU type, driver/runtime, and all versions.

The CPU micro-run must exercise generation, augmentation, feature extraction,
training, export, and load/inference on synthetic fixtures. Validate feature
array shapes, expected counts, and completion metadata before reuse; mere file
existence is insufficient after an interrupted run. Test interruption/resume
without accepting partial arrays. Verify ONNX external weight sidecars are
included and hashed, or fold them into one file and verify equivalent scores.
Do not ignore a final conversion traceback: distinguish an optional TFLite
failure from ONNX failure and accept the export only after independent ONNX
validation and successful inference in a clean runtime.

Save only the resulting model and non-sensitive metadata outside this repository
until licensing and quality gates pass.

If the first model cannot separate the variants, compare separate phonetic
models (English-style Reachy/Richie/Ritchie; Spanish/Portuguese-style Richi;
Italian-style Ricci) behind one local dispatcher. This is an experiment, not
permission to add three always-running audio pipelines.

### 4. Screen the initial positive holdout

After the inference preflight and synthetic development settings are frozen,
run the real 30-clip set locally once at the pre-registered threshold. Report
aggregate and per-variant recall, score/threshold, duplicate detections, drops,
real-time factor, CPU, peak RSS, model load time, and model hash. Keep raw audio
and transcript-like payloads out of logs and reports.

Offline WAV processing may report only `fixture_start_to_result`, which is file
processing elapsed time. It is not acoustic onset-to-detection or online
activation latency; do not infer onset from an unannotated WAV.

The candidate must detect at least **27/30** intended wakes before any negative
recording is requested. If it fails, stop and tune/retrain or reject it; do not
spend time on the long negative run.

### 5. Run final multi-speaker positive acceptance

The 30-clip screen is not the final household gate. Collect a separate private
positive set from every available household speaker, across at least three
sessions per speaker, with at least ten labeled utterances for each applicable
variant and represented language/pronunciation stratum per speaker. If fewer
than three speakers are available, test every available speaker and record that
limitation; never silently omit a variant, speaker, language, or pronunciation
stratum. An unrepresented stratum is `not tested`, never a pass.

Final positive acceptance requires every listed variant to be tested, at least
90% recall for each variant, at least 90% recall for each speaker, at least 90%
recall for each represented language/pronunciation stratum, and at least 95%
aggregate recall across speakers and sessions. Report each variant, speaker,
language, and pronunciation stratum separately, along with the number of
sessions and misses; aggregate recall cannot hide a failing stratum.
Do not call the result fully multilingual when an intended household language
is unrepresented; that language remains `not tested`.

### 6. Measure false activations

Only after the final positive gate passes, record the two-minute ordinary-speech/
near-miss smoke test locally. If it is clean, run at least 30 minutes of
ordinary household speech, silence, music, and near-misses. Report exposure
duration, false activations per hour, exact test conditions, and whether any
activation was a repeated/self-trigger event. The provisional safety target is
zero false activations in the two-minute smoke test and no more than one per
hour in the longer exposure; confirm or revise this target before the run.
Report observed rates, not a statistically established long-term guarantee.
The toolkit's one-hour minimum is useful context for extending exposure, but
its threshold recommendation and weaker positive-recall target do not replace
our gates. Use the frozen threshold and cooldown, distinguish raw score crossings
from actual activation events, and never tune on final acceptance recordings.

### 7. Check runtime fit before integration

Benchmark one detector owner on the Mac using the existing shared stream and
measure queue drops, p50/p95 onset-to-detection latency from annotated live
audio, CPU, RSS, and shutdown behavior. Keep this online timing separate from
offline fixture-start-to-result. Test on a Raspberry Pi 5 8GB only when that
hardware is available; do not infer Pi performance from Mac numbers. Verify
that LOCAL_AMBIENT keeps cloud audio/video counters at zero.

### 8. Integrate only an accepted model

Add a replaceable detector adapter that consumes the existing stream and emits
only metadata (`wake_detected`, score, model ID, timing). Preserve keyboard
activation, transcript fallback, exact local-command routing, cooldown/debounce,
and the existing privacy state machine. Add integration and simulator tests
before enabling it by default.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Synthetic voices do not match household accents | Keep real clips untouched; use multiple languages/voices/noise and reject on the 27/30 holdout gate. |
| One model harms one pronunciation | Report per-variant recall; test separate phonetic models only with measured justification. |
| False activations make Reachy annoying | Use hard negatives, a cooldown, an initial positive screen, a final positive gate, then negative exposure, plus the provisional false-positive target. |
| Training or voice licenses block distribution | Keep a provenance manifest and require terms review before bundling or publishing. |
| GPU spend grows during tuning | CPU/dry-run first, hard provider auto-stop, six-hour/three-attempt/sample/step/storage/egress limits, and a $10 initial cap; stop and request approval before expanding. |
| A detector creates a second capture path or leaks ambient audio | Reuse the existing stream; assert one owner, zero preactivation cloud bytes, no files, and clean cancellation. |
| A model works offline but not online | Measure onset latency, queue pressure, drops, and native/shared-stream behavior before integration. |
| `/tmp` loss or accidental disclosure of household recordings | Require a user-approved private backup and metadata manifest before training; public docs contain counts/hashes only. |

## Execution evidence — 2026-09-07

- **Private metadata corrected:** the user identified two speakers (25 and 5
  clips). Surrounding utterance languages are known except for the standalone
  name; pronunciation variants and independently verified session labels remain
  unknown. A corrected private sidecar supersedes the original manifest's
  one-speaker assumption; original audio and read-only backup remain unchanged.
- **Reference preflight passed:** identical source/model/fixture hashes,
  Python 3.12.14, ONNX Runtime 1.27.0, NumPy 2.4.2, SciPy 1.17.0, and
  scikit-learn 1.8.0 on Mac ARM64 and local Docker Linux ARM64. Six fixture
  runs across two models produced 668 scores with maximum absolute difference
  **0.0**, inside the pre-registered absolute/relative tolerance of 0.0001.
  Both expected phrases detected at 0.5; unrelated speech, silence, and seeded
  noise stayed below 0.5. Reset/repeat results matched. This is a small
  reference check, not household acceptance or Linux x86/GPU evidence.
- **English CPU pipeline smoke passed:** Piper 1.8.0 with LibriTTS-high generated
  12 clips (6 positive, 6 negative), using two training voices and a separate
  validation voice. Upstream augmentation, feature extraction, eight training
  steps, and ONNX export ran with PyTorch/torchaudio 2.5.1, NumPy 1.26.4,
  SciPy 1.15.3, ONNX 1.17.0, and ONNX Runtime 1.22.1. `libgomp1` was required
  in the container and setuptools was pinned to 80.9.0 for the old dependency
  imports; no global compatibility shim was needed. Piper is GPL-3.0-or-later
  and the voice corpus is CC BY 4.0; use is isolated and local, with no
  redistribution. No dependencies were added to the app.
- **Export passed independent loading:** 203,311-byte single-file ONNX,
  SHA-256 `c70f81ca26b7fbaf5ee829a423c513980864680f47117e7533b4c59400cb9c51`,
  passed ONNX checking and matched the PyTorch probe exactly. Fresh Mac/Linux
  inference-only processes loaded it through ONNX Runtime 1.27.0 and the
  openWakeWord streaming API. Their fixed-feature score matched exactly.
  This file is labeled pipeline-smoke-only, is not an accepted Reachy model,
  and has not been run on the private recordings.
- **Budget before the paid shutdown smoke:** zero rented GPU hours and
  $0 reported Runpod usage; no Pods at that checkpoint.
  Conservatively count the smoke's six positives, six negatives, and eight
  steps against the cumulative experiment limits. CLI 2.12.0 was installed
  from the official release with checksum verification. After user setup,
  `runpodctl user` authenticated and confirmed a $10 balance and $0/hour
  current spend. The user confirmed automatic credit top-up is disabled.
  This verifies account setup, not the still-pending automatic Pod stop.
- **Still pending:** CUDA feature/pipeline checks and full candidate training. Provider shutdown was subsequently verified below.
  The latest user-reviewed recipe and local safeguards are recorded below. The default negative-feature file is 17,280,000,128 bytes before
  the 184,836,608-byte validation set, archives, environment, or generated clips;
  it was not downloaded. English smoke success does not satisfy the complete
  multilingual dry-run gate. No GPU may start on this evidence alone.

Local scripts and machine-readable evidence remain in the approved external
experiment directory. `run_preflight.py` consumes only the copied public
reference fixtures and procedural silence/noise. `cpu_smoke.py` generates
synthetic clips; `check_export.py` loads only the smoke model/features.
No private recording folder is mounted into Docker. Inference, training smoke,
and clean-export checks ran with Docker networking disabled; dependency/model
setup was a separate network-enabled step. The full dependency lock, source
hashes, image definition, protocol, provenance decisions, and result JSON are
retained alongside the scripts. The base image is
`python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`.

Validation performed:

- Native reference command: existing ARM Python environment running
  `run_preflight.py`; Linux command: `/work/linux-venv/bin/python
  /work/run_preflight.py` in the pinned image. JSON score comparison passed.
- CPU command: `/work/training-venv/bin/python /work/cpu_smoke.py` in the
  local image with libgomp1/libsndfile1/espeak-ng. Clean export command:
  `check_export.py` in separate native and Linux inference environments.
- `.venv/bin/pytest tests/test_wake_word.py tests/test_sherpa_wake_benchmark.py -q`:
  **100 passed**.
- `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
  `.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -v`:
  **passed**, 863 tests passed and one optional native-speaker subprocess test
  skipped. No new native/simulator, hardware, cloud, latency, or household
  acoustic acceptance was performed.

## Accepted recipe and safeguards — 2026-09-07

- User pronunciation review accepted preview sounds **1, 2, and 5**, rejected
  3 and 4. Direct spelling input produced unintended phonemes, so the recipe
  now uses explicit accepted sound sequences. This supersedes the original
  assumption that each spelling should be read natively by every TTS language.
- Candidate recipe: 4,800 positives, 19,000 synthetic negatives, two augmentation
  rounds, 16,000 steps, seed 20260909, threshold 0.5, 80 ms chunks and a
  three-second cooldown. Positive spellings remain aliases of one wake event.
  Procedural noise/reverberation and generated ordinary/near-miss speech avoid
  the oversized external negative-feature download. Synthetic negatives do not
  establish real household false-positive performance.
- Only LibriTTS-high and Riccardo are used by the candidate recipe. Their source
  terms are CC BY 4.0 and the original M-AILABS permissive data license. The
  rejected preview voices are excluded. Retain notices; do not distribute any
  artifact before the separate publication review. Piper stays an isolated
  GPL-3.0-or-later executable, outside app dependencies.
- CPU candidate smoke passed generation, augmentation, `(352, 16, 96)` feature
  extraction, eight training steps and checked single-file ONNX export. It used
  48 positives and 128 negatives. Export/PyTorch maximum difference was
  7.45e-8 after the final cache/source-hash check. Eight steps yielded zero synthetic positive recall, so this is
  pipeline evidence only, never a quality pass.
- Nine local guard tests pass: partial/corrupt/stale feature rejection, successful
  regeneration, provider-stop retry/timeouts/exhaustion, job launch failure,
  job failure, deadline termination, and detached supervision surviving its
  launcher exit. Provider calls are fakes; real Runpod shutdown remains untested.
  The supervisor is an external POSIX Pod tool, not cross-platform app code.
- Runpod's documented prepaid backstop stops Pods when less than ten seconds of
  funds remain; storage may still bill. Auto-pay is user-confirmed disabled.
  The job supervisor must stop earlier on the absolute runtime/cost deadline
  and on job completion/failure. Terminate storage after verified download.
  Source: [Runpod billing](https://docs.runpod.io/accounts-billing/billing).
- Current full app gate: Ruff/format/mypy passed, **863 passed, one skipped**
  (`pytest tests/ -v`, 14.06 seconds). No runtime integration was added.

The pinned GPU image is `pytorch/pytorch@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755`: Python 3.11.10, PyTorch 2.5.1+cu124, 6,136,636,922 image bytes. Its isolated dependency resolution passed. This differs from the ARM CPU smoke Python 3.12; real GPU import/execution is still required.

A first paid RTX 4090 shutdown smoke was provisioned at 16:05 UTC with 15 GB container disk, 5 GB volume, and an initial ten-minute absolute cutoff. The catalog GPU rate was $0.74/hour; account rate including disk was $0.743/hour. Image download was still pending at the latest check; the startup allowance was extended to 30 minutes (under $0.38) after observing slow transfer. Both the Pod deadline and secondary local cutoff were updated. No CUDA success or automatic-stop pass is claimed yet.

## Tests and evidence

The training work must ship its tests and documentation with the eventual
implementation. Applicable coverage includes:

- manifest/schema tests for labels, languages, stable IDs, hashes, split
  isolation, license fields, technical metadata, and no household-file paths;
- deterministic seed/config tests and model-output/hash recording;
- matched public/synthetic Mac/Linux inference fixtures and positive-reference
  detection, with explicit tolerances, state resets, and preprocessing checks;
- CPU micro-run, verified GPU execution, complete feature-array validation,
  interrupted generation/augmentation recovery, ONNX sidecar packaging and
  clean-runtime export/load checks;
- cumulative sample/storage/time/cost accounting and automatic Pod stop on
  deadline, training failure, and SSH disconnection, with real smoke evidence;
- real-model positive holdout tests using all 30 initial clips, including every
  spelling and near-duplicate handling, without threshold tuning;
- final private multi-speaker/multi-session tests with an explicit result for
  every variant, speaker, language, and pronunciation stratum;
- negative exposure tests with explicit duration and false-positive/hour
  accounting;
- threshold, cooldown, duplicate-result, malformed-audio, empty-input,
  cancellation, timeout, queue-overload, and shutdown tests;
- a shared-stream integration test proving one microphone owner, no raw-audio
  persistence, no transcript/audio logging, and zero ambient cloud bytes;
- Mac native/simulator smoke coverage for model loading, activation,
  interruption, and clean process shutdown; Pi measurements only when hardware
  exists.

Keep three tiers separate: (1) hermetic CI, which uses fakes and no credentials,
model files, private audio, or network; (2) an explicit opt-in private-fixture
tier that runs only on the user's machine and loudly skips when its approved
manifest/model is absent; and (3) an optional native/simulator child-process
tier that loudly skips when Reachy/GStreamer dependencies are unavailable and
reports timeout, signal, stderr, and cleanup evidence. A skipped private or
native tier must never become a green fake result.

The existing deterministic benchmark remains useful for model-contract tests,
but it cannot substitute for the real 30-positive and 30-minute acoustic
evidence.

## Acceptance criteria

The custom model work is accepted only when all of these are true:

1. Provenance, licenses, hashes, seed, configuration, cost, and environment
   are recorded; the $10 initial cap was respected or explicit approval is
   recorded. Reference inference, CPU micro-run, complete export/load, GPU
   execution, and automatic-stop checks have recorded passing evidence.
2. The two-speaker initial 30-clip screen reaches at least 27/30 at
   the pre-registered threshold, with per-speaker and available surrounding-
   language results. Unknown pronunciation groups are reported as unknown,
   not per-variant passes. Final acceptance still requires every variant below.
3. The final multi-speaker, multi-session set tests every listed variant and
   every represented language/pronunciation stratum, reaches at least 90%
   recall for each variant, each speaker, and each represented stratum, and at
   least 95% overall. It reports unrepresented strata explicitly as not tested.
4. The two-minute negative smoke test is clean, followed by at least 30 minutes
   of negative/ordinary exposure with false-positive/hour reported against the
   agreed target.
5. Mac shared-stream integration shows one capture owner, bounded resources,
   clean cancellation, and zero preactivation cloud audio/video.
6. Any Raspberry Pi 5 claim is backed by a real measurement on that device.
7. The adapter, tests, feature docs, benchmark report, and both living plans
   are updated; the full repository gate passes.

## Documentation updates

- Keep this plan and the plans index current after each milestone.
- Update the Phase 4 section of the main hybrid plan with decisions, evidence,
  blockers, and dated progress.
- Update [`docs/features/wake-word.md`](../features/wake-word.md) when a model
  contract or runtime procedure changes.
- Update [`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md) with measured
  results only; distinguish offline fixture processing from online acoustic
  latency.
- Update `README.md`, `.env.example`, and the architecture source only if the
  accepted integration changes user-facing setup, configuration, or data flow.

## Progress log

| Date | Status/evidence |
|------|-----------------|
| 2026-09-07 | Plan created. Thirty private positive recordings are valid and reserved as an untouched one-speaker/one-session initial screen. Spanish sherpa scored 0/30 at documented settings and 2/30 aggressively; the existing community Reachy openWakeWord model scored 6/30 at its documented threshold. Both were rejected before negative testing. No custom training, upload, dependency, model, or app integration has started. |
| 2026-09-07 | Reviewer hardening added: no private-clip threshold tuning; durable user-approved backup/metadata manifest; bounded CPU/GPU budget; explicit license audit for feature extractors/base models/assets; final multi-speaker/multi-session gate; separate offline/online timing; and hermetic, opt-in private, and optional native test tiers. |
| 2026-09-07 | Final review added per-speaker and per-language/pronunciation-stratum recall gates, cumulative initial GPU/sample/storage/egress limits, and a bounded synthetic hard-negative corpus. |
| 2026-09-07 | Private backup completed at the user-approved location outside the repository: 30/30 SHA-256 matches, valid mono 16 kHz PCM16 WAV headers, 186.09 seconds total, read-only clips and manifest. No playback, model evaluation, or upload occurred. Per-clip variant/language and detailed recording conditions were not available and remain explicitly unknown. Runpod MCP read-only checks succeeded: no Pods, zero reported daily usage, RTX 4090 catalog availability; user reports $10 funding. No compute was created. Next: complete missing labels, provenance/license review, synthetic-data evaluation settings, CPU dry run, and tested spend/automatic-stop controls. Backup validation used a local Python standard-library copy/hash/WAV-header/permission check; application tests and the full gate were not run for this backup/documentation-only milestone. |
| 2026-09-07 | Adopted the saintpete toolkit as a reviewed training reference, pending revision pinning and execution. Added public/synthetic Mac/Linux inference preflight before rental, isolated dependency/shim review, pronunciation checks, explicit sample/disk accounting, Runpod stop verification, interrupted-feature recovery, and ONNX sidecar validation. Existing privacy, $10/six-hour limits, bare-name targets, and acoustic acceptance gates remain. Next action: complete labels/license review and run the no-cost inference preflight. Documentation-only update; no GPU, downloads, training, or private evaluation performed. Validation: `git diff --check` and a Python check of local Markdown links, preflight ordering, and retained acceptance gates passed. Application tests/full gate not run for this plan-only change; required before implementation handoff. |
| 2026-09-07 | Completed no-cost Mac/Linux ARM64 reference comparison (668 identical scores), isolated English CPU generation/augmentation/training/export smoke, and clean Mac/Linux export loading. Corrected private metadata to two speakers based on user clarification; exact variants remain unknown. Toolkit and runtime artifacts are pinned outside the app. Full gate: 863 passed, one native-speaker skip; focused wake suites: 100 passed. Paid training remains pending voice/corpus review, complete recipe/recovery tests, CLI authentication, and verified budget/stop controls. |

## Changelog

| Date | Change |
|------|--------|
| 2026-09-07 | Created the approved living plan for a privacy-preserving, multilingual custom openWakeWord experiment with a $10 initial GPU cap and explicit positive/negative acceptance gates. |
| 2026-09-07 | Clarified the initial versus final acoustic gates, durable private-data handling, reproducibility metadata, spend/compute bounds, timing semantics, licensing audit, and test-tier boundaries. |
| 2026-09-07 | Added toolkit adoption boundaries and preflight/training verification requirements; synchronized the parent plan and index. |

| 2026-09-07 | CLI authentication now succeeds: $10 prepaid balance, $0/hour current spend. User confirmed automatic top-up is disabled. No Pod provisioned; automatic-stop verification and the remaining training-recipe gates are still pending. |
| 2026-09-07 | Resolved the Riccardo voice source-corpus terms from the original M-AILABS maintainer: permissive use with attribution/notice retention and no endorsement. Recorded the pinned source externally. Other multilingual asset and recipe checks remain pending. Documentation validation: `git diff --check` passed; application gate not repeated for this metadata-only update. |

## Paid shutdown verification — 2026-09-07

CUDA forward/backward executed on the RTX 4090. The new 2.12.0 CLI inside the
Pod failed to stop it; a scoped REST read returned HTTP 403. The preinstalled
Pod-scoped CLI **1.14.15-dac76ad** successfully ran `runpodctl stop pod` five
seconds after SSH disconnected, and the control plane reported **EXITED**.
The supervisor now uses that verified legacy command; ten local guard tests
pass, including its command routing. The new CLI remains the local control tool.

The account balance after this test was **$9.7899293519**, with **$0.001/hour**
stopped storage. The same Pod was restarted for dependency setup and CUDA
feature/pipeline verification. Both cutoffs use the original provision time
plus six hours, including startup and stopped time conservatively; the job also
stops on completion/error. Full candidate training has not started yet.

## Replacement GPU setup — 2026-09-07

The first stopped host lost GPU capacity before setup could finish. Its Pod was
terminated, then a replacement stopped on a setup error and also lost restart
capacity. Both provider volumes were removed; local recipes and prior evidence
remain. A third Pod completed dependency installation and `pip check`, then exited
during the GPU verification stage. Its stopped host also refused restart. The original 22:05:39 UTC cutoff remains unchanged.
No full candidate or private-holdout evaluation has started.

GPU setup uses a locally downloaded, hash-checked allowlist of 24 wheels
(416,828,185 bytes), ONNX Runtime GPU 1.22.0, and Ninja 1.13.2 to repair
malformed wheel metadata in the base image. These remain isolated experiment
dependencies. The candidate validation split now includes both ordinary speech
and near-sounding negatives: 400 positives and 3,168 negatives, including 1,584
near-word examples, with disjoint training/validation speakers.

### Current handoff: blocked GPU verification, rentals removed

All three experiment Pods and their provider storage have been terminated.
The final account check reports **$9.5334499243 remaining**, **$0/hour**, and
zero matching Pods: approximately **$0.47 spent**. Local cutoff processes were
cancelled after deletion. No full candidate was trained and no private recording
was uploaded or evaluated. The three accepted sounds remain 1, 2 and 5.

The last captured log confirms `pip check` passed; the later CUDA-probe error
was not recovered because live container logs disappeared after shutdown and
Runpod refused restart for lack of GPU capacity. Do not infer that CUDA ONNX
feature extraction passed from the earlier Torch-only test. The next reproducible
step is to retain the complete GPU-probe output locally while running that probe
alone under the verified shutdown guard, then fix the concrete error before any
full training. Further rental attempts were halted after repeated setup/capacity
failures. No user action or further funding is needed for diagnosis.

Validation this turn: ten external guard/cache tests passed; the full app gate
(`.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -v`)
passed with **863 tests passed, one optional native-speaker test skipped**.
The revised negative split was checked to contain ordinary and near-word
validation examples. It still needs a successful GPU smoke before full training.

### Resumed diagnosis — 2026-09-07

User requested continued execution. Preserve the original $10 and six-hour
bounds. The GPU probe now allows a bounded 180-second error-retrieval/retry
window, inside the original absolute cutoff. Stream the full log to the Mac
before publishing the dependency-ready marker; correct the observed error and
continue to synthetic training, with private acceptance gates unchanged.

### GPU failure resolved — 2026-09-07 17:15 UTC

The retained log identified `libnvrtc.so.12` as undiscoverable by ONNX Runtime.
The library already existed inside the pinned image's NVIDIA Python packages;
registering their `lib` directories with the Linux dynamic loader and running
`ldconfig` fixed lookup without adding packages. The setup script now performs
this step. The same Pod passed the retry: Torch CUDA backward, 18 actual CUDA
nodes in melspectrogram and 65 in the embedding model, ONNX Runtime 1.22.0,
CUDA 12.4, RTX 4090. Proof, exact dependency freeze and library paths were saved
locally before continuing. The 48-positive/128-negative GPU smoke is running.
The diagnosis/retry window passed local bounded-failure and success checks;
the real failing probe and successful retry verify the recovery path.

### Full candidate launched — 2026-09-07 17:16 UTC

The GPU smoke passed all 176 generated clips, feature extraction, eight CUDA
training steps and ONNX export; export/Torch maximum difference was 2.98e-8.
An independent Mac process verified the model hash and loaded it successfully.
The full frozen recipe then started: 4,800 positives, 19,000 negatives, two
augmentation rounds and 16,000 steps. It uses trainer SHA-256
`ee85eb4a9c62b5a87fb20586dda8584cc05b6702b8051b927449fdb9581663da`.

Generation initially used network storage. At a verified closed-file pause,
2,773 current files were copied with matching hashes to the existing container
disk, and the output path was redirected there. The trainer code/settings were
unchanged; the network copy remains a small recovery snapshot. This improved
measured positive-generation throughput from about 5 to 12 clips/second. Total
provider allocation remains 15 GB container plus 5 GB volume. The resulting
model and validation artifacts must be downloaded before shutdown.

Local preparation passed: the private evaluator rejects a failed synthetic
gate and duplicate execution, preserves unknown labels, writes private results
with mode 0600, and successfully processed 30 synthetic test fixtures under
macOS network denial. No household audio was read by those tests. The platform
comparison accepts matching results and rejects changed event counts. The full
app gate passed again: 863 tests, one optional native-speaker skip, 18.12 seconds;
ten external guard/cache tests passed in 3.80 seconds.

### Evaluation readiness — 2026-09-07

The synthetic evaluator now runs independent fixtures in four CPU processes,
with one detector and a fixed reset seed per process. Compared with the serial
reference, the 15-fixture smoke had identical event counts and maximum score
difference 8.94e-8. A public Alexa reference test detected all three repeated
positives, kept all three silent negatives clean, and matched serial event
counts with maximum score difference 5.96e-8. The updated evaluator also ran
successfully on the GPU host's 26-fixture smoke in 2.00 seconds, correctly
rejecting its untrained eight-step classifier. No thresholds or event rules
changed. Full-run generation continues; its 4,800 positives are complete.
Three accepted-target CPU/GPU audio comparisons had equal sample counts and
waveform correlations above 0.9992; exact PCM values differ slightly.

### Candidate 1 rejected; local refinement — 2026-09-07

The full GPU run generated all 23,800 clips (5.76 audio hours), extracted
47,600 augmented feature windows and trained 16,000 steps in 9.01 seconds
(classifier/export phase only). Model SHA-256:
`c866c7a940913447be9b245a1ee1fa38ad77fbffaaa97b9b711c9d128a69c045`.
The four-process streaming test took 93.51 seconds: all 400 positive fixtures
detected, but 313/3,168 negative fixtures activated (9.88%). This fails the
pre-registered 1% synthetic gate. No private screen was run. Near-word errors
concentrated on Ricky, recipe, The recipe is ready and quickly; a diagnostic
synthetic threshold comparison did not identify a clean fix, and the registered
0.5 threshold remains unchanged.

All 23,800 generated WAV hashes and seven model/feature/metadata hashes were
verified after local download. The two archives total 871,375,076 bytes, below
the 5 GB egress cap. The Pod and its storage were terminated; control-plane
listing is empty and billing reports $8.8352740152 remaining, $0/hour
(approximately $1.16 total spent). The local cutoff process was cancelled.

Candidate 2 will reuse the same originals: select the two highest-scoring
streaming windows from each of the 20,232 training clips, excluding every
validation speaker, and combine them with the existing augmented windows.
This addresses the observed gap between augmented-feature and actual-stream
negative performance. Add dropout 0.2 and AdamW weight decay 0.01; use fixed
seed 20260911, 24,000 CPU steps and threshold 0.5. No silent GPU fallback:
this is deliberately local, after saving features and removing the rental.
Mining smoke passed eight clips/16 windows; separate positive and negative
training fixtures reproduced the detector's actual maximum score within 1e-5.
Original sample counts stay unchanged; all steps count toward 100,000.

### Candidate 2 trained — 2026-09-07

Training-only replay completed 20,232 clips and published 40,464 hash-checked
streaming windows. Tests reject validation IDs and mismatched labels before
feature loading. The eight-step CPU smoke passed export and fresh Mac streaming
load; its export/Torch maximum difference was 1.49e-8. The full regularized
24,000-step model trained locally in 133.53 seconds. SHA-256:
`c6d6d3d9494580e80333cb8afc26fdc1a723fda442d7a63cfe79a290f91178f1`.
Augmented-feature validation measured 93.375% positive recall and 1.910%
negative crossings; the complete streaming gate is running and remains the
required decision point. Original samples are reused, no private audio is used,
and no Runpod resources remain. Trainer source is saved alongside the output.

### Candidate 2 rejected; final bounded attempt — 2026-09-07

The complete streaming test detected 397/400 positives (97.76%, 100%, 100%
across the three accepted synthetic targets), but 106/3,168 negatives activated
(3.35%). All remaining errors were the held-out Italian voice reading English
“recipe” or “The recipe is ready” with Italian phonemes. This still fails the
unchanged 1% gate. Raising thresholds did not remove these errors; no private
recordings were evaluated.

Attempt 3 adds 600 negative originals: all 20 existing near-word prompts
phonemized as Italian, rendered through English training speakers 0–79. The
held-out voice and validation WAVs remain unchanged. Clean and two seeded
noisy views yielded 14,400 streaming windows, mined using candidate 2. This
covers a missing accent in the training negatives without using validation
voices for training. Validation has informed recipe development, so it remains
a development gate, not independent household acceptance evidence.

The final model uses the same regularized classifier, 24,000 fixed CPU steps,
seed 20260912 and threshold 0.5. Eight-step smoke passed ONNX checking and
Torch/runtime agreement (maximum difference 2.98e-8). Full training is running.
Cumulative full training will be 64,000 steps plus small plumbing smokes, below
100,000. Original negatives including earlier experiments total at most 19,870;
positive originals stay below 5,000. This is the third and last full candidate
within the registered attempt limit. Runpod remains terminated at $0/hour.

Candidate 3 trained in 137.70 seconds on local CPU. SHA-256:
`b370d0aa33d78c0a7bb8034fbb362e278a7af16d125062f1e80374390f599a89`.
Export/Torch maximum difference is 5.96e-8; augmented-feature validation is
98.375% positive recall and 0.489% negative crossings. Full streaming validation
remains authoritative and is running. All 600 additional WAV hashes and the
14,400-window feature cache were verified; generation/mining/training source
snapshots are saved alongside artifacts. The training split/label regression
test passed again in 3.71 seconds, and the external scripts pass Ruff.

### Candidate 3 rejected; bounded experiment exhausted — 2026-09-07

The complete Linux ARM64 streaming test took 141.27 seconds: all 400 positive
fixtures detected (100% for each accepted target), but 36/3,168 negatives
activated (1.136%). The unchanged gate allows at most 31 of these fixtures.
Remaining errors are 24 Italian-voice “quickly” clips and 12 Italian-voice
“recipe” clips. These are synthetic development results, not household false
activations per hour. No threshold tuning, fourth model, private screening,
Mac/Linux candidate equivalence claim, or application integration followed.

The three-candidate limit is exhausted. Scope extension is required before
training another model, even though only about $1.16 was spent and 64,000 full
training steps were used. A proposed next bounded experiment is one additional
local CPU classifier, reusing saved originals/features with more varied speech
context and stronger coverage of accented near-word windows. Keep threshold,
validation fixtures, privacy protections, and all other limits unchanged; keep
its training at most 24,000 steps so the cumulative total remains below 100,000.
This proposal is not executed and has no guaranteed acoustic outcome.

All original private positives remain unevaluated by the three custom models.
The required next stages remain: a passing synthetic model; exact frozen-model
Mac/Linux comparison; one network-denied local 30-clip screen; fresh labeled
multi-session positives; two-minute then 30-minute negative exposure; only then
adapter/runtime integration and live resource/privacy validation. No new
recordings are needed while the synthetic gate remains failed.

Validation this resumed turn: full repository commands
`.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -v`
passed (863 tests, one optional native-speaker skip; pytest 18.12 seconds).
External guard/cache tests passed (10 tests, 3.80 seconds), refinement split/label
regression passed again (one test with two cases, 3.71 seconds), and seven
training/evaluation scripts passed Ruff with only standalone module-docstring
rule D100 excluded. Candidate 3 smoke loaded freshly on Mac and processed 30
silent frames; this is plumbing evidence only. No hardware, live latency,
Raspberry Pi, or real negative-exposure result is claimed.

### Fourth local attempt authorized — 2026-09-07

The user explicitly approved one additional local attempt. The attempt cap is
now four; all other budgets and acceptance rules remain unchanged. No Runpod
rental is needed: the corpus, feature backbone and training features are local.

Freeze attempt 4 before evaluation: 24,000 CPU steps, seed 20260913, threshold
0.5, unchanged 3,568 validation fixtures, same regularized classifier and
negative loss weight 5. Re-mine the 600 existing accent negatives against
candidate 3 (clean plus two noisy views, eight highest-scoring windows each).
Add two positive feature windows from each of the 4,400 training positives,
with an existing ordinary sentence from the same training speaker placed before
or after the name. These are concatenation augmentations, not new original
recordings or evidence of natural coarticulation. Each selected window includes
the entire wake-name segment. Batch allocation: 16 augmented positives,
16 bare streaming positives, 32 contextual positives, 32 original augmented
negatives, 32 original streaming negatives, 128 accented streaming negatives.
No validation voices or private recordings enter feature generation/training.
Cumulative full training becomes 88,000 steps, below 100,000 including smokes.

Attempt 4 preparation completed: 8,800 contextual positive windows and 14,400
accent-negative windows, with zero new originals. The contextual smoke passed
8 clips/16 windows. A regression test verified both speech positions,
deterministic extraction, full-name window inclusion, matched training speakers
and rejection of a corrupted source hash (1.08 seconds). Training split/label
rejection passed again (4.20 seconds). The current repository gate passed:
`.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q`;
863 passed, one optional native-speaker skip, 26.09 seconds. No app code changed.

Fourth full model trained locally in 184.46 seconds (24,000 steps; CPU only).
SHA-256: `088dc0cd5ead76bea1fb19d69523e25b2efb4664e7d90b7a713641ec059cc2e4`.
Torch and ONNX probe scores matched exactly. Augmented-feature validation:
94.125% positive recall, 1.121% negative crossings. The unchanged complete
streaming gate is running; feature validation is not the acceptance result.
The eight-step smoke passed fresh Mac streaming load. The synthetic-only
private evaluator checks and OS network-denial probe also passed again; no
household audio was evaluated during these tests.

### Fourth attempt rejected — 2026-09-07

Complete Linux ARM64 streaming evaluation finished in 145.93 seconds. At the
unchanged 0.5 threshold, all three targets had 100% recall (400/400 total), but
106/3,168 negative fixtures activated (3.346%). Every error was the held-out
Italian voice: 53 “recipe” and 53 “The recipe is ready.” This is worse than
candidate 3 and fails the 1% gate. All 3,568 validation WAV hashes matched the
original manifest. The experiment changed both sampling and contextual
augmentation; this result alone cannot isolate which change caused the
regression. It shows that the revised training coverage still does not
generalize to that held-out voice.

| Candidate | Full steps | Synthetic positives | Negative fixtures activated | Decision |
|-----------|------------|---------------------|-----------------------------|----------|
| 1 | 16,000 | 400/400 | 313/3,168 (9.880%) | Rejected |
| 2 | 24,000 | 397/400 | 106/3,168 (3.346%) | Rejected |
| 3 | 24,000 | 400/400 | 36/3,168 (1.136%) | Best experiment, still rejected |
| 4 | 24,000 | 400/400 | 106/3,168 (3.346%) | Rejected |

Four authorized attempts and 88,000 full steps are now used. No new originals,
Runpod rental, or remote storage were added for attempt 4. All artifacts and
source snapshots are retained locally, including the better third model; none
is designated deployable. No threshold sweep, private evaluation, candidate
Mac/Linux equivalence claim, negative household recording request, or runtime
integration followed the failed synthetic gate.

Before proposing any fifth model, review independent training-voice coverage
for the problematic Italian near-words and design a changed-data experiment;
additional GPU capacity by itself does not address the observed generalization
failure. A new full model is not authorized. No user recordings or additional
Runpod funds are needed at this point. Remaining household and runtime gates
are unchanged. External scripts passed Ruff and `git diff --check` passed;
the full current-turn repository gate is recorded above.

### Exploratory candidate 3 screen authorized — 2026-09-07

The user explicitly requested screening the third model against the existing
30 recordings locally, accepting an exploratory exception to the synthetic
1% gate. Bind this exception to SHA-256
`b370d0aa33d78c0a7bb8034fbb362e278a7af16d125062f1e80374390f599a89`.
Retain the recorded synthetic failure (36/3,168); do not rewrite it as a pass.
Keep threshold 0.5, gain 1, 1,280-sample frames and three-second cooldown.
Verify frozen-model Mac/Linux numerical agreement on synthetic fixtures first,
then run the private screen once with OS network access denied. Report the
27/30 positive criterion separately from final acceptance, which stays false.
No training, provider rental, threshold sweep or app installation is authorized
by this exception. Unknown pronunciation labels remain unknown.

### Exploratory private screen completed: 19/30 — 2026-09-07

Candidate 3 was evaluated exactly once under the user-authorized exception,
with its original model hash and frozen threshold 0.5, gain 1, 1,280-sample
frames, one/two-second leading/trailing padding and three-second cooldown.
The macOS process ran under `sandbox-exec` with `(deny network*)`; a separate
socket probe confirmed OS network denial. Every private WAV hash/header was
verified locally. Results and the exclusive run marker have mode 0600 outside
the repository. No audio was uploaded, changed, or used for training.

Before the private run, native Mac synthetic evaluation took 142.76 seconds.
All 3,568 Mac/Linux fixture event counts matched; maximum score difference
1.55e-6 (tolerance 1e-4). Numerical equivalence passed while synthetic quality
remained explicitly failed at 36/3,168 negative activations. The comparison
and private evaluator now record those two decisions separately. The explicit
exploratory flag is bound to the exact model hash and does not bypass numerical
agreement, repeat-run protection, or final acceptance.

Private outcome: **19/30 detected (63.3%), 11 misses, zero duplicate events**.
This fails the 27/30 positive screen despite being exploratory.

| Group | Detected / total |
|-------|------------------|
| Speaker 1 (user) | 17/25 |
| Speaker 2 | 2/5 |
| English surrounding speech | 6/7 |
| Spanish surrounding speech | 4/7 |
| Portuguese surrounding speech | 5/7 |
| Italian surrounding speech | 3/8 |
| Standalone / unknown language | 1/1 |

Exact name pronunciations remain unknown for all 30; these surrounding-language
counts cannot establish per-pronunciation recall, and language/speaker effects
are confounded in this small set. No threshold sweep or repeat evaluation was
performed. No real negative exposure was collected, so these results cannot
establish household false activations per hour.

Offline Mac measurements: 186.09 seconds of source audio processed in 6.42
seconds, real-time factor 0.0345, model load 0.123 seconds, process CPU 277%
of one core, peak RSS 186.0 MiB. Zero drops means offline serial processing only;
online acoustic latency and Raspberry Pi performance were not measured.

Candidate 3 remains unfit for household use. The private recall gap is now
material evidence for revising pronunciation/voice/acoustic training coverage;
another full training attempt is not authorized. Preserve this screen as an
evaluation record, not training data. Final positive, negative, live resource,
privacy and app-integration gates remain pending. No new recordings are
requested as part of this completed screen.

Validation: synthetic-only evaluator checks passed for normal rejection,
exact-hash exploratory binding, retained numerical gate, preserved synthetic
failure, mode-0600 output and repeat refusal. The changed external scripts pass
Ruff. The full repository gate ran after private timing:
`.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q`
all passed; 863 tests passed and one optional native-speaker test was skipped
in 13.00 seconds. No application code changed.

### Local miss/hit comparison — 2026-09-07

The user judged pronunciation, clarity and spacing acceptable in all 11 misses,
then authorized a local comparison with the successful recordings. Joined the
already saved model scores to hash-verified original WAV measurements under OS
network denial. No private classifier replay, gain experiment, threshold sweep,
transcription, training, or upload occurred. The private comparison JSON is
mode 0600; the original evaluation and audio remain unchanged.

The strongest association is textual name placement, using the user's ordered
utterance list: **name first and followed by speech: 6/17 detected; name after
preceding speech: 12/12; standalone: 1/1**. All 11 misses are name-first. The
pattern also holds within each speaker: speaker 1 has 6/14 name-first and 11/11
other; speaker 2 has 0/3 name-first and 2/2 other. This is an association in a
small diagnostic set, not proof that placement caused the failures.

Measured active-frame median level was -37.38 dBFS for hits and -40.16 dBFS
for misses (misses about 2.8 dB quieter), with strongly overlapping clip ranges
(-42.33 to -34.29 versus -41.72 to -32.65 dBFS). Some misses are louder than
successful clips. Quiet-frame tenth-percentile medians were effectively equal
(-58.68 and -58.70 dBFS). These are energy-based whole-recording proxies, not
word-specific loudness, isolated background noise or true SNR. No near-full-scale
samples were found. Energy activity starts at least 0.98 seconds into each
recording and ends at least 1.97 seconds before its end; this argues against
simple recording-edge truncation but does not annotate name boundaries or
verify within-word clarity. Ten of 11 misses have saved maximum score below
0.1 at the unchanged 0.5 threshold; they are not mostly marginal score misses.

A controlled synthetic diagnostic reused the first 20 existing validation
positives per target (60 total), pairing each exact name waveform with ordinary
speech from the same held-out voice/speaker and an 80 ms join gap. No new original
recordings or models were created. The frozen candidate detected 60/60 names
alone, 59/60 before speech, 60/60 after speech, and 0/60 donor-only controls.
Each alone condition reproduced its saved baseline event count and score within
1e-5. Temporary derived WAVs were removed; configuration, source hash and scores
are retained. This controlled concatenation did not reproduce the large real
name-first deficit. It cannot model natural word transitions or separate voice,
pronunciation, language and acoustic effects.

Next design focus: naturally spoken complete phrases with the name followed by
other words, with independent voice/pronunciation coverage and matched negative
phrases. This is a hypothesis to test, not an accepted root cause or authorization
for a fifth model. Candidate 4 already used concatenation augmentation and
failed its synthetic false-activation gate; do not claim that merely adding
concatenated context is a new sufficient fix. Keep the original 30 out of
training. They now inform development diagnosis; a revised candidate needs fresh
independent evaluation recordings. No new recordings or rental are requested
for this completed comparison.

Verification: acoustic-metric tests passed known tone levels, gain scaling,
activity onset/offset, saturation counting and silence (2 tests, 0.017 seconds).
The diagnostic scripts passed Ruff. The full repository gate ran separately;
its final result is recorded below. No application code changed.

Full current-turn gate passed: `.venv/bin/ruff check . --fix`,
`.venv/bin/ruff format .`, `.venv/bin/mypy --pretty --show-error-codes`,
`.venv/bin/pytest tests/ -q`; 863 passed, one optional native-speaker skip,
17.31 seconds. `git diff --check` passed.

### Name-isolation diagnostic authorized — 2026-09-07

The user explicitly approved comparing the 11 missed full sentences with local
copies retaining only the initial name. This permits diagnostic replay of these
clips, not retraining, threshold tuning or a new acceptance claim. Keep candidate
3, threshold 0.5, gain 1, original leading silence, total sample count and the
existing streaming protocol unchanged. Remove subsequent speech only on temporary
copies. Estimate the word boundary with the already cached offline Whisper-small
model (revision 973afd24965f72e36ca33b3055d56a652f456b4d), without logging or
saving transcripts. Check boundary plausibility before scoring; timestamps are
approximate. Freeze boundaries before viewing detector outcomes and report a
small fixed boundary-sensitivity check rather than selecting a favorable cut.
All processing uses OS network denial, with no new dependencies, model download,
provider rental, original-file changes or training. Results remain diagnostic;
future acceptance still needs independent recordings.

### Name-isolation diagnostic completed — 2026-09-07

Offline approximate alignment found the following supplied word in ten clips.
Clip 14's unprompted alignment did not; a local re-alignment prompted with the
user-supplied sentence identified a plausible name end at 1.90 seconds. These
are automatic estimates, not manually verified word boundaries. No transcripts
were saved or logged. All endpoints were fixed before detector scoring and
fell 0.20–0.80 seconds after measured speech activity onset.

For each of the 11 misses, retained the original samples and leading silence
up to the fixed cut, replacing the remaining samples with zeros. Total duration,
gain, model, padding, frame size, reset seed and threshold stayed unchanged.
Primary copies preserve every prefix sample exactly. Additional predeclared
conditions used cuts 40 ms earlier/later and a 5 ms fade at the primary edge.
All temporary WAVs were mode 0600 inside a private temporary directory and were
removed after scoring. Processing remained under OS network denial.

| Diagnostic condition | Detected / 11 |
|----------------------|---------------|
| Original full sentences | 0/11 |
| Name only, estimated boundary | 3/11 |
| Boundary 40 ms earlier | 3/11 |
| Boundary 40 ms later | 2/11 |
| Estimated boundary with 5 ms fade | 4/11 |

The primary-cut detections were clips 04, 14 and 24. Seven clips activated in
at least one isolation variant, but **none activated in all four variants**.
Do not select the best cut per clip or treat that union as recall. Boundary
variation can remove a name sound or retain the next word's onset; fade sensitivity
also makes an abrupt-edge explanation possible. This diagnostic does not reliably
isolate subsequent speech as the main cause, nor does it rule out contextual,
pronunciation, voice or acoustic effects. It is insufficient evidence for a
simple spacing fix or a new training recipe. Human-verified name boundaries
would be needed before making stronger claims from these edited recordings.

Every full-sentence replay reproduced the saved zero-detection outcome and
maximum score within 1e-5. The original private evaluation remains **19/30**;
never add detections from edited audio to that acceptance result. No model,
threshold, training-data or app change followed. Future acceptance needs fresh
independent recordings, and a fifth training attempt remains unauthorized.

Verification: two unit tests passed for unchanged prefix amplitude, leading
samples, duration, original-array immutability, suffix silencing, bounded fade
and invalid fade rejection. Changed scripts passed Ruff. All 30 original WAV
hashes and the prior evaluation hash were rechecked unchanged; temporary audio
was removed and diagnostic/configuration/alignment files verified mode 0600.
Full gate: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q`;
863 passed, one optional native-speaker skip, 17.90 seconds. No application code
changed. No provider cost or model download was incurred.


### ElevenLabs pilot preflight — 2026-09-07

The user reports permission from ElevenLabs to use generated speech for this
training project; that exception has not been independently verified. The user
configured `ELEVENLABS_API_KEY` in the ignored local `.env`. Authentication,
subscription, model and voice-list requests succeeded without exposing the key.

Proposed listening pilot: two native voices per language (English, Spanish,
Portuguese and Italian), each speaking the three previously accepted name
sounds inside sentences, plus a similar-sounding negative. Prefer European
Portuguese. Review pronunciation before any larger corpus; use distinct speakers
for eventual validation. Send only generic synthetic prompts, never household
audio or voice-cloning samples. A fifth training attempt is not authorized.

Execution is blocked by the account tier: a single TTS request using Eleven v3
and Spanish Voice Library voice Cristina returned HTTP 402,
`paid_plan_required`: free accounts cannot use library voices through the API.
The library's `free_users_allowed` flag does not establish free API access.
No audio was generated. Before and after usage was 39 of 10,000 credits,
leaving 9,961; no credits were charged. No voices were added or deleted, no
subscription was changed and no GPU was rented. Filtered evidence is stored
outside the repository in the private `elevenlabs-pilot-20260907` experiment.

Next step: the user can enable a paid ElevenLabs subscription with Voice Library
API access, then rerun the small pilot. Do not purchase an upgrade automatically.
Keep the detector rejected at 19/30 and require fresh independent recordings for
future acceptance. No application code or dependencies changed in this preflight.

Preflight documentation validation: `.venv/bin/ruff check . --fix`,
`.venv/bin/ruff format .`, `.venv/bin/mypy --pretty --show-error-codes`,
`.venv/bin/pytest tests/ -q` passed; 863 tests passed, one optional native-speaker
skip, 18.11 seconds. No new executable code was introduced.


### Browser-created voices and API listening pilot — 2026-09-07

The user authorized creating synthetic voices in the website, then generating
speech through the API. Direct API Voice Design returned HTTP 403 requiring a
paid plan, but website Voice Design succeeded on this free account. The website
showed zero of three custom slots used before this operation. Saved the first
preview from each requested design, without claiming it was acoustically reviewed:
Portuguese female (Lisbon accent requested), Italian male (standard accent
requested), and Spanish female (Venezuelan/Caracas accent requested). The user
corrected the initial peninsular Spanish choice before saving; those previews
were superseded and only the Venezuelan design was saved. No existing voice
was deleted or modified. All three custom slots are now used.

All three saved voices successfully generated Eleven v3 speech via the API.
An existing premade English Roger voice also succeeded. This supersedes the
paid-subscription blocker for this smaller custom-voice pilot, not the separate
restriction on Voice Library API access. The initial two-voices-per-language
proposal is reduced to one per language for this listening review; it does not
provide independent validation voices or sufficient training diversity.

Generated four review tracks, each with three positive sentences using the
previously accepted IPA name targets, followed by one negative sentence. A
separate Portuguese smoke sample also succeeded: 13 positive and four negative
speech items overall, plus 12 website design previews across four design calls.
Conservatively reserving those 12 previews against both prior corpus bounds
keeps positives at most 4,961 and negatives at most 19,886. None is accepted as
training data yet. Tags `[happily]`, `[softly]` and `[pause]` are performance cues;
accent, pronunciation and whether tags were spoken still require human review.
Do not treat requested accents, IPA or labels as verified audio quality.

Usage increased from 39 to 1,483 of 10,000 credits across website design and
API generation: 1,444 credits consumed, 8,517 remaining, still on the free tier.
No upgrade, GPU rental, fifth training run or household-media upload occurred.
Generic synthetic prompts, exact requests, voice IDs, audio files, SHA-256 hashes,
usage snapshot and local decode checks are in the private
`elevenlabs-pilot-20260907` experiment. Original household recordings were not
accessed. Next: user listens to the four tracks and identifies acceptable
pronunciations and accents before corpus expansion or any new training decision.

Pilot verification: all four MP3s decoded locally to non-silent 16 kHz PCM
(9.68–17.68 seconds); SHA-256 and duration checks recorded. This is technical
validation, not a listening judgment. Repository gate passed: `.venv/bin/ruff
check . --fix`, `.venv/bin/ruff format .`, `.venv/bin/mypy --pretty
--show-error-codes`, `.venv/bin/pytest tests/ -q`; 863 passed, one optional
native-speaker skip in 20.04 seconds. No application code changed.

### Expanded ElevenLabs training authorization — 2026-09-07

The user approved all four pilot tracks and explicitly authorized larger
ElevenLabs batches and repeated new training attempts without further approval,
until a model passes the original 30 recordings or the original $10 Runpod
budget is exhausted. This supersedes the earlier sample, training-step and
attempt-count caps for this continuation. It does not authorize an ElevenLabs
subscription purchase; report when credits are insufficient. Current Runpod
balance is $8.7798144967 with no active pods or hourly spend. Budget is cumulative,
not another $10. Preserve a shutdown reserve and terminate any rented resources
before exceeding the available balance; do not rent while TTS is generating.

Original recordings remain strictly excluded from all training and provider
uploads. Repeated evaluation makes the original 30 a development benchmark;
passing it is not independent acceptance. Preserve threshold 0.5 and the frozen
streaming protocol; initial pass means at least 27/30 with existing synthetic
recall and false-trigger gates, not a detector that activates on everything.
Future production acceptance still requires fresh recordings and live testing.

First expanded batch: 216 independently generated utterances (144 positive,
72 negative), equal representation of English, Venezuelan Spanish, European
Portuguese and Italian; the three user-approved IPA targets appear in full
sentences, paired with ordinary and similar-sounding negative speech. Eleven v3
uses normal and `[happily]` delivery. Frozen prompts total 6,384 characters,
within the 8,517 remaining free-tier credits. A sentence-held-out portion uses
the same voices and is diagnostic only; retain the old speaker-disjoint
synthetic validation suite. Four waveform variants per training utterance
cover gain, speed and light reverberation/noise, without augmenting validation.

Training design: retain the openWakeWord feature backbone and candidate-3
classifier architecture, initialize from its exported weights, mix old
training-only positives/hard negatives with new full-sentence feature bags,
and optimize a maximum-over-time sentence loss so a positive sentence need
not label every non-name frame positive. Only synthetic features enter the
trainer. Freeze each model before local screening and record every attempt,
including failures. Reuse local CPU head training initially; rent a GPU only
when it materially accelerates work. No new app dependency or runtime change.

Expanded batch generation completed: 216/216 receipt hashes and mono 16 kHz
PCM16 decodes verified; 184 training and 32 same-voice sentence-check originals.
Four training-only variants yielded 736 training feature bags; the 32 validation
bags remain unaugmented and excluded from optimization. Provider usage is
8,307/10,000, leaving 1,693 credits; actual account usage delta was 6,824 credits
for this phase (input text length alone is not a billing guarantee). No plan
upgrade is needed for this run. The two-step training smoke exported a valid
ONNX model with zero difference on the checked Torch/ORT output. Candidate 5
is training locally in a network-disabled container mounted only to the synthetic
experiment directory. Repository gate passed (863 tests, one optional native
speaker skip in 26.53 seconds); external scripts passed Ruff after formatting.

Candidate 5 completed 4,000 local CPU steps in 56.54 seconds. Export SHA-256:
`da875c237cad330e2cf5913c31b7591d9be0a0eb5537666083ba86be04d6522c`.
Result: 19/30 original recordings, zero duplicates; new same-voice sentence
check 24/24 positives and 0/8 negatives; old speaker-disjoint suite 400/400
positives and 160/3,168 negative activations (5.05%). Rejected. Original clips
were checked under OS network denial at the unchanged threshold and protocol.

Candidate 6 changes the classifier to two temporal convolutions and temporal
max pooling, trained from scratch for 6,000 steps, to reduce dependence on
feature-window position. Its two-step pipeline/export smoke passed (maximum
Torch/ORT difference 3.73e-8). Candidate 7 separately retains the candidate-3
MLP initialization, restores the extra accent-negative training pool omitted
from candidate 5, and adds training-only waveform gains of 0.1 and 0.03 with
positive sentence-loss weight 6. These 368 additional derived feature bags
require no new TTS originals or provider credits. Both candidates are local,
network-disabled training runs. New extraction/training scripts are versioned
in each run's source/configuration artifacts; no app integration is attempted.

Candidate 6 completed in 409.55 seconds, SHA-256
`cc75550f7e088b19e7ee4820bdb33de46ee7884d7a5ff3763fdac349f666a001`:
21/30 private development detections, zero duplicates; 24/24 new sentence
positives and 0/8 negatives. Candidate 7 completed in 100.32 seconds, SHA-256
`fdb28d4729208f0f40897e758e6f01e54a377a2cfa4d1713792502fd239243e8`:
20/30, zero duplicates; 24/24 and 0/8 on new sentences. Both rejected on the
private development target; the full older synthetic suite was not rerun for
these already-rejected candidates. No private threshold change or training use.

The first new trainer omitted the old streaming/context positive pools; the
next recipes explicitly restore these alongside the accent-negative pool.
Candidate 8 fine-tunes candidate 6's temporal classifier for 3,000 steps on
all these pools and the quiet-augmented ElevenLabs bags. Candidate 9 tests the
same data restoration with the original MLP architecture, initialized from
candidate 3 for 4,000 steps. Old augmented, streaming, accent-negative and
context-positive feature files were rehashed against their completion records;
all four matched. Both runs remain local and network-disabled, with private
recordings outside the container mount and no additional provider spend.


### Iteration results and next data batch — 2026-09-07

| Candidate | Change | Original 30 detected | Old synthetic negative activation |
|-----------|--------|----------------------|-----------------------------------|
| 5 | Full-sentence MLP fine-tune | 19/30 | 160/3,168 (5.05%) |
| 6 | Temporal convolution classifier | 21/30 | Not rerun after private-target failure |
| 7 | Quieter speech and extra accent negatives | 20/30 | Not rerun after private-target failure |
| 8 | Temporal classifier with restored streaming/context positives | 21/30 | Not rerun after private-target failure |
| 9 | MLP with restored streaming/context positives | **23/30** | **125/3,168 (3.95%)** |
| 10 | Candidate 9 plus connected, rushed and soft speech | 21/30 | Not rerun after private-target failure |

All six models were frozen before screening, at threshold 0.5 with zero
private duplicate events. None passes either the combined acceptance criteria
or the requested minimum 27/30 development target. Candidates 5 and 9 detected
all 400 old synthetic positives, but both exceeded the 1% negative limit.
Candidates 5, 6, 7 and 10 also detected all 24 new same-voice sentence positives
with no activations on eight new sentence negatives. Do not present this
same-voice result as independent validation. No detector has been installed.

The complete new corpus contains 264 original utterances: 180 positive and
84 negative, with 232 training originals and 32 sentence checks. Supplementary
48-clip generation deliberately removes the comma after the name and adds
`[rushed]` and `[softly]` delivery; it produced no improvement in candidate 10.
All audio variants remain derived synthetic data. There are 1,424 feature bags:
1,392 training bags and 32 excluded sentence-check bags. All 30 household audio
hashes were reverified unchanged. No household audio entered TTS or training.

ElevenLabs usage updates lag immediately after generation: the later account
snapshot is 9,871/10,000 credits, leaving **129**, superseding the immediate
8,846 reading. Earlier phase usage deltas are observations, not exact invoice
attribution. A meaningful larger batch now needs more credits. Runpod remains
at **$8.7798144967**, with zero hourly spend, no pods and no network volumes;
no new rental was necessary for these six small classifier runs. The original
$10 Runpod cap remains intact and is not the current blocker.

The next useful experiment increases speaker diversity rather than repeatedly
retuning these same four voices against the 30 known recordings. Twelve public
Voice Library voices are selected in `next-selected-voices.json`: three each
for English, Venezuelan Spanish, European Portuguese and Italian. Eight are
reserved for training and four for synthetic validation; voice IDs and public
owner IDs are disjoint across those splits. Metadata has been inspected, but
no new public voice audio has been generated or reviewed. Target approximately
1,200 utterances (900 positive and 300 negative), with the final count bounded
by verified API credit cost after upgrade. Keep phrase positions, name variants
and delivery matched across positive/negative speakers. Freeze that manifest
and its speaker split before synthesis and training.

The user was told Starter at $6/month is sufficient for the next bounded batch;
the official API pricing page lists Creator at $11 for the first month and $22
thereafter: https://elevenlabs.io/pricing/api. Do not purchase a subscription on
the user's behalf. Authorization for further generation and training persists;
resume after the account upgrade without requesting further training approval.
Do not spend Runpod funds merely to exhaust the budget while TTS credits are
blocking the chosen data-diversity experiment.

Evidence: `attempt-summary.json`, each candidate's configuration, trainer source,
ONNX report and private development summary; `usage-settled.json`,
`runpod-final.json`, prior-feature checksum verification, generation receipts
and feature manifests. Full repository gate passed earlier in this continuation:
`.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q`;
863 passed, one optional native-speaker skip, 26.53 seconds. Final external
scripts passed Ruff with standalone docstring/line-length exceptions and were
formatted. Model export parity was verified for every completed candidate.
No new application code, dependencies, hardware tests or live runtime validation.


### Starter upgrade and speaker-diverse batch — 2026-09-07

The user upgraded ElevenLabs to Starter. The live API confirms 30,129 available
credits, zero initially used, and credit overage disabled. A paid-library
one-clip smoke succeeded. The frozen next batch is **720 originals**: 540
positive and 180 matched negative, across the 12 selected voices. This replaces
the earlier approximate 1,200-clip estimate: 24,450 text characters / 26,694
UTF-8 bytes fit conservatively within the verified credit allowance.

Generation is in progress with two concurrent requests, per-clip receipts and
no automatic retries of ambiguous paid requests. Eight speakers supply 480
training originals; four separate speakers supply 240 validation originals.
Both voice IDs and public-owner IDs are disjoint between splits. The validation
voices must remain excluded from training and augmentation. Normal, soft and
rushed delivery and name positions are matched across positive/negative text.

The next classifier experiments remain local with network-disabled containers;
original household recordings remain outside the training mount and are screened
only locally with network denied. No new Runpod rental or runtime change.
The original cumulative $10 Runpod cap and existing acceptance thresholds persist.
Evidence: `starter-subscription-before.json`, `requests-diverse.json`,
`next-selected-voices.json`, generation receipts and the frozen trainer configs.


The first library batch hit one HTTP 409 `already_running` rejection while
initializing the second voice. Provider history contained no matching generated
item. The rejected marker and recovery evidence were retained, and generation
resumed with requests serialized within each voice, two different voices at a
time. Completed receipts are reused. No ambiguous timeout was retried.

The feature smoke passed: two training clips produced 12 augmented bags; two
validation clips produced exactly two unaugmented bags. Voice/owner split
assertions passed. Feature extraction is pipelined behind synthesis. Candidate
11's recipe was frozen before screening: original MLP initialized from candidate
3, 6,000 steps, positive/negative bag weights 6/6, all prior accent-negative and
streaming/context-positive pools restored, new `bags-diverse` features. Starting
from candidate 3 avoids inheriting candidate 9's false-positive regression.
The household benchmark remains strictly evaluation-only.

Repository gate rerun: Ruff check/fix and format passed; mypy passed for 60 source
files; `.venv/bin/pytest tests/ -q` passed with 863 tests and one optional skip
in 19.55 seconds. No application code or dependencies changed in this phase.


The 720-clip library batch completed. Every MP3 matched its receipt hash and
every WAV passed the 16 kHz mono PCM16 / nontrivial-duration checks. Combined
features contain 4,544 bags: 4,272 training and 272 excluded validation bags,
including all 240 new speaker-disjoint originals. All validation augmentation
indices are zero. Candidate 11 training has started using the frozen recipe.
Immediate ElevenLabs usage is 12,035/30,129 credits; allow for reporting lag.
Runpod remains unrented.


Candidate 11 exported with exact Torch/ONNX probe parity and detected **24/30**
original household recordings (zero duplicates). It detected 179/180 new
speaker-disjoint positives and activated on 1/60 matched negatives. It still
fails the 27/30 development target. The full older synthetic suite is running.
Candidate 12 tests the temporal convolution head initialized from candidate 6,
with the same speaker-diverse data and restored pools, 6,000 steps and bag
weights 6/6. Its configuration and source are frozen before screening. No new
TTS or Runpod spending is needed for these two classifier comparisons.


Candidate 11's full older synthetic check detected all 400 positives, but
activated on 228/3,168 negatives (7.20%). It is rejected despite the 24/30
development improvement. Candidate 13 will test active hard-negative selection:
choose the hardest 64 of 512 sampled **training** negative frames each step,
never evaluation fixtures. Frozen recipe: parent 11 MLP, 4,000 steps, positive
bag weight 10, negative bag weight 6, all pools and speaker-diverse bags.
This balances stronger negative practice against loss of name sensitivity.


Candidate 12 detected **29/30** original recordings at threshold 0.5, with
zero duplicate events and network-denied evaluation. This clears the frozen
27/30 development target, but is not yet accepted: the speaker-disjoint and
full older synthetic negative checks are running. Export parity was exact;
training took 420.95 seconds locally. Candidate 13 had already begun before
this result arrived. No household media was used in training or uploaded.


Candidate 12's speaker-disjoint result was 179/180 positive, 0/60 negative,
but the older synthetic suite exposed 260/3,168 negative activations (8.21%).
All 400 older positives passed. The 29/30 recording target alone is therefore
insufficient: candidate 12 is rejected for operational use. Candidate 13
returned 23/30 and is not promoted. Candidate 14 applies the active hard-negative
recipe to candidate 12's temporal classifier: 3,000 steps, 512-to-64 hard
training-negative selection, positive/negative bag weights 10/6, same data and
threshold. Latest settled ElevenLabs usage is 12,244/30,129; no new synthesis
or rental is needed for this refinement.


The new anchor scan found only four above-threshold windows among 8,000
windows from 2,000 balanced English training negatives. This narrows the earlier
window-selection hypothesis: all 260 candidate-12 false activations instead
belong to the held-out Italian voice (five confusable phrases), not English
validation speakers. The existing Italian-phoneme negatives use English training
voices; the new corpus had Italian speakers but different negative words.

An additional 180 synthetic negatives are generating with the two existing
Italian training voices plus the approved custom Italian voice. They cover all
20 established negative phrases, using the existing Italian phonemes and three
delivery styles, 3,654 text characters. No held-out voice/audio enters training.
The new negatives will extend `bags-diverse` into `bags-contrast`. The updated
mining cache and its training-only provenance are retained. Candidate 14 finished
and is being screened; candidate 15 will initialize from candidate 12 with the
contrast bags, 4,000 steps, bag weights 10/10, 512-to-64 hard selection and
the additional mined training negatives.


Candidate 14 retained 29/30 and all 400 old positives, but still activated on
252/3,168 old negatives (7.95%). Hard selection of the existing pools alone
was insufficient. The 180 native-Italian contrast originals completed and
passed receipt/audio-format checks. Combined features contain 5,624 bags:
5,352 training and 272 unchanged excluded validation bags. Speaker separation
and unaugmented validation assertions passed. Candidate 15 is training with
the frozen contrast recipe. Initial contrast loss is substantially larger than
the old-pool refinement loss; final recognition/negative checks remain required.
Immediate ElevenLabs usage after the contrast batch is 13,919/30,129, subject
to reporting lag. No additional Runpod resources were created.


Candidate 15 detected 27/30 original recordings, 177/180 speaker-disjoint
positives with 0/60 negative activations, and all 400 older positives. Older
negative activations fell to 55/3,168 (1.74%), still above the unchanged 1%
limit. Of these, 53 are the Italian rendering of the established recipe
phrase and two are another confusable word. No validation samples are added
to training. Candidate 16 continues candidate 15 for 2,000 steps at learning
rate 0.00005, positive/negative bag weights 10/12, same contrast bags and hard
negative pools. Original household hashes were reverified: all 30 unchanged.
Latest settled credits are 14,068/30,129; Runpod remains $8.7798144967, zero
hourly spend, no pods or network volumes.


Candidate 16 retained 27/30 and all 400 older positives; the new-speaker
check was 176/180, 0/60 negative. Older false activation remained 63/3,168
(1.99%), so additional tuning of the same data did not pass.

A separate native Italian Piper voice, Paola medium, is added to the external
training workspace only, producing 120 negatives (all 20 established phrases)
and 30 positives (all three approved phonemes, isolated and in context). This
adds a new training speaker and TTS domain while preserving the held-out
Riccardo voice. The existing container Piper runtime is reused; no dependency
or runtime model is added to the application. Model source:
https://huggingface.co/rhasspy/piper-voices/blob/main/it/it_IT/paola/medium/MODEL_CARD.
The linked source dataset declares CC0-1.0:
https://huggingface.co/datasets/paolapersico1/Voice-Dataset-Italian.
Downloaded ONNX SHA-256 is
`6fc918b5a0ea6137382833dddfa567bffbe6a5060c02043c87192ee59c04210c`;
config SHA-256 is
`aea19c0a7fce29fbc359b93f10e7902854401e4c95ae2ea328ae516b15d296cf`.
Generation runs with container networking disabled. Candidate 17 is planned
from candidate 15 with the additional `bags-paola`, 3,000 steps, bag weights
10/10, learning rate 0.00005, and the same active hard-negative pools.


The Paola corpus completed: 150 receipt-hashed files, 30 positive and 120
negative. Combined features have 6,524 bags: 6,252 training and the same 272
excluded validation bags, all unaugmented. Short single-word negatives use
non-silence and minimum-sample checks rather than the earlier sentence-duration
cutoff. Generation reused the existing container Piper runtime after correcting
the vendor import path; no dependency was installed.

Candidate 17 trained locally for 194.01 seconds, exported with exact probe
parity, and detected **28/30** original recordings with no duplicates. The two
synthetic suites are running. Updated standalone scripts passed Ruff check and
format with the existing standalone docstring/line-length exceptions.


Candidate 17's final synthetic result: 400/400 old positives, 53/3,168 negative
activations (1.67%); new-speaker check 177/180 positive and 0/60 negative. It
still fails the negative gate despite 28/30 development recognition.

Candidate 18 tests an architectural hypothesis rather than another threshold
change: replace the convolution head's global temporal maximum with flattened
ordered features. This retains timing information in the final linear layer
to distinguish a whole wake word from a similar fragment inside a longer word.
It keeps candidate 17's convolution weights; the output weight is initially
repeated over the 16 positions and divided by 16. Frozen recipe: 4,000 steps,
learning rate 0.0001, bag weights 10/10, `bags-paola`, same hard-negative pools.
This changes only the external ONNX training head, not application runtime code.


### Training stopping condition reached — candidate 18 — 2026-09-07

| Check | Result | Gate |
|-------|--------|------|
| Original 30 development recordings | **27/30**, zero duplicate events | At least 27/30 |
| Older synthetic positives | **400/400**, 100% for each of three variants | At least 90% each |
| Older synthetic negatives | **1/3,168 (0.032%)** | At most 1% |
| New speaker-disjoint positives | **178/180 (98.9%)** | Supplemental generalization check |
| New speaker-disjoint negatives | **0/60** | Supplemental generalization check |

The ordered temporal head passed where global temporal pooling retained one
confusing phrase. These results support selecting candidate 18; they do not
establish a causal explanation beyond the controlled model comparison. All
results use threshold 0.5, 1,280-sample chunks, one-second leading/two-second
trailing padding, three-second cooldown and reset seed 20260909. Original
recordings were evaluated with OS networking denied and remain hash-identical.

Selected ONNX SHA-256:
`0e6ae6e47a1dd4acd1ac955830c32b85eb55fdc4112bde90918d566d976a2029`.
Model: external `candidate-run-18/reachy-candidate.onnx`. Selection and aggregate
evidence: `elevenlabs-training/selected-candidate.json` and
`elevenlabs-training/attempt-summary.json`. Each candidate directory retains
its configuration, trainer source, export report and synthetic results; private
results remain outside the training mount. Candidate 18 trained for 318.25
seconds locally and passed ONNX checking plus exact Torch/ONNX probe parity.

Since the Starter upgrade, 900 ElevenLabs originals and 150 local Italian
originals were added. The complete new corpus, including the earlier 264
ElevenLabs originals, contains 1,314 originals, with 1,042 training and 272
excluded validation originals; augmentation yields 6,252 training bags and
272 unchanged validation bags. No original household audio enters this corpus.

Final budget snapshot: ElevenLabs used 14,068/30,129 credits, leaving 16,061;
overage disabled. Runpod balance $8.7798144967, zero hourly spend, no pods or
network volumes. Additional Runpod spend for this Starter continuation: **$0**.
No further subscription upgrade is needed for the completed training target.

Validation: the repository gate passed (`.venv/bin/ruff check . --fix`,
`.venv/bin/ruff format .`, `.venv/bin/mypy --pretty --show-error-codes`,
`.venv/bin/pytest tests/ -q`): 863 passed, one optional native-speaker skip,
19.55 seconds. External experiment scripts passed Ruff with the documented
standalone docstring/line-length exceptions; feature smoke and speaker-split,
receipt hash, audio, feature checksum and export parity checks passed. Final
evaluation used `screen_private.py --attempt 18` under `sandbox-exec` network
denial, `evaluate_new.py --attempt 18 --diverse`, and
`evaluate_synthetic.py <candidate-run-18> --workers 4`. Older synthetic fixture
processing took 144.17 seconds. No new application dependencies or runtime
code were added in this continuation.

**Next stage:** fresh multi-speaker, multi-language household positives and
long household negative audio, following the existing final acceptance gates.
The old 30 are not independent acceptance data. Short synthetic-negative
activation fractions are not real-world false wakes per hour. No simulator,
hardware, long-duration ambient, or new full Mac/Linux candidate equivalence
claim is made here. No model installation or runtime enablement was performed.


### Fresh household screen — candidate 18 — 2026-09-07

The user supplied 30 new recordings in a separate private folder. Candidate 18
was frozen before this collection and tested once at the unchanged threshold
0.5 and streaming protocol, with OS networking denied. The fresh recordings
remain evaluation-only: no training, model selection among alternatives,
threshold sweep, normalization, audio edits or uploads were performed.

Result: **27/30**, zero duplicate events. Speaker 1: 13/15; speaker 2: 14/15.
Language results: English 7/8, Spanish 6/8, Portuguese 7/7, Italian 7/7.
Missed clip numbers: 01, 08 and 23. Both speakers recorded at a labeled
40 cm distance. Exact pronunciation variants remain unlabeled; this is one
recording session, not the required multi-session final acceptance corpus.

The initial 27/30 screen passes. Final household acceptance does not: observed
aggregate recall is 90% versus the 95% final target, and speaker 1 / English /
Spanish also fall below their 90% final targets. No long-negative collection
or app enablement follows yet. Next: review the three misses and decide the
next step before requesting more recording effort. Keep this fresh benchmark
separate from training and record any future model-selection use explicitly.

All 30 files passed format, finite/non-silent sample and checksum checks:
16 kHz mono PCM16, 5.296–5.418 seconds each. The initial audit expected nearly
six seconds and stopped before inference; the actual files consistently have
about 5.35 seconds of captured audio. The audit was adjusted to accept these
intact shorter files; no audio or detector parameter changed. Full per-file
audits, frozen-start metadata and results remain in the private recording
folder as `candidate18-screen-started.json` and `candidate18-screen-results.json`.

Validation command: existing native inference environment ran external
`evaluate_fresh18.py` with `sandbox-exec -p '(version 1)(allow default)(deny network*)'`.
This reused the frozen `score_fixture` implementation. No app code or dependencies
changed; application tests were not rerun for this recording-only evaluation
and documentation update. No hardware runtime/latency or false-wakes-per-hour
claim is made.


### User-confirmed defective recordings replaced — 2026-09-07

After listening, the user reported no audible speech in original fresh clips
01 and 08 and identified clip 23 as a genuine naturally connected-speech miss.
The user recorded replacements for 01/08 in a separate private subfolder.
Original audio, original 27/30 report, and all prior hashes are preserved.
The earlier numerical non-silence audit did not establish audible speech.

Both replacement clips passed the frozen candidate-18 detector, once each.
The corrected fresh set is **29/30 (96.7%)**, zero duplicate events: speaker 1
15/15; speaker 2 14/15; EN 8/8, ES 7/8, PT 7/7, IT 7/7. Clip 23 remains
a miss, unchanged. This result substitutes only the two user-identified faulty
recordings; the remaining 28 results were reused rather than re-screened.

The corrected set meets aggregate and per-speaker recall targets, but does not
establish final household acceptance: Spanish is 7/8 (87.5%), pronunciation
variants remain unlabeled, and multi-session/per-stratum sample requirements
are incomplete. Next positive validation should include natural connected
Spanish speech from both speakers in additional sessions. Do not normalize away
the miss or ask speakers to add artificial pauses. Long negative collection
and app enablement remain pending the final positive gate.

Validation: `evaluate_replacements18.py` in the existing native inference
environment under `sandbox-exec -p '(version 1)(allow default)(deny network*)'`.
Model hash matched the selected candidate; all 30 original hashes matched;
replacement WAVs passed mono 16 kHz PCM16, duration and sample checks. The
replacement-start record and combined report are saved privately as
`candidate18-started.json` and `candidate18-replacement-results.json`. No
training, threshold change, audio editing, upload or app-code change occurred.
Application tests were not rerun for this recording-only evaluation and
documentation update; `git diff --check` passed.


### Spanish follow-up with delayed speaking cue — 2026-09-07

After an inaudible first attempt, the microphone device listing confirmed the
built-in microphone at audio index 0. The user heard continuous-counting
playback and then confirmed the whole delayed-cue phrase was audible. The
follow-up command used a three-second speaking cue and per-clip playback
confirmation. This is a recording procedure improvement, not proof of the
cause of every earlier defective recording. Earlier files remain preserved.

Frozen candidate 18 detected **8/10** new Spanish recordings from speaker 1
at the labeled 40 cm distance, with zero duplicate events. Misses: clips 01
and 03, both name-first phrases. Name-first recall in this round is 3/5;
name-later recall is 5/5. This pattern is descriptive, not a proven causal
diagnosis. Peak scores on the missed clips were 0.000875 and 0.019734, both
below the unchanged threshold 0.5. Their RMS levels (117.6 and 101.7 PCM
units) lie within the range of detected clips (93.0–147.8), so there is no
clear numerical indication of the earlier near-silent recording defect.

All ten WAVs passed mono 16 kHz PCM16, sample/finite-value, checksum and
duration checks (7.997–8.147 seconds). No trimming, normalization, threshold
sweep, alternate-model comparison or training occurred. Evaluation reused the
frozen streaming protocol and ran locally with OS networking denied. Reports
`candidate18-started.json` and `candidate18-spanish-results.json` remain in
the private recording folder. The command used external `evaluate_spanish18.py`
with the existing native inference environment and
`sandbox-exec -p '(version 1)(allow default)(deny network*)'`.

The 80% Spanish follow-up fails the 90% stratum target. Keep this round
separate from the corrected earlier 29/30 result; aggregate counts must not
hide the weaker session. No final household acceptance, negative collection
or app enablement is warranted yet. Next: investigate valid connected-speech
misses before asking for more household recordings. These files remain
evaluation-only; any future use to guide model selection must be explicitly
recorded as development use. No application code/dependency changed; app tests
were not rerun for this recording-only evaluation. `git diff --check` passed.


### Connected-speech investigation authorized — 2026-09-07

The user requested investigation of the Spanish connected-speech misses.
Scope is diagnostic only: freeze candidate 18 and threshold 0.5; reproduce
the ten-clip baseline, compare fixed gain and chunk-phase/leading-silence
controls across successes and misses, then examine approximate local word
boundaries and matched name/suffix controls if alignment is usable. Use only
cached local inference/alignment models with OS networking denied. Keep private
waveforms and transcripts outside the repository and training mount; derived
audio is temporary and removed. Preserve original hashes and acceptance
reports. Do not report transformed-clip rescues as acceptance improvements.
No additional recording, training, threshold change, provider call or deployment
is authorized by this diagnostic step. Record uncertainty in automatic word
alignment and avoid causal claims from an uncontrolled cut alone.


### Connected-speech diagnostic results — 2026-09-08

Frozen candidate 18 reproduced the Spanish 8/10 baseline exactly. Ninety
baseline/gain/phase/leading-silence cases did not rescue misses 01 or 03:
0.5x/2x/4x gain without clipping, 20/40/60 ms onset shifts, two seconds of
extra leading silence, and removal of one second of existing leading audio.
Originally detected clip 09 became a miss after 20 or 60 ms shifts (scores
0.018/0.019 versus 0.981 originally), exposing separate frame-timing fragility.

Cached local Whisper supplied approximate name-end boundaries. Initial and
cropped/normalized alignment passes were retained separately; normalization
was only for alignment, never for the wake detector. Unmatched alignments
were excluded. Name-start timestamps are unreliable and do not measure actual
word duration or spacing. Context controls preserved the original wake audio
and gain, comparing suffix removal with/without a 5 ms fade, insertion of a
200 ms gap, and suffix-only controls at boundary offsets -40/0/+40 ms.

Spanish clip 03 and earlier fresh clip 23 both passed every gap and prefix
control across these offsets. Gap scores were at least 0.998 and 0.9999,
respectively, versus original 0.0197 and 0.3824. This strongly supports
interference from following speech for these recordings. Suffix-only controls
stayed negative. Clip 01 did not show a robust rescue: one initial hard cut
passed but its faded counterpart failed; all refined-boundary conditions
failed. Its cause remains unresolved; pronunciation coverage is a hypothesis,
not a demonstrated diagnosis. Passing clip 09 also passed all context controls.

Private reports: `connected18-basic-results.json`,
`connected18-context-results.json`, `connected18-refined-context-results.json`,
and the two `connected18-boundaries*.json` alignment reports in the Spanish
recording directory. External scripts `diagnose_connected18.py` and
`diagnose_connected18_refined.py` ran with `--stage basic` / `--stage context`
as applicable, the native openWakeWord Python, existing evaluator PYTHONPATH,
and OS network denial. Alignment used cached Whisper-small with offline flags
and OS network denial; no transcript was retained. Original 40 recording
hashes were reverified; temporary diagnostic WAVs were removed. Baseline
assertions passed in all three diagnostic runs (90, 33, and 44 cases).

Acceptance remains Spanish 8/10 and corrected earlier fresh 29/30. Transformed
rescues do not change either result. No model/threshold/runtime change,
training, provider spending, or additional recording occurred. Recommended
next experiment: synthetic name-to-command transitions with little spacing
and varied onset timing, with a synthetic validation gate before further
household testing. Keep household audio out of training; if these results
guide model selection, label this set development evidence and obtain fresh
acceptance evidence afterward. Clip 01 still requires independent explanation.

Validation: `.venv/bin/ruff check . --fix` and `.venv/bin/ruff format .`
passed (110 files unchanged); `.venv/bin/mypy --pretty --show-error-codes`
passed (60 source files); `.venv/bin/pytest tests/ -q` passed with 863 passed,
1 skipped. External diagnostic/alignment scripts passed Ruff. No application
code or dependency changed. No physical robot or live provider test was run.


### Connected-speech training authorized — 2026-09-08

User approved the targeted experiment. Reuse the local CPU trainer and frozen
candidate 18 as parent, threshold 0.5. Generate 288 synthetic originals with
existing approved voices (Spanish-heavy, name-first commands without commas,
matched rushed/neutral delivery for positives and similar-name negatives).
Freeze the request manifest before generation; cap text at 15,000 characters,
within 16,061 currently available credits, with overage disabled and no
automatic retry of ambiguous paid calls. Preserve prior speaker/owner splits.
Augment training originals with 20/40/60 ms onset shifts, speed and gain
variation; never augment validation into training. First candidate: 4,000
steps, ordered CNN, parent 18, existing negative mining and corpus retained.
Run locally with no network and synthetic-only mount; no Runpod rental needed.

Before household screening require old synthetic recall >=90% per variant
and <=1% negative activation; require new connected validation recall >=90%
per language and <=1% negative activation, plus onset-shift robustness checks.
Do not lower threshold to pass. These short synthetic negative fractions are
not household false wakes per hour. Household sets now inform development
and cannot provide independent final acceptance for this refinement. Keep
all household media outside training. Preserve candidate 18 and reports; no
app enablement. Record training/export/evaluation evidence and remaining
budgets before handoff.

Connected batch completed: 288 originals (216 positive, 72 negative), 8,580
text characters, 4,269 actual credits charged. ElevenLabs usage now
18,337/30,129 (11,792 remaining), overage disabled. All receipts and PCM16
16 kHz mono WAVs verified; durations 1.04–4.64 seconds. Feature checksum and
voice/owner separation passed: 7,980 training bags and 368 excluded validation
bags, including 192 new training originals with nine variations each and
96 new validation originals unchanged. Speed augmentation uses resampling
and therefore also changes pitch; onset shifts are separate conditions.

Candidate 18 baseline on the new connected validation: 72/72 positives and
0/24 negatives at original timing; 286/288 positive phase trials and 0/96
negative phase trials across 0/20/40/60 ms. Both misses are Portuguese, one
source at two offsets. This passes the preregistered gate and shows that the
synthetic set only weakly reproduces the household difficulty. Do not infer
that a later synthetic pass establishes resolution of the real misses.
Candidate 19 is training locally with the frozen recipe; no GPU spend.


### Connected-speech experiment complete — candidate 19 — 2026-09-08

Candidate 19 trained locally for 348.46 seconds / 4,000 steps from candidate
18 with the preregistered recipe. ONNX checking passed and the Torch/ONNX
probe difference was zero. Export SHA-256:
`71bbef9b704cce03a5bc6ab7cf17e540803c049e1bc6a9a6083219c1f2a2568f`.
All evaluations kept threshold 0.5 and the existing streaming/reset protocol.

| Check | Candidate 18 | Candidate 19 |
| --- | --- | --- |
| Original 30 development recordings | 27/30 | **30/30** |
| Corrected later 30 development recordings | 29/30 | **30/30** |
| Spanish follow-up | 8/10 | **8/10**, still misses 01/03 |
| Spanish clip 09 across 0/20/40/60 ms onset shifts | 2/4 | **4/4** |
| Older synthetic positives | 400/400 | **400/400** |
| Older synthetic negative activations | 1/3,168 | **7/3,168 (0.221%)** |
| Prior speaker-disjoint positives / negatives | 178/180; 0/60 | **177/180; 0/60** |
| New connected positives / negatives across four phases | 286/288; 0/96 | **288/288; 0/96** |

The new connected set contains 72 distinct positive and 24 distinct negative
validation originals; phase trials are repeated transforms, not independent
recordings. New per-language/per-phase and older synthetic gates passed before
household checks. Household originals and corrected recordings had no duplicate
events. Candidate 19 scored Spanish 01/03 at 0.0364/0.1355 on unmodified audio;
neither passes at any tested phase. Earlier clip 23 is now detected. These
results support partial improvement, not resolution of the Spanish deficit.
Training changes and seed both differ, so do not attribute every gain to one
augmentation in isolation. The increase in synthetic negatives and one lost
prior speaker-validation detection are explicit regressions.

Preserve candidate 18 and its selection record; candidate 19 is a promising
**development** alternative, not final acceptance or deployment. Record:
external `elevenlabs-training/connected-candidate-selection.json`. Original
30, corrected later 30 and Spanish follow-up now serve development purposes;
none is independent acceptance evidence for this refinement. Do not pool the
stronger sets to hide Spanish 8/10. Further work should first investigate the
synthetic/household mismatch and broader Spanish pronunciation/coarticulation
coverage using synthetic audio, then obtain independent acceptance recordings
only after a candidate improves the weak development session. No additional
training was started after this bounded experiment; no new household recording
is required merely to document this outcome.

Reproduction: existing image `reachy-oww-cpu-preflight:20260907`, `docker run
--rm --network none --cpus 2`, only the synthetic experiment directory mounted
at `/work`, `/work/training-venv/bin/python /work/elevenlabs-training/train.py`
with `--attempt 19 --steps 4000 --positive-weight 10 --negative-weight 10
--learning-rate 0.0001 --architecture cnn_flat --feature-set bags-connected
--include-accent-negatives --include-streaming-positives --parent-attempt 18
--hard-negative-candidates 512 --extra-negative-features remine-candidate12`.
The container exited and was removed. Feature extraction used the native
openWakeWord environment, `extract_connected.py --connected --wait-audio`,
with OS networking denied. Evaluation used that environment and denial with
`evaluate_connected.py --attempt 19`, `evaluate_synthetic.py
<candidate-run-19> --workers 4`, `evaluate_new.py --attempt 19 --diverse`,
`screen_private.py --attempt 19`, and external
`evaluate_connected_household.py --attempt 19`. Reports are in candidate 19
and private household roots; no household report/audio enters the training
mount. All 42 fresh/replacement hashes and candidate 18 were reverified;
original-30 evaluation checked its manifest hashes. Temporary audio removed.

Final costs: **4,269 ElevenLabs credits**, 11,792 remaining; no upgrade needed.
**$0 additional Runpod spend**; cumulative billing remains $1.2201854933 of
$10. Final provider lists contain no pods or network volumes. No runtime
configuration, model installation, application code or dependency changed.
The repository gate passed: Ruff check/format (110 unchanged), mypy (60 source
files), pytest **863 passed, 1 skipped in 18.90 s**. New external extraction and
evaluation scripts passed Ruff with standalone D100/D103/E501 exceptions.
`git diff --check` passed. No hardware, live robot, ambient false-wake-rate or
final acceptance claim is made.


### Candidate 19 domain-gap investigation — 2026-09-08

User authorized investigation of the remaining Spanish misses. Freeze
candidate 19 and threshold 0.5. Compare all ten household Spanish originals
with all 36 connected Spanish positive validation originals under tempo
0.8/1.0/1.2 controls; tempo 1.0 is the processing control. Compare household
gain 2x/4x and synthetic gain 0.03x/0.1x without clipping. Repeat prior
fixed-boundary name/suffix/gap controls on candidate 19. These are diagnostic
transforms only, never acceptance improvements or training material. Use
local inference and ffmpeg with OS networking denied; preserve source hashes,
keep private reports outside training and delete temporary audio. No paid
generation, training, threshold change or deployment in this investigation.


### Candidate 19 timing diagnosis completed — 2026-09-08

All 276 domain-control cases completed (10 household and 36 Spanish synthetic
validation originals, each with six conditions). Original household scores
reproduced exactly. Household recall stayed 8/10 at gains 2x/4x, tempo 1.0
processing control and tempo 1.2, but became 10/10 at tempo 0.8. Synthetic
positives stayed 36/36 in every condition, including gains 0.03x/0.1x and tempo
0.8/1.2. Original median active-frame levels were about -40.2 dBFS household
versus -17.8 dBFS synthetic. Their loudness differs, but these gain controls
do not explain the two misses. Energy-derived activity is not word alignment.

A predeclared follow-up on 01/03 and passing control 09 tested tempo
0.8/0.9/1.0/1.1/1.2 at four onset phases (0/20/40/60 ms), alongside original
and gain controls: 69 cases. Both missed clips passed at tempo 0.8 and 0.9
at **all four phases**; both failed at unchanged speed at all phases. Clip
01 also passed at 1.1, while clip 03 failed at 1.1. Both failed at 1.2.
Control 09 passed all conditions except one phase at 1.2. Thus this is not
a simple monotonic speed limit or instruction to the user to speak slowly.
Evidence supports sensitivity to phonetic timing/context; time-stretching
also alters the waveform, so it does not isolate the exact acoustic cause.

The 44 fixed-boundary context controls reproduced candidate-19 baselines.
Clip 03 passed all gap/prefix/faded-prefix variants across -40/0/+40 ms,
confirming following-speech interference. Clip 01 passed a gap and faded cut
at the central estimate but failed nearby offsets; its exact boundary/cause
remains uncertain. These findings do not prove a specific phoneme deficiency.

Next proposed training experiment: pitch-preserving tempo augmentation with
broader timing coverage, rather than relying only on existing resampling
(which changes pitch too), and synthetic stress examples that actually fail
the frozen candidate before training. Keep positive/negative augmentation
matched and speaker-disjoint validation. Do not add slowed private recordings
to training or runtime preprocessing: the former breaks the privacy boundary,
the latter would require latency and false-wake validation. No new generation,
training or runtime change was performed in this diagnostic turn.

Reports remain private: `candidate19-domain-diagnostic.json`,
`candidate19-tempo-phase-diagnostic.json`, and
`connected19-refined-context-results.json` in the Spanish recording directory.
Reproduce with external `diagnose_gap19.py`, `diagnose_tempo_phase19.py`, and
`diagnose_connected19_refined.py --stage context`, native openWakeWord Python,
existing evaluator/vendor PYTHONPATH and `sandbox-exec -p '(version 1)(allow
default)(deny network*)'`. ffmpeg `atempo` supplied tempo controls; no downloads
or new dependencies. Original Spanish hashes verified, temporary audio removed.
The real Spanish result remains **8/10**, not the transformed 10/10.

Validation: full gate passed again (Ruff check/format, mypy; pytest **863
passed, 1 skipped in 17.50 s**). External diagnostic scripts passed Ruff with
standalone D100/D103/E501 exceptions. `git diff --check` passed. No spending,
provider, robot, ambient false-wake-rate or final acceptance claim.


### Pitch-preserving timing refinement authorized — 2026-09-08

User approved the next experiment. Reuse the 288 synthetic connected originals
only; freeze tempo 0.75/1.25/1.5 with 20/40/60 ms onset shifts respectively,
applied equally to positive and negative examples using ffmpeg atempo. Preserve
voice/owner/source splits and receipts. First screen candidate 19 on synthetic
training stress examples to confirm measurable failures before training.
No private audio, paid generation or rental. Candidate 20: parent 19, 4,000
steps, ordered CNN and existing 10/10 weights/negative mining; append timing
training bags to prior corpus. Threshold stays 0.5. Require >=90% recall per
language/tempo and <=1% negative activation on held-out synthetic voices,
plus prior connected/older synthetic gates, before private development checks.
Stress transforms are correlated trials, not independent speech recordings.
If stress gate fails, diagnose synthetic-only before further household checks.
Preserve candidates 18/19; no runtime preprocessing or enablement.

Tempo corpus: 864 derived synthetic clips, 576 training and 288 excluded
validation variants. Training stress screen exposes 11/432 missed positives
(seven Spanish) and 1/144 negative activation; validation has 214/216
positives and 2/72 negative activations. Candidate 19 fails the stress gate
on negative activations. These fixed stress rates are not measured household
speech rates. Source/derived hashes and all split exclusions passed.
Feature archive contains 9,708 training bags plus 368 prior validation bags;
all 288 tempo validation variants are excluded from feature training.
Independent 220 Hz tone check confirms pitch preservation (220.02 Hz),
60 ms leading silence and expected duration within 50 ms. Candidate 20
training started locally with network disabled, no provider spending.


### Pitch-preserving refinement complete — candidate 20 — 2026-09-08

Candidate 20 trained for 359.50 seconds / 4,000 steps locally, parent 19,
ordered CNN, bags-tempo, positive/negative weights 10/10, learning rate
0.0001, prior streaming positives/accent negatives/remine-candidate12 and
512 hard-negative candidates. Threshold remains 0.5. Export checking passed;
Torch/ONNX probe difference zero. SHA-256:
`aa3a71529505aa735ebb9149e76a42d6838f4152df9288adf4046a0ae6259beb`.

| Check | Candidate 19 | Candidate 20 |
| --- | --- | --- |
| Pitch-preserving validation positives / negatives | 214/216; 2/72 | **216/216; 0/72** |
| Connected phase positives / negatives | 288/288; 0/96 | **288/288; 0/96** |
| Older synthetic positives / negatives | 400/400; 7/3,168 | **400/400; 13/3,168** |
| Prior separate-voice positives / negatives | 177/180; 0/60 | **177/180; 0/60** |
| Original 30 development recordings | 30/30 | **30/30** |
| Corrected later 30 development recordings | 30/30 | **30/30** |
| Spanish follow-up originals | 8/10 | **9/10** |
| Spanish at each 20/40/60 ms onset shift | 8/10 | **9/10 at each phase** |

All synthetic gates passed before household screening. Spanish clip 01 now
detects at every tested phase; its original score is 0.5441, only narrowly
above 0.5 (shifted scores 0.7102/0.7523/0.8556). Clip 03 remains missed:
original 0.1917, shifted 0.0570/0.1559/0.4650. No duplicate household events.
Synthetic negative activation increased to **0.410%**, below the preregistered
1% limit but a clear regression from candidate 19. Short synthetic clip counts
are not real-world false wakes per hour. Do not hide this cost of improved
recall, or claim that the remaining miss is fixed. Training seed differs too;
this experiment does not isolate every effect to tempo augmentation alone.

Candidate 20 is the preferred development alternative in external
`elevenlabs-training/tempo-candidate-selection.json`; candidates 18/19 and their
selection history remain unchanged. The weak Spanish session now reaches
its 90% numeric target, but these reused sets cannot establish independent
acceptance. Next: fresh, independent labeled household acceptance following
the existing multi-session/speaker requirements, then negative screening only
when that positive gate passes. Do not keep training to the last known clip
and relabel the resulting score independent. No model installation, threshold
change, runtime audio slowdown or enablement occurred.

Reproduction uses existing local Docker image, `--rm --network none --cpus 2`,
synthetic experiment mount only, `/work/training-venv/bin/python
/work/elevenlabs-training/train.py --attempt 20 --steps 4000 --positive-weight
10 --negative-weight 10 --learning-rate 0.0001 --architecture cnn_flat
--feature-set bags-tempo --include-accent-negatives --include-streaming-positives
--parent-attempt 19 --hard-negative-candidates 512 --extra-negative-features
remine-candidate12`. Native local feature/evaluation commands with OS network
denial: `prepare_tempo.py`, `evaluate_tempo.py --attempt 19 --split train`,
`evaluate_tempo.py --attempt 19`, `extract_tempo.py`,
`evaluate_tempo.py --attempt 20`, `evaluate_connected.py --attempt 20`,
`evaluate_synthetic.py <candidate-run-20> --workers 4`,
`evaluate_new.py --attempt 20 --diverse`, `screen_private.py --attempt 20`,
and private-root `evaluate_connected_household.py --attempt 20`.
Reports stay in the candidate directory or private root as appropriate.
The container exited and was removed; temporary scoring WAVs were removed.
All 42 fresh/replacement hashes were rechecked; original-30 scoring verified
manifest hashes. Prior model hashes are unchanged. The retained synthetic
corpus/archive is reproducible; local free space is under 1 GB, so another
large corpus should wait for separately authorized storage cleanup.

Cost this turn: **zero ElevenLabs credits and $0 Runpod**; no provider calls
or new dependencies. Full gate passed (Ruff check/format, mypy; **863 passed,
1 skipped in 16.99 s**). External generation/extraction/evaluation scripts
passed Ruff with standalone D100/D103/E501 exceptions. Pitch/delay smoke and
all source/derived hashes and validation exclusions passed; `git diff --check`
passed. No robot, ambient false-wake or final acceptance claim.


### Fresh Spanish acceptance batch — 2026-09-08

User supplied ten new Spanish recordings, speaker 1 at approximately 40 cm.
Frozen candidate 20 (SHA-256
`aa3a71529505aa735ebb9149e76a42d6838f4152df9288adf4046a0ae6259beb`),
threshold 0.5, detected **8/10**, zero duplicate events. Misses 02/09 scored
0.2175/0.0802. This fresh batch fails the 90% Spanish target; final acceptance
remains unmet. Do not pool with prior development recordings or proceed to
negative acceptance/enablement. The result shows the prior improvements have
not yet generalized sufficiently to fresh Spanish recordings.

All ten files passed mono 16 kHz PCM16, finite samples, duration and hash
checks; duration 7.965–8.115 seconds, no clipping. Missed-file RMS values
146.0/100.1 fall within the detected-file range 81.4–227.7; this alone does
not establish a cause or verify pronunciation. Original hashes unchanged.
No audio edits, threshold sweep, model comparison, training, provider spend
or new recording request occurred. Keep this as a failed fresh acceptance
record; any later tuning based on it must explicitly change its role to
development evidence without erasing this result.

Evaluation used external `evaluate_acceptance20.py` with the existing native
openWakeWord environment, evaluator/vendor PYTHONPATH and OS network denial
via `sandbox-exec`. Private reports `candidate20-started.json` and
`candidate20-spanish-results.json` remain in the supplied acceptance folder,
outside the training mount. User-provided speaker/distance metadata is recorded
in both reports; original labels.tsv is unchanged. Next step is investigation
of these failures before requesting more household collection.

Validation for this batch: full repository gate passed (Ruff check/format,
mypy; pytest 863 passed, 1 skipped in 17.73 s). `git diff --check` passed.
No app code/dependency changes or hardware/live-provider validation.


### Fresh acceptance miss diagnosis — 2026-09-08

User authorized local diagnosis of acceptance clips 02/09. Frozen candidate
20, threshold 0.5, original baseline reproduced exactly. Tested all ten clips
under original, 20/40/60 ms leading-silence shifts, 2x/4x gain without clipping,
and pitch-preserving tempo 0.8/0.9/1.0/1.1/1.2: 110 conditions. Derived copies
were temporary; originals and acceptance report remain unchanged.

Clip 02: original 0.2175; +20 ms rises to 0.5143 (narrow pass), +40/+60 ms
remain misses (0.1588/0.1416). No tested gain or tempo rescues it. Clip 09:
original 0.0802; +20/+60 ms detect strongly (0.9882/0.9775), +40 ms misses
(0.0737); tempo 0.8/0.9 detect, unchanged-speed processing control and faster
tempos miss. Gain does not rescue it. Originally passing clip 07 fails at
+20 ms (0.3091). Aggregate original/+20/+40/+60 ms: 8/10, 9/10, 8/10, 9/10.
Thus no fixed delay solves the batch; it can trade one missed clip for another.

The speech samples are byte-identical in phase tests; only leading zeroes
change. This directly demonstrates streaming phase sensitivity, not a need to
say the phrases differently. The evaluator feeds 1,280-sample (80 ms) chunks;
the cached upstream frontend also accumulates 1,280 samples before updating
embeddings. This explains why call timing is relevant, but does not isolate
whether the dominant defect is frontend representation or the trained head.
Do not attribute it exclusively to either without further controls. Slowing
helps only one of these misses; no single simple speed/loudness explanation.

Diagnostic union of original and +20 ms detections covers all ten, but is not
a measured production solution: testing multiple alignments can increase false
wakes and CPU cost. Next proposed bounded experiment is synthetic-only testing
of a shared-cooldown, multiple-alignment detector, with the same negative
corpus and CPU/latency checks before any private or runtime use. Keep the
existing model/threshold fixed; do not introduce runtime delays or another
training round based only on these positive diagnostic rescues.

Private report `candidate20-acceptance-timing-diagnostic.json` and its started
record remain in the acceptance directory. Command: private-root
`diagnose_acceptance20.py`, native openWakeWord Python, existing vendor/evaluator
PYTHONPATH, `sandbox-exec -p '(version 1)(allow default)(deny network*)'`.
All ten original hashes verified; temporary audio removed. No provider calls,
spending, training, app code, threshold changes or enablement. The failed fresh
acceptance result remains **8/10**. Any future model/protocol selection using
these diagnostics must label this batch development evidence; do not reuse it
as independent acceptance for a changed detector.

Validation: Ruff check/format and mypy passed; pytest 863 passed, 1 skipped
in 12.80 s. External diagnostic Ruff passed; baseline/hash assertions and
`git diff --check` passed. No hardware or live-provider validation.


### Dual-alignment experiment authorized — 2026-09-08

User approved an offline dual-alignment test. Freeze candidate 20 and
threshold 0.5; compare original vs causal 0/20 ms lanes with a single
three-second cooldown. Replay both lanes independently with identical reset
seeds, align decisions by available-input wall-clock boundary (80 ms), then
combine scores. No lane reads future input. This is an offline prototype,
not app integration. Preserve single-lane scores as a baseline/parity check.

Synthetic gate: existing old, connected, diverse and tempo validation sets;
>=90% positive recall and <=1% negative clip activation per set, with prior
variant gates retained. Count duplicate events using shared cooldown.
After the gate passes, a separate single-process benchmark measures steady
predict cost for both lanes and event-availability changes; do not call this
physical robot latency. Only then replay the two Spanish household sets as
development checks, never refreshed acceptance. The formerly fresh batch
now informed this protocol and is development evidence for its selection.
No training, generation, provider calls, threshold change or app enablement.


### Dual-alignment prototype rejected — 2026-09-08

Causal 0/20 ms lanes, threshold 0.5 and shared three-second cooldown were
replayed on 4,192 synthetic validation fixtures. Both lanes use only samples
available at the current 80 ms decision boundary; independently reset replay
is an offline equivalent of two separate detector states, not a live app test.
Single-lane events and maximum scores matched saved baselines within 1e-6
for all fixtures. All emitted event separations met the 48,000-sample cooldown.

| Set | Positives single / dual | Negative activations single / dual |
| --- | --- | --- |
| Older synthetic | 400/400; 400/400 | 13/3,168; **48/3,168** |
| Tempo stress | 216/216; 216/216 | 0/72; **2/72** |
| Connected originals | 72/72; 72/72 | 0/24; 0/24 |
| Prior separate voices | 177/180; 177/180 | 0/60; 0/60 |

Zero duplicate positive events in both modes. Dual negative activation rises
to **1.515%** on the older set and **2.778%** on tempo stress: both violate
the predeclared <=1% limit. Reject the simple maximum-score/OR combination.
No private dual screening followed this failed synthetic gate. Do not claim
the prior diagnostic union as an acceptance pass, or lower standards because
it appeared to rescue known household misses.

The user also requested processing cost, so a separate warmed single-process
synthetic benchmark was completed despite the accuracy rejection, solely to
finish that measurement (not to reopen the failed gate). On 24 connected
positive clips / 115.76 seconds including protocol padding, summed predict
wall time was 2.821 seconds single vs 5.631 seconds dual (~2.00x). This is
2.44% vs 4.86% of padded audio duration. Maximum per-clip p99 chunk compute
was 9.60 vs 14.78 ms, below the 80 ms chunk interval on this Mac. Lane times
were measured sequentially and summed for dual cost; no model-load, reset,
file-I/O, live microphone, scheduler, robot/Pi or end-to-end latency guarantee.
An extra lane uses past audio, not lookahead, but its processing cost still
matters. False activations, not measured Mac throughput, reject this variant.

Artifacts remain external: `candidate-run-20/dual20-synthetic.json`,
`dual20-benchmark.json`, their started records and `dual20-decision.json`.
Commands: existing native Python and vendor PYTHONPATH under OS network denial,
`evaluate_dual20.py` and `evaluate_dual20.py --mode benchmark`. The prototype
and candidate 20 are retained; no app integration, model or threshold change,
training, paid generation or provider spend. Fresh single-lane acceptance
remains 8/10. Further multi-alignment work would need independently validated
confirmation logic that reduces false activations; do not enable this OR rule.

Validation: all 4,192 single-lane parity and event-spacing checks passed;
model hash unchanged; external Ruff passed. Full repository gate passed:
Ruff check/format, mypy, pytest **863 passed, 1 skipped in 18.73 s**.
`git diff --check` passed. No live hardware or household negative-rate claim.


### Stricter dual-alignment rules authorized — 2026-09-08

User approved testing stricter evidence before triggering. Freeze candidate20
and lanes0/20ms. Compare four predeclared rules: any lane score >=0.8;
any lane >=0.9; two consecutive >=0.5 frames in the same lane; or >=0.9
OR that repeated evidence. All use one 3sec cooldown and causal decisions.
Do not count alternating lanes as persistence. Keep original single0.5 and
plain dual0.5 as controls. Cache synthetic score traces outside the repo
for auditable rule checks; never put private traces in the training mount.

Screen all existing4192 synthetic validation fixtures. Eligible rules need
>=90% per represented variant/language stratum, old negatives <=13/3168
(no worse than current single lane) and zero activations in each of the
three supplemental negative sets. Among eligible rules choose most positive
detections, then fewest negative activations, then earliest first detection;
fixed tie order strong80,strong90,repeat50,strong90_or_repeat50. Freeze one
rule before private development checks; do not inspect private results to
choose rules. These repeatedly used synthetic sets are development evidence,
not independent acceptance. No training, generation, provider spending or
app enablement. Explicitly report any positive regression or added delay.


### Stricter alignment rules rejected — 2026-09-08

All four predeclared rules were screened on 4,192 synthetic fixtures
(868 positive, 3,324 negative). Candidate 20 and all source audio remained
unchanged. No private recordings were used to choose or assess these rules.
The original single lane detected 865/868 positives with 13 negative clips
activated. No rule satisfied eligibility, so selection is explicitly null.

| Trigger rule | Positive detections | Negative clips activated (old / tempo / other) |
| --- | --- | --- |
| Current single lane >=0.5 | 865/868 | **13** (13 / 0 / 0) |
| Either lane >=0.8 | 865/868 | **28** (26 / 2 / 0) |
| Either lane >=0.9 | 864/868 | **24** (23 / 1 / 0) |
| Same lane >=0.5 for two consecutive frames | 865/868 | **42** (40 / 2 / 0) |
| Either lane >=0.9 OR same-lane repetition | 865/868 | **47** (45 / 2 / 0) |

These scores are model outputs, not calibrated probabilities of correctness.
Even the strongest cutoff has 1/72 tempo-negative activations (1.389%), above
the original 1% gate, as well as more total false activations than the single
lane. Its lost positive is a Portuguese tempo-stress fixture. This is not a
case where a stricter selection criterion alone rejects an otherwise clean
result. Some false activations are strong and persist over multiple frames;
simple confidence/persistence rules do not distinguish them sufficiently.

Median first-event change vs single lane was 0 ms for confidence/hybrid rules
and +80 ms for repetition. P95 was +80 ms for all four; maximum observed
added availability time was 880 ms for >=0.8 and 960 ms for the other rules.
These are offline causal event-timestamp differences on clips detected by
both versions, not microphone-to-robot latency. Two inference passes retain
the roughly doubled compute requirement from the prior benchmark; no new
hardware-throughput claim. No rule was advanced to private screening or app
integration. Keep the single-lane detector and failed 8/10 fresh acceptance
record unchanged; do not claim a usable wake detector from these experiments.
Further work requires better discrimination of confusing audio, not deployment
of one of these rejected rules. No extra household recordings requested here.

Artifacts: external `candidate-run-20/strict20-synthetic.json` (including
synthetic-only score traces), `strict20-selection.json`, and started record.
The raw report's inherited `passed` field evaluates the plain dual control,
not stricter-rule eligibility; the selection report is authoritative. The
script now names this `plain_dual_control_passed` for future runs. Reproduction:
existing native Python/vendor PYTHONPATH under OS network denial,
`evaluate_strict20.py`, followed by local `select_strict20.py`. Selection
freezes the predeclared synthetic-only ordering and records no selected rule.

Validation: all 4,192 single-lane scores and both control event sequences
matched prior reports; all rule events satisfy shared three-second cooldown.
Ruff passed for external scripts. Full repository gate passed: Ruff check/
format, mypy, pytest **863 passed, 1 skipped in 18.59 s**; `git diff --check`
passed. Candidate hash unchanged; no private strict screen, model training,
paid generation, provider spend, app code changes or enablement.


### Explicit household-training authorization — 2026-09-08

User explicitly requested training on their and the other household speaker's
recordings and collecting fresh validation afterward. This supersedes the
previous evaluation-only restriction for the 80 usable sources in the four
existing batches. Preserve historical labels/reports; the new private
`household-adaptation21/features/sources.json` is the authoritative training
role manifest for this experiment. Exclude the two user-reported inaudible
originals from the corrected batch, using their valid replacements. All
variations of each source stay training-only, with no random within-recording
validation split. No household recording remains independent acceptance here.

Train candidate21 from20 locally: 3,000 steps, learning rate0.00005, existing
synthetic corpus/negative mining and weights10/10, plus household bag loss2.0
on four examples per step (two per speaker). Seven household variations:
original, half gain,20/40/60ms delay, pitch-preserving tempo0.85/1.15.
Keep private features/model outside the synthetic/cloud experiment directory;
Docker gets synthetic data read-only, household features read-only and a
separate private output mount, no raw recordings and no networking. No paid
generation or GPU rental. Household-derived features/model must not be
uploaded by existing synthetic-only tooling.

Require existing synthetic gates (>=90% per relevant stratum, <=1% negatives)
before presenting a candidate for fresh recording. Replay old household
recordings only as training-fit checks, never validation. Report remaining
misses/false-activation regressions and freeze a passing candidate before
asking for independent sessions. No model installation/runtime enablement.

First household candidate21: synthetic positives 400/400 old,216/216 tempo,
72/72 connected,177/180 diverse. Negatives23/3168 old and1/72 tempo (others0).
The tempo false activation fails the1% gate; do not advance to fresh recording.
One bounded correction run22: parent21,1500steps, learning rate0.000025,
synthetic negative bag weight20 (from10), positive10, household loss2 unchanged.
Keep training/validation audio split intact; no validation clip is added to
training and no threshold change. Read parent21 through an additional private
read-only model mount; output remains private. Re-evaluate the same gates.

Candidate22 Docker launch failed before training during archive loading
(`LookupError: unknown encoding: cp437`); Docker then reported its overlay
filesystem read-only during cleanup. No candidate22 config/report was written.
Do not restart shared Docker or alter unrelated containers. Retry the same
correction recipe natively under OS network denial, using existing native
Torch2.13.0, ORT1.27.0 and NumPy2.4.2. Install ONNX1.17.0 and protobuf5.29.3
only into private experiment `native-deps` (not app dependencies); package
downloads contain no household data. Explicit legacy ONNX export
`dynamo=False`, opset13, followed by checking/parity gates. This environment
change means no Linux/native numerical equivalence is claimed. Preserve
failed Docker log and separate native log.

Correction22 also fails tempo negatives1/72; old negatives24/3168, positive
counts unchanged from21. Stop the bounded correction effort. To complete
training diagnostics, replay the80 training originals for21/22 even though
their synthetic gates failed; these are training-fit reports only and cannot
override the gate or justify fresh acceptance collection. The evaluator now
permits this explicitly labeled diagnostic while keeping independent household
acceptance false. Do not add the failing validation clip to training.


### Household adaptation completed; negative gate still unmet — 2026-09-08

The80 unique usable household sources now have explicit training roles in
private `household-adaptation21/features/sources.json`. Their560 feature bags
include seven variants per source. All sources and variants remain training;
no random derivative split. The original30, corrected30 and both Spanish10
batches are no longer independent acceptance material for these models.
Original silent fresh clips01/08 stay excluded in favor of valid replacements.
Speaker pools use the existing labels (60 speaker1,20 speaker2); original
second-speaker identity/session labels remain provisional, not independently
verified as a particular person. Sampling balances these pools, not verified
biometric identities. Prior reports and labels remain historical records.

Candidate21 trained3,000 steps locally in519.25s, household loss2.0, synthetic
positive/negative weights10/10, LR0.00005. SHA-256:
`45cb1735ef7e013057ea9fb6cd7a67fdfea71e072e3565fc54a1dd8433a8c051`.
Candidate22 corrected for1,500 steps in633.60s natively after the failed Docker
launch, negative bag weight20 and LR0.000025. SHA-256:
`308765644aa6c41c15b45c9aa225da6dd91449c539cfda11875a624d0d146e05`.
Both exported models passed ONNX checking and Torch/ONNX probe parity (zero
maximum difference). No Linux/native parity claim; the native runtime versions
are recorded above. Candidate21's successful Docker container was removed.
The failed22 cleanup reported read-only Docker storage; Docker was not
restarted/repaired, and unrelated containers were not changed. Only a hung
read-only inspect subprocess from this task was terminated.

| Check | Candidate21 | Candidate22 |
| --- | --- | --- |
| Original30 training fit | 30/30 | 30/30 |
| Corrected30 training fit | 30/30 | 30/30 |
| Earlier Spanish10 training fit | 10/10 | 10/10 |
| Former acceptance Spanish10 training fit | 10/10 | 10/10 |
| Old synthetic positives | 400/400 | 400/400 |
| Old synthetic negative activations | 23/3168 (0.726%) | 24/3168 (0.758%) |
| Tempo positives / negatives | 216/216;1/72 | 216/216;1/72 |
| Connected positives / negatives | 72/72;0/24 | 72/72;0/24 |
| Prior separate-voice positives / negatives | 177/180;0/60 | 177/180;0/60 |

Zero duplicate positive events in these checks. **80/80 is training fit,
not validation.** Both candidates fail the per-set negative gate because
1/72=1.389% exceeds1%. The correction did not improve this; no further
correction attempts or fresh recording request in this experiment. Do not
add the failing validation fixture to training or relax the gate after seeing
results. Preferred artifact for further investigation is21 (fewer older
negative activations, identical fit/positive counts), explicitly not accepted
or enabled; `household-adaptation21/selection.json` records this decision.
Next work should address confusing negative speech using training-side
examples, then obtain genuinely new household sessions for acceptance only
after the synthetic gate is met. No claim of generalization from perfect fit.

All household features, model weights, receipts/configs and training-fit
reports remain under private `household-adaptation21/`, outside the cloud
experiment mount. Successful21 Docker mount audit: `/work`, `/household`,
trainer file read-only; only private `/output` writable; network mode `none`,
no raw-recording mount. Native22 and all household feature/evaluation calls
used OS network denial. No household audio/model uploaded. Original80 hashes
reverified; temporary derived WAVs removed. Existing synthetic corpus was
read-only and its368 feature validation bags excluded from fitting; the
4192 synthetic evaluation fixtures stayed outside training as before.

Reproduction: private-root `prepare_household21.py` with native openWakeWord
Python/vendor PYTHONPATH under sandbox-exec network denial. Existing Docker
image and synthetic read-only mount, private features at `/household`, private
output at `/output`, `train_household.py --attempt 21 --steps 3000
--positive-weight 10 --negative-weight 10 --learning-rate 0.00005 --architecture
cnn_flat --feature-set bags-tempo --include-accent-negatives
--include-streaming-positives --parent-attempt 20 --hard-negative-candidates 512
--extra-negative-features remine-candidate12`. Native22 uses `train_household_native.py`, the same
flags except attempt22,steps1500,negative-weight20,learning-rate0.000025,
parent-attempt21 and `--parent-model <private candidate21 ONNX>`; see saved
configuration and source for exact arguments/paths. Private evaluator:
`household-adaptation21/evaluate.py --attempt 21` / `--attempt 22`, then
`--fit` for training diagnostics; existing native inference and evaluator/vendor
PYTHONPATH, OS network denial. Each candidate retains `synthetic-validation.json`
and `household-training-fit.json`, whose fit flag cannot override a failed gate.

Cost: **$0 Runpod; zero ElevenLabs credits**. Only small local ONNX/protobuf
package dependencies were added in the isolated private native-deps directory;
no app dependency or lockfile changed. Full repository gate passed: Ruff
check/format, mypy, pytest **863 passed,1 skipped in22.44s**. External scripts
passed Ruff; export parity, data hashes/split/role and privacy mount checks
passed. `git diff --check` passed. No app configuration, runtime installation,
robot test, household negative-rate measurement or final acceptance claim.

### Focused training-negative refinement — 2026-09-08

User authorized continuation after restarting Docker. A network-disabled image
smoke check with the existing synthetic mount confirms Python/Torch 2.5.1
works again. Candidate23 starts from21, keeps the threshold0.5, household loss2,
and existing synthetic positive/negative weights10/10. Train1500 steps at
LR0.000025, adding loss3 on32 sampled focused negative windows per step.
Mine eight highest-scoring windows from each of192 training-side connected
negative originals/tempo variants using21. Assert validation voice and owner
exclusion; no validation recording or derivative enters training. Existing
negative caches remain included. Keep new features, model and reports private;
Docker network none, read-only input mounts, private output only writable.
Run all4192 synthetic fixtures and80 training-fit originals; fresh acceptance
is still required after a passing candidate. No provider spending or runtime
change. This is a new bounded experiment following the completed21/22 round.

Candidate23 completed in261.27s; ONNX export probe maximum difference0.
SHA256 `bea05ab6aa1479ed17f5b6f77f4f5e821589687e9a394d2af1afb094388472a1`.
The192 training clips yielded1536 windows, none above0.5 before fitting
(maximum0.003695). Thus this reinforced already-easy negatives rather than
providing actual false-positive training examples. It did not generalize to
the problematic validation voice: same tempo negative remains1/72, score0.9062
(previous21:0.8644). Older negatives increased25/3168 (previous21:23).
Positives unchanged:400/400,216/216,72/72,177/180. Per-set synthetic gate FAIL.
All80 household training originals still detected, zero duplicate positive
events. This is training fit, not fresh acceptance. Reject23, preserve21 as
preferred for further investigation; private selection.json updated.

Next useful experiment needs genuinely harder training-side negatives with
broader voices/acoustics, rather than more loss on this already-rejected Rita
training set. Keep the failing validation source and its voice excluded.
No fresh acceptance recording request until a candidate meets the existing
synthetic gate. No threshold relaxation or runtime enablement.

Reports, trainer source and recipe retained under private candidate-run-23;
focused cache under hard-negatives23. Network-none/read-only Docker inputs
verified, only private output writable; container auto-removed on completion.
OS network-denied mining/evaluation,80 original source hashes unchanged.
No app code/dependency changes or provider charges. External script Ruff passed.
Full repository gate: Ruff check/format, mypy60 files, pytest863 passed,1 skipped
in31.07s. git diff --check passed. No hardware or fresh household acceptance.

### Broader acoustic training-negative mining — 2026-09-08

User authorized harder training examples. Generate1152 offline derivatives of
48 training-side connected negatives: pitch ratios0.85/1/1.15, tempo0.65/0.9/
1.15/1.4, paired gain1+phase0ms or gain0.15+phase40ms. Pitch and tempo are
controlled independently through resampling and atempo. These are acoustic
variations of existing training speakers, not new independent voices. Assert
validation voice/owner exclusion before processing. Mine four top windows per
derivative with candidate21, select the512 highest-scoring windows for focused
negative sampling, and retain the full mining evidence privately. Temporary
WAVs are removed after extraction. No new provider calls or dependencies.

If confusing training windows are found, candidate24 starts from21 using the
same1500-step focused-loss recipe as23, but this harder512-window cache.
Threshold0.5 unchanged. All4192 synthetic tests then80 training-fit checks;
no fresh acceptance claim from reused data. OS network denial for mining and
inference, Docker network none with private/read-only training inputs.

### Candidate24 passes synthetic gates; frozen for fresh acceptance — 2026-09-08

Mining found36 above-threshold windows in12 variants of seven training sources,
mostly pitch ratio1.15 (33 windows). Selected512 of4608 windows for focused
training. This supplies actual model mistakes, unlike23's easy training set.
Private hard-negatives24 retains source hashes, jobs, all scores and selection.
Candidate24 trained1500 steps in201.92s with the unchanged23 recipe and new
focused cache. ONNX probe maximum difference0. SHA256:
`9f3ad43a4ba6d462fc5b8a5e7e64902422fdc2430a7beec43e6857fe07978226`.
After training, selected negative-window fit0/512 activations, maximum0.002582.

All4192 synthetic fixtures completed at fixed threshold0.5:

| Set | Positive detections | Negative activations |
| --- | --- | --- |
| Old synthetic | 400/400 | 14/3168 (0.442%) |
| Tempo | 216/216 | 0/72 |
| Connected | 72/72 | 0/24 |
| Separate-voice diverse | 177/180 | 0/60 |

All configured positive strata and per-set negative limits PASS. Previously
failing Portuguese tempo fixture now rejected; no test source/voice used in
training. Positive counts unchanged from21 while old false activations improve
23 to14. Zero duplicate positive events. Household original30, corrected30,
and both Spanish10 groups remain80/80 training fit, not independent validation.

Freeze24 and threshold0.5 in private candidate-run-24/freeze.json; selection.json
now prefers24 with synthetic_gate_passed true, accepted false and fresh
validation required. Repeated synthetic screening is development evidence,
not a real household false-wake rate. The next step is genuinely fresh positive
recordings from both household speakers across sessions/languages/variants,
using the existing acceptance criteria. Keep new acceptance sources out of
training. Only after the positive gate should household negative exposure
begin. No model/runtime enablement now; preserve prior candidates.

Privacy: Docker network none and all inputs read-only confirmed; output private,
container removed on success. Mining and evaluation under OS network denial.
All80 source hashes unchanged; derivative temporary directories removed. No
provider calls, charges or app dependencies. External mining script Ruff passed.
Full repository gate passed: Ruff check/format, mypy60 files, pytest863 passed,
1 skipped in21.49s. git diff --check passed. No hardware, fresh acceptance,
real-world false-wake rate or end-to-end latency claim.

### Candidate24 first fresh batch — 2026-09-08

User supplied acceptance24-spanish-Ps7W2iKv (Eduardo, Spanish, approximately
40cm) and acceptance24-negative-lunch-8BqDRxx2. Frozen24 SHA and threshold0.5
verified; OS network-denied standard streaming scorer detects9/10, zero
duplicates. Miss reachy-02.wav, prompt “Hola Reachy, ¿cómo estás?”, peak0.177728.
All10 files mono16kHz PCM16 with nonzero signal; miss RMS205.86, no clipping.
This checks signal presence, not perceptual clarity or complete speech capture.
Actual durations about6.2s despite requested7s, consistent with recorder startup;
no clipping/truncation conclusion from duration alone. Keep originals unchanged,
private and excluded from training. Batch90% is below95% overall target; final
household acceptance false, other speakers/sessions/languages still pending.
Do not lower threshold or retrain on this acceptance miss without a separately
explicit role transition and fresh replacement validation.

Lunch recording audited only:1256.288s (20m56s), mono16kHz PCM16. Preserve for
later negative evaluation after positive acceptance; no false-wake count claimed.
It is shorter than the planned30-minute exposure. Private audit/hash and positive
results saved beside originals; evaluator private-root evaluate_acceptance24.py.
No provider spend, runtime change or household upload. Next review clip02 for
recording quality and complete fresh coverage; no negative scoring yet.
Validation for this recording intake: external evaluator Ruff passed; full
repository Ruff/format/mypy passed, pytest863 passed,1 skipped in19.71s;
git diff --check passed. No hardware/live-model test performed.

### Candidate24 fresh miss timing diagnosis — 2026-09-08

User confirms clip02 sounds clear. Diagnostic-only replay of all10 fresh clips
under frozen24 at0.5, OS network denial, original hashes checked. Eleven
conditions/110 fixtures: original; leading silence20/40/60ms; gain2/4 without
clipping; pitch-preserving atempo0.8/0.9/1/1.1/1.2. Unchanged baseline scores
reproduced within1e-6. Private diagnostic report beside acceptance recordings;
derivative WAVs removed. No training, threshold, runtime or acceptance-role change.

Miss02 original0.177728; phase20=0.333282,40=0.882074 (detect),60=0.441236.
Gain2=0.135582 and4=0.087321, both miss. Tempo0.8=0.999598 and0.9=0.986695
both detect;1 control=0.168611 miss;1.1=0.023157 and1.2=0.000187 miss.
All-batch detections: original9/10;phase20/60 both8/10;phase40=10/10;
gain2/4 both9/10;tempo0.8/0.9 both10/10;tempo1/1.1/1.2 all9/10.

Adding40ms of silence preserves the speech exactly yet changes the decision,
confirming streaming timing sensitivity. Slower speech also rescues the miss;
this does not isolate whether prefix, name, or following-word timing dominates.
Louder is not a rescue. Do not blame recording clarity or require unnatural
pauses. The post-hoc10/10 transformations do not replace original9/10 acceptance.
Do not install a40ms delay based on this batch: earlier timing investigations
showed no universal alignment and dual-lane tests increased false positives.
Next model work should broaden training-side positive tempo/phase coverage
while retaining24's hard negatives, then rerun synthetic gates and obtain new
acceptance if this diagnostic batch informs model selection. These fresh clips
remain excluded from fitting; lunch stays unscored pending positive acceptance.
Diagnostic validation: external Ruff passed; full repository Ruff/format/mypy
passed, pytest863 passed,1 skipped in19.38s; git diff --check passed. No provider
spending, household upload, hardware test or new independent acceptance claim.

### Candidate25 positive timing coverage — 2026-09-08

User authorized training-side timing expansion. Keep the same80 authorized
household training sources, add pitch-preserving tempos0.8/0.9/1/1.1/1.2 crossed
with0/20/40/60ms phase; retain original, half-gain and original phase variants.
Total25 bags/source,2000 private bags in features25. All derivatives inherit
training roles; no fresh acceptance24 source or lunch recording is fitted.
Start25 from24 with1500 steps, LR0.000025, same synthetic/household/focused
negative losses as24; retain hard-negatives24 and existing synthetic caches.
Freeze threshold0.5 and streaming protocol. Check4192 synthetic fixtures,80
training originals, then the10 excluded-from-fitting Spanish development clips
only if synthetic gates pass. These10 informed this recipe via diagnosis and
therefore are no longer fresh independent acceptance for25; report replay as
development, never final acceptance. No automatic training-role transition.
All processing local/private under network denial; no provider spending.

Candidate25 preparation blocked by disk capacity before training. After70/80
sources, ffmpeg failed writing the temporary tempo WAV with ENOSPC. No model25
or completed features25 archive produced; original sources untouched, temporary
WAV directory automatically removed. Disk check: approximately140MiB available;
OS swap approximately15GiB used. Do not infer which application caused pressure
or delete unrelated data. Full gate earlier this turn passed863 tests,1 skipped
in17.80s, Ruff/format/mypy clean; no runtime code changed.

Prepared a lower-memory retry: stream each bag into a compressed NPZ rather
than retaining all bags in RAM; require2GiB free before extraction. This retry
has not run; external Ruff passed. Existing features25 contains only source
metadata, so retry can reuse that directory without deleting recordings.
User should free at least5GB for feature preparation and Docker headroom.
No paid compute/upload fallback is authorized for private household data.
Candidate24 remains preserved; lunch still unscored. Candidate25 training and
all model-specific evaluations remain pending.

### Candidate25 resumed and evaluated — 2026-09-08

User freed disk space; verified9.6GiB before retry. Streaming compressed NPZ
completed80 sources/2000 bags, SHA792be840c01e27f53e02b094ccc4a5763d973157b61d9d72a6204b2925954577.
All source hashes unchanged; latest Spanish10 hashes disjoint from training.
Docker network none/read-only inputs verified, only private output writable.
Training1500 steps completed275.46s, export probe maximum difference0.
Candidate25 SHAe45a8c787e584d11d69fe733c0c152e37a7ef867cad779cd071b6cfc20fc1fcc.

Synthetic4192 tests PASS configured limits: positives400/400,216/216,72/72,
177/180; negatives25/3168 (0.789%),0/72,0/24,0/60. Old negative activations
increase from24's14, so this is a recall/false-activation tradeoff, not an
unqualified improvement. Synthetic duplicate events0. Household training
fit80/80, but one duplicate on former acceptance-spanish-CAIwTANw clip07
(two events). Automated fit pass does not erase this regression.

Latest excluded-from-fitting Spanish10 development replay detects10/10 with
zero duplicates. Original miss02 now0.977519 at fixed0.5, versus24's0.177728.
Recipe was informed by diagnosis of this batch; this is development evidence,
not independent acceptance. No fresh clip or derivative was fitted. Preserve
both24 and25; selection prefers25 for further investigation, accepted false,
duplicate_review_required true. Investigate duplicate before fresh acceptance
or enablement; no threshold/cooldown adjustment made. Lunch remains unscored.

No provider spending or app code/config changes. Private artifacts in features25,
candidate-run-25, and candidate25-spanish-results.json beside the development
recordings. External feature/development scripts passed Ruff. Full repository
Ruff/format/mypy passed, pytest863 passed,1 skipped in39.19s; git diff --check
passed. No hardware, independent acceptance, or real household false-wake-rate
claim. Disk blocker resolved; completed features and model preserved.

### Candidate25 duplicate localized — 2026-09-08

Network-denied trace of training source acceptance-spanish-CAIwTANw/reachy-es-07.wav
(duration8.008s) reproduces24's one event and25's two. Times are source-relative
chunk END times:24 event3.96s;25 first1.40s score0.50059754, current-chunk RMS14.65,
second4.44s score0.99998558. The3s cooldown explains why the main event appears
later in25: it becomes eligible3.04s after the early event. Do not interpret
this as proof that main speech was acoustically detected later.

Controlled probes: digital silence0 events for both; original first2s alone
produces0 for24(max0.06707),1 for25(max0.50059754). Muting first2s while preserving
remaining audio yields1 event for both. Whole-source phase20/60 gives1 for25,
phase40 still2;24 remains1 in all phases. Therefore the duplicate arises from
sensitivity to recorded quiet opening audio and timing, not pure digital silence
or persistent activation after the main event. Exact sound identity (breath,
room noise or other content) has not been perceptually established; RMS of the
current chunk does not describe the entire classifier receptive field.

Private duplicate25-diagnostic.json and duplicate25-probes.json preserve the
traces and controls; originals/model/threshold/cooldown unchanged, temporary
WAVs removed. No training or lunch scoring. Next refinement should label verified
non-wake portions of training audio and include them as negatives, preserving
wake recall and synthetic gates. Do not blindly label every first2s segment as
negative: some recordings may contain the wake word there. Candidate25 remains
unaccepted pending correction/fresh independent acceptance; no fixed phase or
threshold adjustment selected based on this example.
Duplicate-diagnostic validation: external scripts Ruff clean after formatting;
full repository Ruff/format/mypy passed, pytest863 passed,1 skipped in20.42s;
git diff --check passed. Zero provider spending; no new hardware validation.

### Candidate26 background-negative correction — 2026-09-08

User authorized autonomous correction/checks. Select first1.5s only from the
older10 training recordings with documented three-second speaking cue, not all
80 sources. Label basis is recording protocol plus conservative low-activity
screen, not perceptual identification: exclude prefixes RMS>=100 or peak>=3000.
Initial strict screen stopped on unexpected activity; revise to explicitly log
and audit excluded sources rather than mislabel their audio. Require at least
three eligible sources. All selected sources were already authorized training;
fresh Spanish10 and lunch remain excluded. Retain all512 hard-negatives24
windows; add eight highest-scoring windows per eligible prefix under gains
0.5/1/2 and phases0/20/40/60. Keep private feature/source audit receipts.

Candidate26 starts25,1000 steps,LR0.00001, existing positive/negative weights
10/10, household loss2 and focused-negative loss3, expanded features25. Freeze
threshold0.5/cooldown3s. Run4192 synthetic checks,80 training originals, and
excluded-from-fitting Spanish10 development replay only after synthetic pass.
No independent acceptance claims; correct any observed regressions before
requesting new recordings. Network-denied local processing, no provider spend.

### Candidate26 correction passes; frozen — 2026-09-08

Nine protocol-labeled quiet prefixes eligible; excluded older clip03 due to
activity. This is protocol-based negative labeling, not a claim of listening
or knowing the sound identity.864 prefix-derived windows, five above threshold
under25, plus512 retained hard negatives produce1376 focused windows.
Cache SHA dda6fef9ad97db93e00984bb454bfa15f70aa2ad91774f35bcc66884c1c43876.
Training completed1000 steps in160.72s, export probe maximum difference0.
Candidate26 SHA c719986539925e0bb11e25a7dd952f958407e8bb8c113c0b6640e517ac444e5b.

Synthetic4192 fixed-protocol tests PASS: positive counts400/400,216/216,72/72,
177/180; negative activations24/3168 (0.758%),0/72,0/24,0/60. Older negatives
improve25 to24, still higher than candidate24's14; do not hide this tradeoff.
Training originals80/80 with zero duplicates, including previously duplicated
older clip07 now one event. Latest Spanish development10/10, zero duplicates.
Focused negative fit0/1376, maximum0.00482. Training and diagnosed development
replays are not independent household acceptance.

Freeze26 at0.5,3s cooldown; private candidate-run-26/freeze.json and selection.json
updated, accepted false, fresh_validation_required true, duplicate review
resolved. Next step requires new household positive recordings across the
planned speaker/session/language coverage. Keep lunch unscored until positive
acceptance. No further training is justified by a remaining observed failure
in this bounded correction; no runtime installation or enablement.

Docker network none/read-only inputs verified; container removed after success.
Private mining/inference under OS network denial,80 source hashes unchanged,
temporary background WAVs removed. No household uploads or provider spending.
External mining/development scripts Ruff clean. Full repository Ruff/format/mypy
passed, pytest863 passed,1 skipped in29.42s; git diff --check passed. No hardware,
real household false-wake-rate or independent acceptance claim.

### Candidate26 fresh Eduardo Spanish batch passes — 2026-09-08

New acceptance26-eduardo-sE0oOY8e:10/10 detections, zero duplicates, no misses.
Frozen26 SHA checked against freeze/report, unchanged threshold0.5 and standard
streaming protocol. Mono16kHz PCM16,7–9.2s duration sanity check, nonzero signal,
recording/source hashes audited and disjoint from80 training originals. User
session metadata:Eduardo,Spanish,approximately40cm,three-second speaking cue.
Private started/results receipts beside recordings; evaluate_fresh26.py uses
OS network denial. This batch is genuinely fresh for26; preserve evaluation
role. Full household acceptance remains false:Adriana and planned independent
session/language/variant coverage still pending. Lunch remains unscored until
positive acceptance. No training, threshold change, provider spending or runtime
enablement; existing model frozen.
Verification: external evaluator Ruff clean; full repository Ruff/format/mypy
passed, pytest863 passed,1 skipped in18.75s; git diff --check passed. No hardware
or negative-exposure validation claimed.

### Candidate26 fresh Adriana Spanish batch passes — 2026-09-08

User explicitly identified acceptance26-eduardo-S0zQVeh5 as Adriana's batch;
folder/session contain copied Eduardo label. Preserve originals and record
speaker override/provenance in evaluation receipts rather than infer identity.
Frozen26 at0.5 passes10/10, zero duplicates, no misses. Same16kHz PCM16/duration/
signal/hash checks and OS network denial as Eduardo batch. Private evaluator
evaluate_fresh26_adriana.py and results beside originals. Combined fresh Spanish
checks20/20 across two adults, zero duplicates. These are one session per speaker,
not completion of all planned independent session/language/variant coverage.
No training, threshold change, enablement or lunch scoring.

User also requested optional child benchmark (age3): evaluation-only, reported
separately, never required for adult/model acceptance. The supplied five-phrase
workflow separates adult demonstration from recording and preserves notes for
adult speech/no response. No child batch received/scored in this intake. Do not
pool child results into adult pass criteria or interpret pronunciation as failure
by the child. All household media remain private.
Intake validation: external evaluator Ruff clean; full repository Ruff/format/
mypy passed, pytest863 passed,1 skipped in13.86s; git diff --check passed. Zero
provider spending; no hardware or negative-exposure result claimed.

### Candidate26 Eduardo session2 multilingual intake — 2026-09-08

acceptance26-session2-NCCmtmvH supplied as Eduardo. All12 files present, notes
contain no skips. Frozen26 standard0.5/3s scoring under OS network denial:
English3/4, Portuguese4/4, Italian4/4; total11/12, zero duplicate triggers.
Miss reachy-01.wav (“Hey Reachy, are you there?”). Format/duration/signal and
hash checks passed; source hashes disjoint from training; freeze SHA verified.
Private started/results receipts beside recordings, evaluate_session2_26.py.
No training/tuning or modified replay in this intake; original result preserved.

Combined fresh adult observations now31/32 overall (96.875%), but English
coverage only3/4 (75%) is below90% stratum target and below10-example minimum.
Do not treat pooled recall as acceptance or drop the miss. Eduardo21/22 across
two sessions; Adriana10/10 in one Spanish session. Further independent coverage
and review of the English miss remain; no enablement or lunch scoring.
Miss01 peak0.00128034 at threshold0.5. Verification: external Ruff clean;
full repository Ruff/format/mypy passed, pytest863 passed,1 skipped in13.23s;
git diff --check passed. No provider spending or hardware validation.

### English session2 miss diagnosis — 2026-09-08

User confirms clip01 sounds clear and asks whether it blocks progress. Diagnostic
replay limited to four English sources,11 conditions/44 fixtures, frozen26 at0.5,
OS network denial and original-hash checks. Original scores reproduced. Clip01
baseline0.00128034;phase20/40/60 remain misses (max0.023762),gain2/4 remain misses
(max0.001466). Pitch-preserving tempo0.8 rescues0.999986;tempo0.9/1/1.1/1.2 remain
misses (all<0.0013). Other three English clips detected throughout; onlytempo0.8
produces4/4. Modified results diagnostic-only; original3/4 remains authoritative.

Evidence shows sensitivity to the tempo transformation, not a simple gain fix;
this does not isolate a specific phoneme/transition or prove user spoke too fast.
Do not retrain or tune based on one miss before planned independent coverage.
Keep26 frozen and continue recording/evaluating planned English samples. Current
English3/4 is not acceptance, but one retained miss could satisfy>=90% once10
examples exist if all six additional examples pass; assess relevant speaker/
language strata and sessions as planned, without selective retries or omitting
failures. Overall31/32 alone does not clear the language gate. No negative lunch
screen or enablement yet. Diagnostics do not require stopping data collection.

Private candidate26-session2-timing-diagnostic.json and started receipt retained;
all derivative audio removed. No fitting or role change for original recordings,
no provider spending, no runtime/threshold/cooldown change.
Diagnostic validation: external Ruff clean; full repository Ruff/format/mypy
passed, pytest863 passed,1 skipped in13.08s; git diff --check passed. No hardware
or end-to-end negative-exposure measurement.

### Candidate26 Adriana session2 passes — 2026-09-08

acceptance26-session2-LGuWK7Vb session metadata agrees with user:Adriana,
session2. All12 clips present, no skipped notes. Frozen26 hash/threshold0.5,
OS network-denied evaluation:English4/4,Portuguese4/4,Italian4/4;12/12, zero
duplicates. File format/duration/signal/hash checks and training-hash exclusion
passed. Private results beside originals; evaluate_session2_26_adriana.py.

Combined genuinely fresh adult results43/44 (97.727%), zero duplicates:
Spanish20/20,English7/8,Portuguese8/8,Italian8/8. Eduardo21/22 and Adriana22/22,
two sessions each. Eduardo English3/4 remains; other language/speaker minimum
counts and third sessions remain incomplete. Do not pool away that miss or
claim completed acceptance. Keep26 frozen, continue planned coverage; no
training/tuning, lunch scoring, provider spend or runtime enablement.
Session2 intake verification: external evaluator Ruff clean; full repository
Ruff/format/mypy passed, pytest863 passed,1 skipped in14.95s; git diff --check
passed. No hardware or household negative-exposure validation claimed.

### Provisional trial scope and lunch scoring — 2026-09-08

User prefers to stop additional positive sessions at43/44 and explicitly
requests lunch scoring. This supersedes waiting for formal positive coverage
for this limited next step: proceed toward provisional household trial only,
not formal acceptance. Preserve unmet session/language minimums and Eduardo
English3/4; no retroactive threshold change. Score the existing20m56s lunch
recording continuously with26 at0.5,3s cooldown, one reset at start,80ms chunks,
1s leading/2s trailing padding. Timestamps are source-relative chunk ends.
Network denial, model/source hash checks; all audio remains private. Report
candidate false wakes pending review for accidentally spoken wake phrases.

Lunch26 continuous result:22 trigger events in1256.288s (20m56s), observed
63.04 events/hour on this recording, conditional on supplied non-wake label.
Not an estimated universal household rate. Scores include strong events; max1.0.
Source-relative trigger seconds:14.04,28.44,75.80,217.16,243.00,300.12,303.96,
310.28,345.16,477.88,526.52,541.88,544.92,550.04,620.12,632.68,711.16,752.52,
755.72,934.20,1244.20,1251.80. Candidate false wakes require review for any
accidentally spoken wake phrase; do not discard events based on speculation.

This result does not support provisional app enablement. Strong positive recall
43/44 did not imply adequate real-conversation rejection. Keep26 disabled;
next review event contexts and improve separate training-negative coverage,
then use independent new conversation for validation. No lunch audio moved into
training. Private candidate26-lunch-results.json and22 six-second review excerpts
(4s before/2s after, clipped at boundaries) in candidate26-trigger-review;
manifest maps excerpts to original offsets. Original audio unchanged, model/
threshold/cooldown unchanged, no provider spend or uploads.

Validation: external scorer Ruff clean; full repository Ruff/format/mypy passed,
pytest863 passed,1 skipped in18.73s; git diff --check passed. Offline stream
benchmark only; no hardware/app end-to-end measurement.

### Lunch confirmed negative; regression diagnosis — 2026-09-08

User confirms no wake phrase occurred anywhere in the lunch recording. Treat
all22 candidate26 events as confirmed false activations for this recording,
not pending accidental-wake review. Preserve source/model/protocol unchanged.
Compare continuous model20 (synthetic-only),24,26 streams under OS network
denial, identical seed/padding/chunks, single reset; no segment-reset bias.
Baseline false activations20:13,24:12,26:22. Synthetic-only model already fails
real-conversation rejection; later fitting worsens it. The synthetic-negative
fixture gates did not predict adequate household conversation performance.

Retrospective26 score-rule diagnostics:threshold0.75=>14,0.9=>13,0.99=>8 events;
require2 consecutive80ms predictions>=0.5=>18,3=>12. These are still excessive;
no rule selected, no positive-recall tradeoff measured for changed rules here.
Strong persistent false detections exist, so not merely one-frame glitches or
borderline confidence. All full-stream traces retained privately in
candidate26-comparison-diagnostic.json; exact semantic/physical sound causing
each event has not been determined. No transcription/upload or audio labeling
by inference. A25 comparison separately isolates the timing-refinement step.

Candidate25 continuous comparison:28 false activations, versus24:12 and26:22.
Thus the24->25 timing-expansion fine-tuning run coincides with the largest
regression;26's quiet-prefix negatives recover six events but do not solve it.
This comparison isolates model versions, not individual causal contributions
of augmentation vs optimizer updates/seed. Do not claim controlled ablation.
Retain24 as lower-false-wake comparison, but12/20m56s still rules out enablement.

Recommended next direction: real conversational training negatives across
speakers/conditions, hard-window mining with positive recall protection, and
independent new conversational validation. Short synthetic negatives and quiet
prefixes are insufficient coverage in these observed runs. Lunch remains
excluded from fitting in this investigation; a future explicit role transition
must acknowledge that this conversation is then development/training material
and obtain independent negatives. Do not cherry-pick a threshold on this lunch
or enable an earlier model merely because it is less bad. Exact triggering
words/noises remain unclassified; all confirmed non-wake by user's annotation.

Evidence: full model traces/results private; network-denied comparisons,
no provider spend or new dependencies. External script Ruff passed; full
repository Ruff/format/mypy passed, pytest863 passed,1 skipped in13.79s;
git diff --check passed. No runtime or model changes; formal/trial acceptance
false. Prior lunch interpretation pending review is superseded by user's
explicit whole-recording negative confirmation.

### Candidate27 real conversational negative training — 2026-09-08

User explicitly started the separate family test recording and authorized the
proposed lunch training. This transitions the entire confirmed-non-wake lunch
source to training-only, superseding earlier evaluation-only status. Historical
receipts remain unchanged; training-role27.json records authorization and source
hash. All derivatives/slices inherit training role. Family recording remains
untouched/evaluation-only; no access to live microphone by this experiment.

Mine continuous lunch streams under26 with gain0.5/1 and phase0/40ms, no resets
between sections. Per stream retain512 highest-scoring windows plus256 seeded
reservoir samples, deduplicated by frame index; keep all1376 previous focused
negatives. Features and metadata private. Parent26, candidate27 train1500 steps
atLR0.00001 with unchanged synthetic/household/focused losses, features25,
threshold0.5/cooldown3s. Check synthetic4192, training positives80 and lunch
training replay, then prior positive batches as development evidence. New family
recording remains independent negative test. No provider spend/upload or runtime
enablement. Negative-role change is explicit; positive sources not added to fit.

### Candidate27 lunch-trained results — 2026-09-08

Four continuous streams each15737 windows; above0.5 counts97/91/93/93 for
(gain0.5,phase0/40) and(gain1,phase0/40). Retained3025 lunch windows plus1376
prior focused negatives. Cache SHAfe2b8070eb93d20714ade29247a3398f3e1d3934ef15c1d7833c3d381d32592b.
Candidate27 trained1500 steps in241.46s, export probe maximum difference0.
Model SHA24f51c4c8ab06ba018f9427eed9643084ad76f50939d442d6b4a5e3b3d4b7dad.

Synthetic4192 gates PASS: positives400/400,216/216,72/72,177/180; negatives
7/3168,0/72,0/24,0/60 (old negatives26:24). Full continuous lunch TRAINING replay
22->1 false trigger, at478.36s,score0.531675. This is training-fit evidence,
not independent negative validation or an accepted real-world rate.
Training positive originals80/80,zero duplicates. Prior adult four-batch replay
10/10,10/10,11/12,12/12=43/44 maintained,zero duplicates. Older diagnosed Spanish
batch acceptance24-spanish-Ps7W2iKv regresses10/10->9/10. Therefore do not claim
all positive development recall preserved; total prior54 clips52/54 vs53/54.
No positive acceptance source or derivative was fitted in27.

Freeze27 at0.5/3s cooldown for independent family-negative evaluation; not ready
for app enablement and not formally accepted. Preserve26 and all earlier models
for comparison. Selection/freeze receipt records older positive regression and
lunch training status. New family recording currently being collected was not
opened or used; user will supply it for independent scoring. Prior frozen26
positive results remain historical; these27 replays are development evidence.
No additional positive or negative tuning during this evaluation.

Private source/model/cache hashes verified;80 training originals unchanged.
Docker network none/read-only inputs verified; private output only writable,
container removed. Local mining/inference under OS network denial; no microphone
access, uploads, provider spend or new dependencies. External scripts Ruff clean;
full repository Ruff/format/mypy passed, pytest863 passed,1 skipped in21.36s;
git diff --check passed. No hardware/runtime enablement or final acceptance.

### Candidate27 untouched family-negative evaluation — 2026-09-08

User supplied fresh-negative-family-tEKsQMGh. Duration1577.1413125s (26m17s),
mono16kHz PCM16; sourceSHA342e042b2a1003c38ddbe61a2285e902e947a914e305fdf6186d4655b4ed90f2.
Source distinct from lunch training, never fitted or opened before frozen27
scoring. Freeze/model hashes verified; continuous single-reset stream with
standard1s/2s padding,80ms chunks,0.5 threshold,3s cooldown,OS network denial.
One event at427.40s (7m07.4s),initial score0.903082,whole-recording peak0.998408.
Observed2.283 events/hour on this recording if confirmed non-wake, not a robust
long-run estimate. No adjustment to protocol/threshold. Family remains untouched
by fitting; candidate26 on lunch is not a controlled same-recording comparison.

Private recording audit, candidate27-family-started/results.json and10s review
excerpt candidate27-trigger-01.wav (422.4–432.4s) with offset receipt saved.
Review needed to distinguish accidental spoken wake phrase from false activation;
no final acceptance/trial enablement yet. Do not retrain on this new test without
explicit future role transition and independent replacement validation.
Full repository Ruff/format/mypy passed, pytest863 passed,1 skipped in16.42s;
external scorer Ruff clean,git diff --check passed. No provider spending,
household upload or hardware/end-to-end runtime test.

### Candidate27 second untouched conversation — 2026-09-08

User confirms prior family event427.4s contains no Reachy:confirmed false wake,
annotation saved separately without overwriting original report. New dinner
fresh-negative-family-wpskmk5l sourceSHAc1c763a818003261107fbd5dc94e254a2beeaab7e3d0b1063b14e683013fe18f,
1597.6843125s (26m38s),mono16kHz PCM16,source distinct from training and first
family. Frozen27 SHA,0.5/3s protocol,continuous reset-once/network-denied stream.
11 trigger events:49.88,231.00,247.08,570.84,619.16,770.28,853.40,1002.20,
1034.68,1293.16,1508.44 seconds. Scores include strong events,max0.999949.
If all non-wake,24.786 false events/hour observed here; exact event labels await
confirmation for this new recording. Previous confirmation covers first family
excerpt only, not automatically this dinner audio.

Two untouched family/dinner recordings total3174.825625s (~52m55s),12 events;
one confirmed false and11 pending label. Do not pool lunch training replay into
independent validation. Substantial session variation; candidate27 not ready
for provisional enablement. Both new recordings remain evaluation-only, no
training-role transition. Private audit/results and11 ten-second excerpts with
offset manifest in dinner candidate27-trigger-review. Next review labels and
broaden conversational training coverage only under authorized scope, retaining
independent validation. No automatic retraining or threshold change.

External scorer Ruff clean; full repository Ruff/format/mypy passed,pytest863
passed,1 skipped in14.41s;git diff --check passed. Zero spending, no audio uploads,
no runtime/model changes or hardware validation.

Dinner label confirmation: user explicitly confirms no Reachy anywhere during
dinner. All11 events are confirmed false activations; alongside first family1,
12 confirmed false activations across3174.825625s (52m55s), about13.61/hour
observed over these two recordings. Supersedes pending labels, not source roles.
Private user-event-confirmation.json records annotation. Both sources remain
evaluation-only; no training authorization inferred from label confirmation.
Candidate27 remains unaccepted and disabled. No model, threshold or runtime
change; no provider spend. Annotation-only update; git diff --check verified.

### Candidate28 broader conversational negatives — 2026-09-08

User approved proposal to add confirmed non-wake dinner to training, retaining
first family recording outside fitting. Explicit role transition documented in
dinner training-role28.json; original metadata/results preserved historically.
All dinner/lunch derivatives remain training. Earlier family is an excluded
reviewed development check, not fresh proof after prior inspection. No additional
recording requested during this bounded experiment.

Parent27; mine dinner continuously atgain0.5/1,phase0/40,512 hardest+256 seeded
reservoir windows each, deduplicate per stream. Retain all hard-negatives27
(lunch+prior negatives). Train28 for1500 steps,LR0.00001,unchanged losses and
features25;threshold0.5,cooldown3s. Gate4192 synthetic fixtures,80 training
positives,54 prior positive development clips, continuous lunch/dinner training
replay and earlier family development stream. No earlier-family or positive
acceptance audio fitted. Local network-denied processing, no provider spending
or runtime change. If no clear improvement, reassess model/verification approach
rather than repeat requests for household recordings.

### Candidate28 rejected; single-head fine-tuning reassessed — 2026-09-08

Dinner mining retains3053 new windows plus4401 prior; focused cache7454 windows,
SHAce2ac6354def9408fdb2ff54e625184bcd9cb1b06a1d6c3881f5b30bf21e3f4f.
Training1500 steps327.61s,ONNX probe difference0. Candidate28 SHA
08a83425bf4dc787a3edd413b6730fbd8aafb0aa1d73557ef2a98ae21be6da68.

Continuous lunch/dinner training replays both0 events, peaks0.184854/0.439736.
Earlier family, excluded from fitting, still1 false event at427.48s,score0.881572
(previous27:427.40s; same acoustic incident). No demonstrated improvement in
this development conversation. Synthetic gates pass: positives400/400,216/216,
72/72,176/180 (one worse than27); negatives3/3168,0/72,0/24,0/60.
Training positives80/80,zero duplicates. Adult four-batch development:10/10,
8/10,11/12,12/12=41/44 versus27's43/44. Adriana Spanish loses two. Older Spanish
development falls9/10->8/10. Total54 development clips49/54 versus27's52/54.
Zero duplicate events in these evaluations. Do not hide recall regression
behind synthetic gate pass or perfect training fit.

Reject28, preserve27 as comparison baseline only; none accepted/enabled.
Selection updated accordingly. Broader conversation training achieved fitting
but not improvement on excluded conversation and harmed observed positive
recall. This does not prove a fundamental model limit; it does justify stopping
this sequence of fine-tuning runs. Next approach should evaluate a separate
local verification stage with existing positive/negative development data,
measuring recall, false wakes and latency before app integration or more user
recording requests. No verifier implemented or claimed validated here; need
assess available local mechanisms and choose a bounded experiment. No change to
threshold/cooldown to fit existing tests. Dinner/lunch remain training-only;
first family excluded from fitting but reviewed development, not untouched
acceptance. New independent validation only after a promising alternative.

All work local/network-denied, Docker input mounts read-only and output private,
container removed.80 original positive hashes unchanged, no raw-family training
mount. Model/source/report hashes retained; no provider spend or dependency/app
changes. Full repository Ruff/format/mypy passed,pytest863 passed,1 skipped in
16.31s; external scripts Ruff clean,git diff --check passed. No hardware/runtime
or final-acceptance claims. No further recording request in this experiment.

### Local second-stage verifier experiment — 2026-09-08

User authorized implementation/evaluation of separate local verification. Existing
cached multilingual openai/whisper-small and native transformers5.16.1/Torch2.13
available; no app dependency or download needed. Existing Spanish sherpa model
was previously0/30, so use independent multilingual ASR mechanism for this
bounded check. Freeze candidate27 first-stage0.5/3s, then inspect3s before each
trigger plus1s future audio with Whisper CPU2 threads. Exact token allowlist:
Reachy/Richie/Ritchie/Richi/Ricci, no supplied transcript prompt or alias tuning
from outcomes. Max64 new tokens, deterministic generation, local-files-only
and OS network denial. No raw transcripts saved/logged; keep only pass/fail,
source hashes, timing and candidate events. One-second audio wait plus measured
compute is extra latency; this is not a live app/hardware latency measurement.

Evaluate54 prior positive clips and13 candidate27 negative triggers across three
conversations. Replay continuous first-stage positive states with standard
padding, then bounded verifier windows, not entire positive phrase hindsight.
No first-stage fallback for missed positives. All evidence is development-only,
including reviewed first-family recording excluded from head fitting. No model
training or new media collection; no app integration until measured benefit.
Artifacts private verifier27-whisper-small. Exact ASR word identity transient;
only allowlist match retained, no semantic transcript output. New independent
acceptance still needed for any selected design.

Whisper-small second-stage result: rejected all13 negative candidate events,
but accepts31/54 positive clips versus52/54 first-stage (21 additional misses).
Per positive batch8/10,7/10,6/12,9/12,1/10. Reject proposed verifier at this fixed
configuration.65 ASR calls: mediancompute1.536s,p952.622s,max3.165s, plus1s audio
lookahead (about2.536s median extra service delay). CPU2-thread offline Mac
measurement includes feature processing/generation, excludes model loading and
real runtime contention/queueing. No appliance latency claim. No transcripts
saved; only decisions/times. No alias expansion or prompt tuning from outcomes.

Bounded diagnostic control: the21 first-stage positive detections rejected by
verifier are re-decoded with full original clips to assess crop/context effects.
This has hindsight and is NOT a candidate live verifier or acceptance result.
Original first-stage misses remain misses; no rescoring model head or fitting.

Full-clip hindsight control rescues only4/21 rejected positives;17 remain
unverified even with complete original audio. Thus short context alone does
not explain rejection. Exact transcript aliases, recognizer proper-name handling
and speech variation are not separately isolated because no raw transcripts were
retained and no post-hoc alias tuning performed. Do not claim ASR universally
cannot work; reject this cached-small-model/exact-alias recipe based on evidence.
Do not substitute hindsight control for causal testing: result remains31/54.
No combined full-clip acceptance statistic selected or applied.

Decision: no second-stage integration, no app changes, no new recording request
for this verifier. Both repeated single-head fine-tuning and this general-ASR
filter have failed their measured tradeoffs. Any next alternative needs a
separately designed benchmark on existing development material; do not promise
that a larger recognizer, different aliases, or a different detector fixes it.
Private verifier27-whisper-small/summary.json accepted false; full control
results preserved separately. Baseline27 remains disabled, unchanged.

Validation:54 source records/52 real candidate events and13 confirmed false
events,65 actual verification calls. Cached Whisper weight hash recorded; local
files only and OS network denial throughout. Private transcripts transient only;
no new audio exports, uploads, dependencies or provider charges. External
scripts Ruff clean; full repository Ruff/format/mypy passed,pytest863 passed,
1 skipped in19.77s;git diff --check passed. CPU benchmark only, no hardware/app
latency, formal acceptance, or zero-false-wake production claim.

### Pipeline review and revised direction — 2026-09-08

Scope: review existing private training, mining and scoring code and recorded
failures before another training run. No new model, uploads, dependencies or
recording request. Candidate27 remains a comparison baseline, disabled.

Findings, with confidence separated from hypotheses:

1. **Positive location is not supervised.** `prepare_household25.py` stores
   every second streaming window from the entire padded utterance. The trainer
   uses the maximum prediction across that bag as its positive loss. Any one
   window can satisfy the label; the loss does not require that window to
   contain Reachy. `evaluate_synthetic.py::score_fixture` likewise counts any
   event in the clip as a detection. Thus historical clip recall remains valid
   as a clip statistic, but does not establish correct keyword timing. This is
   a confirmed protocol gap, not proof that every successful detection is wrong.
   Existing synthetic context features do use known wake boundaries; the gap
   specifically remains in whole-utterance bags and unlocalized scoring.
2. **Augmentation is not independent coverage.** The household manifest has80
   original sources; features25 makes25 variants per source. Sequential training
   keeps the same sources and warm-starts the previous head. Dinner28 removed
   training-conversation events while excluded-family false activation persisted
   and positive development fell52/54 to49/54. This supports poor generalization
   for that recipe; it does not prove an architectural ceiling or unsuitable name.
3. **Negative coverage and selection are narrow.** Dinner mining retains top512
   plus reservoir256 windows for each of four gain/phase streams, then combines
   them with earlier mined windows. These overlapping windows are not thousands
   of independent conversations. The focused batch samples uniformly from the
   combined cache, without per-conversation balancing. Candidate28 contains3053
   dinner windows of7454 (~41%); adding a session changes the effective mix.
   The trainer saves the final fixed-step model, with no intermediate checkpoint
   selection against excluded conversation performance. No causal attribution
   to one loss weight or architecture follows from these coupled changes.
4. **Bounded extraction check.** Compare the public Model.predict path with
   AudioFeatures plus the same classifier after identical reset/padding and
   five-frame startup suppression. Check one original positive and the first15s
   of the excluded family recording. Rebuild the first original feature bag.
   Private receipt: pipeline-audit-20260908.json and feature-parity receipt.
   Initial overly strict1e-6 cache comparison failed: ncpu1 versus original
   ncpu2 differs by maximum0.0000400543; original ncpu2 reproduces exactly.
   This identifies thread-dependent numerical variation, not demonstrated
   material score failure. This limited check cannot certify all cached features,
   exports, phase sensitivity or the eventual microphone/runtime path.
5. **Current acceptance evidence is exhausted for model selection.** The54
   positives and excluded first-family conversation are development evidence;
   lunch/dinner are training. Their historical untouched results remain recorded,
   but repeated reuse cannot establish fresh acceptance for a new candidate.

Upstream context: [openWakeWord training and evaluation documentation](https://github.com/dscripka/openWakeWord#training-new-models)
reports approximately30,000 hours of negative audio for included models and
continuous far-field evaluation. This is evidence of the importance of broad
negative coverage, not a requirement that this project collect that many hours.
Its documented English support also gives no guarantee for our multilingual
case, and does not establish a multilingual impossibility.

Decision: improve localization and the experiment design before another head
or ASR experiment. Do not proceed with a blind candidate29, a larger Whisper
purchase, a voice-identity verifier, or a forced wake-phrase change. A voice
filter alone does not directly address confusing speech by the same household.
No evidence establishes that Reachy itself must change.

Next implementation order (pending, not claimed completed):

1. Establish wake start/end annotations on the existing authorized training
   positives, with source hashes and uncertainty flags. Use local alignment only
   as a proposal; do not use the current detector's peak as ground truth. Keep
   ambiguous intervals excluded from strong window labels. No fresh recording
   needed. Preserve neighboring connected speech rather than hard-cutting words.
2. Add timing-aware development scoring: retain historical clip recall, but
   separately report events before the word, matched events, late events and
   duplicates. Select the permitted response interval before examining scores.
   Build speech-context replays from existing authorized audio with known insert
   positions, preserving source roles. Synthetic splices are diagnostic only.
3. Train a bounded controlled comparison: same backbone/head, fixed source split,
   seed, compute and sampling; compare existing bag loss with localized positive
   supervision and per-session-balanced negatives. Use original sources as split
   units before augmentation. Keep all later positive sources and the first
   family conversation excluded from fitting. Compare multiple fixed checkpoints
   on development recall AND continuous false events, not synthetic gates alone.
4. A new candidate must preserve at least baseline52/54 clip detections, add no
   per-batch loss or duplicates, improve excluded-family behavior, and pass timing
   checks before proposing fresh acceptance. These are development screens only.
   If localization cannot be established or this controlled comparison fails,
   stop and assess a different keyword-specific representation; do not continue
   serial fine-tuning or request more household data without measured benefit.

Risks: uncertain word boundaries, correlated splices, limited session diversity,
selection overfitting and phase sensitivity. No current hardware, runtime or
production false-wake-rate claim. No app behavior or architecture change.

Review validation: native network-denied streaming comparison passed335 frames
(110 positive,225 conversation-prefix) with maximum score difference0. The
first50-window training bag reproduced exactly with ncpu2; all80 source hashes
in the manifest are unique. This is a bounded check, not full-corpus parity.
An intermediate diagnostic wrapper expected an array but received a list;
normalizing that return shape fixed the audit harness, with no detector change.
Full gate: `.venv/bin/ruff check . --fix && .venv/bin/ruff format . &&
.venv/bin/mypy --pretty --show-error-codes && .venv/bin/pytest tests/ -q` passed:
110 files unchanged by formatting,60 source files type-checked,863 tests passed,
1 skipped in13.87s. `git diff --check` passed. No implementation bug fixed in
this review, so no new regression test added; existing runtime unchanged.

### Localization implementation started — 2026-09-08

User authorized implementation and Luna/max delegation for bounded work.
Private localization29 tooling uses existing cached multilingual Whisper-small
for unprompted word timestamp proposals on80 training and54 development
originals. No candidate peak used for localization; no conversation transcription.
All proposals remain unreviewed and excluded from strong labels until independent
boundary review. No transcript text retained or emitted. Original sources/hash
roles preserved; no new dependencies, uploads, provider spend or app integration.

Freeze timing protocol before scoring: source-relative trigger chunk-end time;
early before annotated word end, matched from word end through end+1.0s
inclusive, late afterwards, additional matched events counted as duplicates.
This conservative whole-word rule is an operational choice, not proof that an
early recognizer response is acoustically impossible. Preserve clip recall
separately; uncertain annotations cannot enter validated timing statistics.

Implementation detail: private `localization29/timing_score.py` exposes
WakeAnnotation, score_timing and require_reviewed_annotation. It rejects
unreviewed/uncertain/undocumented/hash-mismatched strong labels. Clip detection
remains available independently; missing timing is null when unvalidated.
Negative source-relative event times from benchmark padding remain valid early
events.16 focused tests cover edges, duplicates, uncertainty and source checks.

`comparison-design.json` freezes a2x2 design (whole-clip versus localized
supervision, uniform versus session-balanced negatives), same parent27,
seed20260929, learning rate1e-5 and500/1000/1500-step checkpoints. At most4
training runs. This is a design artifact, not an implemented/trained candidate.
All arms must use the same eligible original sources; do not compare a smaller
localized subset against all80 positives. No new holdout claim for sources
already seen by the parent. Existing later54/first-family remain excluded from
fitting and development-only. The original training stop condition still
applies if independent localization cannot be established.

Independent inventory found0/80 reviewed keyword intervals. Historical ASR
boundary estimates and synthetic insertion positions do not supply reviewed
boundaries for real speech. The original30's older evaluation-only manifests
remain historical: explicit user authorization already transitioned all80
usable sources to training, so this is NOT a new permission blocker.

Current stop is label quality: proposals are not verified localization.
The comparison remains unrun under the plan's predeclared stop condition.
Human listening/boundary review is needed before strong labels are available;
no new recordings or cloud provider are required. A local review utility is
being prepared, with explicit uncertain status and no automatic promotion.

Completed proposal pass:134/134 source hashes rechecked;80 training and54
development, all unique.88 clips have one usable unprompted alias interval,
46 have none, none have multiple. Training portion57/80 proposals;23 unresolved.
Reviewed intervals0, strong training labels0, validated timing clips0.
Historical baseline clip detections remain52/54. `summary.json` and
`development-timing-status.json` preserve exclusions rather than inventing
localization accuracy. All actual ASR processing ran with OS network denial
and cached files only. No raw transcript output retained.

Code review fixed owner-only artifact permissions, bounded network probe,
strict source-role/count/boolean and finite ordered interval checks. Mock
134-source summary and nonfinite-event rejection checked independently;
root reran16 timing tests successfully. Full repository gate: Ruff/format/mypy
passed;863 tests passed,1 skipped in18.57s. No simulator/hardware validation
or new model acceptance. Localized training remains unstarted, because
independent labels are unavailable, not because spending or authorization
is missing.

Review UI: `localization29/review.html` loads locally selected proposal JSONL
and WAV files, verifies source SHA-256 using Web Crypto, permits full/segment
playback and explicit reviewer/basis/uncertainty export. It has no external
libraries and CSP connect-src none. It does not infer review from ASR or detector
results. Export schema matches WakeAnnotation; metadata presence cannot itself
prove that a person listened. Use explicit independent listening for labels.

Browser preview limitation: the browser tool blocked file:// navigation by
security policy. No alternate browser/server workaround attempted. Browser
playback and download flow remain unverified; static review/syntax checks only.
This is not a request for upload permission and does not require new audio.

Reviewer completed and statically reviewed: audio duration is required before
export, edited boundaries clear review state, and asynchronous verification
callbacks are tied to the selected source/token. File mode0600. Extracted JS
passed `node --check`; no browser playback/download verification claimed.
Root final external Ruff/format checks passed; timing tests16 passed.
`git diff --check` passed. No training container or ASR job remains running.

Operational handoff: open private localization29/review.html locally, load
boundary-proposals.jsonl, select a record and its matching original WAV,
listen/check start/end, explicitly mark reviewed or uncertain, and download
wake-annotations-reviewed.json. Changes stay in page memory until download;
the page does not currently import/resume prior review exports. Preserve each
export for later hash-based merging; do not overwrite earlier review work.
No need to review all134 in one sitting. Uncertain rows stay excluded.
Only reviewed training-role rows can enter fitting; development-role rows
remain excluded. Future trainer implementation and controlled runs are pending,
not silently considered complete.

### Reduce manual labeling with known-phrase alignment — 2026-09-08

User rejected134-file manual workflow and authorized automatic alignment plus
a small spot-check. Supersedes the all-reviewed-label workflow for this bounded
experiment: automatic labels must be named pseudo-labels, never relabeled as
human reviewed. Existing strong-label gate stays intact. Only80 authorized
training sources processed;54 development sources remain separate.

Test cached Whisper teacher-forced cross-attention/DTW with supplied phrases,
following the upstream alignment approach, not a free transcription pass.
Compare Reachy/Richie spellings; reject unstable, degenerate or low-support
intervals using private automatic-alignment-policy.json fixed before results.
Independent small spot-check still needed to estimate usefulness; stability
alone cannot prove correctness. Exclude failed sources from this experiment
instead of assigning a large mandatory labeling queue. At most8 spot-check
clips; no new recordings, provider charges, dependencies or private uploads.

Known-phrase pass complete:80/80 supplied phrases/hash-checked sources;
30 original user-ordered phrases and50 TSV matches including replacements.
No missing phrases. Teacher-forced cached Whisper attention/DTW produces two
spelling variants/source after an activity-based crop with100ms margins.
Audio encoder reused across spelling passes to avoid repeated work; interrupted
initial slow pass resumed from a hash-checked completed prefix. Method follows
[Whisper alignment](https://github.com/openai/whisper/blob/main/whisper/timing.py),
using installed Transformers alignment helpers. No dependency/download needed.
Activity cropping and token support are heuristics, not word-identity proof.

Predeclared screen: both durations0.12–1.4s, start/end spelling differences
<=0.16s, best spelling mean keyword-token probability>=0.05. These thresholds
are not calibrated correctness probabilities.73/80 pass;7 excluded from the
localized experiment without mandatory manual labeling. All remain automatic
pseudo-label candidates, reviewed=false. No inference that73 are verified.
No change to old strong-label gate or development54.

Small check:2 retained sources/language, SHA order within language with second
speaker preference. Eight excerpts cover EN/ES/PT/IT and both speaker pools;
11.76s montage including1s silence after each. Excerpts use exact selected
intervals and original levels, so the listener can report cut names or adjacent
words. Private spotcheck-audio/eight-name-check.wav and numbered manifest.
This is a targeted usability check, not a statistically precise accuracy estimate.
No need for manual timestamp edits or all134-file review. User listening remains
pending before pseudo-label training; failed spot-check means revisit alignment,
not silently accept the other candidates.

Artifacts: known-phrases.json, align_known.py, known-alignment.jsonl,
automatic-alignment-policy.json, select_alignment_sample.py, automatic-screen.json
and known-alignment-receipt.json in private localization29. Roles/hashes retained;
80 original hashes verified, derived audio stays local, no transcribed output
in logs. Provider spend0, no new model trained/enabled. Full repository gate
Ruff/format/mypy passed;863 tests passed,1 skipped in21.17s.

Final check:8 excerpts match the exact original sample intervals; montage and
1s separators verified. Independent code review found no token-index defect.
Canonical source-set and all-source rehash checks added to future/resumed
alignment runs; all80 hashes rechecked now. Executed script snapshot retained
against its receipt hash. Labels remain automatic and unreviewed.

### Controlled pseudo-label training authorized — 2026-09-08

User listened to eight selected name excerpts and replied “all good”. Record
that as an eight-sample check, not73 reviewed boundaries. Proceed with the
previously authorized four-arm local comparison using screened automatic labels.
User confirmation saved privately with sample IDs; no new upload/spending.

All arms share the same eligible source subset,25 augmentations/source, parent27,
seed20260929, optimizer/steps and synthetic sampling. Freeze old hard-negative
mining to parent27 so changed model scores cannot change which old negatives
are selected between arms. Focused negatives use a separate RNG: uniform versus
equal-probability prior/lunch/dinner groups. Localized masks retain chunk ends
from transformed name end through end+0.48s, requiring full name within2s
lookback. Exclude any source that cannot be masked in every variant from ALL
arms. Both interventions isolated by a2x2 design. Checkpoints500/1000/1500.

Compare54 development positives and continuous conversations with baseline27.
Lunch/dinner replays are training fit; firstfamily remains reviewed development
excluded from fitting. No independent acceptance or validated timing claim for
54 development clips (no approved word boundaries for those). Any promising
checkpoint still needs synthetic regression checks and further acceptance
before enablement. No automatic runtime deployment.

The first comparison launch exited137 during loading with no checkpoints or
training log. Docker has8GB available; memory exhaustion is suspected, not
confirmed by an OOM flag. Preserved the failed directory. Replaced resident
padded synthetic features with a hash-verified, read-only packed memory map;
102 sampled bags match the source exactly. Restart produced checkpoints at
approximately4GB container memory. Network mode none and read-only inputs
verified; only output writable. No source media mounted or provider spending.

### Four-arm localization comparison completed — 2026-09-08

All four runs completed1500 steps with checkpoints500/1000/1500. All12 ONNX
hashes/export parity checks passed. Shared frontend evaluation reproduced
baseline27 exactly:52/54 positives, no duplicates, lunch1/family1/dinner11.

| Model/checkpoint | Calls /54 | Double triggers | Lunch* | Family | Dinner* |
|---|---:|---:|---:|---:|---:|
| baseline27 | 52 | 0 | 1 | 1 | 11 |
| control-step-0500 | 50 | 0 | 0 | 1 | 4 |
| control-step-1000 | 50 | 0 | 0 | 1 | 2 |
| control-step-1500 | 51 | 0 | 0 | 1 | 2 |
| localized-step-0500 | 51 | 0 | 1 | 1 | 3 |
| localized-step-1000 | 52 | 0 | 1 | 1 | 3 |
| localized-step-1500 | 51 | 0 | 1 | 1 | 2 |
| balanced-step-0500 | 50 | 0 | 0 | 1 | 3 |
| balanced-step-1000 | 51 | 0 | 0 | 1 | 2 |
| balanced-step-1500 | 51 | 0 | 0 | 1 | 2 |
| localized_balanced-step-0500 | 52 | 0 | 1 | 1 | 3 |
| localized_balanced-step-1000 | 52 | 0 | 1 | 1 | 3 |
| localized_balanced-step-1500 | 51 | 0 | 0 | 1 | 3 |

*Lunch and dinner are training replays. Family is repeatedly reviewed development
excluded from fitting, not independent acceptance. The54 positive clips are also
reused development data. The three52/54 checkpoints preserve every positive batch,
but ALL12 retain the family false wake. None passes the predeclared comparison
rule. Reject this bounded experiment; do not enable, retune thresholds, or start
more blind training rounds. The4192-fixture synthetic regression was not run
because no checkpoint qualified. The prepared evaluator remains unused.

Private evidence: localization29/comparison/{summary.json,results.jsonl,decision.json},
four arm directories, trainer snapshots, model reports and command receipts.
Network denial verified for Docker training and native scoring. No provider cost.
All training containers exited; source audio was not modified. A read-only review
found no blocker for these completed runs. Limitation: some legacy feature inputs
lack per-arm hashes; the read-only preflight mount also includes unused synthetic
audio. Household raw audio was not mounted for training.

Validation: repository `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q` passed;
863 passed,1 skipped in16.98s. Private timing/mask/aggregation tests:20 passed
in0.25s. Root review fixed an aggregation defect before scoring; regression test
and native baseline smoke passed. Private trainer/runner/evaluators/mask/packing
Ruff checks passed. No hardware, live microphone, independent acceptance, or
new timing-accuracy validation was performed.

### Feature-separability diagnostic started — 2026-09-08

User authorized investigating the persistent family false wake and comparing it
with genuine calls. Fit one fixed standardized logistic probe (C=1, max_iter2000)
on existing73 screened training positives and focused negatives; balance positive
sources and prior/lunch/dinner groups. No family or later54 positive fitting.
Evaluate probe alone and same-frame baseline27 AND probe, both at0.5 with3s
cooldown, on the same57 development/replay sources. No threshold sweep. Compare
false-trigger features with nearest known positive/negative training windows.
This tests a classifier hypothesis, not whether features are fundamentally
incapable: failure is inconclusive and success is reused development evidence.
No new dependencies, uploads, provider spending or runtime change. Artifacts
stay private in feature-diagnostic30; diagnostics and full gate recorded at end.

### Feature diagnostic findings — 2026-09-08

Fixed linear probe converged in15 iterations and separates all5579 positive and
7454 negative TRAINING windows. Export logit max difference7.33e-7 from float32
standardization; every training classification identical. Initial1e-8 comparison
was too strict; corrected to1e-6 without changing model settings or decisions.
Baseline full replay again reproduces52/54 and1/1/11 conversation events.

| Diagnostic | Positive clips /54 | Lunch* | Family | Dinner* |
|---|---:|---:|---:|---:|
| Baseline27 |52|1|1|11|
| Linear alone |54|37|43|58|
| Same-frame AND |49|0|0|0|
| Past-only1s event confirmation (exploratory) |52|1|0|0|

All have zero positive duplicate events. Counts mean clip activations, not
approved word-timing accuracy. Lunch/dinner are training replays; family and54
positives remain excluded from fit but repeatedly reviewed development.
The false family frame scores0.9031 on baseline and6.72e-7 on the probe, showing
a usable distinction in current features, not proof of robust generalization.
Nearest-neighbor interpretation changes with normalization: raw cosine favors
positive0.8063 vs negative0.8020; standardized cosine favors negative0.3408 vs
positive0.2739. Do not claim a particular phrase or inherent feature collision.

The three additional same-frame misses have linear events0.64/0.72/0.64s before
baseline events. Tested an EXPLORATORY1s past-only event confirmation after seeing
these development timings: preserve every baseline positive batch,52/54, and
block12/13 false events. No future lookahead; both input event streams retain
independent3s cooldowns. Four causal/cooldown boundary tests pass. This is
development-tuned and not independent acceptance. Full synthetic regression
is being checked before recommending it. No threshold sweep or app enablement.

Negative coverage limitation: all37 linear lunch event windows were absent from
the exact gain1/phase0 training selection. Training-fit perfection does not cover
the remaining conversation. Nine training-positive source hashes also contribute
864 quiet-prefix negative windows; source overlap there is intentional, not
independent generalization. No family/development-positive feature fitting.

### Diagnostic30 disposition — 2026-09-08

Complete: keep the feature-separability finding, reject the confirmation
prototype. Full4192 synthetic fixtures tested under OS network denial. Every
baseline fixture event count matches archived candidate27 exactly.

| Synthetic group | Baseline positives | Confirmed positives | Baseline negative clips | Confirmed negative clips |
|---|---:|---:|---:|---:|
| Older |400/400|400/400|7/3168|5/3168|
| Tempo |216/216|216/216|0/72|0/72|
| Connected |72/72|72/72|0/24|0/24|
| Diverse voices |177/180|134/180|0/60|0/60|

No positive duplicates. Eleven of twelve diverse language/variant strata miss
the90% threshold. The43 additional rejections span all four languages (ES8,
PT10, IT12, EN13). No deployment, promotion, new data, provider spending, or
further automatic training. The current features demonstrably support a
different decision on the stubborn frame; nearest-neighbor metrics alone do
not establish phonetic identity, and no actual spoken phrase was identified.

The next bounded hypothesis is broader confirmation training using EXISTING
synthetic TRAINING positives plus broader training-negative sampling. This is
not started. Keep synthetic validation fixtures, later54 positives and family
conversation excluded from fitting; do not mine them into the training set.
Need both voice coverage and false-event checks before any fresh acceptance.
There is no evidence yet that replacing the feature extractor is necessary.

Private feature-diagnostic30 evidence: design.json, fit.json, probe.npz,
results.jsonl, summary.json, query/training feature arrays and manifests,
neighbors.json, negative-coverage.json, confirmation-replay.json,
synthetic-confirmation.json, decision.json and executed scripts. All files remain
private. The synthetic per-fixture max_score records the strongest CONFIRMED
baseline event, not the maximum over all frames; counts drive decisions.

Validation: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q`: passed,
863 tests passed/1 skipped in13.78s. Private confirmation tests4 passed in0.09s;
private diagnostic/callback/evaluator Ruff checks passed. Native streaming replay
matched all54 per-clip causal decisions. All4192 baseline fixture event counts
matched archived results. Read-only review checked data roles and causal logic.
Two review concerns were disproved by dependency source: threadpool limits apply
in the constructor and openWakeWord suppresses the first5 baseline frames. No
hardware, live microphone, latency, new independent acceptance or word-boundary
accuracy claim. Runtime/configuration unchanged.

### Broadened confirmation31 authorized — 2026-09-08

User requested the next bounded training change. Fit one fixed C1 standardized
linear classifier, retaining the1s causal rule and0.5 thresholds. Positive
weight split equally between existing73 household sources and synthetic TRAIN
voices. Negative weight split across prior/lunch/dinner/synthetic groups.
Use synthetic training bags only (top3 parent27-positive windows>=0.5; negative
top4 plus4 uniform windows). Broaden lunch/dinner training coverage with every
160ms window plus all prior probe30-positive windows. Keep family/later54 and
all synthetic validation excluded. No new data/provider spending/dependencies.
Run57-source replay and4192 synthetic regression; no automatic enablement,
threshold sweep or additional fits. Private artifacts: confirmation31.

### Confirmation31 completed and rejected — 2026-09-08

One C1 fit completed in125 iterations on75,331 windows. Added17,367 synthetic
positive windows and26,925 synthetic negatives; retained5,579 household positives
and prior negatives. Broader lunch/dinner selection added7,920/10,086 windows.
Two synthetic positive bags were skipped because parent27 never reached0.5.
Export max logit difference3.55e-6; every training decision identical.

| Test | Baseline27 | Confirmation30 | Broadened31 |
|---|---:|---:|---:|
| Household development calls |52/54|52/54|49/54|
| Lunch false events (training) |1|1|1|
| Family false events (reviewed development) |1|0|1|
| Dinner false events (training) |11|0|0|
| Older synthetic positives |400/400|400/400|375/400|
| Tempo positives |216/216|216/216|197/216|
| Connected positives |72/72|72/72|66/72|
| Diverse-voice positives |177/180|134/180|150/180|
| Older negative activated clips |7/3168|5/3168|6/3168|

Other synthetic negative groups remain0/72,0/24,0/60. Zero positive duplicates.
Household per-batch detections9/10,9/10,11/12,11/12,9/10; three additional
misses versus baseline. Broader training improves diverse voices relative to30
but fails household preservation, family false-event reduction and synthetic
strata. Reject31; do not enable, sweep thresholds, or continue automatic fits.
This linear confirmation design has not demonstrated an acceptable trade-off.

All57 household baseline source counts and all4192 synthetic baseline fixture
event counts reproduce archived27 exactly. All4192 validation WAVs rehashed;
zero intersection with training-source hashes. Family and later54 remain
excluded from fit. Caveat: trainer embedded excluded-hash list is narrower than
the full validation corpus; external complete-validation-split-audit.json covers
the current run. Future reuse should incorporate that full audit before fitting.
Negative synthetic weighting is group-balanced, not language/voice-balanced.
No provider spending, raw audio upload, runtime/configuration change or new data.

Private evidence: confirmation31/{design.json,train.py,features.json,manifest.json,
fit.json,probe.npz,household-summary.json,synthetic-confirmation.json,decision.json,
complete-validation-split-audit.json} and source-count logs/scripts. Artifacts
retain private permissions. No active training/evaluation process remains.

Validation: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q` passed;
863 passed/1 skipped in15.04s. Four private causal tests passed in0.09s. Private
trainer/evaluators passed Ruff; read-only review verified weights, packing, split
intersection and export. Full57-source and4192-fixture tests ran with OS network
denial. No hardware, live microphone, new independent acceptance or latency
validation. The trial is complete; no replacement model is selected.

### Event-conditioned verifier32 authorized — 2026-09-08

User requested the exact-trigger second-check experiment. One fixed C1 logistic
fit on first actual baseline27 event features from unique synthetic TRAIN
positive WAVs and73 selected household training WAVs. Negatives: existing31
TRAIN negative windows with baseline27>=0.5. This conditions both classes on
detector suspicion instead of ambient windows. Positive event labels inherit
clip labels; no independently verified word timing claim. Keep thresholds0.5
and baseline3s cooldown; eliminate the separate verifier timer/history and
score only the current baseline event. Rehash/guard allvalidation sources
beforefit. Run57 household plus4192 syntheticfixtures. Onefit, no sweep or
automatic enablement; no new audio/dependencies/provider spending. Private
artifacts: event-verifier32.

### Event-conditioned verifier32 completed — 2026-09-08

One C1 fit completed in 9 iterations: 73 household and 1,121 synthetic positive
first-event windows (one of 1,122 synthetic sources had no baseline event),
plus 2 lunch and 234 dinner negative windows. Prior and synthetic negatives
contributed no windows above the baseline threshold. All fitting decisions
match labels; export max logit difference 1.90e-7, identical decisions.
The negative set represents only two source recordings, not 236 independent
examples. Positive candidate labels inherit clip labels, not verified timings.

| Test | Baseline27 | Exact-event32 |
|---|---:|---:|
| Household development calls | 52/54 | 51/54 |
| Lunch false events (training replay) | 1 | 0 |
| Family false events (reviewed development) | 1 | 0 |
| Dinner false events (training replay) | 11 | 0 |
| Older synthetic positives | 400/400 | 400/400 |
| Tempo positives | 216/216 | 216/216 |
| Connected positives | 72/72 | 72/72 |
| Diverse synthetic positives | 177/180 | 176/180 |
| Older synthetic negative activated clips | 7/3168 | 7/3168 |

Other synthetic negatives remain 0/72, 0/24, 0/60. No positive duplicates.
Household per-batch counts: 10/10, 9/10, 11/12, 12/12, 9/10. The additional
miss is Adriana's first Spanish batch clip 06. The diverse Portuguese variant2
stratum falls to 13/15, below the 90% gate. Thus fewer false wakes but no
promotion: the preservation rule and synthetic stratum gate are not met.
Do not enable, change thresholds, or perform more automatic fits. Retain as
experimental evidence, not household acceptance. All conversations are reused;
lunch/dinner are fitting data and family is reviewed development excluded from fit.

Current source guard: all 4,192 validation WAVs rehashed, 3,251 unique hashes,
zero training intersection. All 57 household and 4,192 synthetic baseline event
counts reproduce archived27. At fit time the validated prior audit list was used;
current direct rehash independently confirms it. Keep direct checking on reuse.
The separate verifier has no timer/history: it scores only the frozen baseline's
current event features; rejection still consumes the baseline's 3s cooldown.
No future audio or extra intentional wait; actual runtime latency unmeasured.

Private artifacts: event-verifier32/{design.json,train.py,features.json,
manifest.json,skipped-positive-sources.json,fit.json,probe.npz,
synthetic_callbacks.py,evaluate_household.py,evaluate_synthetic_confirmation.py,
household-summary.json,synthetic-confirmation.json,decision.json,test_events.py}.
All private; no uploads, new dependencies, provider spend, runtime/config change,
or active training/evaluation process. No app integration or model selected.

Validation: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, `.venv/bin/pytest tests/ -q` passed:
863 passed, 1 skipped in 13.94s. Private event tests: 2 passed in 1.36s, proving
only current candidates request verification, rejection preserves baseline
cooldown, and changed source hashes fail before scoring. The first test invocation
found missing threadpoolctl in the app venv; the native audio venv lacks pytest.
Ran existing pytest from the app venv through the native interpreter's appended
site-packages path, without installs. Private scripts pass Ruff; read-only review
confirmed path resolution, splits, causal behavior and the limited negative-source
coverage. No hardware, microphone, independent acceptance or latency test.

### Frozen32 fresh recording workflow — 2026-09-09

User chose a fresh assessment of frozen32 despite the two preservation failures;
this is not runtime admission or a waiver of the synthetic result. Provide one
10-call batch each for Eduardo and Adriana, plus a fresh no-Reachy conversation
(target 30–60 minutes). Freeze both model hashes, thresholds 0.5, baseline 3s
cooldown and current-event-only verification. No tuning or fitting on these
recordings. Assessment is pending; no microphone was started by the agent.

Private `record_acceptance32.py` creates unique mode-700 fresh32 directories,
records mono 16 kHz PCM16 using macOS audio device0, waits 3s before the visible
speaking cue, plays each 8s clip and supports redo/early finish. Retakes stay in
a separate subdirectory; accepted reachy-01..10 WAVs and labels.tsv are the
intake contract. session.json records speaker, distance, hashes, completion and
evaluation-only usage. Conversation stops on Return, with a 60-minute cap,
and asks whether Reachy was spoken before marking the negative label confirmed.
Recordings remain local. Commands use Python3 with positive --speaker Eduardo,
positive --speaker Adriana, and conversation. CLI help, compilation and Ruff
checked; real microphone/playback execution is user-driven and untested here.

Recording workflow validation: full repository Ruff/format/mypy gate passed;
863 tests passed, 1 skipped in 13.46s. No microphone or playback test claimed.

### Fresh32 Eduardo assessment — 2026-09-09

Received fresh32-eduardo-49vsq_lf: session metadata confirms Eduardo, approximately
40 cm, 10 accepted clips, fresh evaluation-only usage and the frozen model hashes.
All accepted root WAVs match labels.tsv, unique hashes, mono 16 kHz PCM16,
nonempty and no verifier training-source overlap. Retakes excluded.

Frozen baseline27 and verifier32 both detect 9/10. No duplicate triggers. Miss:
clip02; baseline produced no event, so the verifier did not reject an otherwise
successful call. ES 3/4, EN 2/2, PT 2/2, IT 2/2. This is one fresh speaker/session
screen, not overall acceptance. Keep model/thresholds unchanged and recordings
excluded from training. Adriana's batch and the fresh negative conversation are
still pending. No automatic retake request, threshold sweep or fit.

Scored using private event-verifier32/evaluate_fresh.py under macOS OS network
denial, with frozen source/model hashes and fail-closed no-overwrite receipts.
Per-file results and intake hashes are beside the recordings in frozen32-results.json
and frozen32-started.json. No upload, provider cost, microphone capture or runtime
enablement. CLI Ruff passed; repository verification recorded below.

Fresh intake validation: Ruff/format/mypy passed; full repository test suite
863 passed, 1 skipped in 14.12s. Accepted durations 6.93–7.19s. Clip02 has
nonzero audio with peak -21.26 dBFS; no perceptual clarity/timing claim inferred.

### Fresh32 Adriana assessment — 2026-09-09


Fresh32 Adriana screen (2026-09-09): baseline27 and frozen verifier32 both detect
9/10, zero duplicates, at approximately 40 cm. Clip04 produced no baseline event.
ES 3/4; EN, PT and IT each 2/2. Combined fresh adult batches: 18/20, zero
duplicates; Spanish 6/8 and other languages each 4/4. Network denied during local
evaluation, no training overlap or parameter changes. Model remains disabled;
earlier preservation failures remain. Only the planned fresh conversation remains
for this assessment. No additional call batches or automatic retakes requested.

Received fresh32-adriana-5urc8fos; complete evaluation-only session, ten accepted
root WAVs, unique source hashes and frozen model hashes verified. Retakes excluded.
Accepted WAVs are mono 16 kHz PCM16, nonempty, durations 7.03–7.20s. Missed clip04
has peak -16.78 dBFS and duration 7.124s; these do not establish perceptual clarity.
Private frozen32-results.json and frozen32-started.json retain results and hashes.
No uploads, spend, fitting, microphone capture or runtime enablement.

Validation: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
`.venv/bin/mypy --pretty --show-error-codes`, and `.venv/bin/pytest tests/ -q`
passed: 863 passed, 1 skipped in 13.74s. No hardware or latency validation.

### Frozen32 fresh assessment decision — 2026-09-09


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

Validation: existing synthetic_callbacks.score_fixture reused unchanged for the
continuous conversation, with format, nonempty audio, session confirmation and
frozen hashes checked before scoring. Full gate: Ruff check/format and mypy
passed; `.venv/bin/pytest tests/ -q`: 863 passed, 1 skipped in 13.17s.
No hardware or latency claims.


### Frozen32 error diagnosis — 2026-09-09

User confirmed none of the three extracted false-wake clips contains Reachy.
Read-only local replay of all 20 fresh positives reproduced every baseline and
verified event count. Private event-verifier32/fresh-signal-diagnostic.json saves
source hashes and frame scores; network denied, no fit or threshold sweep.

Missed Eduardo clip02 baseline maximum is 0.0075389 at source frame end 4.12s;
missed Adriana clip04 maximum is 0.000151068 at 4.36s. Both are far below 0.5:
small sensitivity adjustments cannot recover either. Diagnostic verifier values
at these maxima are 0.8735 and 0.7385, but these are outside its baseline-event
training/use contract and do not establish an independent rescue detector.

Three confirmed false wakes have baseline/verifier scores 0.9249/0.7351,
0.6800/0.8615 and 0.8509/0.7561 at source frame ends 345.72, 524.68 and
1442.04s. The 18 detected fresh calls have first-event verifier scores at least
0.9881; this sample separation does not authorize selecting a new threshold on
the evaluation set or override the earlier rejected genuine call (0.2365).
Scores are classifier outputs, not calibrated probabilities.

Training provenance confirms verifier negative coverage was only lunch (2
windows) and dinner (234 windows); positive sources were included only where
baseline detected them. This selection cannot teach recovery of baseline misses.
Limited independent negative variety is a plausible generalization weakness,
not a proven phonetic explanation of these particular errors. No transcription
or word-level alignment was performed, so the triggering syllables remain unknown.

Recommendation: prioritize improving the main detector's connected-speech
coverage and the verifier's independent negative-source diversity with separate
training data. Any future rescue/parallel detector requires training and evaluation
for that independent role; running the current verifier in parallel is unsupported.
Do not tune on or add these evaluation recordings to training. No new recording
request, automatic training, paid action or runtime change. Frozen32 stays rejected.

Diagnosis validation: all 20 saved event counts reproduced exactly; frozen model
and source hashes checked. Full repository Ruff/format/mypy passed; pytest
863 passed, 1 skipped in 13.40s. No hardware, latency or phonetic-cause claim.

### Model/training research — 2026-09-09

Reviewed current upstream openWakeWord, verifier, microWakeWord and public speech
data documentation against the saved trainer and failed experiments. Recommendation
and bounded two-model design are in [the comparison plan](reachy-wake-detector-comparison.md).
Research complete; training has not started. The proposal changes source diversity
and training targets and tests a different audio representation, without another
verifier loop or new household recording request. Existing evaluation sets remain
out of training; design-informed replay is not fresh acceptance. No spend or
runtime changes.

### Official baseline33 authorized — 2026-09-09

User chose the standard openWakeWord trainer before another custom architecture.
Pinned upstream commit 368c03716d1e92591906a84949bc477f3a834455 in private official33.
Stock Model DNN (32 units, one block), auto_train (50,000 + 5,000 + 5,000 steps,
maximum negative weight 1500, target training proxy 0.2/hour), and export_model
are unmodified. No verifier. Native CPU, OS network denial during private work.

The input adapter reuses cached 16x96 features rather than executing stock Piper
synthesis/augmentation again: 14,379 positive windows from synthetic sources and
73 authorized household sources with prior name-localization masks; 31,664
synthetic negative windows. Stock background batch 1024, adversarial 50, positive
50. Development: 800 positive and 6,336 negative synthetic windows. These are
augmented windows, not independent speakers. Do not describe this as an untouched
end-to-end notebook run: the official network/training algorithm is standard,
but supplied features and memory-bounded native data loaders are the adaptations.

Public background: ten deterministic range-verified blocks totaling 200 nominal
hours from the official ACAV feature file, plus the complete official false-positive
validation feature set. Dataset revision/hashes in background-receipt.json; license
CC BY-NC-SA 4.0, retained private research use. That set is the official trainer's
11.3-hour normalization reference. Its frame-crossing metric is only a training
proxy, not cooldown-aware household events/hour. Training source hashes exclude
protected synthetic/household evaluation sources, including all fresh32 recordings.

Isolated native dependencies: torch/torchaudio 2.5.1, torchmetrics 1.4.3 and
scientific/audio libraries; no app dependency or uv.lock changes. Initial preflight
needed mutagen and scipy 1.14.1 because acoustics imports removed scipy.sph_harm.
No upstream code patch, cloud upload, paid API call or GPU rental. No household
media in Docker. Training currently running; export/replay results pending.

Official33 training finished: 226.64 seconds, CPU, frozen ONNX SHA-256
f94693ce16d970d8ab0524f36854491d7f6067ae0f46b23cea624e86e5928427.
Unmodified auto_train completed all three sequences. Its reported development
recall is 0.85125 and frame-crossing proxy is 21.23894/hour: neither establishes
streaming acceptance. Export parity passed on 256 development feature windows,
maximum absolute score difference 1.64e-5 and identical decisions at 0.5.
Two private adapter tests passed (3.85s), verifying upstream window equivalence
and correct stock-generator class labels. Repository Ruff/format/mypy passed;
863 tests passed, 1 skipped in 14.17s. Frozen evaluation of 4,192 synthetic
fixtures, 74 household call clips and four conversations is running locally.
No new synthetic generation was performed; cached original synthetic and
localized household features are supplied to the official trainer, not the
custom CNN/bag-loss or verifier training code.

### Official33 final result — 2026-09-09

Official33 completed (2026-09-09): stock upstream DNN/auto_train, no verifier,
using cached prepared features and a 200-hour subset of official public background
features. Fresh call replay: Eduardo 10/10, Adriana 7/10 (misses 02/04/06), combined
17/20, zero duplicates. Fresh Spanish 6/8, English 3/4, Portuguese/Italian 4/4
each. Latest confirmed-negative conversation: 1 false event in 49.50 minutes
(1.2122/hour observed), versus 14 for baseline27 and 3 for baseline27+verifier32.
Older household positives: 50/54; lunch/first-family/dinner false events 7/1/4.
Synthetic recall: old 397/400, tempo 158/216, connected 63/72, diverse 119/180.
Old synthetic negatives: 125 events; other synthetic negative groups zero events.

Decision: reject for runtime admission; improved latest-conversation false alarms
do not compensate for Adriana/language and synthetic recall regressions. No model
selected, no follow-up training or thresholds tried, no new recordings requested.
This is the official trainer on supplied features, not a complete rerun of the
official TTS/augmentation pipeline; notably it does not include the later full
ElevenLabs connected-speech bags, which are incompatible with single-window
labels without further preparation. It does not prove standard openWakeWord
cannot meet the target. Any subsequent data preparation must address that gap
without putting evaluation recordings into training.

Private official33 contains pinned source/dependencies, input/background receipts,
training/export hashes, parity result, evaluation-scores.jsonl, evaluation-results.json
and decision.json. All 4,270 replay items completed under OS network denial with
source hash checks: 4,192 synthetic items plus 74 household calls and 4 conversations.
Reused development sources are not independent acceptance. Public feature-corpus
original clip IDs are unavailable; source-level independence within those public
corpora was not independently audited. No paid calls, GPU rental or household
upload. No hardware/latency validation or runtime integration.

### Official34 preparation authorized — 2026-09-09

User authorized one further official-trainer baseline incorporating the later
ElevenLabs training recordings. All 660 positive TRAIN requests from the original,
supplement, connected and diverse request manifests are aligned locally with
cached Whisper-small, comparing Reachy/Richie spellings. Existing validation
requests are excluded from this preparation entirely. Four of the 12 training
voices are reserved for development globally across all request manifests.
Known transcript alignment yields automatic labels, not human-verified boundaries.

Fixed screen before training: spelling boundary delta <=0.16s, union duration
0.12–1.4s, best spelling token probability >=0.05, valid source interval. Retain
0.12s original context on each side; no splicing or synthetic pauses between
name and neighbor. Reject uncertain sources and report coverage; no manual
recording/review batch requested. Local alignment was resumed in two disjoint
shards after 159 completed sources to use available CPU cores, with unchanged
parameters and prefix/hash checks.

Reuse unmodified upstream augment_clips and compute_features_from_generator at
2 seconds, producing 16x96 features. Public simulated RIRs: 24 files from OpenSLR28
(Apache-2.0 listing), eight each from small/medium/large rooms, fetched by verified
ZIP byte ranges; no full archive download. MIT RIR mirror was inspected but not
used because its license is listed unknown. Use separate room files and same-fold
negative speech backgrounds for training/development. Added transformers 4.44.2
to the isolated native training environment; app dependencies unchanged.

An eight-clip native smoke check passed: official augmentation outputs PCM16
(8,32000), official feature extraction outputs (8,16,96). Training remains
local and network-denied, with no verifier or household evaluation fitting.
One predefined fit and frozen replay follow preparation; no GPU rental or paid
generation needed. Alignment, augmentation and final results pending.

Official34 alignment completed: all 660 sources accounted for; 638 accepted and
22 excluded by the preset rules. Accepted TRAIN: Spanish 117, English 95,
Portuguese 91, Italian 91 (394 total); DEV: Spanish 81, English 57, Portuguese
49, Italian 57 (244 total). Every source and accepted crop hash verified.
Frozen augmentation recipe: 24 positive TRAIN rounds, 16 negative TRAIN rounds,
4 DEV rounds, 2-second containers, batch size 64, stock augmentation defaults.
Four whole synthetic voices are DEV-only; public RIR rooms are also split
18 TRAIN / 6 DEV. Only same-fold synthetic negative speech supplies background
noise. Existing official33 prepared features are retained. Adapter tests: 2 passed
(3.87s); full feature generation and the single fit/replay remain pending.

Official34 preparation complete: new positive/negative TRAIN windows 9,456/4,480,
new positive/negative DEV windows 976/576. Combined with official33: TRAIN
23,835/36,144; DEV 1,776/6,912. The 73 household training sources were explicitly
authorized earlier and remain included; protected later evaluation sources remain
excluded. `official34/check_inputs.py` passed exact feature counts, finite arrays,
feature hashes, whole-voice/room split checks, and byte-for-byte comparison of
all 638 crops against their source intervals. Alignment labels are still automatic
estimates. Full repository gate passed: Ruff, format (unchanged), mypy; pytest
863 passed, 1 skipped in 13.19s. No application code changed for official34.

Official34 single stock fit completed in 240.6s on CPU; ONNX SHA-256
`70d2b832bb7b4bc0d98c1fa22491027abf80298856ce79220bed2b3b6946e6c0`.
Export parity passed 256 examples, max absolute error 1.228e-5, identical 0.5
threshold decisions. Independent read-only review found no blocking defect;
recorded automatic-alignment and public-source-ID limitations. The receipt's
legacy official33 aggregate counters are corrected in a separate JSON record;
`official34_counts`, actual array shapes, and verified hashes are authoritative.
Frozen 4,270-item replay started under OS network denial. No runtime change.

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

Official34 preparation limitations: 638/660 automatic alignments accepted (394
TRAIN, 244 DEV). Two-spelling timing agreement and best token probability are
heuristics, not human-verified word boundaries. The 22 exclusions include PT13,
IT5, ES3 and EN1. Accepted pronunciation variants: 213/214/211. Eight TRAIN and
four DEV synthetic voices are disjoint; 18 TRAIN and 6 DEV RIR rooms are disjoint.
The public RIR selection uses [OpenSLR28](https://www.openslr.org/28/), listed
Apache-2.0. Existing public background features retain the prior CC-BY-NC-SA4.0
license and unknown original clip-ID limitation. Isolated alignment dependencies
were recorded in official34/dependencies.txt; application dependencies unchanged.

The old prepared pools still contain the 73 previously authorized household
training sources; no later evaluation source was added. Voice-held-out DEV selects
checkpoints, so this is not a wholly data-only comparison to official33: both
training and internal development composition changed. Improvement on generated
speech therefore does not establish improved household generalization or prove
that any particular architecture is incapable of detecting Reachy.

Private verification commands used the official33 native venv and pinned upstream
source; all private alignment, feature preparation, fit and replay used
`sandbox-exec -p '(version 1)(allow default)(deny network*)'`. Entry points:
`official34/align_sources.py` (two disjoint resumable shards),
`official34/prepare_features.py`, `official34/check_inputs.py`,
`official34/run_training.py`, `official34/check_export.py`,
`official34/evaluate.py`, and `official34/summarize.py`. The evaluator uses the
existing native openWakeWord evaluation venv and unchanged synthetic scoring
helper. Fixed threshold 0.5, cooldown 3s, one fit with stock three-stage
50,000+5,000+5,000-step schedule. Local fit took 240.6s; incremental cloud spend $0.

Two report-only summarization errors (legacy prompt TSV without a language column,
and old conversation rows without duration metadata) were corrected by checking
for the language column and reading WAV headers. Scoring/model inputs were not
changed or rerun. Legacy aggregate counts inherited in input-receipt.json are
clarified in input-receipt-corrections.json, preserving the original fit hash.
Authoritative combined arrays: TRAIN positive23,835/negative36,144; DEV
positive1,776/negative6,912. No pending computation remains for official34.

### Parallel detector diagnostic — 2026-09-09

User requested measured results for running two detectors in parallel. Compare
frozen official33 with official34, baseline27, and baseline27+verifier32 using
the existing20 latest calls and49.50-minute conversation. No fit, threshold change,
new recording or runtime change. Exact individual event timings are replayed
under OS network denial and must reproduce archived counts. The proposed merger
accepts an event from either frozen pipeline and applies a shared3-second cooldown
after their existing internal cooldowns. This is event-level OR, not a raw-score
ensemble or a learned combination. Saved evidence in private parallel35.

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

### Supervised simulator MVP — 2026-09-09

User explicitly accepts trial limitations and authorizes official33 alone, off by
default, using the existing simulator microphone flow. This supersedes earlier
blanket no-runtime-admission wording for a supervised trial only; recorded
quality gates remain failed and no hardware readiness is claimed. Scope: pinned
optional openWakeWord runtime, locally hash-verified model/backbones, 16kHz80ms
streaming adapter, existing HybridRuntime activation/privacy/capture barrier,
manual fallback, timeout return to ambient, deterministic integration/privacy
tests and native simulator smoke. No retraining or parallel-detector integration.
MVP interaction: say Reachy, wait for acknowledgement, then speak; preactivation
audio is never replayed to cloud. A concise retained-model catalog and current
whole-plan status will accompany the change. Dependency review: upstream code
Apache-2.0, ONNX CPU execution, explicit local asset paths/no download call;
trained artifacts and public-backbone licensing remain private/noncommercial
research constraints. Optional upstream dependencies include LiteRT and Speex
on supported platforms, although this path explicitly selects ONNX and no
Speex/VAD. Pin exactly the tested upstream revision. Risks: auto false activation
starts the normal cloud conversation; native simulator audio/AEC remains a
known limitation; real Pi/microphone performance is unmeasured.

Supervised MVP implementation complete in the local checkout. The optional
pinned runtime and three local asset hashes are checked before media startup;
16kHz80ms scoring at0.5/3s is integrated with the existing hybrid capture barrier,
acknowledgment, manual fallback and inactivity close. No model is bundled and
`.env` was not modified. Use the root README's supervised-trial command.
Native app-adapter replay under OS network denial exactly reproduced20 prior
counts (17 detections). Focused model tests19 passed; runtime74 passed; opt-in
headless real MuJoCo/SDK/movement plus fake-provider test1 passed in2.71s.
Full gate892 passed,2 explicit native opt-in skips in13.30s; Ruff/format/mypy and
uv lock check passed. Independent review found per-frame resampling drift;
source80ms buffering fixed it and whole/1024/single-sample regression tests pass.
Audio after the first hit now remains processed, and dropped/stale queue epochs
cannot bridge conversation generations. Runtime implementation does not tune
thresholds or claim independent acceptance.

Graphical startup exited139 in `_glfwGetVideoModeCocoa` while computer-use tools
reported the Mac locked. Headless startup/lifecycle worked. This is a new display
limitation, separate from prior SDK GStreamer crashes. A headless `--no-media`
smoke intentionally disabled SDK audio, so it cannot prove microphone capture;
repeat microphone work requires media-enabled daemon. Final human-spoken live
acceptance remains pending; no user recording batch is requested.


Media-enabled headless microphone smoke also passed: the SDK delivered 472 frames
(75,488 samples at 16kHz) with nonzero input through the actual detector. No wake
was observed during this short unattended check; the provider was fake only,
ambient cloud bytes were zero, and no audio was persisted. This proves microphone
wiring, not a human-spoken wake/conversation. Independent re-review found no
remaining blocking defects and reran 93 focused tests successfully.


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
