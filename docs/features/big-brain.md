# Feature: Big Brain (team quiz show)

- Status: `in-progress` (built and tested 2026-11-01; deployed to the Mac mini 2026-11-01 13:45; first played
  2026-11-01 15:01, two teams; fixes from that game below)
- Last updated: 2026-11-01
- Plan: [party-games.md](../plans/party-games.md) ("Big Brain build")
- Part of: [games.md](games.md) (the shared game host)

## Overview

Big Brain is a quiz show for teams that Richie hosts in a recorded game-show announcer voice. Players join on
their phones by scanning a QR code on the TRMNL; phones are buzzers and private inputs, the TRMNL is the shared
board, and Richie reads the clues, listens to whoever buzzed first, judges, keeps score and banters. It is our
own format (statement clues answered plainly, points 200 to 800, three hidden squares, a final round with secret
bets), bilingual (Spanish or English, answers count in either), adults by default with a kids mode.

## User-facing behavior

1. **Start:** "Richie, juguemos Big Brain" (or "trivia", "el concurso", "let's play Big Brain"), then "sí". Gemini
   can also start it with `play_game` (`bigbrain`).
2. **Join:** the TRMNL shows a QR code, a six-letter code and the four teams (red, blue, green, yellow) with
   their players. Each phone scans it, types a name, picks a team. Intro music, then the welcome and how to join.
   Any player taps "¡Empezar!" (or the room says "¡empezamos!"); a "Modo niños" switch sits next to it.
3. **Board (20 squares):** five columns (themed topics such as "Mitología griega", one broad category), four rows
   of 200/400/600/800 points from the easiest to the hardest quarter of the bank. Richie names the team in
   control ("Red team, it's your pick!"); its phones buzz and show a glowing "Your team's pick!" over the board,
   while the other phones show whose pick it is over a dimmed board. After 40 s Richie picks one himself.
