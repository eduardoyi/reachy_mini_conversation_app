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
| v2 picture search | Brave Image Search, strict SafeSearch; only Brave's proxied thumbnails (`imgs.search.brave.com`, ~500 px) are downloaded | Whole-web coverage (user wanted more than curated sources); thumbnails avoid slow or blocking sites, huge files and requests to LAN addresses from crafted results; ~$0.005 a search, $5 monthly credit |
| v2 picture check | Jev (TypeSafe), one request with seven Noul questions; limits in code (see the feature doc); top 6 checked in parallel, best-ranked approved one wins | User chose Jev over Gemini and OpenAI moderation; narrow per-risk questions per TypeSafe guidance; nudity vs fine art split so the art exception is explicit policy, not a hint inside one question; limits calibrated on real searches |
| v2 fallback | No approved picture, no keys, a used-up daily limit (30) or > 12 s: draw the subject instead | The child always gets something; drawing stays within the 30 s tool deadline |
| Resting screen | Cartoon Reachy (generated from photos of the real robot) with "¡Hola! Soy Reachy / Hi, I'm Reachy"; returns 30 min after the last screen | The user chose a greeting over a blank page |

## v1 screens

1. **Drawing**: a requested subject in the fixed style, with a caption.
2. **Word card**: a word in large type, Spanish-style pronunciation, Spanish meaning, an optional
   small drawing, and an optional header. Also used for "how do you say X in Y" translations.
3. **Text**: one big line (a name, a sum) or a short titled list of up to six lines.

v2 adds **pictures**: a real photo or artwork from the web, checked for young children, with its
source site in small type; drawn instead when none passes.

Deferred: v3 multi-panel visual explanations, bedtime stories, morning page, calendar.

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
- 2026-10-25: v1 committed (`44715e6`). v2 started: user chose Brave Image Search and, mid-design,
  Jev instead of Gemini Flash-Lite for the check. TypeSafe's model page lists Jev 1.14/1.13 as
  text-input only while the API documents image assets behind `TypeSafe-Danger-Zone-Images: true`
  ("image assets require a vision-language model"), so image support must be confirmed live.
  Built `web_pictures.py`, `render_photo` (autocontrast + Floyd–Steinberg to four grays; a noisy
  full-screen photo encodes to 65 KB of the 90 KB limit), `EInkDisplay.picture` (cache with the
  source site, own daily limit, 12 s timeout) and the `picture` tool kind with drawing fallback.
  Pending: `BRAVE_API_KEY` and `TYPESAFE_API_KEY` in `.env`, then a live check of real searches,
  Jev's verdicts on real thumbnails, and the thresholds.
- 2026-10-25: Live v2 check with real keys. Jev reads images: a dinosaur drawing vs a Reachy photo
  scored 0.98/0.02 for "shows a dinosaur" (robot question reversed), 0.2–0.4 s per check, ~800
  input tokens. First policy (five questions, every risk ≤ 0.2) passed art, landmarks and rockets but
  rejected all 20 animals/volcanoes ("frightening" judged the subject) and most classical nudes
  (sexual 0.23–0.29); "tiger" returned only logos. Jev returned 400 "Invalid image dimensions" for
  500x266/500x273 thumbnails; probing showed short side ≥ ~280 and long side ≤ ~1000 accepted.
  Reworded "frightening" to a children's-nature-book standard, split nudity/fine_art/sexual, widened
  "cluttered" to logos and clip art, set frightening ≤ 0.45, cluttered ≤ 0.3, sexual ≤ 0.4 for
  confident fine art, six candidates, resizing to 300–1000 px. Rerun: all David and Venus candidates
  but a collage pass; "a tiger in the wild, photo" picks a calm tiger (snarling 0.87, yawning 0.61
  rejected); spiders and volcanoes pass calm shots; dolphin clip art rejected as cluttered.
- 2026-10-25: Through the tool on the TRMNL: Mona Lisa and a tiger, each found and checked in
  1.1–1.4 s and fetched by the device ~1 s later. The device then reported "File writing ERROR":
  its purge keeps images unless a newer one shares the first 13 characters, and every screen had a
  new id, so photos filled its flash. Shown screens now share `reachy-000001`; the rerun reported
  no write errors. Full gate: 1,844 passed, 4 skipped.
- 2026-10-25: Household policy from the user: open-jawed sharks and roaring dinosaurs are fine;
  block violence, mature content and nudity (fine art still allowed). Replaced "frightening" with
  "mature" (horror, gore, drugs, alcohol, smoking, gambling, crude gestures; fierce animals and art
  explicitly fine) and reworded "violent" (attacks, prey being caught, blood; open jaws are not
  violence). Real searches: sharks 5/6 and T. rex 4/6 pass (a bloody T. rex 0.64 and a cluttered one
  rejected), tigers roaring 6/6, Venus 5/5; horror monsters, zombies, smoking, beer, boxing and
  battle paintings 0 passed (mature 0.84–0.98 or violent 0.45–0.98); "lion hunting a zebra" passed
  only a distant watching lion (violent 0.30) and a calm pair (0.12), all kills and chases rejected
  (0.59–0.98). Limits: violent ≤ 0.3, mature ≤ 0.4.
- 2026-10-25: First real use: a "cocodrilo de verdad" picture was too dark to make out on the TRMNL.
  Jev now gets a second image per candidate, the `render_photo` screen version, and a
  `clear_on_screen` question (≥ 0.5); the clearest approved candidate wins instead of the top-ranked.
  Real searches: the dark pexels crocodile 0.26–0.27 rejected and a clear one chosen; a tiny cat in
  a dark corner 0.27 rejected; the Mona Lisa on a Louvre wall 0.19 rejected and a close-up (0.71)
  chosen; ducks 0.73–0.87; most night-sky shots 0.30–0.46 rejected. Review also found the nudity
  question counted bare skin (a hand near a crocodile 0.51, the Mona Lisa 0.15–0.31); narrowed to
  bare breasts, buttocks, genitals or underwear: hand 0.05, Mona Lisa 0.03–0.13, David still
  0.76–0.98 (allowed as fine art), swimwear beach shots 0.10–0.34.
- 2026-10-25: The v1 commit fails `tests/test_profile_paths.py::test_wheel_file_paths_stay_within_windows_budget`
  (the wheel test builds from tracked files, so it passed before the font was committed): the font
  path was 73 characters against a 71 budget. Fonts renamed `Atkinson-Regular.ttf`/`Atkinson-Bold.ttf`.
  Full gate after v2: ruff, format, mypy (100 files) clean; 1,836 passed, 4 skipped.
