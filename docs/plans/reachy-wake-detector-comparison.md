# Reachy wake detector: model and training comparison

- Status: training paused; official33 supervised simulator MVP selected
- Date: 2026-09-09
- Parent: [custom training history](custom-reachy-openwakeword-training.md)
- Scope: local acoustic detection of bare Reachy and the accepted pronunciations, in EN/ES/PT/IT context.

## Current decision — 2026-09-09

User chose the official openWakeWord recipe first. Run one stock DNN/auto_train
baseline without verifier; microWakeWord and the two-arm custom comparison below
are deferred. The earlier proposal is retained as research history. Native local
training uses an isolated PyTorch environment and pinned upstream source; no
household data goes into Docker or a cloud mount. No new household recordings.

## Earlier research proposal

Stop extending verifier32. Compare a rebuilt openWakeWord temporal classifier with
a microWakeWord streaming classifier on the same improved source data. Evaluate
each alone first. This tests whether data/training changes suffice or a different
audio representation helps; it does not assume microWakeWord will win.
No new household recordings are requested for this research or experiment.

## Evidence and limits

Candidate27 uses two Conv1d layers over fixed openWakeWord 16x96 embeddings,
followed by an order-preserving flattened classifier. Its training uses the
highest-scoring window in each positive bag. That loss can reward a contextual
sound instead of the name; this is a possible shortcut, not a demonstrated cause.
Verifier32 is a linear classifier on the same embeddings, trained on detected
positive events and 236 negative windows from only two conversations. Its two
negative groups each receive 25% of training weight, despite one containing only
two windows. Those windows are not 236 independent conversations.

Fresh results: 18/20 calls, Spanish 6/8, and three false wakes in 49.50 minutes.
The two missed calls peak at 0.00754 and 0.000151 against a 0.5 baseline threshold.
All three false wakes were user-confirmed ordinary conversation. Small threshold
changes cannot recover the misses. A different linear filter cannot be presumed
independent merely because it has different weights.

Earlier localization29 and broader confirmation31 experiments already failed.
Repeating alignment, negative weighting, or a larger linear verifier alone is
not a new solution. The experiment below adds independent source diversity and
a different representation, with a control to avoid attributing data gains to
architecture. No claim that the fixed embeddings are inherently incapable of
recognizing these pronunciations is supported by the current evidence.

## Sources reviewed

