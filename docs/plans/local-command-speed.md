# Plan: Faster local commands on the Mac mini

- Status: `in-progress` (built and tested; live timings on the Mac mini pending)
- Last updated: 2026-10-27
- Feature doc: [Hybrid runtime](../features/hybrid-runtime.md) (local STT server)

## Goal

Make spoken commands ("Reachy, turn off the lights") faster and more reliable on the M1 Mac mini by
removing waste in local speech-to-text, and keep enough logs to measure a whole day.

## Findings (2026-10-27, Mac mini logs since 18:18 and 61 saved wake clips)

- The request model (whisper-large-v3-turbo, PyTorch on MPS) took 3.1 s per request, on the critical
  path of every command; whisper-small took 0.75 s per background clip, however short.
- `scripts/whisper_server.py` encoded every clip twice: once to pick the language and again inside
  `generate`.
- One clip at a time, first come first served: at 18:12 a request's second, accurate transcription
  waited behind background clips until its 6 s timeout, 11 s from the name to the conversation.
- At 18:37 the turbo model picked Portuguese and heard "Leitinho"; the family speaks Spanish and English
  mainly, Italian and Portuguese too, so all four languages stay.
- The app, daemon and Whisper logs were overwritten at every restart; most of the day was lost.

## Benchmark (same 61 clips, M1 Mac mini, Reachy running)

| Engine | turbo median / p90 | small median | Text vs today |
|--------|--------------------|--------------|---------------|
| PyTorch today (encodes twice) | 3.19 / 4.49 s | 0.74 s | — |
| PyTorch, encode once | 1.64 / 1.90 s | 0.46 s | identical 61/61 |
| MLX (`mlx-whisper` 0.4.3), encode twice | 2.64 / 3.73 s | 0.50 s | turbo 53/61 identical |
| MLX, encode once | 1.34 / 1.73 s | 0.30 s | turbo 53/61 identical |

Decision: encode once in the existing server. MLX would save about 0.3 s more per request but adds a
second engine and changes some text; not now.

## Scope

1. `whisper_server.py`: pass the encoder output to `generate` (and to the loop retry).
2. `whisper_server.py`: request-model clips go before waiting background clips (`TurnLock`).
3. Logs: `scripts/start-reachy.sh` appends to `WHISPER_LOG`; the Mac mini's launcher
   (`~/Coding/start-reachy.command`, outside the repo) writes `~/Library/Logs/Reachy/{app,daemon,whisper}-<date>.log`,
   appended across restarts, deleted after 7 days without writes.
- Out of scope: MLX, a preference for Spanish and English in close language picks, local reply voice.

## Acceptance criteria

- [x] Each clip is encoded once, including the loop retry (unit test).
- [x] A request clip goes before background clips already waiting; a cancelled one does not block them.
- [x] Full gate passes.
- [ ] On the Mac mini: `took_s` for whisper-large-v3-turbo about half of before; request clips show
      `waited_s` near 0; logs survive a restart.

## Risks

- Privacy: with `QUICK_COMMANDS_LOG_TEXT=1` the app log holds what was said to Reachy; it is now kept
  up to 7 days instead of until the next restart.
- Background clips can wait longer while a request is transcribed (about 1.6 s at most).

## Test strategy

`tests/test_whisper_server.py` (fake model: one encoder pass, retry reuses it; `TurnLock` order and
cancellation); full gate; live `Transcribed` lines on the Mac mini.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-27 | Logs read, MLX and PyTorch benchmarked on 61 saved clips, encode-once, request priority and dated logs built. |
