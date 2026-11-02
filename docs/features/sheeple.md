# Feature: Sheeple (survey party game)

- Status: `in-progress` (built and tested 2026-11-01; deployed to the Mac mini with the survey bank; not yet played)
- Last updated: 2026-11-02 (three misses a board; a beep before each spoken answer)
- Plan: [party-games.md](../plans/party-games.md) ("Sheeple", "Surveys (Sheeple)")
- Part of: [games.md](games.md) (the shared game host); phones, judging and screens borrowed from
  [big-brain.md](big-brain.md)

## Overview

Sheeple is a survey game for teams that Richie hosts in his own voice. Each board is a question put to 100
people in the US, with its answers hidden and ranked by how many people gave them. You win by thinking like the
flock, not by knowing things. Players join on their phones by scanning the QR code on the TRMNL, as in Big Brain.
The game is bilingual (Spanish or English, and answers count in either language) and plays adult boards by
default, with a kids mode.

## User-facing behavior

1. **Start:** "Richie, juguemos Sheeple" / "let's play Sheeple" (or "shipul", "sheep pull", "el juego de las
   encuestas", "the survey game"), then "sí". Gemini can start it with `play_game` (`sheeple`).
2. **Join:** the TRMNL shows the QR code, the six-letter code and the four teams. Each phone types a name and picks
   a team. Any phone taps "¡Empezar!" (or the room says "¡empezamos!"); a "Modo niños" switch sits next to it.
3. **Six boards**, flock and team rounds in turn (flock first). The last board counts double. Before each
   question Richie says who was asked; in Spanish that's with some cheek ("Le preguntamos a cien gringos: …").
4. **Flock round:** every phone types a guess for 30 s (it ends early once every phone has answered). Richie
   then reveals the answers from the least to the most popular (the TRMNL redraws each one, with a drumroll
   before number 1) and names who said each one. Each guess scores its answer's count for the player and their team.
5. **Team round:** the team whose turn it is sees a big "¡Responder!" button. The first player of that team to
   tap it is the only one Richie listens to: he says "¿Ana?", then a short beep, and only then listens. Her phone
   says "Espera tu nombre y el bip…" until the beep and "¡Dilo en voz alta!" (with a buzz) once it has played. A
   hit reveals the answer, scores its count for the team and keeps the turn. A miss (not on the board, already on
   the board, silence, or nobody tapping within 25 s) passes the turn to the next team. The board ends when it is
   cleared or after three misses. Richie then reads out the answers still hidden.
6. **End:** the winning team (or a tie) on the TRMNL and the phones, "la oveja mayor" (the player whose flock
   guesses scored most), fanfare and applause when Big Brain's sounds are there, and the phones' page stays up 30 s.
7. Pause, quit and "¿lo terminamos?" work as in every game. A phone's "¡Seguimos!" plays on after a pause.

## Architecture and data flow

- `games/sheeple.py`: `Sheeple(Game)` holds the game state, `snapshot(player)` (what each phone sees) and
  `on_message` (what phones send: `start`, `kids`, `answer`, `speak`, `resume`). It also has `play()` (lobby →
  six boards → ending), `find()` (judging) and `pick_surveys()` (which boards play).
- `games/sheeple_screens.py`: `render_survey`, the TRMNL board (round, question, a slot per answer in two
  columns, whose turn it is and the misses, and the scores). Hidden answers are dark-grey bars with their number;
  revealed ones show the answer and its count on a black tab; the team on turn has a black score tile tagged
  "Turno", and the misses are three boxes crossed out one by one. Joining and the winner reuse Big Brain's
  `render_join` (with `title="SHEEPLE"`) and `render_winner`.
- `static/sheeple/index.html`: the phones' page (one WebSocket to the game). It shows the join form, the
  lobby, the question with the answer slots, the guess form, the "Responder" button, who is answering, the
  misses, and the final scores with the flock ranking.
- Reused from Big Brain, unchanged: `Phones` (the server, join codes, public URL; it serves Sheeple's page
  through `page=`), `Judge`/`matches`/`plain`, `load_bank`/`read_history`/`remember`, `Sounds`, `team_name`,
  `START_WORDS`. The only change to a Big Brain file is a backward-compatible `title=` keyword on `render_join`.
- Speech: Richie's normal voice through `GameContext.say` (announcements), with no recorded clips. To listen
  to one player, Richie says "¿Ana?" and then plays a beep (`big_brain_audio.beep`: 880 Hz, 0.18 s) through
  `services.say_audio` with a 6 s start window, so listening opens as the beep ends. The snapshot's `listening`
  is true from then until the answer, which is what turns the speaker's phone to "talk now". Without
  `say_audio`, `services.ask` is used.

### Judging

`Sheeple.find(board, said, spoken=...)` returns the answer a guess names. Code goes first: `matches()` against
each answer's accepted forms (the bank's `accept` plus its English and Spanish text), and the most popular
match wins. When code finds nothing, Big Brain's `Judge` asks Jev once per answer (at most six at a time),
using clue id `<board id>#<rank>` and the question as the clue. Typed flock guesses use `spoken=False`, and
spoken team guesses also get the "sounds like" check. Rulings are kept in `GAME_DATA_DIR/sheeple/rulings.json`,
so the same guess gets the same ruling next time. Repeated flock guesses (same plain text) are judged once.
"Too broad" counts as a miss. Without Jev, only code matches count.

