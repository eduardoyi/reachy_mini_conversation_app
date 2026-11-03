# Plan: La gallina loca (a hot-potato game for the kids)

- Status: `in-progress` (designed 2026-11-03 with Eduardo; being built)
- Owner: Eduardo
- Created: 2026-11-03
- Last updated: 2026-11-03
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

## How a game goes

1. "Juguemos a la gallina loca" (or "la gallina", "crazy hen"; "papa caliente" and "hot potato" too), then the
   usual yes. A jingle, and Richie explains the game in one breath.
2. **Names:** "¿Quién juega? Díganme sus nombres." Each answer can hold one or several names ("Enzo y Sofía");
   Richie greets each one live ("¡Hola, Enzo!") and asks "¿Quién más?" until a "ya", a "nadie más", silence, or
   six names. An adult can say the names for a two-year-old. "¿Otra vez?" keeps the same names.
3. **The round:** a secret timer of 45 to 90 s of playing time, and a bouncy hen tune that switches to a faster
   one somewhere between 55% and 75% of it (when Richie also says the hen is getting crazy).
4. **A turn:** a cluck, the child's name and a challenge: about 40% a Simón dice pose (already drawn and
   voiced), the rest something active (walk like a duck, flap your wings and cluck, hop like a bunny three
   times, run and touch the wall and come back...). The screen shows the drawing, the words and the child's
   name. Sometimes the hen says "¡Otra vez tú!" and the same child gets another one. The turn ends on a pat or
   "¡listo!" (not in its first 1.5 s, so the last child's pat doesn't count), with a quick "¡Bien!", or after
   20 s with "¡La gallina sigue!". A challenge doesn't repeat within a round, and the same child is never called
   twice in a row (except "¡Otra vez tú!").
5. **The end:** the timer runs out even mid-turn: a squawk, "¡COCOROCÓ!", "¡La gallina te tocó a ti, Enzo!"
   and a forfeit (do the hen dance, cluck three times, lay an egg: crouch down and say "¡pop!"...), with its
   drawing. A pat or "¡listo!" (or 15 s), a cheer, then "¿Otra vez?".

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

## Progress

- 2026-11-03: Designed with Eduardo (the decisions above) and built: `games/gallina_loca.py`, 88 new
  moments in `kids_lines.py`, sounds and music prompts, the catalog entry, `Game.holds_head` (pats only
  registered while the head is held, which the host did only while asking or paused). 20 tests. Lines, sounds and
  drawings not made yet; not played yet.
