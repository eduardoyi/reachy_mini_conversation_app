# Plan: La carrera de números (a number race for the kids)

- Status: `in-progress` (designed 2026-11-09 with Eduardo; recorded and deployed to the Mac mini 2026-11-09; not played with the kids yet)
- Owner: Eduardo
- Created: 2026-11-09
- Last updated: 2026-11-09
- Builds on: [Games on Reachy](games.md) (the game host, the kids' recorded voice and sounds, "¿Otra vez?"), [La gallina loca](la-gallina-loca.md) (turns passed by a head pat)

## Goal

Richie's first learning game, and the first card on a new "Learning games" shelf in the admin app. It is the top
pick of the research proposals ("Richie's Learning Lab", 2026-11-09): Siegler and Ramani's straight number-track
board game. In their studies, four 15–20 minute plays of a 1–10 race game took low-income 4–5-year-olds from 15%
to 61% on placing numbers in order on a line, with counting, number reading and "which is bigger?" gains still
there 9 weeks later. Across 18 later studies the effect is smaller but real (g ≈ 0.21). The same game on a round
board did not work, and children who say the numbers they land on, counting on from their square, learn more.

For Enzo (4): numbers 1–10 in order, "farther means bigger", counting on. For Mara (2): one number word per hop,
in order.

## Decisions (Eduardo, 2026-11-09)

- **Build the number race first**, of the five proposals.
- **Learning games are their own category**: a "Learning games" shelf in the admin app's Games tab, after "For
  little ones". Later learning games (sound games, "Muéstrame tres") go on the same shelf.
- **Afternoon sessions**, Spanish at home: the game is played in Spanish (and English, the kids' voice's other
  language). Portuguese waits for a European Portuguese voice.

## How a game goes

1. "Juguemos a la carrera de números" (or "los números", "a contar", "number race"), then the usual yes. A
   jingle, and Richie explains in one breath: the kids drive Tito, he drives Flecha (the cars from the story *La
   gran carrera*), a pat spins the wheel, and they count the squares out loud.
2. **The screen**: two straight roads of squares 1 to 10, Tito's on top and Flecha's under it, so the car
   farther along has the bigger number. Each car sits over its square; a checkered finish after 10.
3. **The kids' turn**: "¡Te toca!", then a pat on his head (or "¡gira!", "¡ya!", heard without his name) spins a
   wheel of 1 or 2. After 12 s he reminds them; after 30 s he spins for them (nothing ever waits for long). The
   squares to count turn grey on the screen and he says "¡Dos! A contar…". The kids count out loud from Tito's
   square ("cinco, seis"); Scribe hears the answer with the number words as hints.
   - The number Tito reaches heard anywhere in the answer: "¡Muy bien!" and the number again ("¡Seis!"), so the
     last word is the total.
   - Anything else, or nothing: "¡Contemos juntos!" and he counts the squares himself ("cinco… seis"). Never a
     "no" or a "wrong".
   Tito moves on the screen.
4. **Flecha's turn**: he spins, says what came out and counts Flecha's squares out loud, counting on from where
   Flecha is, so the kids hear how it is done.
5. **The end**: the first car to 10 wins (a spin past 10 stops at 10). Tito first: "¡Tito llegó al diez!" and a
   celebration; Flecha first: "¡Flecha llegó primero! … ¡La próxima le ganamos!". Nobody is told they lost.
   Then "¿Otra vez?", like the other kids' games; the number race is one of them, so "no, ¡la gallina!" switches.

About 6–8 kids' turns a race, 3–4 minutes.

## Scope

- In: the game (`games/number_race.py`), its screen (`games/number_race_screens.py`), catalog entry and kids'
  game, its lines in the kids' voice (`games/kids_lines.py`), three sounds and a jingle, its card and art on the
  new Learning games shelf, tests, docs.
- Out (later, if the kids like it): a 1–20 track for Enzo, the kids counting Flecha's squares too, "¿Quién va
  ganando?" questions, a per-child skill map, Portuguese.

## Assumptions

- A pat registers while the game holds the head still (`Game.holds_head`), as in la gallina loca.
- Scribe with the number words as keyterms hears a 4-year-old's "cinco, seis"; a 2-year-old's try may not be
  heard, which is why anything other than the number gets "contemos juntos", never a correction.
- The e-ink screen changes twice a kids' turn (the squares to count, then the car moved) and once on Flecha's;
  turns are long enough for the TRMNL to keep up.

## Implementation order

1. Catalog entry (`NUMBER_RACE`, key `numbers`) and `KIDS_GAMES`.
2. The screen: two lanes, numbered squares, the two cars from the story's character sheets (shrunk once by
   `scripts/race_cars.py`, no image calls), counted squares grey.
