# Plan: Drawing lessons on the TRMNL

- Status: `in-progress` (built and tested; live voice check on the TRMNL pending)
- Last updated: 2026-10-30
- Feature doc: [E-ink display (TRMNL)](../features/eink-display.md)

## Goal

"Enséñame a dibujar una jirafa" / "teach me how to draw a shark" shows a one-screen lesson like a children's
how-to-draw book: four numbered panels of the same simple drawing growing step by step, the new lines black and
the earlier ones grey, each with a short caption. It is for children aged 3 to 5, so the drawing is a cute,
simplified version made of shapes a small child can copy. "Draw me a giraffe" still makes a plain drawing.

## Scope

- `show_on_display kind=lesson` with `subject` (English) and `title` (their language, e.g. "Cómo dibujar una
  jirafa"). Reachy only says the steps are on the screen; he does not read them out.
- One screen, exactly four steps. Faces only on things that have faces (people, animals, aliens, monsters,
  characters), never on objects.
- Out of scope: one step per screen, Reachy pacing the steps by voice, more than four steps.

## Decisions

| Question | Choice and evidence (2026-10-30, six subjects: giraffe, shark, cat, house, rocket, butterfly) |
|----------|------|
| How the steps stay the same drawing | The image model draws one 2x2 sheet and the panels are cut from it. Separate drawings per step (as `kind=steps` makes) never match. A text model placing shapes that our code draws was always consistent but looked stiff (giraffe, shark) and took 9–19 s with GPT-6.1 Sol, 22–37 s with Gemini 3.8 Flash |
| New and earlier lines | The prompt asks for earlier lines light grey in every panel, the last included; without it the last panel came back all black in 4 of 6 |
| Panel size | Each panel is trimmed to the same box around the drawing in all four, so the drawing fills the panel and does not move between steps |
| Captions | A vision call on the finished sheet (Gemini 3.8 Flash, low thinking), so captions describe what each panel really adds. 2.2–5.3 s; matched the panels in 12 of 12. gpt-6-luna was 1.6–3.4 s without reasoning but gave long captions (up to 17 words) and one wrong one (two steps "Dos aletas para nadar"); with low reasoning 4–10 s |
| Faces | The first prompt said "faces for animals" and both houses and rockets still got faces; the household chose faces only on people, animals, aliens, monsters and characters |
| A broken sheet | 1 of 12 round-two sheets drew later lines faintly in step 1. Grey away from black lines in panel 1 measured 0 on 17 sheets and 1.5x the black on that one; such a lesson is still shown but not cached, so asking again draws a new one. No retry: a second sheet (~10 s) would not fit the tool's time |
| Speed and cost | 11–16 s from request to screen in testing; ≈ $0.009 per sheet plus ≈ $0.001 per caption call. Lessons are cached per subject, captions per title, so a repeat is instant |
| Slow captions | Live, 2 of 12 caption calls took over 8 s (8+ and 13.7 s; the rest 2.3–3.5 s), with no pattern by sheet. A longer wait would not fit the 30 s tool deadline, so a second request starts after 4 s and the first answer wins; after 10 s in all the panels show without captions |
| Caption model client | The Gemini client is now built whenever `GEMINI_API_KEY` is set, not only for a Gemini image model |

## Implementation order

1. `eink_display.py`: `LESSON_PROMPT`, `LESSON_CAPTION_PROMPT`, `EInkDisplay.lesson()` (sheet, panels, captions,
   cache, step-1 check) and `render_lesson()`.
2. `show_on_display.py`: `kind=lesson`, description routing, one picture at a time.
3. `main.py` builds the Gemini client for captions; Gemini 3.8 Flash price row in `usage_costs.py`.
4. Tests with fake Gemini and OpenAI clients; feature doc, README.
5. Deploy to the Mac mini and ask for a lesson by voice in Spanish and English.

## Acceptance criteria

- [x] A lesson shows four numbered panels cut from one sheet, each with a caption in the title's language.
- [x] The same subject again comes from the cache; a new title only asks for captions.
- [x] Failed captions still show the panels; a failed sheet is a `DrawingError` Reachy explains.
- [x] A sheet with later lines in step 1 is shown but not cached.
- [x] "Draw me X" still uses `kind=drawing`; the tool description routes "teach me to draw".
- [x] Full gate passes.
- [ ] On the Mac mini: a spoken "enséñame a dibujar…" shows a lesson and Reachy does not read the steps.

## Risks

- The image model sometimes adds too much in one step (cat: body, legs and tail at once). Still usable; the
  prompt asks for even steps.
- Captions depend on the vision model reading thin lines; seen correct on all twelve test sheets.
- A Gemini outage leaves lessons without captions (panels only).

## Test strategy

`tests/test_eink_display.py`: the lesson layout renders; panels, captions, cache and cost records with fake
Gemini and OpenAI clients; failed captions; the step-1 check. `tests/tools/test_show_on_display.py`: the lesson
kind, its required fields and its instruction. Then the full gate and a live voice check.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-30 | Compared shapes from a text model with an image-model sheet, then a second round with grey earlier lines, trimmed panels and vision captions; chose the sheet. Built `kind=lesson` and tests. |
| 2026-10-30 | Real APIs on the MacBook, not the TRMNL: house (no face, as asked), rocket and giraffe (English title, English captions) in 12.6–18.1 s; the house's captions timed out at 8 s, so a second caption request was added. With it, shark and cat in 13.4 and 12.4 s with correct captions. Full gate: ruff, format and mypy clean; 2,239 passed, 4 skipped. |
