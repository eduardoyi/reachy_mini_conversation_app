# Feature: Games

- Status: `in-progress` (built and tested on the MacBook; not deployed or played on the robot yet)
- Last updated: 2026-10-31 (treasure hunt, wider colors, Simón dice, Venezuelan Spanish)
- Plan: [games.md](../plans/games.md)

## Overview

Reachy plays games with the household, started by name in Spanish or English, and they all start, pause and
end the same way. A game runs on the Mac mini with no Gemini session open: the room is already transcribed
locally, the lines are cached by the reply voice, and only narrow judgments go to the cloud (Jev on a camera
frame, a small fraction of a cent).

Three games so far. The Spanish is Venezuelan (2026-10-31): "listo", "bueno", "una media", "un carrito",
"rosado", "cachetes"; "ya va" pauses, and "listo", "chévere", "dale" say yes.

Two finding games on one engine (`games/hunt.py`):

- the **color hunt** ("juguemos a los colores", "let's play color hunt"): Reachy names a color, a tune speeds
  up for 30 seconds, and the children show him something of that color;
- the **treasure hunt** ("juguemos a buscar tesoros", "let's play treasure hunt"): Reachy names something
  from around the house (a ball, a shoe, a Lego, an animal, something round), the screen shows a drawing of
  it, and they have 60 seconds to find it and show it.

And **Simón dice** ("juguemos a Simón dice", "let's play Simon says"): Reachy names a still pose ("Simón dice:
¡tócate la nariz!"), the screen shows a child doing it, and the camera checks they do it and hold still.

Out of scope for now: flashing the room lights in the game's color (the light systems do not report a lamp's
current color, so it could not be put back exactly), pausing camera games when nobody is in view, writing
practice on the host, and every other game from the 2026-10-30 brainstorm.

Big Brain, the team quiz show played on phones, runs on the same host and has its own page:
[big-brain.md](big-brain.md). It adds `GameServices.say_audio` (recorded speech played as Reachy's line, through
`request_announcement(..., audio=...)`), which any game can use for a recorded voice.

**Scribble Rush**, the drawing race Reachy guesses with a vision model, uses Big Brain's phone server with its
own page: [scribble-rush.md](scribble-rush.md).

## User-facing behavior

- **Starting by voice:** "Richie, juguemos a los colores" / "vamos a jugar al juego de los colores" / "let's
  play color hunt" / "play the color game". Reachy asks first, with a one-line pitch: "¿Jugamos a los
  colores? Yo digo un color y tú me traes algo de ese color." "Sí", "vale", "dale", "yes", "ok", or a pat on
  his head starts it; "no" gets "Bueno, otro día."; silence or anything else (30 s) starts nothing. The yes
  guards against starts from the TV or chatter. "Juguemos" alone asks "¿A qué jugamos? Puedo jugar a los
  colores."; answering with a game's name starts it. "Play Coldplay" stays music.
- **Starting through Gemini:** the `play_game` tool is always offered. Gemini must read a game's rules
  (`action=rules`) before it may start it (`action=start`, refused otherwise or after 5 minutes), so it always
  asks first; then it says "¡Vamos!" and ends the conversation, and the game runs locally.
- **Starting from the phone:** the admin app's Home tab has a Games panel; Play starts a game at once (the tap
  is the yes) and wakes Reachy if he sleeps.
