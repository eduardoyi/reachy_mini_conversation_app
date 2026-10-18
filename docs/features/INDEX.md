# Feature documentation

Feature documents explain behavior that exists in the application and how a
future agent can safely extend it. Read the relevant feature document before
modifying a major or cross-cutting feature, and update it in the same change as
the implementation.

## Index

| Feature | Document | Status |
|---------|----------|--------|
| GPT Live provider | [openai-live-provider.md](openai-live-provider.md) | conversation + standby accepted |
| Household memory | [household-memory.md](household-memory.md) | M1–M2 user accepted; M3 daily tidy-up default off; synthetic Luna review passed; live follow-up pending |
| Gemini Live provider | [gemini-live-provider.md](gemini-live-provider.md) | in-progress |
| Hybrid runtime | [hybrid-runtime.md](hybrid-runtime.md) | in-progress |
| Local ambient context | [ambient-context.md](ambient-context.md) | in-progress |
| Local audio backends | [local-audio-backends.md](local-audio-backends.md) | in-progress |
| Simulator speaker output bypass | [simulator-speaker-output.md](simulator-speaker-output.md) | in-progress |
| Local transcript wake word | [wake-word.md](wake-word.md) | in-progress |
| Deterministic local intents | [local-intents.md](local-intents.md) | in-progress |
| Speaker following | [speaker-following.md](speaker-following.md) | in-progress |
| Face recognition | [face-recognition.md](face-recognition.md) | in-progress |
| Home lights (IKEA, Tuya) | [home-lights.md](home-lights.md) | in-progress |
| AI usage and cost tracking | [usage-costs.md](usage-costs.md) | in-progress |
| TV control (Android TV) | [tv-control.md](tv-control.md) | in-progress |
| Quick commands (no cloud) | [quick-commands.md](quick-commands.md) | in-progress |

Do not describe planned architecture as shipped behavior. The living
implementation plan in [`../plans/reachy-hybrid-household-companion.md`](../plans/reachy-hybrid-household-companion.md)
is the source for work that has not landed yet.

## When a feature document is required

Create or update one when a change introduces or significantly changes:

- a user-facing flow or runtime mode;
- a provider, audio path, VAD/STT boundary, tool dispatcher, or AI pipeline;
- persistent data, configuration, or a privacy/security invariant; or
- a cross-cutting extension point another feature will depend on.

Trivial fixes, cosmetic-only changes, and pure test/tooling changes normally do
not require a new feature document, but a regression test is still required
for a bug fix.

## Required contents

Copy [`_template.md`](_template.md) and include the overview, user behavior,
architecture/data flow, canonical files and contracts, configuration, safe
extension points, constraints/gotchas, failure modes, dependencies, and an
exact test inventory. Add a dated changelog. Keep the document focused; split
an area only when one document becomes difficult to navigate.
