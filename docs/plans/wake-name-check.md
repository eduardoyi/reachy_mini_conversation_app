# Plan: Whisper checks the name before every acoustic wake

- Status: `in-progress` (deployed on the Mac mini 2026-10-28; a live call not yet checked)
- Last updated: 2026-10-28
- Feature doc: [Hybrid runtime](../features/hybrid-runtime.md)
- Related: [Wake clip capture](wake-clip-capture.md), [Gentler sleep mode](sleep-wake-check.md)

## Problem

The household said Reachy has a hard time waking, especially when called from far away. The wake
clips and logs from 2026-10-27 15:30 to 2026-10-28 10:00 show two separate failures of the acoustic
wake model at threshold 0.95:

- **Missed calls.** Real calls ("Ritchie! Ritchie!", "¿Sí? Richie", "Saluda a Ruti") scored 0.3-0.93
  and were dropped. On 2026-10-28 at 09:53 it took three calls to wake him.
- **False wakes.** 59 of 86 wake clips had no name in them. On the morning of 2026-10-28, 16 of 22
  wakes were short clinks with most energy at 3-4 kHz (most likely dishes at breakfast). Each opened a
  cloud session that sat silent for 15 s, while the wake model was not listening.

## Offline evaluation (2026-10-28, 269 saved `wake` and `near_miss` clips)

The last 2.5 s of each clip (the audio the wake model fired on) was transcribed by the running
Whisper server; "named" means some transcript of the clip contains the name.

| Setup | Named calls woken (of 38) | False wakes |
|-------|---------------------------|-------------|
| Today: threshold 0.95, no check | 27 | 59 |
| Threshold 0.3, whisper-small check only | 25 | 0 |
| Threshold 0.3, turbo check only | 28 | 0 |
| **Threshold 0.3, whisper-small then turbo** | **34** | **0** |

Name check speed on the mini: whisper-small about 0.4 s, turbo about 1.6 s. Allowing near spellings
(difflib) added false passes ("Ready?", "I'm reaching") without more named calls, so the check uses
the existing Whisper spellings (`_WAKE_WORD_VARIANTS`) only.

Limits: the "named" truth comes from the same Whisper transcripts, so recall is probably overstated;
calls the wake model scored under 0.2 were never saved and are not in the set; most named clips came
from one test session on 2026-10-27.

## Design

1. `_observe_acoustic_frame` snapshots the 2.5 s wake window at every detection (not only asleep)
   when a request transcriber is configured. It stays in memory, is used once and is dropped.
2. `_run_activation` with that audio enters `LOCAL_COMMAND` quietly: the request clip starts
   recording at once (nothing said after the name is lost), but there is no antenna cue, no turn
   toward the speaker and no music ducking yet.
3. The window goes to whisper-small (`name_check_transcriber`, the ambient model); if that does not
   hear the name, to the request model (turbo). Awake, the name anywhere in the window counts; asleep,
   it must lead a sentence (`_name_leads`), as before.
4. Heard: wake as before (and leave sleep mode). Not heard: back to `LOCAL_AMBIENT` without moving,
   logging `Wake model fired but the name was not heard`.
5. If every check fails with an error (Whisper down), an awake Reachy wakes anyway (the old behavior)
   and an asleep one stays asleep (as before).
6. On the Mac mini, `LOCAL_ACOUSTIC_WAKE_THRESHOLD` goes from 0.95 to 0.3. The code default (0.5)
   does not change.

This replaces the late asleep recheck from [sleep-wake-check.md](sleep-wake-check.md) step 2
(`_name_heard_accurately`); asleep now uses the same early check. Without a request transcriber the
old paths are unchanged.

## Risks

- **Wake latency.** About +0.4 s before the antennas react; +2 s when whisper-small misses the name.
  The request is recorded meanwhile, so only the cue is later.
- **Whisper queue.** The whisper-small check is not marked urgent in the server queue and can wait
  behind one ambient clip (about 0.3-0.5 s).
- **Deaf moments.** A rejected candidate leaves the wake model off for the check (0.4-2 s). At 0.3
  the clips suggest about one candidate every few minutes, more during kitchen noise.
- **Mid-sentence ambient context.** A candidate closes the ambient sentence in progress, as any wake
  did before.
- **Privacy.** The window stays in memory, only goes to the loopback Whisper server, and is dropped
  after use; no transcript is logged beyond `heard=` and the model name.

## Acceptance criteria

- [x] Awake: a window heard as the name by whisper-small wakes him without asking turbo.
- [x] Awake: whisper-small misses, turbo hears it: he wakes.
- [x] Awake: neither hears it: back to listening, no cue, no cloud, request clip dropped.
- [x] Awake: both checks fail with errors: he wakes as before.
- [x] Asleep: only a name leading the window wakes him; errors keep him asleep.
- [x] No cue or listening notice before the check passes.
- [x] Full gate passes.
- [ ] Live on the Mac mini: false wakes from kitchen noise logged as not heard; a far call that scores
      under 0.95 wakes him.

## Test strategy

`tests/test_hybrid_runtime.py`: the cases above with fake transcribers; the existing asleep tests
(adapted for the early check); `tests/test_main*.py` wiring if touched. Live: watch the dated app log
on the mini for `Wake name check` lines over a day of normal use.

## Documentation

`docs/features/hybrid-runtime.md`, `docs/features/wake-cue.md` if the cue timing note changes,
`docs/plans/sleep-wake-check.md` (step 2 superseded), `.env.example`, README if it names the
threshold, plans index.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-28 | Logs and clips reviewed; offline evaluation above; plan written and built. |
| 2026-10-28 | Deployed to the Mac mini (a410cc6), `LOCAL_ACOUSTIC_WAKE_THRESHOLD` 0.95 → 0.3 (`.env` backup in `~/Coding/.env.bak-2026-10-28`), restarted 10:50. First live candidate (score 0.56, 10:51) was rejected: whisper-small 0.41 s + turbo 1.68 s, no cue, back to listening. In the first 8 minutes that was the only candidate. A live call has not been checked yet. |
