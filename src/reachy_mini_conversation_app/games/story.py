"""Story mode: Richie tells a story from a story file, and the children join in (docs/plans/story-mode.md).

Every line is a recording made ahead of time (scripts/story_build.py), in the role's voice: the narrator, the
wolf, the pigs. Sounds play just before the line that follows them. Every 30 to 45 seconds the story asks the
children to do something, and it never waits on them for long:

- a noise (blow, roar, cheer) or a line everybody knows: anyone speaking within a few seconds counts, heard by the
  local speech model only (the words don't matter); nothing heard gets one "¡Más fuerte!" (noise only), then the
  story does it itself;
- a pat on Richie's head (knock on the door): the head is held still the whole story so a pat shows;
- a move to copy: Richie moves too, music plays for a few seconds, and the children are trusted;
- a choice between two small things: the answer goes to the cloud game model with the options as keyterms; when
  nobody picks, or the answer names neither or both, Richie picks one himself. Either way the story rejoins.

A line without a recording is said in Richie's everyday voice, so a story plays before (or without) its audio.

Pictures fill the e-ink screen, made ahead of time (story_pictures.py). The screen flashes at every change, so a new
picture waits until MIN_SCENE_GAP_S of story has passed since the last one, and only changes between steps, never
while the children are doing something; a picture still waiting when the next one comes is skipped.
"""

import time
import random
import asyncio
import logging
from typing import Final
from pathlib import Path
from collections.abc import Mapping, Sequence

from reachy_mini_conversation_app.games.base import Game, GameContext
from reachy_mini_conversation_app.eink_display import render_game_prompt
from reachy_mini_conversation_app.games.catalog import GAMES, STORIES, GameInfo, story_by_keyword
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.story_file import Line, Step, Story, Common, Option, Picture, clip_id
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, join, read_wav
from reachy_mini_conversation_app.games.big_brain_lines import shown


logger = logging.getLogger(__name__)

# How long the children have to start a noise or a chorus, and a choice's answer, after Richie's line.
NOISE_LISTEN_S: Final[float] = 5.0
CHOICE_LISTEN_S: Final[float] = 7.0
# On top of that, how long the answer may take to come back (recording, then the speech model).
ANSWER_MARGIN_S: Final[float] = 8.0
# How long a pat may take to come.
PAT_WAIT_S: Final[float] = 8.0
# A move to copy lasts this long when the story doesn't say.
COPY_S: Final[float] = 5.0
# How long "¿Otro cuento?" listens.
AGAIN_LISTEN_S: Final[float] = 8.0
# The least story time between two pictures: only enough that two back to back do not flash the screen twice. It
# was 20 s, and pictures then came half a minute after the lines they show (Eduardo, 2026-11-07); the TRMNL itself
# takes about 5 s to fetch and draw one.
MIN_SCENE_GAP_S: Final[float] = 5.0
# The screen gone this long without checking in has napped (a normal gap is under a minute): Richie asks for its
# back button, at most NAP_ASKS times a story and once per nap, and only if it was awake during the story.
NAP_ASK_S: Final[float] = 75.0
NAP_ASKS: Final[int] = 2
# Text that goes to the transcript for sounds played without a line.
SOUND_TEXT: Final[str] = "♪"

# "¿Otro cuento?": cues anywhere in the answer, the first of a yes and a no wins (like the kids' games).
# A plain yes means another story, offered by name; the same story needs "otra vez" (2026-11-10: "sí, otro
# cuento, el de carros" told Los tres cerditos again).
_AGAIN_YES: Final[tuple[str, ...]] = (
    "si", "sii", "dale", "vamos", "claro", "vale", "bueno", "listo", "quiero", "otro", "otra", "ese", "esa",
    "dai", "certo", "un altra", "unaltra", "un altro", "va bene", "voglio", "questa", "questo",
)  # fmt: skip
_AGAIN_SAME: Final[tuple[str, ...]] = (
    "otra vez", "de nuevo", "el mismo", "la misma", "ese mismo", "ese otra vez", "repite", "repitelo", "otra vez ese",
    "ancora", "di nuovo", "la stessa", "lo stesso", "ancora una volta", "rifallo",
)  # fmt: skip
# Picking one of the two offered: "el primero", "el segundo" (else by name).
_FIRST: Final[tuple[str, ...]] = ("el primero", "la primera", "primero", "primera", "il primo", "la prima", "primo")
_SECOND: Final[tuple[str, ...]] = (
    "el segundo", "la segunda", "segundo", "segunda", "el otro", "il secondo", "la seconda", "secondo", "laltro",
)  # fmt: skip
# Another story without saying which: Reachy offers two by name.
OFFER: Final[str] = "offer"
# When each story was last told here (monotonic time), so the two offered are the ones heard least lately.
_TOLD: dict[str, float] = {}
_AGAIN_NO: Final[tuple[str, ...]] = (
    "no", "ya no", "basta", "se acabo", "chao", "adios", "luego", "despues", "otro dia",
    "ciao", "dopo", "basta cosi", "abbiamo finito", "non voglio",
)  # fmt: skip


