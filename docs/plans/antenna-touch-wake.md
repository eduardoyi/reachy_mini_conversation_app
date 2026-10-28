# Plan: Bend an antenna to wake Reachy

- Status: `in-progress` (built and tested; thresholds not yet checked with real touches on the robot)
- Last updated: 2026-10-28
- Feature doc: [Hybrid runtime](../features/hybrid-runtime.md), [Wake cue](../features/wake-cue.md)

## Goal

Pollen's daemon (since SDK 1.9, [reachy_mini#1244](https://github.com/pollen-robotics/reachy_mini/pull/1244))
wakes the robot and starts a configured "startup app" when someone touches an antenna, but only while
no app is running. Our app always runs, so that never fires here. The household wants the same gesture
inside our app: bend an antenna and Reachy wakes as if called by name. It works from close by, in noise,
and needs no words.

## Design

1. **Detection, movement worker** (`moves.AntennaTouchDetector`, `MovementManager._check_antenna_touch`):
   20 times a second the worker reads the measured antennas (cached by the SDK, no I/O) and compares them
   with every antenna target it sent in the last 0.15 s. Held at least 0.25 rad (14°) from all of them for
   0.2 s is a touch; it re-arms once back within 0.10 rad (Pollen's 0.25 / 0.10). One touch per 2 s.
   - Pollen's watcher only looks while the antennas are still; ours sway 15° at 0.5 Hz while idle and
     perk at up to 6 rad/s, so comparing with the recent targets instead absorbs tracking lag.
   - Every 10 minutes the log notes the largest deviation seen without a touch, to tune the threshold
     from real data.
2. **Wake, runtime** (`HybridRuntime.request_activation`, reason `antenna_touch`): thread-safe, starts
   an activation on the runtime's loop (ignored before it runs or after it closes). `antenna_touch` takes
   the acoustic-wake listening path (`_LISTENING_WAKES`): antenna cue, request clip, quick commands,
   otherwise Gemini. Differences: no name check, it wakes him from "go to sleep" too, the sentence in
   progress at the touch is only context, and he does not turn toward the last voice heard.
3. **Wiring** (`main.py`): `ANTENNA_TOUCH_WAKE` (default on) gives the movement manager a handler that
   calls the runtime once it exists.

## Risks

- **False touches** from his own fast moves (dances, emotions) or a head bump. The lag window and the
  0.2 s hold should cover them; the logged peak deviation will show the margin. A false touch in a
  conversation does nothing (activations are ignored then).
- **A touch during a conversation or a quick reply** is ignored; using it to interrupt him is a
  possible follow-up.
- **Motors off:** the measured position then follows a hand freely; our app keeps them on.

## Acceptance criteria

- [x] Sway and perk with a 0.1 s lag never fire; a held 20° bend fires once; a 0.1 s bump does not; a
      second press within 2 s does not.
- [x] The worker reports a touch from the measured antennas and skips a missing reading.
- [x] A touch wakes him from sleep without a name check, records the request, and keeps the talk at the
      touch as context; a touch from another thread starts the activation on the runtime loop.
- [x] Full gate passes.
- [ ] On the robot: bending either antenna wakes him; the 10-minute peak deviation stays well under 14°
      through breathing, speech and dances.

## Test strategy

`tests/test_moves.py` (detector and worker), `tests/test_hybrid_runtime.py` (touch activation and the
thread-safe entry). On the robot: touch each antenna awake and asleep; read `Antenna touched` and the
`largest untouched deviation` lines in the dated app log.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-28 | Pollen's antenna watcher reviewed (daemon-only, idle-only); built and tested in our app. |
