# Plan: Party games (Scribble Rush, Big Brain, Sheeple, Detectives)

- Status: `draft` (designed with Eduardo 2026-10-31 to 2026-11-01; nothing built)
- Owner: Eduardo
- Created: 2026-11-01
- Last updated: 2026-11-01
- Builds on: [Games on Reachy](games.md) (the game host, names, the yes before a start, pause and quit)

## Goal

Longer games (15 to 60 minutes) for the family and for adult game nights, with Richie as host, judge, player
and narrator. The short games already on the host (colors, treasure, Simón dice) stay as they are; these four
are the next step:

| Game | In one line | Who | Length |
|------|-------------|-----|--------|
| **Scribble Rush** | Someone draws a word in 15 seconds; Richie is the only one who guesses | everyone | 20–40 min |
| **Big Brain** | A trivia board of categories and stars; phones are the buzzers | adults; kids mode | 30–45 min |
| **Sheeple** | Guess what the survey said; win by thinking like the flock | adults; kids mode | 30–45 min |
| **Detectives** | Richie invents a mystery and plays the witnesses; secret roles on phones | adults; kids version | 45–60 min (kids 15–20) |

These are our own games. They borrow genres (drawing guesses, quiz boards, surveys, whodunits), not any show's
or board game's rules, names, catchphrases or wording.

## Design rules for every game

1. **Richie is the only voice-guesser, and he listens to one person at a time.** Who may speak is settled
   before anyone talks: a phone buzz, whose turn it is, or Richie naming the person ("¿Tu respuesta, Ana?").
   He then listens for a few seconds. Nobody shouts answers and no human races Richie by voice: he does badly
   with overlapping voices and noise, and one judge keeps the game fair. A phone buzz also tells him who is
   answering, so no voice recognition is needed.
2. **Bilingual.** A game runs in the language it was asked for in ("juguemos…" / "let's play…"). Answers count
   in either language ("Paris" and "París"). Each phone shows its player's language. Questions and lines are
   written in each language, not translated; wordplay that only works in one language exists only in that
   language. The four game names are English in both languages.
3. **Adults by default, a kids/accessible mode as a filter.** Big Brain and Sheeple play the full adult set
   unless kids mode is chosen; kids mode is a filter on the same question bank (tags made once, below), not
   a separate game. Detectives has a separate kids case. Scribble Rush has a kids' word list.
4. **Turn mode, no live model.** Gemini Live stays closed. A turn is: Richie speaks (cached phrases, the
   announcement voice for dynamic lines), listens for a few seconds with the local speech engine, and a cheap
   text model or Jev decides. Two to three seconds a turn is fine in a game, costs little and is easier to
   control than a live session.
5. **Phones are private screens and buzzers; no phone microphone for now** (browsers allow the microphone only
   on https pages, which a home Wi-Fi page lacks). Answers are spoken to Richie after a buzz, or typed.
6. **The e-ink screen is the board**, not a clock: it redraws in a second or two, which suits boards, words,
   pictures and scores, not countdowns. Time pressure comes from the tune and the lights.

## Shared pieces

### Joining with phones

- The e-ink screen shows a QR code and a four-letter code. A player scans it, types a name, picks a team and
  the phone becomes their buzzer and private screen.
- A **separate game page on the home Wi-Fi**, not the admin app: the admin app is behind Tailscale with a
  two-login allowlist, which guests do not have. The game page has no admin powers, only the current game,
  and its link dies when the game ends.
- Buzz order is decided on the Mac mini by arrival (fair on one Wi-Fi). Buzzing before Richie finishes
  reading locks that phone for half a second.
