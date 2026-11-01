# Feature: Scribble Rush (drawing race)

- Status: `in-progress` (first version built and tested with fakes on 2026-11-01; not deployed or played; the
  vision guesses not yet measured on real drawings)
- Last updated: 2026-11-01
- Plan: [party-games.md](../plans/party-games.md) ("Scribble Rush", "Vision guesser")
- Part of: [games.md](games.md) (the shared game host); phones as in [big-brain.md](big-brain.md)

## Overview

Teams take turns drawing a secret word on paper in a rush, and Richie is the only one who guesses. Players join
on their phones by QR code, as for Big Brain; the drawer's phone shows the word, the TRMNL is a race track with a
lane per team, and one camera frame of the drawing goes to a Gemini Flash vision model that gives up to three
guesses. Richie says them aloud one by one; the earlier he gets it, the more squares the team moves. Bilingual
(Spanish or English, a guess counts in either), adults by default with a kids mode and its own word list.

## User-facing behavior

1. **Start:** "Richie, juguemos Scribble Rush", "let's play Scribble Rush", "el juego de dibujar", "dibujo
   relámpago", "juguemos a dibujar", "drawing game" (and mishearings such as "escribel rush", "scribble brush"),
   then "sí". Gemini can start it with `play_game` (`scribble`).
2. **Join:** the TRMNL shows "SCRIBBLE RUSH", a QR code, the six-letter code and the three teams (red, blue,
   green) with their players. Each phone types a name and picks a team; "¡Empezar!" (or "¡empezamos!" in the room)
   starts, and a "Modo niños" switch sits next to it.
3. **A turn:** teams go in turn from a random first team, each team's players drawing in the order they joined.
   The TRMNL shows "ROJO DIBUJA", the drawer, the category and the round over the track. Richie calls the drawer
   ("¡Ana, del equipo Rojo, te toca dibujar! Mira tu teléfono… ¡ya!"); only the drawer's phone shows the word,
   with an "Otra palabra" button (once a turn; the clock keeps running) and "¡Listo! Enseñárselo a Richie". The
   other phones show who is drawing, the category and the countdown. A tick-tock speeds up for 20 s (30 s in kids
   mode).
4. **Showing:** on "Listo" or when time runs out, Richie says "¡Muéstramelo!", waits 2.5 s for the drawing to be
   held up, takes one camera frame and shows it on the TRMNL ("¿Qué será?") while he says "Mmm… déjame ver…".
5. **Guessing:** the model's short remark on the drawing, then each guess from least to most confident: "¿Es un
   perro?", "¿No? Mmm… ¿un zorro?", "¡Ya sé! ¿Un gato?". The phones list each guess as it is said and mark the
   hit. A hit on the first, second or third guess moves the team 6, 4 or 2 squares ("¡Sí! ¡Era gato! ¡El equipo
   Rojo avanza 4 casillas!", a happy move); a miss says the word ("¡Me rindo! Era gato.", a sad move) and moves 1
   square in kids mode ("¡Qué buen intento!"). The TRMNL then shows "ERA: GATO" and the track.
6. **The end:** the first team to reach square 24 wins at once; otherwise, after six rounds, the furthest team
   (or a tie). The winner screen, an excited move, "¡Ganó el equipo Rojo!" and thanks. The phones keep the final
   track for 30 s.
7. **Pause and quit** as every game; the phones show the pause with a "Seguir jugando" button.

## Current architecture

```
"juguemos Scribble Rush" -> GameHost -> ScribbleRush.play
  Phones (big_brain_phones.py, page=static/scribble/index.html) <-> on_message / snapshot
  turn: word -> drawer's phone; render_track -> TRMNL; tick-tock -> play_audio
  services.camera() -> one JPEG -> GeminiGuesser (one generate_content call) -> Guesses
  judge_guesses: code (guessed) then one Jev call for the rest -> say each guess -> squares -> render_track
```

The phone server is Big Brain's (`Phones`, with `page=` for this game's own page) on the same port and public
address; only one game runs at a time. The join and winner screens are Big Brain's (`render_join` takes a
`title`, default "BIG BRAIN"); the track is `scribble_screens.render_track`; the drawing is shown with
`eink_display.render_photo`.

## Canonical files and contracts

- `games/scribble_rush.py`: the game, `ScribbleSetup(port, guess, public_url)`, `GeminiGuesser`, `read_guesses`,
  `guessed` and `judge_guesses`. Timings and scoring are module constants: `DRAW_S` 20, `KIDS_DRAW_S` 30, `HOLD_S`
  2.5, `VISION_TIMEOUT_S` 15, `GOAL` 24, `ROUNDS` 6, `SQUARES` (6, 4, 2), `KIDS_MISS` 1, `JEV_HIT_P` 0.6.