- [openWakeWord architecture and training](https://github.com/dscripka/openWakeWord):
  fixed speech embeddings followed by a trainable head; recommends thousands of
  generated positives and broad negative exposure. Its English support statement
  concerns the supported training ecosystem, not proof that multilingual training
  is impossible. Our multilingual TTS already addresses part of that limitation.
- [Official verifier guidance](https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md):
  small, speaker/environment-focused logistic filter; not a general replacement
  for the first detector. Our broad synthetic verifier goes beyond that narrow use.
- [microWakeWord](https://github.com/OHF-Voice/micro-wake-word): trainable streaming
  convolutions over spectrogram features, with ambient validation and streaming
  export. The authors explicitly warn that the starter notebook may not produce
  a usable model. Apache-2.0 code; review dependencies and artifact licenses before use.
- [Multilingual LibriSpeech](https://www.openslr.org/94/): CC BY 4.0 read speech
  covering all four languages, useful for diverse negatives but not a substitute
  for spontaneous Venezuelan Spanish or household conversation. Full archives
  are large; identify a bounded download method before acquisition.
- [AMI](https://groups.inf.ed.ac.uk/ami/corpus/): CC BY 4.0 meeting speech with
  close/far microphones, useful for conversational negatives and channel variation.
- [MUSAN](https://www.openslr.org/17/): speech/music/noise candidate for augmentation;
  retain per-file attribution and inspect component terms before selecting files.
- [Porcupine](https://picovoice.ai/docs/porcupine/): optional independent benchmark
  with custom words and supported EN/ES/PT/IT models. Requires an account AccessKey
  and platform-specific exports. Account terms, language routing and network-denied
  startup must be checked; no account, paid action or SDK adoption is part of this proposal.

## Training data changes

1. Inventory unique original sources, voices, languages and existing augmentation
   coverage before generating more. Ten pitch/tempo copies still count as one
   original source. Keep already authorized original household training audio
   local. Existing evaluation-only household sets remain excluded from fitting.
2. Build full naturally spoken sentences containing the target at different
   positions with varied following sounds and speaking rates. Retain standalone
   calls too. Generate the sentence together; splicing an isolated name into a
   sentence does not reproduce connected pronunciation. Reuse accepted TTS voices
   and local generators first; more ElevenLabs credits alone are not the solution.
3. Make matched training examples with similar voice, carrier phrase and acoustic
   conditions but no target. Include confusing word sequences from separate
   training corpora. Do not label accepted Reachy homophones as negatives.
   Timestamp the actual target and mask uncertain boundary windows rather than
   labeling all surrounding speech positive or trusting one arbitrary peak.
4. Use multilingual real speech as the bulk of negative variety, supplemented by
   conversations, sound effects and synthetic near-misses. Proposed pilot budget:
   20 hours of multilingual speech (roughly balanced), plus 5 hours of conversation/
   noise. These are bounded starting quantities, not proven sufficiency targets.
   Cap each source/speaker's sampling contribution. Audit transcripts and ambiguous
   target-like segments before treating a source as negative.
5. Mine confusing negatives only from TRAIN sources, with a fixed per-source cap;
   keep randomly sampled easy negatives as well. Score continuous streams so
   mining reflects deployment context, rather than repeated isolated windows.
6. Apply realistic gain, modest rate variation, room responses, channel variation
   and background speech/noise to both classes. Audit existing augmentation first;
   merely adding more already-present augmentations is not a meaningful change.

## Bounded implementation order

1. Freeze provenance and source-level splits before augmentation. Keep speakers,
   recording sessions, TTS source utterances and their derivatives together; hold
   out voices and sentence templates where available. Reserve separate public
   development and test ambient exposure (proposed four hours each), outside the
   training quantities above. Do not use household evaluation scores to pick parameters.
2. Preflight the pinned microWakeWord training/export/inference stack with public
   audio only. Verify streaming reset/chunk behavior, timestamps and export parity
   on Mac and intended Linux target. Measure memory/CPU throughput before a full fit.
3. Train at most two predefined pilot models: A, the current order-preserving CNN
   head on openWakeWord features, rebuilt with the revised data/loss; B, the upstream
   microWakeWord streaming architecture using the same source splits. Retain frozen
   candidate27 as the historical reference. No verifier in either pilot.
4. Choose operating points/checkpoints only on independent development sources,
   constrained by both recall and continuous false activations. Define target-word
   event timing, cooldown and duplicate counting before evaluation. Each engine
   keeps its required preprocessing; stream padding/context conventions must be
   explicitly reconciled rather than blindly copied between engines.
5. Freeze the selected artifacts, evaluate the public test set and replay all
   existing private evaluation recordings locally once. Report every result,
   including per-speaker/language/variant recall, negative events/hour and duplicates.
   No automatic extra seeds, threshold sweeps or verifier branches after failure.

## Acceptance and stop conditions

Preserve the parent plan's >=95% aggregate and >=90% represented-stratum recall
targets and <=1 false activation/hour provisional exposure target. Existing
synthetic preservation requirements remain; do not trade a language away for a
better aggregate. Report missing strata as untested. Passing reused household
recordings is regression evidence, not new independent acceptance: their errors
have now informed our design even though their bytes remain outside training.

If neither pilot improves the recall/false-alarm tradeoff while preserving the
required strata, stop and report a failed comparison. Do not ask for another small
recording batch. If one is promising, report it as a research candidate; deployment
still needs target-microphone evidence. Any future user recording work must be
explained as one complete, separately agreed scope. No promise that existing Mac
recordings can certify a different microphone or all future household conditions.

## Alternatives deferred

- Parallel candidate27 + verifier32 cannot recover misses under the present
  event-only contract. An independent detector needs its own training contract.
- Always-on Whisper/ASR adds cost and latency; the earlier local Whisper verifier
  already reduced recall substantially. Do not repeat it without a distinct hypothesis.
- A pretrained multilingual speech encoder is a possible later research option,
  but increases runtime/training complexity; not the first bounded comparison.
- A longer phrase such as Hey Reachy changes the product requirement. It is a
  possible fallback, not a silent replacement for bare-name detection.

## Privacy, cost, validation and documentation

This turn is research/documentation only: no training, model downloads, synthesis,
account action, new dependencies or rentals. Household recordings, derived features
and adapted models never leave the Mac. Public/synthetic-only GPU work could fit
the existing original $10 ceiling, but remaining balance and projected job cost
must be verified first; no current balance or runtime estimate is asserted here.
Keep household adaptation native/local. Preflight can stop before any rental.

Relevant private evidence: candidate-run-27/{configuration.json,trainer-source.py},
event-verifier32/{train.py,fit.json,fresh-signal-diagnostic.json}, fresh32 result
receipts, and localization29/confirmation31 results in the parent history.
Implementation would require focused split-leakage, causal streaming, export-parity
and event-count tests, full repository gate, and updates to wake-word feature docs,
benchmark and parent plan. No application architecture or SDK integration changes
are proposed before a successful comparison. Research references were checked live;
no alternative model's accuracy on this household has been measured.

Research handoff validation: local plan links and `git diff --check` passed.
Ruff check/format and mypy passed; full pytest: 863 passed, 1 skipped in 13.40s.
No model experiment or hardware validation was run.

## Official baseline outcome — 2026-09-09

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

## Official34 follow-up — 2026-09-09

Authorized: prepare later ElevenLabs TRAIN recordings with local name alignment
and official augmentation, then one stock trainer fit. Protected evaluation
recordings remain excluded. Detailed protocol and progress are in the parent plan.

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

2026-09-09 user decision: pause architecture/training work and try official33 as
an off-by-default supervised simulator MVP. Integration and automated/native
headless checks are complete; human live and hardware acceptance are pending.
The retained-model catalog and parent plan carry the current decision.
