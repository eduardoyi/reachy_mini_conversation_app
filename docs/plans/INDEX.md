# Plans

Every design, implementation, migration, benchmark, rollout, and operational
plan for this project lives in this directory. A plan is not complete merely
because code has landed: its status and evidence must be updated until the
acceptance criteria are met.

## Plan index

| Plan | Status | Last updated | Scope |
|------|--------|--------------|-------|
| [Reachy hybrid household companion](reachy-hybrid-household-companion.md) | `in-progress` | 2026-08-29 | Phases 0–5 |

The required living benchmark report is
[`HYBRID_BENCHMARK.md`](../../HYBRID_BENCHMARK.md). It remains `in-progress`
until the Phase 1 rerun and Phase 3–5 live measurements are recorded.

Phase 2 is accepted: automated privacy/lifecycle coverage and the manual
simulator/LOCAL/Gemini soak, activation, conversation, interruption, and
inactivity-close checks passed. Phase 1 remains in progress only for retained
direct-run evidence and the missing first-response-audio measurement. Generated
architecture-diagram regeneration also remains pending.

Phases 3–5 are implemented and have focused automated coverage, including both
context strategies and the no-prewake-cloud boundary. Their live local-STT,
wake-quality, simulator-command, latency, and CPU/RAM acceptance runs remain
pending, so they are not marked complete.

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