- `games/scribble_words.py`: `KIDS_WORDS` (139: animals, food, things, vehicles, nature, body and clothes) and
  `ADULT_WORDS` (238: actions, movies, Venezuelan sayings, harder things and Venezuelan food, places, characters
  and jobs, sports and games). Each entry is `"Spanish forms / English forms"`, forms separated by `;`, the first
  of each side the word the drawer sees; every form counts as guessed.
- `games/scribble_screens.py`: `render_track(title, caption, lanes, goal, turn=)`.
- `static/scribble/index.html`: the phone page.
- `games/catalog.py`: `SCRIBBLE_RUSH` (key `scribble`). `main._build_games` enables it when the camera is
  enabled and `GEMINI_API_KEY` is set.
- Vision call: `GeminiGuesser(api_key, model)(jpeg, word, language, kids) -> Guesses(remark, guesses)`. The
  prompt gives the word's category (never the word), the language and whether a child drew it, and asks for JSON
  `{"remark", "guesses": [{"guess", "confidence"}]}`, one guess when clear and up to three when unsure. Code sorts
  by confidence and keeps three. Usage is recorded in the ledger as feature `scribble_guess`.
- Judging: code first (`guessed`: any form in either language, once case, accents, articles and lead-ins are set
  aside, a plural, or the form inside a guess of at most three more words: "un gato negro"); the rest go to Jev
  in one call, one `noul` question per guess (synonyms, translations, a more or less precise name for the same
  thing, a film's or saying's main picture count; something merely related or a category does not).
- Phone protocol (`/ws?code=ABCDEF`, JSON): phones send `join` {name, team | player}, `start`, `kids` {on},
  `done`, `skip`, `resume`; the server sends `welcome`, `forget`, `error` and `state` (phase: lobby, drawing,
  showing, guessing, result, over or paused; language, kids, you, teams {key, name, squares, players}, goal,
  round, rounds, turn, drawer {name, team}, drawing (this phone draws), category, word (the drawer's phone while
  drawing, everyone's once revealed), guesses [{text, hit}], deadline (epoch ms), can, message).
- Settings: `SCRIBBLE_VISION_MODEL` (default `gemini-3.8-flash`), `GEMINI_API_KEY`, and Big Brain's
  `BIG_BRAIN_PORT` and `BIG_BRAIN_PUBLIC_URL` for the phones.

## Extension guide

- More words: add entries to `scribble_words.py`; the tests check both languages, unique words and that no form
  belongs to two words of the same list.
- Scoring or length: the constants above. The plan's square types (eyes closed, wrong hand, one line, everyone
  draws, Richie draws, telephone), the no-phone fallback (the word on the TRMNL while Richie turns away) and saving
  the best drawings are not built.

## Constraints, privacy, and failure modes

- One camera frame per turn goes to Google (the drawing, and whatever else is in view); nothing is saved. The
  frame is shown on the TRMNL until the result screen replaces it.
- No camera frame, a vision error or a timeout: Richie says "¡Ay, se me nubló la vista! Este turno no cuenta."
  with the word, the team moves nothing, and the game goes on (logged as a warning). An answer with no guesses
  ("no drawing in view") is a miss.
- Jev unreachable or unreadable: the unmatched guesses are misses (logged). Without `TYPESAFE_API_KEY` there is
  no game host at all.
- The phones have the same limits as Big Brain's (home Wi-Fi or the Funnel address; Android's "Nearby devices"
  permission for the Wi-Fi address).
- The guesser is told the category, which is a hint everyone sees too; a 4-year-old's scribble will often be
  missed, hence the square for trying in kids mode.
- The drawer's phone is the only one with the word; a phone that never joined sees no word.

## Testing

- `uv run pytest tests/games/test_scribble_rush.py` (18, all passing on 2026-11-01): word lists (sizes, both
  languages, no shared forms, no adult categories for kids); code judging in both languages with articles,
  plurals and extra words, and a lion not counting for "El Rey León"; one Jev call for unmatched guesses and a
  Jev failure as a miss; reading the vision answer (confidence order, three at most, nonsense rejected); the
  Gemini call with a fake client (category but not the word in the prompt, the usage recorded); the phone server
  serving this page and the start phrases; a whole two-team game on a pretend Reachy with a fake camera and a
  fake vision call (a first-guess hit, a miss, a vision error, a second-guess hit; only the drawer's phone sees the
  word; the winner announced); kids mode (kids' words, a square for a miss, no camera frame means no squares).
- `tests/tools/test_play_game.py` lists the `scribble` key.
- Full gate on 2026-11-01: `uv run ruff check . --fix && uv run ruff format . && uv run mypy --pretty
  --show-error-codes && uv run pytest tests/ -q` → 2,973 passed, 4 skipped (after checking out the LFS images
  in the worktree; as bare pointers they fail the display tests).
- Not yet done: the phone page in a real browser or on a phone, the real Gemini model on real drawings (hit
  rate and latency), the tick-tock and timings in a real room, and the TRMNL screens on the device.
