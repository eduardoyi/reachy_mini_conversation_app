# Plan: Story mode (Richie tells classic stories the kids join in)

- Status: `in-progress` (deployed to the Mac mini 2026-11-07; not played with the kids yet)
- Owner: Eduardo
- Created: 2026-11-07
- Last updated: 2026-11-07
- Builds on: [Games on Reachy](games.md) (the game host, the kids' recorded voice, sounds and music, "¿Otra vez?"),
  [La gallina loca](la-gallina-loca.md) (pats and "¡listo!" during play)

## Goal

Richie tells classic folk tales (Los tres cerditos first) with designed character voices, sound effects and
music. The kids don't just sit and listen. Every 30 to 45 seconds the story asks them to do something: blow like
the wolf, knock on the door by patting his head, say the line everyone knows, or pick between two small things.
The story **never waits on them**: whatever they do, or don't do, it carries on.

Each story is a data file, so adding a story means writing a file and running a build. No new code. All the
audio is made once, ahead of time, so telling a story spends almost nothing.

## Decisions (Eduardo, 2026-11-07)

- **Languages: Spanish and Italian.** Spanish is Venezuelan like the games. Italian is standard Italian and only
  for stories: the other games stay es/en (quick commands already understand Italian). No English stories for
  now.
- **A new storyteller voice narrates**, not the kids-host voice the games use.
- **The wolf is silly**, not scary.
- **For both kids: the 2-year-old and the 4-year-old.** Short sentences and lots of repetition. Every interaction
  is something a 2-year-old can do (a noise, a pat, a move). Choices are mainly for the 4-year-old. Any try counts.
- **Daytime only.** Roaring, blowing and silly sounds are welcome. No calm bedtime version.
- **Small choices that rejoin the story.** A choice changes a detail (the house's color, what the pigs eat at the
  end), never the plot. It never blocks: after a short wait, or when the answer is random or unclear, Richie
  picks one himself and carries on.
- **Prototype: Los tres cerditos**, with every kind of interaction, played on the robot before more are written.
- **All audio pre-generated:** every line, in every language, with every choice's branch, plus "nobody answered"
  and "Richie picks" lines. Nothing is spoken live, because stories use no names.
- **ElevenLabs** for the voices (Eleven v4 with audio tags), the sound effects and the music.
- **Adapt public-domain folk tales in our own words.** No copying a particular book or film.

## How a story goes

1. **Start** in one of three ways:
   - by voice: "Richie, cuéntanos un cuento" / "Richie, raccontaci una storia" gets "¿Cuál? Puedo contar los tres
     cerditos." Naming one ("cuéntanos los tres cerditos", "i tre porcellini") goes straight to the usual yes;
   - from the admin app's new **Stories** shelf, where Play is the yes;
   - through Gemini's `play_game` (rules, then start), like the games.
2. A short jingle, then the narrator opens: "Había una vez…" / "C'era una volta…".
3. The story plays step by step. Each step is narration, a character line, a sound, music, a new drawing on the
   e-ink screen, or a head or antenna move. Every 30–45 s comes an interaction (below).
4. **Pause and quit** work as in the games: "ya va" / "aspetta", his name, an antenna bend, or End in the app. Pats
   don't pause a story: the head is held still the whole time so a pat can knock on a door, and pats outside a
   knock are ignored.
5. **The end:** "Colorín colorado, este cuento se ha acabado" / "E vissero felici e contenti", a short celebration,
   then "¿Otro cuento?". This is read loosely like the kids' games' "¿Otra vez?": yes plays it again, a story's
   name starts that one, no or silence ends.

## Interactions

Each one has a time limit, one "¡Más fuerte!" retry at most, and a pre-recorded way to carry on without the kids.
Nobody is ever told they got it wrong. Detection starts after Richie's own audio has finished, so he doesn't hear
himself.

| Kind | Example | How it's detected | If nothing happens |
|------|---------|-------------------|--------------------|
| **Make a noise** | "¡Ayúdame a soplar! Uno, dos, tres… ¡FUUU!", a roar, a knock-knock | Mic loudness clearly above the room's level for ≥ 0.3 s within 6 s; no speech recognition | One "¡Más fuerte!", then "¡Bueno, lo hago yo!" and the sound plays anyway |
| **Say the line together** | Wolf: "¡Cerdito, cerdito, déjame entrar!" Kids: "¡Ni por los pelos de mi barbita!" | Any voice within 6 s (voice activity, words not checked) | The pig says it, and the kids can join in |
| **Pat Richie** | "¡Toquen la puerta! Denle un golpecito a mi cabeza." | A head pat within 8 s (head held still, like the gallina) | A knock sound and the story goes on |
| **Pick one of two** | "¿De qué color pintamos la casa de ladrillos, roja o azul?" | 8 s of listening, heard by Scribe with the two options as keyterms (game answers may use Scribe) | Unclear, random, both, or silence: "¡Yo elijo… roja!" from Richie's pre-recorded lines, then the branch |
| **Copy a move** | "¡Escóndanse! Tápense los ojos." "Tiemblen como el cerdito." | Not checked: kids are trusted. A few seconds of music and a matching Richie move | Same: the story carries on |

The pick-one branch rejoins at once: only the next line or two differ, and a few lines later can mention the
choice ("la casa roja") as its own pre-recorded take.

## Story files

One folder per story under `src/reachy_mini_conversation_app/stories/<key>/`, with `story.yaml` and a cover
picture for the app. The cast's voices live in one shared `stories/cast.yaml`, so the wolf sounds the same in
every story he's in. ElevenLabs voices are multilingual, so one voice covers both languages.

```yaml
key: tres_cerditos
title: {es: Los tres cerditos, it: I tre porcellini}
names: {es: [tres cerditos, los cerditos, cerditos], it: [tre porcellini, porcellini]}
pitch: {es: "…", it: "…"}
cast: [narrator, wolf, pig_straw, pig_sticks, pig_bricks]
steps:
  - scene: {picture: "three little pigs waving goodbye to their mother outside a farmhouse", move: happy}
  - say: narrator
    es: "[warm] Había una vez tres cerditos que vivían con su mamá."
    it: "[warm] C'era una volta tre porcellini che vivevano con la loro mamma."
  - sound: door_knock
  - noise:
      ask: {es: "¡Ayúdame a soplar! Uno, dos, tres…", it: "Aiutami a soffiare! Uno, due, tre…"}
      done: {es: "¡Uy, qué fuerte soplaron!", it: "Wow, che soffio fortissimo!"}
      louder: {es: "¡Más fuerte!", it: "Più forte!"}
      without: {es: "¡Bueno, lo hago yo! ¡FUUU!", it: "Va bene, lo faccio io! FUUU!"}
  - choice:
      ask: {es: "¿Roja o azul?", it: "Rossa o blu?"}
      options:
        red: {words: {es: [roja, rojo], it: [rossa, rosso]}, then: {es: "¡Roja!…", it: "Rossa!…"}}
        blue: {words: {es: [azul], it: [blu, azzurra]}, then: {es: "¡Azul!…", it: "Blu!…"}}
      pick: {es: "¡Yo elijo…", it: "Scelgo io…"}
```

Steps can name the chosen option later (`{choice: house_color, red: "…", blue: "…"}`). Lines carry audio tags and
nothing shown on screen has them.

## Build (one-off audio, pictures and cover)

`scripts/story_build.py <key>` reads the story and makes whatever is missing:

- **Lines:** one ElevenLabs take per line per language on Eleven v4 Turbo, like the games (not 2–3 takes: a story is heard in order, so
  variety matters less), in the character's voice. Each clip is named by a hash of its text, tags and voice, so
  editing a line re-records only that line.
- **Sounds and music:** from a shared story sound list, made like `scripts/big_brain_sounds.py` and levelled the
  same way (sounds -24 dB RMS, music -33, voice about -20).
- **Scene drawings and the cover:** drawn by the image model on the mini the first time the story plays
  (`draw_pictures`, in the background, like the treasure drawings, about $0.03 each); until then the screen
  shows the title.
- **Output:** `game-data/stories/<key>/<lang>/…` with a manifest. The mp3s are converted with ffmpeg on the laptop
  (the mini has none), then copied to the mini.
- **Listening check:** `--play es` plays the whole story on the laptop with the no-answer path everywhere, so a
  bad take can be found and re-recorded (`--redo <step>`).

One-off cost for Los tres cerditos (`--cost`, 2026-11-07): 172 clips, 10,914 characters (about 10.9k credits) for
both languages and the shared lines, plus 17 sounds and 3 music beds (about 4.9k credits), and 9 drawings (~$0.30).
**Per play:** only the choice answers go to Scribe (well under a cent). Noise, voice and pats are all local.

## Runtime

- A `Story` game (`games/story.py`) plays any story file: the host gives it yes, pause, quit, screen and music
  handling, the head hold, and "¿Otro cuento?". Stories appear as one catalog entry per story file, read at
  startup, so a new folder shows up in the app and in voice starts with no code change.
- It reuses `KidsAudio`-style clip playback (`GameServices.say_audio`), the music bed, the e-ink picture screen
  (`render_game_picture`) and recorded moves (scared, happy, dance).
- A missing clip falls back to Richie's everyday voice saying the line, like the games.

### Italian for stories only

The other games stay es/en. Only what an Italian story needs while it plays:

- Italian titles, names and pitches for stories, and the yes / no / pause / resume / quit words in Italian ("sì",
  "dai", "va bene"; "aspetta", "pausa"; "basta", "abbiamo finito"), heard only while a story is offered or playing.
- An Italian button on the admin app's Stories shelf only (the Games tab picker keeps es/en).
- Scribe and the local speech model told to expect Italian while an Italian story plays.
- Voice starts in Italian ("raccontaci una storia", "raccontami i tre porcellini") in the quick-command grammar.

## Pictures on the TRMNL (decided 2026-11-07)

- **Paced by the story:** a picture at each important event, which can be as often as every 30 s. Los tres cerditos
  has 17 scenes, and the play and feast scenes each have two versions, one per choice. The screen flashes at every
  change, so in code a new picture waits until 20 s of story (`MIN_SCENE_GAP_S`) have passed since the last one and
  changes only between steps, never during an interaction. A picture still waiting when the next arrives is skipped.
- **Full screen, no title** (`story_pictures.screen_png`: cut to 5:3 from the 3:2 drawing, four greys).
- **Consistent characters** (OpenAI's image prompting guide):
  - each character has a `look` in `cast.yaml`, told apart by outfit and shape, not color;
  - each has a character sheet drawn once (`stories/cast/<role>.png`, in Git LFS), which Eduardo reviews;
  - every scene is an `images.edit` with the sheets of the characters in it as numbered references. The prompt
    restates each look and says not to redesign the characters.
  - One style for every story: a soft hand-drawn children's anime look (`STYLES["anime"]`, the default since
    2026-11-07), dithered to the four greys. The first picture-book style (`book`) is kept in the `-book` folders.
- **Made ahead of time:** `story_build.py --sheets`, then `--pictures`. The scenes go to `pictures/`, the screens to
  `screens/`, and `contact.png` shows them all as the TRMNL will. Nothing is drawn on the mini; the runtime
  `draw_pictures` path is no longer used by stories.

## Los tres cerditos (prototype outline)

About 6–7 minutes and 10 interactions. Written by a Claude Code agent from this outline, then read by Eduardo
before anything is recorded.

1. The pigs leave home ("¡Adiós, mamá!"). *Kids wave and say adiós* (voice).
2. Pig 1 builds with straw, quickly, to go and play. *Choice:* what does he play with, ball or kite?
3. The wolf arrives (a silly wolf, not scary: hungry and grumpy, a big tummy rumble). *Kids knock* (pat).
4. "¡Cerdito, cerdito, déjame entrar!" *Kids say the line* (voice).
5. "¡Soplaré y soplaré!" *Kids help blow* (noise). Straw flies away, and pig 1 runs to pig 2's house.
6. Pig 2 builds with sticks. *Copy a move:* shake like a scared pig.
7. Knock, the line, the blow again (pat, voice, noise). The sticks fall, and both run to pig 3.
8. Pig 3 builds with bricks. *Choice:* red or blue house. *Copy a move:* lift heavy bricks.
9. The wolf blows and blows (*kids blow as hard as they can*, noise), then gets dizzy. The house holds.
10. The wolf climbs onto the roof and slides down the chimney into a pot of **cold** water: "¡SPLASH!". He runs off
    and never comes back. *Kids roar a big "¡bravo!"* (noise).
11. *Choice:* party food, pizza or cake. A dance (Richie's dance move and celebration music).
12. "Colorín colorado…" and "¿Otro cuento?".

The softened ending (cold water, the wolf runs away) is on purpose for the 2-year-old.

## Next stories

Shipped after the prototype (2026-11-07): El nabo gigante, Ricitos de Oro, Los músicos de Bremen. Ideas for later:
Pinocchio in short episodes (Collodi's book, drawn from his descriptions), Los tres chivitos, El hombrecito de jengibre, La gallinita roja, La liebre y la tortuga, El león
y el ratón, Caperucita Roja (softened), Tío Tigre y Tío Conejo (Venezuelan).

## Implementation order

1. Story file format, loader and validation (every step in both languages, every choice with words and a pick
   line), with tests.
2. The `Story` game on the host: steps, the five interactions, timeouts and fallbacks, tested with fake audio,
   mic levels and transcripts.
3. Italian for stories (word sets, story names, Scribe and speech hints, Stories shelf picker, voice starts).
4. `cast.yaml` voices: design a storyteller narrator and the four characters (a silly wolf, three pigs). Eduardo
   picks from 2–3 options each, as he did with the kids' host voice, hearing each in Spanish and Italian.
5. Write Los tres cerditos (agent draft, Eduardo reads it), then `story_build.py` and a full listening check on
   the laptop.
6. Admin app Stories shelf (cards with cover art, es/it, play, pause, end).
7. Deploy to the mini and play it with the kids.

## Acceptance

- Los tres cerditos plays start to finish in Spanish and in Italian on the robot, from the app and by voice.
- With nobody answering at all, the story still finishes, with no step waiting more than 8 s plus one retry.
- On a real play, most noise, voice and pat moments are caught (no false "lo hago yo" when the kids clearly did
  it), and a choice said clearly takes its branch.
- Pause, resume, quit and "¿Otro cuento?" work as in the games.
- A second story can be added by writing a folder and running the build, with no code change.
- Per play, only Scribe is billed (choices); nothing else goes to the cloud.

## Progress

- 2026-11-07: designed with Eduardo; then settled standard Italian for stories only, a new storyteller voice and a
  silly wolf.
- 2026-11-07: built and tested on the laptop:
  - `games/story_file.py` reads and checks story files (`stories/<key>/story.yaml`, `cast.yaml`, `sounds.yaml`,
    `common.yaml`). A line about a choice is `say:` with `about: <choice id>`.
  - `games/story.py` (`StoryGame`) tells a story with the five interactions and their fallbacks. Noise and chorus
    answers stay on the local speech model: `say_audio(..., cloud=False)`, which became
    `request_announcement(cloud_answer=False)`.
  - Stories are catalog entries (`GameInfo.kind="story"`, `languages=("es", "it")`). The host offers, pauses and
    ends them with story wording, and has its lines in Italian. The Italian yes, no, pause, resume and quit words
    are in the shared word sets. Voice starts: "cuéntanos (el cuento de) los tres cerditos", "cuéntanos un cuento"
    (asks which), "raccontaci i tre porcellini", "raccontami una storia". Gemini's `play_game` takes `it`.
  - The admin app has a Stories shelf with an Español/Italiano picker and the card art. The control API accepts
    `it`.
  - `scripts/story_build.py` handles voice design (`--design`, `--save`), `--cost`, recording, sounds and a
    `--play LANG` listening preview.
  - Los tres cerditos: 71 steps, 13 interactions, about 5,600 characters per language.
  - Tests: `tests/games/test_story.py` (22). The full suite passes, and ruff and mypy are clean.
- 2026-11-07: voice previews made for all six roles (`~/.local/share/reachy/story-voice-previews/`). ElevenLabs'
  voice design blocks child-like voice descriptions, so the pigs are described as adult voice actors doing cartoon
  voices. Next: Eduardo picks the voices, then record (`story_build.py tres_cerditos`), listen (`--play es` and
  `--play it`), copy `game-data/stories` to the mini, deploy, and play with the kids.
- 2026-11-07: voices picked and Los tres cerditos recorded.
  - Eduardo asked for a friendly male narrator in the style of option 3. Option 3 was saved and remixed into three
    male versions, and he was happy with any of them, so the narrator is male version 1. Every other role uses
    option 1. The voice ids are in `cast.yaml`.
  - The temporary base voice was deleted to free a slot: the ElevenLabs Creator plan holds 30 voices, and it was
    full.
  - `story_build.py` recorded 172 clips and made 17 sounds and 3 music beds (about 16k credits). The music endpoint
    allows two requests at a time, so sound and music requests now retry on 429.
  - Whisper heard every clip as written; the only differences were spellings of "toc toc" and "fuuu", and no tag
    was read aloud.
  - The full listening previews (`--play es`, `--play it`) are 8 minutes each with every pause left for answers.
  - Next: copy `~/.local/share/reachy/game-data/stories` to the mini, deploy the code, and play it with the kids.
- 2026-11-07: pictures. Scenes now have an `id`, the characters in them (`with:`) and, with `about:`, a picture per
  option. The story game shows the prepared screens full-screen with the 20 s gap. `story_build.py` has `--sheets`
  and `--pictures` on gpt-image-2.6-flare (falling back to 2.5-flare). Five character sheets were drawn; the wolf
  and the straw pig were redrawn because their first versions had a black vignette background, and the sheet
  prompt now asks for pure white. Waiting for Eduardo to approve the sheets before the 19 scenes are drawn.
  Tests: 24 in `test_story.py` (pictures wait for the gap, prompts restate looks, the screen is 800x480).
- 2026-11-07: all 19 scenes drawn on gpt-image-2.6-flare from the approved sheets. The characters stay consistent
  across every scene. The first screens had speckled light-grey fills: the drawings' fills carry a faint texture
  near the edge between two greys. `screen_png` now averages each fill over itself, leaving out outlines and dark
  fills, then snaps it to the four greys, so fills are solid and outlines stay sharp. Eduardo has the contact
  sheet; waiting for his review.
- 2026-11-07: Eduardo asked for the whole set in a children's anime style, "sort of like Ghibli". It is described
  in our own words, without the studio's name: soft hand-drawn animated film look, big shining eyes, cozy
  countryside. The two styles are `story_pictures.STYLES` (`book`, the default, and `anime`), built with
  `--style anime` into `stories/cast-anime/`, `pictures-anime/`, `screens-anime/` and `contact-anime.png`.
  Snapped to four flat greys, the anime shading turned to mud, so anime screens are dithered like photos
  (`DITHERED`). How dithering looks on the actual TRMNL panel is not checked yet. Both sets were sent to Eduardo to
  pick; the runtime shows `screens/`, so the chosen style becomes the default.
- 2026-11-07: Eduardo picked the anime set and made it the default for all future stories: `DEFAULT_STYLE =
  "anime"`. Its sheets are now `stories/cast/`, its scenes `pictures/` and `screens/`; the picture-book set moved to
  `stories/cast-book/`, `pictures-book/`, `screens-book/` and `contact-book.png`. A build with the default style
  redraws nothing.
- 2026-11-07: deployed (`4bdd8be`). The full suite passed (3,223), and ruff and mypy were clean. The 156 MB of
  game-data/stories was copied to the mini (172 clips, 19 screens, 20 sounds), the code pulled, and the app
  restarted (SIGINT, then `launchctl kickstart`). The log lists `tres_cerditos` under "Games enabled", and the
  control API's `/status` shows it as a story in es and it. Not checked yet: a play on the robot (the mic catching
  blows, pats, choices), and how the dithered pictures look on the TRMNL.

- 2026-11-07: three more stories, built straight to final at Eduardo's request (no voice or sheet review):
  `nabo_gigante` (El nabo gigante / La rapa gigante, about 5 min, 12 moments), `ricitos_de_oro` (Ricitos de Oro /
  Riccioli d'oro, kind ending where she says sorry, 13 moments) and `musicos_de_bremen` (Los músicos de Bremen / I
  musicanti di Brema, silly robbers, 12 moments, mostly animal noises). ElevenLabs' 30 voice slots were full, so
  the 14 new roles in cast.yaml reuse voices already on the account (the kids' Detectives cast and two pig voices);
  each role's `description` names whose voice it is, and Lola and Goldilocks share one. 437 clips (about 27k
  credits) and 19 new sounds and music beds (`story_march`, `story_tiptoe`). Whisper heard every clip as written
  apart from animal-noise spellings; the Italian donkey's "Hi-ho" came out English, so it is "Ih-oh". 14 new anime
  sheets (one was blocked once by OpenAI's output filter and drew fine on retry) and 41 screens. Admin card art is
  each cover recoloured. Not checked yet: a play on the robot, and whether the mic hears brays and meows as noise.
- 2026-11-07: Eduardo spotted extra limbs (the donkey drumming with arms on top of four legs, the rooster with
  hands, a cat with an extra paw). Every scene prompt now says to keep each body exactly as on its sheet, with no
  extra limb, paw or hand; the donkey, dog, cat and rooster looks say how many legs and wings they have; and the
  Bremen scenes no longer ask the donkey to drum. All 40 scenes of the three new stories were redrawn and checked
  one by one, and the Bremen card was redone. The pigs' scenes were not redrawn.