- **Language:** the game speaks the language it was asked for in.
- **While a game plays:** Spotify is paused (and not resumed afterwards), the game owns the e-ink screen (the
  music card waits), other announcements (Maribel's replies) wait until the game is paused or over, and the
  `home_lights` and `spotify_control` tools refuse changes.
- **Pause:** "pausa", "espera", "para el juego", "pause", "wait" heard without his name; or his name alone, or
  an antenna bend: "Pausa. ¿Seguimos o terminamos?". The screen shows PAUSA and how to go on; timers stop; the
  head is held still so a pat resumes. "Seguimos", "continue", "sí" or a pat plays on. 10 minutes paused ends
  the game. An incoming WhatsApp call pauses it.
- **Quit:** "se acabó el juego", "terminamos", "ya no quiero jugar", "stop the game", "game over" (with or
  without the name), or End in the admin app: "Listo, se acabó el juego." A bare "para" or "stop" does not
  count (children shout it in games).
- **Other requests mid-game:** "Richie, enciende la luz" gets "Estamos jugando a los colores. ¿Lo
  terminamos?". Yes ends the game and then turns the light on (as if just asked: quick command or Gemini); no,
  or 20 s of silence, plays on.
- **After a game:** the screen shown before comes back, the head moves freely again, Reachy listens as usual.
  A game nobody plays for 5 minutes ends by itself.

### The color hunt

- Each round: "¡Busca algo ROJO!", the screen shows ROJO above a row of five stars, and a staccato tune
  plays, speeding up from 100 to 180 BPM over 30 seconds of play.
- About twice a second a camera frame that changed (or one every 1.5 s if nothing did) goes to Jev:
  "is someone holding an object up to the camera?" (a Noul) and "what is its main color?" (a Choice).
- Two of the last three frames held up (≥ 0.5) with the target's color find it: the tune stops, a happy
  move, "¡Sí! ¡Rojo!", a filled star.
- Eleven colors, each accepting its neighbors (generous on purpose, chosen 2026-10-31; Reachy's camera
  shows red and hot pink as orange): rojo (red, orange, pink), naranja (orange, red, yellow), amarillo (yellow,
  orange), verde (green), azul (blue, purple), morado (purple, pink, blue), rosa (pink, red, orange), marrón
  (brown, orange, black), blanco (white, gray), negro (black, gray, brown), gris (gray, white, black).
  Purple, brown and gray were not in the camera spike.
- A sure color outside the group (≥ 0.7) is named at most every 5 s: "Mmm, eso es azul. ¡Busca algo rojo!".
- A color already in view when the round starts (a bottle on the table) counts only after it has left.
- Time up: "¡Se acabó el tiempo! Era el rojo." Nobody loses. After five rounds: "¡Tres estrellas!",
  "¿Otra vez?"; yes plays five more, otherwise "¡Gracias por jugar!" and the game ends.

### The treasure hunt

- Things at home: una pelota, un zapato, una media, un libro, un peluche, un carrito, una cuchara, un vaso,
  una gorra, una muñeca, un Lego, un creyón o un lápiz. Groups: un animal, una fruta, algo con
  ruedas, algo redondo, algo cuadrado, algo blando, algo con letras, algo más pequeño que tu mano. No letters
  ("algo que empieza con P") and no combinations ("algo verde y redondo") yet. No repeats within five rounds.
- The screen shows a drawing of the thing on the left (for the 2-year-old) and its name on the right. The
  drawings are made once by the image model (about $0.03 each, 18 of them; the last two groups have none)
  and kept in `game-pictures/` beside the display cache, apart from the gallery's drawings. The first game
  draws the missing ones in the background; until a drawing exists its rounds show the name alone.
- Jev is asked "is the object held up {a ball of any kind}?": ≥ 0.6 counts (two of the last three frames). Held
  up surely (≥ 0.7) and surely not it (≤ 0.15): "Mmm, eso no. ¡Busca una pelota!", at most every 5 s. These two
  thresholds were not tested on real frames.
- Rounds last 60 s with the same tune speeding up; "¡Se acabó el tiempo! Era una pelota." when nothing is found.

### Simón dice

- 47 still poses Jev judges from one frame (no sounds or movement, chosen 2026-10-31): face and head (tócate
  la nariz, tápate los ojos, pon cara de bravo, saca la lengua, mira hacia arriba…), arms (levanta los brazos,
  haz el avión, saca músculos, brazos de zombi…), hands and fingers (pulgar arriba, un corazón con las manos,
  cachos de toro, enséñame dos dedos, orejas de conejo…) and the whole body (agáchate, siéntate en el piso,
  párate en un pie, voltéate de espaldas, haz una estrella…). `games/simon_says.py` `POSES` has each one's words,
  what Jev is asked ("Is a person in `frame` touching their nose with a finger?") and its drawing.
- Eight poses a game, random, close-up ones first; before the first whole-body pose "¡Da un paso hacia
  atrás!", after the last "¡Ahora acércate!". For "mira hacia arriba" and "mira hacia un lado" Reachy turns his
  head too, and back after.
- Each pose: "Simón dice: ¡tócate la nariz!", the screen shows a drawing of a child doing it (for the
  2-year-old; the 47 drawings are made once like the treasure ones) beside short words, then 8 s of play for
  two of the last three checked frames to show it (≥ 0.6, untested on real frames). Done: a cheer ("¡Muy
  bien!", "¡Eso!", "¡Chévere!", "¡Perfecto!"), a happy move, a star. Not done: "¡Así! Mira la pantalla." and on.
  Nobody loses; "¡Ocho estrellas!" and "¿Otra vez?" at the end. No trick rounds (without "Simón dice") yet.

## Current architecture

```mermaid
flowchart LR
  Room[Room transcript, no name] -->|room listener| Host
  Quick[Quick command: juguemos a X] -->|offer, addressed| Host
  Gate[Wake no quick command took] -->|cloud gate| Host
  Tool[Gemini play_game rules then start] --> Host
  Admin[Admin app Games panel] -->|control API /game| Host
  Host[GameHost: asking, playing, paused] --> Game[ColorHunt.play]
  Host --> Services[GameServices: speak, ask, show, tune, head, music, camera, Jev]
  Game --> Services
  Services --> Runtime[HybridRuntime: announcements, ambient audio, hold]
```

- The host (`games/host.py`) runs one game at a time: `offer` asks, `start` begins (pausing music and saving
  the screen), `heard` takes room transcripts, `addressed` takes every request by name while a game runs (and
  the start answer while asking), `pause` / `resume` / `end`, and a watch loop (every 50 ms) for pats, playing
  time, a ringing call and the idle and pause timeouts.
- A game is one coroutine, `Game.play(context)`. `GameContext.sleep` counts playing time only and
  `GameContext.playing()` blocks while paused, so a paused game's timers and tune stop by themselves.
- Hybrid runtime hooks: `set_room_listener` (games first, then writing practice), `set_cloud_gate` (a wake no
  quick command took reaches the host before Gemini; his name alone pauses), `set_announcement_hold` (other
  announcements wait while a game plays; game lines are marked `game=True`), `request_announcement(...,
  on_spoken=)` (a line has been heard: the tune starts after it), `play_ambient_audio` (the tune, accepted
  only while Reachy waits for his name; a wake clears it), `request_when_free` (the request a game ended for)
  and `loop` (the control API schedules game actions on it).
- Jev requests go through `jev.py`, shared with web pictures, and are recorded as `game_check` (the color hunt's
  first day as `game_color_check`).

## Canonical files and contracts

| Area | File or interface | Responsibility / contract |
|------|-------------------|---------------------------|
| Names and words | `games/catalog.py` | `GAMES`, `parse_game_request(text)`, `find_game`, `words`, the YES / NO / PAUSE / RESUME / QUIT word sets (whole transcripts, normalized) |
| Host | `games/host.py` | `GameHost(services, factories)`: `offer`, `start(key, language) -> str | None`, `heard`, `addressed`, `pause`, `resume`, `end`, `holding`, `status`; `host_phrases()` for the speech cache |
| Game interface | `games/base.py` | `GameServices` (wired in `main._build_games`), `GameContext`, `Game` (`phrases`, `play`, `heard`, `paused`) |
| Finding engine | `games/hunt.py` | `Hunt` (rounds, tune, frame loop, two-of-three, already in view, wrong line, "¿Otra vez?"), `Check`, `HELD_QUESTION`, thresholds from the 2026-10-31 spike |
| Color hunt | `games/color_hunt.py` | `ColorHunt`, `TARGETS` (accepted Jev answers per color), `color_hunt_phrases()` |
| Simón dice | `games/simon_says.py` | `SimonSays`, `POSES` (words, Jev question, drawing, whole body, head gesture), `pose_drawings()`, `simon_says_phrases()` |
| Treasure hunt | `games/treasure_hunt.py` | `TreasureHunt`, `TREASURES` (names, what Jev is asked, drawing subject), `treasure_drawings()`, `treasure_hunt_phrases()` |
| Jev client | `jev.py` | `ask(client, api_key, model, state, questions, images, feature)`, `sized_jpeg`, `JevError` |
| Tool | `tools/play_game.py` | `play_game(game, action=rules|start, language)`; direct (not a skill); added with `games_configured()` |
| Screens | `eink_display.py` | `render_game_prompt(word, stars, total, caption)`, `render_game_picture(picture, word, stars, total, caption)`, `render_game_paused(title, language)`; screen owner `game`; `game_picture(subject)` (kept only, never waits), `draw_game_pictures(subjects)`, `GAME_PICTURES_DIR` |
| Admin | `control_api.py` `/status` (`game`), `POST /game`; `admin/server.py` `POST /api/game` (audited); `static/admin` Games panel | |
| Config | `HYBRID_RUNTIME_ENABLED`, `QUICK_COMMANDS_ENABLED`, `TYPESAFE_API_KEY`, `DISPLAY_CHECK_MODEL` | Games need all three flags/keys; the color hunt also needs the camera |

## Extension guide

### Safe to extend

- A new game: a `GameInfo` in `catalog.py` (names in both languages with mishearings, title, pitch, rules), a
  `Game` subclass with its fixed lines in `phrases()`, its factory in `main._build_games`, and its phrases in
  the speech cache. The host gives it the yes, pause, quit, screen and music handling for free.
- More names or mishearings for a game, and more words in the word sets.

### Do not change without updating this document and its tests

- Nothing starts without a yes: the spoken question, Gemini's rules-before-start, or a tap in the admin app.
- During a game every addressed request reaches the host first (quick commands and the cloud gate).
- A bare "para" / "stop" is not a quit word.
- Game lines are `game=True` announcements; other announcements wait only while a game plays.
- The screen is put back and the head released when a game ends, however it ends.

## Constraints, privacy, and failure modes

- Camera frames go to TypeSafe (Jev) during the color hunt only, one at a time, never stored; with privacy on
  the camera returns nothing, so nothing is found. Starting a game from the phone is refused while privacy is on.
- A Jev failure skips that frame. A game line not heard within 20 s (Reachy busy) is skipped.
- Head pats only register while the head is held still: while asking and while paused. While playing, voice
  and the antennas are the controls.
- Reachy's own tune reaches the room transcription; game words must be heard over it (to check live).

## Testing

`tests/games/` (catalog, host with a waiting game and a fake clock, color and treasure hunts with a fake camera,
Jev and drawings),
`tests/tools/test_play_game.py`, the runtime hooks in `tests/test_hybrid_runtime.py`, quick-command routing in
`tests/test_quick_commands.py`, the screens in `tests/test_eink_display.py`, `tests/test_control_api.py` and
`tests/admin/test_server.py`. Jev thresholds come from the 2026-10-31 spike on 258 frames from Reachy's camera
(see the plan).
