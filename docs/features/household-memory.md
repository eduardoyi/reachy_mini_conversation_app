# Household memory

- Status: `implemented` — M1–M2 user accepted; M3 engineering complete, default off; real-model review acceptance pending
- Last updated: 2026-09-16
- Plan: [Household memory lifecycle](../plans/household-memory-lifecycle.md)

## Overview

The first milestone replaces the flat list of facts with a small local notebook:
who a note concerns, how certain it is, and how long it belongs in active context.
It adds explicit corrections and safe deletion. Optional GPT Live capture quietly
extracts grounded notes from active conversation, independently of spoken tool
delegation. Optional daily tidy-up consolidates saved notes; older recall remains a later milestone.

`HOUSEHOLD_MEMORY_ENABLED` defaults to false. Existing version 1 stores retain
their old behavior while disabled. Version 2 data is never downgraded or replaced
with an old backup when the feature is disabled.

With household memory enabled, shared conversation instructions tell both Live
and Luna to use unclear first-person details within the current chat and quietly
skip durable storage. They must not ask for a name merely to save an ordinary
detail or narrate the skip. Identity questions remain appropriate when necessary
for the current answer or an explicit request to remember. This policy is present
even when the notebook is empty or contains only unassigned entries. It guides
model behavior; deterministic attribution validation still guards stored notes.

## Architecture and contracts

```mermaid
flowchart LR
    Tools[Remember / update / forget] --> Store[Local versioned JSON notebook]
    Store --> Filter[Subject and lifetime filtering]
    Filter --> Prompt[Bounded session memory snapshot]
    Prompt --> Provider[Selected speech provider and backend]
```

The app owns persistent state independently of provider sessions. M2 adds one
bounded, tool-free Responses worker to active GPT Live sessions. It reuses the
installed OpenAI client; no dependency or Reachy SDK change is needed.
`docs/scheme.mmd` includes this path; the architecture SVG was regenerated from
the source on 2026-09-16, including the shared memory session and shutdown barrier.

| Area | Canonical file | Responsibility |
|---|---|---|
| Store | `src/reachy_mini_conversation_app/household_memory.py` | Records, validation, lifetimes, atomic mutations and explicit migration |
| Compatibility | `src/reachy_mini_conversation_app/memory.py` | Legacy APIs and prompt formatting |
| Session instructions | `src/reachy_mini_conversation_app/prompts.py` | Profile plus memory snapshot |
| Tools | `tools/remember.py`, `tools/forget.py`, `tools/update_memory.py` | Local validated mutations; errors returned to caller |
| Registry | `tools/core_tools.py` | Hide update tool when disabled; enable with default memory tools, respecting custom selections |
| Capture | `household_memory_capture.py` | Active-session RAM buffer, evidence validation, daily reservations and revision-checked writes |
| Daily tidy-up | `household_memory_review.py` | Bounded changed-note review, attempts budget and atomic checkpoint |
| Shared session | `household_memory_session.py` | Active context checks, worker lifecycle and bounded cleanup |
| Mutation receipts | `household_memory_receipts.py` | Ordered process-local acknowledgements of exact store changes; no persistent receipt history |
| Live wiring | `openai_live.py`, `openai_live_backend.py`, `gemini_live.py` | Start after readiness; guard provider requests/tools against stale context |
| Standby boundary | `conversation_handler.py`, `hybrid_runtime.py` | Synchronous background-work fence before entering ambient mode |
| Local review | `console.py`, `static/js/views/settings.js` | Same-computer inspection and deletion |
| Configuration | `config.py`, `.env.example` | Default-off household, capture and review flags |

The [Gemini 3.8 migration](../plans/gemini-38-live-extended-thinking-migration.md)
moves provider memory lifecycle into the shared session. It prepares a memory
snapshot before prompt construction and starts optional workers after provider
readiness. Spoken updates remain usable in the current conversation; spoken forget
removes saved content and pauses capture/review for the rest of that activation.
External destructive changes or expired seeded context require a new session.
Ordered RAM receipts distinguish an activation's exact local writes from external
changes without broadly accepting whatever the latest store contains. Integration and offline regression validation passed; the existing capture/review
opt-ins and budgets remain the contract. OpenAI text capture is preserved. Gemini
captures accepted submitted text once; ambiguous spoken transcript chunks remain
excluded until live framing is verified.

