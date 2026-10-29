# Plan: "Take a picture" sends a photo to the family WhatsApp group

- Status: `in-progress`
- Owner: household
- Created: 2026-10-29
- Last updated: 2026-10-29
- Feature doc: [Photos to WhatsApp](../features/whatsapp-photos.md) (written with the code)

## Goal

"Richie, take a picture" (or "take a photo of us", in English, Spanish, Italian or Portuguese) is handled
locally: Reachy counts "three, two, one" out loud, one second apart, takes a short burst, keeps the best
frame, shows it on the e-ink screen and sends it to the household's "Reachy photos" WhatsApp group from
Reachy's own WhatsApp number. He then says it was sent, or that it could not be.

This is the first milestone of a larger idea: later, Enzo (4) and Mara (2) send voice notes and photos
to family through the same WhatsApp link. Nothing of that is built here, but the link takes any chat id
so it can be reused.

## Decisions (with the household, 2026-10-29)

1. **Reachy's own WhatsApp on the spare number, through an unofficial client.** The household accepted
   WhatsApp's terms-of-service risk; a ban would only hit the spare number. The official Cloud API was
   rejected (business account, templates before messaging someone first).
2. **Maribel stays out of this path** (reliability, privacy). She remains the way to send text from the
   household's own WhatsApp.
3. **Trigger:** "take a picture", "take a photo of us" and the Spanish, Italian and Portuguese
   equivalents, as a quick local command so the countdown starts at once, without Gemini.
4. **Countdown:** three, two, one, spoken, one second apart (3 s in all).
5. **Recipient:** a "Reachy photos" WhatsApp group the household creates; its id goes in `.env`.
6. **Photos are only sent**, never kept by Reachy. (The e-ink preview already deletes its file after
   10 minutes; see the camera-photos plan.)
7. **No cooldown or limit.** The kids may send as many as they like.
8. **Preview on the e-ink screen** when it is set up.

## Assumptions

- One GOWA device slot (`reachy`, created by the `pair` command on a fresh server, whose single-device
  endpoints answer an error until a slot exists); with one slot GOWA needs no `X-Device-Id`.
- The camera frame from `capture_frame` (1920×1080 or what the SDK is set to) is good enough for family
  snapshots; to be judged at the live check.
- Speaker following already turns Reachy toward the person asking, which frames "a photo of us" well
  enough for a first version.
- The GOWA REST API stays as in v9.7.0, which is pinned.

## Context (current code this plan relies on)

- `quick_commands.py`: `parse(text)` matches a normalized request (`normalize` folds accents and
  punctuation; `strip_politeness` drops "can you", "puedes", "puoi", "podes"…, leaving an infinitive in
  polite Romance requests) against per-language full-match regexes and returns
  `QuickCommand(kind, language, args)`. Negations ("no", "don't"…) return None before most grammars;
  `_NOT_A_COMMAND_START` words ("what"…) return None before media/volume/lights. `QuickCommands.handle`
  runs Maribel, routines, then `parse` + `run`; `run` dispatches on `kind`. `QuickReply(text, language,
  kind, after=..., after_timeout_s=..., whole_request=...)`: Reachy says `text`, then runs `after`, whose
  returned text he also says.
- `FIXED_PHRASES` (`quick_commands.py`) is passed to `LocalSpeech(cacheable=...)` in `main.py`: only those
  exact phrases are cached on disk and play at once; anything else is synthesized with Gemini TTS each
  time (0.4-0.6 s to first audio; macOS `say` without the network).
- `hybrid_runtime.py` `_try_quick_command`: `handle` is bounded by `_QUICK_COMMAND_TIMEOUT_S` (8 s);
  then `_speak_locally(reply.text)` (waits until heard, plus a short tail), then `after()` bounded by
  `after_timeout_s`, then `_speak_locally(follow_up)`, then back to ambient listening. A superseded
  generation stops it. `whole_request=True` sends a cut-off request clip to the conversation instead.