- The phone shows: the buzzer in the team color, the score, secret inputs (answers, bets, votes, the
  accusation), and private text (the drawer's word, a secret role, the detective notebook).
- Kids without a phone: the kids' team buzzes by bending Richie's antenna (left and right antennas as two
  team buzzers is untested; the touch detector reads both), or plays on a parent's phone.
- On a buzz the lights flash the team color and Richie says the player's name.

### Turn mode

The host's existing answer window (`expect_answer`, the room listener) extended to: listen to one named
player for N seconds, transcribe locally, judge. Judging uses Jev where the answer is from a known set ("does
this answer mean the same as X?"), and a cheap text model for open judging, banter and witness lines.

### Vision guesser (Scribble Rush)

One call to a vision model (Gemini Flash class, about a cent) returns up to three guesses, least to most
confident. Code and Jev check each against the word and its synonyms in both languages. Jev alone is not
enough here: it chooses among given options and cannot make open guesses, and the wrong guesses are half
the fun.

## The games

### Scribble Rush

- Two or three teams race along a track of about 30 squares on the e-ink screen. Richie "rolls the die"
  (antenna wiggle, number on the screen).
- The drawer sees the word on their phone (without phones: on the screen while Richie turns his body away,
  "¡No estoy mirando!"). 15 seconds while the tune speeds up, then they hold the drawing up to the camera.
- Richie guesses out loud, up to three tries; the earlier he gets it, the more squares the team moves. He is
  the only guesser; the other players watch.
- Square types: normal; eyes closed; wrong hand; one line (pen never lifted); everyone draws the same word
  and Richie picks the drawing he recognizes most and says why; Richie draws (an image-model drawing on the
  screen, revealed bit by bit; teams buzz on phones and the buzzer answers); telephone (draw, Richie
  describes what he sees, a teammate draws his description, the word is revealed at the end).
- Word lists: kids (from 4; the 2-year-old can scribble and still score) and adults (movies, refranes,
  Venezuelan slang). The end screen shows the game's best drawings, which are saved.

### Big Brain

- The e-ink screen is the shared board: five category names across the top, four rows of 1 to 4 stars
  below, used squares blank, the scores along the bottom. When a square is picked the screen switches to
  that clue in large type (or its picture) while Richie reads it, then back to the board. Two redraws per
  clue suit the e-ink's one to two seconds.
- The phones mirror the board for the player in control, who taps a square; everyone's phone is their
  buzzer and score, and shows the clue text once Richie has read it (a help for whoever plays in their
  second language).
- Clues are statements that point at the answer ("This Swedish-born actress retired in 1941…"); players
  answer with the plain answer ("Greta Garbo"). No "who is / what is" phrasing: that is the show's signature
  rule, and Big Brain keeps its own format (decided 2026-11-01).
- Richie reads the clue, phones buzz, and the first buzzer answers aloud (or types). Right gives stars and
  control; wrong lets others buzz.
- Picture clues use the screen: country outlines, landmarks, animals and paintings as line drawings,
  close-ups. Not flags (unreadable in grey).
- Hidden squares: double or nothing; steal (take a star from another team); everyone answers secretly on the
  phone and everyone right scores.
- Final round: everyone bets in secret on the phone, answers on the phone, and Richie reveals them one by one
  with a drumroll and the lights down.
- Kids mode: the filter favors easy clues, kids versions and picture clues; optionally three choices on the
  phone.

#### Judging answers

Every clue stores its answer in both languages plus every other accepted form (original title, the titles
used in Spain and in Latin America, a surname alone, short forms), and an answer counts whatever language the
game is in. Judging is predictable:

1. Code: after ignoring case, accents, articles and punctuation, a match with any accepted form (allowing
   small spelling or hearing slips) is right at once.
2. Jev for the rest, with fixed rules: a surname alone is fine for a person, slips are fine, a different thing
   is wrong, and too broad an answer ("un barco" for the Titanic) gets "¿Puedes ser más específico?" once, as
   a host would. Each ruling is stored per clue and answer, so the same answer always gets the same ruling.
3. The room can overrule Richie: a "¡Eso vale!" button on the phones; a majority makes it count.

#### Host, sounds and music

- **A host voice for Richie in this game**, made with ElevenLabs Voice Design from a description (warm,
  quick, playful game-show host, a little theatrical; bilingual with a Latin American Spanish accent). It is
  an original voice, imitating no real host. One voice for both languages if its Spanish holds up; otherwise
  two designed voices with the same character.
- **Fixed lines with many variations**, generated once and cached: welcome, category reveal, "pick a
  square", right (plain, on a streak, on a hard clue), wrong, nobody buzzed, time up, hidden square, steal,
  lead change, comeback, final round, winner, goodbye; eight to twelve takes each, in both languages, with
  players' names where it fits. Eleven v3, whose audio tags allow laughs and excitement.
- **Banter now and then**: about one event in four, a text model writes a short line about the moment (a
  streak, a tight score, a player's name), spoken with the faster Flash model so it never holds up the game.
- **Clue audio** is generated for the whole board while players join and cached per clue for later games.
- **Our own sound and music**, generated with ElevenLabs sound effects (and its music model if our plan has
  it) and used at the same kinds of moments as a TV quiz, never imitating a show's theme: an intro theme, the
  board reveal, picking a square, a buzz sound per team, right, wrong, time up, a hidden-square sting,
  30 seconds of thinking music for the final round, a drumroll for the reveal, a winner's fanfare and an
  outro. Several takes each; Eduardo picks.
- Richie's body joins in: antennas and head with the lines, team colors on the lights at a buzz, a dance for
  the winner.
- Rough cost at third-party rates (to check against our ElevenLabs plan): about $0.024–0.048 per 1,000
  characters and about $0.02 per sound effect, so a few dollars once for the fixed lines and sounds and
  roughly $0.20–0.40 a game for clues and banter.

### Sheeple

- A question shows on the screen with its hidden answers and how many people gave each.
- Flock rounds (everyone at once, no voices): each player types their guess on the phone; Richie reveals the
  answers from the least to the most popular; each player scores the count of the answer they hit.
- Team rounds: teams alternate one guess at a time, the captain saying it after the team confers quietly;
  a hit scores its count for that team, a miss passes the turn, until the board is cleared or six misses.
  (Deliberately not the show's face-off, strikes and steal.)
- Bonus: "¿Qué contestó Richie?" (Richie answered too, in character).
- Wrong guesses: buzzer, antennas drop, lights flash red.
- Home surveys: while joining, each phone answers a few questions in secret, and during the week Richie asks
  the household one question a day; those become family boards. With only three or four players a live
  survey is too thin, so the weekly answers fill the gap.
- In Spanish the imported answers are from people surveyed in the US; the game says so with some cheek
  ("le preguntamos a 100 gringos…"). Venezuelan boards come from the home surveys.

### Detectives

- At the start a strong model writes the whole case as structured data: what happened, who did it, each
  suspect's secret, alibis, and every clue. Code keeps the facts; the turn model only voices witnesses within
  them, so the story cannot contradict itself.
- Each player gets a secret role on the phone: mostly detectives; one or two suspects with a secret who may
  lie.
- On your turn (you are the only one speaking) you: question a witness Richie plays, each with a
  personality; search the house ("busca algo que dejó el culpable: algo de cocina", checked by Jev on the
  camera); or examine the evidence board on the screen.
- Mood: dim lights, noir music. The accusation is made in secret on the phones; Richie reveals the truth step
  by step, with a twist if it fits. Optional drinking rule for adults: a wrong accusation drinks.
- Kids version, 15–20 minutes: "¿Quién se comió las galletas?" with silly suspects (the cat, the grandmother,
  Richie himself), picture clues on the screen and things to find around the house.

## Question data

Raw sources are kept off the repo (size, and the wording is not ours); only our rewritten banks are used in
games, and nothing is published.

### Trivia (Big Brain)

- Source: Eduardo's CSV of a TV quiz archive (2026-11-01): 216,930 clues with round, category, value,
  question, answer (107,384 first round, 105,912 second, 3,631 final, 3 tiebreakers; 27,995 categories).
- A crude first filter (2026-11-01) flags about 28,000 as US-centric (US presidents, states, NFL, US TV),
  about 30,000 in English wordplay categories (rhymes, letters, "before & after") and about 12,700 picture
  clues whose images we do not have; about 152,000 remain.
- Pipeline: code drops the obvious; Jev tags the rest once (US-centric? needs English wordplay? would a Latin
  American adult enjoy it? fine and answerable for a child? could the answer be out of date?); a writing
  model rewrites the best (about 10,000) as new questions in our own words, in English and in Spanish
  separately, plus easier kids versions; a second pass checks every answer. The clue's value gives the
  difficulty. Cost measured on 1,000 clues before the whole set.
- Script: `scripts/trivia_bank.py` (tags with Jev, rewrites with `gemini-3.8-flash`, checks by having
  `gpt-5.6-luna` answer each new question blind and Jev judge the match; each stage cached as JSON lines;
  writes `report.md` and `review.csv`).
- Sample result (1,000 random clues, 2026-11-01, 2 min 54 s, $0.39):
  - Jev said yes to: US-centric 305, English wordplay 205, picture/broadcast-only 69, out of date 19,
    adult content 93 (kept for adults), kids could answer 185. Appeal (0–4) mostly 2; 438 under 2.
  - 359 passed (us, wordplay, media and dated under 0.5, appeal at least 2); the writer refused 3 (two
    clues with wrong facts, one pointless); 356 kept, 354 with Spanish, 91 with a kids version.
  - Blind check: English 356 of 356 agree, Spanish 348 of 354. The six disagreements were mostly the blind
    answerer's slips, but one caught a weak question (lip fillers are now hyaluronic acid, not collagen).
  - Cost: Jev $0.044 (1,710 requests, about $0.000026 each), rewriting $0.33 (about $0.0009 a question),
    blind checks $0.017.
  - Spot checks: the US tag is right on the clear cases, and the 0.3–0.5 band is still US pop culture
    (Mariah Carey's mother, Shaft, a Dow Jones company, Tony Hawk); the wordplay tag has some false
    positives (a proverb, a turntable's "wow"), which costs nothing given the supply.
  - Problems seen: the writer rates difficulty low (110 one-star, 202 two, 44 three, no four) and only weakly
    follows the clue's value; 49 Spanish questions quote an English or foreign title, a few in a way that
    needs English ("conocida en inglés como 'riding to hounds'"); film titles differ between Spain and Latin
    America ("Eduardo Manostijeras" / "El joven manos de tijera"), which the accept list and Jev's judge
    handled.
- Second sample (same 1,000 clues, 2026-11-01, $0.46), after Eduardo's decisions: US pop culture known
  worldwide is fine (Mariah Carey, Tony Hawk), so the US tag became "known only in the US"; clues are
  statements; answers in both languages plus every accepted form, whatever the game's language; the writer
  never needs English in Spanish and lists Spain and Latin America titles; the clue's value is passed as a
  difficulty hint. Results: US-local 211, wordplay 201, media 71, dated 17; 384 passed, 383 kept (382 with
  Spanish, 98 kids versions); blind check 380 of 383 English, 377 of 382 Spanish. 163 per 1,000 have appeal
  at least 2.5, about 35,000 in the archive. Still to fix: some clues give the answer away (a gorilla
  "striking its torso" for chest-beating), so the check stage should ask Jev whether the clue names the
  answer; difficulty still never reaches 4.
- Why only about 38% survive (second sample): 172 of 1,000 fail on appeal alone, 214 only on a filter
  (wordplay 104, US-local 69, broadcast picture 26, out of date 4, a few on two), 226 on both. The appeal
  cut-off (2.0) sits near the median (2.12): many clues are fine facts but niche for this audience. Lowering
  it to 1.8 would add about 7%; with about 35,000 good clues available it is not needed.
- Giveaway check (2026-11-01): the check stage asks Jev whether a clue contains its answer's words or a
  direct translation. A first wording flagged 209 of 383 (it read "easy" as "gives it away"); the narrower
  wording flags 49, mostly real ("named after the Italian city of Taranto" for tarantula, "an aircraft with
  rotating blades" for helicopter parents), a few not (sheep, kilt). Flagged clues are dropped; the supply
  allows it. The sample leaves 330 clues in the bank file (`bank.jsonl`: our clues, answers and tags only).
- Boards are built from single clues, not the show's columns: each clue has one of our 14 broad categories,
  a specific topic in both languages ("Ríos del mundo", "Películas de los 80"; added 2026-11-01) and a
  difficulty. A game picks 5 columns, a topic when it has four good clues of different difficulty and the
  broad category otherwise, and orders each column's clues by the writer's rating and the original value
  (later by how often players get them right). Dropping a clue never breaks a board. Topics are free text, so
  similar ones ("earthquakes", "earthquakes and seismology") are merged in a pass after the run.
- Full run started 2026-11-01 (stopped after 59,292 tags to add topics, then restarted reusing them): the whole archive tagged, the best 12,000 by appeal rewritten (at most two
  clues per answer), into `~/.local/share/reachy/game-data/trivia-bank/` (source CSV in
  `game-data/sources/`, off the repo). The laptop's disk filled up mid-run (other projects), so the run moved
  to the Mac mini with all 216,930 tags and the first 4,905 rewrites; it runs there detached, from
  `game-data/trivia_bank.py`. Expected cost about $25. Difficulty from the clue's value and the
  writer's rating now, calibrated later by how often players get each clue right.
- Full run result (finished on the Mac mini 2026-11-01): Jev said yes to wordplay 48,615, US-local 42,481,
  broadcast picture 12,763, out of date 4,250 (adult 17,116, kids 41,487); 92,437 under appeal 2.0; 80,155
  passed and 33,597 of them have appeal at least 2.5. The best 12,000 by appeal (two per answer at most)
  were rewritten: the writer refused 49, 13 came back malformed, 11,938 kept (11,922 in Spanish, 5,587 with a
  kids version). The blind check agreed on 11,865 English and 11,763 Spanish; 1,413 give the answer away.
  **10,377 clues are in the bank** (`game-data/trivia-bank/bank.jsonl` on the mini), 1,248 of them adult-only
  and 4,786 with a kids version. Topics: 4,242 different, 591 with at least four clues (491 of them with
  mixed difficulty); most common Greek mythology 126, human anatomy 119, classic literature 110.
  Cost about $22 (tags about $9, rewrites about $12, checks about $1; estimated from the samples' per-request
  costs, since the run was restarted twice and each restart only counts its own requests).
- Still to do on the bank: the best-by-appeal pick is easy (difficulty 7,659 one-star, 4,151 two-star, 128
  three-star, none four-star), so harder rows need a second batch from the 33,597 good clues weighted toward
  high values; merge near-duplicate topics; strip Markdown the writer sometimes adds to titles
  (*El silencio de los inocentes*) before speech and the screen.
- Supplement: the Open Trivia Database (CC BY-SA 4.0, about 4,000 verified, English, mostly multiple choice)
  rewritten into open questions; Spanish and Venezuelan categories written directly.

### Surveys (Sheeple)

- Source: Eduardo's Google Sheet of survey answers from a TV show (ID kept out of the repo), 10 tabs:

  | Tab | Questions | Contents |
  |-----|-----------|----------|
  | 3, 4, 5, 6, 7 Answers | 3,919 | 3–7 answers with counts out of about 100 |
  | Fast Money | 803 | 2 answers with counts |
  | No Points 3, 4, 5 | 1,303 | ranked answers without counts |
  | Sheet1 | 20 | mixed layout |

- Use the 3,919 with counts; the 803 two-answer ones for a quick bonus round of ours; leave the 1,303 without
  counts for later (home surveys could give them counts).
- Cleanup: questions are in Title Case with typos ("Brag" spelt "Brad"). Keep the original text next to the
  cleaned one; fix only clear typos; leave anything that may be deliberate (wordplay, slang, a brand, an
  answer as a respondent gave it, e.g. "Nite") and flag it for Eduardo. Rewrite questions in our own words in
  both languages; answers and counts stay. Jev tags US-only questions (Thanksgiving, "we asked 100 moms"),
  adult-only content (kept for adults, out of kids mode) and kids-answerable ones.
- Never a model's guess shown as a survey result.

## Assumptions

- The local speech engine on the Mac mini is good enough for single short answers in either language.
- Guests' phones can reach the Mac mini on the home Wi-Fi (a guest network with client isolation would block
  it; to check on the real network).
- The e-ink screen is near Richie and readable from the table.

## Implementation order (proposed)

1. Question data pipeline (offline, can run in parallel): survey cleanup and tags; trivia filter, tags and
   rewrite, starting with a 1,000-clue sample to measure cost and quality.
2. Turn mode and the vision guesser; Scribble Rush without phones (word on the screen, Richie turns away).
3. Phone joining and buzzers; Scribble Rush with phones.
4. Big Brain.
5. Sheeple, then home surveys.
6. Detectives (adult case first, then the kids case).

Each step gets its own milestone in this plan and a feature doc when built.

## Acceptance criteria

- Scribble Rush: a 4-player game runs end to end in 20–40 minutes; Richie guesses most adult drawings of
  list words within three tries (measured on real drawings before tuning the lists).
- Phones: joining by QR in under 30 seconds; the earlier of two buzzes 100 ms apart always wins; the page
  works only during a game.
- Big Brain and Sheeple: a full game with no question shown twice in a month; answers judged right in either
  language; kids mode shows no adult-tagged question.
- Detectives: no contradiction with the case facts in a full playthrough; the reveal matches the stored
  truth.
- Every game: one voice at a time (Richie never takes an answer from anyone but the named player), pause and
  quit as on the host, everything restored afterwards.

## Risks

- **Rights.** The two sources are a show's archive. Mitigation: use them as facts, rewrite every question in
  our own words, keep raw files off the repo and never publish the banks.
- **Vision guesses** on rushed drawings may be poor; measure early with real drawings.
- **Noise.** Party rooms are loud; the buzz-then-speak rule and typing on the phone are the fallback.
- **Guest Wi-Fi isolation** could stop phones reaching the Mac mini.
- **Cost of tagging** about 150,000 clues with Jev; measured on a sample first.
- **Story consistency** in Detectives; mitigated by facts kept in code.

## Test strategy

- Unit tests per game on the host with the existing fakes (`tests/games/fakes.py`): turns, scoring, the
  one-voice rule, pause and quit, both languages.
- Data pipeline tests on small fixtures: filters, tags, the "keep deliberate spelling" rule.
- Phone page tests: joining, buzz order, early-buzz lockout, the link dying after the game.
- Live checks on the Mac mini with the family before each game is called done.

## Documentation updates

- A feature doc per game in `docs/features/` when built, and updates to `docs/features/games.md`, the README
  tool table and the admin app doc (Games panel) as they land.

## Not picked from the brainstorms

Kept for the record: peekaboo, emotion charades, picture reveal, I spy, think-of-an-animal, red light green
light, freeze dance, word bomb, rock paper scissors, spin the bottle, never have I ever, the impostor,
werewolves, choose-your-adventure, silly stories, dominoes with Richie as a player, a forbidden-words clue
game, a living-room escape room, map conquest and a co-op island adventure.

## Progress log

- 2026-11-01: designed in conversation (names, the one-voice rule, bilingual, adults by default with a kids
  filter, phones as buzzers without microphones); looked at both data sources and counted them. Nothing
  built.
- 2026-11-01: `scripts/trivia_bank.py` run on 1,000 random trivia clues (results under "Trivia" above):
  356 bilingual questions for $0.39; full-run cut-offs proposed, waiting on Eduardo's read of the examples.
- 2026-11-01: Big Brain decisions: statement clues (no "who is" phrasing), answers in both languages always
  count, predictable judging with a room override, an ElevenLabs host voice with line variations and banter,
  our own sounds and music, the board on the e-ink and a mirror on the phones. Second sample run with the
  new prompts (383 clues for $0.46).
- 2026-11-01: giveaway check and topics added; the full run moved to the Mac mini after the laptop's disk
  filled, and finished with 10,377 clues in the bank for about $22.
