# Plan: Catch "Ri-chi" calls the wake model misses

- Status: `in-progress` (open-segment name check built; wake model retraining in progress)
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

## Part 2: retrain the wake model with her pronunciation (in progress)

The old training pipeline and corpus are archived on an external drive that is not connected, so this is a
fresh, local, CPU-only head training on the same frozen openWakeWord front end (melspectrogram and
embedding models from the official33 bundle).

- Positives:
  - the household's real calls from saved wake clips (log-confirmed wakes with a request, `name_heard` clips);
  - synthetic "Richi / Ricci / Reachy / Richie" from macOS voices (es, it, pt, en), with speed and pitch
    variations, mixed over real room clips.
- Negatives: every unconfirmed wake, near-miss and background clip, plus synthetic household speech
  without the name.
- Held out by day: train on 2026-10-27 and 2026-10-28, test on 2026-10-29. The 30 holdout recordings stay
  evaluation-only, as before.
- Acceptance before swapping it in:
  - no fewer of today's real calls than official33, and clearly more of the `name_heard` ones;
  - no more false wakes on today's negatives;
  - recall on holdout-30 within 2 of official33.
  The Whisper/Qwen name check stays in front of every wake either way.
- Licensing: macOS voices are used only to train a private household model that is never distributed.

## Acceptance criteria

- [x] Part 1 unit tests: a name in a long open segment closes it at the next check; no checks without a
      probe or in request mode; config bounds; Spanish spellings counted, "Ricky" not.
- [ ] Full gate, deploy, and on the mini a `Name heard … into an open segment` line ahead of a wake.
- [ ] Part 2 model trained, compared with official33 on held-out days and holdout-30, and swapped in only if
      it meets the bar above.

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
