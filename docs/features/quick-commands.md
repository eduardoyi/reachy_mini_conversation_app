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
| "turn off the lights", "apaga las luces", "spegni le luci", "desliga as luzes" | all lights and plugs | "Okay." / "Listo." / "Fatto." / "Pronto." |
| "turn on the dining lights", "enciende la luz del comedor" | an area, room, or device | same |
| "dim the lights", "lights to 30%", "pon las luces al 50 por ciento" | brightness | same |
| "make the TV lights blue", "pon las luces azules" | color | same |
| "turn on the TV", "pon la tele", "open Netflix", "put Bluey on YouTube" | TV | "Okay." / "Here's Bluey on YouTube." |
| "play Bad Bunny", "pon a Bad Bunny", "play some jazz" | Spotify search, then play | "Playing Bad Bunny." (music starts after) |
| "pause", "stop the music", "next song", "siguiente", "resume" | Spotify, or the TV when it is what plays | silent |
| "volume up", "louder", "baja la música", "set the volume to 40" | Spotify while it plays, else the TV when on | silent |
| "what time is it?", "qué día es hoy?" | clock | "It's 10:28 AM." / "Hoy es domingo, 18 de octubre." |
| "what's the weather?", "va a llover mañana?", "che tempo fa a Roma?" | Open-Meteo | one or two sentences |
| "go to sleep", "buenas noches" | standby | "Good night!", then the sleep pose |
| "never mind", "nada", "lascia stare" | nothing | silent |

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

## Implementation

- `quick_commands.py`: `parse()` (pure; normalized text, politeness stripped, at most 14 words, whole
  utterance must match) and `QuickCommands.handle()` (runs a match; `None` means "use the
  conversation"). Spanish and Portuguese share verbs ("apaga"); the rest of the sentence decides.
  Misspelled wake words ("Richie") are handled by the runtime's wake pattern.
- `local_speech.py`: `LocalSpeech.stream()` streams Gemini TTS (`LOCAL_SPEECH_MODEL`, default
  `gemini-3.8-flash-lite-tts`) in `GEMINI_VOICE`; first audio in 0.4–0.6 s (measured). Short fixed
  phrases (`FIXED_PHRASES`: "Okay.", "Listo.", "Good night!"…) are cached in
  `~/.cache/reachy-mini-conversation-app/speech`. Without the
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
