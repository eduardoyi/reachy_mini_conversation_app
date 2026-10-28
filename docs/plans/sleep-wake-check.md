# Plan: Gentler sleep mode, then no name check

- Status: `in-progress` (steps 1-2 built and tested; step 2 superseded on 2026-10-28 by
  [wake-name-check.md](wake-name-check.md); step 3 waiting on data)
- Last updated: 2026-10-28
- Feature doc: [Hybrid runtime](../features/hybrid-runtime.md) (go_to_sleep standby)

## Goal

While asleep, Reachy needs local STT to confirm his name leads the utterance before an acoustic wake
counts. It was added on 2026-10-19 because the acoustic model scored ordinary talk 0.95-0.99 and woke
him at night. On 2026-10-27 it missed two real calls (0.99 and 0.98) right after an update had
restarted him into sleep mode. The household's call: restarts are not bedtime, the check should be
smarter, and the check should go away once the wake model is good enough.

## Steps

1. **A restart is not bedtime.** Started in the sleep pose, Reachy rests there quietly, but the
   runtime starts awake (`main.py` no longer passes `start_asleep`), so the wake word alone wakes him.
   Only "go to sleep" turns the strict check on.
2. **A second, accurate look at the name.** The runtime keeps the last 2.5 s the wake model heard, in
   memory, only while listening locally. Asleep, when the fast transcript has no name first, the request
   model (whisper-large-v3-turbo) transcribes that window; the name leading it confirms the wake. The
   missed call at 21:32 was "Richie!" in that audio. Costs about 1.6 s, only in that case.
   Superseded 2026-10-28: every acoustic wake now gets a name check of that window, whisper-small
   first and turbo second, before Reachy reacts ([wake-name-check.md](wake-name-check.md)). Asleep
   it keeps the stricter "name first" match.
3. **Drop the check** (later). Decide from data, not guesses:
   - Label the `wake` clips saved while asleep (`unconfirmed_wake_while_asleep` in the dated logs) as
     a real call or not, for a week or more of normal use.
   - Drop the check when false wakes while asleep are rare enough for the household (proposal: about
     one a week or fewer), most likely after the wake model is retrained on the collected clips.
   - Then drop the stricter asleep match (`_name_leads`), keeping `go_to_sleep` as a pose. The
     wake window stays: the name check for every wake uses it.

## Risks

- Step 1 brings back the 2026-10-19 failure after a restart: ordinary talk scoring 0.95+ can open a
  conversation until someone says "go to sleep". Accepted by the household; the addressee check
  closes side conversations.
- Step 2 keeps 2.5 s of room audio in memory while listening locally; it is never written or sent
  anywhere except the local Whisper server, and cleared when used.

## Acceptance criteria

- [x] After a restart in the sleep pose, an acoustic wake without the name in the transcript opens
      the conversation as when awake (runtime starts awake).
- [x] Asleep, the accurate model hearing the name in the wake audio wakes him; "Thank you." does not.
- [x] The window holds at most 2.5 s and restarts on a new sample rate.
- [x] Full gate passes.
- [ ] Live: a call after "go to sleep" logs `Wake name check heard=True` and wakes him.
- [ ] Step 3 decided from labelled clips.

## Test strategy

`tests/test_hybrid_runtime.py`: the two accurate-check tests, the window test, the existing asleep
tests (unchanged). Live on the Mac mini after "go to sleep".

## Progress log

| Date | Update |
|------|--------|
| 2026-10-27 | Two calls at 21:32 missed after the 20:31 restart; steps 1-2 built and tested; step 3 agreed as the goal. |
| 2026-10-28 | Step 2's late turbo recheck replaced by the early name check on every wake ([wake-name-check.md](wake-name-check.md)). |