- `camera_frames.py`: `capture_frame(media)` returns the latest BGR frame or None (the SDK pulls with a
  20 ms timeout, so a missed frame is normal), serialized with the live stream by `_CAPTURE_LOCK`;
  `encode_jpeg(frame, quality)` is a GStreamer pipeline with a 1 s pull timeout and may return None.
- Speech wobble moves the head while Reachy talks; when he stops (`set_speaking(False)`) head tracking
  resumes and re-anchors.
- `face_identity.py` uses the SDK's YuNet `reachy_mini.vision.face_detector.FaceDetector` (its constructor
  downloads the model from the Hugging Face hub on first use and opens an ONNX session).
- `tools/show_on_display.py`: `kind=camera` renders with `eink_display.render_photo(Image, caption)` and
  calls `display.show(png, expires_after_s=PHOTO_ON_SCREEN_S)`. An asleep TRMNL shows it within about
  five minutes; the now-playing card may replace a requested screen while music plays.
- `ToolDependencies` (`tools/core_tools.py`) carries `camera_enabled`, `display`, `maribel`, etc.;
  `main.py` builds optional services (e.g. `_build_maribel`) and assigns them to `deps`.
- Config: `config.py` reads env vars twice (class body and the reload function); `.env.example`
  documents them; `README.md` has the config table.
- `scripts/start-reachy.sh` reads single keys from `.env` with `env_file_value`, starts the local Whisper
  server if it is not running, waits for it to answer, and its `cleanup` trap stops it on exit only if the
  script started it.

## Design

### WhatsApp server (not our code)

[GOWA](https://github.com/aldinokemal/go-whatsapp-web-multidevice) (go-whatsapp-web-multidevice, MIT,
built on whatsmeow), v9.7.0, the `darwin_arm64` release binary, run as `whatsapp rest`. It links to the
spare number as a WhatsApp "linked device" and exposes a REST API. Chosen over writing a Baileys or
whatsmeow service ourselves: no code of ours to maintain, a Mac binary, and sending images and voice notes
to groups is already there. Reachy talks to it with `httpx`, already a dependency: **no new Python
dependency**.

Run settings, in a `.env` in its own data directory (not Reachy's): `APP_HOST=127.0.0.1`,
`APP_PORT=8792`, `APP_BASIC_AUTH=reachy:<random>`, `APP_UI_ENABLED=false` (no dashboard downloaded from
GitHub; pairing uses a code instead of the QR page), `MCP_ENABLED=false`, `WHATSAPP_AUTO_REPLY=` (empty),
`WHATSAPP_AUTO_DOWNLOAD_MEDIA=false`, no webhook.

What GOWA keeps (verified in its v9.7.0 source): the session keys in `storages/whatsapp.db`, and the text
and metadata of messages in the spare number's chats in `storages/chatstorage.db` (always on; there is
no setting to turn it off). Received media is not downloaded with the setting above. Each image sent is
copied to `statics/senditems` and deleted after the send (an upload error before the send can leave it).
GOWA creates these folders world-readable, and prints all its settings, the basic-auth password
included, to stdout at startup. So: the data directory is `chmod 700`, its log is owner-only and
overwritten at each start.

Calls used: `GET /health` (public readiness), `GET /app/status`, `GET /user/my/groups` (to find the group
id), `POST /send/image` (multipart: `phone=<jid>`, `hd=true`, and an `image` part named `photo.jpg` with
`Content-Type: image/jpeg`, which GOWA requires; group ids end in `@g.us`). Without `hd`, GOWA shrinks
every image to 600 px wide (`compress` defaults to true and cannot be turned off from a form); with it,
the image is fitted to 2560 px. Pairing: `GET /devices`, `POST /devices {"device_id": "reachy"}` when
there is none, then `GET /app/login-with-code?phone=<spare number>` returns a code to type into the
spare phone.

### Reachy side

