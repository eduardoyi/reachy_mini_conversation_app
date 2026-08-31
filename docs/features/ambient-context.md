# Feature: local ambient context

- Status: `in-progress`
- Last updated: 2026-08-31
- Plan: [Reachy hybrid household companion](../plans/reachy-hybrid-household-companion.md)

## Overview

This module provides the local-only VAD/STT boundary needed for the dinner
demo. It can either maintain a continuous rolling transcript (Strategy A) or
transcribe a RAM audio snapshot only when activation occurs (Strategy B). It
does not connect to a provider, persist audio, add a model dependency, or
replace the existing realtime conversation loop.

The parent runtime owns the bounded `AmbientAudioBuffer` and passes its
defensive snapshot to Strategy B. `HybridRuntime` now connects the processor to
local transcript wake events and injects only its bounded derived text into a
new provider session. The hybrid runtime separately submits the bounded
wake-stripped suffix as the first user turn after provider readiness.

## User-facing behavior

While idle, the eventual hybrid runtime can pass copied microphone frames to
`AmbientContextProcessor`. The processor runs the selected local VAD and keeps
only a bounded in-memory speech segment until local STT completes. In Strategy
A, completed local transcripts are retained for the configured time and text
limits. In Strategy B, no transcript is retained between calls; activation
transcribes the supplied in-memory ring snapshot and returns derived text.

An empty snapshot, empty transcription, VAD failure, STT failure, or local
timeout produces empty context and keeps the cloud boundary closed. A caller
must treat empty context as normal rather than opening a provider because of
the local processor.

## Architecture and data flow

```mermaid
flowchart LR
  Mic[Copied microphone frame] --> VAD[LocalVAD]
  VAD --> Segment[Bounded in-memory speech segment]
  Segment --> STT[LocalSTT]
  STT --> Rolling[RollingTranscript]
  Ring[AmbientAudioBuffer snapshot] --> Activation[Activation-time LocalSTT]
  Rolling --> Context[Text-only AmbientContext]
  Activation --> Context
  Context --> Gemini[Cloud session integration]
```

`LocalVAD` and `LocalSTT` are typed protocols. Implementations may be
synchronous or return an awaitable; the processor runs them behind bounded
asyncio timeouts. Synchronous calls execute in a processor-owned bounded
executor so one model call does not block the event loop. A timeout cancels the
waiter but not an already-running synchronous function: its admission slot and
defensive frame copy remain tracked until the worker exits. STT results are
committed by segment sequence, not completion order, so a slow earlier result
cannot reorder the ambient context. Results that arrive after a newer sequence,
or after a newer activation request, are rejected.

Strategy A activation reads the already-completed rolling transcript without
waiting for an in-flight local STT call. This keeps wake-to-cloud latency
bounded; the still-local STT result may become available for a later
activation. Strategy B deliberately waits only up to its configured STT
timeout because activation-time transcription is its context source.

The processor normalizes accepted frames to defensive mono int16 before local
processing. It never returns an audio frame to its caller. `AmbientContext`
contains only normalized derived text and ordering metadata; metrics contain
only counters and aggregate durations.

## Canonical files and contracts

| Area | File or interface | Responsibility / contract |
|------|-------------------|---------------------------|
| Local VAD | `ambient_context.LocalVAD` | `is_speech(frame)` returns `bool` or an awaitable boolean. |
| Local STT | `ambient_context.LocalSTT` | `transcribe(audio)` returns text or an awaitable string. |
| Strategy selection | `AmbientContextStrategy` | `CONTINUOUS_TRANSCRIPT` for Strategy A; `ACTIVATION_BUFFER` for Strategy B. |
| Bounds | `AmbientContextConfig` | Time window, text size, speech segmentation, VAD timeout, and STT timeout. |
| Rolling context | `RollingTranscript` | Accepts ordered final `TranscriptSegment` values and returns bounded text. |
| Coordinator | `AmbientContextProcessor` | Observes local frames, schedules Strategy A STT, and builds activation context. |
| Integration output | `AmbientContext` | Text-only context plus strategy/time/sequence metadata; no PCM. |
| Diagnostics | `AmbientContextMetrics` | Immutable metadata-only counts and aggregate local timing. |
| Runtime integration | `hybrid_runtime.HybridRuntime` | Supplies ring snapshots locally and configures provider startup with derived text only. |

