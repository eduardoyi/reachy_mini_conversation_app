# Reachy Mini Lite hardware bring-up

- Status: `approved` — ready to execute when the robot arrives
- Owner: Reachy project
- Created: 2026-10-10
- Last updated: 2026-10-10
- Scope: first power-on through re-tuning of audio, wake and hybrid runtime on a
  USB-tethered Reachy Mini Lite driven from the existing Mac checkout

## Goal

Move the simulator-accepted companion onto the physical Lite in small,
observable steps: motion first, then robot audio, then a plain conversation,
then the hybrid runtime and acoustic wake. Each step must pass before the next
adds risk. Re-measure every threshold that was tuned on the Mac microphone
instead of assuming simulator results carry over.

Non-goals: Wireless/Raspberry Pi deployment, new wake training, new providers,
upstream merges, or changing privacy contracts. Pi measurements remain out of
scope because the Lite runs the app on the Mac.

## Assumptions and known facts

Verified in code or SDK source on 2026-10-10:

- The app and its SDK client run on the Mac; the daemon runs locally (normally
  started by the Reachy Mini Control desktop app). `main.py` builds
  `ReachyMini(media_backend=...)` without a host, so auto mode tries
  `localhost` first. A running simulator daemon would therefore win.
- `--media-backend local` is correct for the Lite. The SDK only enables software
  WebRTC AEC when no Reachy audio card is found; on hardware it relies on the
  XVF3800 on-board AEC, so Mac echo findings do not transfer.
- On macOS with the LOCAL backend, `console.py` skips native `clear_player()`
  unless `MACOS_LOCAL_AUDIO_FLUSH_ENABLED=1`, which is only safe with the
  out-of-tree SDK flush candidate (see
  [audio dropout comparison](audio-dropout-comparison.md)). SDK 1.12.0rc1 still
  flushes only the player `appsrc`, so barge-in will not cut buffered speaker
  audio on the stock SDK.
- Local VAD and barge-in share `LOCAL_VAD_RMS_THRESHOLD` (default 500; the
  README hybrid example uses 100), tuned on the Mac microphone. Hybrid/OpenAI paths average both mic
  channels; Gemini and Hugging Face use channel 0.
- `AUDIO_OUTPUT_BACKEND=gstreamer` plays through `autoaudiosink` (the Mac's
  default output), not the robot speaker. Keep `sdk`.
- `wake_up_if_sleeping` enables motors only when the head is near the sleep
  pose. The app never disables torque; the default inactivity sleep is 24 hours
  (`REACHY_MINI_APP_TIMEOUT_MINUTES`).
- The Lite camera defaults to 1920×1080; `tools/camera.py` sends the JPEG
  unresized.
- The Official33 wake model was trained and tuned on Mac-microphone audio and
  lives in a private local directory; `openwakeword` is only in the
  `wake-runtime` dependency group.
- App target is `reachy-mini>=1.11.0,<1.13`. A client/daemon version mismatch
  only logs a warning.

From Pollen documentation (not verified on this hardware): the Lite needs its
7 V/5 A supply because USB cannot power the motors; macOS cameras can appear
dark; mixer levels other than PCM should stay at 100%.

## Implementation order and acceptance gates

### Step 0 — Preparation (before arrival)

- Install Reachy Mini Control; update the robot daemon on first connection and
  match the local SDK to it if it is newer than 1.11.
- Prepare a minimal `.env` profile for bring-up: provider key only, with
  `HYBRID_RUNTIME_ENABLED`, `LOCAL_ACOUSTIC_WAKE_ENABLED`, barge-in flags,
  Spotify and household memory unset.
