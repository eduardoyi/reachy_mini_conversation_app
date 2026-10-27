# Plan: Camera photos and photo edits on the e-ink screen

- Status: `in-progress`
- Created: 2026-10-25
- Feature doc: [eink-display.md](../features/eink-display.md#camera-photos)

## Goal

"Take my picture" shows a photo from Reachy's camera on the TRMNL; "make me a pirate", "show me as
an astronaut" redraws that photo with the change, in the screen's bold illustration style.

## Decisions (agreed with the household, 2026-10-25)

| Question | Choice |
|----------|--------|
| Who may use it | Anyone in the household, kids included, on request only |
| Edits | Sent to the image model (`DISPLAY_IMAGE_MODEL`; Google's until 2026-10-27, OpenAI's `gpt-image-2.5-flare` since) with the photo; plain photos stay local |
| Saving | Never: camera photos and edits are not cached; the usage ledger records only token counts |
| Time on screen | 10 minutes, then the resting card; the screen file is deleted then, and never served after |
| Posing | The photo is taken 3 s after the call, while Reachy counts down |

## Model check (2026-10-25, synthetic photos generated for the test, no real people)

`gemini-3.1-flash-lite-image` edited a made-up 4-year-old (pirate, astronaut, as a dinosaur), a
made-up adult (party hat, superhero) and a child's drawing with no refusals, in 3.6–6.3 s (one 15.6 s
outlier). Asking for the change as the main thing on a plain white background made weak edits clear
(the hat, the dinosaur) and kept rooms from cluttering the four greys. Turning a child's drawing into
a real scene did not work (it redraws the drawing).

## Design

- `show_on_display kind=camera` with optional `edit` (English) and `caption`.
- The tool waits `CAMERA_COUNTDOWN_S`, captures with `camera_frames.capture_jpeg`, and shows either
  the dithered photo or `EInkDisplay.edit_photo()` drawn like a drawing. A refused or failed edit
  leaves the screen as it was and tells Reachy.
- `EInkDisplay.show(..., expires_after_s=600, private=True)`: `current.json` carries `expires_at`
  and `private`; the device server treats an expired screen as gone (resting card, 404) and deletes
  a private one's file.

## Execution log

- 2026-10-25: Model check above; plan agreed; implementation started.
- 2026-10-25: Built `kind=camera` (countdown, plain photo or edit), `EInkDisplay.edit_photo` (shares
  the drawing call, never cached), `expires_at`/`private` screens in the spool and device server.
  Full gate clean (1,923 passed). Pending: a live voice check with the robot's camera.
- 2026-10-25: First voice test ("draw me as a dinosaur") showed a dinosaur costume that looked nothing
  like the person: Reachy had looked with the `camera` tool and then made a plain `drawing` from text,
  so no photo was used. The tool description and profile now send every "draw/dress/show me as …"
  request to `kind=camera` with `edit`; live, two runs each: "dibújame como un dinosaurio", "¿cómo me
  vería de pirata?", "disfrázame de astronauta", "make me a superhero" → camera edits (8/8), "hazme una
  foto" → plain photo (2/2), "draw me a dinosaur" → drawing (2/2).
- 2026-10-25: Likeness: the edit prompt now asks for each person's real features exactly as in the
  photo, adding nothing (naming glasses made the model add them), more detail in faces, and a full
  costume with their own face showing when they become an animal or character. On synthetic photos a
  bearded man's pirate and a boy's and man's dinosaur costumes became clearly them.
- 2026-10-25: Live: "draw me as …" photo edits worked for an adult (about 10 s). "Enzo as Spiderman"
  first failed because Gemini's prepaid credits ran out (conversation could not start, then a 402
  from the image model crashed the tool), then came back empty after credits were added (a named
  character, likely refused). Image-model errors now become a polite `DrawingError`; the tool asks for
  costume descriptions instead of character names; a conversation that cannot connect now says so.