The version 2 envelope contains `version: 2`, monotonic `revision`, and `entries`.
Optional `review_checkpoint` stores a local day and up to 260 entry fingerprints;
review batch/request digests and revision make replay harmless. Every existing
mutation preserves this metadata and removes deleted IDs from the watermark. Records preserve
`id`, `text` and millisecond `createdAt`, adding `subject_id`, `source_person_id`,
`source_ref`, `kind`, `certainty`, UTC `learned_at`/`updated_at`, grounded
`event_start`/`event_end`, `active_until`, `delete_after` and `status`.
IDs survive corrections. Kind is note/situation/plan/fact; certainty is
stated/tentative; status is current/resolved/superseded. Tentative claims use a
bounded kind. Date-only values preserve their precision; date-times require a
timezone. The store does not parse phrases such as “next Christmas”.

`remember` takes `fact`, an explicit `subject_id`, and optional attribution,
kind, certainty, dates and evidence reference. `update_memory` takes `memory_id`
and only the fields being corrected; explicit null clears nullable fields.
`forget` accepts an exact memory ID or a text query. Multiple query matches return
candidates without deleting anything. Save/update results retain `memory_id`.
The legacy off-mode API keeps its previous query behavior. Custom Tool access
selections are authoritative: add `update_memory` explicitly there if an override
is saved; the automatic addition applies only to profile defaults.

Limits are 280 characters per entry, 60 lasting facts and 200 other entries.
M1 prunes retention-expired entries, then reports capacity exhaustion rather than
silently evicting a retained note. This conservative policy can be relaxed only
after real capacity pressure is observed.
Exact normalized duplicates do not refresh evidence dates. A reused evidence
reference must describe the same entry; conflicting reuse is rejected. Use a
per-note reference, not one reference for every note in a session.

### Local inspection and migration

Stop Reachy before file maintenance. Migration is explicit, in place and one-way;
the default-off feature does not automatically touch an existing file. From the
repository's virtual environment, use the public API with the same instance
directory as the app:

```python
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore

store = HouseholdMemoryStore()  # Or pass the app's explicit instance directory.
entries = store.migrate_v1()
for entry in entries:
    print(entry.id, entry.subject_id, entry.text)
```

This prints private memory locally; do not paste its output into logs or commits.
Migration preserves legacy IDs/text/times and leaves ownership unknown for review.
Settings labels these notes “Unassigned — not used in conversation”; displaying a
retained note does not mean it is included in the next conversation's prompt.
Assign only verified ownership with `store.update(memory_id, subject_id="...")`.
Then launch with `HOUSEHOLD_MEMORY_ENABLED=1`. No migration has been run against
the household's actual file during implementation.

After migration, `store.list_entries(include_inactive=True)` inspects retained
history, `store.forget(memory_id="...")` deletes one entry, and `store.clear()`
clears all entries. `list_entries()` returns currently active entries. Unknown
entries may appear in local inspection but never in the automatic prompt snapshot.

### Lifetimes

| Kind | In active context | Retained |
|---|---|---|
| Note | 48 hours | 7 days from evidence |
| Situation | 7 days after user update | 30 days after active deadline |
| Dated plan | Explicit end plus 7 days | 30 days after active deadline |
| Fact | Until corrected or forgotten | Until corrected or forgotten |

A plan without a grounded end uses situation lifetimes. Becoming old does not
mean the luggage arrived or the trip happened. A resolution removes a note from
active context immediately. Reads do not count as new evidence.

Retention-expired entries are hidden on reads and removed on a subsequent store
write. With review enabled, the next eligible active-session review prunes expired
entries atomically. An unused file can still contain expired entries until maintenance
or deletion. Explicit forget removes the selected entry
from disk immediately.

## Invisible capture (M2)

