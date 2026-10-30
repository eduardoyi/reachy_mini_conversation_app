# Plan: WhatsApp voice calls through Reachy

- Status: `in-progress`
- Owner: household
- Created: 2026-10-30
- Last updated: 2026-10-30
- Feature doc: none yet (`docs/features/whatsapp-calls.md` once stage 3 lands)
- Builds on: [Voice messages and photos to family groups](whatsapp-family-messages.md)

## Goal

"Richie, call Abuela" rings her WhatsApp from Reachy's spare number. When she answers, her voice
comes out of Reachy and his head moves with it as it does when he speaks; his microphone carries the
room back to her. Approved contacts can call the spare number too: Reachy says "Abuela is calling" and
answers when told. Nobody outside the approved list is ever called or answered.

## Decisions (with the household, 2026-10-30)

1. **A ringing WhatsApp call, not a link.** The ring matters; a call link opened in the browser was
   turned down for that reason.
2. **One number.** No second number for Reachy: calls use the spare number GOWA already uses, as a
   second linked device of the same account (WhatsApp allows several). GOWA keeps photos and messages.
3. **Unofficial calling, its risk accepted.** Calls use [meowcaller](https://github.com/purpshell/meowcaller)
   (MIT, pure Go, on whatsmeow), which implements WhatsApp's VoIP stack. Calling from an unofficial client
   is outside WhatsApp's terms and can get the number banned, which would take photos and messages with
   it. The household prefers this to two numbers (official Business calling on the Twilio number) and to
   phone calls (Twilio rates: about $1.80 per 10 min to mobiles in Spain and Venezuela).
4. **Twilio phone calls and the official WhatsApp Business calling API are not used** (cost, and a
   second number; the official API also cannot keep GOWA's casual group photos and messages).
5. **Approved first: only Eduardo's own phone.** Its number lives in the contacts file on the mini, never
   in the repo or the logs.
6. **Only Eduardo or Adriana start calls.** The kids answer and talk, never dial. Reachy checks with face
   recognition that one of them is in front of him before calling.
7. **Incoming calls are announced first,** by voice and with caller id on the TRMNL ("Abuela is
   calling" with her name), and answered only when told.
8. **No quiet hours that decline calls.** At night, during naps, or whenever Reachy is asleep, an
   incoming call rings silently: no voice, just a small antenna movement and the caller id on the TRMNL,
   so someone nearby can still answer.

## Context (current state this relies on)

- GOWA (`go-whatsapp-web-multidevice`, not our code) runs on the Mac mini as
  `com.eduardo.reachy-whatsapp` from `~/.local/share/reachy-whatsapp` (`./whatsapp rest`, port 8792,
  basic auth). Its `--auto-reject-call` option is off (not set in its `.env`), so it does not decline a
  call the call service should answer. Checked 2026-10-30.
- `whatsapp_contacts.py`: `Contact(name, chat, say)` loaded from `WHATSAPP_CONTACTS_FILE`; only group
  ids (`@g.us`) are accepted, matching the rule "Reachy may message only the defined groups". Calls go to
  people, so this file needs a per-person call field (step 4).
- Reachy's head sways with whatever the app plays: the daemon's head wobbler
  (`reachy_mini.media.audio_gstreamer`) follows played audio, switched by `robot.enable_wobbling()` /
  `disable_wobbling()` (`quick_commands.switch_wobble` / `restore_wobble`). Playback goes through
  `console.LocalStream._push_playback`.
- `HybridRuntime` modes: `LOCAL_AMBIENT`, `LOCAL_COMMAND`, `CLOUD_CONVERSATION`, `STOPPING`. Mic frames
  feed the wake models and ambient STT in `LOCAL_AMBIENT`.
- `HeadTouchDetector` (`moves.py`) needs the head held still (`hold_still`) and wobbling off, so it
  cannot end a call while the head moves with the caller's voice. `AntennaTouchDetector` (a held antenna
  bend, 14°) works while the head moves.
- Face recognition (`face_identity.FaceRecognitionService`): `identify_now()` matches the faces in front
  of the camera now; `person_in_front(max_age_s)` returns the last person seen recently. Eduardo and
  Adriana are enrolled.
- The TRMNL e-ink screen (`eink_display.py`): `show(png, owner, expires_after_s=...)` puts a picture up
  for a while; `show_home()` puts the home screen back.
- Reachy's audio chip (XMOS, configured at startup: `PP_*` settings in `console.py`) cancels his own
  speaker in what the mic hears during conversations.

## Design

1. **Call service** (`tools/whatsapp-calls/`, Go, new): a small program built on meowcaller, linked to the
   spare number as its own device (paired once by QR from the spare phone), session stored under
   `~/.local/share/reachy-whatsapp-calls/` (mode 700). It listens on `127.0.0.1` only and talks to the
   app over one local WebSocket:
   - app → service: `call(jid)`, `answer(call_id)`, `decline(call_id)`, `hang_up()`, mic audio frames;
   - service → app: `ringing(from_jid)`, `connected`, `ended(reason)`, caller audio frames.
   It refuses any `call` or `answer` whose number is not in its own copy of the approved list, so the
   rule holds even if the app misbehaves, and declines unknown incoming calls itself.
   Runs as a launchd agent next to GOWA, built for `darwin/arm64`, meowcaller pinned to one commit.
2. **Approved list:** each contact in the contacts file may get a `"call": "<phone>@s.whatsapp.net"`
   field. Only entries with it can be called or answered. Group ids stay the only message targets.
3. **Call mode in the app** (`whatsapp_calls.py`, new; `HybridRuntime` gains a `CALL` mode): while a
   call is on, mic frames go to the call service instead of the wake models, ambient STT and Gemini;
   caller audio is resampled to the playback rate and played through `_push_playback`, so the wobbler
   moves the head; breathing and speaker turns pause; antennas stay still so a bend reads as a touch.
4. **Commands:** "call / llama a / chiama / liga para [x]" (quick commands, four languages, contact names
   as for messages). Reachy first runs `identify_now()`: only if Eduardo or Adriana is in front of him does
   he say "Calling Abuela…" and ring; anyone else (or nobody recognized) hears "Only Mom or Dad can start
   a call." Gemini gets no calling tool, so a call never starts from a conversation. "Hang up / cuelga" cannot be heard (no wake in a call),
   so a call ends by an **antenna bend**, by the other side hanging up, or at a 60 min cap.
5. **Incoming:** an approved contact calls → the TRMNL shows a caller id card (their name, "is
   calling") for as long as it rings, and Reachy says "Abuela is calling" and listens once without the
   name (`expect_answer`): yes / sí / answer or an antenna bend answers, anyone may answer (kids too);
   no declines; silence lets it ring out. Asleep (night, naps) or in quiet mode: no voice, only a small
   repeating antenna wiggle and the caller id; an antenna bend still answers. In a conversation: the
   conversation ends and the call is announced. When the call ends or rings out, the TRMNL goes back to
   what it showed.
6. **Privacy:** no call audio is stored or sent anywhere but the call; logs carry the contact's name
   and call length, never numbers or audio.

## Stages and acceptance

1. **Does it connect?** The call service links to the spare number and calls only the household's own
   phone, plays a test sound, saves what it hears to a local file (deleted after the check) and hangs
   up. Accepted when: calls connect both ways at least five times; GOWA still sends photos and messages
   during and after; no WhatsApp warning or logout over the next week.
2. **Through Reachy.** Caller audio out of Reachy's speaker, his mic back to the call. Accepted when:
   the far end does not hear itself (echo), delay feels like a normal call, the head moves with the
   caller's voice.
3. **The nice parts.** Antenna bend hangs up; incoming calls are announced with caller id on the TRMNL
   and answered; a silent ring while asleep; only Eduardo or Adriana (by face) can start a call; wake and
   Gemini stay off during the call and come back after it.
4. **Real use.** "Call Abuela" in four languages, the approved list, feature doc, tests.

## Risks and mitigations

- **Ban of the spare number** (photos and messages go too). Calls only to and from approved contacts
  who have the number saved; no group calls; no automatic redial; stage 1 runs a week before anyone else
  is called. If WhatsApp warns or logs the device out, calls are switched off and the risk revisited.
- **A WhatsApp update breaks calls** (meowcaller lags): the call service fails on its own; photos and
  messages keep working; Reachy says "I can't make calls right now."
- **Echo:** the chip's echo cancelling may not cover call audio. Stage 2 checks it; fallbacks are a
  lower call volume or half-duplex while the caller speaks.
- **Audio format:** meowcaller sends and receives 16 kHz mono float32 frames of 960 samples (60 ms)
  (`meowcaller.SampleRate`, `FrameSamples`); the app resamples to and from its own rates.
- **The spare phone rings too** on every call (linked devices do): keep it muted.
- **MLow** is WhatsApp's own codec, reimplemented by meowcaller; noted with the dependency review below.

## Dependencies (review before adding)

- meowcaller (MIT, Go, about 280 stars, no tagged releases, group calls experimental), pinned at
  `27a3c6b` (2026-08-10), the last commit on upstream whatsmeow (`go.mau.fi/whatsmeow`, MPL-2.0, the
  library GOWA is built on). Its next commit (2026-09-07) moved to `polymorfa/hypermeow`, a company's
  fork of whatsmeow for business deployments; not adopted, to keep to the widely used library. Its own
  CLI example does not build at the newer commit. Network: WhatsApp's servers only. Its `DISCLAIMER.md`
  (read 2026-10-30) asks for research with one's own accounts and devices, or with the other party's
  consent in test calls: stage 1 calls only Eduardo's own phone.
- **Since 2026-10-30, with the household's OK: the unmerged fix in meowcaller PR 26**
  (`pedrobartolini/meowcaller` at `3494d98`, four commits on top of our pin, `replace` in
  `tools/whatsapp-calls/go.mod`). It binds every relay the offer lists, so a phone caller that moves its
  media to a closer relay after the accept is still heard (upstream issues #21, #22, #36, #40, all
  open). Reviewed before use: it adds only relay fan-out code over the library's own relay package (no
  new network, file or process access). Go back to upstream once a fix for incoming calls is merged
  there; PR 49 (relay election steering) is a later alternative on the hypermeow line.
- Go toolchain to build it (on the laptop, cross-compiled for the mini).

## Tests

- Go: the approved-list check in the call service (calls, answers and declines).
- Python: `whatsapp_calls.py` with a fake call service (call flow, decline of unknown callers, antenna
  bend hangs up, mode changes in `HybridRuntime`), quick-command grammar for "call [x]", contacts with
  the `call` field, the face check (Eduardo or Adriana call; a kid or nobody is refused), the caller id
  card shown and removed, the silent ring while asleep.
- Live: the stage acceptance checks above, recorded in the progress log.

## Out of scope

Video calls (meowcaller supports them; Reachy's camera could come later), group calls, calling anyone
not on the approved list, recording calls.

## Open questions

1. Is face recognition enough to gate calls, or should a call to anyone other than Eduardo also be
   confirmed by voice ("Call Abuela?" "Yes")? Decide at stage 4, when more contacts are added.

## Progress log

- 2026-10-30: Options compared (Twilio phone calls, official WhatsApp Business calling, a call link,
  Telegram, unofficial WhatsApp calls); the household chose a ringing WhatsApp call on the one spare
  number. GOWA's call auto-reject checked off on the mini. Plan drafted; nothing built.
- 2026-10-30: Stage 1 tool written: `tools/whatsapp-calls/` (Go module, `reachy-calls pair | call |
  wait`), cross-compiled for the mini (pure Go, no cgo), installed in
  `~/.local/share/reachy-whatsapp-calls/` (mode 700) with the approved list in
  `~/.config/reachy-whatsapp/calls.json` (mode 600, Eduardo only) and a `say` test sound. It refuses
  anyone not on the list, declines unknown callers, logs names never numbers, hangs up after 60 s
  connected or 60 s unanswered, and prints how much of the other side it heard. Linking to the spare
  number waits on the spare phone.
- 2026-10-30: Linked to the spare number with a pairing code (shows as "Google Chrome (Mac OS)"); GOWA
  stayed connected and logged in. **First call worked:** 12:47 to Eduardo's phone, connected 5 s after
  ringing, 16.8 s connected, the test sound heard clearly on the phone, 11.1 s of his side received
  (voice 3.2 s, peak RMS 0.35; the first 3 s of the recording silent). The local transcript of the
  recording read as his reply that he heard it perfectly; the recording was deleted after the check.
  Warnings at hang-up only (an SRTCP packet after the end). Calls so far: 1 outgoing, 0 incoming.
- 2026-10-30: **Incoming calls silent from the phone's side** (calls 2-4, Eduardo calling the spare
  number): answered within 1 s and the greeting heard on the phone, but only 4-8 small comfort-noise
  packets ever arrived; the phone then showed "Reconnecting". Packet diagnostics (deleted after the
  check: they hold key material) showed the phone's media leaving the relay we were bound to right after
  the accept, matching upstream issues #21/#22. Switched to PR 26 (above).
- 2026-10-30: **Call 5, incoming with PR 26: worked.** Fan-out to all 3 offered relays (IAD, LGA, LHR);
  23.9 s of his side received with no gaps, the local transcript read his counting "one" to "seventeen"
  (recording deleted). The phone still showed "Reconnecting" after Reachy hung up at 30 s: meowcaller's
  `hangup` reports the end before it sends the terminate, and the tool closed the connection in between.
  The tool now waits 2 s after a call ends. Calls so far: 1 outgoing, 1 incoming that worked (3 earlier
  incoming silent).
- 2026-10-30: **Call 6, outgoing, Reachy hanging up at 15 s: ended cleanly** on the phone (no
  "Reconnecting") with the 2 s wait. Calls so far that worked: 2 outgoing, 1 incoming. GOWA connected
  throughout. Left for stage 1: about two more calls and a week without a WhatsApp warning or logout
  (watch until 2026-11-06).
- 2026-10-30: Household answers: only Eduardo approved first (stage 1 calls his phone); only Eduardo or
  Adriana start calls, checked by face; incoming calls announced with caller id on the TRMNL; no
  declining quiet hours, a silent ring with antenna movement and caller id instead.
