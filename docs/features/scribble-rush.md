# Feature: Scribble Rush (drawing race)

- Status: `in-progress` (first version built and tested with fakes on 2026-11-01; not deployed or played; the
  vision guesses not yet measured on real drawings)
- Last updated: 2026-11-08 (best guess first, word levels, Venezuelan words set aside)
- Plan: [party-games.md](../plans/party-games.md) ("Scribble Rush", "Vision guesser")
- Part of: [games.md](games.md) (the shared game host); phones as in [big-brain.md](big-brain.md)

## Overview

Teams take turns drawing a secret word on paper in a rush, and Richie is the only one who guesses. Players join
on their phones by QR code, as for Big Brain; the drawer's phone shows the word, the TRMNL is a race track with a
lane per team, and one camera frame of the drawing goes to a Gemini Flash vision model that gives up to three
guesses. Richie says them aloud one by one; the earlier he gets it, the more squares the team moves. Each team holds one or
two secret sabotage cards to throw at another team's turn (other hand, eyes closed, one line, half time). Bilingual
(Spanish or English, a guess counts in either), adults by default with a kids mode and its own word list.

## User-facing behavior

1. **Start:** "Richie, juguemos Scribble Rush", "let's play Scribble Rush", "el juego de dibujar", "dibujo
   relámpago", "juguemos a dibujar", "drawing game" (and mishearings such as "escribel rush", "scribble brush"),
   then "sí". Gemini can start it with `play_game` (`scribble`).
2. **Join:** the TRMNL shows "SCRIBBLE RUSH", a QR code, the six-letter code and the three teams (red, blue,
   green) with their players. Each phone types a name and picks a team; "¡Empezar!" (or "¡empezamos!" in the room)
   starts, and a "Modo niños" switch sits next to it.
