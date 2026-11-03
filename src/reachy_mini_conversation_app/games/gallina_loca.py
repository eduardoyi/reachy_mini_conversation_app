"""La gallina loca ("juguemos a la gallina loca"): a hot-potato game for children of 2 to 6 (docs/plans/la-gallina-loca.md).

Richie is a crazy hen with a secret timer. Each child says their name at the start; he matches what he hears
against the family's names (the face-recognition list, used as words only: no camera) and takes any other name
as heard. Then he calls a child (never the same one twice running) with a cluck and something silly to do, a
Simón dice pose or something active, with its drawing on the screen. A pat on his head or "¡listo!" passes the
turn. A bouncy hen tune plays and speeds up partway; when the timer runs out, even mid-turn, the hen squawks
"¡COCOROCÓ!" and whoever's turn it is gets a silly forfeit. Nobody loses. Then "¿Otra vez?", with the same names.

Lines are the kids' voice's recorded takes (games/kids_lines.py); names are said live in the same voice
(``Speech``, kept by text). Without a clip or a key, that part is said in Richie's everyday voice, in order.
"""

import re
import random
import asyncio
import difflib
import logging
from typing import Final, Callable, Awaitable
from dataclasses import dataclass

from reachy_mini_conversation_app.games.base import UNCLEAR_ANSWER, Game, GameContext
from reachy_mini_conversation_app.games.hunt import ask_again
from reachy_mini_conversation_app.eink_display import render_game_prompt, render_game_picture
from reachy_mini_conversation_app.games.catalog import GALLINA_LOCA, words
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.simon_says import POSES
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, Speech, join


logger = logging.getLogger(__name__)

# The secret timer, in playing seconds, and where between its start and end the faster tune comes in.
ROUND_S: Final[tuple[float, float]] = (45.0, 90.0)
HURRY_AT: Final[tuple[float, float]] = (0.55, 0.75)
# A turn ends on a pat or "¡listo!" after MIN_TURN_S (sooner is the last child's), or after TURN_S.
TURN_S: Final[float] = 20.0
MIN_TURN_S: Final[float] = 1.5
FORFEIT_S: Final[float] = 15.0
MAX_PLAYERS: Final[int] = 6
# A name may start up to NAME_LISTEN_S after "¿Quién juega?" and is waited for NAME_ANSWER_S at most.
NAME_LISTEN_S: Final[float] = 8.0
NAME_ANSWER_S: Final[float] = 20.0
# A heard word this close to a known name (difflib ratio) is that name: "Endzo" is Enzo.
NAME_MATCH: Final[float] = 0.75
POSE_SHARE: Final[float] = 0.4  # of the challenges, Simón dice's poses
AGAIN_YOU: Final[float] = 0.12  # "¡Otra vez tú!": the same child gets another one
_TICK_S: Final[float] = 0.1


@dataclass(frozen=True)
class Challenge:
    """Something silly to do: what Richie says, the drawing shown, and shorter words for the screen."""

    spanish: str
    english: str
    drawing: str
    label_es: str = ""
    label_en: str = ""


def _child(doing: str) -> str:
    return f"a happy young child {doing}, seen from the front"


