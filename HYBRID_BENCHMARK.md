# Hybrid household companion benchmark

- Status: `in-progress`
- Last updated: 2026-08-29
- Machine: Apple Silicon Mac, simulator, SDK LOCAL media backend
- Scope: Phases 0–5

This report separates implemented/automated evidence from live measurements.
Phases 3–5 are implemented and comprehensively tested with fakes, but their
microphone/local-model acceptance measurements have not yet been run. Missing
values are marked `not measured`; they are not estimated.

## 1. Architecture implemented

```text
Mac microphone → LocalStream → HybridRuntime
                              ├─ LOCAL_AMBIENT
                              │    20 s RAM PCM ring
                              │    RMS VAD → loopback local STT
                              │    bounded rolling transcript → “Reachy” wake
                              ├─ LOCAL_COMMAND
                              │    exact LocalIntentRouter
                              │    existing tools / MovementManager
                              └─ CLOUD_CONVERSATION
                                   bounded derived text context + addressed text turn
                                   direct Gemini Live audio
                                   existing tools / movement / memory
```

Strategy A continuously transcribes completed local speech segments. Activation
uses only already completed bounded text, so it does not wait for an in-flight
STT task. A wake-stripped question is sent as the first user text turn only
after the new provider is ready, so the dinner question actually receives a
response rather than existing only as background instructions. Strategy B keeps the raw ring in RAM and transcribes its defensive
snapshot locally after keyboard activation. Strategy B has no continuous text
stream, so voice wake/local voice commands are intentionally disabled.

The local STT adapter accepts only explicit loopback hosts, disables proxy
environment discovery, sends no credentials, builds WAV input in memory, and
returns text only. Gemini is constructed only after activation. Existing
provider, tool, movement, profile, and local-memory boundaries are reused.

## 2. How to run it

Use headphones to prevent speaker-to-microphone feedback. Configure the ignored
`.env` without committing its values:

```env
REALTIME_PROVIDER=gemini
HYBRID_RUNTIME_ENABLED=1
HYBRID_CLOUD_INACTIVITY_SECONDS=60
HYBRID_AMBIENT_BUFFER_SECONDS=20
LOCAL_CONTEXT_STRATEGY=continuous
LOCAL_STT_BASE_URL=http://127.0.0.1:8766/v1
LOCAL_STT_MODEL=openai/whisper-small
REACHY_MINI_CUSTOM_PROFILE=household_companion
```

One-time optional Apple Silicon local-STT install:

```bash
/opt/homebrew/bin/uv tool install --python 3.12 --force \
  "transformers[serving]" --with librosa
```

Run three terminals:

```bash
# Terminal 1 — simulator
.venv/bin/mjpython -m reachy_mini.daemon.app.main \
  --sim --scene empty --fastapi-host 127.0.0.1 --fastapi-port 8000

# Terminal 2 — local STT
transformers serve --host 127.0.0.1 --port 8766 \
  --force-model openai/whisper-small

# Terminal 3 — app and UI
.venv/bin/reachy-mini-conversation-app --media-backend local --ui
```

Open http://127.0.0.1:7860/. Space/Enter remains the keyboard fallback. For
Strategy B, change `LOCAL_CONTEXT_STRATEGY=activation`; activate by keyboard.

## 3–8. Measurements

