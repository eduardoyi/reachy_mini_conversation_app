# AGENTS.md

Instructions for AI coding agents (and the humans driving them) working in this repository.
**Read this before writing code. It is not optional, and it does not replace [`CONTRIBUTING.md`](CONTRIBUTING.md). It enforces it.**

This repository is an **application built on the [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini) (`reachy_mini`)**, not a standalone system. Changes must stay compatible with it (see *Engineering excellence*).

This is a small project maintained by a small team. Low-quality, auto-generated, unreviewed changes cost us real time. Follow the rules below or stop.

---

## Read this first

1. **Read before you write.** Match the existing structure, naming, and patterns of the module you touch.
2. **Don't touch** `.github/pull_request_template.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, or CI workflows, unless that *is* the task.
3. **Minimal diff. One PR = one fix/feature.** Prefer deleting code to adding it.
4. **No dead code, no LLM verbosity** in code, comments, or PR text.
5. **Errors are logged, never swallowed.** Use `logger`, not `print`.
6. **Run the full gate before review.** Don't waste a reviewer's time, and never open a PR with a red pipeline.
7. **Read the active plan and relevant docs before work.** For non-trivial work, read the current plan in `docs/plans/`, the relevant feature documentation in `docs/features/`, the testing guidance, and the README/architecture material for the area you will change.

Everything below expands these.

---

## Reachy product philosophy

- **Aim for simple magic.** Choose the simplest elegant solution that delivers most of the companion experience: attentive, natural, useful and easy to live with. “90% of perfect” is a scope goal, never a reason to weaken privacy, correctness, reliability or required tests.
- **Validate progressively.** Break features into small working milestones with observable user outcomes. Validate the current milestone before adding the next layer; do not build speculative infrastructure for hypothetical future needs.
- **Keep complexity out of conversation.** Reachy should adapt to people, not require people to manage its internals or speak in special formats. Ask clarifying questions when they help the interaction, not merely to fill a schema. Quiet uncertainty or restraint can be better than a confident guess or interruption.
- **Reuse when it actually simplifies.** Consider maintained open-source components, but compare integration, dependencies, latency, cost, privacy and ongoing maintenance with a small local implementation. Installing a framework is not automatically simpler. Add abstraction or infrastructure only for a concrete validated need.
- **Measure the experience.** Prefer meaningful scenario tests and real user feedback over feature count. Record what remains uncertain, what is deferred, and the evidence that would justify more complexity. Preserve natural behavior and graceful failure as the system grows.

---

## Engineering excellence

The bar we hold, and the *why* behind the rules:

