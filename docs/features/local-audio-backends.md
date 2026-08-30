# Feature: local audio backends

- Status: `in-progress`
- Last updated: 2026-08-29
- Plan: [Reachy hybrid household companion](../plans/reachy-hybrid-household-companion.md)

## Overview

This module supplies the first concrete local processing backends for the
ambient-context protocols. `RmsEnergyVAD` is a small, conservative baseline for
development and benchmarking. `OpenAICompatibleLocalSTT` sends an in-memory
mono PCM16 WAV to a local OpenAI-compatible transcription server, such as a
locally hosted Whisper-compatible endpoint. Neither backend contacts Gemini,
stores audio, or requires a new dependency.

The RMS detector is intentionally a VAD baseline, not a production wake-word
detector. A measured Silero VAD or other local VAD can be substituted later;
the transcript wake detector can independently be replaced by a measured
acoustic wake model without changing `AmbientContextProcessor`.

## User-facing behavior

When configured as the local VAD, the RMS backend returns a boolean for each
valid microphone frame. Silence and frames below the configured threshold are
false; non-zero frames at or above the threshold are true. Invalid samples or
frame metadata fail locally and do not reach a provider.

When a local STT endpoint is configured, the STT backend normalizes each frame
to mono int16, resamples non-16 kHz input to 16 kHz, creates a WAV payload in
memory, and posts it to `/v1/audio/transcriptions`. It accepts either a JSON
object containing a string `text` field or a plain UTF-8 response. A missing,
malformed, oversized, failed, or timed-out response is reported as a sanitized
local error so the ambient processor can continue without context.

If no local STT endpoint is configured, the parent runtime should use a no-op
or explicitly selected fake backend; this module does not invent a remote
fallback.

## Current architecture and target changes

The ambient processor already owns segmentation, bounded backend admission,
timeouts, ordering, bounded transcript storage, and activation strategy
selection:

```mermaid
flowchart LR
  Mic[Copied microphone frame] --> VAD[RmsEnergyVAD or another LocalVAD]
  VAD --> Segment[AmbientContextProcessor segment]
  Segment --> STT[OpenAICompatibleLocalSTT or another LocalSTT]
  STT --> Transcript[Bounded local transcript]
  Transcript --> Context[Text-only activation context]
  Context --> Gemini[Optional cloud session]
```

`local_audio_backends.py` owns only backend-specific validation and transport.
The processor passes copied frames and is the only component that decides
whether a continuous segment or an activation snapshot is transcribed. The
STT backend returns derived text only; pre-activation PCM is never forwarded to
the cloud provider by this module.

## Canonical files and contracts

| Area | File or interface | Responsibility / contract |
|---|---|---|
| VAD protocol | `ambient_context.LocalVAD` | `is_speech(frame)` returns `bool` or an awaitable boolean. |
| STT protocol | `ambient_context.LocalSTT` | `transcribe(frame)` returns text or an awaitable string. |
| Baseline VAD | `local_audio_backends.RmsEnergyVAD` | Validates a frame, downmixes locally, and compares RMS against a bounded threshold. |
| Local STT | `local_audio_backends.OpenAICompatibleLocalSTT` | Posts a memory-only WAV to the validated loopback endpoint and parses bounded text. |
| Sanitized errors | `LocalSTTError`, `LocalSTTResponseTooLarge` | Expose operation/status metadata, never response content or audio. |
|

`OpenAICompatibleLocalSTT` accepts:

```python
OpenAICompatibleLocalSTT(
    "http://127.0.0.1:8765/v1",
    model="whisper-1",
    language="en",  # optional ISO/BCP-47-style hint
    timeout_seconds=15.0,
    target_sample_rate=16_000,
    max_audio_seconds=20.0,
    max_response_bytes=256 * 1024,
    max_text_chars=16_000,
)
```

An optional existing `httpx.AsyncBaseTransport` is available for deterministic
tests. It is not needed in production. No authorization header or API key is
added by this client. `language`, when non-empty, is validated as a bounded
ISO/BCP-47-style tag and sent as the multipart `language` form field; it is
omitted otherwise. `trust_env=False` is always used so proxy environment
variables cannot redirect loopback audio outside the machine.

The endpoint must use `http` or `https` and have exactly `localhost`, an IPv4
loopback address in `127.0.0.0/8`, or IPv6 `::1` as its host. Public/private
addresses, arbitrary DNS names, credentials, query strings, and fragments are
rejected before any request is created. Hostnames are not resolved: allowing
only the explicit loopback names avoids DNS rebinding and accidental household
or internet uploads.

## Extension guide

### Safe to extend

- Add another `LocalVAD` or `LocalSTT` implementation in a separate module.
- Keep model loading outside the processor and behind the same typed protocol.
- Use the injected transport to test an endpoint adapter without network I/O.
- Add a format or response variant only with bounded parsing and a privacy test.

### Do not change without updating this document and its tests

- Do not permit a public, private-LAN, or arbitrary DNS endpoint for ambient
  audio.
- Do not enable environment proxy discovery for this loopback-only request.
- Do not add authorization or cloud fallback behavior to this adapter.
- Do not write WAV files, raw frames, response bodies, or transcripts to disk.
- Do not remove the request timeout, response byte bound, text bound, or
  20-second input bound, cancellation cleanup, or proxy bypass.
- Do not log audio, multipart bodies, transcript text, or server response text.

### Common extension path

1. Implement the `LocalVAD` or `LocalSTT` protocol with explicit resource and
   platform bounds.
