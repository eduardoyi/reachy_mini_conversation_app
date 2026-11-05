# Plan: Ballpark (a range-guessing party game on phones)

- Status: `in-progress` (designed 2026-11-05 with Eduardo; being built)
- Owner: Eduardo
- Created: 2026-11-05
- Last updated: 2026-11-05
- Builds on: [Party games](party-games.md) (phones, the TRMNL board, Richie's host voice), Sheeple's code

## Goal

A party game for the grown-ups and older kids in the style of range-guessing games like Confiante? (Confident?)
and Wits & Wagers, but our own: our name, our questions, our twists. Richie asks a question whose answer is a
number ("How many bones does a cat have?"). Each team doesn't guess the number; it writes a range it is sure
the answer is in. The smallest range that holds the answer wins the most points, so the game is about how
confident you are, not what you know. Richie roasts the widest ranges and builds up to the answer.

## Decisions (Eduardo, 2026-11-05)

- **Twists:** each team has three powers, once per game each, chosen in secret on the phone with its range:
  - **Copy:** don't write a range; take another team's.
  - **Swap:** two other teams trade ranges (only with three teams or more).
  - **Double:** this question's points count twice.

  There is also **one surprise question per game**, announced when it comes up: either "Nail it" (the exact
  number scores 5) or "double points for everyone".
- **The end:** the first team to 15 points wins.
- **Questions:** silly general facts (animals, the body, food, space, records) and pop culture (films,
  cartoons, TV, music, games). A betting round and Richie playing as a team were not chosen.

## Design

- **Name:** "Ballpark" in both languages, as Sheeple and Big Brain keep their English names. Asked for by
  name, or as "más o menos", "el juego de los rangos" and "entre cuánto y cuánto".
- **Teams:** the four Big Brain colors. A team answers together: any phone on the team can type the range and
  pick powers. Every phone on the team sees the same draft, and the last one sent counts. A family that wants
  to play one-on-one picks one color each.
- **A question:**
  1. Richie reads it, and the TRMNL shows it with its unit.
  2. The phones show two number fields, "from" and "to", the powers left, and Send.
  3. Teams have 60 seconds, with a "hurry" line at 15 seconds left. Answering ends sooner once every team has
     sent.
  4. The reveal:
     - every team's range shows up as a bar on one number line (on a log scale when the ranges are very far
       apart);
     - powers are announced and applied (copy, then swap, then double);
     - Richie roasts the widest range;
     - a drumroll, then the answer drops onto the line, then a fun fact;
     - then the points.
- **Scoring** (as in the original, to keep the strategy right):
  - a range holds the answer when low ≤ answer ≤ high;
  - the smallest range that holds it scores 3 (tied sizes all score 3), and every other range that holds it
    scores 1;
  - if every team holds it and there are two or more teams, the widest scores 0;
  - Double makes it ×2; the "Nail it" surprise makes an exact answer (low = high = answer) score 5.
- **Copy and swap:**
  - copy follows a chain (A copies B, who copies C: A takes C's range);
  - teams that copy each other in a loop score nothing that question, and so does copying a team that sent
    nothing;
  - swaps then apply in team order;
  - a used power is spent only when it was sent, once the question is revealed.
- **The end:** a team at 15 or more wins after the question that got it there; when two get there together, the
  higher score wins, then the smaller range on that question, otherwise both win. A cap of 20 questions ends a
  game where nobody scores (the highest score wins).
- **The surprise question:** a random question from the 3rd to the 7th, either kind equally likely. A
  `hidden` sound plays and Richie explains it before the question.
- **Questions bank:**
  - ships with the app as `games/ballpark_questions.jsonl`, about 150 questions (Sheeple's surveys are
    Eduardo's, so they stay on the mini; these are written for the game, like the Detectives cases);
  - each question has Spanish and English text, a whole number answer of zero or more in metric units, the
    unit, how the answer is said ("unos 70 kilos"), a fun fact, a theme and a source URL;
  - the questions are written and fact-checked by Claude agents with web search: one pass writes, an
    independent pass checks every answer against a second source;
  - questions played in the last games come last, as in Sheeple (`history.json` on the mini).
- **Voice:**
  - fixed moments are recorded clips in the Big Brain host voice (`games/ballpark_lines.py`, `--set
    ballpark`), with ElevenLabs audio tags;
  - questions, answers, facts, team names in copy/swap lines, and ranges are said live in the same voice
    and kept by text.
- **Sounds:** Big Brain's (`intro`, `board`, `hidden`, `steal`, `drumroll`, `correct`, `wrong`, `time_up`,
  `fanfare`, `applause`). Nothing new to make. No music under the answering: a clip would hold up the "hurry"
  line and the early end.
- **Screens (TRMNL):**
  - the join screen with a "BALLPARK" title and icon;
  - the question with its unit and who has sent;
  - the reveal number line with each team's bar, the answer marker and points;
  - Big Brain's winner screen.
- **Phones:** `static/ballpark/index.html`, built on Sheeple's page (join, lobby, pause, game over), with the
  range form, power buttons and reveal list of its own, and a `?demo=` mode for checking the design.
- **No kids mode** in the first version; the questions are family-safe.

## Acceptance

- A whole game plays on the real host with pretend phones in tests: ranges, every power, the surprise, the
  scoring rules above and the first team to 15.
- Every moment Richie plays has two or more tagged takes in both languages.
- The bank loads, every entry has both languages, a whole number answer and a source; a test checks it.
- The full gate passes; the phone page's demo states look right on a phone-sized screen.
- Shipped with its catalog entry, `main._build_games` registration, Games-tab card and art, recorded lines,
  docs (`docs/features/ballpark.md`), and the cost stated when asking to deploy.

## Progress

- 2026-11-05: designed with Eduardo and built.
  - The game, phone page, screens, host lines (both languages), catalog entry, registration and Games-tab card
    are done, with tests covering the scoring, powers and a whole game with pretend phones.
  - Five Claude agents wrote 170 questions with web search, and five more checked them against two sources each:
    17 dropped and 46 corrected; 4 more dropped as duplicates or an identifier, leaving 149.
  - Not yet: clips recorded, deployed, played.
