# Feature: deterministic local intents

- Status: `in-progress`
- Last updated: 2026-08-30
- Plan: [Reachy hybrid household companion](../plans/reachy-hybrid-household-companion.md)

## Overview

`LocalIntentRouter` is the Phase 5 safety boundary for commands that are small,
obvious, and useful without Gemini. It recognizes a deliberately small exact
allowlist and returns immutable metadata. `LocalActionExecutor` executes only
those local decisions through existing tool and movement/playback boundaries;
neither class opens a provider or persists the utterance.

The modules are intentionally small so `HybridRuntime` can route only an exact
addressed suffix while in `LOCAL_COMMAND`. Automated integration is complete.
A live simulator run with the configured wake phrase `computer` reached local
activation, but `look left` escalated to Gemini and Gemini performed the
movement. It therefore did not satisfy local-command acceptance. The same path
with `Reachy` remains blocked because no exact matching final transcript token
reached the detector.

If local STT emits the wake and command as separate final segments, the runtime
waits briefly for one follow-up and routes its exact stripped text through the
same fail-closed boundary. The timeout is bounded; uncertain follow-ups still
escalate to Gemini and are never guessed locally.

## User-facing behavior

The allowlist contains:

| Action | Accepted exact phrases |
|---|---|
| `stop` | `stop`, `stop moving`, `halt` |
| `be_quiet` | `be quiet`, `quiet`, `shush`, `stop talking` |
| `look_left` | `look left`, `look to the left` |
| `look_right` | `look right`, `look to the right` |
| `look_at_me` | `look at me`, `look here` |

Case and boundary punctuation are normalized locally. Additional words,
ambiguous combinations, malformed values, and unsupported synonyms escalate to
Gemini. A local decision carries no spoken response requirement; the caller
may use the existing physical acknowledgment/movement path.

## Architecture and data flow

```text
local STT or wake detector
          │
          ▼
LocalIntentRouter.route(...)
          │
          ├── LOCAL_COMMAND + typed action metadata
          │       └── LocalActionExecutor
          │               ├── MoveHead / HeadTracking tools
          │               └── MovementManager / silence callbacks
          └── ESCALATE
                  └── normal Gemini activation path (caller-owned)
```

The router has two explicit input contracts:

- `route_wake_prefixed(text)` accepts text whose first words are the configured
  wake word, such as `Reachy, look left`.
- `route_wake_stripped(text)` accepts text after a local wake detector has
  removed the wake word, such as `look left`.

The contracts do not silently overlap. A missing prefix in the prefixed method
and an unexpected prefix in the stripped method both escalate.

## Canonical files and contracts

| Area | File or interface | Responsibility / contract |
|---|---|---|
| Router | `src/reachy_mini_conversation_app/local_intent_router.py` | Pure, side-effect-free exact classifier |
| Executor | `src/reachy_mini_conversation_app/local_actions.py` | Async typed executor for local decisions |
| Decision | `LocalIntentDecision` | Frozen route/action/normalization metadata |
| Action | `LocalIntent` | Five allowlisted action values; no motor data |
| Route | `IntentRoute` | `local_command` or `escalate` |
| Reason | `IntentDecisionReason` | Explainable fail-closed reason metadata |
| Result | `LocalActionResult` | Frozen execution status and metadata |

`LocalIntentDecision.action` is `None` for every escalation. `is_local` and
`should_escalate` are convenience properties for callers. `normalized_text`
is metadata for routing/tests; do not log it because transcripts are sensitive.

`LocalActionExecutor.execute()` accepts a local decision and returns
`LocalActionResult`. `NOT_LOCAL` means the decision must continue through the
cloud path. `FAILED` means a local action was selected but its callback or
existing tool failed. `EXECUTED` means the boundary completed. Stop defaults to
`MovementManager.clear_move_queue()`; callers may provide an async stop
callback for interruption/playback work. Be-quiet requires a caller-provided
silence callback. Look-left/right reuse `MoveHead`; look-at-me reuses
`HeadTracking`. Cancellation propagates as `asyncio.CancelledError` and is not
misreported as success.

The default wake word is `Reachy`. The constructor accepts a replacement wake
phrase for tests and later local detector experiments; it is normalized before
matching.

## Extension guide

### Safe to extend

- Add one narrowly defined alias only after a false-positive review and a
  regression test.
- Add another `LocalIntent` only when its action can be represented by existing
  local APIs and has an explicit privacy/latency test.
- Add a different detector upstream; keep the router's stripped/prefixed
  contract and exact-match boundary.
- Provide stop and silence callbacks from the owning runtime; callbacks must
  operate through existing playback/movement boundaries and must not call raw
  robot motor APIs.

