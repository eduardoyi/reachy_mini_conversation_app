# Plan: Wake antenna cue

- Status: `in-progress`
- Last updated: 2026-10-25
- Feature doc: [Wake cue (antennas)](../features/wake-cue.md)

## Goal

Make it obvious that Reachy heard the wake word, the way a smart speaker lights
up. The previous acknowledgment (a 0.18 s, 4° nod) was easy to miss.

## Decisions

- Use the antennas: they are light and fast, so they react first, and they sit
  above the head where they are visible from across a room.
- Two parts: an instant perk (damped spring with a small overshoot) and a hold
  while listening, released when Reachy starts answering. The hold is the
  "light stays on" state.
- Alert pose ±35° outward, matching Pollen's recorded `attentive` emotions,
  rather than inward, where the antennas could get close to each other.
- Replace the head nod with an 8° chin-up lift (0.35 s).
- Implement in the antenna layer of `MovementManager`, not as a queued move, so
  it is not delayed behind wake or gaze moves.
- A sound cue (earcon) is left for later.

## Acceptance criteria

- [x] Wake flicks the antennas to the alert pose with a visible overshoot.
- [x] Alert pose holds while listening and until Reachy answers.
- [x] Speaking, conversation close, sleep and an 8 s timeout release it smoothly.
- [x] Antenna speed is capped (6 rad/s) when waking from the folded sleep pose.
- [x] Unit tests, full suite, ruff and mypy pass.
- [ ] On the robot: the cue is clearly visible, and the antenna motion is not
      audible to the mic in a way that disturbs listening.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-25 | Implemented on `feat/antenna-wake-cue`. `tests/test_moves.py` 29 passed; full suite 1,939 passed, 4 opt-in skips; ruff, format, mypy clean. Robot check pending. |
