# Feature: E-ink display (TRMNL)

- Status: `in-progress` (drawings, web pictures, word cards, text and visual explanations checked live on the household TRMNL; pictures used by voice; explanations and drawing lessons not yet asked for by voice)
- Last updated: 2026-10-30
- Plan: [docs/plans/eink-display.md](../plans/eink-display.md)

## Overview

Reachy shows drawings, checked pictures from the web, word cards, short text, step-by-step visual
explanations and drawing lessons on a TRMNL OG e-ink display (7.5", 800x480, four grays). It is a deliberately slow, low-stimulation screen: no video, no feed, no apps. The
screen changes only when someone asks; otherwise it shows a resting card with a cartoon Reachy and
"¡Hola! Soy Reachy / Hi, I'm Reachy".

Out of scope so far: stories, scheduled pages such as a morning page or calendar (see the plan).

## User-visible behavior

- "Draw me a dinosaur": Reachy says to look at the screen; a clean, simple illustration (accurate
  shapes with only the main details, bold outlines, large flat greys, no cartoon faces) with a caption
  appears a few seconds later (about 3 s to draw, usually 0–2 s for the TRMNL to fetch it, then the
  e-ink redraw). Asking for the same subject again reuses the saved drawing.
- "Show me the Mona Lisa", "what does a Falcon 9 look like?": a real picture from the web, dithered
  to the four grays, with its source site in small type. Brave Image Search (strict SafeSearch)
  finds candidates and Jev checks each shows the subject and has no violence, mature content or
  nudity; classical and fine-art nudity is allowed, and fierce animals (open-jawed sharks, roaring
  dinosaurs) are fine, by the household's choice. If none passes, the keys are missing or
  it takes over 12 s, the subject is drawn instead and Reachy is told to say why, in plain words:
  the pictures found were not right for children, would be too hard to see, did not clearly show it,
  or the search or check did not work (when candidates fail for different reasons the most important
  one is given, safety first). A subject shown before comes from the cache.
- "How does a seed grow?", "why does it rain?": a visual explanation, a title over 2–4 numbered
  panels, each with its own drawing and a short caption in the child's language (one row for two or
  three steps, two rows of two for four). The drawings are made at the same time (measured: four in
  3.0 s, a 9 KB screen). A panel whose drawing fails shows only its words, and Reachy is told.
- "Enséñame a dibujar una jirafa", "how do I draw a shark?": a drawing lesson for children aged 3 to 5, like
  a how-to-draw book. A title over four numbered panels, each the same simple drawing one step further (the
  new lines black, the earlier ones grey) beside a short caption in the title's language naming the shapes
  ("Un óvalo grande para el cuerpo"). Faces only on things that have faces (people, animals, aliens,
  monsters, characters), never on objects. Reachy only says the steps are on the screen. About 11–16 s the
  first time; the same subject again is instant. Plan: [drawing-lessons.md](../plans/drawing-lessons.md).
- "Enséñame a escribir PAPÁ": writing practice, one big capital at a time for a child to copy on paper; a pat
  on Reachy's head or "¡ya!" shows the next. See [Writing practice](#writing-practice).
- Teaching an Italian word, or "how do you say rainbow in Italian?": a word card with the word in
  large type, its pronunciation written for a Spanish speaker ("la MÉ-la"), the Spanish meaning, an
  optional header and an optional small drawing. If the drawing fails the card shows without it.
- "Write MARA big", "put up the shopping list", "show 3 + 4": one big line, or a title over up to
  six lines; longer text is cut with "…".
- "Go back to the start screen": the resting card. It also returns 30 minutes after the last screen.
- There is no daily cap on new drawings or picture searches (removed 2026-10-25 at the household's
  request); a subject drawn or found before still comes from the cache.
- One picture at a time: while a drawing, web picture, explanation, camera photo or word-card picture
  is being made, another such request is refused at once and Reachy says the first one is coming (text,
  home and the song card still show at once). Added 2026-10-27 after one "draw me as a Minecraft
  character" turned into two edits and a plain drawing while people kept talking during the 10-15 s wait.