# The active ones; Simón dice's poses are the still ones (games/simon_says.py).
CHALLENGES: Final[dict[str, Challenge]] = {
    "duck": Challenge("camina como un pato", "walk like a duck", _child("waddling like a duck, hands as wings")),
    "wings": Challenge("aletea como una gallina y di cocorocó", "flap your wings like a hen and say cluck cluck", _child("flapping their elbows like chicken wings"), "aletea como gallina", "flap like a hen"),
    "bunny_hop": Challenge("salta como un conejo tres veces", "hop like a bunny three times", _child("hopping like a bunny"), "salta como conejo", "hop like a bunny"),
    "frog": Challenge("salta como una rana", "jump like a frog", _child("squatting and jumping like a frog")),
    "jump": Challenge("salta tres veces bien alto", "jump three times, really high", _child("jumping high in the air"), "salta tres veces", "jump three times"),
    "touch_wall": Challenge("corre, toca la pared y vuelve", "run, touch the wall and come back", "a happy young child running to touch a wall", "¡toca la pared!", "touch the wall!"),
    "touch_floor": Challenge("toca el piso con las dos manos", "touch the floor with both hands", _child("touching the floor with both hands"), "manos en el piso", "hands on the floor"),
    "spin": Challenge("da tres vueltas", "spin around three times", _child("spinning around happily")),
    "clap": Challenge("aplaude cinco veces", "clap five times", _child("clapping their hands")),
    "puppy": Challenge("camina en cuatro patas como un perrito", "walk on all fours like a puppy", "a happy young child crawling on all fours like a puppy", "como un perrito", "like a puppy"),
    "lion": Challenge("ruge como un león", "roar like a lion", _child("roaring like a lion with hands like claws")),
    "kitten": Challenge("maúlla como un gatito", "meow like a kitten", _child("pretending to be a kitten, hands like paws")),
    "elephant": Challenge("haz de elefante, con la trompa", "be an elephant with a trunk", _child("pretending to be an elephant, one arm swinging as a trunk"), "haz de elefante", "be an elephant"),
    "robot": Challenge("camina como un robot", "walk like a robot", _child("walking stiffly like a robot")),
    "giraffe": Challenge("estírate como una jirafa", "stretch up tall like a giraffe", _child("stretching up very tall on tiptoes")),
    "fish": Challenge("nada como un pez", "swim like a fish", _child("pretending to swim like a fish")),
    "bird": Challenge("vuela como un pajarito", "fly like a little bird", _child("flapping their arms like a flying bird")),
    "funny_face": Challenge("haz una cara chistosa", "make a funny face", _child("making a very funny silly face")),
    "hug": Challenge("dale un abrazo a alguien", "give someone a hug", "two happy young children hugging", "¡un abrazo!", "a hug!"),
    "march": Challenge("marcha en tu puesto", "march on the spot", _child("marching on the spot with knees high")),
    "backwards": Challenge("camina tres pasos hacia atrás", "take three steps backwards", _child("walking backwards carefully"), "tres pasos atrás", "three steps back"),
    "snake": Challenge("arrástrate como una culebra", "slither like a snake", "a happy young child slithering on the floor like a snake", "como una culebra", "like a snake"),
}  # fmt: skip

# What whoever's turn it is does when the hen goes off.
FORFEITS: Final[dict[str, Challenge]] = {
    "hen_dance": Challenge("haz el baile de la gallina: aletea y mueve la colita", "do the hen dance: flap your wings and wiggle your tail", _child("dancing like a chicken, flapping elbows and wiggling"), "¡el baile de la gallina!", "the hen dance!"),
    "cluck": Challenge("cacarea tres veces: ¡coc, coc, coc!", "cluck three times: cluck, cluck, cluck!", _child("clucking like a hen, hands as a beak"), "cacarea tres veces", "cluck three times"),
    "egg": Challenge("pon un huevo: agáchate y di ¡pop!", "lay an egg: crouch down and say pop!", _child("crouching down like a hen laying an egg"), "¡pon un huevo!", "lay an egg!"),
    "rooster": Challenge("párate como un gallo y canta ¡quiquiriquí!", "stand like a rooster and crow cock-a-doodle-doo!", _child("crowing like a rooster, chest out, elbows flapping"), "¡canta como gallo!", "crow like a rooster!"),
    "chicken_walk": Challenge("camina como gallina, picoteando el piso", "walk like a chicken, pecking the floor", _child("walking like a chicken, pecking at the ground"), "camina como gallina", "walk like a chicken"),
}  # fmt: skip