2. Add deterministic fake-transport tests for success, invalid input, failure,
   timeout, cancellation, response limits, and privacy-safe logging.
3. Wire it through `AmbientContextProcessor`, then add hybrid-runtime tests for
   context ordering and zero pre-activation cloud bytes.

## Constraints, privacy, and failure modes

- Input is normalized and copied in memory. Stereo frames are downmixed before
  VAD or WAV encoding; the source array is never retained.
- Non-16 kHz input is resampled in memory using the existing generic resampler.
- WAV construction uses `io.BytesIO`; no temporary or persistent file is made.
- Input longer than 20 seconds is rejected before transport, keeping the
  default 16 kHz mono WAV below approximately 641 KB.
- Every STT request is made through a newly scoped `httpx.AsyncClient` and is
  closed on success, error, timeout, or cancellation. The full request is
  bounded by `timeout_seconds`, including transports that do not enforce
  httpx socket timeouts themselves.
- The consuming `AmbientContextProcessor` owns a bounded executor for
  synchronous backends. A timed-out sync call may continue until its model
  function returns, but its slot and copied frame remain tracked, preventing
  queued ambient audio from growing without bound. `close()` reports such work
  as pending instead of claiming it was cancelled; the executor shuts down
  once the worker exits.
- Responses are streamed and rejected after `max_response_bytes`; declared
  lengths are checked before reading. Text is rejected after `max_text_chars`.
- HTTP status, request, timeout, UTF-8, and parse errors are sanitized. Error
  messages contain no URL query, server body, request body, or transcript.
- Cancellation propagates `asyncio.CancelledError` to the processor/runtime;
  it is not converted into an empty successful transcription.
- The loopback allowlist is deliberately stricter than “private network”: a
  Raspberry Pi/LAN STT service must be moved behind an explicitly reviewed
  local transport or a future allowlist change with new privacy evidence.
- The RMS threshold is amplitude-dependent and does not handle echo,
  background noise, or wake-word semantics. Production selection requires
  measurements on the target microphone.
- Backend privacy is part of the boundary: the current MLX Audio server writes
  a temporary input file and is therefore rejected for ambient use. The first
  benchmark candidate is Hugging Face Transformers Serve, whose current
  transcription handler reads uploaded bytes and decodes them from `BytesIO`.
  Monitor temporary directories during live acceptance before claiming the
  complete sidecar path is RAM-only.

## Dependencies

- Depends on: existing `httpx`, NumPy, `AudioFrame`,
  `audio_to_mono_int16`, `audio_to_float32`, and `resample_audio`.
- Used by: `AmbientContextProcessor` and the Phase 3–5 hybrid integration.
- New runtime dependencies: none.

## Testing

Tests are hardware-, credential-, disk-, and network-independent.

### Unit and transport tests

| File | Scenarios |
|---|---|
| `tests/test_local_audio_backends.py` | RMS threshold and stereo behavior; invalid rates, dimensions, dtypes, non-finite samples, and thresholds; multipart model/file/language shape; WAV mono/PCM16/16 kHz metadata; stereo downmix and resampling; 20-second request bound; JSON/plain-text parsing; status and malformed response errors; response byte/text limits; bounded timeout and client cleanup; cancellation cleanup; loopback allowlist and rejection of public/private/DNS/credential/query endpoints; no-auth, proxy-bypass, language-validation, and privacy-safe logs. |

### Integration tests

`tests/test_hybrid_runtime.py` and `tests/test_main.py` prove that the local STT
boundary is composed only for hybrid local processing, that Strategy B receives
the defensive RAM snapshot locally, and that no pre-activation audio bytes
reach a provider. A live local STT server is intentionally not a test-suite
dependency.

### Simulator / end-to-end tests

No simulator or physical-microphone claim is made by this module. Phase 3
manual runs must record the selected local backend, activation/context latency,
CPU/RAM, and transcript quality on the development machine.

### Test commands

```bash
.venv/bin/ruff check src/reachy_mini_conversation_app/local_audio_backends.py tests/test_local_audio_backends.py
.venv/bin/ruff format --check src/reachy_mini_conversation_app/local_audio_backends.py tests/test_local_audio_backends.py
.venv/bin/mypy --pretty --show-error-codes src/reachy_mini_conversation_app/local_audio_backends.py
.venv/bin/pytest tests/test_local_audio_backends.py -v
```

Validation on 2026-08-29: focused Ruff checks passed, formatting was unchanged,
strict mypy passed for the backend source, and 39 focused tests passed in 0.91 s.
After final integration and added duration-bound coverage, the full repository
gate passed all 649 tests. Live local-server privacy and resource validation
remain pending.

## Changelog

| Date | Change |
|---|---|
| 2026-08-29 | Added dependency-free RMS VAD and loopback-only OpenAI-compatible local STT with in-memory WAV encoding, bounded response parsing, cancellation cleanup, and deterministic privacy tests. |
| 2026-08-29 | Disabled environment proxy discovery and added an optional validated language hint, with regression coverage for proxy bypass and multipart inclusion/omission. |
| 2026-08-29 | Bounded uploads to 20 seconds and rejected MLX Audio's temp-file STT server for this privacy boundary; documented the in-memory Transformers Serve candidate and required live temp-directory verification. |
| 2026-08-29 | Documented the processor-owned bounded executor and honest close behavior for blocking synchronous VAD/STT implementations. |
| 2026-08-30 | Corrected the Transformers Serve launch command after live acceptance showed that `FORCE_MODEL` is positional. The installed 5.16.1 help summary omits the transcription route, but its server implementation includes `/v1/audio/transcriptions`. |
