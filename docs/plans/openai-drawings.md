# Plan: Drawings with GPT Image 2.5 Flare

- Status: `in-progress` (built and tested; live check on the TRMNL pending)
- Last updated: 2026-10-27
- Feature doc: [E-ink display (TRMNL)](../features/eink-display.md)

## Goal

Switch the TRMNL drawings and camera photo edits from Google's `gemini-3.1-flash-lite-image` to
OpenAI's `gpt-image-2.5-flare` (household request, 2026-10-27), keeping Gemini one `.env` line away.

## Scope

- `DISPLAY_IMAGE_MODEL` defaults to `gpt-image-2.5-flare`. A model named `gpt-image-*` draws through
  the OpenAI Images API with `OPENAI_API_KEY`; any other name stays on Gemini with `GEMINI_API_KEY`.
- Same prompts, cache folder (`drawings-v3`), 20 s timeout, four-gray clean-up and error handling.
- Out of scope: web pictures (Brave + Jev) and the music card are unchanged.

## Decisions

| Question | Choice |
|----------|--------|
| Size and quality | `1024x768` (4:3, as before), `medium`. Low left blotchy fills on the e-ink screen; medium was clean (giraffe test, 2026-10-27) |
| Speed and cost | Drawing 11.9 s, 113 text in / 301 image tokens out ≈ $0.009; photo edit 10.4 s, 191 text + 768 image in / 301 out ≈ $0.017 (Gemini drawings were ≈ $0.034) |
| Photo edits | Also OpenAI: the camera photo of the family now goes to OpenAI instead of Google when someone asks for an edit; plain photos still stay local |
| Rollback | `DISPLAY_IMAGE_MODEL=gemini-3.1-flash-lite-image` in `.env` |
| Errors | 402/429 → out of credits or busy; 400 (moderation) → may have been blocked; other errors and connection failures → did not answer |
| Dependencies | None new: the `openai` SDK (2.28.0) is already used |

## Implementation order

1. `EInkDisplay` takes `openai_client` and picks the provider by the model name.
2. `main.py` builds `AsyncOpenAI` only for a `gpt-image-*` model; price row in `usage_costs.py`.
3. Tests with fake Images API clients; README, `.env.example` and feature doc.
4. Deploy to the Mac mini and ask for a drawing and an edit by voice.

## Acceptance criteria

- [x] A `gpt-image-*` model draws with `generate` and edits with `edit` at 1024x768 medium; tokens and
      cost recorded; drawings cached, edits not.
- [x] Credit, rate, moderation and server errors and a missing key become `DrawingError`s.
- [x] Gemini still works when configured (existing tests unchanged).
- [x] Full gate passes.
- [ ] On the Mac mini: a spoken drawing and photo edit show on the TRMNL.

## Risks

- Privacy: photo edits now leave the house to OpenAI instead of Google.
- Moderation may refuse different subjects than Gemini did; refusals already become a spoken apology.
- Latency: 10-12 s measured, inside the 20 s timeout but slower than Gemini's 2.7 s fox.

## Test strategy

Unit tests in `tests/test_eink_display.py` with a fake Images API (generate, edit, errors, missing key,
no image); the full repository gate; a live voice check on the mini.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-27 | Probed the API (sizes, quality, edits); built the OpenAI path and tests. |