4. **A clue:** the TRMNL shows it large while Richie reads it in the host voice; then the buzzers unlock for 8 s.
   The first buzz wins (its sound and the team's name), and only that player answers aloud; Richie listens only
   then, for up to 6 s for the answer to start (silence is a miss at once; speech he could not make out gets one
   "say that again?"). Right: the points, the next pick, a cheer (a streak and the 800 row get their own lines). Wrong: the
   points are lost (not in kids mode, nor on a double), that team is locked out and the others get 6 s more.
   Nobody: "time's up", and the answer on the TRMNL and in his voice.
5. **Hidden squares** (never in the 200 row, in different columns): double or nothing (twice the points; a miss
   costs nothing), steal (a right answer also takes the square's points from the leading other team), everyone
   (each phone types an answer for 30 s; every right one scores for its team). Richie says what each means and
   the phones spell it out under the badge.
6. **Fairness:** after a wrong ruling, every other phone gets "Was Ana's “Pacific” right?" with "That counts!"
   until the next square is picked; a majority makes it count (points back and awarded, control to that team, and
   the ruling is kept for good). While the square is still open it ends there, with the correct sound and Richie's
   "the room has spoken".
7. **Final round:** the category on the TRMNL; each team bets in secret on a phone (up to its score, or 800 when
   it has less) for up to 30 s, moving on as soon as every team has bet; Richie reads the final clue; 30 s of thinking music while each team types one answer;
   a drumroll and the reveal team by team from the lowest score, each answer on the TRMNL and read aloud.
   Each phone shows the answer once the TRMNL does (the final's stays up 8 s, since e-ink takes a few seconds to
   draw, and on the phones' end screen).
8. **The end:** the winner (or a tie) with a drumroll, fanfare and applause, Richie's happy moves, the winner
   screen, goodbye and the outro. The phones keep the final scores for 30 s.
9. **Pause and quit** as every game: "pausa", his name or an antenna pauses, "se acabó el juego" ends it, any other
   request asks "¿lo terminamos?". The phones say why it may have paused and have a "Keep playing!" button, since a
   false wake in table talk pauses it too (twice in the 2026-11-01 game).

## Current architecture

| Piece | File | What it does |
|-------|------|--------------|
| Game | `games/big_brain.py` | `BigBrain(Game)`: the lobby, the 20 squares, hidden squares, the vote, the final, the ending; `snapshot(player)` is each phone's view, `on_message` takes phones' messages |
| Board | `games/big_brain_board.py` | `build_board(bank, language, kids, recent)`: rows by hardness quarter, themed columns (one per broad category) plus a broad one, three hidden squares, a final from the hardest quarter with appeal ≥ 2.3; no two clues sharing an accepted answer; `imperial()` leaves out clues measured in miles, feet or Fahrenheit; `remember`/`read_history` keep clues out for 30 days |
| Judge | `games/big_brain_judge.py` | `matches()` by code (case, accents, articles, lead-ins, slips ≥ 0.84 similar, a surname, a name without its generic word such as "Pacific" for "Pacific Ocean", the answer with a few words around it; a filler such as "um" is wrong), then Jev (`right`, `too_broad`, and for spoken answers `sounds_like`: speech recognition's spelling of an accepted answer, "Téphanis" for "Tiffany's") → `Ruling.RIGHT/WRONG/SPECIFIC`; rulings kept in `rulings.json` |
| Audio | `games/big_brain_audio.py` | `Clips` (host lines by moment, never the same take twice running), `Sounds` (as `chosen.json` picks), `Speech` (Eleven v4 Turbo, raw 24 kHz PCM, kept by text) |
| Lines | `games/big_brain_lines.py` | 41 moments, 334 takes in English and Venezuelan Spanish with ElevenLabs audio tags |
| Phones | `games/big_brain_phones.py`, `static/big_brain/index.html` | FastAPI on `0.0.0.0:BIG_BRAIN_PORT` inside Reachy's loop (no signal capture), only during a game; the code in every request; joins kept here |
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
  `pick` {column, row}, `buzz`, `answer` {text}, `bet` {points}, `vote`; the server sends `welcome` {player},
  `forget`, `error` {message: closed | name | join} and `state` (phase, language, kids, you, teams, board,
  control, clue {column, points, text once read, hidden}, buzzed, deadline (epoch ms), can, max_bet, vote,
  message, final {category, answers}).
- Timings (`games/big_brain.py`): pick 40 s, buzz 8 s (6 s after a miss), 6 s for an answer to start (passed to
  the runtime as `say_audio(..., listen_s=)`, where silence then reaches the handler as ""), everyone 30 s, bets up
  to 30 s, final answers 30 s, the page stays 30 s after the end.
- Settings: `ELEVENLABS_API_KEY`, `GAME_DATA_DIR`, `BIG_BRAIN_PORT` (7880), `BIG_BRAIN_PUBLIC_URL` (the QR code's
  https address through Tailscale Funnel; on the Mac mini `https://eduardos-mac-mini.tailfbc80d.ts.net/bigbrain`,
  set up with `tailscale funnel --bg --set-path /bigbrain http://127.0.0.1:7880`).

## Extension guide

- New host lines: add takes to `big_brain_lines.py` (known tags only; the tests check), then
  `uv run python scripts/big_brain_voice.py --out <game-data>/big-brain/voice --check` makes only the new ones.
- New sounds: add a prompt to `scripts/big_brain_sounds.py`; pick a take by editing `sounds/chosen.json`.
- More clues: `scripts/trivia_bank.py --pick hard|appeal --rewrite-top N`, then `scripts/trivia_topics.py`.

## Constraints, privacy, and failure modes

- The phone page is plain http on the home Wi-Fi, so no phone microphone (browsers allow it only on https).
  Guests on a Wi-Fi with client isolation cannot reach the Mac mini.
- Android blocks Chrome from home-network addresses until Chrome has the **Nearby devices** permission (Settings →
  Apps → Chrome → Permissions); without it the join link fails with `ERR_TOO_MANY_RETRIES`. Found on a Pixel 9a
  on 2026-11-01; every Android guest needs it once.
- Only the buzzing player is listened to, and only right after the buzz; nothing is recorded or kept beyond the
  usual local transcript. Typed answers and names stay on the Mac mini for the game (rulings keep the answer
  text, without names).
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

## Testing

- `uv run pytest tests/games/test_big_brain.py` (16): board shape and order, kids mode, the 30-day memory, the
  judge by code in both languages, Jev's kept rulings and the room's overrule, the QR code's structure, the
  phones' code and joining (FastAPI test client), and a whole show with two pretend phones from joining to the
  winner (red always right wins; the reveal goes from the lowest score; 21 clues remembered).
- `tests/test_hybrid_runtime.py::test_a_game_announcement_can_play_recorded_speech_instead_of_saying_its_text`.
- `tests/games/test_big_brain_lines.py` (4): both languages, known tags only, tags stripped for screens.
- Full gate on 2026-11-01: `ruff check . --fix && ruff format . && mypy --pretty --show-error-codes && pytest
  tests/ -v` → 2,928 passed, 4 skipped.
- QR codes decoded by macOS Core Image for 2, 33, 72 and 100-byte texts (versions 1, 3, 4, 5).
- The phone page was tested by its author against a stand-in server in the browser at 360 px; not yet against
  the real server or an Android phone. Not yet played on the robot.