1. **`whatsapp.py`** (new)
   - `WhatsAppLink(url, auth)`; `async send_image(chat, jpeg) -> SendResult` (`sent`, `failed`,
     `unsure`): one multipart POST to `/send/image` with basic auth; connect timeout 3 s, and a 15 s
     deadline for the whole send including a retry (`asyncio.timeout`; httpx timeouts are per phase).
     `sent` on 2xx with `code == SUCCESS`; `failed` on any other reply, or when it could not connect;
     `unsure` when the deadline or a read/write timeout ends it after the request may have left (GOWA's
     own limit is 45 s, so it may still deliver). Retried once after 1 s only on `httpx.ConnectError` or
     `httpx.ConnectTimeout` (the request never left), so Reachy never sends a photo twice. Logs the outcome, the HTTP status and GOWA's `code`, the byte count and the
     time; never the chat id, the credentials or the image.
   - `async status() -> str | None`: GOWA's connection message from `/app/status`, or None if unreachable.
   - `python -m reachy_mini_conversation_app.whatsapp groups` prints each joined group's name and id, so
     the household can copy the "Reachy photos" id into `.env`; `status` prints whether GOWA is logged in;
     `pair <phone>` prints the pairing code. They read `WHATSAPP_API_URL` / `WHATSAPP_API_AUTH` from config.
2. **`photographer.py`** (new): `take_photo(media, detector) -> bytes | None`, blocking.
   - Burst of up to `BURST_FRAMES` (5) frames `BURST_INTERVAL_S` (0.12 s) apart with `capture_frame`; a
     missed frame (None) is retried every 30 ms within a 1.5 s burst window. A failed encode is retried
     once.
   - Score each: number of faces found by the detector (on a 2× downscaled frame, as `face_identity`
     does), then sharpness (variance of a 3×3 Laplacian of the downscaled gray image, numpy only).
     Keep the best; encode with `encode_jpeg(frame, quality=PHOTO_JPEG_QUALITY)` (92).
   - `FaceFinder`: creates the SDK `FaceDetector` once (thread-safe); `warm_up()` runs in a background
     thread at startup when WhatsApp photos are configured. A photo never waits for it: while the
     warm-up holds the lock, or after creation failed (remembered for the process), sharpness alone
     decides. A detection error on a frame is logged and that frame counts no faces.
   - Known limits: five frames over 0.6 s mostly differ by blinks and motion; Laplacian variance also
     rewards sensor noise in a dark room; a false face can tip the count. Accepted for a first version.
   - None when the camera gives no frame at all.
