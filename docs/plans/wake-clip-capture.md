# Plan: Wake clip capture for retraining

- Status: `in-progress`
- Last updated: 2026-10-27
- Feature doc: [Wake clip capture](../features/wake-clip-capture.md)

## Goal

Collect a few days of short, real living-room clips from Reachy's own microphones to retrain the
"Reachy" wake model, especially for music from the Echo, TV and kids. The household agreed on
2026-10-27 to record for a few days on the Mac mini.

## Scope

- Short clips saved locally around wake events, never continuous recording.
- Off by default (`WAKE_CLIPS_ENABLED`); clips are deleted after `WAKE_CLIPS_KEEP_DAYS`.
- Out of scope: labelling, training and the music echo-cancellation experiment (held on
  2026-10-27). Clips are labelled later, offline, with local Whisper and by listening.

## Decisions (agreed with the household, 2026-10-27)

| Question | Choice |
|----------|--------|
| What is saved | 4 s around each acoustic wake and near miss; the utterance when local STT hears the name but the wake model did not fire (up to 10 s); a random 4 s background sample about every 10 minutes |
| When | Only while Reachy listens locally for his name (`LOCAL_AMBIENT`); never during a conversation |
| Transcripts | Not saved; the transcript is only checked for the name, as before |
| Where | `~/.local/share/reachy_mini_conversation_app/wake-clips/<date>/` on the Mac running Reachy; never uploaded |
| Keeping | 14 days by default, then deleted automatically |
| Who | Everyone in the room is recorded, kids and guests included; the household knows when it is on |

## Design

- `wake_clips.py`: `WakeClipRecorder` keeps a rolling 12 s of the microphone audio the wake model
  hears, in memory, and writes a WAV slice of it for an event. It also saves the background samples
  and deletes old day folders.
- `OpenWakeWordDetector` gains an `event_observer(score, detected)`, called at a detection and at
  the end of a near miss.
- `HybridRuntime` feeds the recorder the frames it already gives the wake model (local listening
  only), clears it when a listening stretch starts over, and saves a `name_heard` clip when local
  STT wakes Reachy without the wake model.
- No new dependencies: WAV files are written with the standard library.

## Implementation order

1. Recorder with unit tests (buffer bounds, WAV contents, background timing, pruning, clearing).
2. Detector observer with tests (near miss at event end, detection at once).
3. Runtime hooks with tests (feeding only while listening locally, `name_heard` clips, nothing
   when disabled).
4. Config, `.env.example`, README, feature doc.
5. Deploy on the Mac mini with `WAKE_CLIPS_ENABLED=1`; check clips appear and sound right.

## Acceptance criteria

- [x] With the switch off, nothing is buffered or written.
- [x] Wake, near-miss, name-heard and background clips are written as 16-bit mono WAV with the
      event kind and score in the file name; logs carry only kind, score and length.
- [x] Nothing is buffered or saved during a cloud conversation.
- [x] Day folders older than the keep period are deleted.
- [x] Unit tests, full suite, ruff and mypy pass.
- [ ] On the Mac mini: clips of each kind appear during a day of normal use and contain the
      expected sound.

## Risks

- Privacy: this deliberately persists ambient audio, which the app otherwise never does. Mitigated
  by the opt-in switch, local-only storage, short clips, automatic deletion and no transcripts.
- Disk: about 128 KB per 4 s clip; background samples add about 18 MB a day.
- Timing: `name_heard` clips are cut from the buffer by the segment's length plus the STT delay,
  so they may include a little sound after the utterance; labelling crops them.

## Test strategy

Unit tests for the recorder and detector observer with fake clocks and temporary folders; runtime
tests with fake frames for the listening-only and name-heard behaviour; full repository gate.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-27 | Plan agreed; implementation started on `feat/wake-clip-capture`. |
| 2026-10-27 | Built and tested: 1,948 passed, 4 skipped; ruff and mypy clean. On the Mac mini (enabled 14:50): background clips every ~5–15 min, near misses (0.25–0.35) and two wakes at 0.99 saved as 4 s, 16 kHz mono, `0600` files. A local Whisper check found no name in the first near-miss clip (ordinary speech). Calls from another room left no trace. Pending: a `name_heard` clip in normal use. |