def _first_at(said: str, phrases: Sequence[str]) -> int | None:
    padded = f" {said} "
    found = [padded.find(f" {phrase} ") for phrase in phrases if phrase]
    return min((at for at in found if at >= 0), default=None)


def named_story(said: str, stories: Sequence[str] = STORIES) -> str | None:
    """Return the story ``said`` (normalized) names, by one of its names or else by its one keyword ("carros")."""
    named = [
        (at, key) for key in stories if key in GAMES and (at := _first_at(said, tuple(GAMES[key].names))) is not None
    ]
    if named:
        return min(named)[1]
    game = story_by_keyword(said, stories)
    return game.key if game is not None else None


def again_answer(text: str, playing: str, stories: Sequence[str] = STORIES) -> str | None:
    """Read the answer to "¿Otro cuento?": a story named, ``playing`` on "otra vez", OFFER on a plain yes, else None."""
    said = normalize(text)
    named = named_story(said, stories)
    if named is not None:
        return named
    yes, no = _first_at(said, _AGAIN_YES), _first_at(said, _AGAIN_NO)
    if _first_at(said, _AGAIN_SAME) is not None:  # "no, ¡otra vez!"
        return playing
    if yes is not None and (no is None or yes < no):
        return OFFER
    return None


def offer_answer(text: str, offered: Sequence[str], playing: str, stories: Sequence[str] = STORIES) -> str | None:
    """Read the answer to "¿A o B?": a story named, the first or second, ``playing`` on "otra vez", None on a no.

    Anything else (silence, a yes, words that name neither) takes the first offered.
    """
    said = normalize(text)
    named = named_story(said, stories)
    if named is not None:
        return named
    no = _first_at(said, _AGAIN_NO)
    if _first_at(said, _AGAIN_SAME) is not None:  # "no, ¡otra vez el mismo!"
        return playing
    second, first = _first_at(said, _SECOND), _first_at(said, _FIRST)
    if second is not None and (first is None or second < first):
        return offered[1]
    if first is not None:
        return offered[0]
    if no is not None and _first_at(said, _AGAIN_YES) is None:
        return None
    return offered[0]


def stories_to_offer(playing: str, rng: random.Random, stories: Sequence[str] = STORIES) -> list[str]:
    """Return two stories other than ``playing``: the ones told least lately here (never told first)."""
    others = [key for key in stories if key != playing]
    rng.shuffle(others)
    others.sort(key=lambda key: _TOLD.get(key, float("-inf")))
    return others[:2]


def picked(text: str | None, options: Sequence[Option], language: str) -> Option | None:
    """Return the one option ``text`` names (any of its words, anywhere); None for silence, neither or both."""
    if not text:
        return None
    said = normalize(text)
    named = [option for option in options if _first_at(said, option.words.get(language, ())) is not None]
    return named[0] if len(named) == 1 else None


def joined_in(text: str | None) -> bool:
    """Whether someone spoke: any words, or speech the model could not read (a roar, "¡fuuu!")."""
    return bool(text and text.strip())


class StoryVoice:
    """A story's recordings and screens: ``<folder>/voice/<clip id>.wav`` and ``<folder>/screens/<picture id>.png``.

    Both are made by scripts/story_build.py.
    """

    def __init__(self, folder: Path, cast: Mapping[str, Mapping[str, str]]) -> None:
        """Read clips from ``folder`` for the voices in ``cast`` (role to ``{"voice": id}``)."""
        self._folder = folder
        self._cast = cast
        self._cache: dict[str, Audio | None] = {}

    def path(self, line: Line, language: str) -> Path:
        """Where ``line``'s recording in ``language`` is (or goes)."""
        voice = self._cast.get(line.who, {}).get("voice", "")
        return self._folder / "voice" / f"{clip_id(voice, language, line.tagged(language))}.wav"

    def screen(self, picture: Picture) -> bytes | None:
        """Return ``picture`` ready for the e-ink screen, or None when it was not made."""
        path = self._folder / "screens" / f"{picture.id}.png"
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError as error:
            logger.warning("Story screen unreadable %s: %s", path.name, type(error).__name__)
            return None

    def clip(self, line: Line, language: str) -> Audio | None:
        """Return ``line``'s recording in ``language``, or None when it was not made."""
        path = self.path(line, language)
        key = str(path)
        if key not in self._cache:
            try:
                self._cache[key] = read_wav(path) if path.is_file() else None
            except (OSError, ValueError, EOFError) as error:
                logger.warning("Story clip unreadable %s: %s", path.name, type(error).__name__)
                self._cache[key] = None
        return self._cache[key]