### Do not change without updating this document and its tests

- Do not turn the router into an LLM, fuzzy matcher, or cloud gateway.
- Do not return callbacks, motor positions, tool instances, or raw audio.
- Do not make the executor call `ReachyMini` directly; use `ToolDependencies`,
  existing tools, `MovementManager`, and caller-owned callbacks.
- Do not treat an uncertain phrase as a local action.
- Do not make wake-prefixed input optional in the prefixed method.

### Common extension path

1. Add the typed action and its exact aliases.
2. Add positive, punctuation/case, false-match, ambiguity, malformed, and
   escalation tests.
3. Wire the action through existing `MovementManager`/tool boundaries and add
   integration/privacy tests there.
4. Run the focused router tests and the full repository gate.

## Constraints, privacy, and failure modes

- The router never opens Gemini and never sends bytes to any provider.
- The executor never calls the robot object directly. Directional actions use
  the existing `MoveHead`/`HeadTracking` tool contracts; stop and quiet use
  movement/playback callbacks.
- It never stores raw audio or transcripts and does not log payloads.
- It fails closed for invalid values, invalid input mode, missing wake word,
  unexpected wake word, incomplete commands, and ambiguous text.
- Unicode normalization and punctuation handling are deterministic; they are
  not fuzzy semantic matching.
- The returned dataclass is immutable so another thread cannot alter a routing
  decision after classification.
- Action execution, cloud escalation, acknowledgment, interruption, and
  movement errors belong to the caller and must be tested at that boundary.
- Tool error payloads and callback exceptions become typed `FAILED` results;
  cancellation is deliberately re-raised so lifecycle owners can stop work.

## Dependencies

- Depends on: Python standard library and existing `ToolDependencies`,
  `MoveHead`, and `HeadTracking` contracts.
- Used by: `HybridRuntime` after local transcript wake detection.

## Testing

### Unit tests

| File | Scenarios |
|---|---|
| `tests/test_local_intent_router.py` | Canonical commands and aliases, wake-prefixed/stripped contracts, punctuation/case, custom wake words, false matches, ambiguity, incomplete/malformed input, fail-closed modes, and frozen typed metadata |
| `tests/test_local_actions.py` | Exact action mapping, movement-manager/playback callbacks, existing tool reuse, tool/callback failures, cancellation, non-local decisions, no raw robot calls, and frozen results |

### Integration tests

`tests/test_hybrid_runtime.py` verifies that exact local decisions do not
construct a cloud handler, failures and uncertain text escalate, and required
privacy counters remain zero. `tests/test_main.py` verifies composition with
the existing movement/playback owners.

Focused validation for this module:

```bash
pytest tests/test_local_intent_router.py -v
pytest tests/test_local_actions.py -v
ruff check src/reachy_mini_conversation_app/local_intent_router.py tests/test_local_intent_router.py
ruff check src/reachy_mini_conversation_app/local_actions.py tests/test_local_actions.py
mypy --pretty --show-error-codes
```

Validated on 2026-08-29: the focused router/action suites, integration tests,
Ruff, formatting, and strict mypy passed. After final integration, the full
repository gate passed all 649 tests. On 2026-08-30, a live simulator run with
`LOCAL_WAKE_WORD=computer` confirmed final STT → wake detector → local
activation, but the command escalated to Gemini and Gemini called `MoveHead`.
A `Reachy` wake was not accepted because no exact matching final transcript
token reached the detector. Live local-command acceptance, false-positive/miss
rates, latency, and the remaining commands still require measurement.

The split-final integration regressions additionally cover exact follow-up
success without provider construction, uncertain/timeout cloud escalation with
FIFO preservation, cancellation/close, capture-boundary dual observation, and
zero ambient-cloud bytes. They use fake providers and local processors; no live
split-final latency claim is made.

## Changelog

| Date | Change |
|---|---|
| 2026-08-29 | Added the fail-closed typed local intent router and deterministic test matrix. |
| 2026-08-29 | Added the async executor using existing movement/tool boundaries, with failure and cancellation coverage. |
| 2026-08-29 | Integrated wake-suffix routing into `LOCAL_COMMAND`; exact success remains local and every uncertain/failing decision escalates. |
| 2026-08-30 | Live `computer, look left` reached local activation but escalated; Gemini performed the movement, so Phase 5 live acceptance remains pending. The `Reachy` failure remains isolated to the exact final-transcript match boundary. |
| 2026-08-30 | Added the configurable two-second one-shot split-final follow-up path and deterministic tests for local success, cloud escalation, timeout, FIFO, cancellation, and privacy. Live STT segmentation and latency remain pending. |
