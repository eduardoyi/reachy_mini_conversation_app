# Plan: Party games (Scribble Rush, Big Brain, Sheeple, Detectives)

- Status: `in-progress` (designed 2026-10-31 to 2026-11-01; clue bank and host clips made; Big Brain being built)
- Owner: Eduardo
- Created: 2026-11-01
- Last updated: 2026-11-02
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
  players' names where it fits, with audio tags for laughs and excitement.
- **Banter now and then**: about one event in four, a text model writes a short line about the moment (a
  streak, a tight score, a player's name), spoken the same way; about three seconds a line, so it never holds
  up the game.
- **Clue audio** is generated for the whole board while players join and cached per clue for later games.
- **Our own sound and music**, generated with ElevenLabs sound effects (and its music model if our plan has
  it) and used at the same kinds of moments as a TV quiz, never imitating a show's theme: an intro theme, the
  board reveal, picking a square, a buzz sound per team, right, wrong, time up, a hidden-square sting,
  30 seconds of thinking music for the final round, a drumroll for the reveal, a winner's fanfare and an
  outro. Several takes each; Eduardo picks.
- **Chosen voice (2026-11-01): "C, announcer", take 2** (a booming, smooth vintage-TV announcer, grand
  and dramatic), designed with `eleven_ttv_v3` and saved to the account as "Richie Big Brain host"
  (voice id `PluKrefK4JWrlyoTgZkd`). The four options and their previews are kept on the mini in
  `game-data/host-voice/`.
- **Audio tags in every generated line** (Eduardo, 2026-11-01; ElevenLabs' tag list:
  https://elevenlabs.io/blog/elevenlabs-audio-tags-list). Tags go in square brackets right before the words
  they shape, last until the next tag, combine with commas inside one bracket, and work with Eleven v4
  (`eleven_v4`, which handles them most consistently), v4 Turbo (for live lines) and v3. Groups: high energy
  (excited, playful, amazed, proud), delivery (booming, whispers, hushed, shouts), pacing (pause, long pause,
  drawn out, snappy, slowly), reactions (laughs, sighs, gasps, clears throat), plus free-form ones
  ("[mock disappointment]"). One tag per change of mood; dashes, ellipses and capitals shape timing too.
  Examples: "[booming] Welcome to Big Brain!", "[excited] Correct!", "[playful] Ooh… [sighs] so close.",
  "[hushed] Final round… [long pause] place your bets." Clue readings get light tags (a "[curious]" or a
  "[pause]" before the last detail) so the clue stays clear. Banter written by the text model may only use
  square-bracket tags from an allowed list, and the screen and phones show text with the tags stripped.
- **ElevenLabs limits**: Eduardo moved from Starter (3 requests at a time, 30,000 credits a month) to Creator
  (121,000 credits a month, 5 requests at a time) on 2026-11-01, so a board's clue audio is made five at a
  time while players join.
  Credits needed: about 30,000 once for the fixed lines (about 18 moments × 10 takes × 2 languages), about
  9,000 for sounds (45 takes at 200 each) and about 7,000 for music (7.5 minutes at 900 a minute); then about
  4,500 characters a game in one language (26 clues, the answers, some banter); clue audio is saved after its
  first use.
- **Speech model: Eleven v4 Turbo (`eleven_v4_turbo`) for everything** (Eduardo, 2026-11-01, after hearing
  the same lines on v4, v4 Turbo and Flash). It reads audio tags like v4 and none were read aloud, costs half
  (0.5 credit a character, as Flash does), and took about 3 seconds a line where v4 took 6 to 11. So a game
  costs about 2,250 credits, and Creator covers the one-time lines, sounds and music plus about 45 games a
  month.
- **Fixed lines made (2026-11-01)**: `games/big_brain_lines.py` holds 41 moments and 334 takes (welcome,
  joining, the board, picking, answering, right on a streak or a hard clue, wrong, anyone else, time up, the
  answer was, more specific, overruled, double or nothing, steal, everyone answers, lead change, comeback,
  scores, the board done, the final round, bets, thinking, the reveal, the winner, a tie, goodbye, pause,
  resume, and each team color's buzz and win), written for the announcer voice with audio tags from a fixed
  list and in Venezuelan Spanish ("Qué cráneo", "¡Epa!", "Qué sobrado"). `scripts/big_brain_voice.py` made
  all 334 clips with v4 Turbo (16,030 characters, none failed) into `game-data/big-brain/voice/<moment>/`
  on the mini, with a manifest of each clip's text so only changed lines are remade, and Whisper checked
  every clip: 6 flagged, all one- or two-word clips Whisper mishears ("Era…" as "Error", "Red!" as
  "Revved") plus one false alarm, now fixed (a tag word that is part of the line). Eduardo to listen to the
  flagged five.
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
  a hit scores its count for that team, a miss passes the turn, until the board is cleared or three misses
  (six until 2026-11-02: too long).
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
- The best-by-appeal pick was easy (difficulty 7,659 one-star, 4,151 two-star, 128 three-star, none
  four-star), so a second batch picked hard ones (`--pick hard`, 2026-11-01, on the mini, $16.38): Jev
  scored the hardness (0 everyone knows to 4 only buffs) of all 80,155 passing clues, and the 12,000 hardest
  with appeal at least 2.2 not yet rewritten were rewritten and checked (two per answer at most, counting the
  first batch). **The bank now holds 20,499 clues** (2,355 adult-only, 5,559 with a kids version; the first
  batch is kept as `bank-first-batch.jsonl`). Hardness quartiles 1.21 / 1.99 / 2.36, 95th percentile 2.75.
  Using those quartiles as a board's four rows, all 14 categories fill every row many times over, and 379
  topics fill a column with at least one clue per row (90 with three or more choices per row), before
  merging near-duplicate topics. Hard examples: Verdi's *Il trovatore* from its anvil chorus, Francis I
  inviting Leonardo, Emma Thompson's acting and writing Oscars. Markdown the writer added to titles is now
  stripped in the bank file.
- Topics merged into columns (2026-11-01, `scripts/trivia_topics.py`): every topic name embedded
  (`text-embedding-3-small`); from the most common down, each joins the most similar column at 0.85 cosine
  or starts its own. At 0.75 it made wrong merges ("south american cities" into "south american nations"),
  and 0.85 gave as many full columns as 0.8. Result: 6,279 topics into 5,517 columns, 1,161 with at least four
  clues (Greek mythology 213, World War II 186, classic literature 181, human anatomy 148, 1980s movies 129).
  Each bank entry has `column` ({"en", "es"}); the bank before the merge is kept as
  `bank.before-columns.jsonl`.
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

## Big Brain build (approved by Eduardo 2026-11-01)

The first party game to build, music and sounds included. Mechanics as agreed (in plain words):

- **Join:** "Richie, juguemos Big Brain", then "sí". The TRMNL shows a QR code, a four-letter code and the
  teams; each phone scans it, types a name and picks red, blue, green or yellow, and becomes a buzzer. Intro
  music and the welcome line play.
- **A turn (20 squares):** the TRMNL shows the board: five columns (themed topics), four rows worth
  **200, 400, 600 and 800 points** (Eduardo, 2026-11-01: points like a TV quiz, no money). The team in control
  taps a square on a phone (the board is mirrored there). The TRMNL shows the clue in large type while Richie
  reads it; then the buzzers unlock. The first buzz wins (its phone lights in the team color, Richie calls the
  team); only that player answers aloud, and Richie listens only to them. Right: the team gets the points and
  picks next. Wrong: it loses them (adults; kids mode loses nothing) and the other teams may buzz. Nobody:
  Richie says the answer, and the square is crossed out.
- **Hidden squares (three, never in the 200 row):** double or nothing; steal (a right answer takes the
  square's points from the leading other team); everyone (each phone types an answer; every right one scores
  for its team).
- **Fairness:** after a wrong ruling, phones show "¡Eso vale!" until the next square is picked; a majority of
  players makes it count. The clue text also appears on the phones once read.
- **Final round:** the TRMNL shows the category; each team bets in secret on a phone (up to its score, or up
  to 800 when it has less); Richie reads the clue; 30 seconds of thinking music while each team types one
  answer; the reveal goes team by team from the lowest score, with a drumroll; the winner gets the fanfare,
  a dance and the TRMNL.
- **Around it:** our own sounds and music; banter now and then; Spanish or English with answers in either;
  kids mode (easier clues, kids versions, no lost points); "pausa" or an antenna pauses; about 30–40 minutes.

### How it is built

| Piece | Where | What |
|-------|-------|------|
| Prerecorded speech | `hybrid_runtime.py` | `request_announcement(..., audio=(rate, samples))` plays a clip through the same announcement path as a spoken line (waits until Reachy is free, no wake from his own voice, `on_spoken`, then an awaited answer if asked), so a host clip followed by "listen to the buzzer" works like a question |
| Clips and sounds | `games/big_brain_audio.py` | Host clips (`game-data/big-brain/voice/<moment>/<lang>-NN.wav`, 24 kHz mono, converted from the ElevenLabs mp3s), never the same take twice in a row; sounds and music (`game-data/big-brain/sounds/`); live speech for clues, answers and banter with Eleven v4 Turbo as raw 24 kHz PCM, cached by text on disk |
| Board | `games/big_brain_board.py` | Loads `trivia-bank/bank.jsonl`; picks five columns (a merged topic with a clue in each hardness quarter, else a broad category), four clues each ordered by hardness, three hidden squares, a final clue (hardness ≥ the 75th percentile); filters adult clues out of kids mode and prefers kids versions; avoids clues used in the last 30 days (`big-brain/history.json`) |
| Judge | `games/big_brain_judge.py` | Code first (normalize case, accents, articles and punctuation; any accepted form; close spelling or hearing slips; a surname alone), then Jev with the fixed rules (right, wrong, or too broad → "¿más específico?" once); every ruling stored per clue and answer (`big-brain/rulings.json`) so the same answer always gets the same ruling |
| Phones | `games/big_brain_phones.py` + `static/big_brain/` | A FastAPI app on the home Wi-Fi (`0.0.0.0:7880`), only while a Big Brain game runs; every request needs the game's four-letter code; one WebSocket per phone; the phone page renders whatever state the server sends |
| Screens | `eink_display.py` | `render_big_brain_join`, `_board`, `_clue`, `_final`, `_reveal`, `_winner` (800×480, four greys) |
| Game | `games/big_brain.py` | The rounds above on the shared game host (pause, quit, idle end, "¿lo terminamos?" all inherited) |
| Wiring | `catalog.py`, `main.py`, admin Games panel | Names ("Big Brain", "juguemos Big Brain", "el big brain", mishearings); enabled when the bank file and the ElevenLabs key exist (no camera needed) |
| Making sounds | `scripts/big_brain_sounds.py` | ElevenLabs sound effects (200 credits each) and music (900 a minute); several takes each, the first used until Eduardo picks |

Phone protocol (JSON over `/ws?code=ABCD`): the phone sends `join` {name, team, language, player}, `buzz`,
`pick` {column, row}, `bet` {points}, `answer` {text}, `vote` {}; the server answers every change with a
`state` snapshot for that phone (phase, teams and scores, the board, who is in control, the clue once read,
who buzzed, deadlines, what this phone may do now, the vote) and `welcome` {player} on joining, which the
phone keeps so a reload rejoins.

Deferred: team colors on the room lights (lights cannot report their color, so they cannot be put back, as
for the color hunt), the phone microphone, a game without phones.

No new dependency: a QR library would have meant re-resolving the lock, which failed on 2026-11-01 on an
unrelated package's build, so `qr_code.py` encodes the join link itself (checked with macOS's QR reader).

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
- 2026-11-01: harder batch (Jev hardness scores, the 12,000 hardest enjoyable clues) on the mini: 20,499
  clues in the bank, every category able to fill four rows. Host voice chosen (C, take 2) and saved; audio
  tags made a rule; Eduardo moved ElevenLabs to the Creator plan; v4, v4 Turbo and Flash compared on the same
  lines, waiting on his ear.
- 2026-11-01: v4 Turbo chosen; the host's 334 fixed clips made and checked; trivia topics merged into
  columns (1,161 can fill a column).
- 2026-11-01: Big Brain built (game, board, judge, audio, phones' server and page, TRMNL screens, QR code,
  recorded speech through announcements, catalog and wiring); 18 sounds and music pieces made (three takes
  each, the first chosen); host clips converted to WAV; feature doc [big-brain.md](../features/big-brain.md).
  Points are 200/400/600/800 (Eduardo, mid-build). Full gate 2,928 passed. Not yet deployed or played.
- 2026-11-01: deployed to the Mac mini (b0877d8, restarted 13:45): "Games enabled: colors, treasure, simon,
  bigbrain", no errors; clue bank, host clips, sounds and the ElevenLabs key in place. A QR library was tried on
  the mini too and fails the same way (pygobject needs cairo to re-lock), so the encoder stays. Not played yet.
- 2026-11-01: Scribble Rush first version built on its own branch (feature doc
  [scribble-rush.md](../features/scribble-rush.md)): phones join by QR on Big Brain's phone server, two or three
  teams race 24 squares over six rounds, one Gemini Flash call per drawing gives up to three guesses (least to most
  confident) that code and Jev judge; 139 kids' and 238 adults' words in both languages. Square types, Richie
  drawing, telephone, the no-phone fallback and saved drawings are deferred. Tested with fakes only; the vision
  guesses still need measuring on real drawings.
- 2026-11-01: Sheeple first version built (flock and team rounds, six boards with the last one double, kids filter,
  phones page, TRMNL board, judging by code then Jev); feature doc [sheeple.md](../features/sheeple.md). Plays once
  `sheeple/bank.jsonl` is in `GAME_DATA_DIR`. Not yet deployed or played.
- 2026-11-01: Detectives built (first version, without waiting for approval, as Eduardo asked): pre-written
  cases instead of a case written at game time (two adult dinner-party cases and "¿Quién se comió las galletas?"),
  phones for turns, roles, clues and the secret accusation, Gemini text for witness lines held to one character's
  sheet; feature doc [detectives.md](../features/detectives.md). Searching the house with the camera, dim lights
  and noir music are not built. Not deployed or played.
- 2026-11-02: Detectives' characters speak in their own voices (Eduardo's ask). Open-source voices on the mini
  were tried first and dropped: Qwen3-TTS, VoxCPM2 and Chatterbox took 1.5–3.5 times as long as the audio, and
  Kokoro, the only fast one, sounded poor. ElevenLabs instead: 15 voices designed from per-character descriptions
  (`scripts/detectives_voices.py`, ids in the case files), answers voiced live on Eleven v4 Turbo with audio tags
  from a fixed list, first sentence first (about 3–4 s from the question to the voice), Richie still narrating.
- 2026-11-02: After the household played Sheeple and Scribble Rush: Sheeple's team boards end at three misses (six
  dragged), and a beep opens each spoken answer, the speaker's phone saying "talk" only then (they spoke over
  the name). Big Brain and Detectives got the same beep and "talk now" phone (the same problem in Big Brain;
  Detectives' phone said "Richie is listening" while he was still asking). Big Brain's "¡Correcto!" card clears at
  the next pick and before the final, and an empty board no longer asks for a pick. Scribble Rush shows the
  category as a big label on the TRMNL and the phones. Not yet played again.