3. **Sabotage cards:** on "¡Empezar!" each team (with two teams or more) is dealt one or two different cards at
   random from four: **Mano contraria / Other hand**, **Ojos cerrados / Eyes closed**, **Una sola línea / One line**
   (don't lift the pen) and **Medio tiempo / Half time**. Richie explains them after "¡Empezamos!" while the phones
   show "¡Cartas de sabotaje!" and the team's cards. From then on, any phone of a team can tap a card, then a rival
   team ("¿A qué equipo se la lanzas?"); the card is spent for the whole team and hits that team's next turn. A
   throw is secret: only the thrower's phones list it ("Ojos cerrados → Azul, en su próximo turno") until the
   target's turn starts. A card thrown at a team while it is drawing waits for its following turn. Several cards
   can land on one turn; Half time halves the drawing time once however many are thrown (never below 8 s). The
   game never curses anyone by itself.
4. **A turn:** teams go in turn from a random first team, each team's players drawing in the order they joined.
   The TRMNL shows "ROJO DIBUJA" and, under it, a black "CATEGORÍA: ANIMAL" label beside the drawer and the round,
   over the track. Richie calls the drawer
   ("¡Ana, del equipo Rojo, te toca dibujar! Mira tu teléfono… ¡ya!"); only the drawer's phone shows the word.
   An adult team draws easy words until square 8, medium ones until 16 and hard ones after, so nobody gets a
   saying on the first turn and the leader gets the tough ones ("Otra palabra" draws one of the same level). The
   drawer's phone has an "Otra palabra" button (once a turn; the clock keeps running) and "¡Listo! Enseñárselo a Richie". The
   drawer's card has the category as a big black label above the word ("Categoría / animal"), and the other
   phones show who is drawing, the same label and the countdown. A tick-tock speeds up for 20 s (30 s in kids
   mode). A cursed turn starts with Richie revealing each card before the call ("¡Ojo! El equipo Azul le lanza a
   Rojo… ¡ojos cerrados! Nada de espiar."); every phone shows the curses as red stamps ("¡Sabotaje! Ojos cerrados ·
   de Azul"), above the word on the drawer's, and the TRMNL caption reads "Ana · ¡Ojos cerrados + Medio tiempo!".
5. **Showing:** on "Listo" or when time runs out, Richie says "¡Muéstramelo!", waits 2.5 s for the drawing to be
   held up, takes one camera frame and shows it on the TRMNL ("¿Qué será?") while he says "Mmm… déjame ver…".
6. **Guessing:** the model's short remark on the drawing, then its best guess: "¿Es un gato?". Only when it is
   unsure (best guess under `SURE` 0.7 confidence) do up to two backups follow a miss, most likely first: "¿No?
   Mmm… ¿un zorro?", "¡Último intento! ¿Un perro?". The phones list each guess as it is said and mark the
   hit. A hit on the first, second or third guess moves the team 6, 4 or 2 squares ("¡Sí! ¡Era gato! ¡El equipo
   Rojo avanza 4 casillas!", a happy move); a miss says the word ("¡Me rindo! Era gato.", a sad move) and moves 1
   square in kids mode ("¡Qué buen intento!"). The TRMNL then shows "ERA: GATO" and the track. A hit while cursed
   moves one more square per curse ("¡Y con sabotaje y todo! Les doy una casilla más de premio."); the vision
   model is told how the drawing was sabotaged, so it reads it more generously.
7. **The end:** the first team to reach square 24 wins at once; otherwise, after six rounds, the furthest team
   (or a tie). The winner screen, an excited move, "¡Ganó el equipo Rojo!" and thanks. The phones keep the final
   track for 30 s.
8. **Pause and quit** as every game; the phones show the pause with a "Seguir jugando" button.

## Current architecture

```
"juguemos Scribble Rush" -> GameHost -> ScribbleRush.play
  Phones (big_brain_phones.py, page=static/scribble/index.html) <-> on_message / snapshot
  start: deal() -> each team's cards; phones send "sabotage" -> _thrown[target] (secret)
  turn: _thrown.pop(team) -> curses announced; word -> drawer's phone; render_track -> TRMNL; tick-tock -> play_audio
  services.camera() -> one JPEG -> GeminiGuesser (one generate_content call) -> Guesses
  judge_guesses: code (guessed) then one Jev call for the rest -> say each guess -> squares -> render_track
```

The phone server is Big Brain's (`Phones`, with `page=` for this game's own page) on the same port and public
address; only one game runs at a time. The join and winner screens are Big Brain's (`render_join` takes a
`title`, default "BIG BRAIN"); the track is `scribble_screens.render_track` (a grey lane per team, covered squares in black, the team's shape as its
token, a checkered finish, the lane on turn outlined); the drawing is shown with
`eink_display.render_photo`.

## Canonical files and contracts

- `games/scribble_rush.py`: the game, `ScribbleSetup(port, guess, public_url)`, `GeminiGuesser`, `read_guesses`,
  `guessed`, `judge_guesses` and `deal(teams, rng)` (one or two distinct cards a team). Timings and scoring are
  module constants: `DRAW_S` 20, `KIDS_DRAW_S` 30, `MIN_DRAW_S` 8 (Half time's floor), `HOLD_S` 2.5,
  `VISION_TIMEOUT_S` 15, `GOAL` 24, `ROUNDS` 6, `SQUARES` (6, 4, 2), `KIDS_MISS` 1, `JEV_HIT_P` 0.6, `SURE` 0.7
  (a best guess this confident comes without backups), `LEVEL_SQUARES` (8, 16) and `word_level(squares)`; `CARDS`
  ("hand", "eyes", "line", "half"). Richie's lines are plain text in `_LINES` (no audio tags; said in his own
  voice, the fixed ones cached): the sabotage moments are `cards_rules`, `sabotage_<card>` and `bonus`.
- `games/scribble_words.py`: `KIDS_WORDS` (139: animals, food, things, vehicles, nature, body and clothes) and
  `ADULT_WORDS` (470: actions, movies, sayings, harder things, places, characters and jobs, sports and games,
  absurd scenes, everyday disasters, mythical creatures and monsters, wacky inventions, internet and pop culture),
  each `level` "easy" (143), "medium" (185) or "hard" (142): how hard it is to draw in 20 s and for the vision
  model to recognise, rated by a Claude agent on 2026-11-08. `_ADULTS` is category → level → entries.
  `VENEZUELAN_WORDS` (42: Venezuelan life, sayings like "Cachicamo diciéndole a morrocoy conchúo", tequeño,
  hallaca, cachapa, cuatro, tostiarepa, Salto Ángel, tepuy) are set aside for a Venezuelan edition and not dealt;
  they have no level yet. Words with a Venezuelan name for an everyday thing show the widely used one (inodoro,
  not poceta) and still accept the Venezuelan one. A scene's forms always name both
  things ("pulpo en una patineta"), so one noun alone is not a hit by code; Jev may still accept a near guess. Each entry is `"Spanish forms / English forms"`, forms separated by `;`, the first
  of each side the word the drawer sees; every form counts as guessed.
- `games/scribble_screens.py`: `render_track(title, caption, lanes, goal, turn=, tag=)` (`tag`: the category label).
- `static/scribble/index.html`: the phone page. `?demo=<join|lobby|closed|cards|pick|draw|cursed|watch|cursedwatch|
  show|guess|hit|miss|paused|over|all>&lang=en` shows sample states with no game running.
- `games/catalog.py`: `SCRIBBLE_RUSH` (key `scribble`). `main._build_games` enables it when the camera is
  enabled and `GEMINI_API_KEY` is set.
- Vision call: `GeminiGuesser(api_key, model)(jpeg, word, language, kids, curses) -> Guesses(remark, guesses)`.
  The prompt gives the word's category (never the word), the language, whether a child drew it and, for a
  sabotaged turn, how ("drawn with eyes closed and with the wrong hand… read it generously"), and asks for JSON
  `{"remark", "guesses": [{"guess", "confidence"}]}`, one guess when clear and up to three when unsure. Code sorts
  by confidence and keeps three. Usage is recorded in the ledger as feature `scribble_guess`.
- Judging: code first (`guessed`: any form in either language, once case, accents, articles and lead-ins are set
  aside, a plural, or the form inside a guess of at most three more words: "un gato negro"); the rest go to Jev
  in one call, one `noul` question per guess (synonyms, translations, a more or less precise name for the same
  thing, a film's or saying's main picture count; something merely related or a category does not).
- Phone protocol (`/ws?code=ABCDEF`, JSON): phones send `join` {name, team | player}, `start`, `kids` {on},
  `done`, `skip`, `sabotage` {card, target}, `resume`; the server sends `welcome`, `forget`, `error` and `state`
  (phase: lobby, cards, drawing, showing, guessing, result, over or paused; language, kids, you, teams {key, name,
  squares, players}, goal, round, rounds, turn, drawer {name, team}, drawing (this phone draws), category, word
  (the drawer's phone while drawing, everyone's once revealed), guesses [{text, hit}], seconds (this turn's
  drawing time), cards (this team's cards left), thrown [{card, target}] (this team's throws still waiting),
  curses [{card, by}] (the turn being played), deadline (epoch ms), can (`sabotage` while the team holds a card),
  message). A `sabotage` with a card the team doesn't hold (or spent), its own team, a team not playing or no
  team, or after the game, is ignored (logged).
- Settings: `SCRIBBLE_VISION_MODEL` (default `gemini-3.8-flash`), `GEMINI_API_KEY`, and Big Brain's
  `BIG_BRAIN_PORT` and `BIG_BRAIN_PUBLIC_URL` for the phones.

## Extension guide

- More words: add entries to `scribble_words.py` under their category and level; the tests check both
  languages, unique words, that no form belongs to two words of the same list and at least 100 words a level.
- A Venezuelan edition: deal `VENEZUELAN_WORDS` (rated by level first), the way Big Brain's editions are their
  own catalog games.
- Scoring or length: the constants above.
- A new sabotage card: add its key to `CARDS`, a `sabotage_<key>` moment, its `_CARD_NAMES` and `_CURSED` (the
  vision hint) entries, and its name, hint and icon on the phone page (`T.*.cards`, `T.*.how`, `ICONS`).
- The plan's other square types (everyone draws, Richie draws, telephone), the no-phone fallback (the word on the TRMNL while Richie turns away) and saving
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
- Sabotage throws are kept only in memory and shown only to the thrower's phones until the turn; the curses
  themselves are honour-system (nobody checks the drawer's eyes are closed). With one team there are no cards.
  The drawer sees the word while Richie announces the curses (as before, during the call), and the clock starts
  after the call.

## Testing

- `uv run pytest tests/games/test_scribble_rush.py` (24, all passing on 2026-11-08): the category label on the
  track; word lists (sizes, both
  languages, no shared forms, no adult categories for kids); code judging in both languages with articles,
  plurals and extra words, and a lion not counting for "El Rey León"; one Jev call for unmatched guesses and a
  Jev failure as a miss; reading the vision answer (most confident first, three at most, no backups when sure, nonsense rejected);
  every adult word has a level with at least 100 a level; the Venezuelan words not dealt; a team's square picking
  the level, and a level that runs out falling back; the
  Gemini call with a fake client (category but not the word in the prompt, the usage recorded); the phone server
  serving this page and the start phrases; a whole two-team game on a pretend Reachy with a fake camera and a
  fake vision call (a first-guess hit, a miss, a vision error, a second-guess hit; only the drawer's phone sees the
  word; the winner announced; blue throws a card at red, which red's phones never see before its turn, is
  announced right before red's call, reaches the guesser and adds a square on a hit); kids mode (kids' words, a
  square for a miss, no camera frame means no squares); the deal (one or two distinct cards, both counts seen);
  sabotage plays (refused at the own team, an unknown or missing team, a card not held or already spent; secret
  until the turn, then on every phone; four stacked curses announced before the call and told to the guesser;
  two Half times halve the time once, floored at `MIN_DRAW_S`; +1 square per curse on a hit; a throw during the
  target's turn waits for its next one); the vision prompt naming each curse once and never the word.
- `tests/tools/test_play_game.py` lists the `scribble` key.
- Full gate on 2026-11-01: `uv run ruff check . --fix && uv run ruff format . && uv run mypy --pretty
  --show-error-codes && uv run pytest tests/ -q` → 2,973 passed, 4 skipped (after checking out the LFS images
  in the worktree; as bare pointers they fail the display tests).
- 2026-11-08: `uv run pytest -q tests/games tests/tools/test_play_game.py` → 339 passed; `uv run ruff check` and
  `uv run ruff format` on the touched files clean; `uv run mypy --pretty --show-error-codes` → no issues in 181
  source files. The phone page's `?demo=cards|pick|cursed|cursedwatch` (and `&lang=en`) were looked at in a
  desktop browser at phone width.
- 2026-11-08 (best guess first, levels): full suite → 3,277 passed, 4 skipped; ruff clean; mypy src clean.
- Not yet done: the phone page on a real phone or in a real game, the real Gemini model on real drawings (hit
  rate and latency), the tick-tock and timings in a real room, and the TRMNL screens on the device.

## Changelog

- 2026-11-08: Richie says his best guess first, with backups only when unsure (he had been saving his best guess
  for last: in 30 turns from three game nights his most confident guess was right 24 times, but usually said
  third, for 2 squares instead of 6). Adult words are rated easy, medium or hard and a team draws harder ones as
  it climbs. The Venezuelan-only words (42) are set aside for a Venezuelan edition, since the household often
  plays with non-Venezuelans; Venezuelan names for everyday things show the widely used one.
- 2026-11-08: The adult list grew from 238 to 512 words with six new categories (absurd scenes, everyday
  disasters, mythical creatures, wacky inventions, internet and pop culture, Venezuelan life); the kids' list is
  unchanged.

- 2026-11-08: Sabotage cards: each team is dealt one or two (other hand, eyes closed, one line, half time) and
  throws them secretly at rivals' next turns; Richie reveals them, the guesser is told, and a hit while cursed
  pays a square more per card.

- 2026-11-02: The category is a big black label on the TRMNL and both phone views (players didn't notice it as a
  word in the caption and a small tag; only Richie seemed to know it).
- 2026-11-02: Redesigned track: the shared game header with a pencil and a checkered edge, team-shaped tokens and
  black progress (see [games.md](games.md#what-the-screens-look-like)).
