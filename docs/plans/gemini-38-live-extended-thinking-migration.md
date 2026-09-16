# Gemini 3.8 Live Extended Thinking migration

- Status: `in-progress` — offline implementation validated; live transcript parity and rollout pending
- Owner: Reachy project
- Created: 2026-09-16
- Last updated: 2026-09-16
- Current milestone: local implementation and adversarial review complete; controlled live validation next

## Goal and scope

Restore Gemini as the user's selected conversation provider using
`gemini-3.8-live-extended-thinking`, preserving natural speech, existing tools,
movement, household memory and hybrid standby privacy. The user prefers Gemini's
conversation style; whether this model feels better remains a human acceptance
question, not an inference from benchmarks.

The user authorized implementation with Luna/max subagents and the primary agent
as orchestrator/reviewer on 2026-09-16. The fork has GitHub Issues disabled; the user
explicitly approved using this reviewed plan as the local implementation record.
No issue, PR, commit or push is part of this task. Preserve existing uncommitted
audio, GPT and memory work. Paid provider trials and changing the active local
provider remain later gates; no household content is used in offline validation.

Non-goals: new wake training, new recognition, a new agent framework, replacing
the local audio stack, migrating the memory store, or deleting GPT/Hugging Face.
Keep the global Hugging Face default; the eventual switch is an explicit local
provider selection. Do not silently fall back to another cloud provider.

## Evidence and assumptions

Official sources inspected on 2026-09-16:

- [Model contract](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live-extended-thinking):
  stable model ID, asynchronous-only tools, search grounding, configurable
  low/medium/high thinking, permanently enabled proactive audio.
