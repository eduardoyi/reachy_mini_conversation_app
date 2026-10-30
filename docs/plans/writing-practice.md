# Plan: Writing practice on the TRMNL

- Status: `in-progress` (built and tested; live check on the Mac mini pending)
- Last updated: 2026-10-30
- Feature doc: [E-ink display (TRMNL)](../features/eink-display.md)

## Goal

"Richie, enséñame a escribir PAPÁ": the screen shows one capital letter at a time, very large, with the word
filling in above it. The child copies it on paper; a pat on Reachy's head, or "¡ya!", "listo", "siguiente",
shows the next. Reachy names each letter and its sound ("Ahora, la eme. Suena mmm, como en mamá."). For a child
learning to write in Spanish. No image model, and no cloud conversation while he writes.

## Scope

- Capitals on plain paper (no guide lines); single words or short phrases up to 12 letters (spaces not
  counted), a space being its own step.
- Spanish letter names and sounds, the sound following the word where Spanish changes it (CA/CE, GA/GE, CH,
  LL, QU, RR, silent H).
- Out of scope: stroke order, lowercase or cursive, other languages, checking what he wrote.

## Decisions

| Question | Choice |
|----------|--------|
| Starting | A local quick command ("enséñame a / ayúdame a / quiero / vamos a escribir X"): no cloud. Anything longer than 12 letters, or that needs thinking ("el nombre de la abuela", "algo bonito"), goes to Gemini, which calls `show_on_display kind=writing` (suggesting something shorter first when too long) and ends the conversation at once |
| Next letter | A pat on his head (the head is held still and the voice-driven head wobble is off while he writes, so a touch shows), or a room transcript that is only "ya", "listo", "siguiente"…, heard without his name. "Richie, siguiente" also works. Two seconds between steps, so a pat with a "¡ya!" is one step |
| Back, end | "otra vez" / "atrás" shows the previous letter; "ya terminé", 10 minutes without a step, or any other request to Reachy ends it |
| Reachy's voice | Each phrase is an announcement (spoken once Reachy is free) in the quick-command voice; every letter phrase is a fixed line, cached on disk the first time, so a word costs nothing after the alphabet has been heard once. The closing "¡Muy bien! Has escrito papá." is the one uncached line per word |
| The screen | Owned by "writing" while it runs, so the music card does not replace it; the finished screen and one left after "ya terminé" go back to the usual rules |
| Cost | A word started by voice and finished by pats costs nothing; a request Gemini handles costs one short turn. Local Whisper already transcribes the room |

## Implementation order

1. `writing_mode.py`: letters, phrases, `WritingMode` (start, hear, stop, the touch and idle watch).
2. `eink_display.render_writing()`; `now_playing` holds a "writing" screen.
3. `HybridRuntime.set_room_listener()`; quick command `write`; `show_on_display kind=writing`; wiring in `main.py`
   (cacheable phrases, ending on a new conversation).
4. Tests, feature doc, README, profile.
5. Deploy to the Mac mini; try a word with pats and with "ya".

## Acceptance criteria

- [x] "Enséñame a escribir papá" starts locally and shows P with the word row above; a pat or "ya" shows A.
- [x] "Atrás" goes back; "ya terminé" ends; 10 minutes idle ends; another request ends it.
- [x] Phrases name the letter and its sound, following CE/CI, GE/GI, CH, LL, QU, RR and silent H.
- [x] More than 12 letters, or a name to look up, goes to Gemini; the tool refuses too-long text with a hint.
- [x] The music card does not replace the screen while he writes.
- [x] Full gate passes.
- [ ] On the Mac mini: a word written with pats and with "¡ya!", heard without false steps.

## Risks

- "Ya" is short and common: a room transcript counts only when it is just a writing word. Pats are the main
  way on.
- Holding the head still stops breathing and speaker turns for the whole word (at most 10 minutes idle).
- Gemini may leave the conversation open; announcements then wait until it closes (60 s inactivity).

## Test strategy

`tests/test_writing_mode.py`: letters, phrases, steps by touch and by word, back, end, idle, cooldown, the
screen owner, head held and released. Quick command parsing and hand-off, the runtime's room listener, the
music card hold, the tool kind and the screens in their existing test files. Then the gate and a live try.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-30 | Designed with the household (capitals, plain paper, name and sound, pats and "ya", cheap: no long Gemini session). Built and tested. |
| 2026-10-30 | Full gate on the MacBook: ruff, format and mypy clean; 2,277 passed, 4 skipped. The Mac mini runs `continuous_transcript` room transcription with quick commands on, which writing words without the name need. Not deployed yet; nothing tried with the robot. |