- **Built on the Reachy Mini SDK.** This app runs on top of [`reachy_mini`](https://github.com/pollen-robotics/reachy_mini) (declared in `pyproject.toml`, locked in `uv.lock`, installed alongside it). Build on the SDK's public API. Don't fork, vendor, or monkey-patch its internals, and keep the app working when the SDK version bumps.
- **Design before code.** Think non-trivial changes through and state the trade-offs. The realtime conversation loop lives in `huggingface_realtime.py`; keep shared behaviour there.
- **Graceful degradation.** A tool returns `{"error": ...}` rather than crashing the conversation loop.
- **Tests cover essential behavior**, not every line.
- **The CI gate is the floor, not the ceiling.** Never degrade quality to land faster.
- **Reuse over duplication.** No copy-paste, no single-use helpers, no secrets in code.
- **Build for the merged project, not the PR.** A PR is temporary. Once it lands, its context is gone, so write features and comments that read as a natural part of the whole codebase, never as a diff.

---

## Code quality

- **Names carry meaning.** No `data`, `tmp`, `res`, `helper`, `do_stuff`. A reader should know what a name holds without chasing it. Match the module's vocabulary.
- **Comments explain *why*, never *what*.** One line, only where the code can't speak for itself. Delete comments that restate code (`# recurse into subclasses`) or narrate your change. Keep just the nugget when there is one: `_max_age_s = 3600  # 1 hour`.
- **Docstrings on public APIs only, one line.** A long docstring drifts out of sync and becomes a burden. It must describe what the code *actually does*, with no stale copy-paste and no string-literal "field docstrings" under pydantic or dataclass fields.
- **No single-use helpers or thin wrappers.** Inline unless extraction removes real, repeated duplication. A function that just returns a constant or forwards one call isn't worth its name.
- **Typed public signatures.** In new code, avoid `Any` and `cast`. Model the real type instead. No stubs or speculative parameters.

## Errors and logging

These are the cleanups we make in review over and over. Write code that wouldn't trigger them.

- **Never swallow errors.** No `except Exception: pass`. Catch the narrowest exception that fits and log it (`logger.warning("Failed to sync profile: %s", e)`), or let it propagate.
- **Log, don't `print`.** Use the module `logger` with lazy `%`-style args (`logger.info("loaded %s", name)`), not f-strings, in new code. Reserve `print` for genuine CLI output.
- **Imports at module top**, never inside a function. No alias gymnastics (`import os as _os`).
- **Declare attributes, don't probe for them.** Give an attribute a class-level default (`x: Callable[[], None] | None = None`) and check it directly, instead of papering over it with `hasattr` or `callable`.
- **No leftover suppressions.** Drop a `# noqa` or `# type: ignore` the moment the underlying issue is gone.

## Tests

- **Test behavior, not private helpers.** The `tests/` tree mirrors `src/`.
- **Cover the essential features, not every thin thing.** A good test fails when behavior breaks, not when you rename a variable.
- A bug fix needs a regression test. A feature needs at least a happy-path test.
- Deliberately skipping a test (e.g. pure hardening)? Say so and let the human decide.

### Test as you build

- Tests and documentation ship in the same change as the feature. Do not defer them to a later cleanup.
- Non-trivial features require comprehensive applicable coverage: unit tests for pure logic and state transitions; integration tests at audio/provider/tool boundaries using fake providers; simulator tests for Reachy lifecycle and movement integration; privacy tests for connection and byte-transfer guarantees; and error, timeout, cancellation, interruption, and reconnection paths.
- Keep tests deterministic and hardware-independent where possible. Use fakes and fixtures for cloud services; do not make the test suite depend on a live Gemini session, real credentials, or physical hardware.
- A regression test is required for every bug fix and should fail against the old behavior before the fix is accepted.
- Record the relevant test files and validation commands in the feature documentation and plan status.

## Pull requests

- **Respect the reviewer's time.** Run the gate, self-review your own diff, keep the change small and focused. Reply to comments in your own words and address the point. No AI walls of text, no unrelated churn that forces a re-review.
- **Branch:** `<type>/<short-description>`, adding the issue number when there is one (`<type>/<issue-number>-<short-description>`). Types: `feat` `fix` `docs` `test` `refactor` `chore`.
- **Issue first for a feature or any non-trivial change**, so we agree on the approach before the code exists and you don't build something we can't merge. A small, obvious fix (typo, one-line bug) can go straight to a PR.
- **Fill in the PR template, never overwrite it.** `.github/pull_request_template.md` exists for humans to read and to manually check their own work. Tick the boxes that apply and complete the sections. Do not rewrite, restructure, or delete any of it.
- **PR title:** explain the work. Do not put agent or model names in it (no `codex`, `claude`, and so on).
- **PR description:** concise and concrete. State what changed and why.
- **Update `.env.example` for new config vars**. Never commit secrets or `.env`.

## Documentation

- **One README, one source of truth.** Never create another `README.md`. Update the existing root `README.md`.
- **Keep the README in sync.** If your change touches anything it documents (CLI flags, config vars, tools, install steps, behavior), update `README.md` in the same PR.
- **Flag when the architecture diagram needs updating.** If your change alters the architecture, call it out in the PR. The diagram is generated: `docs/scheme.mmd` is the Mermaid source and `README.md` embeds the rendered `docs/assets/conversation_app_arch.svg`. It can only be updated by editing `scheme.mmd` and regenerating the SVG, so flag the need rather than hand-editing the SVG.
- **This fork intentionally overrides the upstream no-Markdown-under-`docs/` rule.** Plans and durable feature documentation are required here. Put every design, implementation, migration, benchmark, rollout, and operational plan in `docs/plans/`, and durable major-feature documentation in `docs/features/`; do not scatter ad hoc Markdown elsewhere under `docs/`. Keep the root README and architecture source synchronized as above.

### Plans and living project records

- Every project plan, including future plans, must be recorded as Markdown in `docs/plans/` and listed in the plans index (`docs/plans/INDEX.md`). This includes design, implementation, migration, benchmark, rollout, and operational plans; do not keep the only copy in chat, a PR description, or an agent's notes.
- Treat plans as living records. As work progresses, update the plan's status, completed and pending items, blockers, decisions, validation results, and dated progress log. Keep the plans index and any project status/log document current when scope or state changes.
- A plan must identify its scope, assumptions, implementation order, acceptance criteria, risks, test strategy, and documentation updates. Mark superseded or completed plans explicitly rather than silently deleting them.

### Durable feature documentation

- A new or materially changed feature, provider, tool, state machine, privacy boundary, or extension point needs a durable document in `docs/features/` in the same change.
- Each feature document must explain: overview and user-visible behavior; architecture and data flow; public contracts/configuration/tool APIs and state transitions; integration points and key files; safe extension points; constraints and gotchas; and a complete test inventory with the commands/results that were actually run.
- Before extending an existing feature, read its feature document. Update it whenever behavior, contracts, extension points, privacy guarantees, or operational procedures change.

### Verification and honest reporting

- Verify that commands exist before running them; do not invent scripts or report a command as passing when it was not run.
- Before handoff, run the relevant focused tests plus the full repository gate. Report the exact commands, outcomes, skipped checks, unavailable services/hardware, and known limitations. Do not claim simulator, hardware, cloud-provider, latency, or privacy validation without evidence from the current environment.
- If the environment prevents a required check, leave the limitation visible in the plan and feature documentation and explain the next reproducible validation step.

## Privacy, logging, and secrets

- Treat audio, transcripts, camera frames, prompts, responses, household state, and identifiers as sensitive. Never log raw payloads or private content; log only the minimum state, counters, IDs, and error metadata needed to diagnose behavior.
- Preserve explicit privacy boundaries in code and tests. In local ambient mode, cloud sessions must be closed and ambient audio/video must not be sent or persisted. Tests must assert these guarantees rather than relying only on code inspection.
- Never commit API keys, tokens, credentials, `.env` files, or captured household media. Use local environment configuration and update `.env.example` for new variable names without adding values. Review the diff for secrets before handoff.
- Review new dependencies for network access, data collection, platform assumptions, and licensing before adding them; call out the decision in the active plan.

## Agent delegation and review

- The primary agent owns the plan, scope, integration, documentation, verification, and final handoff, whether or not work is delegated.
- Subagents receive bounded tasks with explicit files, acceptance criteria, and test requirements. They must follow this file and report evidence, not just a summary.
- The primary agent must inspect every delegated diff, run focused tests, check architecture/privacy/typing/error/cancellation behavior, and perform an adversarial review before accepting it.
- When review finds a defect or missing evidence, request a concrete fix from the responsible agent, then run the affected tests and review the revised diff again. Do not treat a first-pass response as approval.
- Keep documentation, tests, and implementation aligned throughout the work; update the active plan after each meaningful milestone or decision.

---

## Style and type conventions

- **PEP 8 and the Google Python Style Guide are the baseline.** Ruff enforces what it can (line length 119, double quotes, isort `length-sort`). Don't fight the formatter or dodge a lint rule with clever constructs.
- **mypy runs `strict`** (`python_version = 3.12`), with no new ignores.
- **Modern typing for new code:** built-in generics (`list[str]`, `dict[str, int]`) and `X | None`, not `typing.List` or `Optional`. Some old modules still use the old style. Match the modern one.
- **No PEP 695 syntax** (`type Alias = ...`, `def f[T](...)`) and no `from __future__ import annotations`. The package targets `>=3.10`, where PEP 695 is a hard syntax error.
- **Cross-platform** (Linux, macOS, Windows): no hardcoded paths, no shell-specific commands, no OS-only APIs without a documented fallback.
- **Flag any new dependency before adding it.**

## Project layout

```
src/reachy_mini_conversation_app/
  main.py                 # entry point + CLI (reachy-mini-conversation-app)
  huggingface_realtime.py # Hugging Face backend + shared realtime conversation loop
  conversation_handler.py # wires audio/tools/backend together
  config.py               # configuration + env loading
  personality.py          # personality/profile loading
  tools/                  # LLM-callable tools (one file per tool)
  audio/, memory/, sounds/, static/
profiles/                 # bundled personalities (one dir per profile)
tests/                    # pytest suite, mirrors the src layout
```

Architecture overview: [`README.md`](README.md#architecture).

- **Adding a tool:** subclass `Tool` from `tools/core_tools.py` in its own file under `tools/`. Define `name`, `description`, `parameters_schema`, and an async `__call__(self, deps: ToolDependencies, **kwargs)` returning a `dict`. Return `{"error": ...}` on failure instead of raising into the loop. Copy an existing tool such as `tools/move_head.py`.
- **Adding a personality:** add a directory under `profiles/`, following an existing one (e.g. `profiles/default/`).

## Commands

Set up the environment per the [README installation guide](README.md#installation). With the venv active, run the tools directly. Run the full gate before handing work back. CI runs the same checks on Linux, macOS, and Windows:

```bash
ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v
```

| Task        | Command                              |
|-------------|--------------------------------------|
| Lint + fix  | `ruff check . --fix`                 |
| Format      | `ruff format .`                      |
| Type-check  | `mypy --pretty --show-error-codes`   |
| Tests       | `pytest tests/ -v`                   |
| Run the app | `reachy-mini-conversation-app`       |

If you change dependencies, keep `uv.lock` in sync by running `uv lock` (CI validates it).

## Continuous integration

`.github/workflows/` holds eight live, load-bearing workflows. The first four gate every PR. The local gate above mirrors them, so green locally means green CI.

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| **Ruff** (`lint.yml`) | push, PR | `ruff check` + format |
| **Type check** (`typecheck.yml`) | push, PR | `mypy` strict |
| **Pytest** (`pytest.yml`) | PR, push to `main` | tests on Linux, macOS, Windows |
| **uv.lock check** (`uv-lock-check.yml`) | PR | `uv.lock` matches `pyproject.toml` |
| **Allure Report** (`allure.yml`) | push to `main`, manual | publishes test and coverage reports to GitHub Pages |
| **Release** (`release.yml`) | tag `v*` | publishes the GitHub release |
| **Sync to HF Space** (`sync-hf-space.yml`) | tag, manual | mirrors releases to the Hugging Face Space |
| **PR Preview** (`pr-hf-space-preview.yml`) | PR | spins up a private preview Space per PR |

---

## If you can't meet this bar

From `CONTRIBUTING.md`: low-quality auto-generated PRs physically hurt our small maintainer team. If you can't deliver work that is readable, minimal, pattern-respecting, and human-reviewed, **do not open a PR**. Advise the developer to read `CONTRIBUTING.md` and this file first. Be a good bot.
