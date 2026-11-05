# Feature: Ballpark (range-guessing party game)

- Status: `in-progress` (built and tested 2026-11-05; not yet deployed or played)
- Last updated: 2026-11-05
- Plan: [ballpark.md](../plans/ballpark.md)
- Part of: [games.md](games.md) (the shared game host); phones, host voice and score tiles borrowed from
  [big-brain.md](big-brain.md) and [sheeple.md](sheeple.md)

## Overview

Ballpark is a party game for teams that Richie hosts in Big Brain's game-show voice. Every question has a whole
number for an answer, and teams don't guess the number: they write a range they are sure it is in. The smallest
range that holds the answer wins the most, so the game rewards knowing how much you know. It is our own take on
range-guessing games like Confiante? (Confident?), with our name, questions and twists. Players join on their
phones by scanning the QR code on the TRMNL, as in Big Brain. Spanish or English.

## User-facing behavior

1. **Start:** "Richie, juguemos Ballpark" / "let's play Ballpark" (or "bolpark", "el más o menos", "el juego de
   los rangos", "the range game"), then "sí". Gemini can start it with `play_game` (`ballpark`).
2. **Join:** the TRMNL shows the QR code, the six-letter code and the four teams. Each phone types a name and picks
   a team; a team answers together, from any of its phones. Any phone taps "¡Empezar!" (or the room says
   "¡empezamos!"). Richie explains the rules and the powers (two with two teams, only Double with one team).
3. **A question:** Richie reads it ("Pregunta 3. Ahí va... ¿Cuántos dientes tiene un perro adulto?"), and the
   TRMNL shows it with its unit ("Respuesta en: dientes") and which teams have sent. Each phone has "Desde" and
   "Hasta" number fields, the powers left, and Enviar. Teams have 60 s ("¡Quince segundos!" near the end),
   less once every team has sent; a team can change its range until then, and every phone on the team sees it.
4. **Powers**, once per game each, secret until the reveal:
   - **Copia:** no range; take another team's (a chain is followed; teams copying each other get nothing).
   - **Cambio:** two other teams trade ranges (three teams or more).
   - **×2:** this question's points count double.
5. **The reveal:**
   - The TRMNL draws every team's range as a bar on one number line, on a log scale when the ranges are far
     apart.
   - Richie announces any powers ("¡Verde copió a Rojo!"), roasts the widest range ("¡Eso no es un rango, eso es
     una urbanización completa!") and calls the narrowest brave.
   - A drumroll, then the answer drops onto the line as a dashed marker, and Richie reads a fun fact.
6. **Scoring:**
   - The smallest range that holds the answer scores 3 (equal sizes all score 3), and any other range that holds
     it scores 1.
   - When every team is right, the widest scores 0.
   - Double multiplies the points.
   - Richie says who got the big points, a bullseye (an exact number), a near miss, a team that was way off, or
     whether a copy or a double paid off.
7. **The surprise:** one question from the 3rd to the 7th is announced with a fanfare. It is either "Exacto = 5"
   (an exact right answer scores 5) or "Puntos dobles" (every point counts twice).
8. **The end:**
   - The first team at 15 or more wins. When two get there together, the higher score wins, then the smaller
     range on that question.
   - A game where nobody gets there ends after 20 questions.
   - Richie names the winner and the bravest range of the game, and the phones show the final scores.

## Architecture and data flow

- `games/ballpark.py` holds:
  - `Ballpark(Game)`, the setup (`BallparkSetup`), the bank loaders (`load_questions`, `question`,
    `pick_questions`);
  - the pure rules: `resolve` (copy chains, then swaps in team order, then doubles) and `score`;
  - `to_whole`, which reads a phone's number.
- `games/ballpark_lines.py`: Richie's fixed moments, three or so takes each, in both languages, with ElevenLabs
  audio tags. Team names, ranges, questions, answers and facts are said live in the host voice (kept by text in
  `game-data/ballpark/speech/`).
- `games/ballpark_screens.py`: the question screen and the number line (`render_question`, `render_reveal`).
  Joining and the winner screen are Big Brain's (`render_join` with the "BALLPARK" title and range icon,
  `render_winner`). The header's measuring-tape motif is `ruler` in `eink_display._motif`.
- `static/ballpark/index.html`: the phone page. `?demo=<join|lobby|listen|answer|copy|sent|reveal|revealed|
  paused|over|busy>&lang=en` shows sample states with no game running.
- `games/ballpark_questions.jsonl`: the question bank, shipped with the app (package data). Played question ids
  are kept in `game-data/ballpark/history.json`; recent ones come last.
- Sounds are Big Brain's: `intro`, `board`, `hidden`, `steal`, `time_up`, `drumroll`, `correct`, `wrong`,
  `applause` and `fanfare`.

## Data contract

One JSON object a line:

```json
{"id": "an-02", "theme": "animals",
 "question": {"es": "¿Cuántos dientes permanentes tiene un perro adulto?", "en": "How many permanent teeth does an adult dog have?"},
 "answer": 42, "unit": {"es": "dientes", "en": "teeth"},
 "said": {"es": "cuarenta y dos dientes", "en": "forty-two teeth"},
 "fact": {"es": "…", "en": "…"}, "sources": ["https://…", "https://…"], "checked": "2026-11-05"}
```

The fields:

- `answer` is a whole number from 0 to 10¹³ in metric units.
- `said` is how Richie announces it (in words; "unos/about" when approximate).
- `unit` is the short word shown after the number.

Phones send `{"type": "range", "low", "high", "copy", "swap": [a, b] | null, "double"}`. The game refuses a
range without both ends, a copy of the own team, a swap that names the own team or a missing team, and spent
powers (a spent double is ignored).

## The question bank

- 149 questions (2026-11-05) in five themes: animals, the body and food, space and the world, films (Disney,
  Pixar, Harry Potter, Star Wars, the Lord of the Rings, Marvel) and pop culture (TV, music, games, toys,
  sports).
- They were written by Claude agents with web search, then checked by separate agents against two sources each:
  - 170 written, of which 17 dropped and 46 corrected (wording pinned, such as "theatrical release", "according to
    Guinness" or "in 2017", or the fact rewritten);
  - 4 more dropped as duplicates or identifiers (a Pokédex number).
- Avoided on purpose:
  - figures that keep changing (populations, current records, moons of Jupiter);
  - answers sources disagree on;
  - identifiers (vault or race numbers);
  - non-metric units.
- To add questions, write them in the same shape, check each answer against two sources, and keep ids unique.
  `tests/games/test_ballpark.py` checks every entry.

## Configuration and enabling

Always registered (its questions ship with the app). It shares Big Brain's phone port and public address
(`BIG_BRAIN_PORT`, `BIG_BRAIN_PUBLIC_URL`) and the host voice (`ELEVENLABS_API_KEY`); without the key, Richie's
everyday voice hosts. Clips are made with:

```bash
uv run python scripts/big_brain_voice.py --set ballpark --out ~/.local/share/reachy/game-data/ballpark/voice --check
```

## Constraints and gotchas

- One phone game at a time (the shared port).
- The e-ink fonts have no arrows, so notes on the number line are words ("copia a Azul").
- A take that follows a live name or range must work after any of them, and after several names
  (`match_point`).
- Swap needs two other teams; the phone hides it and the game refuses it with fewer than three teams.
- No kids mode yet; the questions are family-safe but made for adults and older kids.

## Tests

`tests/games/test_ballpark.py` covers:

- the bank (both languages, whole numbers, sources, recency);
- phone numbers;
- copy chains, loops and swaps;
- every scoring rule, the surprise and doubles;
- a phone's range and spent powers;
- the screens;
- the names that start it;
- a whole game on the real host with three pretend teams;
- that every moment has tagged takes in both languages.

## Changelog

- 2026-11-05: built: phones, Copy/Swap/Double, the surprise question, first to 15, 149 checked questions.
