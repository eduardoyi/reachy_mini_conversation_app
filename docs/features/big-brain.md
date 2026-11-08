# Feature: Big Brain (team quiz show)

- Status: `in-progress` (built and tested 2026-11-01; deployed to the Mac mini 2026-11-01 13:45; first played
  2026-11-01 15:01, two teams; fixes from that game below)
- Last updated: 2026-11-08 (power cards and the bomb square)
- Plan: [party-games.md](../plans/party-games.md) ("Big Brain build")
- Part of: [games.md](games.md) (the shared game host)

## Overview

Big Brain is a quiz show for teams that Richie hosts in a recorded game-show announcer voice. Players join on
their phones by scanning a QR code on the TRMNL; phones are buzzers and private inputs, the TRMNL is the shared
board, and Richie reads the clues, listens to whoever buzzed first, judges, keeps score and banters. It is our
own format (statement clues answered plainly, points 200 to 800, four hidden squares, once-a-game power cards, a
final round with secret bets), bilingual (Spanish or English, answers count in either), adults by default with a kids mode.

## User-facing behavior

1. **Start:** "Richie, juguemos Big Brain" (or "trivia", "el concurso", "let's play Big Brain"), then "sí". Gemini
   can also start it with `play_game` (`bigbrain`).
2. **Join:** the TRMNL shows a QR code, a six-letter code and the four teams (red, blue, green, yellow) with
   their players. On the grey screen each team is also a shape: red a black circle, blue a striped square, green a
   dark-grey triangle, yellow a light-grey diamond. Each phone scans it, types a name, picks a team. Intro music, then the welcome and how to join.
   Any player taps "¡Empezar!" (or the room says "¡empezamos!"); a "Modo niños" switch sits next to it. After
   "let's play", Richie explains the power cards (below).
3. **Board (20 squares):** five columns (themed topics such as "Mitología griega", one broad category), four rows
   of 200/400/600/800 points from the easiest to the hardest quarter of the bank. Richie names the team in
   control ("Red team, it's your pick!"); its phones buzz and show a glowing "Your team's pick!" over the board,
   while the other phones show whose pick it is over a dimmed board. After 40 s Richie picks one himself.