Enable both `HOUSEHOLD_MEMORY_ENABLED=1` and
`HOUSEHOLD_MEMORY_CAPTURE_ENABLED=1` after explicit migration. Capture is currently
GPT Live only. Other providers retain explicit memory tools. Flags are off by
default and were not changed in the user's `.env` during implementation.

After `session.started`, Live user/assistant transcript deltas enter a bounded
RAM buffer. Assistant speech provides context only; saved claims require exact
user-fragment evidence and grounded ownership. Ambient pre-wake context, audio,
camera frames and injected text commands do not enter this worker. A short settle
period lets fragments arrive; this does not make them authoritative final turns.
Unclear ownership is skipped, without questioning someone just to fill a field.
Request eligibility requires user words, not terminal punctuation. The extractor
judges whether a claim is semantically complete; an unpunctuated claim is not
automatically unfinished. This judgment remains model-based. New user fragments
still invalidate an in-flight result, and shutdown still cancels without a flush.
Raw delta spacing is preserved. Exact excerpt evidence may span nearby user
pieces within one sentence, including a split name. Attribution checks reconstruct
the full original fragments rather than concatenating excerpts that could omit a
qualifier. Overlaps up to three seconds are allowed when fragment starts remain
ordered. Assistant fragments, sentence boundaries, backward starts, missing evidence
text or a gap/overlap above three seconds prevent joining identity evidence. This is a conservative grouping
heuristic, not speaker recognition or an authoritative turn boundary. A separate
later first-person statement still cannot borrow an earlier introduction.

A tool-free Luna Responses call proposes at most six notes or clearly grounded
corrections/resolutions by an existing ID. Automatic edits must concern the same
named person and an entry included in that request; ambiguous conflicts are
skipped. Automatic corrections preserve existing event dates; date changes,
reassignment and deletion use explicit tools. Daily promotion remains deferred. Local validation
checks their shape, dates, evidence and attribution. `snapshot()` supplies the
store revision; `apply_capture()` validates the whole batch and writes once only
if that revision still matches. Corrections, deletion and clear invalidate older
work. Closing a session clears the buffer and cancels processing without a final
flush. Capture waits for a two-second pause in new user text and spaces requests
by at least ten seconds. A bounded active-session text window survives successful,
empty and cancelled attempts so later continuations do not lose their subject.
Only new user text can trigger another attempt; assistant text alone does not.
External edits require stopping the app; deletion still invalidates that window. A session ending
before an attempt finishes can still lose its unsaved note. No transcript archive
or end-of-day job is created.

### Limits and cost

| Limit | Value |
|---|---|
| RAM transcript text | 24,000 characters |
| Request input JSON | 12,000 characters, including bounded existing notes |
| Output | 1,000 tokens; at most six proposals |
| Request interval | At least 10 seconds within an activation, after a user-text pause |
| Timeout / retries | 20 seconds / no automatic retries |
| Daily allowance | 60 attempts and 60,000 reserved output tokens |

`memory.capture-budget.v1.json` stores date and counters only. Reservations are
written before requests, including failed/cancelled attempts, and survive
restarts. Corrupt budget metadata stops capture. This is a per-installation call
and token guard, not an account-wide dollar spending limit.

An exhausted allowance is expected after 60 reservations, even if no notes were
saved. Empty responses, failures and cancellations also consume reservations.
Capture resumes on the next local day; restarting does not replenish it. The
Terminal diagnostic includes reserved counts and limits, without private text.
Settings shows saved notes, not background review diagnostics. Review runs at
session readiness against notes already stored, so new notes from that conversation
are not evidence that the once-daily review has processed them.

