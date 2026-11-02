# Feature: Detectives (mystery party game)

- Status: `in-progress` (first version built and tested on the laptop 2026-11-01; not deployed or played)
- Last updated: 2026-11-01
- Plan: [party-games.md](../plans/party-games.md) ("Detectives")
- Part of: [games.md](games.md) (the shared game host); phones as in [big-brain.md](big-brain.md)

## Overview

Detectives is a whodunit for the family or a game night. Richie tells a pre-written case, plays every suspect and
witness, and reveals the truth at the end; players investigate in turns on their phones. Cases are fixed data
written in English and Spanish, so the story cannot contradict itself: only the witnesses' answers are made at
game time, by a text model that sees one character's sheet and nothing else. Adults play a dinner-party case;
kids mode plays "¿Quién se comió las galletas?" (the cat, the grandmother, Uncle Pancho and Richie himself).

Changed from the plan: the plan had a strong model write the case at the start of each game; this version uses
cases written ahead into the repo (more reliable, free, reviewable). Searching the house with the camera, dim
lights and noir music are not built.

## User-facing behavior

1. **Start:** "Richie, juguemos Detectives", "let's play Detectives", "el juego de detectives", "el caso",
   "juguemos a los detectives", "mystery game", "murder mystery" (and mishearings), then "sí". Gemini can start it
   with `play_game` (`detectives`). The game speaks the language it was asked in.
2. **Join:** the TRMNL shows a QR code, a six-letter code and who has joined. Each phone scans it and types a name.
   Richie welcomes them. A "Caso para niños" switch picks the kids' case. Anyone taps "¡Empezar!" (or the room
   says "¡empezamos!").
3. **The case:** the TRMNL shows the title and what happened while Richie tells it; each phone shows its secret
   role. Everyone is a detective. In an adult game of three or more players, one detective is also a **secret
   witness**: their phone shows a private fact from the case (`insider`) to share or keep.
4. **Turns:** rounds go round the players in the order they joined; the TRMNL board shows the suspects, the evidence
   found, the round and whose turn it is. The player on turn has 60 s and a glowing phone with two choices:
   - **Question someone:** tap a suspect or witness. A question typed in the box is used as it is; with the box
     empty Richie says "Ana, what do you ask Valeria?" and listens to the next thing said (only then, so one voice
     at a time). The question and the answer appear on the TRMNL and in every phone's notebook, and Richie says the
     answer (two or three sentences, in character).
   - **Examine a clue:** tap one of the clues open this round (each clue has the round it opens in). It goes up on
     the TRMNL and every phone, and Richie reads it.
   Doing nothing for 60 s passes the turn.
5. **Accuse early:** any phone can vote to accuse now; once most players have, the current turn ends and the
   accusation starts. Otherwise it starts after the case's last round.
