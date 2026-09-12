# Household memory lifecycle

- Status: `in-progress` — milestone 2 capture, recall, correction and deletion passed; quiet unknown-person behavior passed one spoken trial
- Owner: Reachy project
- Created: 2026-09-10
- Last updated: 2026-09-11
- Scope: milestone 1 accepted; milestone 2 capture, recall, correction, deletion and quiet unknown-person behavior passed user simulator trials. The newer uninterrupted correction/spoken-forget behavior has automated coverage but awaits a spoken check. Daily review is not implemented; flags remain default off.
- Related: [companion plan](reachy-hybrid-household-companion.md), [GPT Live](openai-live-migration.md), [provider contracts](../features/openai-live-provider.md).

## Outcome and philosophy

Reachy quietly gets to know the household, remembers a useful detail at the right
moment, accepts corrections, and lets ordinary details fade. People should not
need to manage memory, identify themselves before every sentence, or carefully
phrase ordinary conversation. Warmth comes from attention and restraint, not
from constant questions or declarations that something was saved.

Seek the simplest elegant implementation delivering most of the desired
experience (the user's “90% of perfect” goal). This is a product ambition, not a
measured accuracy claim or permission to compromise privacy or correctness.
Validate a small working milestone before implementing the next. Prefer the
existing store and tools; adopt a library only when its total integration and
operating burden is lower for a demonstrated need. Do not build a framework for
speculative future recognition, agents, or unlimited history.

## Current baseline and lessons

The upstream Conversation App introduced JSON memory and remember/forget in
[PR #390](https://github.com/pollen-robotics/reachy_mini_conversation_app/pull/390),
commit `bb1fda1`. It is an app feature, not robot firmware. `memory.py` stores
at most 60 facts of 280 characters each, with IDs and creation times. Exact
case-insensitive duplicates are avoided; alternate wording is not reconciled.
`prompts.py` prepends all facts to profile instructions. There is no structured
person, source, event time, expiry or resolution. New entries can push old ones
out regardless of importance.

The user's audit found duplicated identity entries and facts mixed across
household members. At their request, the local store was reset to one name fact.
Do not copy the deleted private entries into documentation, fixtures or migration
backups. Their prior deletion must not be undone. Current app memory is separate
from Codex's own memory.

### Official GPT Live clarification — 2026-09-11

The four official guides below were fetched and checked against our source.
Separate supported API mechanisms from local implementation and quality claims:

| Finding | Evidence / implication |
|---|---|
| GPT Live handles speech and decides when to delegate; the backend handles tools. | [Getting started](https://developers.openai.com/api/docs/guides/live). Client delegation leaves backend execution and context under app control. |
| Delegation conditions are application-authored prompt policy. | [Prompting](https://developers.openai.com/api/docs/guides/live-prompting). The guide recommends concrete conditions and a compact voice prompt; it does not prohibit delegating memory work during casual conversation. |
| Transcript processing can trigger application-owned work without a delegation event, in either delegation mode. | [React to transcript fragments](https://developers.openai.com/api/docs/guides/live-delegation#react-to-transcript-fragments). Accumulate meaningful new text, process with application logic or a lightweight model, and reject outdated results. This explicitly supports the mechanism proposed for invisible extraction; extraction accuracy is still to be measured. |
| Lightweight transcript checks may use `gpt-5.6-luna` with low reasoning. | [Transcript deltas](https://developers.openai.com/api/docs/guides/live-conversations#transcript-deltas). This is a documented example, not an accuracy guarantee for memory extraction. |
| Client delegation events carry metadata and an offset, not task text. | [Client context](https://developers.openai.com/api/docs/guides/live-delegation#keep-the-conversation-context-in-your-application). We must supply accumulated transcripts and verified state; a delegation does not itself supply a complete user request. |
| Input/output transcript events distinguish user speech from assistant speech. | The [session guide](https://developers.openai.com/api/docs/guides/live-conversations#transcript-deltas) documents delta/start_ms/end_ms, not named household identities or a diarization contract among multiple humans. Do not interpret “each speaker” as automatic Eduardo/Adriana identification. |
| Fragments lack authoritative turn completion and may arrive unevenly. | The same guide explicitly requires preserving text/timing, allowing overlap and revising grouping; the delegation guide notes transcription errors. Keep incomplete/corrected text revisable. |
| Quiet context can be supplied without asking for speech. | [Session context](https://developers.openai.com/api/docs/guides/live-conversations#add-context-during-the-conversation): `session.thinking.append`, `delegation_id: null`, at most 500 tokens. Acceptance is not proof of complete model consumption; quiet context can influence later speech and is not a privacy boundary. |

Verified local choices: `openai_live.py` passes resolved profile/memory to both
Live and the Luna backend. Its `_LIVE_DELEGATION_POLICY` currently discourages
casual-conversation delegation. Its rolling buffer retains at most 64 transcript
fragments and 12,000 characters, evicts older fragments and resets per session.
These are application limits, not OpenAI retention limits or a day-long archive.
The documentation recommends app-owned history for client context; our bounded
implementation deliberately provides only recent context. Incremental extraction
must consume new active-session fragments before eviction, not read the buffer
once at the end of the day. Persist short notes rather than expanding to an
unbounded transcript archive.

Decision: milestone 2 uses documented transcript-driven, application-owned
extraction independently of the Live delegation decision. Keep explicit
remember/forget requests on the existing tool path and deduplicate both paths.
No extra sideband is required for our primary server WebSocket to receive events.
Do not introduce a new socket, change delegation mode, or ask Live to announce
routine saves merely to collect memory. Quiet context refresh is available when
useful, but cannot erase previously injected text or guarantee immediate adoption
of a correction. The deletion/reset contract below remains necessary.

Remaining validation concerns are capture accuracy, unfinished/late corrections,
unknown-person attribution, processing cost and lifecycle races. Whether the API
supports processing transcripts independently is established, not a hypothesis.

## Decisions and iterations from discussion

| Iteration | Decision and reason |
|---|---|
| Shared sticky note | Preserve the convenience of silent capture; fix misattribution and duplicate facts. |
| Separate people and SQLite proposal | Person ownership remains necessary. Defer SQLite and framework adoption; storage size is not today's main problem. |
| Voice plus face recognition first? | Defer until real hardware. Memory must work with unknown identity even after recognition is added. |
| Ask before saving ambiguous facts | Rejected as the default: turns conversation into a form. Ask only when understanding matters to the conversation; otherwise keep uncertainty or skip durable capture. |
| Temporary, dated and permanent memories | Accepted as lifetime hints, but permanence means “until corrected/forgotten,” not unquestionable truth. |
| Expire temporary facts | Refined: leaving active context does not mean the event resolved or never happened. Separate attention from retention. |
| Hot/cold memory | Use “on its mind” versus “available to recall”; these are views over one store, not separate infrastructure. |
| Background capture | Useful because casual speech may bypass Luna. Limited to addressed active conversations, never standby audio. |
| Daily notebook and dreaming | Collect useful short notes immediately, then perform a bounded periodic consolidation. Daily review is not required for same-day recall. |
| Exceptions to daily review | Explicit remember requests, corrections, resolutions and deletion apply immediately. |
| Correction during conversation | Keep talking: the user supplied the new fact in the current chat. Persist the change for future sessions without forcing standby. |
| Spoken forgetting | Delete the saved note and keep talking. The current chat may still contain the detail; pause automatic capture until the next wake to avoid saving it again. |
| Settings Forget | Stronger action: stop the current session before deleting so the active cloud context is discarded too. |
| Review timing | Catch up once per local day during an active session, not a new nightly service. Preserve conversational continuity; deletion races must still prevent stale writes. |
| Proactivity | Defer unsolicited check-ins and reminders. Recall quality comes first. A birthday memory is not a scheduled birthday announcement. |

## Scope and non-goals

In scope: person-labelled notes, uncertain attribution, grounded extraction,
immediate recent recall, lifetime handling, correction/deletion, daily
consolidation, bounded older recall, local inspection and progressive validation.

Out of scope: biometric identity, persistent audio or full transcript archives,
standby extraction, personality inference, psychological profiles, inferred
sensitive facts, knowledge graphs, embeddings, separate memory servers,
multi-agent review chains, autonomous tool execution during consolidation,
notifications, elaborate UI and automatic migration to another speech provider.
This is a follow-on to the companion MVP, not a retroactive expansion of its
phases 0–5. Gemini fallback and the accepted wake behavior must remain usable.

## User-visible behavior

- “Adriana likes this artist” may quietly become a note about Adriana, reported
  by the speaker if known. Speaking about a person does not establish who spoke.
- Unidentified “I like this” is usable in the current exchange but must not become
  Eduardo's fact. No name guessing from accent, language, topic or age.
- “Maybe Porto at Christmas” remains a tentative plan. Cancellation supersedes
  it immediately; the date passing does not establish the trip happened.
- A missing-luggage note stops dominating context after its active window.
  Later recall says “you mentioned it was missing,” not “it is still missing.”
- “It arrived” resolves the relevant ongoing situation when the reference is
  clear. Ambiguous corrections must not edit the wrong person's memory.
- A birthday can be lasting after one clear statement; repeated mentions are not
  required. Do not invent a birth year or interpret a birthday as current age.
- “Forget that” removes the resolved target promptly. Ask a brief clarification
  only when the referent cannot safely be determined.
- The assistant's own repetition never confirms a memory or extends its lifetime.
- Useful recall should be quiet and specific; no recitation of the whole profile.

## Minimal data contract (proposed v2)

Keep one versioned JSON store in the existing instance data directory. Use typed
records, atomic replacement, and one app-owned writer. Persistent memory remains
provider-neutral. No installed service or new dependency is required initially.

| Field | Meaning |
|---|---|
| `id` | Stable memory ID used for updates and deletion. |
| `subject_id` | Known person, `household`, or null for unknown. |
| `text` | Short grounded statement, at most 280 characters; no credentials. |
| `kind` | `note`, `situation`, `plan`, or `fact`. Lifetime hint, not truth. |
| `certainty` | `stated` or `tentative`; no invented numerical confidence. |
| `source_person_id` | Who supplied it, null when unknown; distinct from subject. |
| `source_ref` | Opaque active-session/batch reference for duplicate prevention; not a stored transcript. |
| `learned_at`, `updated_at` | UTC times of actual user evidence or correction. Recall does not update these. |
| `event_start`, `event_end` | Optional explicitly grounded date/time with precision; date-only is allowed. |
| `active_until` | When to stop automatic inclusion; null for lasting facts. |
| `delete_after` | Actual retention deadline; null only for lasting facts. |
| `status` | `current`, `resolved`, or `superseded`. |

People have stable IDs and explicit names/aliases only. Do not auto-merge people
using fuzzy name similarity. Seed household identities through explicit user
statements or settings; membership does not authorize disclosure of every fact.
V1 supports only ordinary household-safe facts. Exclude private/sensitive notes
from capture rather than introducing a private-vault permissions system now.
Unknown-subject notes may remain briefly in the originating session but must not
be injected into another speaker's later session as personal facts.

### Lifetime defaults to validate, not research-established constants

| Kind | Automatically relevant | Retention |
|---|---|---|
| Ordinary note | 48 hours from user evidence | 7 days |
| Ongoing situation | 7 days from latest user update | 30 days after leaving active context |
| Dated plan | Through explicit end + 7 days | 30 days after leaving active context |
| Lasting fact | Relevant until changed | Until correction or explicit deletion |

If a plan date cannot be grounded, use situation defaults and preserve the
original uncertainty. Resolve relative dates using supplied conversation time
and local timezone; do not guess a return date. Future event dates can keep a
plan beyond ordinary note limits. Recurring dates remain facts, not cron jobs.
Resolution ends automatic inclusion immediately; it may retain a short dated
history under the existing retention deadline. Superseded entries are excluded
from ordinary retrieval and removed on the same bounded schedule. Do not
silently transform a short note into a lasting inferred trait.

The clock filters stale/deleted content before every memory read or write; a
nightly job is not required for expiry. Explicit deletion also invalidates
pending candidates and cached context. Already sent cloud context cannot be
unsent: close/reset the affected active session when needed, and do not claim
provider-side erasure. Avoid retaining private deletion text in tombstones;
opaque IDs/revisions can reject stale background writes.

## Capture, recall and daily consolidation

### Capture without conversation interruptions

Use existing remember/forget first for explicit requests. At milestone 2, use
the documented transcript-driven application-work mechanism for a bounded silent
Luna extraction pass, with a dedicated prompt and no action
tools. Feed only new active-conversation transcript fragments plus necessary
short prior context, labelled by source role and time. Assistant text supplies
context but cannot serve as evidence of a user fact. Treat quotes, fiction,
hypotheticals, uncertainty and corrections explicitly. Exclude incomplete or
ambiguous fragments rather than completing them imaginatively.

Proposed bounds: one worker, up to 12,000 input characters and 1,000 output tokens
per extraction, a 20-second timeout and at most once per 10 seconds of active conversation
(revised from 60 seconds after the third simulator trial).
M2 deliberately omits the proposed final session-end flush. Buffer stays in RAM, capped at 24,000 characters;
overflow drops oldest unprocessed content with counters, not private logs.
Notes already committed are available immediately at the next activation.

Background cloud work needs an explicit privacy contract: local standby still
means no cloud sessions or uploads. Cancel unfinished extraction on deactivation;
discard the activation's pending RAM fragments.
Do not delay standby or keep the voice session billed to finish memory work.
This can miss late notes, including complete phrases waiting for the request
interval, when a short conversation ends first. Measure that loss before changing
the cadence or adding machinery.
Decision: no flush or cross-activation buffer is simpler and makes the standby
boundary explicit. Revisit only if measured capture loss warrants it.

### Daily review

Once per local calendar day when eligible and idle, review only new/changed
notes and a bounded relevant existing set. “Idle” does not itself authorize
network traffic: a cloud Luna review must run in an explicitly enabled maintenance
window, or on the next active session without delaying speech. Default v1 is
catch-up at the next active session; no nighttime cloud connection while ambient.
No Codex automation or OS cron is required: the app checks its last successful
review date. An absent/offline app catches up once, not once per missed day.

One tool-free structured request, 20,000 input characters, 2,000 output tokens,
30-second timeout, one review worker. Outputs propose keep, merge, update,
resolve or promote by stable IDs. Validate schema, evidence references, person
ownership, dates, revision and retention limits before atomic commit. No new
facts without cited input evidence. Do not upgrade certainty during promotion.
Persist batch/revision metadata so retries or restarts do not repeat operations.
Failures leave prior memory intact; no automatic tight retry loop.

Proposed initial ceiling: 60 extraction calls and one successful consolidation
per local day, with at most one failed consolidation retry in a later eligible
window. Enforce a total 65,000 generated-token allowance per day; stop background
work at either ceiling without interrupting speech. These call limits bound work, not dollars. Before enabling paid processing,
record current model pricing and a daily token/call ceiling in the feature doc.
Use the configured Luna backend model, not GPT Live audio sessions. An unavailable
backend skips maintenance; it must never stop normal conversation.

### Context and older recall

Milestones 1–2: bounded initial context containing a short known-person profile,
shared safe facts and relevant current notes; proposed total 3,000 characters.
Label dates and tentative/resolved status. Never assume the current speaker is
the store's owner. Give lasting core facts a protected budget so incidental notes
cannot evict identity or birthdays, as the current newest-60 policy can.

Milestone 4: a simple local recall tool filters by person, status and dates and
uses keyword matching, returning at most five notes/2,000 characters. Let Luna
select the relevant result for the conversation. Do not add embeddings unless
measured paraphrase misses justify them. Do not preload the entire history.
Both GPT Live and Luna must get compatible memory revisions, and corrections
must invalidate stale prompt snapshots rather than wait for another daily review.

## Milestones and acceptance gates

### 1. Smarter notebook

Implemented 2026-09-11 with Luna max agents for store, tools and compatibility,
and primary-agent integration/adversarial review. Local validation passed;
the user accepted the [synthetic examples](../features/household-memory.md#synthetic-review--2026-09-11)
and authorized milestone 2 on 2026-09-11. No household migration has been run.

Implement v2 records, person ownership, immediate mutation APIs, lifetimes,
read filtering, atomic storage and migration tests. Retain the simple tools;
no background calls yet. Proposed tools: extend remember with optional subject,
kind/certainty and grounded dates; add an update operation by known ID; preserve
forget's natural-language interface with unambiguous target resolution.
Unknown or invalid tool inputs return an error without mutating storage.

Gate: deterministic tests for attribution, ambiguity, duplicate updates,
tentative plans, relative dates, expiry versus resolution, corrections and
forget. No extra conversational questions for unambiguous capture. Human review
of a small synthetic memory list before proceeding.

### 2. Invisible collection and immediate recall

Implementation authorized and started 2026-09-11. First provider is GPT Live;
other providers retain explicit tools but no automatic capture. The separate
`HOUSEHOLD_MEMORY_CAPTURE_ENABLED` flag defaults false. Implement store revision
checks, bounded extraction, provider invalidation and local inspection together.
No daily consolidation, recognition or ambient processing is included.

Wire bounded active-conversation capture and context injection across provider
boundaries. Tools, extraction and background writers share one mutation path.
No transcript disk archive. Test actual Live-style delta sequences, not only
clean final sentences. Provide a minimal inspect/delete surface using existing
console patterns rather than a new dashboard. The console now has local-only inspect/delete endpoints and a small Settings view.

Gate: casual personal facts survive a new activation without an explicit save
request; unclear speakers are not assigned names; correction/deletion wins over
in-flight extraction. Measure extraction calls, delay and dropped fragments.
Validate a short human simulator conversation; speech/standby remain unaffected.
This completes the first product checkpoint. Do not start 3–4 merely because
1–2 compile.

### 3. Daily tidy-up

Add revision-safe, bounded consolidation with once-daily catch-up and deterministic
retention cleanup. Implement the default active-session maintenance policy above;
nighttime cloud work requires a separately documented opt-in, not silent enablement.

Gate: a birthday survives one mention, duplicated notes merge, cancellation
updates the plan, a resolved situation stays resolved, unknown identity remains
unknown. Same batch twice is harmless. Simulate time, restart, offline operation,
malformed output and deletion racing review. No real multi-day waiting required.

### 4. Natural older recall and product validation

Add bounded local recall, then tune lifetimes and active context from evidence.
Proposed evaluation: a fixed synthetic suite of at least 20 short scenarios plus
ordinary user trials. Target at least 90% of expected useful recalls in that suite;
report sample size and failures, not a general “90% perfect” claim. Require zero
wrong-person durable assignments, stale writes after deletion, or ambient cloud
traffic in the controlled boundary tests. Fix any such failure before rollout.

Gate: relevant older memory recalled, unrelated memory not volunteered, no repeated
follow-ups, reliable corrections, no memory-management dialogue imposed on the
user. User acceptance determines companion quality; test scores alone do not.

## Adversarial scenarios and required responses

| Challenge | Required result |
|---|---|
| Missing luggage ages out | Historical report, not assumed delivery or continued loss. |
| “Maybe Porto” then “cancelled” | Tentative plan becomes inactive immediately; never inferred completed trip. |
| Assistant repeats a guess | No confirmation, promotion or lifetime extension. |
| Child quotes a character | No identity/preference fact based on fictional speech. |
| “I like it” from unknown person | Session-only uncertainty; no Eduardo default. |
| Person changes mid-conversation | Earlier self-identification is not authoritative for all later turns. |
| Same note arrives via tool and extractor | Shared evidence IDs and update policy prevent duplicate side effects. |
| User deletes while review runs | Stale revision rejected; forgotten note cannot reappear. |
| Day boundary/DST/restart | UTC storage, local-day review key, at most one successful review per day. |
| Store full | Expired/ordinary notes discarded first; never silently evict protected facts. Return bounded-capacity status if all entries are protected. |
| Corrupt file/model timeout | No destructive replacement; report metadata-only error and keep conversation working. |
| User asks a secret near others | V1 did not capture private material; no inferred household-wide permission. |
| Repeated mention of birthday | Update existing fact; do not require repetition or trigger an announcement. |

Proposed store caps: 60 lasting facts plus 200 bounded notes. Change only from
observed capacity pressure; avoid unlimited archives and significance scoring
systems in the first release.

## Integration, migration and rollback

Relevant code: `memory.py`, `prompts.py`, `tools/remember.py`, `tools/forget.py`,
`conversation_handler.py`, `hybrid_runtime.py`, `openai_live.py`,
`openai_live_backend.py`, `config.py` and the legacy memory Python API.
Read each provider's feature documentation before changing shared behavior.
Use SDK public contracts; do not put durable state inside a provider instance.

Migration is explicit and one-way only when the feature is enabled. Parse v1
without mutating it; carry existing text and IDs but do not guess people from
ambiguous “user” facts. Show unknown assignments for review. The user-cleared
store is the migration source, never an old backup. Keep any required recovery
copy local with restrictive permissions and a short documented lifetime; propagate
explicit deletion to it. Do not create an undeletable transcript or backup trail.
Rollback disables capture/consolidation and cloud maintenance; retain inspectable
v2 data. Do not silently feed old v1 memories back into prompts or duplicate the
store. Define downgrade/export behavior before migration lands.

Feature defaults off until milestone acceptance. Proposed configuration surface:
`HOUSEHOLD_MEMORY_ENABLED`, `HOUSEHOLD_MEMORY_CAPTURE_ENABLED`,
`HOUSEHOLD_MEMORY_CONSOLIDATION_ENABLED`; all default false. Use internal bounded
defaults for lifetimes initially instead of exposing many knobs. Any eventual
maintenance opt-in and spend ceiling must be explicit and documented before use.
Disabling a feature removes unavailable tool guidance too.

## Validation and documentation

Extend existing `tests/test_memory.py`, memory tool tests and prompt tests after
verifying their current paths; add focused lifecycle/provider integration tests
for new contracts. Use fake clocks and fake model outputs for all routine tests.
Cover privacy, migration, cancellation, idempotency, malformed output, bounded
storage, date interpretation and stale context. Never use private recordings as
committed fixtures. Runtime/perceived-quality claims require simulator evidence;
Pi/hardware claims remain deferred.

Before each implementation handoff run the repository gate from AGENTS.md with
`REACHY_MINI_SKIP_DOTENV=1` for pytest so private configuration does not leak into
tests. Record exact commands/counts and skipped checks. No new test suite is
required for this planning-only change; existing full gate is run for compliance.

Implementation must update root README, `.env.example`, plans index, companion
status, and create `docs/features/household-memory.md` with schemas, tools,
privacy/maintenance windows, deletion limits, tests and operational procedures.
Update `docs/scheme.mmd` when runtime wiring changes and flag required generated
SVG regeneration. Do not create feature documentation claiming behavior exists
before implementation. No runtime diagram change is made by this plan.

## Research and alternatives (consulted 2026-09-10)

These are upstream design references, not installed dependencies or benchmark
endorsements. APIs/defaults evolve; recheck before adopting any library.

| Project / repository | Reference and lesson | Decision |
|---|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw) | [Memory](https://docs.openclaw.ai/concepts/memory), [dreaming](https://docs.openclaw.ai/concepts/dreaming), [search](https://docs.openclaw.ai/concepts/memory-search): daily notes, curated facts, scheduled consolidation and recency ranking. Normal heartbeat is not memory maintenance itself. | Borrow notebook + bounded consolidation; not its full agent/runtime, graph/wiki or score gates. Decay is attention, not truth. |
| [Hermes](https://github.com/NousResearch/hermes-agent) | [Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/): small USER/MEMORY files, add/replace/remove, session snapshots and searchable history. | Borrow compact editable facts; avoid full transcript persistence and assuming one profile equals one household member. |
| [Honcho](https://github.com/plastic-labs/honcho) | [Hermes integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho/): peer identities and richer inferred user models. | Defer; a microphone lacks messaging sender IDs, and inferred profiles exceed scope. |
| [Mem0](https://github.com/mem0ai/mem0) | [Companion example](https://docs.mem0.ai/cookbooks/essentials/building-ai-companion), [search](https://docs.mem0.ai/core-concepts/memory-operations/search): user filtering and expiry metadata with app-owned pruning. | Reconsider if extraction/retrieval complexity is demonstrated; it does not solve identity or lifetime semantics for us. |
| [Graphiti](https://github.com/getzep/graphiti) / Zep | [Temporal search](https://help.getzep.com/searching-the-graph): event validity versus learned time; old facts can become historical without being erased. | Borrow distinction; defer graph database and associated extraction machinery. |
| [Hindsight](https://github.com/vectorize-io/hindsight) | [Retain](https://hindsight.vectorize.io/developer/retain), [freshness](https://hindsight.vectorize.io/blog/2026/06/17/freshness-aware-memory): event/learned dates and derived summaries lagging source facts. | Borrow provenance and stale-revision protection; defer reflect loops and multi-layer inferred models. |
| [Letta](https://github.com/letta-ai/letta) | [Core blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks), [archive](https://docs.letta.com/v1-sdk/memory/archival-memory): compact active context plus on-demand recall. | Borrow separation; no agent framework replacement. |

## Progress and open implementation decisions

- 2026-09-10: Recorded the conversation's iterations, research, adversarial
  findings and four milestone gates. Codified the product philosophy in AGENTS.md.
  This document authorizes no recording, background processing, install or spend.
- Numeric lifetimes/caps above are proposed starting defaults, not previously
  accepted measurements. Confirm through milestones, not more up-front machinery.
- Before milestone 2: establish transcript completeness/capture yield and the
  token budget; select the cheapest adequate extraction frequency from tests.
- Before enabling daily cloud work: maintain the explicit ambient boundary or
  obtain a separately approved maintenance policy. Default catch-up needs none.
- Before migration: finalize v2 serialization and deletion-safe recovery behavior
  against the legacy Python/JSON contract; do not silently downgrade v2 records.

### Planning-change validation — 2026-09-10

- `.venv/bin/ruff check .`: passed.
- `.venv/bin/ruff format --check .`: 120 files already formatted.
- `.venv/bin/mypy --pretty --show-error-codes`: passed, 64 source files.
- `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`: 967 passed, 2 skipped.
- `git diff --check` and local Markdown link checks: passed.
- Documentation only; no memory runtime changes, migration, installs or paid calls.

- 2026-09-11: Replaced ambiguous GPT Live baseline language with four-guide
  official evidence, verified local policy/buffer limits, and the explicit
  independent transcript-processing path. No runtime implementation changed.

- 2026-09-11 documentation validation: Ruff check/format and strict mypy passed;
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`: 967 passed, 2 skipped.
  Local plan links and `git diff --check` passed. No paid API calls.

### Milestone 1 decisions — 2026-09-11

- Use a small local JSON implementation without new dependencies. The filename
  stays `memory.v1.json`; its version 2 envelope holds structured entries. No
  backup or downgrade copy can resurrect deleted facts.
- New durable notes require an explicit subject label. Unknown personal notes
  are skipped quietly; bounded session-only capture comes in milestone 2.
  Migrated unknown facts remain available for local review but are not injected.
  Stable labels are caller-supplied; no fuzzy identity merging or recognition.
- Retention filtering happens on every read; physical pruning happens on writes,
  not through a new timer. Explicit deletion is immediate. The file can contain
  expired data while the app is unused; do not confuse filtering with disk erasure.
- M1 capacity is conservative: after pruning retention-expired entries, a full
  category rejects a new note/fact rather than silently evicting retained content.
  Ordinary-note eviction is deferred until observed capacity pressure justifies it.
- Explicit ISO dates/aware date-times only at the storage boundary. Relative date
  interpretation needs reliable conversation time/timezone; the store never guesses.
- Profile defaults permitting both remember and forget receive update_memory when
  the feature is enabled; explicit Tool access overrides must select it themselves. Disabling it removes this tool and v2 prompt guidance.
- No existing console memory endpoint was found in this checkout. M1 exposes
  Python inspection/migration; a minimal console surface remains milestone 2.
- Provider snapshots are unchanged in M1. External maintenance requires stopping
  the app; active-session correction/deletion invalidation remains an M2 gate.
- Adversarial review caught resolved-fact reload failure and unsafe evidence-ref
  reuse. Regression tests must cover these before the milestone is accepted.

### Milestone 1 validation — 2026-09-11

- Implemented core notebook, tool routing, legacy compatibility, default-off
  configuration and bounded prompt snapshots. No new dependencies.
- Primary review fixes covered resolved/superseded fact reload, exact millisecond
  migration, canceled future-plan retention, expired-byte deletion, source-ref
  conflicts, failed atomic writes, malformed argument logging, tool-manager
  injection and explicit Tool access overrides.
- Focused command in [feature documentation](../features/household-memory.md#validation):
  **121 passed**. Full repository gate: Ruff passed (126 formatted files), strict
  mypy passed (66 source files), `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`:
  **1013 passed, 2 skipped**. `uv lock --check` and `git diff --check` passed.
- Existing opt-in native acoustic simulator and native speaker smoke tests were
  skipped; no new microphone, hardware or real-provider acceptance claimed.
- Temporary synthetic notebook exercise passed; temporary files removed.
  Actual household memory and `.env` unchanged. No paid calls or migration.
- M1 engineering complete; user accepted the synthetic review and authorized M2
  on 2026-09-11. M2 implementation follows; daily review remains gated.


### Milestone 2 implementation decisions — 2026-09-11

- User authorized implementation after accepting M1. Luna max agents own store,
  capture and provider integration; primary owns review, settings and documentation.
- Added monotonic revisions and atomic batches to the same local store. Clear
  invalidates pending work even when already empty. No second memory database.
- Added a separate default-off capture flag. Capture uses only active GPT Live
  deltas; no final flush, ambient context, archived recordings or transcript file.
- Corrections/deletion close old Live/Luna context. Incremental provider messages
  cannot guarantee erasure of earlier instructions; routine additions wait for
  the next activation's snapshot. This deliberately trades a conversation restart
  after an explicit edit for a clear, testable guarantee.
- Same-computer Settings inspection/deletion reuses the existing console.
  Synthetic browser review showed the saved test note, then removed it after
  Forget. Temporary test data only; actual household memory remains unchanged.
- Daily capture reservations are counts-only metadata and survive restarts.
  Pricing and concrete limits are in the feature document. Daily consolidation
  and its additional allowance remain deferred to M3.
- M2 capture adds grounded notes and applies clear same-person corrections or
  resolutions by an existing ID in the supplied context. Unclear conflicts are
  skipped; reassignment/deletion remain explicit. This honors the original
  immediate-correction requirement without implementing daily reconciliation.
- Real-provider synthetic checks: English named fact and Spanish named/unknown
  separation passed. A correction initially replaced the ID; clearer instructions
  made the repeated probe update the existing ID (6.83 seconds). Four bounded
  fictional-data requests total; temporary stores removed. This is not speech
  recognition or broad memory-quality acceptance.
- Independent reviews covered cleanup bounds, stale Responses/tool rounds, budget
  rollback and the synchronous cloud-to-ambient fence. Content sensitivity and
  semantic accuracy remain model-based; no privacy-vault guarantee is claimed.
- Final primary verification: **184 focused tests passed; 1076 passed, 2 skipped**
  in the full suite. Ruff (130 files), strict mypy (67 source files), lockfile
  validation, JavaScript syntax, local documentation links and secret diff scan
  passed. Commands and limits are recorded in the feature document.
- M2 engineering is complete. The short human simulator conversation remains
  pending; both flags remain off and actual household memory is unchanged.
  M3 daily consolidation remains deferred until this experience is validated.
  Architecture source changed; generated SVG refresh remains outstanding.

### First human M2 trial — 2026-09-11

- User ran migration and enabled both flags for the simulator trial. The legacy
  name appeared as unassigned; such notes are deliberately excluded from prompts.
  The UI now states that exclusion explicitly rather than implying recall.
- A Spanish self-introduction and preference in the same sentence were understood
  within the active conversation but not recalled after standby. No new preference
  appeared in Settings. This checkpoint failed; it is not a successful M2 acceptance.
- The supplied log contains one successful Responses HTTP request but no capture
  outcome counters, so it does not establish which validation/cancellation path ran.
  Investigating split transcript evidence and adding content-free capture diagnostics.
- Reproduced and repaired split-sentence attribution: preserve raw delta spacing
  and validate contiguous full-fragment evidence within a bounded sentence group.
  No identity carry across sentence/assistant boundaries or gaps above three seconds;
  missing text and backward/overlapping intervals cannot establish a joined identity.
  This is a heuristic requiring a human retry, not speaker recognition. The local
  fresh-session regression confirms assigned facts enter new Live instructions.
- No household audio or transcript was uploaded for this investigation. Reproductions
  use fictional text and fake providers. Daily consolidation remains deferred.
- Repair verification: **54 focused tests passed; 1086 passed, 2 skipped** in the
  full gate (20.76 seconds). Ruff, formatting, strict mypy, JavaScript syntax and
  diff checks passed. Human retry still pending; restart with the same flags,
  without repeating migration, and ask about the named person to isolate recall.

### Second human M2 trial — 2026-09-11

- The new summary established a request-eligibility failure: 31 fragments received,
  zero requests attempted, 10 punctuation-based incomplete checks, no budget block
  or stale result. The extractor never ran, so selection/recall was not the cause.
- Removed the terminal-punctuation requirement for request eligibility. Settled
  user text goes through the existing bounded extractor; semantic completeness is
  assessed there. Existing evidence, attribution, cancellation and revision checks
  remain. `incomplete_batches` now counts non-completed extractor responses.
- Human acceptance remains failed/pending retry. No migration or actual memory
  edits are part of this repair; daily consolidation remains deferred.
- Two bounded real Luna requests with temporary fictional text passed: a split
  Spanish introduction/preference without punctuation saved one correct note;
  an unfinished clause saved none. Temporary stores removed, no household upload.
- Primary focused capture/provider tests: **51 passed**. Full gate: Ruff, format,
  strict mypy and **1090 passed, 2 skipped in 19.64 seconds**. Diff check passed.

### Third human M2 trial — 2026-09-11

- Local Settings API confirmed one unassigned legacy note and no assigned notes.
  Shutdown counters: 48 fragments, one completed request, zero applied proposals,
  zero failures/incomplete responses/stale batches/budget blocks. This rules out
  the previous punctuation gate but cannot distinguish no proposal from validation
  rejection; the response content was intentionally not logged or persisted.
- Added content-free per-attempt outcome logs: proposed/validated/applied counts
  and cancellation/invalidation/noncompletion. These diagnostics require restart.
- Local streamed-text reproduction also found lost context: a prefix request
  canceled by the next user delta discarded the self-introduction. The retry
  lacked attribution. The 60-second request interval also exceeds the normal
  30-second inactivity window. Repairing scheduling with a trailing user pause,
  preserved bounded continuation context and a shorter interval; daily budget and
  ambient cancellation remain unchanged. This is not yet human acceptance.
- Independent parser review found a prompt/validator mismatch: an explicit
  `status: current` on a new note was rejected although the prompt allowed it.
  The parser now accepts that default; new resolved notes remain rejected.
  The regression failed before the change and both status cases passed afterward.
- Scheduler repair retains the bounded active text window across attempts, waits
  for user-text settling after cooldown, and reads the latest buffer at dispatch.
  A user-generation marker prevents assistant-only or unchanged-text retries.
  New text still fences in-flight results; external changes and standby clear RAM.
- Real streamed fictional Luna probe passed: introduction, three-second pause,
  then four continuation pieces without punctuation. Two requests, one expected
  cancellation, one correct preference saved; temporary store removed. No household
  upload or memory edit. This exercises streaming rather than a finished input string.
- Primary full gate: **1095 passed, 2 skipped in 19.47 seconds**, Ruff/format/mypy
  passed. Primary capture/provider focused tests: **56 passed**; capture-only agent
  tests: **44 passed**. Diff check passed. Human
  simulator acceptance remains pending; do not begin daily consolidation yet.

### Fourth human M2 trial — 2026-09-11

- Outcome is now specifically local rejection: one proposal, zero validated notes.
  Final counters were 69 fragments, three attempts, two cancellations, zero applied
  notes and no incomplete responses, stale batches or budget blocks. This does not
  reveal the exact rejection rule, because response content is not persisted.
- Added aggregate rejection categories (fields, evidence, continuity, attribution)
  without logging text, quotes, names or IDs. Independent review reproduced valid
  overlapping timestamps and exact excerpt evidence being rejected unnecessarily.
  Repair these cases using full original text for grounding; retain unrelated-speaker,
  missing-text, sentence and large-gap boundaries. Human acceptance remains pending.
- Repairs accept overlap up to three seconds with ordered starts and exact excerpt
  quotes grounded against full original contiguous text. Manufactured identity,
  backward starts, missing fragments and unrelated speech remain rejected.
- Real fictional Spanish overlap probe passed: one request, one proposal, one
  validated/applied note, zero rejection counts. Temporary store removed and no
  household content uploaded. The original run's rejection category remains unknown.
- Primary verification: **58 focused tests passed; 1097 passed, 2 skipped** in the
  full suite (19.37 seconds), with Ruff, formatting and strict mypy passed. Human
  retry remains pending; these synthetic checks do not establish spoken acceptance.

### Human capture and recall passed — 2026-09-11

- User confirmed the note appeared in Settings, then was recalled after standby
  and a new wake. This validates the main spoken capture-to-recall path for this trial.
- Supplied logs corroborate an empty first extraction, then one proposed, validated
  and applied note at 20:29:10 local time. First activation: 27 fragments, two
  requests, zero failures and one applied note. Standby occurred at 20:29:36, with
  a new activation at 20:29:43. Recall correctness is user-observed, not inferred
  from API success alone.
- Correction/deletion across a fresh activation and unknown-person behavior still
  need human validation. No broad accuracy, hardware or daily-consolidation
  acceptance is claimed. Feature defaults and private store were not changed here.
- The log separately contains an HTTP 400 from the simulator stop-current-app
  request, followed by runtime closure; this does not invalidate memory recall
  but is not evidence that the daemon stop endpoint succeeded.
- Documentation-only update; `git diff --check` passed. No code or tests changed.

### Human correction and deletion passed — 2026-09-11

- User confirmed a spoken correction updated the visible note and the corrected
  value was recalled after a new wake. Forgetting in Settings also prevented recall
  after waking again. These two user-observed checks passed.
- Logs show delegated `update_memory` at 20:33:25 and 20:35:20, each immediately
  followed by `OpenAI delegation failed error=RuntimeError` and then standby with
  `reason=provider_stopped`. Source inspection shows that a changed memory snapshot
  deliberately fails the backend context guard, which raises a generic RuntimeError
  and is logged as delegation failure. This is consistent with the intended reset;
  the generic log alone cannot identify an exception message or stack trace.
- Clearing old conversation context after an edit is intentional. Abrupt standby
  and the misleading failure warning are an observed UX/diagnostic rough edge,
  not evidence the memory write failed. No code behavior changed in this review.
- Unknown-person human validation remains pending. No hardware or general accuracy
  acceptance claimed. Documentation-only verification: `git diff --check` passed.

### Quiet unknown-person policy — 2026-09-11

- User reported Reachy asked who they were so it could save a preference. This
  avoids guessing attribution but fails the unobtrusive conversation goal.
- Added an enabled-only shared instruction for Live and Luna: use unclear personal
  details in the current chat, quietly skip durable storage, and do not ask for
  identity merely to save an ordinary detail. Questions needed for the actual
  answer or an explicit save request remain allowed. Empty and unassigned-only
  notebooks receive the same policy; flag-off legacy behavior is unchanged.
- Regression coverage: `tests/test_prompts.py` exercises empty/unassigned memory,
  named memory reaching Live and Luna, and disabled legacy behavior. These tests
  verify instruction delivery, not model compliance. No live provider probe or
  fresh spoken acceptance is claimed; the next normal conversation can check it.
- Primary full gate: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
  `.venv/bin/mypy --pretty --show-error-codes`, and
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` passed: **1101 passed,
  2 skipped in 19.27 seconds**. Skips remain the opt-in native tests. Daily review
  remains unimplemented; no private notebook or runtime flags were changed.

### Corrections preserve conversation — 2026-09-11

- User confirmed the quiet unknown-person policy worked in a spoken trial.
- Supersedes the earlier correction-reset decision: edits to an existing memory
  ID no longer invalidate Live/Luna context. The current conversation already
  contains the user's correction; the next activation loads the updated notebook.
  There is no hot prompt rewrite. External maintenance still requires stopping
  the app, so this does not promise live synchronization of out-of-band edits.
- Removing an ID or failing to read an existing snapshot still closes context.
  Settings forgetting still stops the session before deletion. Capture revision
  fences remain unchanged, so old extraction results cannot overwrite newer edits.
- Regression tests cover corrections with capture enabled/disabled, continued
  audio output, and deletion after correction. Existing pre-delegation and
  in-flight deletion tests retain the strict forgetting boundary. The correction
  regression failed against the previous implementation as expected.
- Fresh spoken correction continuity remains to be checked; daily review is deferred.
- Verification: `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_openai_live_memory.py -q`
  passed **14 tests**. Full gate (`.venv/bin/ruff check . --fix`,
  `.venv/bin/ruff format .`, `.venv/bin/mypy --pretty --show-error-codes`,
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`) passed:
  **1103 passed, 2 skipped in 18.75 seconds**; skips remain native opt-in tests.
  `git diff --check` passed. No live provider or hardware test was run.

### Spoken forgetting without standby — 2026-09-11

- User approved keeping the conversation after spoken forgetting, accepting that
  the detail can remain in the current conversation. This supersedes the earlier
  rule that every deletion closes Live. Settings Forget still stops the provider
  before deletion; unacknowledged removals and store read failures remain fail-closed.
- The successful household `forget` tool synchronously notifies the active Live
  handler through `ToolDependencies.on_memory_forgotten`. Only the removed ID is
  acknowledged; unrelated deletions still trigger the watcher. No await occurs
  between deletion and acknowledgement. The callback is cleared on shutdown.
- Pending capture is invalidated and further automatic capture is paused until
  the next activation. This avoids rebuilding the note from remaining transcript
  fragments; explicit tools remain available. No persistent transcript or new service.
- Tests cover the real forget tool with capture on/off, continued audio, stopped
  fragment ingestion and a subsequent unrelated deletion still closing context.
  Fresh spoken acceptance remains pending; no live API or hardware test is claimed.
- Verification: focused `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest
  tests/test_openai_live_memory.py tests/test_household_memory_tools.py
  tests/test_memory_console.py -q` passed **41 tests**. Full gate
  (`.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
  `.venv/bin/mypy --pretty --show-error-codes`, and
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`) passed:
  **1105 passed, 2 skipped in 19.28 seconds**. Native opt-in skips unchanged.
  `git diff --check` passed.

### Decision consolidation and next step — 2026-09-11

The decision table above is the current contract; earlier progress entries preserve
historical behavior and are superseded where noted. Next acceptance check: after
restarting, correct a saved fact and continue talking, then ask to forget it and
continue talking. Verify removal in Settings and no recall on a fresh activation.
No new recording batch is required. After that, implement milestone 3's bounded
daily tidy-up with simulated dates and restart/deletion races before a user trial.
This documentation update changes no runtime behavior; `git diff --check` passed.

### Spoken forget acknowledgement without deletion — 2026-09-12

- User confirmed attributed capture and modification, then reported a verbal
  forgetting confirmation while the note remained visible. Supplied logs contain
  two applied capture proposals and no forget invocation, removal or forget error.
  Background capture cannot delete entries. Settings only refreshes on Show memories
  or its own deletion, but this log provides no evidence of an actual spoken deletion.
- Shared Live/Luna policy now explicitly requires delegating saved-fact forgetting
  to the forget tool and confirming only successful removal. A failed, ambiguous
  or missing tool call must not be presented as a deletion. No schema, storage,
  capture or reset behavior changed. This prompt fix guides model behavior; it
  does not establish spoken compliance or delete the user's existing note.
- Prompt integration assertions verify the rule reaches both Live and Luna.
  Next human check: restart, ask to forget the saved fact, refresh Show memories,
  and confirm a successful forget log plus continued conversation. Daily review
  remains deferred until this path works in normal use.
- Verification: `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_prompts.py -q`
  passed 4 tests. Full gate: `.venv/bin/ruff check . --fix`, `.venv/bin/ruff format .`,
  `.venv/bin/mypy --pretty --show-error-codes`, and
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` passed:
  **1106 passed, 2 skipped in 26.94 seconds**. `git diff --check` passed.
  No live provider test was run for this prompt change.

### Spoken forgetting accepted and progress checkpoint — 2026-09-12

- User confirmed the spoken forgetting retry worked after the explicit tool-success
  instruction. This is user-observed acceptance of that trial, not general accuracy
  or hardware validation. Daily tidy-up remains the next unimplemented milestone.
- Checkpoint includes household storage, capture, review UI, attribution policy,
  correction/forget continuity, tests and living documentation. No private notebook,
  recordings or credentials are included. Architecture source is updated; rendered
  SVG regeneration remains outstanding as previously documented.
- Commit verification: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
  `.venv/bin/mypy --pretty --show-error-codes`, and
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` passed:
  **1106 passed, 2 skipped in 19.07 seconds**. The two native opt-in skips remain.
  Staged diff whitespace and credential-pattern checks passed.