4. **A clue:** the TRMNL shows it large while Richie reads it in the host voice; then the buzzers unlock for 8 s.
   The first buzz wins (its sound and the team's name), and only that player answers aloud. After Richie's
   line a short beep plays and only then does he listen, for up to 6 s for the answer to start; that player's
   phone says "Espera el bip… y responde" until the beep and "¡Responde en voz alta!" (with a buzz) from it (silence is a miss at once; speech he could not make out gets one
   "say that again?"). Right: the points, the next pick, a cheer (a streak and the 800 row get their own lines). Wrong: the
   points are lost (not in kids mode, nor on a double), that team is locked out and the others get 6 s more. A
   team's third wrong answer in a row and a wrong answer that drops it below zero get their own lines. After a
   square Richie calls a new leader (a comeback when it had trailed by 1,500 or more) or a runaway (3,000 ahead,
   once per leader).
   Nobody: "time's up", and the answer on the TRMNL and in his voice.
5. **Hidden squares** (never in the 200 row, in different columns): double or nothing (twice the points; a miss
   costs nothing), steal (a right answer also takes the square's points from the leading other team), everyone
   (each phone types an answer for 30 s; every right one scores for its team), and the **bomb** (a drumroll and
   "¡Es una BOMBA!"; only the team that picked it may buzz, the others' buzzers are locked; right doubles the
   points, "¡Bomba desactivada!"; a wrong answer or nobody buzzing loses the square's value, "¡BUM!"; a Shield
   cancels the loss; kids mode loses nothing and Richie's warning leaves the loss out). Richie says what each
   means and the phones spell it out under the badge.
6. **Power cards** (2026-11-08): at the start each team is dealt 2 different cards of 4 (kids mode: of the 3
   without Shield, since kids lose no points). They sit under the stage on every phone of the team; any phone
   plays one, and it is gone for the whole team. Richie announces each in the host voice: the team's buzz line
   ("¡Equipo rojo!") and the card's line, naming the target where there is one.
   - **Congelar / Freeze**, on another team: it cannot buzz on the next clue (its buzzer turns icy, "¡Congelados!").
     Playable while a square is being picked or while a clue is read, before the buzzers unlock; it then holds for
     that clue. A frozen team skips an everyone square and stays frozen for the next.
   - **Escudo / Shield**, own team: a wrong answer (or a bomb going off) costs nothing on the current clue, or on
     the next one that can cost points when played between squares. Not while its team is answering or locked out.
   - **Pista / Hint**, own team, during a clue once it is shown: that team's phones alone show the answer's first
     letter, how many letters and words, and a pattern ("P______ _____", worked out from the bank's answer). Richie
     teases that the team asked for a hint, never what it says, at the clue's next pause (before the buzzers
     unlock, or before "anyone else?"); a tease still untold when the square ends is dropped.
   - **Secuestro / Hijack**, while another team has the pick: the pick is theirs, with a fresh 40 s and Richie
     asking them to pick.
   The game ignores a card played at the wrong moment, on its own team or a missing one, or already spent.
   Every phone sees who is frozen or shielded.
7. **Fairness:** after a wrong ruling, every other phone gets "Was Ana's “Pacific” right?" with "That counts!"
   until the next square is picked; a majority makes it count (points back and awarded, control to that team, and
   the ruling is kept for good). While the square is still open it ends there, with the correct sound and Richie's
   "the room has spoken".
8. **Final round:** the category on the TRMNL; each team bets in secret on a phone (up to its score, or 800 when
   it has less) for up to 30 s, moving on as soon as every team has bet; Richie reads the final clue; 30 s of thinking music while each team types one answer;
   a drumroll and the reveal team by team from the lowest score, each answer on the TRMNL and read aloud.
   Each phone shows the answer once the TRMNL does (the final's stays up 8 s, since e-ink takes a few seconds to
   draw, and on the phones' end screen).
9. **The end:** the winner (or a tie) with a drumroll, fanfare and applause, Richie's happy moves, the winner
   screen, goodbye and the outro. The phones keep the final scores for 30 s.
10. **Pause and quit** as every game: "pausa", his name or an antenna pauses, "se acabó el juego" ends it, any other
   request asks "¿lo terminamos?". The phones say why it may have paused and have a "Keep playing!" button, since a
   false wake in table talk pauses it too (twice in the 2026-11-01 game).

## Editions

A themed edition is the same show with its own clue bank, title, opening and closing lines and intro tune
(`Edition` in `games/big_brain.py`, its `GameInfo` in `games/catalog.py`). It is its own game in the catalog, so
"Richie, let's play Big Brain, Hogwarts", "big brain Harry Potter", "juguemos Big Brain, edición Harry Potter" or
"la trivia de Harry Potter" start it ("Big Brain, Lord of the Rings", "el Señor de los Anillos", "la Tierra Media"
for Middle-earth; "Big Brain, Star Wars", "la guerra de las galaxias" for Galactic; "Big Brain, Disney", "la trivia de Pixar" for Disney), while plain "Big Brain" or
"trivia" is still the general quiz. It is enabled
once `trivia-bank/editions/<folder>/bank.jsonl` exists.

| Edition | Game key | Bank | Lines and tune |
|---------|----------|------|----------------|
| Hogwarts (the eight Harry Potter films: story, characters, spells, creatures, the cast, the making) | `bigbrain_hp` | `editions/harry-potter/` | `welcome_hp`, `goodbye_hp`, `intro_hp` |
| Middle-earth (Peter Jackson's three Lord of the Rings films: story, characters, places, battles, the cast, the making) | `bigbrain_lotr` | `editions/lord-of-the-rings/` | `welcome_lotr`, `goodbye_lotr`, `intro_lotr` |
| Galactic (the six Star Wars saga films, Episodes I to VI: no sequels, spin-offs or series) | `bigbrain_sw` | `editions/star-wars/` | `welcome_sw`, `goodbye_sw`, `intro_sw` |
| Disney (Walt Disney Animation Studios and Pixar animated features, 1937 to today: no live-action remakes, series or parks) | `bigbrain_disney` | `editions/disney/` | `welcome_dis`, `goodbye_dis`, `intro_dis` |

An edition's clues are written new by `scripts/edition_bank.py` (the archive had 151 Harry Potter clues, mostly
the books): Gemini with Google Search writes each column in batches; Gemini checks the facts (true, the only
answer, in the films); another provider's model answers blind and Jev judges the match and any giveaway; for
real-world columns (cast, making, release) Gemini also answers each clue with Google Search, since asked only to
verify it answers from memory; Jev rates appeal, hardness and kids suitability for the edition's audience. The
bank uses each clue's column as its category, so the board names columns by their titles.

Eduardo prefers this batch work on his Claude subscription rather than the Gemini API, whose prepaid credits
Richie's voice shares (on 2026-11-01 the Hogwarts run used them up mid-run and the camera games fell back to the
Mac's voice). So the Hogwarts edition's last 390 fact checks were done by Claude Code agents with web search.
`--claude` now keeps every model call but Jev's on Claude: `--claude --export DIR` writes the clues still to
check as batches with two briefs (`brief-facts.md`: fact checks with web search; `brief-blind.md`: blind answers
from the clue alone, no search, as a player would), parallel Claude agents write `verdicts-N.json` and
`answers-N.json`, and `--claude --import DIR` adds them and runs Jev, which judges the blind answers as it judges
players in the game and rates each clue. Columns without clues get writing tasks too (`brief-write.md`, two
columns an agent), so an edition can be written, checked and rated with Claude and Jev alone, as Middle-earth was
on 2026-11-01 (563 written, 457 kept). Jev then picks each clue's best column, and a clue clearly in the wrong
one moves (at least 90% sure, under 2% for the writer's column; looser cut-offs moved clues between columns that
overlap, such as the Fellowship and the cast). Up to 4 clues may share an answer, since a franchise's main
characters answer many; the board keeps each answer to one square a game, and a column missing a difficulty row
borrows the nearest clue from the next row. The script's Gemini and OpenAI paths stay for reference.

## Current architecture

| Piece | File | What it does |
|-------|------|--------------|
| Game | `games/big_brain.py` | `BigBrain(Game)`: the lobby, the deal, the 20 squares, hidden squares, the cards (`_playable`, `_play_card`, announced by `_call_cards` at safe moments: while a square is picked, before the buzzers unlock, before "anyone else?" and after the square), the vote, the final, the ending; `snapshot(player)` is each phone's view, `on_message` takes phones' messages; `answer_hint` |
| Board | `games/big_brain_board.py` | `build_board(bank, language, kids, recent)`: rows by hardness quarter, themed columns (one per broad category) plus a broad one, four hidden squares (`HIDDEN`: double, steal, everyone, bomb), a final from the hardest quarter with appeal ≥ 2.3; no two clues sharing an accepted answer; `imperial()` leaves out clues measured in miles, feet or Fahrenheit; `remember`/`read_history` keep clues out for 30 days |
| Judge | `games/big_brain_judge.py` | `matches()` by code (case, accents, articles, lead-ins, slips ≥ 0.84 similar, a surname, a name without its generic word such as "Pacific" for "Pacific Ocean", the answer with a few words around it; a filler such as "um" is wrong), then Jev (`right`, `too_broad`, and for spoken answers `sounds_like`: speech recognition's spelling of an accepted answer, "Téphanis" for "Tiffany's") → `Ruling.RIGHT/WRONG/SPECIFIC`; rulings kept in `rulings.json` |
| Audio | `games/big_brain_audio.py` | `Clips` (host lines by moment, never the same take twice running), `Sounds` (as `chosen.json` picks), `Speech` (Eleven v4 Turbo, raw 24 kHz PCM, kept by text) |
| Lines | `games/big_brain_lines.py` | 74 moments (with the teams' own), 732 takes in English and Venezuelan Spanish with ElevenLabs audio tags; a moment with no clip yet is said live in the host voice (with `ELEVENLABS_API_KEY`) |
| Phones | `games/big_brain_phones.py`, `static/big_brain/index.html` | FastAPI on `0.0.0.0:BIG_BRAIN_PORT` inside Reachy's loop (no signal capture), only during a game; the code in every request; joins kept here. `?demo=<cards\|freeze\|frozen\|hint\|bomb>&lang=en` shows the cards and the bomb with no game running |
| Screens | `games/big_brain_screens.py` | `render_join`, `render_board`, `render_clue`, `render_answer`, `render_final`, `render_reveal`, `render_winner` |
| QR | `qr_code.py` | Byte mode, level L, versions 1–5; checked against macOS's own reader (all sizes decoded) |
| Recorded speech | `hybrid_runtime.py` | `request_announcement(..., audio=(rate, samples))`: a clip plays through the announcement path (waits until free, no wake from his own voice, `on_spoken`, then an awaited answer); `GameServices.say_audio` in `main.py` wraps it |

Data on the Mac mini, under `GAME_DATA_DIR` (`~/.local/share/reachy/game-data`):

```
trivia-bank/bank.jsonl          the clue bank (20,499 clues; scripts/trivia_bank.py, scripts/trivia_topics.py)
big-brain/voice/<moment>/<lang>-NN.wav   host clips (scripts/big_brain_voice.py), manifest.json
big-brain/sounds/<name>/take-N.wav       sounds and music (scripts/big_brain_sounds.py), chosen.json
big-brain/speech/<hash>.wav     live speech kept by text
big-brain/history.json          clues played, by time
big-brain/rulings.json          Jev's and the room's rulings
```

## Canonical files and contracts

- Enabled in `main._build_games` when `trivia-bank/bank.jsonl` exists (no camera needed). Missing clips or sounds
  are skipped; without `ELEVENLABS_API_KEY` clues are read in Richie's usual voice.
- Phone protocol (`/ws?code=ABCD`, JSON): phones send `join` {name, team | player}, `start`, `kids` {on},
  `pick` {column, row}, `buzz`, `answer` {text}, `bet` {points}, `vote`, `card` {card: freeze | shield | hint |
  hijack, team: the frozen team for Freeze}; the server sends `welcome` {player}, `forget`, `error` {message:
  closed | name | join} and `state` (phase, language, kids, you, teams, board, control, clue {column, points,
  text once read, hidden}, buzzed, deadline (epoch ms), can, max_bet, vote, message, final {category, answers},
  cards (the team's hand), playable (the cards it may play now), frozen, shielded (teams), hint {first, letters,
  words, pattern} only on the hinted team's phones).
- Timings (`games/big_brain.py`): pick 40 s, buzz 8 s (6 s after a miss), 6 s for an answer to start (passed to
  the runtime as `say_audio(..., listen_s=)`, where silence then reaches the handler as ""), everyone 30 s, bets up
  to 30 s, final answers 30 s, the page stays 30 s after the end.
- Settings: `ELEVENLABS_API_KEY`, `GAME_DATA_DIR`, `BIG_BRAIN_PORT` (7880), `BIG_BRAIN_PUBLIC_URL` (the QR code's
  https address through Tailscale Funnel; on the Mac mini `https://eduardos-mac-mini.tailfbc80d.ts.net/bigbrain`,
  set up with `tailscale funnel --bg --set-path /bigbrain http://127.0.0.1:7880`).

## Extension guide

- New host lines: add takes at the end of a moment in `big_brain_lines.py` (clips are named by take number, so
  never edit or reorder one; known tags only, the tests check; a line naming a team goes in `_team_lines`), then
  `uv run python scripts/big_brain_voice.py --out <game-data>/big-brain/voice --check` makes only the new ones.
- New sounds: add a prompt to `scripts/big_brain_sounds.py`; pick a take by editing `sounds/chosen.json`. The
  cards use `steal` (Freeze, Hijack), `hidden` (Shield) and `pick` (Hint); the bomb `drumroll`, then `correct` or
  `wrong`. An explosion and an icy whoosh would suit them better; none is made yet.
- A new card: add it to `CARDS`, `CARD_SOUNDS`, `_playable` and `_play_card`, its lines, and its strings on the
  phone page.
- More clues: `scripts/trivia_bank.py --pick hard|appeal --rewrite-top N`, then `scripts/trivia_topics.py`.

## Constraints, privacy, and failure modes

- The phone page is plain http on the home Wi-Fi, so no phone microphone (browsers allow it only on https).
  Guests on a Wi-Fi with client isolation cannot reach the Mac mini.
- Android blocks Chrome from home-network addresses until Chrome has the **Nearby devices** permission (Settings →
  Apps → Chrome → Permissions); without it the join link fails with `ERR_TOO_MANY_RETRIES`. Found on a Pixel 9a
  on 2026-11-01; every Android guest needs it once.
- Only the buzzing player is listened to, and only right after the buzz. That answer's few seconds of audio go
  to ElevenLabs Scribe v2 to be transcribed, biased toward the board's answers (see
  [games.md](games.md#hearing-game-answers)); nothing is kept. Typed answers and names stay on the Mac mini for
  the game (rulings keep the answer text, without names).
- ElevenLabs allows five requests at a time on the Creator plan; the board's clues are prepared while players
  join. Its cost is not yet in the admin app's usage ledger (Jev's judging is, under "Games").
- A clip the speech model cannot read (2026-11-01: "Pacific", 1.6 s of audio, no text) reaches the game as
  `UNCLEAR_ANSWER` rather than silence; Richie asks once to say it again ("say_again"), then it is no answer.
- Answers are transcribed with the household's language guess, so a short English answer can come back in
  Spanish spelling; Jev's `sounds_like` covers that rather than biasing the recognizer with the answer, which on
  unclear audio could write the answer itself.
- Jev's API refuses an empty `assets` object; `jev.ask` leaves it out without images (every Big Brain ruling
  failed on 2026-11-01 until this was fixed).
- Lights do not change color during the game (they cannot report their color, so they could not be put back).
- Deadlines on phones use the phone's clock against the Mac mini's.
- A Freeze on the team that picked a bomb leaves nobody to answer it, so it goes off (a Shield still saves it).
- A Hijack needs another team to hold the pick, so a team that keeps winning the pick may never play its own.
- With one team, Freeze and Hijack have no one to play on and stay in the hand.
- The TRMNL shows the bomb's ribbon ("¡BOMBA!") but not the cards; Richie's voice and the phones carry them.

## Testing

- `uv run pytest tests/games/test_big_brain.py` (28 tests, 52 cases): board shape and order (four hidden squares; the bomb never
  in the 200 row nor sharing a column with another hidden square), kids mode, the 30-day memory, the
  judge by code in both languages, Jev's kept rulings and the room's overrule, the QR code's structure, the
  phones' code and joining (FastAPI test client), and a whole show with two pretend phones from joining to the
  winner (red always right wins; the reveal goes from the lowest score; 21 clues remembered); misses in a row,
  below zero, a comeback, a runaway and the fallback to the older line until a new moment is recorded. Cards and
  the bomb (2026-11-08): the deal (2 different of 4 by the game's rng, no Shield in kids mode, the rules line),
  Freeze (refused on the own team, a missing team, an already frozen one, once spent and once the buzzers
  unlocked; the frozen team cannot buzz), Shield (waits past a double, saves one miss, then a miss costs again),
  Hint (refused before the clue shows; only the team's phones get it; `answer_hint`), Hijack (refused on its own
  pick; Richie asks the new team), the bomb (only the picker buzzes; right doubles; wrong and silence cost; a
  Shield saves it; kids lose nothing), a moment with no clip said live in the host voice, and the whole show with
  both pretend phones playing every card they can.
- `tests/test_hybrid_runtime.py::test_a_game_announcement_can_play_recorded_speech_instead_of_saying_its_text`.
- `tests/games/test_big_brain_lines.py` (4): both languages, known tags only, tags stripped for screens.
- Full gate on 2026-11-01: `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest
  tests/ -v` → 2,928 passed, 4 skipped.
- QR codes decoded by macOS Core Image for 2, 33, 72 and 100-byte texts (versions 1, 3, 4, 5).
- The phone page was tested by its author against a stand-in server in the browser at 360 px; not yet against
  the real server or an Android phone. Not yet played on the robot.

## Changelog

- 2026-11-08: Power cards and the bomb, for more chaos: each team is dealt 2 of Freeze, Shield, Hint and Hijack
  (no Shield in kids mode), shown and played on its phones and announced by Richie (a Hint's content stays on the
  team's phones); a fourth hidden square, the bomb, only for the team that picked it (double if right, the
  square's value lost if wrong or silent). 17 new moments (116 takes, about 11,000 characters to record), said
  live in the host voice until their clips are made. The pick now waits out its 40 s after an overrule instead of
  Richie picking at once, and a pick made while Richie still names the team is no longer missed.

- 2026-11-03: More banter: 196 more takes (correct and wrong 20 each, lead changes 10, streaks and hard ones 12,
  …), and new moments for a third wrong answer in a row, a drop below zero and a runaway leader; the unused
  "comeback" now plays when the new leader had trailed by 1,500. A new moment falls back to the older one until its
  clips are made.
- 2026-11-02: After the household played the Disney edition: a beep after Richie's "¿Sí?" opens the listening,
  and the phone says "answer now" only from then (players answered over his line); the "¡Correcto!" card goes
  when the next pick starts and before the final (it stayed on); nobody is asked to pick once the board is empty
  (the last team saw "¡Les toca escoger!" for a moment before the final).
- 2026-11-02: Redesigned TRMNL screens (see [games.md](games.md#what-the-screens-look-like)): a black header
  with the bulb and marquee bulbs; the board's values as black tiles that turn hatched once played, the category
  on a grey band over each clue, hidden squares on a notched ribbon, the answer in a framed card, a tick or cross
  with the points in the reveal, a sunburst and trophy for the winner, and a tile per team (shape, name, points)
  with the team in control in black.
