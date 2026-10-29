# Plan: Voice messages and photos to family WhatsApp groups

- Status: `in-progress`
- Owner: household
- Created: 2026-10-29
- Last updated: 2026-10-29
- Feature doc: [Photos to WhatsApp](../features/whatsapp-photos.md) (extended with messages)
- Builds on: [Photos to the family WhatsApp group](whatsapp-photos.md)

## Goal

Enzo (4), Mara (2) and the adults send voice messages and photos to family through Reachy:
"Richie, send a message to Tío Sergio" → "Okay! Tell me your message for Tío Sergio after the beep." →
beep → they talk → it is sent to the "Reachy <> Sergio" group as a WhatsApp voice note → "Sent to Tío
Sergio!". "Send a picture to Sergio" does the photo countdown and sends it there. Without a name ("send a
message") or with one Reachy does not know, he asks "Who should I send it to?" and listens for the answer.

## Decisions (with the household, 2026-10-29)

1. **Groups, not direct chats.** Each recipient is a WhatsApp group with them, the spare number, and both
   parents, so the parents see every message. Start with one: "Reachy <> Sergio"; Abuelo and the nonni
   later.
2. **Reachy may send only to the groups in his contacts file** (group ids only: `@g.us`).
3. **Names:** Tío Sergio: "tío sergio", "zio sergio", "uncle sergio", "sergio" (the household checked the
   table, including abuelo and nonna / nonno / nonni for later).
4. **Requests:** "send a message to [x]", "vamos a mandar un mensaje a [x]" and variations in the four
   languages; "send a message" without a name asks who. The same for "send a picture (to [x])".
5. **No quiet hours, no limits, adults too, one way** (replies later).
6. **A beep** says recording has started. A message ends when someone touches his head (since the head
   tests of 2026-10-29), after 5 s of silence, or at 60 s.

## Context (current code this relies on)

- `photographer.take_and_share(deps, chat)` takes, shows and sends a photo to any chat.
- `whatsapp.WhatsAppLink.send_image`; GOWA's `POST /send/audio` (`phone`, `audio`, `ptt`) sends a voice
  note. With `ptt=true` an OGG file is sent as is (no `ffmpeg`; without it GOWA uses a default waveform).
- `HybridRuntime`: in `LOCAL_COMMAND` every captured frame after the activation goes to
  `_enqueue_postactivation`, which also appends it to `_request_clip` when that is a list. The request
  clip watches it with `RmsEnergyVAD` (`_request_clip_vad`) for speech start, silence and a cap.
  `_enqueue_postactivation` keeps at most 20 s and logs a warning per dropped frame beyond that.
- `HybridRuntime.expect_answer(handler)`: after the current reply, listen once without the name (15 s
  for speech to start) and pass the text to `handler`, which returns a `QuickReply`.
- `MovementManager.set_listening(True)` perks the antennas and suppresses idle breathing.
- GStreamer on the mini has `opusenc`, `oggmux` and `audioresample`.

## Design

1. **Contacts** (`whatsapp_contacts.py`, new): `WHATSAPP_CONTACTS_FILE` (default
   `~/.config/reachy-whatsapp/contacts.json`): `{"contacts": [{"name": "Tío Sergio", "chat": "…@g.us",
   "say": ["tio sergio", "zio sergio", "uncle sergio", "sergio"]}]}`. Read on each request (a small file;
   edits need no restart); entries without a group id or a name are skipped with a warning.
   `find(phrase) -> Contact | None`: the phrase folded like quick commands, leading words such as
   "a / al / to / the / my / mi / el / il / ai / ao / para" dropped; an alias that is the whole phrase, or
   whose words all appear in it (the longest wins); two contacts matching is no match.
2. **Grammar** (`quick_commands.py`): kinds `message` and `send_photo`, with an optional `to` phrase:
   - en: "(let's) send (a) (voice) message (to x)", "send x a message", "send (a) picture / photo (to x)",
     "send x a picture";
   - es: "manda / mándale / envía / envíale / mandar / enviar / vamos a mandar(le) / quiero mandar(le) un
     mensaje (de voz) / un audio / una nota de voz / una foto (a / al / para x)";
   - it: "manda / mandiamo / invia / inviamo un messaggio (vocale) / un vocale / un audio / una foto (a /
     al / ai / allo / per x)";
   - pt: "manda / envia / mandar / enviar / vamos mandar uma mensagem (de voz) / um áudio / uma foto (a /
     ao / à / para x)".
   A `to` that names Maribel is left alone (her hand-off already owns "message Maribel").
