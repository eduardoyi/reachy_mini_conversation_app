# Plans

Every design, implementation, migration, benchmark, rollout, and operational
plan for this project lives in this directory. A plan is not complete merely
because code has landed: its status and evidence must be updated until the
acceptance criteria are met.

## Plan index

| Plan | Status | Last updated | Scope |
|------|--------|--------------|-------|
| [E-ink display (TRMNL)](eink-display.md) | `in-progress` | 2026-10-25 | v1 and v2 (web pictures, Jev check) live-checked on the TRMNL; voice check pending; v3 visual explanations planned |
| [Local music controls and a local command model](local-music-controls.md) | `in-progress` | 2026-10-14 | V1 music lowering, volume and wind-down accepted on hardware; V2 local music commands and V3 local command model planned |
| [Reachy Mini Lite hardware bring-up](reachy-mini-lite-bringup.md) | `in-progress` | 2026-10-13 | Stepwise first power-on, robot audio, interruption, hybrid/wake re-tuning; SDK target 1.11 |
| [TypeSafe judgment exploration](typesafe-judgment-exploration.md) | `draft` | 2026-09-17 | Synthetic evaluation complete; background memory verification proposed, no runtime integration |
| [Gemini 3.8 Live Extended Thinking migration](gemini-38-live-extended-thinking-migration.md) | `in-progress` | 2026-09-21 | SDK crash fix user-confirmed; wake acknowledgment and opt-in interruption assist implemented; 1,263 tests passed; speaker retest and live transcript parity pending |
| [Local audio dropout comparison](audio-dropout-comparison.md) | `GPT speaker path accepted; Gemini tiny-chunk fix native-tested, live pending` | 2026-09-13 | Audio diagnostics, SDK flush candidate and live audio acceptance |
| [Household memory lifecycle](household-memory-lifecycle.md) | `in-progress` | 2026-09-13 | M1–M2 user accepted; M3 daily tidy-up implemented; synthetic Luna review passed; live follow-up pending |
| [GPT Live migration](openai-live-migration.md) | `Mac speech movement and speakers accepted; hardware pending` | 2026-09-13 | GPT Live speech, Luna delegation, local movement repair; simulator echo/hearing follow-up |
| [Spotify playback control](reachy-spotify-control.md) | `complete: Mac playback and search/play/queue accepted` | 2026-09-10 | Optional Mac playback voice controls |
| [Reachy hybrid household companion](reachy-hybrid-household-companion.md) | `in-progress: headless MVP accepted; hardware validation pending` | 2026-09-10 | Phases 0–5 |
| [Custom multilingual Reachy openWakeWord training](custom-reachy-openwakeword-training.md) | `Official33 supervised headless MVP accepted; training paused` | 2026-09-10 | Phase 4 custom acoustic wake detector |
| [Reachy wake detector model/training comparison](reachy-wake-detector-comparison.md) | `Training paused; architecture comparison deferred` | 2026-09-09 | Controlled data and architecture comparison |
| [Retained Reachy wake models](reachy-wake-model-catalog.md) | `Official33 chosen for supervised MVP` | 2026-09-09 | Private artifacts, frozen metrics and alternatives |

The required report is [HYBRID_BENCHMARK.md](../../HYBRID_BENCHMARK.md).
The companion loop is implemented in the local checkout; phases 0–3 and functional
phase 5 have prior simulator acceptance with documented limits. Official33 acoustic
wake is now integrated behind an off-by-default setting. Native 20-call adapter
parity and headless SDK/movement integration passed; human-spoken graphical
acceptance, actual robot/Pi measurements, speaker echo outside the accepted Mac
SDK-candidate configuration and some latency/resource
measurements remain open. Training is paused. See the main plan's current-status
table and retained-model catalog before reading the historical execution notes.

## Status vocabulary

Use one of these statuses in both the plan header and this index:

| Status | Meaning |
|--------|---------|
| `draft` | Exploratory; not yet approved for implementation. |
| `approved` | The approach is agreed and ready to implement. |
| `in-progress` | Work is active or required validation remains. |
| `blocked` | A concrete blocker is recorded in the plan. |
| `complete` | Scope, acceptance criteria, and required tests have passed. |
| `superseded` | Replaced by another plan; link the replacement. |

## Plan naming and update rules

- Use one descriptive `kebab-case.md` file per plan.
- Start with a title and metadata for `Status`, `Owner`, `Created`, and `Last
  updated`.
- Record goals, non-goals, decisions, phases, acceptance criteria, tests,
  benchmarks, risks, and next steps.
- Maintain a dated execution log and changelog. Record material deviations
  rather than silently rewriting an approved decision.
- Add or update the index row in the same change as the plan.
- When a plan is `complete`, link the implementation/feature documentation and
  the evidence that supports completion.

## Historical execution notes

Custom-training status at 2026-09-07: sounds **1, 2 and 5** accepted;
GPU setup fixed and four full candidates completed. Candidate 3 remains best
on synthetic tests (400/400 positives, 36/3,168 negative activations, 1.136%).
Under the user-authorized exploratory exception, it detected **19/30 private
positives** (17/25 and 2/5 by speaker), below 27/30. Mac/Linux numerical
agreement passed; the synthetic failure remains recorded. No audio upload,
threshold sweep, negative collection or app integration occurred. All rental
resources were removed: about $1.16 spent, $8.84 remaining, $0/hour; the local
screen added no provider cost. Further model training is not authorized.

Local diagnosis: all 11 misses are name-first phrases (6/17 detected versus
13/13 other placements). Level ranges overlap and quiet-background proxies
match. Paired synthetic concatenation detects 59/60 name-first examples, so
simple placement alone does not reproduce the real gap. Next design focus is
naturally spoken connected phrases; no fifth training attempt is authorized.

Name-isolation diagnostic: 3/11 misses activate after the estimated name-only
cut, versus 0/11 full sentences. Nearby cuts/fade yield 2–4/11, with no clip
detected in every variant. Automatic boundary uncertainty prevents a clear
causal conclusion. Original private recall remains 19/30; no retraining.