6. **Accusation:** each phone chooses in secret who did it and, optionally, why (the case's motives). It ends
   after 90 s or once everyone has accused.
7. **The truth:** Richie reveals the solution step by step on the TRMNL, names the culprit (drumroll), then who
   solved it (fanfare) and who also got the motive, or says the culprit won. Phones show the culprit, the motive,
   whether you got it right and who did; the page stays 30 s.
8. **Pause and quit** as every game; a paused game's phones show "Keep playing!".

## Cases: the JSON schema

Cases live in `src/reachy_mini_conversation_app/games/detectives_cases/<id>.json` (shipped with the app, listed in
`pyproject.toml` package data). Every text is an object with non-empty `"en"` and `"es"`, each written in its
language (not translated word for word). `detectives_case.parse_case` checks every rule below and names the field
that breaks one; `tests/games/test_detectives.py` loads every case file, so a broken new case fails the gate.

```jsonc
{
  "id": "last-toast",              // unique; also the file name; kept in the played history
  "kids": false,                   // true: played in kids mode only (gentle, silly)
  "rounds": 4,                     // 1–8 rounds of one turn per player (adults 4, kids 3 so far)
  "title": {"en": "...", "es": "..."},
  "intro": {"en": "...", "es": "..."},   // what happened, read by Richie at the start; public
  "characters": [                  // at least three suspects; witnesses optional
    {
      "id": "julian",
      "kind": "suspect",           // "suspect" (may be accused) or "witness"
      "name": {...},
      "about": {...},              // one public line on the phones and board
      "personality": {...},        // how the model plays them
      "alibi": {...},              // where they were (the truth; "lies" says what they claim instead)
      "knows": [{...}, ...],       // facts they share when asked (at least one)
      "secret": {...} | null,      // what they hide; for the culprit, what they did and why
      "lies": {...} | null,        // what they lie about; the culprit never confesses
      "fallback": {...}            // said when the model fails: an in-character dodge with no new facts
    }
  ],
  "culprit": "julian",             // a suspect with a secret and a lie
  "motives": [{"id": "secret", "text": {...}}, ...],   // at least two, offered in the accusation
  "motive": "secret",              // the true one
  "clues": [                       // examined on a turn; at least one in round 1
    {"id": "glass", "round": 1, "title": {...}, "text": {...}}
  ],
  "insider": {...} | null,         // a private fact for one adult player (the secret witness)
  "reveal": [{...}, ...]           // the solution step by step; the culprit is named in it
}
```

Writing a good case: every clue and every fact a character knows must agree with the solution; the culprit's
sheet holds the whole truth of what they did (the model needs it to lie well); innocent suspects each get a
secret that makes them look guilty (red herrings), cleared by a clue or a witness; the decisive clues open in the
later rounds; the reveal explains why each red herring is innocent before naming the culprit. Witness answers can
only be as consistent as the sheets, since the model sees only the speaking character's sheet, the public intro,
the clues found and the others' names and `about` lines.

Cases so far: "The Last Toast" / "El último brindis" (a poisoning at a birthday dinner, four suspects and the
housekeeper), "Blackout at the Gala" / "Apagón en la gala" (a stolen necklace, five suspects and the bartender),
"Who Ate the Cookies?" / "¿Quién se comió las galletas?" (kids: the cat, Grandma, Richie, Uncle Pancho and Pepe the
parrot). A game picks a case never played in that mode, else the one played longest ago
(`GAME_DATA_DIR/detectives/played.json`).

## Current architecture

| Piece | File | What it does |
|-------|------|--------------|
| Game | `games/detectives.py` | `Detectives(Game)`: lobby, roles, rounds and turns, the vote, the accusation, the reveal; `snapshot(player)` is each phone's view, `on_message` takes phones' messages; `LINES` holds Richie's fixed lines in both languages |
| Cases | `games/detectives_case.py`, `games/detectives_cases/*.json` | `parse_case`, `load_cases`, `pick_case`, `CaseError`; the dataclasses `Case`, `Character`, `Clue`, `Motive` |
| Witnesses | `games/detectives_witness.py` | `witness_instructions(case, character, found, language)`, `spoken()` (at most three sentences, no markdown), `gemini_witness(client, model)` |
| Phones | `games/big_brain_phones.py` (shared, `page=`), `static/detectives/index.html` | the same server and code rules as Big Brain, serving Detectives' page |
| Screens | `games/detectives_screens.py` | `render_join`, `render_board`, `render_card`, `render_solution` (800×480, four greys): a magnifying glass and crime-scene tape under every header, suspects as silhouettes (witnesses grey and tagged), evidence on numbered markers, players as name chips on the join screen, the culprit beside a mugshot |
| Sounds | `games/big_brain_audio.Sounds` | Big Brain's `correct`, `drumroll`, `fanfare`, `wrong` from `GAME_DATA_DIR/big-brain/sounds/` when made; skipped otherwise |
| Wiring | `catalog.py` `DETECTIVES`, `main._build_games`, `config.DETECTIVES_MODEL` | |

Richie speaks in his normal voice (`GameContext.say`); questions are heard through `GameServices.ask`.

## Contracts and configuration

- Enabled in `main._build_games` when `GEMINI_API_KEY` is set (the cases ship with the app); like every game, it
  also needs the game host (`HYBRID_RUNTIME_ENABLED`, `QUICK_COMMANDS_ENABLED`, `TYPESAFE_API_KEY`).
- `DETECTIVES_MODEL` (default `gemini-3.8-flash`): the Gemini text model for witness answers, 15 s at most, else
  the character's `fallback` line.
- Phones use `BIG_BRAIN_PORT` (7880) and `BIG_BRAIN_PUBLIC_URL`: only one game runs at a time, and the existing
  Tailscale Funnel path reaches whichever page the port serves.
- Phone protocol (`/ws?code=ABCDEF`, JSON): phones send `join` {name, team: "red"} (the shared server needs a
  team; Detectives ignores it), `start`, `kids` {on}, `ask` {id, text}, `examine` {id}, `vote`, `accuse`
  {suspect, motive}, `resume`; the server sends `welcome`, `forget`, `error` and `state` (phase: lobby, intro,
  turn, asking, answering, clue, accuse, reveal, over, paused; title, intro, kids, you {name, role}, players, round,
  rounds, turn, characters, clues found, available (clues the player on turn may examine), notes, votes, motives,
  accused, deadline, can, message, result).
- Timings (`games/detectives.py`): turn 60 s, question 25 s, witness 15 s, accusation 90 s, the page 30 s after.

## Extension guide

- **A new case:** add a JSON file following the schema above (both languages), run
  `uv run pytest tests/games/test_detectives.py`, then play it once to hear the witnesses. No code change.
- New fixed lines: `LINES` in `games/detectives.py`, both languages.
- Do not give the model more than the speaking character's sheet: the solution and the other characters' secrets
  stay out of every prompt (a test checks it).

## Constraints, privacy, and failure modes

- Questions are heard only right after Richie names the player; typed questions and names stay on the Mac mini
  for the game. The question text goes to Gemini with the character's sheet; nothing is stored.
- Gemini's cost is not yet in the admin app's usage ledger (a few hundred tokens a question).
- If the model fails or is slow, the character's scripted `fallback` line is said instead (logged as a warning).
- The model can still improvise beyond the sheet; the prompt forbids new facts, but this is not yet checked on a
  real model.
- The phones' page shares Big Brain's caveats: plain http on the home Wi-Fi unless the Funnel address is set, and
  Android Chrome needs the "Nearby devices" permission for home-network addresses.

## Testing

- `uv run pytest tests/games/test_detectives.py` (20): every case file follows the schema in both languages and
  names its culprit in the reveal; two adult cases with 4–5 suspects and the kids' case with its silly suspects;
  broken cases name what is wrong; kids mode and the played history pick the case; a witness prompt holds only
  its own sheet and the clues found; answers cut to three sentences; the phones get Detectives' page; the screens
  render; the start phrases; and a whole game with three pretend phones and a fake witness model (a typed and a
  spoken question, a model failure answered by the scripted line, a clue, a pass, the vote to accuse in round 2,
  the secret accusation, the step-by-step reveal and who solved it).
- `tests/tools/test_play_game.py` lists the game.
- Full gate on 2026-11-01: `uv run ruff check . --fix && uv run ruff format . && uv run mypy --pretty
  --show-error-codes && uv run pytest tests/ -q` → 2,975 passed, 4 skipped (after `git lfs checkout`: in a fresh
  worktree the PNGs are LFS pointer files and 98 display tests fail).
- Not tested: a real Gemini model's witness answers, the phone page in a real browser or on Android, the TRMNL
  screens on the device, Richie's listening for questions on the robot, a real game night.

## Changelog

- 2026-11-02: Redesigned TRMNL screens in the case-file look described above (see also
  [games.md](games.md#what-the-screens-look-like)).