- [Thinking integration](https://ai.google.dev/gemini-api/docs/live-api/thinking):
  background reasoning, intermediate spoken updates and interaction status.
- [Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities):
  multi-part messages and client-content interruption behavior.

Pre-implementation snapshot: `gemini_live.py` implements Gemini; config accepts a model
override. Tool declarations omit `behavior`; session setup omits thinking config.
The receive loop continues across utterances, but there is no interaction-status
tracking. Tool results currently wait for siblings in their batch. Installed and
locked `google-genai==2.19.0` exposes `thinking_level`, function `behavior`, and
`LiveServerContent.interaction_status`, but not a typed top-level
`LiveServerMessage.interaction_status`.

Use the public Gemini Developer API with explicit `v1alpha` transport for this
model, as shown in the thinking guide. Preserve other models' transport behavior.
Google's tutorial examples place status both at top level and inside `serverContent`.
The [WebSocket protocol reference](https://ai.google.dev/api/live#BidiGenerateContentServerContent),
checked during implementation, resolves the contract: `interactionStatus` belongs
inside `serverContent`, always alongside `turnComplete`; `ServerMessage` has no
such top-level field. Use that typed nested contract. The pinned SDK discards the
unsupported top-level example form; record this in synthetic decoder tests rather
than inventing a raw-payload bypass or requiring support for a non-schema alias.

The first gate exercises public live session receiving with fake wire fixtures,
including setup conversion, nested statuses, combined fields and transcript flags.
Normalize documented enum/string values to IN_PROGRESS or IDLE; unknown nested
values cause sanitized protocol failure. Missing status in ordinary messages means
no state change; missing status on an Extended Thinking `turnComplete` is a
protocol error, never inferred IDLE. A message dropped due to an unsupported shape
cannot extend deadlines or make the runtime idle. Real synthetic server validation
later confirms schema compliance; failure blocks rollout. No SDK dependency
upgrade is required solely for the contradictory tutorial example. Any upgrade
still needs compatibility/network/license review and lock update.

Automatic capture, daily review and stale-memory invalidation currently have GPT
provider wiring in `openai_live.py`. Store and prompt construction are shared.
A model/provider-string-only switch is not feature parity. Read the
[memory feature](../features/household-memory.md),
[Gemini feature](../features/gemini-live-provider.md),
[GPT feature](../features/openai-live-provider.md),
[hybrid plan](reachy-hybrid-household-companion.md), and
[audio comparison](audio-dropout-comparison.md) before implementation.

Account entitlement and live compatibility have not been verified. The session
policy and pricing snapshot below are documentation-derived, not account evidence. Prior GPT Mac speaker acceptance does
not establish Gemini 3.8 audio acceptance. No private environment values, audio,
transcripts or memory contents were read for this plan.

## Design decisions

### Provider and protocol

Reuse `GeminiLiveHandler`, shared tools, profiles, movement and SDK playback.
Proposed local selection after validation:

```env
REALTIME_PROVIDER=gemini
GEMINI_LIVE_MODEL=gemini-3.8-live-extended-thinking
```

Add a validated optional `GEMINI_THINKING_LEVEL` setting, proposed default
`medium` for Extended Thinking. Send thinking configuration only for explicitly
supported models; preserve the legacy 3.1 contract and omit it for standard 3.8.
Match the exact ID, allowing only the SDK's optional `models/` prefix. For
Extended Thinking accept case-normalized low/medium/high, default medium; reject
other nonempty values. For other models an explicitly supplied thinking setting
fails clearly rather than being ignored; with it unset, preserve existing setup.
Retain the existing Gemini voice unless live compatibility requires a change.

Declare application functions `NON_BLOCKING` for Extended Thinking. Keep managed
Google Search and profile tool permissions. Return independently completed tool
results immediately in separate responses keyed by call ID, including out-of-order
completion. Do not wait for slow siblings. This uses the supported manual function
response contract in the [tool guide](https://ai.google.dev/gemini-api/docs/live-api/tools).
For this model, compute one effective tool deadline as the earliest of 30 seconds
from admission, the interaction deadline, and the session deadline minus two
seconds of response headroom. Reject tool admission if that deadline has passed. On timeout return one sanitized error if the session is
still valid. Do not alter legacy providers' tool deadlines. A provider-cancelled
call sends no late result; cancellation after a sent result cannot retract it.
Serialize response admission to prevent completion/cancellation double sends.
If cancellation cannot contain a tool, fence the session and enter stopping;
never report that an already executed external side effect was undone. Preserve call-ID association, duplicate prevention,
bounded execution, failure reporting and cancellation. Never retry a completed
side effect automatically after a disconnect. Do not add function scheduling,
which Extended Thinking does not support.

Separate audible speaking from background reasoning. `turn_complete` can end one
utterance without ending the interaction; `IN_PROGRESS` retains busy state and
`IDLE` ends it. Neither should freeze listening movements for an entire session.
Continue processing all audio parts, transcripts and tool events. Distinguish
first audible acknowledgement from final-answer latency in diagnostics.

### Interaction, shutdown and session lifetime

Expose a provider-neutral interaction-busy signal through the shared handler
contract; retain protocol translation in Gemini. Start a fixed 120-second deadline
when successfully submitted user audio reaches an observed local speech end, or
when an addressed text submission succeeds. This is locally accepted input even
if proactive audio ignores it. If speech-end is unavailable, the first correlated
IN_PROGRESS/tool event after user submission starts the cap. Every tool remains
bounded by its independent 30-second/session limit even without that correlation.
Unsolicited assistant audio before user input never creates a user interaction.
Ordinary inactivity can close earlier while no authoritative busy state is active;
it never replaces or extends the hard deadline. Repeated busy/filler events
never extend that deadline.
IDLE ends reasoning; ordinary inactivity resumes only once queued playback drains.
Track playback and reasoning separately so speech movement does not imply thinking.
A new user interruption cancels/fences the previous turn's pending tools and output;
only a demonstrably new addressed turn starts a new deadline. User speech itself
continues to suppress ordinary inactivity as today, subject to session lifetime.

At the deadline, provider failure, explicit stop, memory invalidation or GoAway:
synchronously reject new media/text/tool/auxiliary requests and memory writes,
invalidate the generation, clear playback and movement flags, then close. A
missing status is not IDLE and cannot defeat the deadline. Late events cannot
reopen a fenced generation. Silence with no accepted user input uses normal
inactivity. Ignored submitted input keeps its hard cap, while normal inactivity
may close earlier if no busy status arrived. Proactive audio
cannot open a cloud session from ambient mode.

Add a shared STOPPING state (or equivalently exposed lifecycle state), distinct
from LOCAL_AMBIENT. Entry immediately fences admission and cancels pending work.
Allow up to 15 seconds for provider and auxiliary worker/client closure. If any
close remains uncontained, retain STOPPING with a visible sanitized error and
reject reactivation; do not claim privacy-safe ambient or create another handler.
A later confirmed close may finish the transition; operator restart is recovery
if cleanup never completes. No automatic retry loop. Closed inert Python objects
may remain referenced; the guarantee concerns active clients/connections, requests,
media/text transfer and writes, not object allocation. Remote work already accepted
cannot be recalled. Tests instrument both transports and all auxiliary workers.

Choose controlled closure for this migration, not session resumption or compression.
The [session guide](https://ai.google.dev/gemini-api/docs/live-api/session-management)
lists 15-minute audio sessions, two-minute audio/video sessions and approximately
10-minute connections without these features. Use conservative application limits:
close at nine minutes from readiness for audio-only. Before first camera send,
apply a 110-second total session-age limit: require enough time for capture and
response headroom within the effective tool deadline. Serialize deadline installation, session-age validation and image-send
admission against closure; recheck immediately before image transfer. If already
past the effective deadline, reject without capturing/sending an image. A rejection
error may be sent best-effort only while the transport remains unfenced and before
the session deadline; closure never waits for it. If the deadline expires during
capture, discard the frame and close on schedule. Otherwise send the image and
its call-ID response within the remaining budget, closing at session age 110s.
This deliberately treats any image as activating the shorter limit until live
verification establishes otherwise. GoAway closes immediately even during work;
no automatic replay/resumption. Show the closure reason locally and require a new
activation, with a fresh memory snapshot. These conversation-length limits need
human acceptance; continuous long sessions are deferred, not silently promised.

Diagnostics use monotonic times: acknowledgement latency is speech-end to first
audio; interaction completion latency is speech-end to authoritative IDLE. Neither
is labelled final-answer onset. Report unavailable if boundaries cannot be matched;
interrupted turns have no successful completion sample. Do not retain raw payloads.

### Memory parity and privacy

Reuse existing store/capture/review components, budgets and default-off flags.
Extract only genuinely shared lifecycle behavior needed by both providers; shared
conversation behavior belongs in the existing shared conversation layer, with
Gemini protocol handling confined to its adapter. Do not duplicate the GPT worker
wiring wholesale or add another memory database.

Keep the current Responses-based extraction/review model initially. Switching the
voice to Gemini does not make this Google-only: when these optional features are
enabled, their bounded text still goes to the configured OpenAI service. Preserve
existing opt-ins and budgets; do not enable daily review during this migration.

Capture starts after readiness and context seeding. It receives finalized active
conversation text through a shared normalized contract: session generation,
utterance ID, role, monotonic timestamp, text, and final/interrupted disposition.
Interim observations update an in-memory utterance only, never start extraction.
The first offline compatibility gate must prove the exact event/field framing
through SDK-decoded fixtures: prefer `input_transcription.finished=True` and the
output equivalent only if proven to contain a complete final utterance. The pinned
SDK exposes no delta/cumulative discriminator. Therefore interim text stays UI-only
unless a documented per-field contract is proven; do not infer framing from text
prefixes or a nonexistent flag. A proven cumulative stream replaces the current
utterance; a proven delta stream appends. If reliable finalized text cannot be
established for a role, skip capture for that role and fail memory-parity acceptance;
conversation may be tested but the migration cannot be called complete. Unknown or
ambiguous transcript framing skips capture for that utterance, not conversation.
A final event commits once per generation/utterance, including repeated finals;
identical text in a different utterance remains distinct. Multi-part events process
all parts. Interrupted or unfinalized assistant text is excluded from capture.
Reconnect drops unfinished buffers and changes generation. No fallback flush on stop.

Accepted activation text enters this contract once after successful `say()` send,
using a local utterance identity; it is distinct from seeded background context.
If server transcription echoes that submission without a correlatable identity,
suppress ambiguous capture rather than risk storing twice. Regression fixtures
must cover cumulative/delta partials, repeated finals, genuine repeated phrases,
activation echo, interruption and reconnect. Existing capture input/output limits,
attribution checks and reservations still apply. No audio, images, seeded prewake
text or reasoning parts enter capture.

### Memory mutation matrix

Preserve the documented spoken behavior. All automatic writes remain revision-
checked, and unknown speaker details are quietly skipped. The matrix is normative
for both providers after the shared lifecycle milestone:

| Mutation/source | Active session action |
|---|---|
| Routine spoken/capture addition | Keep session; include in next activation snapshot. |
| Spoken correction or evidence-validated capture update/resolve | Keep session, acknowledge exactly the committed change as originating in this conversation; refresh watcher baseline only, not seeded prompt. Next activation loads saved changes. |
| Spoken forget | Keep session; acknowledge only removed IDs, invalidate pending capture/review, pause both for this activation to prevent restoration. The current cloud context can still contain the fact; do not claim erasure. |
| Settings delete | Fence and close conversation before deletion, as today; fresh activation reloads memory. |
| Unacknowledged external delete, same-ID content/ownership correction, or unreadable store | Fence and close; never silently rebase. External editing still requires stopping the app. |
| Active-context expiry, even with unchanged revision | Compare active entry membership independently of revision; fence and close if a seeded entry is removed. |
| Validated daily review merge/remove/promote | Reuse existing atomic review/checkpoint and explicit review-result rebase, only for the validated before/after revisions. Cancel/rebase capture as existing rules require; preserve the current spoken context until next activation. No review change counts as fresh evidence. |
| Concurrent/unrecognized mutation during capture or review | Reject stale write/rebase and close if seeded context was removed/changed; additive changes wait for next activation. |

Use a shared in-process mutation receipt contract for tool, capture and review
writers: source, generation, exact before/after revisions and changed IDs/values.
Publish the RAM-only receipt in writer order within the same application mutation
critical section as the atomic store commit, before the watcher may consume that
transition. Never log, persist or speak the receipt. The watcher consumes only the
exact acknowledged transition and independently diffs later/current snapshots;
a receipt cannot authorize a broad baseline reset. A missed receipt or unknown
transition fails closed rather than exempting concurrent changes. External edits
cannot produce trusted receipts. Thread/task coordination must cover all in-process
writers and watcher reads. Add a deterministic writer-versus-watcher race test,
including a legitimate same-ID correction followed by an unrelated deletion.
Expiry closure and same-ID external-edit detection are deliberate hardening beyond
current GPT behavior; implement as a separate focused change with regression tests
and document it, not as an unexplained parity claim. This does not erase remote
provider history. Preserve existing default-off flags and do not migrate the store.

### Pricing and trial gate

[Pricing](https://ai.google.dev/gemini-api/docs/pricing), checked 2026-09-16,
lists $0.005/input audio minute and $0.018/output audio minute, plus text/thinking,
image/video and applicable Search charges. Text is $0.75/M input and $4.50/M output
tokens; image/video is $1/M tokens. Search is $14/1,000 after the shared allowance;
one request can trigger multiple billable queries. Auxiliary OpenAI memory costs
are separate. Do not present audio rates as total conversation cost.

Before paid validation, propose an explicit bounded run: at most two 60-second
synthetic sessions, one tool batch each, memory workers and native Search disabled
for the transport probe. Refresh pricing, specify token limits supported by the
actual model, and agree a dollar budget with the user before any call. If total
spend cannot be hard-capped, distinguish duration/request caps from a dollar
estimate. Search, memory and fresh household trials require the later agreed
budget. Verify paid-tier data treatment before household content: the pricing
page distinguishes free-tier product-improvement use from paid tier. Never change
billing settings automatically or upload archived household media.

## Implementation order and acceptance gates

1. **Contract gate.** Verify the prescribed SDK/transport fixture contract first.
   A failing decoder blocks model integration until a supported SDK passes.
   Refresh price/access documentation and propose the bounded synthetic budget.
   Do not start paid tests without user authorization. Review the existing dirty
   checkout and isolate implementation without losing its required memory work.
2. **Protocol milestone.** Add model-aware config, non-blocking tools and interaction
   lifecycle handling. Fake sessions prove fillers followed by tools/final speech,
   prompt result delivery, interruption, timeout, reconnect and stale-event fences.
3. **Shared memory milestone, then Gemini wiring.** First extract shared lifecycle
   behavior with GPT regressions, then implement the documented invalidation
   hardening separately, then wire Gemini capture/review and transcript normalization. Fake-provider
   tests prove attribution, budget, revision, deletion and standby boundaries for
   both Gemini and GPT. Disabled features create no auxiliary provider work.
4. **Integration milestone.** Exercise actual Gemini handler inside HybridRuntime
   with fake cloud and SDK interfaces. Verify activation, camera/tools, movement,
   interrupted long work, inactivity, standby and reactivation. Run full gate and
   adversarial review; fix findings and rerun affected checks.
5. **Authorized live trial.** First use synthetic content for account/handshake and
   protocol checks with bounded duration/cost; do not upload archived household
   media. Then a fresh user-led Mac session on the existing SDK route: headphones,
   speakers, casual conversation, a reasoning question, tool/camera request,
   interruption, silence-to-standby and second activation. Record SDK/audio route,
   voice/model/thinking level, echo/dropouts and available timing metadata.
6. **Local selection and rollback.** Switch only after user acceptance. Record the
   prior non-secret provider/model/audio settings and restore them if access,
   memory, audio or standby acceptance fails. Stop/close the trial session before
   selecting GPT again; do not mutate the memory store or erase unrelated work.

Each milestone is a separate focused change with its own tests and documentation;
do not combine the shared shutdown change, memory hardening and provider wiring
into one oversized PR. No issue or PR is opened by this local implementation task.

Implementation is complete only when essential automated gates pass and memory
parity is demonstrated. Local rollout is complete only after user conversation
acceptance and verified effective provider selection. Hardware/Pi validation is
separate and remains pending until tested on those devices.

## Test inventory and commands

Extend existing suites: `tests/test_config.py`, `tests/test_gemini_live.py`,
`tests/test_hybrid_runtime.py`, `tests/test_hybrid_smoke.py`,
`tests/test_openai_live_memory.py`, `tests/test_household_memory_capture.py`,
`tests/test_household_memory_review.py`, `tests/test_household_memory_integration.py`,
`tests/test_openai_live_review.py`, `tests/test_household_memory_review_store.py`,
`tests/test_household_memory_tools.py`, `tests/test_main.py`, `tests/test_moves.py`, and `tests/audio/test_speaker_output.py`. Add a focused
Gemini memory integration suite if needed rather than putting protocol fixtures
in unrelated store tests. New bug fixes require regressions failing on old code.

Required cases include nested/unsupported status shape, missing/duplicate IDLE,
audio plus transcript in one event, interruption during filler/tool/final speech,
tool sibling failure/cancellation, reconnect during seeding, stale callback after
replacement, missing key, setup timeout, bounded busy timeout, proactive silence,
no late memory request/write, same-ID mutation, same-revision expiry, exactly scoped
mutation acknowledgements, and no ambient cloud bytes. Include cancellation-resistant
shutdown staying STOPPING, denied reactivation, delayed closure recovery, GoAway
while busy, camera limits before/after image send, and independent out-of-order
tool completion/cancellation. Instrument client construction, requests, text/media
sends and writes across both Gemini and auxiliary OpenAI paths.
Keep tests deterministic, synthetic, hardware-independent and secret-free.

Existing commands, to run during implementation (not reported as run here):

```bash
REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_config.py tests/test_main.py tests/test_gemini_live.py tests/test_hybrid_runtime.py tests/test_hybrid_smoke.py tests/test_openai_live_review.py tests/test_household_memory_review_store.py tests/test_household_memory_tools.py tests/test_openai_live_memory.py tests/test_household_memory_capture.py tests/test_household_memory_review.py tests/test_household_memory_integration.py tests/test_moves.py tests/audio/test_speaker_output.py -q
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/mypy --pretty --show-error-codes
REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v
uv lock --check
git diff --check
```

Run any newly added suites too. Inspect formatter changes to avoid unrelated
churn. Record exact results, skipped native checks and unavailable services here
and in feature docs. Never equate fakes with measured latency or live acceptance.

## Documentation and risks

During implementation update `.env.example`, root README, Gemini and household
memory feature docs, affected GPT/shared lifecycle docs, feature index, this plan
and the plans index. Update the main companion plan when rollout state changes.
If shared lifecycle/data flow changes, update `docs/scheme.mmd` and regenerate the
SVG through its supported workflow; do not hand-edit the generated image.

Main risks: SDK/docs mismatch, busy-state leaks or premature inactivity close,
stale memory writes, duplicate side effects, increased reasoning latency/cost,
proactive audio differences, and recurrence of speaker echo. Roll back local
selection on acceptance failures. Avoid a broad provider refactor; memory parity
is necessary work, but unrelated cleanup and changing extraction models are not.

## Progress and validation log

### Adversarial review — 2026-09-16

The first Sol/high read-only review found the original draft unready for
implementation. That historical verdict prompted the revisions below. The primary agent checked the local
memory contract, snapshot comparison, runtime shutdown ordering and tool batching
against source. The original findings and their disposition after plan revision follow. Design
resolution does not mean the behavior has been implemented or tested:

| Finding | Required design correction | Status |
|---|---|---|
| Standby containment | Distinguish immediate admission/output fencing from completed connection shutdown. Specify a stopping barrier and failure behavior before claiming LOCAL_AMBIENT has no cloud connections. Current runtime exposes ambient before asynchronous cleanup finishes. | Addressed in revised design; implementation tests pending |
| Memory mutation semantics | Replace the blanket close/rebuild rule above with an explicit matrix for Settings delete, spoken forget/correction, external edits, expiry, capture and daily review. Current documented behavior intentionally keeps spoken forget/correction in-session; changing it is a product change, not parity. Include same-ID changes and expiry without a revision increment. | Addressed in revised design; implementation tests pending |
| Busy deadline | Define a shared busy signal, fixed hard deadline, IDLE handling and timeout cleanup. Repeated busy events cannot extend the deadline; explicit stop always fences work immediately. | Addressed in revised design; implementation tests pending |
| SDK status contract | Test synthetic wire fixtures through the actual SDK decoder, pin transport version, define conflicting/missing/unknown status handling and require a supported SDK upgrade if needed. | Addressed in revised design; implementation tests pending |
| Async tool responses | Specify prompt independent responses by call ID, bounded duration, cancellation races and no side-effect replay. Current handler waits for siblings and the shared manager allows a one-day duration. | Addressed in revised design; implementation tests pending |
| Session lifetime | Verify model-specific limits and choose controlled closure versus resumption. Cover GoAway during reasoning, camera input, stale events and no replay. Do not assume generic Live limits apply without model verification. | Addressed in revised design; implementation tests pending |
| Transcript normalization | Define delta/cumulative/final semantics, deduplication, interruption/reconnect boundaries and exactly-once accepted activation text, excluding seeded context and reasoning from capture. | Addressed in revised design; implementation tests pending |

The review also requests focused implementation-sized changes, additional GPT
review/store/tool/main regression suites, precise acknowledgement versus completion
timing definitions, and privacy instrumentation for client construction, text
requests and late writes as well as media bytes. Verify current pricing and all
applicable charges before proposing a paid trial budget.

- 2026-09-16: Revised the normative design with bounded busy/tool/closure policies,
  SDK fixture gate, explicit memory mutation and transcript contracts, conservative
  session limits, pricing/trial gates and separate implementation milestones.
  Second Sol/high review identified four remaining text corrections: finalized
  transcript evidence, ordered mutation receipts, accepted-input deadline semantics,
  and shared camera/tool deadlines. All four are incorporated above. Reviewer
  reported no further plan blocker after those corrections. Primary review checked
  the resulting contracts for consistency. Design is ready to begin the staged
  implementation; SDK fixtures, automated gates and live acceptance remain future
  evidence, not completed validation. No runtime code or configuration changed.

- 2026-09-16: Created draft from official Google documentation and local source/SDK
  inspection. Documentation and adversarial review are authorized; implementation,
  configuration changes and paid/live trials are not started. First Sol/high review
  completed; findings incorporated into the revised design above. No runtime tests run for this
  documentation-only milestone.

- 2026-09-16: Local implementation authorized. Preserved a source/docs/tests baseline
  outside the checkout for attribution of existing changes. Luna/max agents assigned
  SDK decoding gate, shared memory lifecycle, and hybrid STOPPING/busy contracts.
  User approved the reviewed plan in place of a GitHub issue because fork issues
  are disabled. No paid sessions, environment changes, or switch performed.

- Baseline verification before source edits: `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_gemini_live.py tests/test_hybrid_runtime.py tests/test_openai_live_memory.py -q` passed 143 tests. SDK gate found that 2.19.0 discards top-level status; investigating a supported SDK resolution before adapter integration.

- 2026-09-16: Primary review resolved the status shape using Google's authoritative
  WebSocket reference: canonical status is nested under serverContent. The plan's
  earlier requirement to support the tutorial's top-level form is superseded by
  that schema evidence. Keep typed SDK decoding and fail closed on invalid/missing
  completion status; no vendor patch or extra dependency is introduced for this.

- SDK gate: primary ran `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_gemini_sdk_contract.py -q`: 4 passed. Canonical nested status and v1alpha parsing work on 2.19.0; no dependency upgrade required. Fixtures do not establish real transcript completeness. Baseline Ruff, format check and mypy passed (69 source files). Implementation branch: `feat/gemini-38-live-extended-thinking`; original dirty work preserved.

- Architecture source updated for STOPPING and shared memory session; regenerated `docs/assets/conversation_app_arch.svg` with Mermaid CLI 11.17.0 from `docs/scheme.mmd` using an isolated headless browser. PNG inspection passed. Renderer used from npm cache; no application dependency or lock change.

- Intermediate primary verification: SDK public-receive suite now passes 5 tests;
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_config.py tests/tools/test_background_tool_manager.py -q`
  passed 124 tests. These are component checks, not completed integration evidence.
  Primary adversarial review requested regressions for duplicate call IDs,
  cancellation-resistant tools and activation factories, failed provider closure,
  direct playback idle suppression, and trusted-memory/external-edit races.

- Shared runtime milestone: primary ran the hybrid runtime/smoke, console, main,
  movement and speaker-output command recorded in `docs/features/hybrid-runtime.md`:
  227 passed, 1 skipped (native speaker smoke is opt-in). Source review required
  enforcing STOPPING for cancellation-resistant activation factories, shared
  shutdown-task ownership, failed direct restart blocking, and playback estimates
  in both direct and hybrid mode. Provider/memory integration remains in progress.

- Gemini protocol milestone: primary ran the Gemini/config/SDK/tool-manager command
  recorded in `docs/features/gemini-live-provider.md`: 195 passed, with two expected
  SDK warnings from invalid-status fixtures. Shared memory adapter integration and
  the complete repository gate remain pending. No provider session was opened.

- First combined repository test run completed: 1,236 passed, 3 skipped, 5 failed.
  Follow-up covers changed shutdown failure expectations, Settings delete using
  runtime containment, queued playback preventing inactivity, and review fixtures
  using the shared lifecycle and matching clocks. Full mypy passed 71 source
  files and `uv lock --check` passed. This is not a green handoff yet.
  Independent review also found a shared tool-manager shutdown result that callers
  could ignore; uncontained tool cleanup must propagate an exception instead.

- Second combined gate passed: Ruff check/fix, Ruff format, strict mypy (71 files),
  `uv lock --check`, and `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v -rs`
  (1,245 passed, 3 skipped, 2 deliberate invalid-status SDK warnings).
  Remaining final-review changes cover the visible STOPPING UI, a pending
  invalidation callback preventing replacement, and direct-mode policy closure.
  Rerun affected checks and the full gate after these changes before handoff.

## Final offline validation — 2026-09-16

The primary agent reviewed the Luna/max implementation and required fixes for
cleanup containment, memory invalidation races, direct-mode policy reconnection,
and visible STOPPING behavior before acceptance. Final commands:

```bash
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/mypy --pretty --show-error-codes
REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -v -rs
uv lock --check
node --check src/reachy_mini_conversation_app/static/js/views/talk.js
node --check src/reachy_mini_conversation_app/static/js/views/settings.js
git diff --check
```

All passed: 1,247 tests, strict types across 71 source files, 144 formatted files,
and 156 locked packages. Two warnings are deliberate unknown-status SDK fixtures.
Three opt-in checks were skipped: native acoustic simulator/model assets, native
speaker smoke, and SDK flush smoke requiring the app to be stopped. The architecture
SVG was regenerated from Mermaid and visually inspected.

Coverage includes `test_gemini_sdk_contract.py`, `test_gemini_live.py`,
`test_gemini_live_memory.py`, `test_household_memory_session.py`, capture/review/store
and memory-tool suites, OpenAI memory/review/hybrid regressions, hybrid runtime,
console/Settings, static contracts, and the background tool manager. Fake providers
cover readiness, interruption, deadlines, concurrent tools, cancellation-resistant
cleanup, stale context, exact mutation receipts, and ambient connection/media fences.

This completes the local code and offline review milestone, not the full migration
acceptance criteria. No paid provider call, real microphone/camera session, native
simulator/hardware acceptance, or latency measurement was performed in this change.
Automatic saving of Gemini spoken transcripts remains gated until a controlled live
trial establishes transcript framing and exactly-once capture. Run that synthetic
provider trial with an agreed budget, then the plan's supervised conversation,
standby, memory and audio acceptance scenarios before switching local configuration.
No `.env`, dependency, active-provider, commit, push or PR change was made.

### User-led Gemini trial — 2026-09-16

User reported that the conversation seemed to work. The supplied terminal log
confirms two connections to `gemini-3.8-live-extended-thinking` (readiness 1,051.7 ms
and 401.5 ms), inactivity shutdown through STOPPING to LOCAL_AMBIENT, and a second
activation. Interruption flush and tool-response diagnostics were recorded; these
are internal timings, not end-to-end perceived latency. Launch selected medium
thinking and the candidate SDK playback route.

Acceptance is partial: the log also reports capture overload with dropped-frame
counter reaching 400 and repeated GStreamer recording-speed warnings. The second
session ended with a sanitized `APIError`; runtime cleanup returned to LOCAL_AMBIENT.
The log does not expose the API error's cause. Investigate that failure and audio
capture overload before declaring stable acceptance. Spoken transcript framing,
automatic memory parity, camera and hardware acceptance remain unverified. No
household content from the attachment is copied into documentation.

### Commit and memory handoff — 2026-09-16

User requested documentation, a commit, and a handoff to the memory task
`01a07c35-538f-7331-a8eb-c80b48620625`. The commit includes the existing uncommitted
memory-review and audio prerequisites with their tests/documentation because the
shared lifecycle and validated runtime build on them. This is a combined local
checkpoint, not a claim of separately reviewed upstream PRs. Private configuration
and household media are excluded; no push or permanent provider selection is made.

The memory task must treat Gemini Extended Thinking as the newly tested speech
path. Shared `MemorySession` and mutation receipts now own lifecycle across Gemini
and OpenAI. Existing capture/review opt-ins, OpenAI text-model budgets, store schema,
and spoken forget semantics remain. Gemini's ambiguous spoken transcripts are
UI-only; only exact accepted submitted text enters automatic capture. Explicit
memory tools and opted-in review remain available. Do not restore provider-specific
lifecycle duplication or enable spoken capture until real transcript framing is
verified. The first live trial's APIError and audio capture overload remain open.