Important contracts:

- `transcript_window_seconds` and `max_transcript_chars` are always positive.
- A transcript segment must have a positive sequence and finite, ordered times.
- Final transcript text is whitespace-normalized and bounded to the newest text.
- Partial segments can be staged for the same sequence but are not returned as
  context until a final segment replaces them.
- `context_for_activation(snapshot)` does not persist the snapshot or add it to
  the rolling transcript. Strategy B sends only its returned text to the next
  integration layer.
- Strategy A does not wait for pending STT at activation. The
  `continuous_stt_pending_at_activation` counter records how many results were
  still outstanding; those results remain local and can populate a later
  context.
- Continuous STT retains at most two pending segments by default. Under
  overload, the newest additional segment is dropped locally, ordering advances,
  and a metadata counter records the drop.
- Local backend admission is bounded independently of waiter timeouts. The
  `local_work_pending` metric includes calls whose synchronous worker is still
  running after a timeout or cancellation; those calls do not admit more audio
  until they exit.
- `close()` is idempotent, cancels pending local STT, and releases in-memory
  speech references. If an uncancellable synchronous worker is still running,
  close returns with that work visible in `local_work_pending` and logs only its
  count; the executor shuts down after the worker exits. Calls after close fail
  clearly instead of silently doing work.

## Extension guide

### Safe to extend

- Implement `LocalVAD` with a local Silero/openWakeWord-compatible boundary or
  another measured backend without changing the processor.
- Implement `LocalSTT` with a local Whisper, Parakeet, or other backend. Keep
  model loading and platform-specific code outside this module.
- Add a transcript observer to forward derived local transcript events to the
  wake-word or local-intent layer. The observer receives no audio.
- Benchmark both strategies by passing `strategy=` to
  `context_for_activation`; do not upload the pre-activation snapshot.

### Do not change without updating this document and its tests

- Do not return, serialize, or log raw audio from this module.
- Do not make a cloud/provider call from a VAD or STT implementation used here.
- Do not remove ordering checks or timeout/cancellation handling.
- Do not turn the transcript buffer into persistent memory. Persistent memory
  is a separate local feature.

### Common extension path

1. Add a typed local backend implementing `LocalVAD` or `LocalSTT`.
2. Add deterministic fake-backend tests for success, timeout, failure,
   cancellation, ordering, and input-copy behavior.
3. Wire it through the hybrid runtime, then run the privacy and simulator tests
   at the integration boundary.

## Constraints, privacy, and failure modes

- No dependency beyond the application's existing Python and NumPy runtime was
  added for this seam.
- Raw frames are copied, downmixed locally, and held only while required for
  local segmentation/transcription. The module has no file, database, or cloud
  output path.
- Logs contain operation names and exception types only; transcript and PCM
  payloads are not logged.
- A VAD timeout/error skips that frame and leaves the coordinator usable.
- An STT timeout/error records metadata, advances the sequence barrier, and
  leaves later segments eligible for context.
- A slow STT backend cannot retain an unbounded number of audio segments;
  `max_pending_stt_tasks` bounds admission and `continuous_stt_dropped` records
  overload without logging content. VAD has one admitted call at a time, and
  `vad_dropped` records frames rejected while a blocking VAD worker is still
  running.
- The owning capture loop currently awaits each local frame observation. A slow
  VAD can therefore delay capture and contribute to capture-worker drops; this
  is a benchmark follow-up rather than a reason to remove the bounded admission
  and executor safeguards.
- Speech shorter than `min_speech_seconds` is discarded once the configured
  silence boundary is reached. A sample-rate change also discards the current
  segment and advances its sequence barrier; neither can block later context.
- Cancelling an observation or activation propagates `CancelledError` so the
  owning runtime can shut down deterministically. `close()` cancels background
  STT tasks and is safe to call repeatedly.
- An activation result from an older concurrent request becomes empty context;
  this prevents stale derived text from being injected into a newer cloud
  session.