class StoryGame(Game):
    """Tells one story, with the children joining in."""

    holds_head = True  # a pat on his head knocks on the door

    def __init__(
        self,
        story: Story,
        info: GameInfo,
        voice: StoryVoice,
        common: Common,
        common_voice: StoryVoice,
        sounds: KidsAudio,
        rng: random.Random | None = None,
    ) -> None:
        """Tell ``story`` (its catalog ``info``) with its recordings, the shared lines and the sounds."""
        self.info = info
        self._story = story
        self._voice = voice
        self._common = common
        self._common_voice = common_voice
        self._sounds = sounds
        self._rng = rng or random.Random()
        self._pending: list[str] = []  # sounds that play before the next line
        self._chosen: dict[str, str] = {}
        self._title = ""
        self._waiting: Picture | None = None  # a picture that came too soon after the last one
        self._shown_at: float | None = None  # story time of the last picture
        self._next: Step | None = None  # the step after the one playing
        self._early: Step | None = None  # a choice's scene already shown before its line
        self._screen_seen = False  # the screen checked in during this story
        self._nap_asked = False  # asked for the button during this nap
        self._nap_asks = 0

    # Playing -------------------------------------------------------------------------------------

    async def play(self, context: GameContext) -> None:
        """Tell the story from the start, then ask whether to tell another."""
        _TOLD[self._story.key] = time.monotonic()
        language = context.language
        self._title = self._story.title.get(language, "")
        self._scene(context, self._story.cover)
        self._pending.append("story_intro")
        steps = self._story.steps
        for number, step in enumerate(steps, 1):
            self._next = steps[number] if number < len(steps) else None
            await context.playing()
            context.progress = f"{number}/{len(steps)}"
            context.played()
            self._scene(context, None)  # a picture that waited for the gap
            await self._wake_screen(context)
            await self._step(context, step)
        await self._flush(context)
        await self._again(context)

    async def _step(self, context: GameContext, step: Step) -> None:
        kind = step.kind
        if kind == "say":
            line = step.line if step.line is not None else step.variants.get(self._chosen.get(step.choice, ""))
            if line is None:  # a choice that was never asked (it is checked when the file is read)
                line = next(iter(step.variants.values()))
            await self._say(context, line)
        elif kind == "sound":
            self._pending.append(step.name)
        elif kind == "scene":
            if step is self._early:  # shown as its choice was made
                return
            picture = step.pictures.get(self._chosen.get(step.choice, "") if step.choice else "")
            if picture is None and step.pictures:  # a choice that was never asked
                picture = next(iter(step.pictures.values()))
            self._scene(context, picture)
            if step.move:
                context.services.emotion(step.move)
        elif kind == "move":
            context.services.emotion(step.move)
        elif kind == "music":
            await self._flush(context)
            await self._music(context, step.name, step.seconds)
        elif kind == "noise":
            await self._noise(context, step)
        elif kind == "chorus":
            await self._chorus(context, step)
        elif kind == "pat":
            await self._pat(context, step)
        elif kind == "copy":
            await self._copy(context, step)
        elif kind == "choice":
            await self._choice(context, step)

    async def _noise(self, context: GameContext, step: Step) -> None:
        """Ask for a blow, roar or cheer: anyone heard counts; nothing gets "¡Más fuerte!" once, then the story does it."""
        heard = await self._ask(context, step.lines["ask"], NOISE_LISTEN_S, cloud=False)
        if not joined_in(heard):
            heard = await self._ask(context, step.lines["louder"], NOISE_LISTEN_S, cloud=False)
        if joined_in(heard):
            context.played()
            context.services.emotion("surprised")
            await self._say(context, step.lines["done"], after=(step.sound,))
        else:
            await self._say(context, step.lines["without"], after=(step.sound,))

    async def _chorus(self, context: GameContext, step: Step) -> None:
        """Hear the line everybody knows: anyone heard counts; otherwise someone in the story says it."""
        heard = await self._ask(context, step.lines["ask"], NOISE_LISTEN_S, cloud=False)
        if joined_in(heard):
            context.played()
            context.services.emotion("happy")
        await self._say(context, step.lines["done" if joined_in(heard) else "without"])

    async def _pat(self, context: GameContext, step: Step) -> None:
        """Wait for a pat on Richie's head (a knock on the door); without one, the knock comes anyway."""
        touches = context.services.touches
        seen = touches()
        await self._say(context, step.lines["ask"])
        waited = 0.0
        while touches() <= seen and waited < PAT_WAIT_S:
            await context.sleep(0.05)
            waited += 0.05
        patted = touches() > seen
        if patted:
            context.played()
        self._pending.append(step.sound)
        await self._say(context, step.lines["done" if patted else "without"])

    async def _copy(self, context: GameContext, step: Step) -> None:
        """Play a move to copy: Richie moves too, music for a few seconds; the children are trusted."""
        await self._say(context, step.lines["ask"])
        if step.move:
            context.services.emotion(step.move)
        await self._music(context, step.music, step.seconds or COPY_S)
        await self._say(context, step.lines["done"])

    async def _choice(self, context: GameContext, step: Step) -> None:
        """Ask for one of two small things; nobody picking (or a muddle) has Richie pick. The story rejoins either way."""
        language = context.language
        hints = [word for option in step.options for word in option.words.get(language, ())]
        context.services.hint_answers(hints)
        try:
            heard = await self._ask(context, step.lines["ask"], CHOICE_LISTEN_S, cloud=True)
        finally:
            context.services.hint_answers([])
        option = picked(heard, step.options, language)
        if option is not None:
            context.played()
            context.services.emotion("happy")
            logger.info("Story choice %s: %s (picked by the children)", step.choice, option.key)
        else:
            option = self._rng.choice(step.options)
            logger.info("Story choice %s: %s (picked by Richie)", step.choice, option.key)
            await self._say(context, option.picked)
        self._chosen[step.choice] = option.key
        # The picture of what was picked goes up now, as Richie says what happened, not after it.
        following = self._next
        if following is not None and following.kind == "scene" and following.choice == step.choice:
            self._early = following
            self._scene(context, following.pictures.get(option.key))
            if following.move:
                context.services.emotion(following.move)
        await self._say(context, option.then)

    async def _again(self, context: GameContext) -> None:
        """Ask "¿Otro cuento?": yes tells it again, a story's name tells that one, anything else says goodbye."""
        ask = self._common.lines.get("again")
        if ask is None:
            return
        context.services.hint_answers(
            [name for key in STORIES if key in GAMES for name in GAMES[key].spoken.get(context.language, ())]
        )
        try:
            heard = await self._ask(context, ask, AGAIN_LISTEN_S, cloud=True, common=True)
        finally:
            context.services.hint_answers([])
        following = again_answer(heard or "", self._story.key)
        if following == OFFER:
            following = await self._offer(context)
        if following is not None:
            context.next_game = following
            return
        goodbye = self._common.lines.get("goodbye")
        if goodbye is not None:
            await self._say(context, goodbye, common=True, after=("story_end",))

    async def _wake_screen(self, context: GameContext) -> None:
        """Ask the children to press the screen's back button when it has napped mid-story (2026-11-09)."""
        asleep_s = context.services.screen_asleep_s()
        if asleep_s <= 0:
            self._screen_seen, self._nap_asked = True, False
            return
        line = self._common.lines.get("screen_nap")
        if line is None or not self._screen_seen or self._nap_asked or self._nap_asks >= NAP_ASKS:
            return
        if asleep_s >= NAP_ASK_S:
            logger.info("Story screen asleep for %.0f s; asking for its button", asleep_s)
            self._nap_asked = True
            self._nap_asks += 1
            await self._say(context, line, common=True)

    async def _offer(self, context: GameContext) -> str | None:
        """Offer two other stories by name ("¿La gran carrera... o Bigotes?") and return the one picked."""
        offered = stories_to_offer(self._story.key, self._rng, STORIES)
        lines = self._common.lines
        first = lines.get(f"offer_{offered[0]}") if offered else None
        second = lines.get(f"or_{offered[1]}") if len(offered) > 1 else None
        if first is None or second is None:  # no offer recorded for them: the first story not told lately
            return offered[0] if offered else self._story.key
        await self._say(context, first, common=True)
        context.services.hint_answers(
            [name for key in STORIES if key in GAMES for name in GAMES[key].spoken.get(context.language, ())]
        )
        try:
            heard = await self._ask(context, second, AGAIN_LISTEN_S, cloud=True, common=True)
        finally:
            context.services.hint_answers([])
        return offer_answer(heard or "", offered, self._story.key, STORIES)

    # Audio ---------------------------------------------------------------------------------------

    def _audio(self, context: GameContext, line: Line, common: bool, after: Sequence[str]) -> Audio | None:
        """Return the pending sounds, the line's recording and the ``after`` sounds as one piece; None without a clip."""
        clip = (self._common_voice if common else self._voice).clip(line, context.language)
        if clip is None:
            return None
        pieces = [sound for name in self._pending if name and (sound := self._sounds.sound(name)) is not None]
        pieces.append(clip)
        pieces += [sound for name in after if name and (sound := self._sounds.sound(name)) is not None]
        self._pending.clear()
        return join(*pieces)

    async def _say(self, context: GameContext, line: Line, *, common: bool = False, after: Sequence[str] = ()) -> None:
        """Play ``line`` (with the sounds before and after it); Richie's own voice without its recording."""
        await context.playing()
        text = shown(line.tagged(context.language))
        say_audio = context.services.say_audio
        audio = self._audio(context, line, common, after) if say_audio is not None else None
        if say_audio is not None and audio is not None:
            await say_audio(RATE, audio, text, context.language, None)
            return
        await self._flush(context)
        await context.say(text)
        self._pending.extend(name for name in after if name)
        await self._flush(context)

    async def _ask(
        self, context: GameContext, line: Line, listen_s: float, *, cloud: bool, common: bool = False
    ) -> str | None:
        """Play ``line`` and return what was said after it ("" for silence), or None when nothing came back."""
        answered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def answer(text: str) -> None:
            if not answered.done():
                answered.set_result(text)

        await context.playing()
        text = shown(line.tagged(context.language))
        say_audio = context.services.say_audio
        audio = self._audio(context, line, common, ()) if say_audio is not None else None
        if say_audio is not None and audio is not None:
            await say_audio(RATE, audio, text, context.language, answer, listen_s=listen_s, cloud=cloud)
        else:
            await self._flush(context)
            context.services.ask(text, context.language, answer)
        try:
            return await asyncio.wait_for(answered, listen_s + ANSWER_MARGIN_S)
        except asyncio.TimeoutError:
            return None

    async def _flush(self, context: GameContext) -> None:
        """Play the sounds still waiting for a line."""
        names, self._pending = [name for name in self._pending if name], []
        say_audio = context.services.say_audio
        pieces = [sound for name in names if (sound := self._sounds.sound(name)) is not None]
        if say_audio is not None and pieces:
            await say_audio(RATE, join(*pieces), SOUND_TEXT, context.language, None)

    async def _music(self, context: GameContext, name: str, seconds: float) -> None:
        """Music ``name`` for ``seconds`` of playing time (or a quiet wait when it was not made)."""
        if not name or self._sounds.music(name) is None:
            await context.sleep(seconds)
            return
        end = context.played_s + seconds
        await self._sounds.play_music(context, lambda _: name, lambda: context.played_s >= end)

    def _scene(self, context: GameContext, picture: Picture | None) -> None:
        """Show ``picture`` now, or once MIN_SCENE_GAP_S has passed since the last; None shows one still waiting."""
        if picture is not None:
            self._waiting = picture  # a newer picture replaces one that never got its turn
        waiting = self._waiting
        if waiting is None:
            if not context.screen:
                self._title_screen(context)
            return
        if self._shown_at is not None and context.played_s - self._shown_at < MIN_SCENE_GAP_S:
            return
        screen = self._voice.screen(waiting)
        self._waiting = None
        if screen is None:
            logger.info("Story picture not made: %s", waiting.id)
            if not context.screen:
                self._title_screen(context)
            return
        self._shown_at = context.played_s
        context.show(screen)

    def _title_screen(self, context: GameContext) -> None:
        """Show the story's title, until its pictures are made."""
        try:
            context.show(render_game_prompt(self._title, 0, 0))
        except Exception as error:  # a screen problem must not stop the story
            logger.warning("Story screen not drawn: %s", type(error).__name__)

    def paused(self, context: GameContext) -> None:
        """Nothing keeps playing on its own: music stops with the pause."""
