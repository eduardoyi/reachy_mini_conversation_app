# Plan: La gallina loca (a hot-potato game for the kids)

- Status: `in-progress` (designed 2026-11-03 with Eduardo; being built)
- Owner: Eduardo
- Created: 2026-11-03
- Last updated: 2026-11-03 (redone after the first game with the kids)
- Builds on: [Games on Reachy](games.md) (the game host, the kids' recorded voice, sounds and music, "¿Otra vez?")

## Goal

A fourth kids' game for children of 2 to 6, in the hot-potato style of games like Hurry Up Chicken Butt but our
own: our name, our challenges, our sounds. Richie is a crazy hen with a secret timer. He calls a child by name
and gives them something silly to do; when they are done they pat his head or shout "¡listo!", and he calls the
next one. The music speeds up, and when the hen goes off ("¡COCOROCÓ!"), whoever's turn it is gets a silly
forfeit. Nobody loses. Then "¿Otra vez?", like the other kids' games.

## Decisions (Eduardo, 2026-11-03)

- **Name:** "La gallina loca" / "Crazy hen". "Hurry up chicken" was too close to the original. The chicken
  theme gives us clucks and squawks, which are funny for small kids.
- **Nothing is passed around.** Richie calls turns by name, with no face recognition: each child says their
  name at the start ("¿Quién juega?"), and he matches what he hears against the family names he already knows
  (the face-recognition list, used only as a word list; the camera is not used). A name he doesn't know is
  taken as heard. With no names, he says "¡Siguiente!".
- **A turn passes** on a head pat or "¡listo!" (or "¡ya!", "¡ya está!"), heard without his name. Children are
  trusted to do the challenge; nothing is checked with the camera.
- **The ending:** a silly forfeit for whoever's turn it is when time runs out. Nobody loses.

## Decisions after the first game (Eduardo, 2026-11-03)

Eduardo found it "kinda lame": random turns left some children out, nobody knew what "penitencia" meant, and the
challenges were not cool or funny enough. Hurry Up Chicken Butt's rules (a shaken chicken picks one of three
practised activities, the chicken goes to the player on the left, a run to touch the Chicken Butt and back, and
whoever holds it when it yells loses) showed what was missing: everyone in turn, quick repeatable silliness, and
a clear ending.

- **Turns in order:** the order the names were said, announced at the start; the next round starts with the
  child after the one caught.
- **Eggs:** whoever has the hen when it goes off lays an egg (a silly hen move) and gets an egg by their name on
  the screen; at the end, the most eggs is "la gallina de oro". Chosen over "becomes the hen" and a clearer
  forfeit.
- **Mild butt and fart jokes are fine** (shake your chicken butt, an armpit toot, a pretend burp).
- **New challenges:** short and silly (moves, voices, quick thinking, runs, copying Richie's own moves); Simón
  dice's still poses are gone.

## How a game goes

1. "Juguemos a la gallina loca" (or "la gallina", "crazy hen"; "papa caliente" and "hot potato" too), then the
   usual yes. A jingle, and Richie explains the game in one breath.
2. **Names:** "¿Quién juega? Díganme sus nombres." Each answer can hold one or several names ("Enzo y Sofía");
   Richie greets each one live ("¡Hola, Enzo!") and asks "¿Quién más?" until a "ya", a "nadie más", silence, or
   six names. An adult can say the names for a two-year-old. "¿Otra vez?" keeps the same names.
3. **The round:** a secret timer of 45 to 90 s of playing time, and a bouncy hen tune that switches to a faster
   one somewhere between 55% and 75% of it (when Richie also says the hen is getting crazy).
4. **A turn** (as redone after the first game): in the order the names were said, a cluck, the child's name and
   one of 30 quick, silly challenges (games/gallina_loca.py `CHALLENGES`), none repeated until all have been
   played, with its drawing, short words and the child's name on the screen. The turn ends on a pat or
   "¡listo!" (not in its first 1.5 s, or 4 s after one of Richie's moves to copy), sometimes with a quick
   "¡Bien!" before the faster tune, or after 20 s with "¡La gallina sigue!".
5. **The egg:** the timer runs out even mid-turn: a squawk, "¡COCOROCÓ!", the name of whoever has the hen, "¡Te
   toca poner un huevo!" and a way to lay it, with its drawing. A pat or "¡listo!" (or 15 s), a pop and a
   cheer, and everyone's eggs on the screen; then "¿Otra vez?". At the end, the golden hen.

## Build

- `games/gallina_loca.py`: `GallinaLoca(Game)`, the challenges and forfeits (with the drawing each needs), name
  matching (`heard_names`), the turn loop and the forfeit. Registered in `main.py` without the camera.
- `games/catalog.py`: `GALLINA_LOCA` (key `gallina`), added to `KIDS_GAMES`, so "¿Otra vez?" can switch to and
  from it.
- `games/kids_lines.py`: the hen's recorded lines (start, names, turns, hurry, the end, the forfeit cheer), one
  per challenge and forfeit, in the kids' voice ("Richie kids host"). Names are said live in the same voice
  (ElevenLabs, kept by text in `game-data/kids/speech`); without a key, Richie's everyday voice says them.
- `scripts/big_brain_sounds.py --set kids`: `cluck`, `squawk`, `egg_pop`, and music `intro_gallina`,
  `gallina`, `gallina_hurry` (original, not imitating any song).
- Drawings: each new challenge and forfeit is drawn once in the background, as Simón dice's poses were.

## Acceptance

- [x] Tests: name matching (known, misheard, unknown, several at once, "ya"), a whole game on the fake host
      (names, turns by pat and by "¡listo!", a turn timing out, the timer going off mid-turn, the forfeit,
      "¿Otra vez?" keeping the names), no names ("¡Siguiente!"), and "¿Otra vez?" switching to and from it.
- [ ] Lines, sounds and music recorded on the mini; drawings made.
- [ ] Played with the kids: pats and "¡listo!" heard over the music, names heard right, the length feels right.
- [ ] Played again after the redo: everyone gets turns, the egg is understood, the challenges get laughs, a
      "¡Copia a Richie!" move is not taken for a pat.

## Progress

- 2026-11-03: Designed with Eduardo (the decisions above) and built: `games/gallina_loca.py`, 88 new
  moments in `kids_lines.py`, sounds and music prompts, the catalog entry, `Game.holds_head` (pats only
  registered while the head is held, which the host did only while asking or paused). 20 tests. Lines, sounds and
  drawings not made yet; not played yet.
- 2026-11-03: Deployed; 362 lines recorded, sounds and music made and copied to the mini. Its Games-tab card
  added after Eduardo saw it on the party shelf with no picture: "Crazy Hen" on the kids' shelf with its art.
  AGENTS.md now says a new game ships with all its assets, and a test checks every game has a card and art.
- 2026-11-03: Redone after the first game (decisions above): turns in order with the order said and shown, 30
  new challenges (Simón dice's poses dropped), six ways to lay an egg, an egg count screen
  (`render_egg_count`), the golden hen, the rules said in kid words, fewer "¡Bien!"s. Lines, sounds and drawings
  for the new challenges not made yet; not played yet.
