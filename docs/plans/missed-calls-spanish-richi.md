# Plan: Catch "Ri-chi" calls the wake model misses

- Status: `in-progress` (both parts live on the Mac mini since 2026-10-29; a week of live results pending)
- Last updated: 2026-10-29
- Feature docs: [Hybrid runtime](../features/hybrid-runtime.md), [Wake word](../features/wake-word.md)
- Related: [Wake clip capture](wake-clip-capture.md), [Custom openWakeWord training](custom-reachy-openwakeword-training.md)

## Problem (2026-10-29 morning)

The household reported many missed calls, especially from Adriana.

- **The wake model never fires on her calls.** official33 scored 0.00 on "Ricci! Ricci! Ricci!" (08:19), "Hola
  Ritzi, hola Ritzi" (08:45) and "Richie, lights off" (08:51). Over every `name_heard` clip saved since
  2026-10-27, 10 of 14 scored 0.00-0.10. A lower threshold cannot help; the Spanish "Ri-chi" is not in what
  it learned.
- **The backup is slow.** Those calls were caught only by the transcript wake, which waits for a room segment
  to end. Segments end after 0.6 s under the RMS threshold, which a noisy kitchen never reaches. Segments ran
  up to 20 s (11 of 60 this morning were 8 s or more), and "Hola Ritzi" came at the end of a 14.2 s segment.
  At 20 s the oldest audio slides out and the segment keeps going.
- **Some spellings are not recognised.** The transcript wake knew "richie, richy, ritchie, reachie, reechy"
  but not "richi, ricci, ritzi".
- **Not the cause.** The switch to Qwen at 08:44 did not cause the misses: the same misses happened under
  Whisper from 08:00. After 08:57 a steady high-frequency noise fired the wake model about 20 times with no
  speech in it, and every one of those was rejected correctly.

## Part 1: name check in open segments (built)

- `AmbientContextProcessor` transcribes the newest 2.5 s of an open segment every 1.5 s, once the segment
  reaches 2.5 s, with the fast model. If the text names Reachy, the segment closes there and goes through the
  normal transcript wake. A probe's text is never kept.
- A probe runs only when no other STT call is in flight, never while a request is being taken, and only one
  at a time. Cost: about one 2.5 s clip per 1.5 s of continuous speech, about 0.15 s each on Qwen 0.6B.
- `TranscriptWakeWordDetector.mentions_wake_word` is the probe. The aliases add "reachi, reechi, richi,
  ritchy, ricci, ritzi"; real names such as "Ricky" stay out. The runtime's name-check spellings gain
  "ritzi".
- Config: `AmbientContextConfig.name_probe_interval_seconds` (1.5) and `name_probe_window_seconds` (2.5).

## Part 2: a second wake head for "Ri-chi" (built)

The old training pipeline and corpus are archived on an external drive that is not connected. This is a fresh,
local, CPU-only training of one small head on the same frozen openWakeWord front end (the melspectrogram and
embedding models of the official33 bundle). It runs next to official33, not in its place, so the English
"Reachy" calls official33 already catches cannot regress.

### Data (private, `~/wake-retrain` on the mini, `0700`)

- **Labels from the app log:**
  - a call is a wake whose name check passed and was followed by a request;
  - a non-call is an unconfirmed wake that was not trusted.
- **Second labeller:** Qwen3-ASR 1.7B with the names hint checks every clip (yes/no and times only, no
  text). A non-call in which Qwen hears the name is left out, and `name_heard` clips are located with 1.5 s
  windows of the 0.6B.
- **Train days** (2026-10-27/28):
  - 14 calls, 10 `name_heard` calls;
  - 783 real non-calls (unconfirmed wakes, near misses without a name, room clips);
  - 1,032 synthetic calls ("Richi", "Ricci", "Hola Richi", "Richie", "Reachy"…) and 432 synthetic non-calls
    ("Qué rico", "Ricardo", "Ricky", "ricetta"…). The synthetic clips use macOS voices (es, it, pt, en) at 4
    speeds and 5 pitches, with reverb, over the train days' room clips.
- **Test day** (2026-10-29, never used for training or for choosing settings): 7 calls, 4 `name_heard` calls,
  109 non-calls.
- 138 clips were left out as uncertain.

### Model and choice

- An MLP (16×96 → 96 → 64 → 1, dropout 0.3) trained 60 epochs with positives oversampled 1:10, exported to
  ONNX (`reachy-richi.onnx`, sha256 `d2e30fbbfba3f6e7…`).
- Four settings were compared on the train days' validation clips. The one with the fewest false fires there
  was kept, at threshold 0.9.

### Results (clip level, before the name check)

| Set | official33 @0.3 | new head @0.9 | Either |
|-----|-----------------|---------------|--------|
| Test calls caught by the wake model (7) | 7 | 2 | 7 |
| Test calls only the transcript caught (4) | 0 | 2 | 2 |
| Test room clips (58) | 0 | 1 | 1 |
| Test near misses (17) | 0 | 0 | 0 |
| Holdout-30 recordings (30) | 30 | 13 | 30 |

The test room clip it fired on has no name according to Qwen, so the name check would reject it. A scaled
score of the new head never reaches the "trusted without the name" bar, so every wake it causes goes through
the name check.

### Runtime

- `LOCAL_ACOUSTIC_WAKE_EXTRA_HEAD` (path) and `LOCAL_ACOUSTIC_WAKE_EXTRA_THRESHOLD` (default 0.8; 0.9 on the
  mini). The detector runs both heads on one feature stream. The extra head's score is scaled onto
  official33's threshold (`threshold / extra_threshold`), the strongest head wins, and the wake log names it
  (`head=reachy-richi`).
- Licensing: macOS voices are used only to train this private household model, which is never distributed.

## Acceptance criteria

- [x] Part 1 unit tests: a name in a long open segment closes it at the next check; no checks without a
      probe or in request mode; config bounds; Spanish spellings counted, "Ricky" not.
- [x] Full gate and deploy (`df39d7d`, live 09:44).
- [ ] On the mini, a `Name heard … into an open segment` line ahead of a wake.
- [x] Part 2 head trained and compared on the held-out day and holdout-30: 2 of 4 transcript-only calls
      now caught acoustically; no call lost; one extra room-clip fire, which the name check rejects.
- [ ] A week live: `head=reachy-richi` wakes that turn into requests; no rise in confirmed false wakes.

## Risks

- Probes add GPU work in long noisy stretches. They only run when STT is idle, so they cannot delay a finished
  segment.
- More aliases can wake on chatter that sounds like "Richi". The cooldown, and the name leading the sentence
  while asleep, still apply.
- A locally trained head on synthetic voices can raise false wakes. The held-out day and the name check guard
  against it.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-29 | Diagnosed this morning's misses; built the open-segment name check and the new aliases. |
| 2026-10-29 | Trained and evaluated the "Ri-chi" head; added extra-head support to the detector. |