| Metric | Evidence | Result |
|---|---|---:|
| Direct Gemini connection | Historical partial direct runs | 545.7–640.8 ms; not accepted as the final saved-log baseline |
| Direct conversation quality | User-observed long SDK LOCAL session | Responsive conversation and successful interruption; numeric first-audio/interruption samples not retained |
| Direct tool latency | Historical greeting-tool samples | 270.8/274.2 ms and 231.6/230.9 ms; not a final representative tool benchmark |
| Hybrid keyboard activation to connected | Accepted Phase 2 simulator/Gemini run | approximately 885 ms total, including 505.6 ms provider connection |
| Hybrid interruption flush | Accepted Phase 2 run | 1.0–1.3 ms local flush samples |
| Hybrid session duration | Accepted Phase 2 run | 167.511 s |
| First response audio | Provider instrumentation | not measured; event ordering can make a sample explicitly unavailable/reordered |
| Strategy A dinner context | Automated integration | correct ordered text injection; live latency/quality not measured |
| Strategy B dinner context | Automated integration | acknowledgment precedes local STT, which precedes provider construction; live latency not measured |
| Wake activation latency | Automated deterministic clock only | live microphone value not measured |
| Wake false positives / misses | — | not measured |
| Local command latency | Automated deterministic clock only | live simulator value not measured |
| Local STT CPU / process RSS | — | not measured |
| Local STT backend | Implemented protocol / planned Mac run | loopback OpenAI-compatible endpoint; Transformers Serve with `openai/whisper-small` is the first benchmark candidate, not yet validated live |

The direct Phase 1 saved-log rerun and all Phase 3–5 live measurements remain
required before this report can be marked complete.

Automated verification on 2026-08-29 passed the full 649-test repository suite,
Ruff, formatting, strict mypy, lock consistency, JavaScript syntax, and diff
checks. These results use fakes for cloud and local STT boundaries and do not
substitute for the live measurements above.

## 9. Proof ambient audio was not sent to cloud

Automated tests assert the send boundary, not only counters:

- ambient and Strategy A/B pre-activation frames never reach the fake provider;
- Strategy B's RAM snapshot is supplied only to the local STT fake;
- exact local commands never construct a provider;
- public/LAN/DNS STT targets and environment proxy forwarding are rejected;
- raw PCM is not logged or written to an application file.

The accepted five-minute Phase 2 live soak retained a full 20-second RAM ring
while `ambient_audio_bytes_sent_to_cloud`, cloud audio, and cloud video remained
zero before activation. Its final snapshot still reported zero ambient-cloud
bytes. Phase 3–5 must repeat this proof with the real local STT endpoint running.

## 10. Problems encountered

- Open-speaker Phase 0 testing caused acoustic feedback; headphones are the
  repeatable control because software echo cancellation is not established.
- macOS simulator rendering requires `mjpython` and a native arm64 environment.
- The SDK WebRTC reverse-audio chain failed with `not-negotiated (-4)`; accepted
  simulator conversations use the SDK LOCAL backend.
- Gemini input-transcript and audio events are independent, so first-audio
  latency is reported as unavailable/reordered when a defensible speech-end
  marker does not precede audio.
- The initial RMS VAD is amplitude-dependent and does not solve echo or noisy
  rooms. Its threshold must be measured with the actual microphone.
- A transcript-based wake word depends on local STT completing a segment. It is
  a V0 implementation and must be compared with an acoustic detector.
- MLX Audio's current STT server explicitly bridges inference through a file in
  `/tmp`, so it was rejected for this RAM-only ambient path. The selected
  Transformers candidate decodes uploaded bytes through `BytesIO`; the live run
  must still monitor temporary directories before accepting that guarantee.

## 11. Raspberry Pi 5 8GB assessment

Not yet assessable. No Phase 3 local-model CPU/RAM measurement exists, and the
first candidate backend has not been measured on the Pi. The core interfaces,
Transformers candidate, and RMS VAD are cross-platform, but this report will not claim Raspberry Pi feasibility
until a Pi-compatible STT model's known requirements are compared with measured
Mac and Pi workload data.

## 12. Recommendation

First run both dinner-demo strategies on the Mac with the same microphone and
local model, recording activation/context/connection/first-audio latency,
process CPU/RSS, transcript quality, wake false positives/misses, and local
command latency. Prefer Strategy A only if its always-on local cost and wake
quality are acceptable. Then benchmark a Pi-compatible VAD/STT backend through
the existing protocols before selecting production models or starting later
memory, Home Assistant, face recognition, heartbeat, or local-LLM work.
