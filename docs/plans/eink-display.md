# E-ink display (TRMNL) for drawings, words and short text

- Status: `in-progress`
- Owner: Reachy project
- Created: 2026-10-24
- Last updated: 2026-10-25

## Scope and assumptions

Give Reachy a deliberately slow, low-stimulation screen: a TRMNL OG e-ink display (7.5", 800x480,
four grays on firmware ≥ 1.6; the household device reports 1.8.17). No video, no feed, no apps: the
screen changes only when someone asks, and otherwise shows a resting "¡Hola! Soy Reachy" card.
Develop on the MacBook Pro; the robot moves to a Mac mini later (only the display's server URL
changes). The household speaks Spanish; the kids learn Italian, so word cards give Spanish-style
pronunciation and the Spanish meaning.

Assumptions: the TRMNL stays on USB power (60 s check-ins), on the same LAN as the Mac. Gemini
image generation uses the existing `GEMINI_API_KEY`.

## Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Server | The app serves the TRMNL "bring your own server" API itself (`/api/setup`, `/api/display`, `/api/log`) on `TRMNL_PORT` | TRMNL's cloud limits refreshes to ≥ 15 min and webhooks to 12/hour; the device polls a LAN URL set in its captive portal |
| HTTP stack | Standard-library `ThreadingHTTPServer` in a daemon thread | Three tiny endpoints; no new dependency; runs with or without `--ui` |
| Image format | 800x480 2-bit grayscale PNG (four levels 0/85/170/255), < 90 KB | What the OG draws (TRMNL ImageMagick guide); encoded with zlib + numpy |
| Drawing model | `gemini-3.1-flash-lite-image` (Nano Banana 2 Lite) | Listed for this key; 2.9 s for a 1200x896 drawing in a live test (2026-10-24); same API key; Google's safety filters |
| Drawing style | Fixed prompt: friendly children's line art, bold black outlines, flat light/mid grey, white background, no text | Looks right in four grays; also the content guardrail; kids choose subjects, never the style |
| Text | Typed by our renderer, never drawn by the model | Image models misspell; Spanish/Italian need accents |
| Font | Atkinson Hyperlegible (OFL, bundled, ~55 KB per weight) | Pillow's built-in font has no accents or ¡¿ñ; designed for legibility |
| New dependency | Pillow (HPND license, no network access, wheels for Linux/macOS/Windows) | Decoding the model's JPEG, scaling, drawing text |
| Speed | Hold each `/api/display` up to 10 s and answer when the screen changes; 1 s sleep between check-ins | The firmware deep-sleeps and reconnects Wi-Fi on every wake (~16 s cycle at a 5 s rate); it stores any server rate without a minimum and does not redraw an unchanged image (firmware source, 2026-10-22) |
| Tool timing | `show_on_display` awaits the drawing (20 s timeout) | Generation takes ~3 s, under the 30 s Live tool deadline; the result is honest (shown or failed) |
| Cost guard | 20 new drawings a day (`DISPLAY_DRAWINGS_PER_DAY`); a subject drawn before comes from the cache and does not count | ~$0.034 per drawing |
| Resting screen | Cartoon Reachy (generated from photos of the real robot) with "¡Hola! Soy Reachy / Hi, I'm Reachy"; returns 30 min after the last screen | The user chose a greeting over a blank page |

## v1 screens

1. **Drawing**: a requested subject in the fixed style, with a caption.
2. **Word card**: a word in large type, Spanish-style pronunciation, Spanish meaning, an optional
   small drawing, and an optional header. Also used for "how do you say X in Y" translations.
3. **Text**: one big line (a name, a sum) or a short titled list of up to six lines.

Deferred: v2 web pictures (any source; vision check; classical art with nudity allowed), v3
multi-panel visual explanations, bedtime stories, morning page, calendar.

## Implementation order

1. Spike in a scratch directory: a server returning one PNG; the TRMNL pointed at the Mac. Done.
2. Renderer and PNG encoder; resting screen.
3. Device server and screen state (idle return, unique filenames, image paths).
4. Drawings: Gemini call, cache, daily cap, usage ledger.
5. `show_on_display` tool, config gate, `ToolDependencies.display`, startup/shutdown in `main.py`,
   profile guidance.
6. Tests, feature doc, README and `.env.example`; full gate.
7. Live check on the TRMNL: resting screen, each screen kind by voice.

## Acceptance criteria

- With `TRMNL_ENABLED=1` the TRMNL shows the resting screen within one check-in, and the tool is
  offered to the model; without it, no server starts and the tool is absent.
- "Draw a dinosaur" shows a drawing with a caption within about a minute; asking again reuses it.
- Word, translation and text cards show correct accents.
- After the daily cap, new drawings are refused politely; cards still work.
- A failed or blocked drawing leaves the screen unchanged and tells the model so.
- Screens return to the resting card 30 minutes after the last change.
- No conversation content is logged; the server never serves anything but the current screen.

## Risks

- LAN exposure: any device on the LAN can fetch the current screen (drawings and short text, no
  audio or camera data). Acceptable for a home LAN; documented.
- The TRMNL caches images by filename; a reused filename would hide a new screen. Every screen
  gets a new name.
- Laptop IP changes break the display until re-pointed; the Mac mini should get a fixed address.

## Test strategy

Unit: PNG format (2-bit grayscale, size, < 90 KB), each layout renders, text fitting with long
input, screen state and idle return with a fake clock, filename rules, drawing cache and daily cap
with a fake Gemini client, blocked/failed generation. Integration: the HTTP server on an ephemeral
port answers setup/display/image like the device expects. Tool: no display, each kind, cap reached,
generation failure. Gating: tool present only with `TRMNL_ENABLED`.

## Execution log

- 2026-10-24: Spike served a 1-bit, then 2-bit PNG to the household TRMNL (FW 1.8.17) from the
  MacBook; check-ins every ~65 s with `refresh_rate: 60`; the device reused the cached image when
  the filename did not change.
- 2026-10-24: `gemini-3.1-flash-lite-image` drew a dinosaur in 2.9 s (1200x896 JPEG, 1,407 output
  tokens). Pillow added (`uv lock` needed temporary pkg-config files for macOS zlib/bzip2/expat/ffi
  to build pygobject metadata; no project change).
- 2026-10-25: v1 implemented (`eink_display.py`, `tools/show_on_display.py`, gate, config, profile
  guidance, docs). Full gate: ruff, ruff format, mypy (99 files) clean; `pytest tests/` 1,817
  passed, 4 skipped. A first run failed 20 realtime-backend tests because `ToolDependencies.display`
  used a type-only forward reference that pydantic (via `ToolCallRoutine`) could not resolve; it is
  now `Any` like the other optional services.
- 2026-10-25: Live check on the household TRMNL from the MacBook, using the real module and tool
  (not the full app): resting card, then a drawing ("a friendly fox", 2.7 s), a word card reusing
  the cached fox (0.0 s), and a list; each fetched at the next check-in (~70 s apart), then back
  home. Pending: a spoken request through the running app.
- 2026-10-25: Check-in speed on the household TRMNL (time from `show()` to the device fetching the
  image): 60 s rate ~35 s average; 5 s rate: 16 s cycle, median 5.7 s, max 9.7 s; held check-ins
  (10 s) with a 1 s rate: 12.5 s cycle, 0–2 s when a screen follows a quiet spell, ~5.5 s when shown
  6 s after the previous one; median 3.8 s, max 7.8 s over 8 screens. Firmware read timeout is 15 s.