3. **Replies** (`QuickCommands`), only when WhatsApp is set up (and the camera for photos):
   - known recipient, message: "Okay! Tell me your message for {name} after the beep." then `after`:
     `set_listening(True)`, `deps.record_message()`, `set_listening(False)`; nothing heard → "I didn't
     hear a message, so I didn't send anything."; else `encode_voice_note` → `send_audio` → "Sent to
     {name}!", "I couldn't send the message." or "I'm not sure the message went through.".
     `whole_request=True`.
   - known recipient, photo: the photo countdown, then `take_and_share(deps, contact.chat)` → "Sent to
     {name}!" / the photo failure lines.
   - no or unknown recipient: "Who should I send it to? I can send to {names}." and `after` calls
     `deps.expect_answer(answer)`; the answer (a name, "a Sergio", "para el tío Sergio") goes on with the
     same flow; a name he still does not know: "Sorry, I can only send to {names}."; "never mind" and
     other cancels end it quietly. With no contacts: the request goes to the conversation.
4. **Recording** (`HybridRuntime.record_message() -> tuple[int, NDArray[np.int16]] | None`): only during
   the current quick command (else None). Plays a short beep (`local_speech.beep()`, 24 kHz) through the
   reply speaker and waits for it, clears the post-activation queue, then collects frames into
   `_request_clip` while a flag stops the post-activation queue from holding (and warning about) them. Ends
   when no speech started in `MESSAGE_START_S` (8 s, → None), after `MESSAGE_SILENCE_S` (3 s) of silence
   after speech, or at `MESSAGE_MAX_S` (60 s); a superseded activation returns None. Returns mono int16.
   Kept in memory only.
5. **Voice note** (`voice_notes.py`, new): `encode_voice_note(rate, pcm) -> bytes | None`: GStreamer
   `appsrc ! audioconvert ! audioresample ! opusenc ! oggmux ! appsink`, 48 kHz mono, 32 kbit/s; None on
   failure (logged).
6. **Link:** `WhatsAppLink.send_audio(chat, ogg)` (`phone`, `ptt=true`, `audio` part `message.ogg`,
   `audio/ogg`), sharing `send_image`'s retry and deadline rules.
7. **Wiring:** `ToolDependencies.record_message`; `main.py` sets it to `hybrid_runtime.record_message`.
   `whatsapp_photos_configured()` still gates the link; contacts are optional.

## Implementation order

1. Contacts + matching, `send_audio`, `voice_notes`, `beep` with tests.
2. `send_photo` kind and the "who?" question (step 1 of the household plan).
3. `record_message` in the runtime and the `message` kind (step 2).
4. Docs, `.env.example`, README; gate; the contacts file and a live check on the Mac mini.

## Acceptance criteria

- "Send a picture to Sergio" → countdown → the photo in "Reachy <> Sergio", "Sent to Tío Sergio!".
- "Send a message to Tío Sergio" (and "vamos a mandar un mensaje a Sergio") → the prompt, a beep, the
  message recorded until 3 s of silence → a voice note in "Reachy <> Sergio" that plays back clearly.
- "Send a message" → "Who should I send it to?" → "Sergio" → the same.
- A name not in the contacts file never sends anything; nothing is sent to a chat that is not a group in
  that file.

## Test strategy

- `tests/test_whatsapp_contacts.py`: loading (missing file, bad entries skipped, non-group ids refused),
  matching (aliases, articles, accents, longest alias, ambiguity, unknown).
- `tests/test_whatsapp.py`: `send_audio` fields (`ptt`, part name and type) and the shared retry rules.
- `tests/test_voice_notes.py`: a tone encodes to an OGG Opus file (`OggS`, `OpusHead`), skipped without
  GStreamer; failure → None.
- `tests/test_quick_commands.py`: the grammar in four languages, with and without a name; photo to a
  contact; message happy path, nothing heard, failures; the "who?" question and its answers (known,
  unknown, cancel); Maribel untouched; no contacts → conversation; privacy: only contact chats.
- `tests/test_hybrid_runtime.py`: `record_message` plays the beep before recording, returns the speech,
  ends on silence and at the cap, returns None with no speech or when superseded or outside a command,
  and keeps the post-activation queue quiet.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| A kid pauses longer than 5 s mid-message | The silence ending is 5 s now that a touch ends a message. |