- A drawing that fails (no API key, blocked by the image model's filter, over 20 s) leaves the screen as it
  was, and Reachy is told to say so and not retry.
- If the TRMNL has not checked in for over 30 s (plus the refresh rate), the screen is still set,
  but Reachy is told to say the screen is napping and will show it within a few minutes, or at once
  if someone presses the button on its back. The firmware sleeps a fixed 5 minutes after any failed
  check-in (a Wi-Fi drop, the Mac asleep, the server being replaced or the server's first start).
- Restarting Reachy does not disturb the screen: the device server runs in its own process and keeps
  answering the TRMNL, and the screen shown before the restart stays until it is replaced or idles out.

## Setup

1. `.env`: `TRMNL_ENABLED=1` (and optionally `TRMNL_PORT`, `TRMNL_REFRESH_SECONDS`). Drawings and photo edits use
   `OPENAI_API_KEY` with the default `gpt-image-2.5-flare`, or `GEMINI_API_KEY` with a Gemini image model.
2. On the TRMNL, hold the back button until the setup screen shows; join the `TRMNL-xxxx` Wi-Fi
   from a phone. On the setup page tap **Advanced** and set **API Server** to
   `http://<computer's LAN IP>:8090` (no trailing slash), then **Back**, choose the home Wi-Fi
   (2.4 GHz) and **Connect**. This takes the device off TRMNL's cloud; to go back, clear the field.
3. Allow incoming connections for Python if macOS asks. Keep the TRMNL on USB power: with the
   default 1 s sleep and held check-ins its Wi-Fi is on most of the time, which would drain the
   battery in hours. On battery set `TRMNL_REFRESH_SECONDS` to several minutes.

### Web pictures (v2)

```mermaid
flowchart LR
  Tool -->|kind=picture| Pic[EInkDisplay.picture]
  Pic -->|cache miss| Find[WebPictures.find]
  Find -->|q, safesearch=strict| Brave[Brave Image Search]
  Find -->|top 4 thumbnails, imgs.search.brave.com only| Thumbs[download]
  Thumbs -->|each as a jpg asset, in parallel| Jev[Jev: 5 yes/no questions]
  Jev -->|best-ranked approved| Pic
  Pic -.->|PictureError| Draw[draw instead]
```

Jev's questions (all Noul, one request per candidate, state `{subject, audience, image}`), and the
policy applied in code (`WebPictures._approved`, calibrated on real searches 2026-10-25):

| Question | Rule |
|----------|------|
| `shows_subject` | ≥ 0.7 |
| `nudity`, `fine_art` | nudity ≤ 0.2, or fine_art ≥ 0.8 (classical and fine-art nudes allowed). Nudity means bare breasts, buttocks or genitals or underwear, not bare hands or faces (a hand in a crocodile photo scored 0.51 under broader wording, 0.05 now) |
| `sexual` | ≤ 0.2; ≤ 0.4 when fine_art ≥ 0.8 (Venus and David score 0.19–0.29 while judged fine art) |
| `violent` | ≤ 0.3: fighting, attacks, prey being caught, blood, injury; fierce animals with open jaws and roaring dinosaurs are not violence (boxing, battles, zombies, kills, a bloody T. rex 0.45–0.98; roaring T. rex and open-jawed sharks 0.05–0.18). No limit when fine_art ≥ 0.8: famous art may depict violence if it is not graphic |
| `graphic` | ≤ 0.3 for every picture: realistic blood, open wounds, gore or dead bodies. Guernica 0.04–0.07 (shown); Goya's Third of May 0.81–0.90, The Death of Marat 0.77–0.82, The Raft of the Medusa 0.43–0.79, a lion kill 0.48–0.57 (blocked) |
| `mature` | ≤ 0.4: horror, gore, drugs, alcohol, smoking, gambling, crude gestures (smoking, beer, horror, zombies 0.84–0.98; Botticelli's Venus 0.17–0.27) |
| `cluttered` | ≤ 0.3: logos, icons, clip art, text, screenshots, memes, collages (tiger logos 0.80–0.95) |
| `clear_on_screen` | ≥ 0.5 except for fine art (busy paintings such as Guardi's Venice 0.16–0.22 or The Night Watch 0.12–0.20 are faithful reproductions, better than a drawing), judged on a second image, the candidate rendered by `render_photo` exactly as the TRMNL will show it: a dark crocodile 0.26–0.27, a tiny cat in a dark corner 0.27, a Louvre wall with the Mona Lisa small 0.19 are rejected; clear ducks, tigers and close-ups score 0.7–0.87. Among approved candidates the clearest wins (ties go to search rank) |

A missing answer counts against the picture. Six candidates are checked in parallel; the
approved one clearest on screen wins. Every verdict is logged as scores only (no subject). Requests send
the image as a base64 `jpg` asset with `TypeSafe-Danger-Zone-Images: true`. Jev answers HTTP 400
"Invalid image dimensions" for a short side under ~280 px or a long side over ~1000 px, so each
candidate is resized to a 300 px short side within a 1000 px long side first. Brave's plan allows
about one search a second (a burst returned 429, which falls back to a drawing).

## Writing practice

"Enséñame a escribir PAPÁ" (Spanish): the screen shows the letter to copy now, very large, with the word filling
in above it (written letters in black, the current slot underlined in black, the rest grey), "2 de 4" in the
corner, and a space as its own "espacio" step. Reachy says the letter's name and its sound in this word
("Primero, la pe. Suena p, como en pato."; CE/CI, GE/GI, CH, LL, QU, GUE/GUI, RR, soft R and silent H follow
Spanish spelling). The child copies it on paper, then:

- a pat on Reachy's head, or a room transcript that is only "ya", "listo", "siguiente"… (no name needed), or
  "Richie, siguiente" → the next letter, at most one step every 2 s (a pat with a "¡ya!" is one step);
- "otra vez" / "atrás" → the previous letter; "ya terminé" → "Vale, lo dejamos aquí.";
- after the last letter: the whole word with "¡Muy bien!" and "¡Muy bien! Has escrito papá.";
- 10 minutes without a step, or any other request to Reachy (quick command or conversation), ends it.

How it stays cheap: the start is a quick command ("enséñame / ayúdame / quiero / vamos a escribir X") with no
cloud. More than 12 letters (spaces not counted), or something to look up ("el nombre de la abuela"), goes to
Gemini, which calls `show_on_display kind=writing` (or suggests something shorter) and then ends the
conversation. During writing nothing goes to the cloud: the room is already transcribed by local Whisper, and
every letter line is a fixed phrase the reply voice keeps on disk (`writing_mode.WRITING_PHRASES`, 106 lines);
only the closing line, which names the word, is new speech each time.

- `writing_mode.WritingMode` (built in `main.py` when the hybrid runtime and the screen are both on; on
  `ToolDependencies.writing`): `start(text) -> problem | None`, `hear(text) -> bool`, `stop()`, `active`.
- While writing the head is held still and the speech wobble is off (`quiet_head`), so a pat moves it
  (`MovementManager.head_touches`); the hold is renewed every minute, since it lapses after 3. Breathing and
  speaker turns pause meanwhile.
- `HybridRuntime.set_room_listener(writing.hear)`: each final room transcript reaches it before wake detection
  while Reachy waits for his name; it uses only a transcript that is just a writing word.
- `QuickCommands.handle` offers every request to writing first while it runs, and stops it otherwise; a new
  conversation (`on_cloud_session`) stops it too.
- Phrases are spoken as announcements (`request_announcement`), once Reachy is free.
- The screen's owner is `writing`, which the music card leaves alone; the finished word, and a word left after
  "ya terminé", idle or another request, go back to `requested`.
- Plan: [writing-practice.md](../plans/writing-practice.md).

## Now playing

While Spotify plays on Reachy's speaker (`SPOTIFY_DEVICE_ID`), the screen shows a music card: the
album art (dithered to four greys; show art for podcasts) on the left, a small drawn note, the title
in bold, the artist, and the album in grey. Music on any other device is ignored. Plan:
[now-playing-display.md](../plans/now-playing-display.md).

- **Detection** (`now_playing.py`, `NowPlaying`): Spotify has no playback push, so the watcher asks
  `/me/player?additional_types=episode` every 10 s while music plays, right when the song should end,
  every 30 s otherwise, and at once after Reachy's own play, resume, pause or next
  (`SPOTIFY_DUCKER.playback_changed_callback` → `refresh_soon`). It runs as a task on the app's
  event loop (`LocalStream(background_tasks=...)`).
- **Who wins the screen:** the display records each screen's owner (`requested` or `music`). A
  screen someone asked for holds while their conversation is open (`SPOTIFY_DUCKER.in_conversation`);
  after it ends, the music card returns at the next song change or after 2 minutes. The music card
  replaces itself per song, is exempt from the 30-minute idle return while music plays
  (`keep_alive`), and stays 2 minutes after the music stops before the resting card returns.
- **"What song is this?"**: `show_on_display kind=now_playing` puts the card up at once (even over a
  requested screen) and returns `song: {title, artist, album}` for Reachy to say.
- **Album art:** only from `i.scdn.co` over https (the 640 px image), cached per album or show in
  `display/album-art/`. Covers and explicit tracks are shown as they are (household choice; no Jev
  check). A missing cover leaves a card with a note placeholder.
- **Off switch:** `DISPLAY_NOW_PLAYING=0`. Needs `TRMNL_ENABLED` and Spotify (`SPOTIFY_CREDENTIALS_FILE`,
  `SPOTIFY_DEVICE_ID`).
- Logs say "Now playing card updated" and whether it had art, never titles, artists or albums.

## Camera photos

"Take my picture" or "make me a pirate": `show_on_display kind=camera` takes a photo with Reachy's
camera 3 s after the call (Reachy counts down "3, 2, 1") and shows it dithered like a web picture.
With `edit` ("make him a pirate", "show her as an astronaut") the photo goes to the image model
with the change (`PHOTO_EDIT_PROMPT`: the change as the main thing, on white, in the drawing style)
and is shown like a drawing, in about 4–6 s. The prompt keeps each person's real features as in the
photo and turns creatures into a costume with their face showing, so the result looks like them.
"Draw me as…", "how would I look as…" and "dress me as…" always use the photo, never a plain drawing. Plan: [camera-photos-display.md](../plans/camera-photos-display.md).

- **A second path:** with [Photos to WhatsApp](whatsapp-photos.md) set up, a woken "take a picture" is a
  quick command: it shows the photo here the same way (`render_photo`, 10 minutes) and sends it to the
  family group, without Gemini.
- **Anyone may use it**, kids included, but only on request (household choice).
- **Not cached for the screen:** photos and edits are not cached (`edit_photo` bypasses `DRAWINGS_DIR`); the
  usage ledger records only an edit's tokens (`display_photo_edit`). Photos sent to WhatsApp by the quick
  command are kept for the [phone admin app's](phone-admin-app.md) gallery unless `PHOTO_ARCHIVE_ENABLED=0`.
- **Ten minutes on screen:** the spool marks the screen `private` with `expires_at`; after that the
  device server shows the resting card, refuses to serve the photo (404) and deletes its file at the
  next check-in.
- **Edits leave the house:** the photo goes to the image model (OpenAI's `gpt-image-2.5-flare` since 2026-10-27, Google's before); a plain photo does not.
- **Failures:** a refused, slow or failed edit (including the image service being out of credits) leaves the
  screen as it was and Reachy offers the plain photo. Named characters are often refused, so the tool
  asks Gemini to describe the costume instead ("a red and blue superhero suit with a spider-web pattern"). Turning a child's drawing into a "real scene" did not work with the Gemini model (it
  redraws the drawing); not yet tried with GPT Image.

## Architecture

```mermaid
flowchart LR
  Live[Gemini Live] -->|show_on_display| Tool[tools/show_on_display.py]
  Tool -->|drawing subject| Draw[EInkDisplay.drawing]
  Draw -->|cache miss| Model[gpt-image-2.5-flare, or a Gemini image model]
  Tool --> Render[render_drawing / render_word / render_text]
  Render -->|2-bit PNG| State[EInkDisplay.show]
  State -->|PNG + current.json| Spool[(display/screen/)]
  TRMNL -->|GET /api/display, held up to 10 s| Server[eink_server process]
  Server -->|reads screens, writes device.json| Spool
  TRMNL -->|GET /screens/name.png| Server
```

- The device server (`eink_server.py`, standard library only) is its own process: a
  `ThreadingHTTPServer` on `0.0.0.0:TRMNL_PORT`. It outlives the app because the TRMNL sleeps
  5 minutes after a failed check-in and a restarting app is down for about 20 s.
  `EInkDisplay.start()` (from `main.run()`, independent of `--ui`) reuses a running server whose
  fingerprint (hash of `eink_server.py` plus port and refresh rate) matches, and otherwise launches
  `python -m reachy_mini_conversation_app.eink_server` in a new session. A new server SIGTERMs the
  old one, which answers its held check-ins at once and frees the port; the new one binds within
  milliseconds, so a takeover is not a failed check-in. `stop()` leaves the server running. To stop it:
  `.venv/bin/python -m reachy_mini_conversation_app.eink_server --stop`.
- They share the spool folder `~/.cache/reachy-mini-conversation-app/display/screen/`: one PNG per
  screen (only the current screen and the resting card are kept), `current.json` (screen, resting
  card, time shown; replaced atomically), `device.json` (check-in start and end times, which
  `EInkDisplay.awake()` reads), `server.json` (pid, fingerprint) and `server.log`.
- The tool runs on the conversation loop and awaits the drawing (about 3 s; the Live tool deadline
  is 30 s). Rendering and the screen swap are synchronous.
- Speed: the TRMNL deep-sleeps between check-ins and reboots and reconnects to Wi-Fi on every wake,
  so a plain poll is slow (measured: a 60 s rate averaged ~35 s to pick up a screen; a 5 s rate gave
  a 16 s cycle and ~6 s). Instead each `/api/display` request is held for up to `CHECK_IN_HOLD_S`
  (10 s, under the firmware's 15 s read timeout) and answered as soon as `show()` or `show_home()`
  changes the screen (`DeviceServer.screen_for_check_in` polls `current.json` every 0.2 s). With a 1 s sleep the device is
  listening most of the time: measured 0–2 s to fetch a screen shown after a quiet spell, ~5.5 s
  when screens come seconds apart (the device is still drawing the last one).
- The firmware does not redraw an image whose file name it has just shown, so frequent check-ins
  never flash the panel (`display_session.cpp`: "The image hasn't changed since the last wakeup").
- Drawings and pictures are cached as grayscale PNGs in `~/.cache/reachy-mini-conversation-app/display/`
  (`drawings-v3/` for the current style, `pictures/` with the source site in a PNG text chunk), named by the subject folded
  to ASCII ("A Cat" and "a cat" share one).
- Drawing lessons: the image model draws one 2x2 how-to-draw sheet (`LESSON_PROMPT`, through the same
  `_generate` as drawings); `EInkDisplay.lesson()` cuts it into four panels trimmed to one shared box around
  the drawing, and `LESSON_CAPTION_MODEL` (Gemini 3.8 Flash, low thinking) reads the sheet and returns one
  caption per panel in the title's language. A caption answer slower than `CAPTION_HEDGE_S` (4 s) gets a
  second request and the first answer wins; after `CAPTION_TIMEOUT_S` (10 s), an error or an unusable answer
  the panels show without captions. Sheets are kept in `lessons/<subject>.png` and captions per title in
  `lessons/<subject>.json`. A sheet whose first panel has grey away from its black lines (later steps leaked
  in) is shown but neither it nor its captions are kept. Captions need `GEMINI_API_KEY` even when drawings
  use OpenAI.
- Each drawing's tokens go to the usage ledger as feature `display_drawing` (a lesson's sheet as
  `display_lesson`, its captions as `display_lesson_captions`), each Brave search as
  `display_picture_search` (`{"request": 1}`, $0.005) and each Jev check as `display_picture_check`
  (Jev's reported input and output tokens; $0.042 per 1M input, output free). Failed searches and
  checks are not recorded.

## Canonical files and contracts

| Area | File or interface | Responsibility / contract |
|------|-------------------|---------------------------|
| Screens | `eink_display.py` | `render_home/drawing/photo/word/text/steps/lesson/writing/now_playing() -> bytes` (PNG); `encode_png`; `EInkDisplay.show(png, owner, expires_after_s)/show_home/current/owner/keep_alive/awake/drawing/lesson/edit_photo/picture/start/stop`; `lesson(subject, title) -> [(caption, panel)] * 4` |
| Now playing | `now_playing.py` | `parse_playback(body, device_id) -> Playback`; `NowPlaying(display, fetch, in_conversation, art_dir).run/check/show_now/refresh_soon`; `spotify_fetcher(credentials, device)` |
| Device server | `eink_server.py` | Separate process; `DeviceServer(spool, port, refresh_s)`; spool contract `current.json` (incl. `owner`, `expires_at`, `private`) / `device.json` / `server.json`; `current_filename`, `is_awake`, `fingerprint`, `running_pid` |
| Web pictures | `web_pictures.py` | `WebPictures(..., screen_preview=render_photo).find(subject) -> WebPicture(image, source)`; raises `PictureError`. Downloads only from `THUMBNAIL_HOST` |
| Tool | `tools/show_on_display.py` | `kind`: `drawing` / `picture` (`subject`, `caption`), `lesson` (`subject`, `title`; tells Reachy not to read the steps), `writing` (`word`; starts writing practice and asks Reachy to end the conversation, or says it is too long), `word` (`word`, `pronunciation`, `meaning`, `header`, `picture`), `text` (`lines`, `title`), `steps` (`title`, `steps`: 2–4 of `{caption, picture}`), `camera` (`edit`, `caption`), `now_playing`, `home`. Returns `{"ok": True, "note": ...}` or `{"error": ..., "instruction"?: ...}` |
| Gate | `tools/core_tools.py` `DISPLAY_TOOLS` | Present only with `TRMNL_ENABLED`; added automatically unless the profile's tools were customized |
| Dependency | `ToolDependencies.display` | Built by `main._build_display()` |
| Config | `config.py` | `TRMNL_ENABLED` (0), `TRMNL_PORT` (8090, 1024–65535), `TRMNL_REFRESH_SECONDS` (1, 1–3600), `DISPLAY_IMAGE_MODEL` (`gpt-image-2.5-flare`), `BRAVE_API_KEY`, `TYPESAFE_API_KEY`, `DISPLAY_CHECK_MODEL` (`jev-latest`) |
| Assets | `static/eink/` | `reachy.png` (resting-card art, generated from photos of the robot), Atkinson Hyperlegible Regular/Bold (`Atkinson-*.ttf`, short names for the Windows wheel path budget) and its `OFL.txt` |
| Guidance | `profiles/household_companion/profile.md` | When to use the screen (explanations for how and why questions); Spanish-style pronunciation and Spanish meanings for Italian words |

TRMNL device API, as served (firmware 1.8.17 checked):

- `GET /api/setup` → `{"status": 200, "api_key", "friendly_id", "image_url", "message"}`.
- `GET /api/display` → held up to 10 s for a screen not yet sent, then `{"status": 0, "image_url", "filename", "refresh_rate": <int>, "reset_firmware": false, "update_firmware": false, "firmware_url": null, "special_function": "none"}`.
  `refresh_rate` must be an integer: the firmware reads a string as 0 and wakes immediately.
- `GET /screens/<filename>.png` → the current screen or the resting card, else 404.
- `POST /api/log` → 204; the device's report is logged (first 500 characters). The device reports a failed
  image download this way, and can then sit on its error screen while being sent the file name it already has,
  which it never redraws (seen 2026-10-31 after a server restart). So the next check-in after a report sends the
  same screen as `<filename>~<n>` (served like the original) until the screen changes.

## Extension guide

### Safe to extend

- New layouts: add a `render_*` function that returns `encode_png(canvas)` and a `kind` in the tool.
- Lesson style: `LESSON_PROMPT`. A new style needs a new `LESSONS_DIR`. Keep "earlier lines light grey in
  every panel, including the last one" (without it the last panel came back all black) and the 2x2 order,
  which the cutting and the caption prompt rely on.
- Drawing style: `DRAWING_PROMPT`. Keep "no text" and the grayscale wording, or fills speckle. A new
  style needs a new `DRAWINGS_DIR`, or cached drawings in the old style keep showing.

### Do not change without updating this document and its tests

- Filenames are `<name>-<id>-<unix time>` (13-character `<name>-<id>`, at most 31 characters) and
  change with every screen: the TRMNL caches by name, so a reused name hides a new screen.
- Shown screens share one `<name>-<id>` (`reachy-000001`) and differ by timestamp; the resting card
  is `reachy-000000`. The firmware deletes a saved image when a newer one shares its first 13
  characters, otherwise only after 24 h (`filesystem_purge_old_file`). A new id per screen filled
  the device's flash with photos and caused "File writing ERROR" reports (2026-10-25).
- A held check-in must answer within the firmware's 15 s read timeout (`HTTP_DEFAULT_TIMEOUT_MS`);
  a timeout counts as a failed request and the device backs off to its retry ladder.
- PNGs are 2-bit grayscale (color type 0) and at most 90,000 bytes (`MAX_PNG_BYTES`).
- `_LEVEL_EDGES` puts only near-white (≥ 230) on white, so light-grey fills from the image model
  become one even grey. Rendered text uses the exact levels 0/85/170/255.
- Text is always typed by the renderer, never asked of the image model.

## Constraints, privacy, and failure modes

- The server listens on all interfaces without authentication (the TRMNL firmware sends no
  credential when fetching images). Anything on the LAN can read the current screen: drawings and
  short text, never audio, camera frames or memory. The setup `api_key` is a fixed placeholder.
- The current screen and resting card are kept on disk in the spool folder until replaced, so short
  text shown on the screen (a list, a name) stays in `~/.cache` until the next screen.
- Web pictures send the subject words (chosen by the model, never raw speech) to Brave, and web
  thumbnails (never household images) to TypeSafe. Picture checks rely on TypeSafe's experimental
  image input; if Jev rejects or cannot read images, every picture falls back to a drawing.
- Logs carry the screen kind, firmware/model at setup, and battery/signal at debug level, never the
  words or subjects shown.
- A port already in use (or a server that does not start within 5 s) logs an error pointing to
  `server.log`; the app runs without the display. The server log has setup, device reports and
  "TRMNL checking in again after N s"; Reachy's own log no longer shows them.
- Drawing failures leave the screen unchanged; the word card degrades to no picture.
- The TRMNL sleeps 5 minutes after one failed check-in (`SHORT_TERM_SLOW_RETRY_INTERVAL`; API retries
  are fixed at that value in current firmware). `EInkDisplay.awake()` reports it from check-in times,
  and the log says "TRMNL checking in again after N s" when it returns.
- Pillow's built-in font lacks accents and ¡¿ñ, hence the bundled font.

## Dependencies

- Pillow (added for this feature): image decoding, scaling, text. HPND license, no network access.
- `openai` (existing) for drawings and edits with `gpt-image-*` models; `google-genai` (existing) for Gemini image models and lesson captions.
- Atkinson Hyperlegible, SIL Open Font License 1.1 (bundled with its license).

## Testing

| Test file | Covers |
|-----------|--------|
| `tests/test_eink_display.py` | Shown screens share one id so the device keeps only the latest; photo dithering keeps four levels; picture cache with source, no keys / failed / slow search; PNG format and four levels; light-grey fills; every layout incl. writing steps (first letter, a space, the longest word, done), overflowing text, explanations of 2–4 panels with and without pictures and lessons with and without captions; a lesson cut from one sheet into four equal panels whose grey grows, its prompts, cost records and caches (a repeat asks nothing, a new title only captions); a lesson with later lines in step 1 shown but not kept; no captions from a bad answer or without a Gemini client; a slow caption answer gets a second request, two slow ones leave no captions; panels in one row up to three, two rows for four, at most four; the music card with and without art; screen owners and keep-alive; camera photos expire, are no longer served and their files are deleted, while normal screens keep theirs; photo edits recorded and never cached; idle return; filename rules; held check-ins answered on change and after the hold; awake only while checking in; a restarted app keeps the screen and the resting card name; a real server process outlives the app, is reused, and is replaced in place when its settings change; oversized screens; drawing cache, prompt and usage record; no key / blocked / timeout; gpt-image models through OpenAI (1024x768 medium, edits send the JPEG, token record, credit / rate / moderation / server errors, missing `OPENAI_API_KEY`); the device API over real HTTP; 404s and the log endpoint |
| `tests/test_web_pictures.py` | Strict SafeSearch and Brave auth; best-ranked approved candidate and its source; Jev request shape (danger-zone header, bearer, jpg asset, placeholder in state, one Noul question per risk); every rejection rule incl. nudity outside fine art and suggestive art, each with its plain-words reason, and safety outranking clarity outranking match; classical nudes and calm wild animals pass; resizing into Jev's accepted sizes; only Brave thumbnails downloaded (LAN and other hosts never fetched); each search and check recorded with its cost, failures not; every doubt rejects; search, check, tiny and broken-image failures |
| `tests/tools/test_show_on_display.py` | One picture at a time (others refused without calling the model, text still shown, a cancelled picture frees it); No display; each kind incl. lessons; writing starts practice, refuses a long word with a hint, and needs it set up; a lesson needs a title and tells Reachy not to read the steps; home; incomplete requests; drawing failure message; camera photos shown for 10 minutes and never cached, edits send the photo and the change, no camera / no frame / failed edit; `now_playing` returns the song or says nothing plays / not set up; explanations drawn together, shown with words only when drawings fail, and refused with fewer than two captioned steps; word card without its picture; the drawing fallback tells Reachy why; a sleeping screen is reported, not claimed shown; the `TRMNL_ENABLED` gate |
| `tests/test_writing_mode.py` | Capitals and spaces from text, letters Spanish lacks refused; each letter's name and sound in context (CH, CE/CI, GE/GI, QU, LL, RR, soft R, silent H, final Y, spaces, digits), all cached lines; a word written one letter per pat with the head held and released and the finished screen back to `requested`; room words step, go back and end, other talk ignored; a pat with a "¡ya!" is one step; 10 idle minutes end it; the hold is renewed; too long or unwritable refused; a new word replaces the old |
| `tests/test_quick_commands.py` | (writing) the word kept as heard; names and "algo" go to the conversation; a word that fits starts writing, one too long does not; while writing, "ya" steps and another request ends writing and runs |
| `tests/test_hybrid_smoke.py` | (writing) the room listener takes what it acts on before wake detection and passes the rest on |
| `tests/test_now_playing.py` | A writing screen holds through new songs; track and episode parsing; other devices and empty players ignored; covers only from `i.scdn.co` over https; each new song gets the card once, covers cached; the next look at the song's end; no idle return during a long track; requested screens hold during the conversation, then return at the next song or after 2 minutes; 2 minutes after the music stops; nothing playing leaves screens alone; "what song is this"; a missing cover; `refresh_soon`; the Spotify fetch (token, episodes, 204); the watcher needs the screen, the switch and Spotify |
| `tests/tools/test_spotify_control.py` | Pause, next and resume refresh the screen; rejected commands do not |
| `tests/test_config.py` | `TRMNL_*` parsing and fallbacks |

Commands run on 2026-10-25 (macOS, Python 3.12):

```bash
.venv/bin/pytest tests/test_eink_display.py tests/tools/test_show_on_display.py tests/test_config.py -q
ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v
```

Results: full gate clean; 1,817 passed, 4 skipped (v1). Latest (2026-10-25, explanations and picture costs): 1,883 passed, 4 skipped.

Live check 2026-10-25 (household TRMNL OG, firmware 1.8.17, MacBook on the same Wi-Fi): the real
`EInkDisplay` and `show_on_display` served the resting card, a fox drawing made in 2.7 s, a word card
reusing the cached drawing and a list; the device fetched each at its next check-in. Not yet tried
through a spoken conversation.

## Changelog

- 2026-10-30: Writing practice (`kind=writing`, quick command "enséñame a escribir X"): one capital at a time,
  stepped by pats or "¡ya!" without the name, the letter's name and sound spoken from cached lines; no cloud
  conversation while the child writes. Plan: [writing-practice.md](../plans/writing-practice.md).

- 2026-10-30: Drawing lessons (`kind=lesson`): four numbered steps cut from one image-model sheet, captions
  read from it by Gemini 3.8 Flash. Live with the real APIs on the MacBook: a house (no face), a rocket and a
  giraffe in 12.6–18.1 s; one of three caption calls ran past 8 s, which led to the second request. Not yet
  shown on the TRMNL or asked for by voice. Plan: [drawing-lessons.md](../plans/drawing-lessons.md).

- 2026-10-25: v1 — drawings, word and translation cards, text, resting card, device server.
- 2026-10-25: v2 web pictures (Brave Image Search + Jev check, drawing fallback).
- 2026-10-25: Camera photos and photo edits (`kind=camera`), never saved, 10 minutes on screen.
- 2026-10-25: Image-model API errors (402 out of credits, 5xx) become a `DrawingError` Reachy can explain
  instead of a tool crash; edits of famous characters describe the costume instead of the name
  ("Spiderman" was refused live).
- 2026-10-27: Drawings and photo edits default to OpenAI's `gpt-image-2.5-flare` (1024x768, medium:
  10-12 s, about $0.009 a drawing); Gemini stays available through `DISPLAY_IMAGE_MODEL`. Plan:
  [openai-drawings.md](../plans/openai-drawings.md).
- 2026-10-27: One picture at a time; a second picture request while one is being made is refused (a
  Minecraft photo edit was made twice and then drawn plainly, each replacing the last).
- 2026-10-25: Now playing: the Spotify song on Reachy's speaker shows as a music card (album art,
  title, artist, album), following the household's rules for requested screens; "what song is this".
- 2026-10-25: The encyclopedia style was too detailed for the TRMNL: thin lines broke into dashes (a
  RAV4) and a busy scene (Parque das Nações) became dark noisy blobs. Smoothing at screen size erased
  the lines instead. Drawings now ask for bold thick outlines, large flat greys and only the main
  details (`drawings-v3/`); still accurate, still about 3 s.
- 2026-10-25: Drawings switched from children's cartoons to an encyclopedia-style illustration (same
  model and speed, new `drawings-v2/` cache). Grounded Nano Banana 2.1 was more accurate but took 13–30 s,
  so it is not used; see the plan's execution log.
- 2026-10-25: How and why questions about real machines now get steps: "explain how Starship works"
  got a single photo because the picture kind listed "a rocket" and the profile limited explanations
  to children. Live check, two runs each: Starship, volcano, rain and airplane → steps (8/8); "show me
  Starship" and "show me the Eiffel Tower" → picture (4/4); before: Starship picture/none, airplane none.
- 2026-10-25: Visual explanations (`kind=steps`): a title over 2–4 numbered panels, drawings made in
  parallel. Brave searches and Jev checks now go to the usage ledger with their costs.
- 2026-10-25: Daily caps on new drawings (20) and picture searches (30) removed at the household's
  request; `DISPLAY_DRAWINGS_PER_DAY` and `DISPLAY_PICTURES_PER_DAY` are gone.
- 2026-10-25: The device server runs as its own lasting process, so restarting Reachy no longer
  sends the TRMNL into its 5-minute sleep.
- 2026-10-25: Reachy no longer claims a screen it could not show: a drawing fallback comes with the
  reason to say, and a sleeping TRMNL (5 minutes after a failed check-in) is reported as napping.
- 2026-10-25: Clarity no longer rejects fine art (Guardi's Venice was drawn instead); it still ranks copies.
- 2026-10-25: Fine art may depict violence unless it is graphic (new `graphic` question): Guernica shows,
  Goya's Third of May and The Death of Marat do not.
- 2026-10-25: Jev also judges each candidate as the screen will show it and the clearest approved one
  wins; nudity narrowed to bare breasts, buttocks, genitals or underwear.
- 2026-10-25: Household policy: block violence, mature content and nudity (fine art excepted), not
  scary-looking animals; "frightening" replaced by "mature", violence reworded and recalibrated.
- 2026-10-25: Live calibration of Jev's questions and limits, resizing for Jev, six candidates; shown
  screens share one id so the TRMNL's flash no longer fills up.
- 2026-10-25: Held check-ins and a 1 s default refresh: screens appear in seconds instead of up to a minute.
- 2026-10-31: After the TRMNL reports a failure, its screen is sent under a fresh name so it redraws.