# Words that are not names in "yo me llamo Enzo", "este es Enzo y ella es Sofía".
_NOT_NAMES: Final[frozenset[str]] = frozenset(
    {"yo", "tu", "el", "ella", "mi", "me", "te", "se", "nos", "llamo", "llama", "llaman", "llamamos", "nombre", "es",
     "son", "soy", "somos", "y", "e", "o", "con", "de", "del", "la", "las", "los", "un", "una", "este", "esta", "ese",
     "esa", "aqui", "hola", "juega", "juegan", "juego", "jugamos", "jugar", "quiero", "tambien", "nadie", "mas", "ya",
     "listo", "no", "si", "todos", "ninguno", "eso", "todo", "bueno", "ah", "eh", "mm", "i", "im", "am", "my", "name",
     "names", "is", "are", "and", "me", "too", "also", "hi", "hello", "the", "a", "play", "playing", "want", "to",
     "nobody", "else", "thats", "it", "all", "done", "yes", "ok", "okay", "we", "us", "this", "that", "he", "she",
     "richie", "reachy"}
)  # fmt: skip
# Said alone (or at the end), no more names: "ya", "nadie más", "that's all".
_NO_MORE: Final[tuple[str, ...]] = (
    "ya", "ya esta", "ya estamos", "estamos todos", "somos todos", "son todos", "nadie", "nadie mas", "ninguno",
    "eso es todo", "listo", "no", "no mas", "thats all", "thats it", "nobody", "nobody else", "no more", "done",
    "thats everyone",
)  # fmt: skip
# Family words kept with their accents.
_FAMILY: Final[dict[str, str]] = {
    "mama": "Mamá", "papa": "Papá", "abuela": "Abuela", "abuelo": "Abuelo", "tia": "Tía", "tio": "Tío",
    "mom": "Mom", "dad": "Dad", "mommy": "Mommy", "daddy": "Daddy", "grandma": "Grandma", "grandpa": "Grandpa",
}  # fmt: skip
# "¡Listo!" heard without the name passes the turn.
_DONE: Final[frozenset[str]] = frozenset(
    {"listo", "lista", "listos", "listas", "ya", "ya esta", "ya lo hice", "termine", "hecho", "done", "ready",
     "finished", "im done", "i did it", "all done"}
)  # fmt: skip

_HELLO: Final[dict[str, str]] = {"es": "¡Hola, {name}!", "en": "Hi, {name}!"}
_CALL: Final[dict[str, str]] = {"es": "¡{name}!", "en": "{name}!"}
_NEXT: Final[dict[str, str]] = {"es": "¡Siguiente!", "en": "Next!"}
_BOOM: Final[dict[str, str]] = {"es": "¡COCOROCÓ!", "en": "BAWK!"}
_CAPTION: Final[dict[str, str]] = {"es": "La gallina loca", "en": "Crazy hen"}


def heard_names(text: str, known: list[str]) -> tuple[list[str], bool]:
    """Return the names in an answer to "¿Quién juega?", and whether it also says there are no more.

    Known names match closely ("Endzo" is Enzo, said with the family's spelling); with none of them, any word
    that is not a usual word around a name is taken as a name, as heard ("yo me llamo Sofía": Sofía).
    """
    said = normalize(text)
    done = said in _NO_MORE or any(said.endswith(f" {phrase}") for phrase in _NO_MORE if phrase != "no")
    heard = [(normalize(raw), raw) for raw in re.findall(r"[^\W\d_]+", text)]
    found: list[str] = []
    for name in known:
        first = name.split()[0] if name.split() else ""
        plain = normalize(first)
        if not plain:
            continue
        close = any(
            token == plain or (len(token) >= 3 and difflib.SequenceMatcher(None, token, plain).ratio() >= NAME_MATCH)
            for token, _ in heard
        )
        if close and first not in found:
            found.append(first)
    if not found:
        for token, raw in heard:
            if token in _FAMILY:
                found.append(_FAMILY[token])
            elif token not in _NOT_NAMES and len(token) >= 2:
                found.append(raw[:1].upper() + raw[1:])
    return list(dict.fromkeys(found))[:MAX_PLAYERS], done