## Data contract

`GAME_DATA_DIR/sheeple/bank.jsonl`, one JSON object a line, made by `scripts/sheeple_bank.py` (not by the game):
Jev tagged all 4,721 counted questions in Eduardo's sheet (US-only, adult, kids, appeal; $0.11), the best 636
that work outside the US were rewritten in both languages by Claude agents with every count kept (600 boards, 36
two-answer quick boards; 548 suit kids, 88 are adult, 11 work only in English).

```json
{"id": "s-0001", "kind": "board", "question": {"en": "...", "es": "..."},
 "answers": [{"en": "...", "es": "...", "count": 51, "accept": ["..."]}],
 "us_only": false, "adult": false, "kids": true}
```

- Only `kind: "board"` entries play. `quick` (two-answer) boards are kept for a later bonus round.
- A board needs its question and every answer in the game's language, and at least three answers. Answers
  are sorted by count in the game, whatever their order in the file.
- Kids mode plays only boards with `kids: true` and neither `adult` nor `us_only`. Adults get every board.
- Boards played in the last 30 days (`sheeple/history.json`) come last, so a short bank still fills a game.

## Configuration and enabling

There are no new settings. The game is registered in `main.py` `_build_games` once
`GAME_DATA_DIR/sheeple/bank.jsonl` exists (games need the hybrid runtime, quick commands and `TYPESAFE_API_KEY`).
Only one game plays at a time, so Sheeple shares Big Brain's phone port and public address
(`BIG_BRAIN_PORT`, `BIG_BRAIN_PUBLIC_URL`). The sound effects come from `GAME_DATA_DIR/big-brain/sounds`
(`board`, `correct`, `wrong`, `drumroll`, `fanfare`, `applause`), and each one is skipped when it is missing.

## Constraints and gotchas

- In flock rounds, a team's score is the sum of its players' guesses, so a bigger team has an edge there. The
  team rounds are even, and the flock ranking at the end is per player.
- In team rounds the first player of that team to tap answers; there is no fixed captain.
- Each answer revealed is a separate spoken line, so a seven-answer reveal takes about 20–30 s.
- The e-ink redraws on each reveal, hit and miss. That is about one redraw every few seconds, which the TRMNL handles.

## Safe extension points

- A quick bonus round from the `quick` boards, the "¿Qué contestó Richie?" bonus and home surveys (see the
  plan) can be added as new board kinds in `_play_board`.
- The lines live in `_LINES` and `_ASKED`. A line without placeholders must also be listed in `_FIXED`, so the speech
  cache gets it.

## Tests

`tests/games/test_sheeple.py` (18 tests):

- boards: one language, answers sorted, both languages accepted, quick boards and short boards left out;
  kids mode filters; boards played lately last.
- judging: code places guesses in either language without Jev; Jev is asked once per answer for the rest
  (typed: no "sounds like"), the most popular hit wins, rulings are kept; without Jev an unplaced guess misses.
- the TRMNL board renders at 800×480 with hidden, revealed and long answers.
- starting by name and mishearings in both languages.
- a whole six-board game with two pretend phones on the real game host: typed flock guesses (English
  answer in a Spanish game), team turns with a miss passing the turn, the last board doubled, the winner,
  the flock ranking and the history file.
- a game where nobody hits: each team board ends at its third miss; a beep plays before every spoken answer and
  the speaker's phone says "talk" only while Reachy listens.
- every fixed line goes to the speech cache.

Also covered: `tests/tools/test_play_game.py` (the game list) and Big Brain's tests (unchanged
`render_join`).

Run: `uv run pytest tests/games/test_sheeple.py -q` (16 passed). Full gate on 2026-11-01: `uv run ruff check . --fix && uv run ruff
format . && uv run mypy --pretty --show-error-codes && uv run pytest tests/ -q`: 2,971 passed, 4 skipped.

Checked by hand on 2026-11-01: the TRMNL board rendered to a PNG and looked at, and the phones' page served by
`Phones` and opened at phone size (team round, another team's turn).

Not yet checked: a real game on Reachy (speech recognition of spoken guesses, Jev's rulings on real survey
answers, timing of the reveals, the "Responder" button on real phones), and the real bank.

## Changelog

- 2026-11-02: After the household played it: a team board ends after three misses, not six (it dragged on), and a
  beep before each spoken answer, with the speaker's phone waiting for it (players spoke over "¿Ana?", because
  the phone said "¡Dilo en voz alta!" as soon as they tapped).
- 2026-11-02: Redesigned board: the shared game header with a sheep and a wool edge, answer bars, Big Brain's
  team tiles and a misses box (see [games.md](games.md#what-the-screens-look-like)).
