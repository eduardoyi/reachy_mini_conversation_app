# Feature: quick commands (everyday requests without the cloud conversation)

- Status: `in-progress` (built, unit-tested, components checked live; not yet tried by voice)
- Last updated: 2026-10-18

## Overview

Reachy answers everyday requests the way a smart speaker does: "Reachy, turn off the lights",
"pause", "what time is it?", "¿qué tiempo hace?", "metti la musica", "desliga a TV". The local
transcript of the request is matched against a small grammar in English, Spanish, Italian and
Portuguese. A match runs the existing tool (`home_lights`, `spotify_control`, `tv`) or answers from
the clock or Open-Meteo, and Reachy replies with a short sentence in the request's language, in the
Gemini Live voice. The Gemini conversation is never opened. Anything else, anything the grammar is
unsure of, a light name that matches nothing, or any failure goes to Gemini exactly as before.

Why: on 2026-10-18 a request took 8–9 s from the wake word to Reachy's answer (about 4–5 s for the
local transcript of the request, 1 s to open Gemini Live, 3–5 s of thinking and tool calls). A quick
command answers about 1 s after the person stops talking (0.6 s end-of-speech silence, local
Whisper, then the action and the first reply audio in 0.4–0.6 s).

## User-visible behavior

| Say | Does | Reply |
| --- | --- | --- |
| "turn off the lights", "apaga las luces", "spegni le luci", "desliga as luzes" | all lights and plugs | silent (speaks only if a light did not respond) |
| "turn on the dining lights", "enciende la luz del comedor" | an area, room, or device | silent |
| "dim the lights", "lights to 30%", "pon las luces al 50 por ciento" | brightness | silent |
| "make the TV lights blue", "pon las luces azules" | color | silent |
| "turn on the TV", "pon la tele", "open Netflix", "put Bluey on YouTube" | TV | silent (speaks only on failure) |
| "play Bad Bunny", "pon a Bad Bunny", "play some jazz" (also "Richie plays X", how "Richie, play X" is often heard; 2026-10-30) | Spotify search, then play | "Playing Bad Bunny." (music starts after) |
| "pause", "stop the music", "next song", "siguiente", "resume" (also a plain "stop", and "stop." followed by misheard words; 2026-10-30) | Spotify, or the TV when it is what plays | silent |
| "volume up", "louder", "baja la música", "set the volume to 40" | Spotify while it plays, else the TV when on | silent |
| "what time is it?", "qué día es hoy?" | clock | "It's 10:28 AM." / "Hoy es domingo, 18 de octubre." |
| "what's the weather?", "va a llover mañana?", "che tempo fa a Roma?" | Open-Meteo | one or two sentences |
| "go to sleep", "buenas noches" | standby | "Good night!", then the sleep pose |
| "never mind", "nada", "lascia stare" | nothing | silent |
| "head test", "haz la prueba de cabeza" | head-touch test (diagnostic) | seven cues, then "Done, thank you!" |

Rooms, areas and device names match across languages ("comedor" → Dining, "salón" → Living room,
"luces de la tele" → TV Lights). "Play with me" and other games are not music.

## Safety rules (from a review on 2026-10-18)

