---
title: Reachy Mini Conversation App
emoji: 🎤
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Talk with Reachy Mini!
suggested_storage: large
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Reachy Mini conversation app

Conversational app for the Reachy Mini robot combining realtime voice, vision, personality-aware tools, and choreographed motion.

![Reachy Mini Dance](docs/assets/reachy_mini_dance.gif)

## Table of contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Realtime provider selection](#realtime-provider-selection)
- [Running the app](#running-the-app)
- [LLM tools](#llm-tools-exposed-to-the-assistant)
- [Creating and adding tools](#creating-and-adding-tools)
- [Advanced features](#advanced-features)
- [Contributing](#contributing)
- [License](#license)

## Overview

- Realtime audio conversation through the selected backend. Hugging Face remains the default; Gemini Live and GPT Live are opt-in.
- Optional hybrid mode keeps audio processing local until activation, supplies recent locally derived context, and handles exact movement commands without cloud.
- Vision is handled by the realtime backend when the `camera` tool is used.
- Layered motion system queues primary moves (dances, emotions, goto poses, breathing) while blending speech-reactive wobble.
- Async tools integrate motion, camera capture, and MCP Tool Spaces. The optional web UI (`--ui`) manages conversations, personalities, tools, and settings.

## Architecture

The app connects the user, AI services, and robot hardware:

<p align="center">
  <img src="docs/assets/conversation_app_arch.svg" alt="Architecture Diagram" width="600"/>
</p>

## Installation

> [!IMPORTANT]
> Install [Reachy Mini's SDK](https://github.com/pollen-robotics/reachy_mini/) before using this app.<br>
> Windows support is currently experimental and has not been extensively tested. Use with caution.

<details open>
<summary>Using uv (recommended)</summary>

Set up with [uv](https://docs.astral.sh/uv/):

```bash
# macOS (Homebrew)
uv venv --python /opt/homebrew/bin/python3.12 .venv

# Linux / Windows (Python in PATH)
uv venv --python python3.12 .venv

source .venv/bin/activate
uv sync
```

Include dev dependencies:
```bash
uv sync --group dev
```

</details>

> [!NOTE]
> Run `uv sync --frozen` to install the exact dependency set from `uv.lock` without re-resolving versions.

<details>
<summary>Using pip</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Install dev dependencies:
```bash
pip install -e .[dev]                   # Development tools
```

</details>

## Configuration

The default setup uses the Hugging Face backend and does not require a Gemini API key.

Copy `.env.example` to a local, ignored `.env` when you want to select Gemini/GPT Live or
point Hugging Face at your own local endpoint. Never commit `.env` or put a
credential in the web UI.

At the initial project configuration load, values already present in the
process environment take precedence over `.env`; `.env` fills only unset
values. This keeps one-off commands such as
`HYBRID_RUNTIME_ENABLED=0 ...` effective while allowing unset values such as
`GEMINI_API_KEY` to remain in the ignored `.env`.

| Variable | Description |
|----------|-------------|
| `REALTIME_PROVIDER` | Realtime provider: `huggingface` (default), `gemini`, or `openai_live`. Leading/trailing whitespace and case are normalized; another value fails closed with a configuration error. |
| `GEMINI_API_KEY` | Required only when `REALTIME_PROVIDER=gemini`. Configure it in the local app environment; it is not accepted by settings RPC and is never logged. |
| `GEMINI_LIVE_MODEL` | Optional Gemini Live model. Defaults to `gemini-3.1-flash-live-preview`. |
| `GEMINI_VOICE` | Optional Gemini voice. Defaults to `Puck`; it must be a voice supported by the selected Gemini Live model. |
| `OPENAI_API_KEY` | Required for `openai_live`; local environment only, never accepted or displayed by settings. |
| `OPENAI_LIVE_MODEL` | Speech model, default `gpt-live-1`. |
| `OPENAI_LIVE_BACKEND_MODEL` | Delegated tool/image model, default `gpt-5.6-luna`. |
| `OPENAI_LIVE_VOICE` | GPT Live voice, default `marin`. |
| `REALTIME_TRANSCRIPTION_LANGUAGE` | Optional input transcription language for the realtime backend. Defaults to `en`; set to a backend-supported code such as `zh` for Chinese. |
| `HF_REALTIME_CONNECTION_MODE` | Hugging Face connection selector: `deployed` uses the built-in Hugging Face server; `local` uses `HF_REALTIME_WS_URL`. Defaults to `deployed`. |
| `HF_REALTIME_WS_URL` | Direct websocket endpoint for your own Hugging Face backend. Accepts either a base URL like `ws://127.0.0.1:8765/v1` or the full websocket URL `ws://127.0.0.1:8765/v1/realtime`. Used when `HF_REALTIME_CONNECTION_MODE=local`. |
| `HF_TOKEN` | Optional token for Hugging Face access. Local endpoints receive only this explicitly configured token. |
| `REACHY_MINI_APP_TIMEOUT_MINUTES` | Minutes of inactivity before Reachy goes to sleep and the app stops. Defaults to `1440` (one day); set to `0` to disable. |
| `HYBRID_RUNTIME_ENABLED` | Opt into local ambient mode. Defaults to `0`, preserving the direct provider path. |
| `HYBRID_CLOUD_INACTIVITY_SECONDS` | Seconds without cloud conversation activity before the provider closes and ambient mode resumes. Defaults to `60`; `0` disables this close. |
| `HYBRID_AMBIENT_BUFFER_SECONDS` | RAM-only ambient PCM ring duration, capped at 20 seconds and defaulting to `20`. |
| `LOCAL_CONTEXT_WINDOW_SECONDS` | Seconds of finalized local transcript retained as text-only context. Independent from the raw PCM ring and defaults to `60`. |
| `LOCAL_CONTEXT_STRATEGY` | `continuous` (default) transcribes completed local speech segments and enables transcript wake/local commands; `activation` transcribes the RAM snapshot only after keyboard activation. |
| `LOCAL_STT_BASE_URL` | Optional OpenAI-compatible transcription base URL. Only explicit loopback hosts are accepted; when unset, hybrid mode retains keyboard activation and the RAM ring without local STT. |
| `LOCAL_STT_MODEL` | Model name sent to the local endpoint. Defaults to `whisper-1`. |
| `LOCAL_STT_TIMEOUT_SECONDS` | Per-request local STT timeout. Defaults to `5`. |
| `LOCAL_VAD_RMS_THRESHOLD` | PCM RMS speech threshold for the initial lightweight VAD. Defaults to `500`. |
| `LOCAL_VAD_SILENCE_SECONDS` | Silence needed to close a local speech segment. Defaults to `0.6`. |
| `LOCAL_ACOUSTIC_WAKE_ENABLED` | `0` (off). Opt into the supervised official33 acoustic trial; requires hybrid mode. Replaces transcript wake while enabled. |
| `LOCAL_ACOUSTIC_WAKE_MODEL_DIR` | Private directory containing the frozen official33 ONNX model and both verified backbone files. No automatic downloads. |
| `LOCAL_WAKE_WORD` | Local transcript wake phrase. Defaults to `Reachy`; matching remains exact and caller-configured. |
| `LOCAL_WAKE_COOLDOWN_SECONDS` | Duplicate wake suppression window. Defaults to `2`. |
| `LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS` | Seconds to wait for one next final local command after a wake-only transcript. Defaults to `5`, with a `15`-second maximum; timeout opens the normal cloud path. |
| `LOCAL_CONTEXT_MAX_CHARS` | Maximum rolling context sent as text when a cloud session opens. Defaults to `2000`; valid range is 128–8000. |
| `LOCAL_TRANSCRIPT_PREVIEW_ENABLED` | Opt-in browser diagnostic preview of the bounded local transcript. Defaults to `0`; preview retrieval/display adds no cloud traffic and is never included in `conversation.status`, logs, browser storage, or disk. Normal activation may still send bounded derived context to the provider. |
| `AUDIO_OUTPUT_BACKEND` | `sdk` (default) uses Reachy Mini playback. `gstreamer` explicitly selects the experimental app-owned speaker path used to work around the macOS simulator SDK mixer crash. Invalid values stop startup. |

### Realtime provider selection

Think of `REALTIME_PROVIDER` as the one switch that chooses which realtime
conversation handler receives the local microphone stream. With no setting, the
existing Hugging Face path is selected. To try Gemini Live, set the selector and
the required key in the ignored local `.env` file:

```env
REALTIME_PROVIDER=gemini
GEMINI_API_KEY=<configure locally; never commit>
# GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
# GEMINI_VOICE=Puck
```

To use GPT Live, set `REALTIME_PROVIDER=openai_live` and add `OPENAI_API_KEY`
to the ignored `.env`. GPT Live handles speech; Luna executes existing tools
(including Spotify), searches the web when needed, and receives requested camera images.
The app resamples microphone input to 24 kHz; GPT Live returns 24 kHz PCM. Standby remains local
when hybrid mode is enabled. Gemini stays available by selecting `gemini`.
See [GPT Live provider](docs/features/openai-live-provider.md) for validation
status, session limits and the separate voice/backend billing boundary.

The Gemini Phase 1 audio path is direct: 16 kHz microphone PCM → Gemini Live → 24 kHz
assistant PCM → the generic local resampler already used by `LocalStream` → the
Reachy Mini speaker. The provider choice does not create a second tool or
movement loop. Existing tools, profiles, memory, simulated movement, speech
movement, transcripts, interruptions, and reconnect handling remain shared.
The `camera` tool captures a frame only when the assistant requests that tool;
camera frames are not sent merely because a camera is available.

Gemini Live sessions also enable Google's managed `google_search` capability for
current web information. It is sent alongside the profile's application
function tools and handled server-side by Gemini; it is not a per-profile Tool
access setting. The dedicated weather MCP tool remains available for structured
weather results, while flight status and fare lookups still need a dedicated
flight-data integration.

In the Settings view, Connection selects the provider. Hugging Face endpoint
controls are shown only for Hugging Face. Gemini and OpenAI keys are environment-only, so
the page cannot enter or display it. If the selected provider is missing its
required target, the app keeps the settings surface available and reports what
is missing instead of starting a backend with incomplete configuration. Saving
a provider change writes the selector to the instance `.env` and requests an
in-process backend rebuild/reconnect when the runtime can do so. Profile and
voice changes normally use the same reconnect path. GPT Live voice changes are
saved for its next session; the current session keeps its original voice.

The Gemini adapter, provider-neutral runtime wiring, shared resampler, bounded
daemon-capture worker, and automated tests have passed the repository gate. The
latest WebRTC attempt first hit a producer-startup race (`KeyError`) until the
producer registered. A retry selected WebRTC and initialized the bidirectional
client, reaching `connection_ready_ms=640.8` and greeting-tool timings of
231.6 ms and 230.9 ms, but the SDK reverse-audio send queue then failed with
`not-negotiated (-4)`. No complete WebRTC conversation, first-audio, or interruption
benchmark was obtained. The current Reachy SDK main still contains the same
send-chain implementation, so this workaround is not validated and should not
be presented as expected to work on this Mac. Disk-pressure cleanup left 19 GiB
free, and the Python runtime was rebuilt for arm64. The earlier 545.7 ms and
270.8/274.2 ms samples remain historical only.
The Phase 1 root gate passed 392 tests in 7.64 s; Ruff was clean, formatting left
81 files unchanged, and strict mypy was clean across 46 source files.

The accepted direct Phase 1 headphone run connected Gemini in 637.5 ms, sounded
smooth for five turns, executed visible head movement, and flushed two audible
interruptions in 1.4 ms and 3.8 ms. Gemini emitted no usable speech-end marker,
so exact first-audio latency remains unavailable rather than fabricated. A
controlled Mac-speaker run immediately fed assistant output into the microphone
and produced six self-triggered interruption flushes in about 18 seconds while
the user stayed silent. The process remained live; this is acoustic feedback,
not a freeze. Use headphones for simulator conversation.

The Phase 0 speaker echo loop remains a Mac speaker/headphone benchmark item,
separate from the successful Phase 1 LOCAL conversation. Use headphones for
repeatable testing. WebRTC remains unvalidated on this Mac; use the SDK LOCAL
media backend for the simulator path. Quantitative Phase 1 first-audio evidence
and remaining quantitative Phase 3–5 measurements remain pending. Both live
English dinner-context strategies passed with zero ambient cloud bytes;
Strategy B added about 7.2 seconds before Gemini was ready, so Strategy A is the
current latency preference. Multilingual local STT remains separate follow-up
scope. The source diagram in
[`docs/scheme.mmd`](docs/scheme.mmd) includes the provider choice; the generated
SVG is not hand-edited and still needs regeneration.

### Hybrid ambient mode

Set `HYBRID_RUNTIME_ENABLED=1` to keep the local media loop running while the
selected provider is closed. The runtime starts in `LOCAL_AMBIENT`; microphone
frames are copied into a bounded mono-int16 RAM ring and passed to a local
processing boundary. No ambient bytes or camera frames are sent to a cloud
provider, and no raw audio is written to disk or logged. The ring is never
replayed when a session opens.

Press Space or Enter in the browser conversation view to activate. With a local
STT endpoint and `LOCAL_CONTEXT_STRATEGY=continuous`, saying “Reachy” also
activates from locally derived transcript text. The runtime briefly enters
`LOCAL_COMMAND` and acknowledges physically before any cloud setup. Exact
`stop`, `be quiet`, `look left`, `look right`, and `look at me` requests run
through the existing movement/tool boundaries without opening cloud. Anything
uncertain escalates to one fresh selected provider handler in
`CLOUD_CONVERSATION`. Provider setup failure, cancellation, or configured
conversational inactivity returns to ambient mode.

If local STT emits the wake and command as two final segments, Reachy waits up
to `LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS` (default five seconds, maximum fifteen)
for exactly one next final segment. An exact command stays local; an uncertain
follow-up is sent as the addressed cloud turn, and a timeout opens the normal
cloud path. Context seeding uses an exclusive wake-sequence fence, so later
ambient finals cannot be included as background history.
The wait is bounded and is released during shutdown or cancellation.

For an addressed question captured before cloud startup, the rolling transcript
is added as untrusted background and the bounded wake-stripped question is sent
as the first user text turn after provider readiness. Raw pre-wake audio is not
replayed or uploaded.

For a short-lived local STT diagnostic, set `LOCAL_TRANSCRIPT_PREVIEW_ENABLED=1`.
The Talk view then shows the current bounded continuous transcript in a small
diagnostic panel and refreshes it through the separate
loopback-only `GET /api/transcript-preview` endpoint with `Cache-Control:
no-store`. The default is off; the text is never included in
`conversation.status`, logs, browser storage, or disk, and preview retrieval
itself adds no cloud traffic. Normal activation can still send bounded derived
context to the selected provider. Non-loopback clients receive `403`; the
panel reports unavailable when hybrid local processing is not running or has
closed.

Provider readiness can take time after activation. Postactivation frames are
held in a bounded 20-second FIFO until the provider is ready; overflow drops
oldest frames and increments the diagnostic `postactivation_frames_dropped`.
Cloud inactivity starts once the provider reports connected and pauses while
the provider reports user speech; ambient capture alone does not keep the cloud
session alive. GPT Live ignores quiet output packets for inactivity, while
non-whitespace transcripts and louder output refresh the timer.

Keyboard activation remains available through the browser UI and the
`conversation.activate` JSON-RPC method; headless runs do not start a blocking
stdin listener. Ambient PCM adopts the first sample rate it receives and
rejects mixed-rate frames rather than resampling them.

The status RPC exposes `ambient_audio_seconds_buffered`,
`ambient_audio_bytes_sent_to_cloud`, `cloud_audio_bytes_sent`,
`cloud_video_frames_sent`, and accumulated `gemini_session_duration`. Counters
are updated at successful provider send boundaries; they do not replace the
privacy tests. Status also exposes bounded context, wake, local-command,
activation-latency, and postactivation-drop diagnostics without transcript or
audio payloads.

The current automated integration gate passes 726 tests with one explicitly
opt-in native-audio test skipped by default, plus Ruff, formatting, strict
mypy, lock consistency, JavaScript syntax, and diff checks. Live microphone,
local-model, wake-quality, dinner-demo, audible-cadence, and CPU/RAM acceptance
measurements remain pending.

#### Local STT benchmark server

The app does not install a speech model. It talks to a replaceable local
OpenAI-compatible endpoint, keeping model code outside the core application.
For the first cross-platform benchmark, Hugging Face Transformers provides the
required local transcription endpoint:

```bash
# One-time install outside this project's virtual environment.
/opt/homebrew/bin/uv tool install --python 3.12 --force \
  "transformers[serving]" --with librosa --with python-multipart

# Local STT terminal; the Reachy daemon already uses port 8000.
transformers serve --host 127.0.0.1 --port 8766 \
  openai/whisper-small.en
```

Then add this to the ignored `.env`:

```env
HYBRID_RUNTIME_ENABLED=1
LOCAL_CONTEXT_STRATEGY=continuous
LOCAL_STT_BASE_URL=http://127.0.0.1:8766/v1
LOCAL_STT_MODEL=openai/whisper-small.en
LOCAL_STT_TIMEOUT_SECONDS=15
LOCAL_VAD_RMS_THRESHOLD=100
REACHY_MINI_CUSTOM_PROFILE=household_companion
```

The first transcription downloads/loads the selected model and can be slower.
The adapter accepts only `localhost`, `127.0.0.0/8`, or `::1`, disables proxy
environment discovery, constructs at most 20 seconds of WAV data in memory,
and never adds an API key. The current Transformers transcription handler reads
the upload as bytes and decodes it from `BytesIO`; the live acceptance protocol
must still monitor temporary directories before confirming the end-to-end
RAM-only guarantee. MLX Audio's current server is not recommended here because
its STT execution path writes a temporary audio file. Strategy A (`continuous`)
is the full dinner-demo path. Strategy B
(`activation`) keeps only the raw RAM ring until Space/Enter is pressed; voice
wake and local voice commands are intentionally unavailable in that strategy.
The English-only model and calibrated threshold above are the accepted settings
for the current Mac microphone. They are not the final household backend:
Spanish, English, Portuguese, and Italian accuracy require a separate
multilingual benchmark before production selection.
See [`docs/features/local-audio-backends.md`](docs/features/local-audio-backends.md)
for its contracts and limitations.

#### Optional acoustic wake benchmark

The fixture-only `wake-benchmark` command is separate from the production
runtime. Install it with `uv sync --group wake-benchmark`, then pass a
caller-owned extracted Spanish sherpa-onnx model directory and one or more
bounded WAV/raw PCM16 fixtures to `wake-benchmark --model-dir ... --positive
... --negative ...`. It pins `sherpa-onnx==1.13.6` with its required
`sherpa-onnx-core==1.13.6` wheel and `sentencepiece==0.2.2` plus
`pypinyin==0.55.0`, does not download or bundle
weights, has no network, simulator, robot, or microphone mode, and emits
metadata without paths, samples, result tags, or transcripts. Its offline
fixture-start-to-result timing is not acoustic activation latency. The default
acoustic keyword is `Reachy`; repeat `--keyword` to benchmark the bounded
candidate spellings `Reachy`, `Richie`, `Ritchie`, `Richi`, and `Ricci`.
Candidates are benchmark inputs only, not automatic production aliases, and
all exact acoustic detections are reported with the metadata-only canonical
tag `@reachy`. See
[`docs/features/wake-word.md`](docs/features/wake-word.md) for the complete
command and the deterministic-only acceptance status.

### Hugging Face Connection Modes

Use the built-in Hugging Face server through the app-managed Space proxy. This is the default for a new install; set it explicitly only when you want to switch back from a saved local endpoint:

```env
HF_REALTIME_CONNECTION_MODE=deployed
```

Deployed session allocation falls back to cached `hf auth login` credentials and reports the daemon-provided hardware ID when available. Cached credentials and the hardware ID are not sent to local endpoints.

Run your own realtime voice backend using [speech-to-speech](https://github.com/huggingface/speech-to-speech) on the same machine as the conversation app:

```env
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime
```

Run your own Hugging Face backend on your laptop and connect to it from Reachy Mini Wireless over the same Wi-Fi network:

```env
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://<your-laptop-lan-ip>:8765/v1/realtime
```

For that LAN setup, make sure the backend listens on an address reachable from the robot, not only on `127.0.0.1`.

If the backend stays bound to loopback on your laptop, you can forward it into the robot over SSH instead:

```bash
ssh -N -R 8765:127.0.0.1:8765 <robot-user>@<robot-host>
```

Then set this on the robot:

```env
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime
```

In the web UI's Settings view, the Connection section lets you choose either the built-in server or a local `host:port` target. The UI writes `HF_REALTIME_CONNECTION_MODE` for you, and the local path writes `HF_REALTIME_WS_URL` with a default of `localhost:8765`.

## Running the app

Activate your virtual environment, then launch:

```bash
reachy-mini-conversation-app
```

> [!TIP]
> Make sure the Reachy Mini daemon is running before launching the app. If you see a `TimeoutError`, it means the daemon isn't started. See [Reachy Mini's SDK](https://github.com/pollen-robotics/reachy_mini/) for setup instructions.

The app runs in console mode. Add `--ui` to serve the web interface at http://127.0.0.1:7860/.

### Supervised Reachy wake trial

Official33 is the current simulator MVP: fewer false wakes than our parallel
pair, with known missed calls. See the [retained model catalog](docs/plans/reachy-wake-model-catalog.md)
for exact artifacts and results. No model is bundled or approved for production.
The private bundle has been prepared on this Mac at
`~/Documents/Reachy/private-wakeword/simulator-official33`.

```bash
uv sync --frozen --group dev --group sim --group wake-runtime --group wake-benchmark
```

Start the simulator as described below (keep the Mac unlocked). Then launch:

```bash
REALTIME_PROVIDER=gemini HYBRID_RUNTIME_ENABLED=1 \
LOCAL_ACOUSTIC_WAKE_ENABLED=1 \
LOCAL_ACOUSTIC_WAKE_MODEL_DIR="$HOME/Documents/Reachy/private-wakeword/simulator-official33" \
LOCAL_STT_BASE_URL= AUDIO_OUTPUT_BACKEND=gstreamer \
HYBRID_CLOUD_INACTIVITY_SECONDS=30 \
.venv/bin/reachy-mini-conversation-app --media-backend local --ui
```

Use headphones. Say **Reachy**, wait for the acknowledgment nod, then speak your
request. The wake frame and earlier audio stay local; follow-on live speech uses
the normal cloud conversation. This trial does not recover a command spoken
inside the same microphone frame as the wake. The cloud closes after 30 seconds
of conversational inactivity and listening resumes. Manual console/browser
activation remains available. Stop with Ctrl-C; set `LOCAL_ACOUSTIC_WAKE_ENABLED=0`
to launch with acoustic wake disabled. These process settings do not modify `.env`.

This acoustic trial opens the normal conversation directly. The existing
transcript-based exact local-command/follow-up flow remains available with
acoustic wake disabled and continuous local STT configured. The trial command
leaves local STT off to isolate the acoustic detector.

If the graphical viewer is unavailable, add `--headless` to the daemon command.
Do **not** add `--no-media` for microphone testing: the SDK then disables client
microphone initialization too. Headless mode has no visible simulated robot.
The 2026-09-09 graphical attempt crashed in GLFW display setup while the Mac was
locked; headless model-to-runtime-to-SDK testing passed. A human-spoken graphical
wake/conversation session and Pi performance remain unverified.

### Simulator development

Use two terminals: start the Reachy Mini simulator first, then start this app.
On macOS, use the virtual environment's `mjpython`; the ordinary
`reachy-mini-daemon --sim` command cannot launch the MuJoCo passive viewer on
macOS because `launch_passive` requires `mjpython`:

```bash
# Install the optional simulator dependencies, including MuJoCo.
uv sync --frozen --group dev --group sim

# Terminal 1 — macOS
.venv/bin/mjpython -m reachy_mini.daemon.app.main \
  --sim --scene empty --fastapi-host 127.0.0.1 --fastapi-port 8000

# Terminal 2
# GEMINI_API_KEY remains in the ignored .env; select Gemini explicitly here.
REALTIME_PROVIDER=gemini .venv/bin/reachy-mini-conversation-app --media-backend local
```

This is the console-only benchmark command. Add `--ui` only when port 7860 is
free: `REALTIME_PROVIDER=gemini .venv/bin/reachy-mini-conversation-app --media-backend local --ui`.

The optional `sim` group supplies the locked MuJoCo 3.3.0 dependency. It is not
required for ordinary non-simulator runs.

The SDK LOCAL backend produced the earlier accepted simulator conversations on
this Mac, but three later cloud-activation runs crashed in the native SDK
GStreamer `audiomixer` zero-frame path. For the current macOS simulator test,
explicitly route assistant sound around that mixer input:

```bash
AUDIO_OUTPUT_BACKEND=gstreamer REALTIME_PROVIDER=gemini \
  .venv/bin/reachy-mini-conversation-app --media-backend local --ui
```

This is an experimental simulator containment path, not an upstream SDK fix.
It keeps SDK microphone capture, providers, tools, and normal movement, but the
SDK audio-reactive wobbler does not receive assistant PCM. The SDK mixer still
exists for capture, so repeated live validation is required before the crash is
considered contained. Unset the variable to restore the normal SDK output path.

A separate WebRTC attempt reached setup and then failed its reverse-audio
send chain with `not-negotiated (-4)`, so WebRTC is not an accepted workaround
here. Quantitative first-response-audio benchmarking is still pending.

On Linux or Windows, use the normal daemon CLI in Terminal 1:

```bash
reachy-mini-daemon --sim
```

Then run `reachy-mini-conversation-app --ui` in Terminal 2. Do not pass
`--headless` when rendered camera frames are needed, and do not pass
`--no-media` when testing audio or camera capture. The first macOS run uses the
default microphone and speaker and may require macOS microphone/audio
permissions. The app's `--no-camera` option disables only its camera tool; it
is not a daemon capture or privacy switch.

### CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--no-camera` | `False` | Run without camera capture. |
| `--ui` | `False` | Serve the web UI at http://127.0.0.1:7860/, in addition to console mode. |
| `--robot-name` | `None` | Optional. Connect to a specific robot by name when running multiple daemons on the same subnet. See [Multiple robots on the same subnet](#advanced-features). |
| `--media-backend` | `default` | Primary app choices: `default`, `local`, `webrtc`, or `no_media`. The value is used only when this process constructs the SDK robot. `no_media` disables usable conversation audio and is for headless/non-audio diagnostics, not this benchmark. |
| `--debug` | `False` | Enable verbose logging for troubleshooting. |

### Examples

```bash
# Audio-only conversation (no camera)
reachy-mini-conversation-app --no-camera

# Launch with the minimal web UI for personality/mic/settings control
reachy-mini-conversation-app --ui
```

## LLM tools exposed to the assistant

The default profile exposes these tools. Use Tools → Tool access to customize any profile.
Every bundled profile enables `head_tracking` by default; users can still disable it per personality.

| Tool | Action | Dependencies |
|------|--------|--------------|
| `dance` | Queue a dance from `reachy_mini_dances_library`. | Core install only. |
| `stop_dance` | Clear queued dances. | Core install only. |
| `play_emotion` | Play a recorded emotion clip via Hugging Face datasets. | Core install only. Uses the default open emotions dataset: [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library). |
| `stop_emotion` | Clear queued emotions. | Core install only. |
| `camera` | Capture the latest camera frame and analyze it with the selected realtime backend. | Core install only. Requires the camera (disable with `--no-camera`). |
| `idle_do_nothing` | Explicitly remain idle during an idle turn. Not intended for normal conversation turns. | Core install only. |
| `move_head` | Queue a head pose change (left/right/up/down/front). | Core install only. |
| `head_tracking` | Follow the user's face with the head, or stop following. | Core install only. Requires a daemon with the `vision` extra and a camera. |
| `go_to_sleep` | Run Reachy's sleep movement and stop the current app after an explicit user request. | Core install only. |
| `sweep_look` | Sweep Reachy's head left, right, and back to center. | Shared tool, enabled by default in the default profile. |
| `remember` | Save one short, stable fact about the user for future sessions. | Core install only. Stored in the app instance data directory. |
| `forget` | Remove a saved memory fact by matching a short query. | Core install only. |
| `pollen_robotics_reachy_mini_search_tool__search_web` | Search the web and return a short list of results. | Preinstalled MCP Space: `pollen-robotics/reachy-mini-search-tool`. |
| `pollen_robotics_reachy_mini_weather_tool__get_weather` | Report today's weather for a place: current conditions, high and low temperature, and rain chance. | Preinstalled MCP Space: `pollen-robotics/reachy-mini-weather-tool`. |
| `pollen_robotics_reachy_mini_time_tool__get_time` | Report the current time for a timezone or the user's local time, or the difference between two timezones. | Preinstalled MCP Space: `pollen-robotics/reachy-mini-time-tool`. |

When `REALTIME_PROVIDER=gemini`, the provider additionally enables Google's
managed `google_search` tool for every Live session. It is separate from the
profile-managed MCP tools above and is unavailable on the Hugging Face backend.

> [!NOTE]
> `remember`/`forget` facts are stored in `memory.v1.json` inside the app's instance data directory (`~/.local/share/reachy_mini_conversation_app/` by default, or the instance path used by the desktop launcher). `forget` only removes facts matched by query. To reset all remembered facts, delete this file.

## Creating and adding tools

Tools can run locally as Python code or remotely in an MCP-compatible Hugging Face Space. Keep robot, camera, and local-data operations in local tools. A Space is a better fit for shareable, stateless services such as search and external API lookups.

### Local tools

Create one Python module per tool, with the file name matching the tool's unique `name`. See [`idle_do_nothing.py`](src/reachy_mini_conversation_app/tools/idle_do_nothing.py) for a minimal implementation.

Each tool subclasses `Tool` and defines `name`, a model-facing `description`, an object-shaped JSON Schema in `parameters_schema`, and an async `__call__` method. Use `ToolDependencies` for runtime services, and set `needs_response = False` for actions that should not trigger a spoken follow-up. Catch expected operational failures, log them with the module logger, and return `{"error": "..."}` so the conversation can continue.

Restart the app after adding the module. Use Tools → Tool access to enable it for a personality, or add its name to that profile's `default_tools` in `profile.md`. See [External profiles and tools](#external-profiles-and-tools) for external directories and autoload behavior.

### Hugging Face Space tools

To publish a remote tool, create a Gradio Space, expose its API as MCP with `mcp_server=True`, and give each function clear type hints and docstrings. Verify that `https://<space-subdomain>.hf.space/gradio_api/mcp/schema` lists the expected tools before installing the Space.

Use the maintained [weather](https://huggingface.co/spaces/pollen-robotics/reachy-mini-weather-tool), [time](https://huggingface.co/spaces/pollen-robotics/reachy-mini-time-tool), and [search](https://huggingface.co/spaces/pollen-robotics/reachy-mini-search-tool) Spaces as examples. See Gradio's [MCP server guide](https://www.gradio.app/guides/building-mcp-server-with-gradio) for additional publishing guidance and [Installing Hugging Face Space tools](#installing-hugging-face-space-tools) for this app's installation steps.

## Advanced features

Built-in motion content is published as open Hugging Face datasets:

- Emotions: [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library)
- Dances: [`pollen-robotics/reachy-mini-dances-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library)

<details>
<summary>Custom profiles</summary>

Create custom profiles with dedicated instructions and per-profile tool access.

Select and save a startup profile in the UI. The choice is stored in `startup_settings.json`. Before one is saved, `REACHY_MINI_CUSTOM_PROFILE=<name>` can select `profiles/<name>/`; otherwise the app uses `default`.

Every profile directory contains one strict schema-version-1 `profile.md`. TOML metadata is enclosed by `+++`; the remaining Markdown body is the realtime assistant prompt:

```markdown
+++
schema_version = 1
voice = "Aiden"
greeting = "Greet me warmly in one sentence, in character, and vary the wording each time."
hidden = false
default_tools = [
  "dance",
  "camera",
  "sweep_look",
]
+++

## Identity

You are a concise, friendly robot guide.
```

`schema_version`, `default_tools`, and a non-empty Markdown body are required. `voice`, `greeting`, and `hidden` are optional. Set `hidden = true` to omit a profile from the UI. An empty `default_tools` list is valid and inherits nothing.

`default_tools` is the authored baseline. Tools → Tool access stores overrides in instance-local `profile_toolsets.json` without changing bundled profiles. Restoring defaults removes the override. Active-profile changes reconnect the conversation; other changes apply when selected.

Profile directories are data-only. Python tool implementations belong in `src/reachy_mini_conversation_app/tools/`, or in `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY` for external tools. Each enabled tool ID must resolve to a shared tool, an external tool, or a tool from an installed Hugging Face Space.

See [Creating and adding tools](#creating-and-adding-tools) for the local tool interface and a maintained example.

To manage personalities in the UI:

With `--ui`, Home lists the available profiles and the built-in default:

- Tap a card to apply that personality and start talking.
- Tap "Manage tools" on a saved personality to open its tool access directly.
- Tap "Custom" to create a personality with a name, instructions, and optional greeting. It inherits the default tools, which can be changed under "Manage tools". Managed instances store it at `user_personalities/<name>/profile.md`; standalone runs use `external_content/user_personalities/<name>/profile.md`.

Switching a personality reloads its prompt and effective tools through a quick backend reconnect. Editing `profile.md` directly requires re-selecting the profile or restarting the app.

</details>

<details>
<summary>Locked profile mode</summary>

To create a locked variant of the app that cannot switch profiles, edit `src/reachy_mini_conversation_app/config.py` and set the `LOCKED_PROFILE` constant to the desired profile name:
```python
LOCKED_PROFILE: str | None = "mars_rover"  # Lock to this profile
```
When set, the app ignores saved startup settings, `REACHY_MINI_CUSTOM_PROFILE`, and UI selection. The UI marks the profile as locked and disables editing.

</details>

<a id="external-profiles-and-tools"></a>

<details>
<summary>External profiles and tools</summary>

You can extend the app with profiles/tools stored outside the repository defaults.

- Core profiles are under `profiles/`.
- Core tools are under `src/reachy_mini_conversation_app/tools/`.

Recommended layout:

```text
external_content/
├── external_profiles/
│   └── my_profile/
│       └── profile.md
├── external_tools/
│   └── my_custom_tool.py
├── user_personalities/
│   └── my_custom_profile/
│       └── profile.md
├── installed_tool_spaces.json
└── profile_toolsets.json
```

Environment variables:

Set these values in your `.env` when you want env-driven external profile/tool selection:

```env
# Optional fallback/manual profile selector:
REACHY_MINI_CUSTOM_PROFILE=my_profile
REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY=./external_content/external_profiles
REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY=./external_content/external_tools
# Optional convenience mode:
# AUTOLOAD_EXTERNAL_TOOLS=1
```

Loading rules:

- Profiles: each directory requires a schema-version-1 `profile.md` with explicit `default_tools`; there is no cross-profile fallback.
- Default mode: enabled IDs must resolve to a shared, external, or installed Tool Space tool.
- Autoload: `AUTOLOAD_EXTERNAL_TOOLS=1` adds every valid `*.py` module from `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`.
- Web UI: Tools → Tool access enables external modules per profile; it does not upload or edit Python.
- Separation: profile directories contain data only; external Python belongs in `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`.
- Tool names: every loaded class needs a unique `Tool.name`; duplicates fail fast.

</details>

<a id="installing-hugging-face-space-tools"></a>

<details>
<summary>Installing Hugging Face Space tools</summary>

You can install MCP-compatible Hugging Face Spaces as remote tool sources for this app. Private Spaces work too, as long as `HF_TOKEN` is set (or you have run `hf auth login`) for an account that can access them. To publish a new Space, follow [Creating and adding tools](#hugging-face-space-tools).

Tools → Tool Spaces installs or refreshes a global source. Its tools then appear under Tools → Tool access for per-profile selection. Removing a Space removes its tools from every profile. Active-profile changes reconnect the conversation; other changes apply when selected.

The app accepts Hugging Face Spaces exposing the standard `/gradio_api/mcp/` endpoint, not arbitrary MCP URLs. Installation discovers the Space's tools and assigns namespaced local IDs, so do not guess or hard-code those IDs beforehand.

```bash
# install + enable in active profile
reachy-mini-conversation-app tool-spaces add <owner/space-name>

# enable in a specific profile
reachy-mini-conversation-app tool-spaces add <owner/space-name> --profile NAME

# install without enabling
reachy-mini-conversation-app tool-spaces add <owner/space-name> --install-only

# list installed spaces
reachy-mini-conversation-app tool-spaces list

# remove an installed space
reachy-mini-conversation-app tool-spaces remove owner/space-name
```

Bundled Pollen Spaces use static specs and are enabled by the default profile. Custom Spaces are validated through the Hugging Face Hub; HF tokens are sent only to private Spaces. Tool metadata is cached in:

- `installed_tool_spaces.json` in the managed app instance directory
- `external_content/installed_tool_spaces.json` in terminal mode

Startup and profile switching read this cache without discovery or MCP probing. Network access occurs only during install, refresh, or remote tool calls. Per-profile access is stored in `profile_toolsets.json` beside the manifest, or under `external_content/` in terminal mode.

Recommended tags for discoverability on Hugging Face:

- `reachy-mini-tool`
- `mcp`

Tags are advisory; installation still requires successful MCP validation.

> [!NOTE]
> Preinstalled Pollen Spaces can be removed like any other (`tool-spaces remove pollen-robotics/reachy-mini-weather-tool`). To restore access, reinstall the Space and restore or update the relevant profile under "Tool access".

</details>

<details>
<summary>Multiple robots on the same subnet</summary>

If you run multiple Reachy Mini daemons on the same network, use:

```bash
reachy-mini-conversation-app --robot-name <name>
```

`<name>` must match the daemon's `--robot-name` value so the app connects to the correct robot.

</details>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and [`AGENTS.md`](AGENTS.md) for coding-agent standards.

## License

Apache 2.0

### Spotify on the companion

The household profile includes optional `spotify_control`: ask to resume Spotify,
pause, skip, report what is playing, search and play tracks/artists/albums/playlists/
podcast episodes, or append a track/episode to the queue. Queue does not guarantee
immediate next playback.
Open Spotify on the configured computer and start a song there first, then test
with headphones using the normal headless simulator command. Restart the app
after setup to load its new tool selection and environment.

Set `SPOTIFY_CREDENTIALS_FILE` to a private OAuth credential JSON outside this
repository and `SPOTIFY_DEVICE_ID` to the authorized computer's exact Connect ID.
Both are already configured in this Mac's ignored `.env`. No other playback device
is selected automatically. Remove those settings or disable the profile tool to
turn the integration off; revoke Reachy Home in Spotify account settings to
remove account access. See [Spotify control](docs/features/spotify-control.md)
for authentication, privacy, limitations and tests.