- Microphone level probe: done (`reachy-mic-levels`, see
  [local audio backends](../features/local-audio-backends.md#microphone-level-probe)).
- Acceptance: gate green on the target SDK.

### Step 1 — Power-on and motion only

1. Stop any simulator daemon (`pgrep -fl reachy_mini.daemon`). Connect power,
   then USB. Start the daemon from Reachy Mini Control.
2. Grant the terminal camera and microphone access in macOS settings.
3. Run with the robot clear of obstacles, using the inactivity timeout to
   trigger sleep without speech:
   `REACHY_MINI_APP_TIMEOUT_MINUTES=1 .venv/bin/reachy-mini-conversation-app --media-backend no_media --no-camera`.
4. Observe wake-up, the first second after start (neutral target before
   breathing interpolates), idle breathing, the timeout sleep, and shutdown.

Acceptance: wake-up is smooth from both the sleep pose and a hand-moved head; no
jitter or buzzing while breathing; sleep reaches the sleep pose; the app exits
cleanly. Record whether torque remains on after sleep.

### Step 2 — Robot audio path

1. Run with `--media-backend local`. Confirm the logs show the LOCAL backend,
   "Applied Reachy audio startup config", and no "Enabling webRTC echo
   cancellation".
2. With the app stopped, run the opt-in chunk smoke:
   `REACHY_RUN_SDK_CHUNK_SMOKE=1 .venv/bin/pytest tests/test_native_sdk_gemini_chunks.py -q`.
3. Confirm sound comes from the robot speaker and the head wobbles with speech.

Acceptance: playback from the robot speaker without audible drops; microphone
frames are non-silent (silence suggests an inverted FPC cable per Pollen docs).

### Step 3 — Plain conversation

1. Direct mode (no hybrid runtime), stock SDK, barge-in flags off. Hold a
   short conversation at normal speaker volume.
2. Check the robot does not respond to its own voice.
3. With the app stopped, run `.venv/bin/reachy-mic-levels --media-backend local`
   at normal speaker volume and from the usual seating distance. Record the
   table here, including both `mixed` and `channel0` rows.

Acceptance: conversation, tools, movement and camera work; no self-triggering;
measured RMS levels recorded here.

### Step 4 — Interruption

1. Enable the provider's local barge-in flag and re-tune its threshold from the
   Step 3 measurements.
2. Test interruption on the stock SDK, then with the flush candidate via
   `PYTHONPATH` and `MACOS_LOCAL_AUDIO_FLUSH_ENABLED=1`.

Acceptance: record whether speech stops audibly on each SDK. If only the
candidate works, decide whether to submit the SDK patch upstream.

### Step 5 — Hybrid runtime and wake

1. Enable `HYBRID_RUNTIME_ENABLED` with continuous local STT. Re-tune
   `LOCAL_VAD_RMS_THRESHOLD` from Step 3.
2. Enable acoustic wake and re-measure recall on robot-microphone speech and
   false wakes during ordinary conversation and robot speech.

Acceptance: ambient mode opens no cloud session; wake recall and false-wake
observations recorded; cloud session closes after inactivity.

### Step 6 — Endurance

Leave the app idle for at least 30 minutes, then check servo temperature,
breathing jitter and memory/CPU of the app process on the Mac.

Acceptance: no overheating warnings or visible jitter; decide whether the
24-hour inactivity default should change.

## Risks

- Barge-in may not cut buffered speech on the stock SDK (see facts above).
- Thresholds tuned on the Mac microphone may cause missed or false interruption
  and wake events, made worse by motor and fan noise.
- Channel handling differs between providers; if the XVF3800 channels differ,
  averaging may dilute AEC output.
- Long-running torque may heat servos; upstream has an open branch about
  breathing-induced micro-vibrations.
- Full-resolution camera frames increase vision latency and cost.

## Test strategy

Hardware steps are manual and recorded here with the exact command, SDK and
daemon versions, and outcome. Any code change made during bring-up follows the
normal gate and needs a hardware-independent regression test where possible.
Do not record audio, transcripts or camera frames; log only levels, counts and
timings.

## Documentation updates

Update this plan's log after each step. Update
[hybrid runtime](../features/hybrid-runtime.md),
[simulator speaker output](../features/simulator-speaker-output.md) and
[wake word](../features/wake-word.md) when hardware thresholds or defaults
change, and the README launch section once the Lite procedure is confirmed.

## Progress log

| Date | Step | Result |
|------|------|--------|
| 2026-10-10 | Plan | Created from a read-only hardware-readiness audit. SDK target moved to 1.11.0; gate green (1,264 passed, 4 skipped). A headless MuJoCo daemon launched from the agent sandbox did not bind its API port on either 1.11.0 or 1.10.0rc5, so simulator connectivity on 1.11.0 still needs a normal terminal run. |
| 2026-10-10 | Step 0 | Added the `reachy-mic-levels` probe for Step 3: four guided phases, channel-average and channel-0 levels, barge-in onset counts and a candidate threshold. 15 unit tests with fake media; not yet run against hardware. |
| 2026-10-12 | Step 1 | Lite connected over USB with power; venv daemon 1.11.0 found all motors and the Reachy Mini Audio/camera devices. Motion-only run (`--media-backend no_media --no-camera`, 1-minute timeout): wake-up and timeout sleep fine, sleep pose reached, clean shutdown; user observed slight jitter while idle. Daemon logged three throttled "IK error: Collision detected or head pose not achievable" warnings during the run (untimed; source not yet identified). `stop-current-app` returned HTTP 400 (harmless; upstream #550). Fixed: `no_media` previously polled the absent SDK audio system every ~6 ms, flooding warnings; capture now does not start without media (regression test added). Note: `no_media` makes the SDK release daemon media, which stays released until `POST /api/media/acquire` or a daemon restart. |
| 2026-10-12 | Step 3 (probe) | First runs read exact digital zero: Terminal lacked macOS microphone permission (a direct GStreamer check read 0 on both the MacBook and Reachy microphones; System Settings input meter moved). After granting permission, `reachy-mic-levels --media-backend local` at threshold 500: quiet p99 66 / peak 89; robot playback p99 35 / peak 74 (on-board AEC leaves less than room noise); speech p50 1049 / p99 7825, 67% at threshold; speech over playback p50 600, 52% at threshold. Zero onsets in quiet and playback phases; mixed and channel0 identical. Candidate threshold ~407; keep 300–500 and do not use 100. The probe now warns on all-zero input. Daemon from Reachy Mini Control reported 1.8.0 versus SDK 1.11.0; update the daemon before Step 3 conversation. |
| 2026-10-12 | Step 3 (conversation) | Desktop-app daemon updated (no version warning). Gemini 3.1 Flash Live direct mode: connection ~0.3–0.4 s, first audio 0.5–0.7 s (2–9 s with web search/time tools); robot speaker, camera pipeline and tools worked. User reported missed speech. `AUDIO_DIAGNOSTICS_ENABLED=1` showed 10 ms SDK frames against an 8-frame (80 ms) capture queue: 72 frames dropped during startup and 14 (140 ms) during a 158 ms event-loop stall around a tool call, with only the first drop logged. Queue raised to 200 frames (~2 s) with per-burst drop warnings; regression tests added. Retest pending. The web UI showed no transcripts (not yet investigated). |
| 2026-10-12 | Step 3–4 | Retest with the 200-frame queue: `dropped=0` for the whole session, including a 175 ms stall. Spotify play returned 204 but nothing played on the Mac (Spotify app open; cause under investigation). Interruptions were slow: native flush is skipped on macOS LOCAL, so seconds of speech stayed queued in the SDK; added paced SDK playback (0.3 s lead, 0.1 s pieces) with tests; audible retest pending. After ~9.5 min Gemini sent GoAway and direct mode retried the fenced handler forever, so Reachy stopped responding; direct mode now rebuilds the handler after a provider-ended session (regression test added). The `household_companion` profile now relies on Gemini built-in Google Search instead of the remote search tool (2–7 s per call). |
| 2026-10-12 | Step 3–4 | Second stall, about 2.5 min into a session with no GoAway: the last reply's audio ended at 22:54:14 and the server then sent no further messages for over a minute (no voice-activity events) while the app kept forwarding microphone frames (~100/s, `dropped=0`, sub-ms sends, socket open). Playback pacing was ruled out: the paced loop only cancels a cancellation-safe queue read. The log had no microphone levels, so a silent microphone could not be told apart from a silent server; diagnostics now report forwarded channel-0 levels and seconds since the last backend activity. Spotify: the Mac was listed and unrestricted but not the active player, so play returned 204 with no sound and the model claimed success. The tool now activates the pinned device first and checks playback afterwards. Retest of both pending. |
| 2026-10-12 | Step 3–4 | Third stall, 15 s into a session, with the new diagnostics: the microphone was clearly live (241 of 502 frames above threshold, peak RMS 3461) while `server_idle_s` climbed from 2.5 to 16.2, so the provider stopped answering. Both stalls in this session followed a tool call answered within 2 ms: the startup greeting's `play_emotion` call was never followed by greeting audio, and a mid-reply `play_emotion` and `move_head` pair was never followed by more speech or voice-activity events. Google now lists `gemini-3.1-flash-live-preview` as a legacy model with synchronous function calling. Next: retest on `gemini-3.8-live-extended-thinking` (non-blocking tools; implemented by the migration plan) before adding an app-side stall watchdog. |
| 2026-10-12 | Step 3 (Spotify) | Direct API checks outside the app isolated the Spotify failure to the Mac desktop client: pause and resume on the Mac worked, and the same track played immediately on the account's TV, but starting a track on the Mac left an empty player (no item, all actions disallowed) even after restarting the app. A `market=from_token` search parameter added earlier this session was reverted because it needs a profile scope the app lacks (HTTP 403). No further tool change needed. |
| 2026-10-12 | Step 3–4 | Switched to `gemini-3.8-live-extended-thinking` (thinking level low): a long session with tools and interruptions worked, and the user rated interruptions good. After the user went quiet, the server stopped sending events at 23:20:15 and closed the session at 23:21:01 (`APIError: 1000`). The app then retried the fenced handler instead of opening a new session; direct mode now rebuilds the handler after any failed start (regression test added). Spotify: the user's phone could start playback on the Mac, so the Mac app can start remote playback in general; only this app's Web API start fails there, while pause and resume work. |