@dataclass(frozen=True)
class _Live:
    """Words said live in the kids' voice: a child's name."""

    text: str


class GallinaLoca(Game):
    """Turns by name with silly challenges until the hen goes off; then a forfeit and "¿Otra vez?"."""

    info = GALLINA_LOCA
    holds_head = True  # pats pass the turns

    def __init__(
        self,
        audio: KidsAudio,
        *,
        speech: Speech | None = None,
        known_names: Callable[[], list[str]] = list,
        rng: random.Random | None = None,
    ) -> None:
        """Speak with ``audio`` (names with ``speech``); ``known_names`` is the family's names, read at the start."""
        self._audio = audio
        self._speech = speech
        self._known_names = known_names
        self._rng = rng or random.Random()
        self._names: list[str] = []
        self._turn_open = False
        self._done = False  # "¡listo!" heard during the open turn
        self._deadline = 0.0
        self._hurry_at = 0.0

    # Speaking -------------------------------------------------------------------------------------

    async def _piece(self, context: GameContext, part: str | _Live) -> tuple[str, Audio | None]:
        language = context.language
        if isinstance(part, _Live):
            samples = await self._speech.say(part.text, language) if self._speech is not None else None
            return part.text, samples
        take = self._audio.take(part, language)
        return take if take is not None else (self._audio.text(part, language), None)

    async def _say(
        self,
        context: GameContext,
        *parts: str | _Live,
        sounds: tuple[str, ...] = (),
        answer: Callable[[str], Awaitable[object]] | None = None,
        listen_s: float | None = None,
    ) -> None:
        """Say ``parts`` (moments of kids_lines, or live names) after ``sounds``; with ``answer``, listen after."""
        say_audio = context.services.say_audio
        pieces: list[tuple[str, Audio | None]] = [("", s) for n in sounds if (s := self._audio.sound(n)) is not None]
        pieces += await asyncio.gather(*(self._piece(context, part) for part in parts))
        segments: list[tuple[list[str], list[Audio]]] = []  # one recorded run, or one text said in Richie's voice
        for text, samples in pieces:
            if samples is not None and say_audio is not None:
                if segments and segments[-1][1]:
                    segments[-1][0].append(text)
                    segments[-1][1].append(samples)
                else:
                    segments.append(([text], [samples]))
            elif text:
                segments.append(([text], []))
        for index, (texts, run) in enumerate(segments):
            last = index == len(segments) - 1
            text = " ".join(t for t in texts if t)
            await context.playing()
            if run and say_audio is not None:
                await say_audio(RATE, join(*run), text, context.language, answer if last else None, listen_s=listen_s if last else None)  # fmt: skip
            elif last and answer is not None:
                context.services.ask(text, context.language, answer)
            else:
                await context.say(text)

    async def _ask(self, context: GameContext, *parts: str | _Live) -> str | None:
        """Say ``parts`` and return the answer (None when none came)."""
        answered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def answer(said: str) -> None:
            if not answered.done():
                answered.set_result(said)

        await self._say(context, *parts, answer=answer, listen_s=NAME_LISTEN_S)
        try:
            said = await asyncio.wait_for(answered, NAME_ANSWER_S)
        except asyncio.TimeoutError:
            return None
        return None if said == UNCLEAR_ANSWER else said

    # What the room says ---------------------------------------------------------------------------

    def heard(self, context: GameContext, text: str) -> bool:
        """Pass the open turn on "¡listo!" (or "¡ya!") said without the name."""
        said = words(text)
        if self._turn_open and (said in _DONE or (said and set(said.split()) <= {"listo", "lista", "ya"})):
            self._done = True
            return True
        return False

    # The game -------------------------------------------------------------------------------------

    async def play(self, context: GameContext) -> None:
        """Ask who plays, then play rounds until nobody wants another (or another kids' game is asked for)."""
        self._draw_missing(context)
        language = context.language
        context.show(render_game_prompt(_BOOM[language], 0, 0, _CAPTION[language]))
        await self._say(context, "gallina_start", sounds=("intro_gallina",))
        await self._ask_names(context)
        while True:
            name = await self._round(context)
            await self._forfeit(context, name)
            following = await ask_again(context, self._audio)
            if following == self.info.key:
                continue
            context.next_game = following
            if following is None:
                await self._audio.say(context, "thanks")
            return

    async def _ask_names(self, context: GameContext) -> None:
        """Ask who plays until "ya", silence, or MAX_PLAYERS names; each name is greeted live."""
        try:
            known = self._known_names()
        except Exception as error:  # the names are a nicety: a broken list must not stop the game
            logger.warning("La gallina loca: known names unavailable: %s", type(error).__name__)
            known = []
        language = context.language
        moment = "gallina_who"
        for _ in range(MAX_PLAYERS + 2):
            said = await self._ask(context, moment)
            if not said:
                break
            names, done = heard_names(said, known)
            new = [name for name in names if name not in self._names][: MAX_PLAYERS - len(self._names)]
            self._names += new
            context.played()
            if new:
                greeting = _HELLO[language].format(name=" y ".join(new) if language == "es" else " and ".join(new))
                if done or len(self._names) >= MAX_PLAYERS:
                    await self._say(context, _Live(greeting))
                    break
                moment = "gallina_more"
                self._prepare_names(context)
                await self._say(context, _Live(greeting))
            elif done:
                break
            else:
                moment = "gallina_more" if self._names else "gallina_who"
        logger.info("La gallina loca: %d players named", len(self._names))
        await self._say(context, "gallina_ready" if self._names else "gallina_no_names")

    def _prepare_names(self, context: GameContext) -> None:
        if self._speech is None:
            return
        for name in self._names:
            self._speech.prepare(_CALL[context.language].format(name=name), context.language)

    def _pick(self, used: set[str]) -> tuple[str, Challenge]:
        """Return a challenge not played this round: a pose about POSE_SHARE of the time, else an active one."""
        poses = [key for key in POSES if f"pose_{key}" not in used]
        active = [key for key in CHALLENGES if f"do_{key}" not in used]
        if not active or (poses and self._rng.random() < POSE_SHARE):
            key = self._rng.choice(poses or list(POSES))
            pose = POSES[key]
            return f"pose_{key}", Challenge(pose.spanish, pose.english, pose.drawing, pose.label_es, pose.label_en)
        key = self._rng.choice(active)
        return f"do_{key}", CHALLENGES[key]

    def _screen(self, context: GameContext, challenge: Challenge, caption: str) -> bytes:
        es = context.language == "es"
        words_shown = (challenge.label_es or challenge.spanish) if es else (challenge.label_en or challenge.english)
        words_shown = words_shown[:1].upper() + words_shown[1:]
        picture = context.services.picture(challenge.drawing) if context.services.picture else None
        if picture is None:
            return render_game_prompt(words_shown, 0, 0, caption)
        return render_game_picture(picture, words_shown, 0, 0, caption)

    async def _round(self, context: GameContext) -> str | None:
        """Play turns until the secret timer runs out; return whose turn it was then (None without names)."""
        language = context.language
        started = context.played_s
        length = self._rng.uniform(*ROUND_S)
        self._deadline = started + length
        self._hurry_at = started + length * self._rng.uniform(*HURRY_AT)
        hurried = False
        music = asyncio.get_running_loop().create_task(
            self._audio.play_music(
                context,
                lambda _: "gallina_hurry" if context.played_s >= self._hurry_at else "gallina",
                lambda: context.played_s >= self._deadline,
            ),
            name="gallina-music",
        )
        used: set[str] = set()
        name: str | None = None
        again = False
        turn = 0
        try:
            while context.played_s < self._deadline:
                turn += 1
                if not again and self._names:
                    others = [n for n in self._names if n != name] or self._names
                    name = self._rng.choice(others)
                key, challenge = self._pick(used)
                used.add(key)
                caption = name or _NEXT[language]
                context.progress = f"Turn {turn}: {name or 'next'}, {challenge.english}"
                context.show(self._screen(context, challenge, caption))
                lead: list[str | _Live] = []
                if not hurried and context.played_s >= self._hurry_at:
                    hurried = True
                    lead.append("gallina_hurry")
                if again:
                    lead.append("gallina_again_you")
                elif name is not None:
                    lead.append(_Live(_CALL[language].format(name=name)))
                else:
                    lead.append("gallina_next")
                await self._say(context, *lead, f"gallina_{key}", sounds=("cluck",))
                ended = await self._wait_turn(context, TURN_S, until_deadline=True)
                if ended == "boom":
                    break
                context.played()
                again = ended == "done" and bool(self._names) and self._rng.random() < AGAIN_YOU
                if context.played_s >= self._deadline:
                    break
                await self._say(context, "gallina_good" if ended == "done" else "gallina_slow", sounds=("star",) if ended == "done" else ())  # fmt: skip
        finally:
            music.cancel()
        return name

    async def _wait_turn(self, context: GameContext, seconds: float, *, until_deadline: bool = False) -> str:
        """Wait for a pat or "¡listo!" ("done"), ``seconds`` ("timeout"), or the hen going off ("boom")."""
        touches = context.services.touches
        seen = touches()
        self._done, self._turn_open = False, True
        started = context.played_s
        try:
            while True:
                await context.sleep(_TICK_S)
                elapsed = context.played_s - started
                if until_deadline and context.played_s >= self._deadline:
                    return "boom"
                patted = touches() > seen
                if elapsed < MIN_TURN_S:
                    # The last child's pat or "¡listo!" is not this one's.
                    seen, self._done = touches(), False
                elif patted or self._done:
                    return "done"
                if elapsed >= seconds:
                    return "timeout"
        finally:
            self._turn_open = False

    async def _forfeit(self, context: GameContext, name: str | None) -> None:
        """Set the hen off: a squawk, whose turn it was, and a silly forfeit; a pat or "¡listo!" ends it."""
        language = context.language
        context.services.emotion("excited")
        context.show(render_game_prompt(_BOOM[language], 0, 0, name or _CAPTION[language]))
        key = self._rng.choice(list(FORFEITS))
        forfeit = FORFEITS[key]
        who: list[str | _Live] = (
            [_Live(_CALL[language].format(name=name)), "gallina_caught"] if name else ["gallina_caught_anyone"]
        )
        await self._say(context, "gallina_boom", *who, sounds=("squawk",))
        context.show(self._screen(context, forfeit, name or _CAPTION[language]))
        context.progress = f"Forfeit: {name or 'someone'}, {forfeit.english}"
        await self._say(context, f"gallina_forfeit_{key}", sounds=("egg_pop",))
        await self._wait_turn(context, FORFEIT_S)
        context.played()
        await self._say(context, "gallina_forfeit_done", sounds=("cheer",))

    def _draw_missing(self, context: GameContext) -> None:
        """Draw the challenges and forfeits not drawn yet, in the background (once, for every later game)."""
        draw = context.services.draw_pictures
        if draw is None or any(not task.done() for task in _DRAWING):
            return
        subjects = [c.drawing for c in (*CHALLENGES.values(), *FORFEITS.values())] + [
            p.drawing for p in POSES.values()
        ]
        task: asyncio.Task[None] = asyncio.get_running_loop().create_task(draw(subjects), name="gallina-pictures")
        _DRAWING.add(task)
        task.add_done_callback(_DRAWING.discard)


_DRAWING: set["asyncio.Task[None]"] = set()