3. **`quick_commands.py`**
   - `_PHOTO` grammar (full match after politeness is stripped), kind `photo`, checked in `parse` after
     the negation check and before the `_NOT_A_COMMAND_START` check:
     - en: `(?:take|snap) (?:a |our |my )?(?:picture|photo|pic)(?: of (?:us|me|us all|all of us|everyone))?`
     - es: `(?:toma|tomar|tomanos|tomame|tomarnos|saca|sacar|sacanos|sacame|sacarnos|haz|hacer|haznos|hazme|
       hacernos) una foto(?: de (?:nosotros|todos))?`
     - it: `(?:fai|fare|facci|farci|fammi|scatta|scattare|scattaci|scattarci|scattami) una foto(?: a (?:noi|tutti))?`
     - pt: `(?:tira|tirar|tira nos|tirar nos|tira me|tire|bate|bater) uma (?:foto|fotografia)(?: (?:de|a) (?:nos|todos))?`
     A request with more ("take a picture of me as a pirate") does not match and goes to Gemini, which
     still has `show_on_display kind=camera` for edits.
   - `QuickCommands.run` → `_photo(command)`: returns None (to the conversation, today's behavior) unless
     `deps.whatsapp` is set and `deps.camera_enabled`. Otherwise `QuickReply(None, language, "photo",
     beats=COUNTDOWN[language], after=take_and_send, after_timeout_s=PHOTO_STEP_TIMEOUT_S,
     whole_request=True)` (a cut-off "take a picture of me as…" goes to the conversation).
     - `COUNTDOWN`: en ("Three!", "Two!", "One!"), es ("¡Tres!", "¡Dos!", "¡Uno!"), it ("Tre!", "Due!",
       "Uno!"), pt ("Três!", "Dois!", "Um!").
     - `take_and_send`: waits `SETTLE_S` (0.4 s) for the head to settle after the last beat (speech
       wobble stops and head tracking re-anchors then), then `take_photo` in a thread, bounded by
       `PHOTO_TAKE_S` (4 s) → None or too slow: `PHOTO_NO_PICTURE[language]`. Else, if
       `deps.display`: `render_photo` + `display.show(png, expires_after_s=PHOTO_ON_SCREEN_S)` in a thread
       (an error is logged and does not stop the send). Then `send_image(config.WHATSAPP_PHOTOS_CHAT,
       jpeg)` → `PHOTO_SENT` ("Sent!"), `PHOTO_NOT_SENT` ("I couldn't send the photo."), or
       `PHOTO_UNSURE` ("I'm not sure the photo went through.").
     - `PHOTO_STEP_TIMEOUT_S` = `SETTLE_S` + `PHOTO_TAKE_S` + 1 s (preview) + the 15 s send deadline +
       2 s margin, so the runtime's own bound never cuts a send short (which would end in silence).
     - The countdown and the four result phrase sets join `FIXED_PHRASES`, so they are cached on disk;
       `main.py` speaks each of them once into the cache in the background at startup (silently, only
       those not cached yet), so even the first countdown plays at once in Reachy's voice.
   - `QuickReply` gains `beats: tuple[str, ...] = ()` and `beat_s: float = 1.0`.
4. **`hybrid_runtime.py`** `_try_quick_command`: after `reply.text`, speak each beat with
   `_speak_locally`, beat k starting at `t0 + k·beat_s` (at once if the previous beat ran long:
   `_speak_locally` waits for the audio plus a 0.4 s tail, so a beat may take 1.1-1.4 s), and wait out
   the last beat before `after()`. Stop between beats when the generation is superseded. The
   wait goes through the runtime's existing clock/sleep seams (or `monkeypatch` of the reply tail) so the
   tests do not take seconds.
5. **`tools/core_tools.py`**: `ToolDependencies.whatsapp: Any = None` (a `WhatsAppLink`), and
   `whatsapp_photos_configured()`.
6. **`config.py`**: `WHATSAPP_API_URL` (e.g. `http://127.0.0.1:8792`), `WHATSAPP_API_AUTH` (`user:pass`),
   `WHATSAPP_PHOTOS_CHAT` (the group id); configured when all three are set. Both read sites.
7. **`main.py`**: `_build_whatsapp()` → `WhatsAppLink` or None; `deps.whatsapp = _build_whatsapp()`.
   When configured, a daemon thread (`main` builds services before any loop runs) warms up the face
   detector, caches the photo phrases, and after 20 s (GOWA connects to WhatsApp after its HTTP server
   is up) logs GOWA's status, a warning if unreachable or not logged in. The link makes a new
   `httpx.AsyncClient` per call, so it is safe from any loop.
8. **`scripts/start-reachy.sh`**: reads `WHATSAPP_SERVER_BIN` (path to the GOWA binary; script-only),
   `WHATSAPP_SERVER_DIR` (default `~/.local/share/reachy-whatsapp`) and `WHATSAPP_API_URL` from the
   environment or `.env` (`env_file_value`). If the binary is set and `${WHATSAPP_API_URL}/health` does
   not answer, it starts `"$WHATSAPP_SERVER_BIN" rest` in a subshell (`cd` to the data directory,
   `umask 077`, `chmod 700` that directory; a failure there is a warning, not an abort), `nohup`, writing
   a fresh owner-only `${TMPDIR}/reachy-whatsapp.log` each start, and waits up to 10 s for `/health`
   (a warning if it never answers). GOWA keeps running when the app exits, so a restart of Reachy does
   not relink WhatsApp; nothing supervises it (a crash means "couldn't send" until the next start).

### Operations on the Mac mini (household + agent)

1. `curl` `whatsapp_9.7.0_darwin_arm64.zip` and `checksums-macos.txt` (curl adds no Gatekeeper
   quarantine), check the checksum, unpack into `~/.local/share/reachy-whatsapp` (`chmod 700`), write its
   `.env` there (settings above).
2. Start it (`scripts/start-reachy.sh` or `whatsapp rest` from that directory) and run
   `python -m reachy_mini_conversation_app.whatsapp pair <spare number>`; on the spare phone: WhatsApp →
   Linked devices → Link with phone number, type the code.
3. The household creates the "Reachy photos" group with the spare number in it.
4. `python -m reachy_mini_conversation_app.whatsapp groups` → copy the id into `.env`.
5. The spare phone stays plugged in on Wi-Fi: WhatsApp logs out linked devices whose phone has been
   offline for about two weeks.

## Implementation order

1. `whatsapp.py` + tests (fake transport).
2. `photographer.py` + tests (synthetic frames, fake detector).
3. `QuickReply.beats` + runtime countdown + tests.
4. Photo grammar, phrases and `_photo` + tests.
5. Config, deps, `main.py`, `.env.example`, start script.
6. Feature docs, README, indexes; full gate.
7. Mac mini: install GOWA, pair, create the group, live check.

## Acceptance criteria

- "Richie, take a picture" (each language, including polite forms like "¿puedes sacarnos una foto?") →
  three spoken beats 1-1.5 s apart, the photo in the "Reachy photos" group within about 6 s of "one",
  "Sent!", and the photo handed to the e-ink screen (which shows it at once when awake, within about
  five minutes when asleep).
- GOWA down or logged out → "I couldn't send the photo." and a log line with the reason; a send that
  times out → "I'm not sure the photo went through."; Reachy keeps working either way. Camera off or
  WhatsApp not configured → the request goes to the conversation as today.
- Reachy never sends a photo twice for one request; nothing of it is written to disk by Reachy except the
  existing 10-minute e-ink preview (GOWA's own files are described above).
- The photo reaches WhatsApp at its camera size (up to 2560 px), not 600 px.

## Test strategy

- `tests/test_whatsapp.py`: via `httpx.MockTransport`: the `phone` and `hd=true` fields, the `image`
  part's filename and `image/jpeg` content type, the basic-auth header; `sent` / `failed` (non-2xx,
  `code != SUCCESS`) / `unsure` (read timeout or the deadline, no retry); one retry on `ConnectError` and
  on `ConnectTimeout`; logs hold no chat id or credentials;
  `groups` and `status` CLI output from fake responses.
- `tests/test_photographer.py`: the frame with more faces wins; among equal faces the sharper one wins
  (synthetic noise vs. blurred image); detector creation or detection failure falls back to sharpness;
  a warm-up still running does not block a photo; missed frames are retried within the window; no
  frames → None; a failed encode is retried once.
- `tests/test_quick_commands.py`: grammar positives in 4 languages, polite and infinitive forms
  ("can you take a picture", "¿puedes sacarnos una foto?", "puoi farci una foto", "podes tirar uma foto");
  negatives ("take a picture of me as a pirate", "don't take a picture", "what's in this picture");
  not configured or camera off → None; `after` with fake photographer, link and display: sent, not sent,
  unsure, no picture (and a photo step that runs too long), display error still sends; the settle comes
  before the burst; every countdown and result phrase is in `FIXED_PHRASES`; the reply has
  `whole_request=True`. Privacy: other commands never touch the camera or WhatsApp; the photo goes
  only to `WHATSAPP_PHOTOS_CHAT`; the runtime test shows the photo path never opens the cloud session.
- `tests/test_hybrid_runtime.py`: beats spoken in order before `after`, on a `t0 + k·beat_s` schedule;
  a superseded generation stops the countdown and skips `after`; the conversation is never opened.
- `tests/test_config.py`: the three variables at both read sites; `_build_whatsapp` returns None unless
  all three are set.
- Full gate: `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest tests/ -v`.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Ban of the spare number | Accepted by the household; low volume to one group; Reachy degrades to "couldn't send". |
| Linked device logged out (phone offline ~14 days, or WhatsApp update) | Startup status check logs it; `whatsapp status` CLI; phone stays plugged in. |
| GOWA update changes the API | Pin v9.7.0; the plan names the calls used; tests pin our request shape. |
| GOWA keeps the group's message text and metadata on disk, and prints its password at startup | Data directory and log owner-only; log overwritten each start; only the "Reachy photos" group is in that account for now. A periodic purge of `chatstorage.db` is a follow-up if the family's groups join later. |
| A send times out but still arrives | Reachy says he is not sure instead of "couldn't send", so nobody re-sends by reflex; never retried after the request left. |
| A second GOWA device makes it require `X-Device-Id` | Only one device is linked; the status check shows the error. |
| Reachy moving during the countdown blurs the photo | Burst + sharpness choice; revisit after the live check. |
| Face detector model download on first use | Warmed up at startup; sharpness-only fallback. |
| Kids' photos leave the house | Only to the household's own group, by explicit request. |
| "Take a picture" now bypasses Gemini's `show_on_display kind=camera` | Only when WhatsApp is configured; edits and longer requests still go to Gemini. |
| The now-playing card replaces the preview while music plays | Accepted; the photo is in WhatsApp. |
| No way to cancel once the countdown starts (Reachy does not listen during a local command) | Accepted: the trigger is an explicit full-sentence match after his name; the household allows spam. |
| Reachy is busy (not listening) for the countdown plus the send, up to about 23 s when GOWA is slow | Usually about 5 s; the send deadline bounds it. |
| GOWA down: Reachy still counts down, then says "I couldn't send the photo" (connection refused is immediate) | Accepted rather than polling GOWA's health; the startup check logs it. |
| In an open Gemini conversation, "take a picture" goes to Gemini | Gemini has `take_photo` (added 2026-10-29), which sends the same way; only edits stay unsent. |

## Out of scope

- Kids' voice notes and photos to family members, contacts, approvals, replies (next milestones).
- Sending photo edits (`show_on_display kind=camera` with `edit`) to WhatsApp.
- Framing (turning toward all faces) beyond what speaker following already does.
- Video.

## Documentation updates

`docs/features/whatsapp-photos.md` (new), `docs/features/INDEX.md`, `docs/features/quick-commands.md`
(the `photo` kind, `QuickReply.beats`), `docs/features/hybrid-runtime.md` (countdown beats),
`docs/features/eink-display.md` (a second path that shows camera photos), this plan and
`docs/plans/INDEX.md`, `README.md` (config table, quick commands), `.env.example`. Architecture: a new
external service (GOWA) is added; flag that `docs/scheme.mmd` needs the WhatsApp box.

## Execution log

- 2026-10-29: plan written from the household's answers.
- 2026-10-29: review round 1 (completeness): countdown phrases added to `FIXED_PHRASES`; GOWA's chat
  storage, world-readable folders and settings dump documented and contained; `rest` subcommand, the
  `image/jpeg` part and pairing by code; an "unsure" outcome for timeouts; polite infinitive forms in the
  grammar; detector warm-up at startup; start-script readiness wait and cleanup; doc list completed.
  Rejected: reusing `face_identity._default_detector` (a two-line private factory; sharing it would mean
  renaming it for one caller).
- 2026-10-29: review round 2 (failure modes): `hd=true` (GOWA otherwise sends 600 px); a 15 s send
  deadline inside the link, budgets cut and the runtime bound derived from them; `ConnectTimeout`
  retried like `ConnectError`; detector never blocks a photo; missed frames retried, encode retried;
  head settle before the burst; phrases pre-cached at startup; beats on an absolute schedule; GOWA left
  running across app restarts, started in a subshell; cancel, busy time and the conversation path
  recorded as accepted risks; privacy tests added. Rejected: a background GOWA health state that skips
  the countdown when it is down (polling infrastructure for a failure Reachy already reports within
  seconds); a settle-free burst before the last tail (the settle is simpler).
- 2026-10-29: built: `whatsapp.py` (link, `status` / `groups` / `pair` commands; `pair` creates the
  `reachy` device slot GOWA needs on a fresh server), `photographer.py`, the `photo` quick command,
  `QuickReply.beats` in the runtime, config, `main.py` wiring (`photo-setup` thread), the start-script
  block, docs. GOWA's `/app/status` returns `is_connected` / `is_logged_in`, which the startup check reads.
  Gate: `ruff check .` and `ruff format .` clean, `mypy` clean (108 files), `pytest tests/` 2115 passed,
  4 skipped. The start-script block was checked with a fake server (start, owner-only directory and log,
  reuse, missing binary). Not yet done: step 7 (GOWA on the Mac mini, pairing, the group, a live photo),
  and the architecture diagram (`docs/scheme.mmd` needs a WhatsApp/GOWA box).
- 2026-10-29: "¿nos tomas / me sacas una foto?" and "…a todos" added to the Spanish grammar.
- 2026-10-29: Mac mini: GOWA v9.7.0 (checksum OK) in `~/.local/share/reachy-whatsapp` (owner-only;
  the release binary is named `darwin-arm64`, renamed `whatsapp`), its `.env` with a random password and
  `APP_OS=Reachy`; paired with the spare number by code (the device slot `reachy` had to be created
  first, as the `pair` command does); one group, "Reachy group", whose id is `WHATSAPP_PHOTOS_CHAT`.
  Deviation: on the mini GOWA runs as the LaunchAgent `com.eduardo.reachy-whatsapp` (starts at login,
  restarted if it exits) instead of from the start script, because Reachy there starts from its own
  LaunchAgent and `start-reachy.command`; the link survived the switch. Reachy's `.env` backup:
  `~/Coding/.env.bak-2026-10-29-whatsapp`.
- 2026-10-29: first live try on the Mac mini: the request clip read "Richie, tomemos una foto." ("let's
  take a photo"), which the grammar did not know, so it went to Gemini (`show_on_display kind=camera`:
  on the screen, not sent). Added "let's take…" in all four languages ("tomemos / saquemos / hagamos /
  vamos a … una foto", "facciamo / scattiamo una foto", "vamos tirar / tiremos uma foto").
- 2026-10-29: the household asked for Gemini to send photos too (decision 3 extended). `take_photo` tool
  (`WHATSAPP_TOOLS`, offered only when configured): Gemini counts "3, 2, 1" as it calls it, the tool waits
  3 s and runs `photographer.take_and_share`, now the one take-show-send path for the quick command and
  the tool. `show_on_display kind=camera` keeps edits and points plain photos to `take_photo`. This
  replaces the "conversation path is not sent" accepted risk and the out-of-scope item.
- 2026-10-29: live check on the Mac mini passed (household: "it worked, really fast"). "Tomemos una foto"
  handled locally (kind=photo, es): request recognized → photo taken 6.7 s (three beats plus the 0.4 s
  settle: each beat ran about 1.8 s, longer than the 1 s target, since a beat waits for its audio and
  the 0.4 s tail), burst 0.8 s (5 frames, best had 1 face), 185 KB JPEG sent on the first attempt in
  1.6 s, done 9.7 s after the request. The photo arrived in "Reachy group". The `take_photo` tool is
  registered; its live use is not checked yet.
- 2026-10-29: tighter countdown, as the household asked. The cached countdown words carry Gemini's
  padding: 0.12-0.31 s before the voice and up to 1.05 s after ("¡Dos!" is 1.72 s in all), and each beat
  then waits a 0.4 s playback tail. `LocalSpeech` now plays every cached phrase without the silence
  around the voice (1% of full scale, 50 ms kept on each side; the cached file is unchanged), so a beat
  is the voice plus the tail, about 1 s, and every fixed reply starts sooner. The first, uncached play
  is unchanged; the startup warm-up caches the photo phrases.
- 2026-10-29: photos now frame the face in view during the countdown (see the family-messages plan's log).
