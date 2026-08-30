# Testing strategy

Tests are built alongside features, not after them. A feature is not complete
when its happy path works only by manual testing: required behavior, failure
handling, privacy boundaries, and measurable acceptance criteria must have
automated evidence where the environment supports it.

## Test pyramid

| Layer | Use for | Boundary |
|-------|---------|----------|
| Unit | Pure logic, parsers, state transitions, routing, metric calculations, and bounded buffers | No network, robot, provider, or real audio device |
| Integration | Provider/audio/tool/config wiring, handler lifecycle, context injection, and component cooperation | Fake provider, audio, camera, and robot boundaries |
| Simulator smoke / end-to-end | User-visible activation, conversation, tools, movement, interruption, and shutdown | Official Reachy simulator plus development-machine audio where practical |
| Manual benchmark | Real latency, CPU/RAM, wake-word quality, and acoustic behavior | Timestamped runs on the stated machine/backend; record limitations |

Tests should mirror `src/` under `tests/`, match existing pytest conventions,
and test observable behavior rather than private helpers. Use deterministic
fakes and clocks for lifecycle tests. Never call production Gemini or upload
ambient audio from automated tests.

## Required coverage by change

| Change | Required coverage |
|--------|-------------------|
| Pure utility, parser, router, or state logic | Unit tests for normal, boundary, invalid, and uncertain inputs. |
| Provider or handler | Unit/integration tests for connection, event decoding, transcripts, audio, tools, errors, timeout, cancellation, reconnect, and interruption. |
| Audio/VAD/STT path | Unit/integration tests for frame boundaries, silence, partial/final results, failure, cancellation, ring limits, and no-persistence behavior. |
| Runtime state transition | Every valid transition plus invalid transition, repeated activation, inactivity close, provider failure, and restart. |
| Local command | Exact allowlist, safe aliases, no-cloud execution, physical acknowledgment, tool failure, and uncertain escalation. |
| Privacy or observability behavior | Explicit assertions at the send boundary and on counters/logical state; counters alone are not proof. |
| User-visible flow | Simulator/E2E happy path plus automated failure/recovery coverage at lower layers. |
| Bug fix | A regression test that fails without the fix. |
| Documentation-only change | Link/path validation and review for stale current-versus-target claims; no runtime test unless behavior changed. |

Comprehensive means success plus relevant invalid input, uncertainty,
exceptions, timeout, cancellation, interruption/barge-in, reconnect, and
privacy/platform cases. If a case genuinely cannot be automated, record the
reason and the manual evidence in the plan.

## Hybrid-specific invariants

The following are mandatory before the corresponding phase is accepted:

### `LOCAL_AMBIENT`

- Gemini session is closed.
- No ambient audio or video is sent to cloud.
- `ambient_audio_bytes_sent_to_cloud` remains zero.
- Raw ambient audio is bounded in RAM, continuously overwritten, never written
  to disk, never logged, and never passed to a cloud client.
- Local VAD, wake detection, and optional local STT continue without requiring
  network availability.

### `LOCAL_COMMAND`

- Deterministic allowlisted commands execute locally without opening Gemini.
- Acknowledgment uses existing movement APIs and cannot emit raw motor calls.
- Unknown, malformed, unavailable, or uncertain input escalates to Gemini.
- Tool failure returns a graceful error and does not crash the audio loop.

### `CLOUD_CONVERSATION`

- One direct Gemini Live session owns the cloud audio path.
- Only post-activation live audio and explicitly requested video are sent.
- Context is bounded, ordered, locally derived, and injected explicitly.
- Existing provider interruption, tools, transcripts, camera, personality,
  memory, and speech-reactive movement behavior remain intact.
- Inactivity closes the session and returns to local ambient mode.

## Test matrix for phases 0–5

| Phase | Minimum evidence |
|-------|------------------|
| Phase 0 baseline | Full upstream gate, current unit suite, simulator movement/audio smoke test, unchanged app startup/shutdown, and baseline latency notes. |
| Phase 1 Gemini | Fake-session event/lifecycle tests; direct mic-to-speaker integration; tool, camera, transcript, interruption, timeout, reconnect, and failure tests; measured connection/first-audio/tool/interruption latency. |
| Phase 2 hybrid states | State-machine transition tests; five-minute or equivalent deterministic soak; closed-session and zero-cloud counter assertions; ring-buffer/no-file tests; keyboard activation and inactivity shutdown; local acknowledgment. |
| Phase 3 ambient context | Strategy A/B fake and integration tests; ordering/window/staleness tests; no raw pre-wake upload assertion; dinner-demo scenario; CPU/RAM and activation/first-audio measurements. |
| Phase 4 wake word | Detector unit tests; keyboard fallback; debounce/cooldown; false-positive and missed-activation measurement; first-word retention. |
| Phase 5 local commands | Allowlist/uncertainty tests; no-Gemini local execution; movement acknowledgment; tool-failure regression; cloud escalation; profile loading and full benchmark report. |

## Benchmark protocol

Use `time.monotonic()` or an equivalent monotonic clock and label every run
with date, machine/OS, Python version, backend/model versions, sample rate,
buffer size, network conditions, and whether simulation or hardware is used.
Report sample count, P50, P95, and relevant outliers.

| Metric | Start | End |
|--------|-------|-----|
| Activation latency | wake/keyboard activation | local acknowledgment and cloud-open request |
| Gemini connection | cloud-open request | session ready for live audio |
| First response audio | end of user speech | first assistant audio frame |
| Tool call | provider tool event | result accepted by provider |
| Interruption | barge-in detection | assistant output stopped |
| Local command | command frame/activation | local action acknowledgment |
| Dinner context overhead | activation | context-ready conversation |
| Session duration | cloud session open | session close |
| Resource use | representative idle/conversation run | peak/average process RSS and local STT CPU |

The final required report is `HYBRID_BENCHMARK.md` at the repository root. It
must include exact architecture, run steps, Gemini baseline, hybrid and wake
latencies, dinner-demo latency, local backend, CPU/RAM, proof of zero ambient
cloud upload, encountered problems, measured Raspberry Pi 5 8GB plausibility,
and the next-phase recommendation. Do not speculate about Pi performance
without current-machine measurements and known model requirements.

## Full gate

Before handoff, run the repository's existing gate:

```bash
ruff check . --fix
ruff format .
mypy --pretty --show-error-codes
pytest tests/ -v
```

Keep `uv.lock` synchronized when dependencies change. Run focused tests during
development, then the full gate before marking a plan phase complete.