- A synchronous backend runs in the processor-owned bounded executor;
  cancelling its await does not forcibly stop code already executing inside
  that worker thread. Its result and frame are retained only until the worker
  exits, then discarded at the timeout/stale-generation boundary. `close()`
  does not claim to have released such work until its completion callback runs.
- The module does not claim OS-level swap protection. The application-level
  guarantee is that no raw ambient audio is persisted or sent to a provider by
  this seam.

## Dependencies

- Depends on: existing `AudioFrame`, `AmbientAudioBuffer` integration boundary,
  and `audio_to_mono_int16`; no new package.
- Used by: the hybrid runtime, local transcript wake detector, and local intent
  activation path.

## Testing

Tests ship with this seam. They use deterministic VAD/STT fakes and do not need
credentials, a cloud session, a microphone, a simulator, or robot hardware.

### Unit tests

| File | Scenarios |
|------|-----------|
| `tests/test_ambient_context.py` | Configuration bounds; transcript normalization, partial/final replacement, 2/5/10/20-second and text limits, stale rejection; Strategy A speech segmentation, defensive copies, ordered out-of-order results, bounded STT backlog and failure recovery; Strategy B snapshot-only transcription and bounded derived text; concurrent activation staleness; VAD/STT timeout and failure; repeated blocking-sync timeout admission, bounded frame retention, honest close, async cancellation, empty snapshots, and payload-free logging. |

### Integration tests

| File | Scenarios |
|------|-----------|
| `tests/test_hybrid_runtime.py`, `tests/test_gemini_live.py`, `tests/test_main.py` | Strategy A/B activation ordering, Gemini ordered initial-history seeding, addressed-text-before-PCM ordering, seed failure/cancellation/shutdown cleanup, wake routing, application wiring, and zero ambient cloud bytes. |

### Simulator / end-to-end tests

| Command or artifact | Scenarios |
|---------------------|-----------|
| Pending live acceptance | Measure Strategy A/B activation latency, first response audio, local CPU/RAM, and dinner-demo transcript quality on the simulator/Mac path. |

### Test commands

```bash
.venv/bin/ruff check src/reachy_mini_conversation_app/ambient_context.py tests/test_ambient_context.py
.venv/bin/mypy --pretty --show-error-codes src/reachy_mini_conversation_app/ambient_context.py
.venv/bin/pytest tests/test_ambient_context.py -q
```

Validated on 2026-08-29: focused Ruff and strict mypy passed; the combined
Phase 3–5 implementation suite passed 195 tests. After final integration, the
repository gate passed all 649 tests; the live Strategy A/B benchmark is
recorded separately in the plan.

The 2026-08-31 provider-handoff correction passed 108 focused Gemini/runtime
tests and the full repository gate passed 736 tests with one explicit native
skip, plus Ruff, formatting, strict mypy, lock consistency, JavaScript syntax,
and `git diff --check`.

## Changelog

| Date | Change |
|------|--------|
| 2026-08-29 | Added dependency-free VAD/STT protocols, bounded Strategy A/B context processor, ordered transcript handling, timeout/cancellation paths, metadata-only metrics, and deterministic tests. |
| 2026-08-29 | Integrated text-only activation context, transcript observation, local wake routing, and zero-prewake-cloud privacy tests through `HybridRuntime`. |
| 2026-08-29 | Added a two-request continuous-STT admission bound with overload metrics and ordering-safe drop behavior. |
| 2026-08-29 | Replaced uncancellable default-thread dispatch with a processor-owned bounded executor. Sync timeout/cancellation now retains admission and frame ownership until the worker exits; close reports still-running work honestly, with deterministic blocking VAD/STT regressions. |
| 2026-08-29 | Rejected reordered partial updates after the same transcript sequence was finalized, preventing late partials from reopening or duplicating final context. |
| 2026-08-30 | Recorded the current inline capture/VAD await boundary and its possible capture-drop impact; concurrency remains a measured performance follow-up. |
| 2026-08-31 | A live Strategy A run produced 96 local context characters from three completed segments but Gemini asked for context. Corrected the provider handoff to ordered initial history and serialized the addressed question before bounded live PCM; live response-quality retest remains pending. |