3. The game: spin wait (pat or word), the count question, Flecha's turn, the end, "¿Otra vez?".
4. Lines (`race_*`, `num_1`…`num_10`, es and en), sounds (`spin`, `putt`, `zoom`) and the `intro_race` jingle.
5. Registration in `main._build_games` (no camera needed), the admin card on a new `learning` shelf, card art.
6. Tests; then record on the mini, deploy, and play with the kids.

## Acceptance criteria

- A race plays to the end with pats and spoken counts, and with nobody answering (Richie counts and spins).
- A right count is cheered with the number; any other answer or none gets the count done together.
- The screen shows both cars on straight 1–10 tracks and the squares being counted.
- "¿Otra vez?" plays again or switches to another kids' game.
- The Games tab shows a "Learning games" shelf with the Number Race card and art.
- Played with Enzo and Mara at least twice; Eduardo judges whether they enjoy it and whether Enzo counts on.

## Risks

- **Toddler speech**: Mara's counting will rarely be heard. Mitigation: nothing depends on it.
- **Repetition**: the same lines each turn. Mitigation: two to four takes per moment, never the same twice in a
  row.
- **Flecha wins half the races**, as in the original game. If it upsets the kids, Tito can get a small head
  start.

## Test strategy

`tests/games/test_number_race.py`: numbers heard in Spanish and English (words and digits); a whole race with a
fixed spinner, pats and right counts (cheers with the number, Tito wins, "¿Otra vez?", thanks); a race nobody
answers (the count done together, Richie spins after the wait); "¡gira!" spins without a pat; the head held
still; the number words sent as hints. `tests/games/test_catalog.py` for the names; `tests/admin/test_game_cards.py`
already checks the card and art; `tests/games/test_kids_audio.py` checks every moment is written in both
languages.

## Documentation

`docs/features/games.md` (the game, the Learning games shelf), this plan, the plans index.

## Cost to record (asked before deploying)

22 new moments, 98 takes in both languages, 4,112 characters with tags: up to about 4,100 ElevenLabs credits
on Eleven v4 Turbo (fewer if turbo is billed at a discount). Three sounds of two takes (6 × 200 = 1,200 credits)
and one 10 s jingle of two takes (music credits). The card art was one image call, already made.

## Progress log

- 2026-11-09: Designed from the research proposals and built: catalog, screen, game, lines, sounds list, admin
  shelf and card, tests. Not yet recorded or deployed.
- 2026-11-09: The cars on the screen are now Tito and Flecha as drawn in the story (Eduardo asked): their
  character sheets shrunk to the four greys by `scripts/race_cars.py`, in place of the shapes first drawn.
- 2026-11-09: Recorded and deployed. 98 lines in the kids' voice on the mini (4,112 characters, none failed; the
  check's 16 race flags are digits for number words and "Flecha" heard as "Fletcher", no tag read aloud);
  `spin`, `putt`, `zoom` and the `intro_race` jingle made on the laptop (two takes each, take 1 chosen) and
  copied to the mini; the app restarted with `numbers` among the games.