- Only words addressed to Reachy are matched: what follows his name in the same sentence, or the
  next sentence after "Reachy" alone. An acoustic wake whose transcript has no name ("we should turn
  off the lights") goes to Gemini, which can judge whether it was meant for him.
- Negations ("don't turn the lights off", "no apagues la luz") and questions or remarks ("is the TV
  on?", "who turned the lights off?", "mom said turn off the TV") are never commands.
- Bare words that are also everyday speech ("time", "sleep", "tiempo", "salta", "baja") are not
  commands; "play/pon X" refuses games, stories, timers, the table, movies, cartoons, clothes and
  context words ("that song again", "my favorite").
- A Spotify error is reported ("Sorry, that didn't work."), never taken as "nothing is playing".
- A command that times out is not handed to Gemini (it may have half run); a reply that stalls is
  abandoned after a cap so Reachy listens for his name again.

## Hearing the request — 2026-10-18

The first live tries failed in Whisper, not in the grammar: the continuous segmenter splits speech at
every short pause, so the request after the name reached STT as 0.6–1.4 s fragments, and
whisper-small turned them into "which you can't", "Good day.", "Thanks.". Now, after an acoustic
wake:
- Spotify is lowered at once (as a smart speaker does), not only when Gemini connects, and restored
  after a quick command.
- Everything said after the name is recorded as one clip from the post-wake audio until 0.8 s of
  silence (up to 8 s) and transcribed by `LOCAL_STT_REQUEST_MODEL` (whisper-large-v3-turbo, loaded
  by the launcher next to whisper-small). That text drives quick commands. If the request goes to
  Gemini, Gemini hears the clip's audio and gets the text only as a hint (see 2026-10-19 below).
  Measured: turbo heard "Reachy, ¿qué hora es?" correctly where small gave "¿Real si que hora es?",
  at about 1.3 s instead of 0.4 s per clip. Background speech stays on whisper-small.
- `QUICK_COMMANDS_LOG_TEXT=1` prints the request text when it goes to Gemini (debugging only).
- First live try of the clip (19:53): "Reachy, prende la luz" was clipped to "la luz" and went to
  Gemini. Right after startup the local worker was behind, so the wake was detected late and the
  start of the request was already before the activation barrier, outside the clip. The clip now
  also keeps audio after the wake that was still queued for local processing, or captured before
  the barrier but routed after activation. If the clip text still isn't a quick command, the fast
  transcript's request is tried before opening the conversation.
- Second live run (20:44): when the name is caught in the transcript instead of acoustically,
  only whisper-small hears the request, and "prende las luces" became "prendela los". Gemini got
  that plus the earlier "Reproduce Bad Bunny" from the background transcript, turned on the lights
  and played Bad Bunny again. Now, when whisper-small's request isn't a quick command, that
  segment's audio (the last three are kept in memory) is transcribed again with the accurate model
  and tried once more; Gemini gets the accurate text if it still goes there. The activation context
  also tells Gemini that requests in the background transcript were already handled.

- 2026-10-19 09:06: with music at 29, "Reachy, stop the music" was heard as "Start the music." (turbo)
  and "Sato music" (small). Lowering only took it from 29 to 20. Music now drops to a third of its
  level, at most `SPOTIFY_DUCK_VOLUME_PERCENT` (20), so 29 goes to 10. A level still lowered because a
  restore failed is not lowered again.
- The same request then went to Gemini as the text "Start the music." sent as the user's turn. The
  clip's audio had been dropped "so it is not heard twice", so Gemini resumed the music and only paused
  it when the user corrected it. Now, when a clip's request goes to the conversation, the clip's audio
  (including what was captured before activation) is what Gemini hears. The local text goes in the
  starting context as a hint that may be wrong. Checked against Gemini Live with a wrong hint ("Start
  the music.") and recorded "stop the music": the old way resumed, the new way paused, also with music
  and singing mixed in. First audio comes about 0.7 s later than with text (1.3–1.9 s after the
  request audio instead of 0.7–0.9 s); the tool call came at the same time or earlier. A transcript
  wake still sends text, since its request was spoken before activation and has no queued audio.

## Implementation

- `quick_commands.py`: `parse()` (pure; normalized text, politeness stripped, at most 14 words, whole
  utterance must match) and `QuickCommands.handle()` (runs a match; `None` means "use the
  conversation"). Spanish and Portuguese share verbs ("apaga"); the rest of the sentence decides.
  Misspelled wake words ("Richie") are handled by the runtime's wake pattern.
- `local_speech.py`: `LocalSpeech.stream()` streams Gemini TTS (`LOCAL_SPEECH_MODEL`, default
  `gemini-3.8-flash-lite-tts`) in `GEMINI_VOICE`; first audio in 0.4–0.6 s (measured). Short fixed
  phrases (`FIXED_PHRASES`: "Okay.", "Listo.", "Good night!"…) are cached in
  `~/.cache/reachy-mini-conversation-app/speech` and played from there without the silence Gemini pads
  them with (2026-10-29; `test_a_cached_phrase_plays_without_the_silence_around_it`). Without the
  network it speaks with macOS `say`. Calls are recorded in the usage ledger
  (`quick_reply_speech`, about $0.0003 a reply).
- `weather.py`: Open-Meteo forecast and geocoding (free, no key, about 0.25 s), spoken summaries in
  four languages, the home city's name localized ("Lisboa").
- `hybrid_runtime.py`: `_try_quick_command()` runs in `_run_activation` once the request text is
  known (acoustic or transcript wake) and before the cloud handler is built. The reply goes through
  a local output queue that `emit()` serves while no handler exists, so the normal playback loop
  plays it. The runtime stays in `LOCAL_COMMAND` until the reply has played (no self-wake), runs the
  reply's follow-up (start the music, lower the head), then returns to `LOCAL_AMBIENT`. Logs carry
  the command kind and timings, never the words.
- `tools/get_weather.py` and `tools/get_time.py` replace the remote weather and time Spaces in the
  household profile: inside a conversation they answer in 0.24 s and 0 s instead of 1.2–1.5 s.
- Settings: `QUICK_COMMANDS_ENABLED` (default on), `LOCAL_SPEECH_MODEL`, `HOME_LOCATION`,
  `HOME_LATITUDE`, `HOME_LONGITUDE`.

## Checked live — 2026-10-18

- Harness with the real runtime, commands and voice (no robot audio): time, first audio 0.58 s;
  date in Italian 0.39 s; weather in Spanish 1.46 s on the first request (one name lookup), weather
  fetch 0.25–0.55 s.
- Gemini Live models with a short prompt answer in 0.5–0.8 s (3.8 extended thinking low, 3.8,
  3.1 flash), so the model itself was not the slow part; context, tool round trips and the wait for
  the request were.
- Lights, music and TV changes were not exercised live (nobody home); the tools themselves were
  checked live earlier.

## Tests

`tests/test_quick_commands.py` (grammar in four languages, conversation requests left alone, light
targets, execution with fake tools), `tests/test_weather.py`, `tests/test_local_speech.py`, and the
quick-command tests at the end of `tests/test_hybrid_runtime.py`.

## Household routines — 2026-10-18

Phrases that set several lights at once live in the home layout file (`HOME_LAYOUT_FILE`, default
`~/.config/reachy-home/layout.json`), not in code, so each household names its own:

```json
"left_out_of_all_on": ["TV Lights"],
"routines": [
  {"say": ["movie mode", "modo cine", "modo película"], "on": ["TV Lights"], "off": ["all"]},
  {"say": ["lights", "luces"], "on": ["all"]},
  {"say": ["lights off", "darkness", "oscuridad"], "off": ["all"]},
  {"say": ["good night", "goodnight", "buenas noches"], "off": ["all"], "sleep": true, "tv": "off", "music": "pause"}
]
```

- A routine runs when its phrase is the whole request (after the name, politeness stripped), before
  the grammar, so "good night" now also turns everything off before Reachy sleeps. Silent on success.
- `on`/`off` take what `home_lights` takes (device, room, area or "all"); "all" in `off` means
  every light not in `on`. Offline devices are skipped.
- `left_out_of_all_on`: "turn on the lights" (any language, quick or Gemini) skips these unless
  named; turning everything off still includes them.
- `"tv": "off"` turns the TV off (in the background; the reply doesn't wait for the TV to confirm,
  and a TV that is already off gets no key). `"music": "pause"` pauses Spotify if it is playing.
- Gemini sees routines as scenes (`home_lights` action=scene), so "put movie mode on" works there too.
- Weather in one word: "weather", "clima", "el clima", "el tiempo".

## Volume words — 2026-10-18

- "lower" / "louder" (also "bájale" / "súbele") change Spotify by 5 (`SPOTIFY_VOLUME_STEP`, was 15).
  The step counts from the level before Reachy lowered the music to listen
  (`SpotifyDucker.level_before_ducking`), so at 30 "lower" gives 25, not 15. If music isn't
  playing and the TV is on, they change the TV volume.
- "set volume to 25" also accepts "said volume to 25%" (how Whisper often hears "set"), "put the
  volume at 25" and "volume 25".

## Requests for Maribel — 2026-10-28

A request that starts with "ask / tell Maribel…" (or the Spanish, Italian or Portuguese equivalent;
`maribel_request`) is checked before routines and the grammar, with no word limit, when Maribel is set up.
The reply says "Asking Maribel"; its `after` step sends the request to her and returns her spoken reply
(`after_timeout_s` 23 s). The reply sets `whole_request`, so a request clip cut off at its 8 s cap goes
to the conversation rather than sending half a sentence. See [Maribel hand-off](maribel-handoff.md).

## Photos for the WhatsApp group — 2026-10-29

"Take a picture (of us)" and its Spanish, Italian and Portuguese forms, polite infinitives included, parse
as kind `photo` (after the negation check, before `_NOT_A_COMMAND_START`). With WhatsApp photos configured
and the camera on, the reply has no text, the countdown as `QuickReply.beats` ("Three!", "Two!", "One!"),
`whole_request`, and an `after` step that takes, shows and sends the photo (`after_timeout_s` 22.4 s);
otherwise it returns None and the conversation handles it as before. All its phrases are in
`FIXED_PHRASES`. See [Photos to WhatsApp](whatsapp-photos.md).

## Messages and photos to family groups — 2026-10-29

Kinds `message` and `send_photo` (`{"to": phrase or None}`): "send a message / a picture to [x]" and the
Spanish, Italian and Portuguese forms. With a name from the WhatsApp contacts file the reply is the
message prompt (its `after` records with `deps.record_message`, encodes and sends a voice note) or the
photo countdown for that group; without one it asks who and hands the answer to the same flow through
`expect_answer`. See [Photos and messages to WhatsApp](whatsapp-photos.md).

## Head-touch test — 2026-10-29

"Head test" / "do the head test" / "haz la prueba de cabeza" (kind `head_test`, English and Spanish) is a
diagnostic for ending a voice message with a touch. The Lite has no motion sensor in the head, so a touch can
only show as the head being away from where the motors were told to put it, which only the app knows. The
reply speaks seven cues (`HEAD_TEST_CUES`: rest, tap, rest, press, rest, pat, rest), each phase lasting
`HEAD_TEST_PAUSE_S` (8 s) after its cue has been heard (`QuickReply.beat_pause_s`; `on_beat` records when).
From the start, as while a message records, the antennas perk (`set_listening(True)`), the head holds still
(`MovementManager.hold_still`: breathing ends where it is and speaker turns are skipped), the daemon's speech
wobble pauses (it moves the head out of the app's sight), and `MovementManager.start_head_recording` keeps
every control tick: the commanded and measured head poses, the seven head joints, and the commanded and
measured antennas (`HEAD_RECORDING_COLUMNS`). An antenna touch during the test is logged ("Antenna touched
during the head test; not waking") instead of starting an activation that would end it. At the end the rows
go to `~/.cache/reachy-mini-conversation-app/head-test/head-test-<time>.csv` with `t` counted from the start
and a `phase` column naming the last cue heard (`intro` before the first), and Reachy says "Done, thank
you!". A test cut short stops listening, holding and recording after `HEAD_TEST_MAX_S` (155 s); the hold and
the recording each lapse after 180 s on their own. The file holds only motor numbers. Code:
`head_touch_test.py`, `QuickCommands._head_test`, `MovementManager._record_head`. Tests:
`test_the_head_test_cues_touches_while_recording_then_saves_a_file`,
`test_a_head_test_records_the_commanded_and_measured_head_on_every_tick`,
`test_an_antenna_touch_during_a_head_test_does_not_wake`,
`test_holding_still_stops_breathing_where_the_head_is_and_keeps_it_from_restarting`,
`test_holding_still_skips_speaker_turns_and_ends_on_its_own`,
`test_beats_with_a_pause_wait_after_each_is_heard_and_report_it`, `tests/test_head_touch_test.py`.

First run, 2026-10-29 21:44 (2,572 readings at about 46 Hz). Breathing was still running: the commanded head
rose and fell 5 mm every 10 s and the measured head trailed it by about 0.55 s, sticking in steps. Untouched,
the head sat up to 1.4 mm sideways and 1.1 degrees of tilt from the command (lag removed). Taps showed as
2-3 mm sideways and about 1.5 degrees, presses as about 3 mm down, 2.5 mm sideways and 2 degrees, the firmest
touch as 8.5 mm and 7 degrees. More than 2 mm sideways or 1.5 degrees caught every touch burst and nothing in
the untouched stretches. The cues were spaced 8 s from the start of each, but each was synthesized first, so
the touches came 3-4 s late and spilled into the next phase; hence the pause after each cue is heard and the
still head for the second run.

Second run, 2026-10-29 22:06 (3,962 readings, head held still, no speaker turns or antenna wakes; one cue fell
back to the local voice after a Gemini timeout). Untouched, the held head sat within 0.06 mm and 0.05 degrees
of the command. But a touch moves it and it stays there: after the taps it rested 1.75 mm sideways and 1.1 mm
low, after the pats 2.2 mm and 2 degrees off, with no drift back. So distance from the command finds the first
touch and then reads "touched" forever. What works is sudden movement: the measured head moving more than
1 mm or 0.7 degrees within 0.2 s. Outside the 1.5 s after each "Stop", the untouched head never moved more than
0.38 mm or 0.19 degrees in 0.2 s, and every touch phase crossed the threshold several times (taps peaked at
3.3 mm, presses at 4.6 mm down and 3.9 degrees, pats at 4.2 mm).

## "Stop" with music playing — 2026-10-30

"Richie, stop" left the music playing several times in a row:

- The old local "stop" (`local_intent_router`, `LocalIntent.STOP`) took a plain "stop" first and only
  stopped Reachy moving. With quick commands set up it now steps aside, and the quick "stop" pauses
  the music when it plays (and stops moving otherwise).
- Music heard as words after the command ("Richie, stop. Oké, graag.") made the request too long to
  match. A pause or stop in the first sentence now counts on its own, unless the rest takes it back
  ("stop. no, keep playing"). Other commands still need the whole request.

Tests: `test_a_plain_stop_goes_to_the_quick_command_that_pauses_music`, the two new rows in
`test_everyday_commands_are_recognized`, and "stop. no, keep playing" in
`test_conversation_requests_are_left_to_gemini`.
