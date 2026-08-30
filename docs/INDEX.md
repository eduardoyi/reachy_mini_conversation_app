# Project documentation

This directory contains the Reachy Mini Conversation App's durable engineering
documentation. It is intentionally maintained in this application repository:
the upstream app's preference against Markdown files under `docs/` is
overridden for this household-companion fork.

## Sources of truth

| Document | Purpose |
|----------|---------|
| [`README.md`](../README.md) | Installation, CLI options, configuration, and user-facing app behavior. |
| [`AGENTS.md`](../AGENTS.md) | Coding, testing, documentation, and review rules for this repository. |
| [`scheme.mmd`](scheme.mmd) | Mermaid source for the current architecture diagram. The rendered SVG is generated from it. |
| [`TESTING.md`](TESTING.md) | Canonical test strategy and the minimum evidence for a feature. |
| [`plans/INDEX.md`](plans/INDEX.md) | Index of every design, implementation, benchmark, migration, and rollout plan. |
| [`features/INDEX.md`](features/INDEX.md) | Index of durable implementation and extension documentation. |
| [`HYBRID_BENCHMARK.md`](../HYBRID_BENCHMARK.md) | Living Phase 0–5 benchmark report; unmeasured live fields remain explicit. |

The root README remains the single installation and CLI reference. The files
under `docs/` explain how the system is designed, implemented, tested, and
extended; they do not replace the README or the Reachy Mini SDK documentation.

## Read order for agents

1. Read the root [`AGENTS.md`](../AGENTS.md) and any applicable nested
   instructions.
2. Read the relevant plan in [`plans/`](plans/), including its current status,
   decisions, and execution log.
3. Read the relevant feature document in [`features/`](features/) and
   [`TESTING.md`](TESTING.md).
4. Read the README and current source before changing a documented behavior.
5. Update the plan, implementation documentation, indexes, and README in the
   same change when the change affects them.

## Documentation lifecycle

- All plans belong in `docs/plans/` and use descriptive `kebab-case.md` names.
- Plans are living records: update status, checklists, decisions, evidence,
  deviations, risks, and next steps as work progresses.
- Major or cross-cutting features get a document under `docs/features/` in the
  same change that implements them.
- Every feature document includes its safe extension points, constraints,
  failure modes, and exact test inventory.
- Keep current architecture separate from proposed architecture. Mark planned
  components as planned until code and tests establish them.
- Do not create a second README. Use these indexes for navigation.

## Relevant external references

- [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini)
- [Official Conversation App](https://github.com/pollen-robotics/reachy_mini_conversation_app)
- [Gemini Live and Reachy Mini tutorial](https://dev.to/googleai/build-a-talking-robot-with-gemini-live-and-reachy-mini-20e2)
- [Hugging Face speech-to-speech](https://github.com/huggingface/speech-to-speech)
