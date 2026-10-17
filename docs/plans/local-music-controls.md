# Local music controls and a local command model

- Status: `in-progress` — V1 accepted on hardware; V2 and V3 planned
- Owner: Reachy project
- Created: 2026-10-13
- Last updated: 2026-10-14
- Scope: Spotify behaviour around wake-word conversations on the Reachy Mini Lite
  (hybrid mode), local music commands without Gemini, and a later small local model
  for commands that need light reasoning

## Goal

Make music behave like a smart speaker: music should not stop Reachy from hearing the
household, a conversation should end once music is playing, and simple music commands
should work instantly without a cloud session.

## Background

On 2026-10-13, music Reachy started kept playing near the robot microphone. Gemini
heard it as continuous speech, never answered the user, and the session never went
idle, so the wake word could not be used again. The acoustic wake detector also scores
"Reachy" poorly over music (0.48 observed).

## V1 — lower music and wind down (implemented 2026-10-13)

- In hybrid mode, `SpotifyDucker` lowers music on the configured device to
  `SPOTIFY_DUCK_VOLUME_PERCENT` (default 20) when a cloud conversation opens, and also
  lowers music started during the conversation. The previous level is restored when
  the conversation ends unless the user changed the volume meanwhile.
- New `volume` action on the Spotify tool.
- Wind-down: when Reachy starts music in a conversation, the tool reply asks the model
  for only a brief "Enjoy!", and the runtime closes the conversation once Reachy has
  finished speaking and no final user transcript has arrived for 5 s
  (`_MUSIC_WIND_DOWN_SILENCE_S`, after a 3 s reply grace). Gemini's speech and busy
  flags are ignored for this rule because music keeps them set. The music is still at
  the lowered level during the window and returns to full volume after the close.

Acceptance (pending on hardware): asking for a song ends with "Enjoy!", the session
closes about 5 s later and the music returns to its previous level; music already
playing is lowered at the wake; "set the volume to 40" works.

## V2 — local music commands (planned)

Handle transport and volume commands on the robot, like the existing local head
commands, without opening Gemini.

Commands (all four household languages):

| Intent | English | Spanish | Italian | Portuguese |
|---|---|---|---|---|
| Pause | pause, pause the music, stop the music | pausa, para la música | pausa, ferma la musica | pausa, pare a música |
| Resume | resume, play, continue | continúa, reanuda, sigue | riprendi, continua | continua, retoma |
| Next | next, skip, next song | siguiente, salta | prossima, salta, avanti | próxima, pula |
| Louder | louder, volume up, turn it up | más alto, sube el volumen | più alto, alza il volume | mais alto, aumenta o volume |
| Quieter | quieter, volume down, turn it down | más bajo, baja el volumen | più basso, abbassa il volume | mais baixo, abaixa o volume |
| Set level | volume 40, set the volume to 40 | volumen 40 | volume 40 | volume 40 |

Design:

1. Lower music at the wake itself (`LOCAL_COMMAND`), not only when a cloud session
   opens, so local STT can hear the command over music; restore if the wake ends
   locally.
2. Route the text after an acoustic wake through `LocalIntentRouter` as the transcript
   wake already does; only exact, unambiguous matches stay local, everything else
   (including "play <something>") opens Gemini as today.
3. Add a Spotify executor action reusing `spotify_access_token`; louder/quieter step
   by 15 points; acknowledge with the existing nod; on failure escalate to Gemini so
   the user hears what went wrong.
4. Tests per language and intent, no-match escalation, wake-time lowering and restore,
   and Spotify failures. Metrics: local command counts and router rejections already
   exist.

Risks: Whisper accuracy for one-word commands over music; the Latin-only filter must
not drop short commands; household members calling unrelated "next" or "pause" while
addressing someone else (the wake word guards this).

## V3 — small local command model (idea)

Replace or back the exact-phrase router with a small local language model (about 3B
parameters, run on the Mac through a local server) that turns free-form text into tool
calls with a confidence score, for example "turn this down a bit", "skip this one, I
don't like it", "pon algo más tranquilo". No voice: it only acts (nod) and escalates
anything needing a spoken answer or knowledge to Gemini. Open questions: model choice
and multilingual accuracy, latency next to Whisper on the same Mac, the confidence
threshold for acting locally, and keeping tool authorization limited to the live
request.

## Progress log

| Date | Update |
|---|---|
| 2026-10-13 | Plan created. V1 implemented with tests: wake-time music lowering and restore, Spotify `volume` action, and the music wind-down ("Enjoy!", close after 5 s of quiet). Hardware acceptance pending. |
| 2026-10-14 | V1 accepted on the Reachy Mini Lite: a requested song ended with "Enjoy!" and the conversation closed about 5 s later with the volume restored; music already playing was lowered at the wake; "set the volume to 40" worked. |
| 2026-10-14 | Quick closing extended to any conversation while Spotify plays on the configured device: close 5 s after Reachy answers the latest turn (10 s after a question, 20 s cap without an answer). |
| 2026-10-17 | Quick closing accepted on the Reachy Mini Lite: with music playing, a weather question was answered over lowered music, the conversation closed about 5 s later and the volume came back. |