The worker uses `OPENAI_LIVE_BACKEND_MODEL` (Luna by default), low reasoning,
`tools=[]` and `store=False`. The official
[Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
checked 2026-09-11, lists $0.20/M input tokens, $0.02/M cached input and $1.20/M
output. Input characters are not tokens; actual cost depends on language and
usage. Prices may change. These small capture calls are additional to speech and
tool delegation. `store=False` is not a promise of zero provider retention.

### Daily tidy-up (M3)

`HOUSEHOLD_MEMORY_REVIEW_ENABLED` is a separate default-off opt-in, requiring
household memory. During the next active GPT Live conversation, a small review
can examine changed saved notes and a bounded related set without delaying speech.
It never reads ambient audio or a day-long transcript. An offline app catches up
once instead of replaying every missed day.

The initial operations keep existing wording: retain useful notes, merge duplicates
for the same subject, and promote suitable notes to lasting facts. Promotion does
not increase certainty. Resolution and substantive corrections continue to use the
existing evidence-driven capture/tool path; aging alone never resolves a situation.
Situation classification preserves the original note deadlines; lasting fact promotion
removes expiry only for stated, known-owner notes. Expiry cleanup follows existing retention dates. Review is not fresh evidence and
must not reset evidence dates or repeatedly extend a temporary note's life.

One attempt per activation, at most two reserved attempts per local day, at most
one successful daily review. Each request is capped at 20,000 input characters,
2,000 output tokens and 30 seconds. With capture's independent 60,000-token cap,
combined background output reservations are at most 64,000 tokens per local day.
This does not include normal speech/delegation and is not a dollar budget.

Standby, forgetting or shutdown fences pending work. A revision change while the
review runs prevents stale writes. The successful review and its checkpoint are
committed together; unselected notes remain eligible for later reviews. Settings
Forget retains its stronger session reset. Spoken forgetting also cancels review
for that activation so it cannot restore a removed note.

### Review and forgetting

Settings → **What Reachy remembers** → **Show memories** loads saved entries only
on demand. Each entry has a **Forget** button. GET `/api/memory` and POST
`/api/memory/forget` are limited to loopback clients and hosts, use `no-store`, and
POST requires a matching Origin plus a small JSON body containing `memory_id`.
The response omits provenance references. No private notes go into browser storage.

The console stops the current conversation before deleting. Spoken `forget`
removes the saved note without ending GPT Live. It synchronously acknowledges
only that removed ID and invalidates pending capture, then pauses automatic
capture until the next activation to avoid saving the detail again. The existing
conversation may still contain the detail; spoken forgetting does not erase it
from cloud context. Unacknowledged deletions and unreadable stores still close
Live/Luna context. Settings Forget remains the option that clears the session. Corrections preserve the current
conversation: the spoken correction is already in context, and the updated saved
snapshot is loaded on the next activation. External edits require stopping the app. Routine additions appear in the next activation's prompt;
there is no mid-sentence prompt refresh. Existing conversation context already
contains the statement that produced the note.

## Privacy and current limits

Only ordinary household-safe facts belong here. This is not a private vault or
speaker recognition system. Unknown ownership must not become a fact about the
current speaker. Dates and attribution must come from explicit evidence; storage
validation cannot prove that a model understood speech correctly. The extractor
is instructed to skip sensitive content; local validation checks evidence and
structure, not a comprehensive sensitivity classifier. This is a shared household
notebook, with model-based selection, rather than a private vault.

New notes require an explicit subject label (`household` for genuinely shared
information). Without one, skip saving quietly; do not turn the conversation into
an identity questionnaire. Migrated entries can remain unassigned for local review
and are omitted from prompts. M1 accepts caller-supplied stable labels and does
not infer identities or merge similar names. Uncertain fragments remain only in M2's bounded RAM capture path.

The local store performs no network operations and saves no audio or transcript
archive. Relevant notes become cloud prompt context when a conversation starts,
as existing memory already did. The 3,000-character prompt cap limits that snapshot. Lasting facts receive a
protected share before recent notes fill the remainder; complete entries are
used, never cut-off fragments.

Stop the app before external migration/correction/deletion. GPT Live's M2
invalidation covers its active provider path; other speech providers still require
a new conversation after tool changes. Nothing here promises deletion of content
already sent to a cloud provider. Model grounding checks reduce mistakes but do
not prove semantic accuracy or establish who was speaking.

One app process owns the file. Stop it before external maintenance; atomic file
replacement protects against partial writes, not concurrent independent apps.
Do not use an older app release against a migrated store. There is no automatic
backup or downgrade that could resurrect forgotten facts.

## Extension guide

Keep all future writers on the same validated store API. Preserve separate subject
and source identities, attention and retention deadlines, bounded capacity and
unknown attribution. Add regression tests before changing these rules. Preserve revision checks and cancellation so corrections and deletion always win
over stale work. The plan owns the progressive acceptance sequence.

## Validation

| Test file | Evidence |
|---|---|
| `tests/test_household_memory.py` | Attribution, lifetimes, ambiguity, duplicate evidence, explicit migration including odd timestamps, atomic-write failure, corruption, restrictive permissions, expired deletion, clock rollback and resolved-fact restart |
| `tests/test_household_memory_tools.py` | Enabled/off contracts, malformed inputs, unknown ownership, correction, store failures, no private tool logs and real dispatcher manager injection |
| `tests/test_household_memory_integration.py` | v1/v2 compatibility, off-mode preservation, explicit migration, unknown/expired filtering, complete-entry cap, learned/event date distinction and session instructions |
| `tests/test_household_memory_registry.py` | Flag changes, default/custom Tool access, malformed argument logging privacy |
| `tests/test_household_memory_capture.py` | Bounded tool-free requests, incomplete/assistant evidence, stale writes, persistent budget, timeout and resistant cancellation |
| `tests/test_openai_live_memory.py` | Active-only capture wiring and stale Live/Luna context invalidation |
| `tests/test_household_memory_review_store.py` | Atomic review/checkpoint, replay, retention, ownership, deadlines and rollback |
| `tests/test_household_memory_review.py` | Active-only requests, bounded input/output, daily reservations, restart, invalid output and cancellation |
| `tests/test_openai_live_review.py` | Readiness/standby/forget boundaries, maintenance continuity and deletion fencing |
| `tests/test_memory_console.py` | Local-only review, Origin/body limits, owner-loop deletion, shutdown failure and late cancellation |
| `tests/test_memory.py` | Existing legacy memory behavior and tool/prompt integration |
| `tests/test_config.py` | Default-off flag and explicit environment parsing; existing config behavior |

```bash
REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_household_memory.py tests/test_household_memory_tools.py tests/test_household_memory_integration.py tests/test_household_memory_registry.py tests/test_memory.py tests/test_config.py -q
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/mypy --pretty --show-error-codes
REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q
uv lock --check
git diff --check
```

M1 validation on 2026-09-11: focused command **121 passed**; full suite **1013 passed,
2 skipped**. Ruff passed (126 files formatted), strict mypy passed (66 source
files), `uv lock --check` passed (156 packages), and `git diff --check` passed.
The skips are the existing opt-in native acoustic simulator and native speaker
smoke tests. These results validate local/fake-provider behavior, not microphone
or real-provider memory quality.

### Synthetic review — 2026-09-11

Executed against a temporary store with an injected clock, then removed it.
These are fictional examples, not saved household facts:

| Example | Observed result |
|---|---|
| Clara's birthday | Still active after eight days |
| Clara reports missing luggage | Inactive after eight days, retained as a report; no assumption of resolution |
| Maybe a Christmas trip, ending January 2 | Still active and tentative; dates preserved |
| Trip cancelled | Immediately inactive, history capped at 30 days from cancellation |
| Unknown speaker says “I like jazz” | No durable write |
| Forget the birthday | Text removed from disk |

The local exercise passed. The user accepted this checkpoint and authorized
milestone 2 on 2026-09-11. No actual household store has been migrated and no simulator,
speech-extraction quality, hardware or paid-provider memory acceptance is claimed.
M1 added no new cloud path. M2 adds opt-in active-session capture; its separate
validation and human acceptance must be recorded before enabling it.

## Changelog

- 2026-09-11: M1 local storage, tools and compatibility implemented behind the
  default-off flag; synthetic exercise passed. Runtime activation remains pending.


### M2 validation — 2026-09-11

- Store-focused tests: **34 passed**.
- Local console tests: **10 passed**, including cross-thread owner-loop dispatch
  and a cancellation-resistant shutdown that cannot delete after timeout.
- Synthetic browser exercise: Show memories displayed the fictional Clara note;
  Forget removed it and displayed the restart message. Temporary server stopped.
- Two bounded real Luna requests used temporary fictional data only. English
  named-fact capture passed in **5.66 seconds**. Spanish named-fact plus unknown
  first-person statement passed in **4.75 seconds**: only the named fact was saved,
  without assigning the unnamed statement to an existing person. Each used one
  request, with no failed requests or dropped fragments. Temporary stores removed.
- A third real probe failed the stable-ID correction assertion: two mutations
  left one active entry with a different ID. The prompt was clarified to update
  changed values in place. The fourth real request passed in **6.83 seconds**:
  one mutation, the original ID, and one retained note with the corrected preference.
  Four paid requests total, all fictional data; no household transcripts uploaded.
- These probes establish API compatibility for these examples, not general accuracy.
- Final primary verification: **184 focused tests passed** using
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_household_memory.py tests/test_household_memory_tools.py tests/test_household_memory_integration.py tests/test_household_memory_registry.py tests/test_household_memory_capture.py tests/test_openai_live_memory.py tests/test_memory_console.py tests/test_memory.py tests/test_config.py -q`.
- Full gate: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`
  (130 files), `.venv/bin/mypy --pretty --show-error-codes` (67 source files), and
  `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` passed:
  **1076 passed, 2 skipped in 24.02 seconds**. The skips remain the opt-in native
  acoustic simulator and native speaker smoke tests.
- `uv lock --check` (156 packages), JavaScript module syntax checking, local
  documentation links, configured-secret diff scanning and `git diff --check`
  passed. The architecture source is updated; rendered SVG regeneration remains
  outstanding alongside the provider diagram update.
- Actual household store migration, human simulator capture quality and hardware
  acceptance are not claimed. Runtime flags remain off by default.


### Human simulator checkpoint (partially passed)

On 2026-09-11 the user confirmed a note was saved, visible in Settings, and
correctly recalled after standby and a new wake. The log shows one validated/applied
note at 20:29:10, standby at 20:29:36 and activation at 20:29:43. This establishes
the main spoken capture/recall path for one trial. A subsequent user trial confirmed
that corrections were recalled after a new wake and Settings deletion prevented
recall. The unknown-person trial prompted an unnecessary identity question; the revised quiet-skip policy awaits a spoken recheck. Historical failures follow.
Corrections deliberately reset the conversation. In the observed run the stale
backend context guard surfaced as a generic `OpenAI delegation failed` RuntimeError
warning before standby. This misleading diagnostic and abrupt reset are a known
UX rough edge; they did not prevent the observed correction or subsequent recall.

The first trial on 2026-09-11 failed capture/recall: a Spanish self-introduction
and preference were understood during the conversation but not saved for the next
activation. The visible unassigned name was the migrated legacy note. A local
synthetic reproduction demonstrated rejection when a self-introduction and its
first-person preference arrived in separate deltas; a single-delta version passed.
The original log cannot establish the exact fragment split or rejection path.
Shutdown now logs only capture counters (fragments, requests, applied proposals,
incomplete/stale batches and budget blocks), never transcript or memory text.
After the second trial, `incomplete_batches` counts non-completed extractor
responses; it no longer counts transcript pieces lacking terminal punctuation.
The fresh-session prompt regression separately confirms assigned notes are loaded
and unassigned legacy notes are excluded. Human acceptance remains pending.

After the split-evidence repair, primary verification passed:
`REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_household_memory_capture.py tests/test_household_memory_integration.py tests/test_openai_live_memory.py -q`
reported **54 passed**. The full Ruff fix/format, strict mypy and pytest gate
reported **1086 passed, 2 skipped in 20.76 seconds**, with 130 formatted files and
67 typed source files. JavaScript module syntax and `git diff --check` passed.
These checks used fake providers and fictional notes; no new paid calls or edits
to the user's memory store were made during the repair. Restart the app with the
same flags to retry; migration does not need repeating. Ask for the named person's
preference after standby to isolate recall from unimplemented speaker recognition.

The second trial received 31 fragments but attempted zero requests: the old
terminal-punctuation gate rejected 10 batches. After removing that gate, two real
Luna requests with temporary fictional notes passed: a split Spanish introduction
and complete unpunctuated preference saved one correct note; an unfinished clause
saved none. Temporary stores were removed; no household content was uploaded.
Primary verification: **51 passed** with
`REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/test_household_memory_capture.py tests/test_openai_live_memory.py -q`;
the full Ruff/format/mypy/pytest gate passed **1090 tests, 2 skipped in 19.64 seconds**.
`git diff --check` passed. These examples are not broad extraction-quality or human
acceptance evidence; the next spoken retry remains pending.

The third trial completed one request but applied no note. Local API inspection
confirmed this was not a stale Settings display. The original result was not
retained, so no-proposal versus validation rejection remains unknown. Two further
defects were reproduced: a cancelled prefix discarded needed context, and explicit
`status: current` caused a valid new note to be rejected. Both are repaired; request
spacing is now ten seconds and the bounded active text window is retained until
invalidation/standby. Per-attempt logs expose only outcome counts, never content.
A real streamed fictional Luna probe (introduction, three-second pause, then four
unpunctuated continuation pieces) passed: two requests, one expected cancellation,
and one correct preference saved. Temporary notes were removed. This used no
household audio/text and is not human acceptance.
Primary full gate passed **1095 tests, 2 skipped in 19.47 seconds**, plus Ruff,
formatting and strict mypy. The primary capture/provider focused command above
passed **56 tests**; capture-only agent tests passed **44 tests**;
`git diff --check` passed. Human simulator retry remains pending.

The fourth trial reached extraction but rejected its single proposal. Two local
false-rejection cases are now repaired: bounded timestamp overlap and legitimate
excerpt quotes across contiguous fragments. Grounding uses the original text,
including omitted qualifiers. Rejection logs now distinguish fields, evidence,
continuity and attribution using counts only. The exact original rejection cannot
be reconstructed from the old logs. A real fictional Spanish probe with overlapping,
unpunctuated deltas passed: one proposed, one validated, one applied, zero rejection
counts. The temporary store was removed; no household content was uploaded.
Primary full gate: **1097 passed, 2 skipped in 19.37 seconds**, Ruff/format/mypy
passed. The capture/provider focused command passed **58 tests**. This remains
synthetic provider verification, with human simulator acceptance pending.

Use the normal GPT Live simulator launch after explicit migration, adding both
household-memory flags. This trial uses normal speech; no recording batch is needed.

1. Mention one true fact about a named person naturally. After a short pause,
   inspect Settings → What Reachy remembers. Do not explicitly ask to remember it.
2. Let the conversation return to standby, wake again, and ask about that fact.
3. Give a clear correction with the person's name. Continue talking without an
   immediate reset, then verify the correction is recalled after the next wake.
4. Forget the note in Settings, wake again, and verify it is no longer recalled.
5. Check that an unnamed first-person preference is not silently assigned to the
   owner, and that ordinary speech/standby behavior still feels natural.

Record capture misses and needless interruptions before changing cadence or
attribution rules. The daily consolidation milestone remains gated on this check.

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

### M3 implementation and review — 2026-09-13

- Added a separate default-off daily review flag; existing user settings and the
  private notebook were not modified. Runs after Live readiness, at most once per
  activation, without delaying speech or continuing work in ambient mode.
- Incremental eligibility uses per-entry fingerprints rather than a single time
  cursor, so bounded input does not permanently skip unselected notes. Checkpoints
  and mutations share the same atomic JSON write. Attempts persist separately in
  `memory.review-budget.v1.json`, including failed/cancelled attempts.
- No arbitrary text rewriting or inferred resolutions. Keep/merge/promote operate
  on existing entries; correctness of semantic duplicate/durability judgments still
  depends on the model. Fake-provider tests do not prove real model quality.
- Maintenance acknowledgement is scoped to its exact atomic revision and removed
  IDs. Fresh capture fragments are retained across that known revision, while old
  extraction responses are cancelled. Explicit forgetting still fences all review
  work; spoken forgetting pauses capture until the next activation.
- Three Luna max agents supplied store, worker and provider work. Primary review
  removed unnecessary compatibility layers and caught removal-ID, replay, budget,
  callback and capture-overlap issues before acceptance. When agent limits were
  hit, primary continued implementation/tests; agents resumed after the user reset
  limits for final adversarial checks.
- The existing unrelated audio diagnostics changes were preserved. No new package
  dependency, private-data upload or real cloud review test was used. The Mermaid
  source includes the review path; rendered SVG regeneration remains outstanding.

### Final M3 validation — 2026-09-13

- Primary reviewed every delegated change and requested fixes for concrete defects;
  resumed Luna max reviewers completed store, worker and integration checks.
- `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .` (139 files), and
  `.venv/bin/mypy --pretty --show-error-codes` (69 source files) passed.
- Focused capture/store/review/provider scenario suite passed during integration
  (94 tests at that checkpoint); final agent checks also covered added concurrency,
  direct cancellation, merge compatibility and configuration restoration cases.
- Final `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q`:
  **1169 passed, 3 skipped in 29.79 seconds**. Skips are native opt-in checks,
  including the SDK flush smoke added by concurrent audio work.
- A preceding full run failed the existing local VAD/deadline test
  `test_processor_backed_split_followup_after_deadline_is_not_local_command`
  with a VAD timeout. Its isolated rerun passed (1 test in 1.54 seconds), then the
  complete suite passed. No timing threshold was weakened to obtain a pass.
- `git diff --check` passed. No dependency change, real cloud review, hardware
  acceptance or rendered architecture SVG update is claimed. M3 engineering is
  complete; real-model/user acceptance is pending. M4 older recall is not started.


### First live M3 troubleshooting — 2026-09-13

The simulator run reached review transport, but logged one rejected proposal as
malformed output. The response was not retained, so its exact contents cannot be
recovered. Inspection identified a reproducible contract mismatch: unassigned
legacy notes could enter requests even though validation rejects operations on
unknown owners. Such notes must stay local and unchanged, outside model review.

Capture was independently blocked by the persisted allowance: 60 calls and 60,000
reserved output tokens for the local day. This is not an API account balance
error. Reservations include unsuccessful and empty requests; historical counters
do not show which earlier attempts saved notes. No allowance was reset or raised.
Diagnostics now include counts, limits and next-local-day resumption. A regression
checks blocked transport and next-day recovery in an isolated store.

A real `gpt-5.6-luna` review against one made-up named note in a temporary store
succeeded: one attempt, one applied promotion from note to fact, zero failures,
and a saved checkpoint. No household text was sent by this diagnostic and no
user store or budget was changed. This is a narrow provider smoke check, not
household quality acceptance. Settings lists stored memories; maintenance logs
are in Terminal. Live user follow-up remains pending.

The final worker filters unassigned candidates before building the request. A
legacy-only temporary store now checkpoints with zero provider attempts and
`skipped_unassigned=1`; the note remains unchanged. A second real Luna smoke
check after the fix again applied one named-note promotion. Malformed response
shape and rejected proposals now have separate count-only diagnostics.

Validation after the fix: `.venv/bin/ruff check .`, `.venv/bin/ruff format
--check .` (139 files), `.venv/bin/mypy --pretty --show-error-codes` (69 source
files), and `REACHY_MINI_SKIP_DOTENV=1 .venv/bin/pytest tests/ -q` passed:
**1184 passed, 3 skipped in 23.05 seconds**. Skips remain native opt-in tests.
The focused capture/review/Live suite passed during review (76 tests before the
last three regression cases). An intermediate run caught an inverted parser
result flag in the diagnostic edit; that defect was corrected before this final
gate. No user simulator retest or hardware acceptance is claimed.

## Shared migration validation — 2026-09-16

The final full repository gate passed: **1,247 passed, 3 native opt-in checks
skipped**, with two expected synthetic SDK warnings. Ruff, formatting, strict
mypy, lock validation, JavaScript syntax and diff checks passed. The
[final validation record](../plans/gemini-38-live-extended-thinking-migration.md#final-offline-validation--2026-09-16)
lists exact commands, test inventory, skipped checks and the reproducible next
live-validation step. Gemini spoken-memory parity and real audio/hardware
acceptance remain unverified; the active provider was not switched.