| A bump on the table ends a message early | The head must move more than 1 mm or 0.7 degrees in 0.2 s; the untouched head moved at most 0.38 mm. The parents see a cut-off message in the group. |
| Speech recognition mishears the name | The "who?" question; nothing is sent to an unknown name. |
| The recording picks up Reachy's own beep | Recording starts after the beep has played. |
| Background noise keeps the VAD "speaking" until 60 s | Capped at 60 s; the parents see it in the group. |
| A message that also carries the words ("send Sergio a message saying…") | Not matched (extra words) → conversation; the Gemini path is a later step. |

## Out of scope

Replies, Abuelo and nonni groups (only contacts-file entries), the Gemini path for
messages, video.

## Execution log

- 2026-10-29: plan written from the household's answers; "Reachy <> Sergio" group created
  (`120363413010779824@g.us`).
- 2026-10-29: built and deployed on the Mac mini (branch `feat/family-messages`); contacts file
  `~/.config/reachy-whatsapp/contacts.json` with Tío Sergio. Live: "Richie, vamos a mandar un mensaje a
  Sergio" → 10.2 s recorded, ended by silence, 42 KB voice note sent in 1.1 s (22.4 s request to done);
  "Richie, mándale una foto a Sergio" → photo (1 face) sent in 0.9 s, 8.1 s in all. Both arrived.
- 2026-10-29: household feedback: for the photo his head did not turn to the person asking. The logs show
  no speaker turn for either photo today: speaker following turns only for voices more than 25° off, and
  the earlier visible move was him rising from sleep. Photos now follow the face in view from the
  countdown to the shot (`QuickReply.before` → `MovementManager.frame_faces`), then ease back.
- 2026-10-29: second live photo try. Nine wakes before it failed the name check (the wake-word work in
  [missed-calls-spanish-richi.md](missed-calls-spanish-richi.md)). The request was heard as "puedes
  poner una foto a Sergio" and went to Gemini, whose `take_photo` had no recipient: the photo went to
  "Reachy group", not Sergio's. During the conversation speaker following turned him to -51°, -90° and
  -150°, away from the speaker, so the photo had no face. Fixes: "pon / poner … a [x]" with a recipient;
  `take_photo(to=...)` resolves contacts and takes nothing for an unknown name; while a photo is framed
  speaker turns wait (30 s at most); the tool frames like the quick command. Not fixed here: why speaker
  following turned the wrong way.
- 2026-10-29: the wrong turns. The face tracker recognized the household member at -64° to -71°, on
  Reachy's right where he stood, so the first turn (-51°) and the left/right convention were right. The
  -150° turn came from a few stray readings: a turn needed only 2 in 1.5 s, and a test on the mini showed
  readings in a talking room spanning -89° to +13° within seconds, while Reachy's own voice through his
  speaker was flagged as speech in 7 of 65 readings. A turn now needs 3 readings with 60% within 25° of
  their median, and readings while his speech plays (plus 0.5 s) are dropped. See
  [speaker following](../features/speaker-following.md). A front / left / right test with a person is
  still to do.
- 2026-10-29: tooling for the touch-to-end step. The first probe ran outside the app, which cannot see
  where the head is told to be (the daemon's state API omits targets in 1.11), and stopping the app also
  stopped the daemon. "Richie, head test" / "haz la prueba de cabeza" now runs the test inside the app
  (see [quick commands](../features/quick-commands.md#head-touch-test--2026-10-29)); its CSV decides
  tap, press or neither.
- 2026-10-29: first head test. Touches show (taps 2-3 mm / 1.5 degrees, presses about 3 mm / 2 degrees,
  the firmest 8.5 mm / 7 degrees) against at most 1.4 mm / 1.1 degrees untouched, but breathing still moved
  the head and the cues ran 3-4 s late. The test now holds the head still and starts each phase after its
  cue is heard; a second run sets the touch threshold.
- 2026-10-29: second head test, head held still. A pushed head stays where it was pushed, so a touch is read
  as sudden movement (more than 1 mm or 0.7 degrees within 0.2 s), not distance from the command. That never
  fired untouched (at most 0.38 mm / 0.19 degrees) and fired during taps, presses and pats alike. Touch-to-send
  can use it while the head is held still.
- 2026-10-29: touch-to-send. While a message records, the head holds still with the speech wobble off, and
  `HeadTouchDetector` counts sudden moves (`MovementManager.head_touches`); a touch after speech starts ends
  the recording, less 0.6 s for the hand. The prompt says to touch his head when done; the silence ending
  went from 3 s to 5 s. Replaying both head-test recordings: 17 touches in the touch phases, none when
  untouched, none on the breathing run. Also: the post-activation queue warned once per dropped frame
  (6,912 lines in the 90 s test); it now warns once per filling and logs the total when cleared.
