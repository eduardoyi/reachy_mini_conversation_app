"""La gallina loca ("juguemos a la gallina loca"): a hot-potato game for children of 2 to 6 (docs/plans/la-gallina-loca.md).

Richie is a crazy hen with a secret timer. Each child says their name at the start; he matches what he hears
against the family's names (the face-recognition list, used as words only: no camera) and takes any other name
as heard, then says the order the turns go in. He calls each child in that order, with a cluck and something
silly and quick to do (a funny walk, a silly voice, three yellow things, a run, copying one of his own moves),
with its drawing on the screen. A pat on his head or "¡listo!" passes the hen to the next child. A bouncy tune
plays while they do it and speeds up partway; when the timer runs out, even mid-turn, he crows "¡COCOROCÓ!" and
whoever has the hen lays an egg: a silly hen move, and an egg next to their name on the screen. The next round
starts with the child after them. When nobody wants another round, whoever laid the most eggs is "la gallina de
oro".

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
from reachy_mini_conversation_app.eink_display import render_egg_count, render_game_prompt, render_game_picture
from reachy_mini_conversation_app.games.catalog import GALLINA_LOCA, words
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.big_brain_audio import RATE, Audio, Speech, join


logger = logging.getLogger(__name__)

# The secret timer, in playing seconds, and where between its start and end the faster tune comes in.
ROUND_S: Final[tuple[float, float]] = (45.0, 90.0)
HURRY_AT: Final[tuple[float, float]] = (0.55, 0.75)
# A turn ends on a pat or "¡listo!" after MIN_TURN_S (sooner is the last child's), or after TURN_S. After one of
# his own moves to copy, pats are not counted for COPY_SETTLE_S: the move could look like one.
TURN_S: Final[float] = 20.0
MIN_TURN_S: Final[float] = 1.5
COPY_SETTLE_S: Final[float] = 4.0
FORFEIT_S: Final[float] = 15.0
MAX_PLAYERS: Final[int] = 6
# A name may start up to NAME_LISTEN_S after "¿Quién juega?" and is waited for NAME_ANSWER_S at most.
NAME_LISTEN_S: Final[float] = 8.0
NAME_ANSWER_S: Final[float] = 20.0
# A heard word this close to a known name (difflib ratio) is that name: "Endzo" is Enzo.
NAME_MATCH: Final[float] = 0.75
GOOD_SHARE: Final[float] = 0.35  # of the turns done before the faster tune, a quick "¡Bien!" (after it, none)
_TICK_S: Final[float] = 0.1


@dataclass(frozen=True)
class Challenge:
    """Something silly to do: what Richie says, the drawing shown, shorter words for the screen, and his move.

    ``move`` is one of his recorded moves (an emotion intent) for the child to copy.
    """

    spanish: str
    english: str
    drawing: str
    label_es: str = ""
    label_en: str = ""
    move: str = ""


def _child(doing: str) -> str:
    return f"a happy young child {doing}, seen from the front"


_COPY_ES, _COPY_EN = "¡Copia a Richie!", "Copy Richie!"


def _copy(move: str, doing: str) -> Challenge:
    drawing = f"a happy young child copying a small white robot that is {doing}"
    return Challenge("copia a Richie", "copy Richie", drawing, _COPY_ES, _COPY_EN, move)


# Quick and silly (said in about two seconds): funny moves and voices, quick thinking, a run, and Richie's moves.
CHALLENGES: Final[dict[str, Challenge]] = {
    "tail": Challenge("mueve la colita como gallina", "shake your chicken butt", _child("wiggling their bottom like a chicken, hands on hips"), "¡Mueve la colita!", "Shake your chicken butt!"),
    "butt_name": Challenge("escribe tu nombre con la colita", "write your name with your butt", "a happy young child from behind, wiggling their bottom to write in the air, giggling over their shoulder", "¡Escribe con la colita!", "Write with your butt!"),
    "armpit": Challenge("haz un pedito con la axila", "make an armpit toot", _child("pressing a hand in their armpit to make a funny noise, laughing"), "¡Pedito con la axila!", "Armpit toot!"),
    "jelly": Challenge("tiembla como gelatina", "wobble like jelly", _child("wobbling all over like jelly")),
    "noodle": Challenge("baila como espagueti", "dance like a wet noodle", _child("dancing all floppy like a wet noodle"), "¡Baila como espagueti!", "Noodle dance!"),
    "penguin": Challenge("camina como pingüino", "waddle like a penguin", _child("waddling like a penguin, arms stiff at their sides")),
    "zombie": Challenge("camina como zombi", "walk like a zombie", _child("walking like a silly zombie, arms out in front")),
    "frog": Challenge("salta como rana y di ¡croac!", "jump like a frog and say ribbit", _child("squatting and jumping like a frog"), "¡Rana: croac!", "Frog: ribbit!"),
    "one_foot": Challenge("salta en un pie y di ¡pío, pío!", "hop on one foot and say cheep cheep", _child("hopping on one foot"), "¡En un pie: pío, pío!", "One foot: cheep cheep!"),
    "monkey": Challenge("haz de mono: ¡uh, uh, ah, ah!", "be a monkey: ooh ooh ah ah", _child("pretending to be a monkey, scratching their head and armpit"), "¡Haz de mono!", "Be a monkey!"),
    "statue": Challenge("quédate como estatua... ¡y no te rías!", "freeze like a statue... no laughing", _child("frozen like a statue, trying very hard not to laugh"), "¡Estatua sin reírte!", "Statue, no laughing!"),
    "monster": Challenge("di ¡pollito! con voz de monstruo", "say chicken in a monster voice", _child("roaring like a funny monster, hands like claws"), "¡Voz de monstruo!", "Monster voice!"),
    "witch": Challenge("ríete como bruja", "laugh like a witch", _child("cackling like a witch, hands rubbing together")),
    "baby": Challenge("llora como bebé", "cry like a baby", _child("pretending to cry like a baby, fists at their eyes")),
    "opera": Challenge("canta tu nombre como ópera", "sing your name like an opera singer", _child("singing dramatically like an opera singer, one hand on their chest"), "¡Canta como ópera!", "Sing like opera!"),
    "robot": Challenge("di ¡hola! como robot", "say hello like a robot", _child("talking and moving stiffly like a robot"), "¡Hola como robot!", "Robot hello!"),
    "sneeze": Challenge("estornuda gigante", "do a giant sneeze", _child("doing a huge pretend sneeze"), "¡Estornudo gigante!", "Giant sneeze!"),
    "burp": Challenge("haz un eructo de mentira", "do a pretend burp", _child("doing a silly pretend burp, hand on their tummy"), "¡Eructo de mentira!", "Pretend burp!"),
    "tongue": Challenge("di tu comida favorita con la lengua afuera", "say your favorite food with your tongue out", _child("talking with their tongue sticking out"), "¡Con la lengua afuera!", "Tongue out!"),
    "yellow": Challenge("di tres cosas amarillas", "say three yellow things", "a banana, a sun and a little chick", "¡Tres cosas amarillas!", "Three yellow things!"),
    "flies": Challenge("di un animal que vuela", "say an animal that flies", "a bird, a butterfly and a bee flying", "¡Un animal que vuela!", "An animal that flies!"),
    "blast_off": Challenge("cuenta del cinco al cero... ¡y despega!", "count down from five... and blast off", _child("jumping up like a rocket blasting off, arms over their head"), "¡5, 4, 3, 2, 1... despega!", "5, 4, 3, 2, 1... blast off!"),
    "touch_red": Challenge("corre, toca algo rojo y vuelve", "run, touch something red and come back", "a happy young child running to touch a red ball", "¡Toca algo rojo!", "Touch something red!"),
    "touch_soft": Challenge("corre, toca algo suave y vuelve", "run, touch something soft and come back", "a happy young child running to touch a soft pillow", "¡Toca algo suave!", "Touch something soft!"),
    "tickle": Challenge("hazle cosquillas a Richie", "tickle Richie", "a happy young child tickling a small white robot's head", "¡Cosquillas a Richie!", "Tickle Richie!"),
    "invent": Challenge("inventa algo loco", "make up something crazy", _child("doing a wild made-up silly dance"), "¡Inventa algo loco!", "Make up something crazy!"),
    "copy_dance": _copy("dance", "dancing"),
    "copy_electric": _copy("electric", "shaking all over as if electrified"),
    "copy_scared": _copy("scared", "pretending to be scared"),
    "copy_surprised": _copy("surprised", "looking very surprised"),
}  # fmt: skip

# How whoever has the hen when it goes off lays their egg.
FORFEITS: Final[dict[str, Challenge]] = {
    "squat": Challenge("agáchate, mueve la colita y di ¡plop!", "squat down, wiggle your butt and say plop", _child("squatting like a hen laying an egg, wiggling their bottom"), "¡Pon un huevo!", "Lay an egg!"),
    "giant": Challenge("pon un huevo gigante: haz fuerza... ¡y plop!", "lay a giant egg: push, push... and plop", _child("squeezing with a red face like a hen laying a giant egg, laughing"), "¡Un huevo gigante!", "A giant egg!"),
    "cluck": Challenge("aletea, cacarea ¡coc, coc, coc! y pon el huevo: ¡plop!", "flap, cluck cluck cluck, and lay it: plop", _child("flapping their elbows like wings and clucking"), "¡Coc, coc... plop!", "Cluck cluck... plop!"),
    "spin": Challenge("da una vuelta como gallina loca y pon el huevo: ¡plop!", "spin like a crazy hen and lay it: plop", _child("spinning like a crazy chicken, elbows flapping"), "¡Vuelta y huevo!", "Spin and lay!"),
    "rooster": Challenge("párate como gallo, canta ¡quiquiriquí! y pon el huevo", "stand like a rooster, crow cock-a-doodle-doo, and lay your egg", _child("crowing like a rooster, chest out, elbows flapping"), "¡Quiquiriquí!", "Cock-a-doodle-doo!"),
    "peck": Challenge("camina picoteando como gallina... ¡y plop!, un huevo", "walk pecking like a hen... and plop, an egg", _child("walking like a chicken, pecking at the ground"), "¡Picotea y plop!", "Peck and plop!"),
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
_ORDER: Final[dict[str, str]] = {"es": "En este orden", "en": "In this order"}
_EGGS: Final[dict[str, str]] = {"es": "Huevos", "en": "Eggs"}
_GOLDEN: Final[dict[str, str]] = {"es": "¡Gallina de oro!", "en": "Golden hen!"}
_AND: Final[dict[str, str]] = {"es": " y ", "en": " and "}


def _listed(names: list[str], language: str) -> str:
    """Return "Enzo, Sofía y Papá" (or "and")."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + _AND[language] + names[-1]


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
    """Turns in order with silly challenges until the hen goes off; then an egg and "¿Otra vez?"."""

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
        self._eggs: dict[str, int] = {}
        self._used: set[str] = set()  # the challenges played this game
        self._holder = 0  # whose turn it is: an index into the names, going round
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
        """Ask who plays, then play rounds until nobody wants another; then the golden hen."""
        self._draw_missing(context)
        language = context.language
        context.show(render_game_prompt(_BOOM[language], 0, 0, _CAPTION[language]))
        await self._say(context, "gallina_start", sounds=("intro_gallina",))
        await self._ask_names(context)
        while True:
            name = await self._round(context)
            await self._lay_egg(context, name)
            following = await ask_again(context, self._audio)
            if following == self.info.key:
                continue
            await self._golden_hen(context)
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
        context.services.hint_answers([name.split()[0] for name in known if name.split()])  # the family's names
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
        if not self._names:
            await self._say(context, "gallina_no_names")
            return
        self._eggs = dict.fromkeys(self._names, 0)
        context.show(render_egg_count([(name, 0) for name in self._names], _ORDER[language]))
        order = _listed(self._names, language) + "."
        if self._speech is not None:
            self._speech.prepare(order, language)
        await self._say(context, "gallina_order", _Live(order), "gallina_ready")

    def _prepare_names(self, context: GameContext) -> None:
        if self._speech is None:
            return
        for name in self._names:
            self._speech.prepare(_CALL[context.language].format(name=name), context.language)

    def _pick(self, used: set[str]) -> str:
        """Return a challenge not played yet this game (all of them again once each has been)."""
        fresh = [key for key in CHALLENGES if key not in used] or list(CHALLENGES)
        if len(fresh) == len(CHALLENGES):
            used.clear()
        key = self._rng.choice(fresh)
        used.add(key)
        return key

    def _screen(self, context: GameContext, challenge: Challenge, caption: str) -> bytes:
        es = context.language == "es"
        words_shown = (challenge.label_es or challenge.spanish) if es else (challenge.label_en or challenge.english)
        words_shown = words_shown[:1].upper() + words_shown[1:]
        picture = context.services.picture(challenge.drawing) if context.services.picture else None
        if picture is None:
            return render_game_prompt(words_shown, 0, 0, caption)
        return render_game_picture(picture, words_shown, 0, 0, caption)

    def _holding(self) -> str | None:
        return self._names[self._holder % len(self._names)] if self._names else None

    async def _round(self, context: GameContext) -> str | None:
        """Play turns in order until the secret timer runs out; return who has the hen then (None without names)."""
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
        turn = 0
        try:
            while context.played_s < self._deadline:
                turn += 1
                name = self._holding()
                key = self._pick(self._used)
                challenge = CHALLENGES[key]
                context.progress = f"Turn {turn}: {name or 'next'}, {challenge.english}"
                context.show(self._screen(context, challenge, name or _NEXT[language]))
                lead: list[str | _Live] = []
                if not hurried and context.played_s >= self._hurry_at:
                    hurried = True
                    lead.append("gallina_hurry")
                lead.append(_Live(_CALL[language].format(name=name)) if name is not None else "gallina_next")
                await self._say(context, *lead, f"gallina_do_{key}", sounds=("cluck",))
                if challenge.move:
                    context.services.emotion(challenge.move)
                settle = COPY_SETTLE_S if challenge.move else MIN_TURN_S
                ended = await self._wait_turn(context, TURN_S, until_deadline=True, settle_s=settle)
                if ended == "boom":
                    break
                context.played()
                self._holder += 1  # the hen goes to the next child, even if the timer runs out now
                if context.played_s >= self._deadline:
                    break
                if ended == "timeout":
                    await self._say(context, "gallina_slow")
                elif not hurried and self._rng.random() < GOOD_SHARE:
                    await self._say(context, "gallina_good", sounds=("star",))
        finally:
            music.cancel()
        return self._holding()

    async def _wait_turn(
        self, context: GameContext, seconds: float, *, until_deadline: bool = False, settle_s: float | None = None
    ) -> str:
        """Wait for a pat or "¡listo!" ("done"), ``seconds`` ("timeout"), or the hen going off ("boom").

        Pats and "¡listo!" in the first ``settle_s`` (MIN_TURN_S) are not this turn's.
        """
        touches = context.services.touches
        seen = touches()
        self._done, self._turn_open = False, True
        started = context.played_s
        settle = MIN_TURN_S if settle_s is None else settle_s
        try:
            while True:
                await context.sleep(_TICK_S)
                elapsed = context.played_s - started
                if until_deadline and context.played_s >= self._deadline:
                    return "boom"
                patted = touches() > seen
                if elapsed < settle:
                    # The last child's pat or "¡listo!" (or his own move) is not this one's.
                    seen, self._done = touches(), False
                elif patted or self._done:
                    return "done"
                if elapsed >= seconds:
                    return "timeout"
        finally:
            self._turn_open = False

    def _egg_screen(self, context: GameContext, caption: str) -> bytes:
        return render_egg_count([(name, self._eggs.get(name, 0)) for name in self._names], caption)

    async def _lay_egg(self, context: GameContext, name: str | None) -> None:
        """Crow: whoever has the hen lays an egg (a silly hen move); a pat or "¡listo!" and it is laid."""
        language = context.language
        context.services.emotion("excited")
        context.show(render_game_prompt(_BOOM[language], 0, 0, name or _CAPTION[language]))
        key = self._rng.choice(list(FORFEITS))
        way = FORFEITS[key]
        who: list[str | _Live] = (
            [_Live(_CALL[language].format(name=name)), "gallina_caught"] if name else ["gallina_caught_anyone"]
        )
        await self._say(context, "gallina_boom", *who, sounds=("squawk",))
        context.show(self._screen(context, way, name or _CAPTION[language]))
        context.progress = f"Egg: {name or 'someone'}, {way.english}"
        await self._say(context, f"gallina_forfeit_{key}")
        await self._wait_turn(context, FORFEIT_S)
        context.played()
        if name is not None:
            self._eggs[name] = self._eggs.get(name, 0) + 1
            context.show(self._egg_screen(context, _EGGS[language]))
            self._holder += 1  # the next round starts with the child after them
        await self._say(context, "gallina_laid", sounds=("egg_pop", "cheer"))

    async def _golden_hen(self, context: GameContext) -> None:
        """Name whoever laid the most eggs (all of them on a tie) "la gallina de oro"."""
        most = max(self._eggs.values(), default=0)
        if most == 0:
            return
        language = context.language
        winners = [name for name in self._names if self._eggs.get(name) == most]
        context.show(self._egg_screen(context, _GOLDEN[language]))
        context.progress = f"Golden hen: {len(winners)} with {most} eggs"
        moment = "gallina_golden" if len(winners) == 1 else "gallina_golden_tie"
        named = _CALL[language].format(name=_listed(winners, language))
        await self._say(context, moment, _Live(named), "gallina_golden_after", sounds=("celebrate",))

    def _draw_missing(self, context: GameContext) -> None:
        """Draw the challenges and ways to lay an egg not drawn yet, in the background (once, for every later game)."""
        draw = context.services.draw_pictures
        if draw is None or any(not task.done() for task in _DRAWING):
            return
        subjects = [c.drawing for c in (*CHALLENGES.values(), *FORFEITS.values())]
        task: asyncio.Task[None] = asyncio.get_running_loop().create_task(draw(subjects), name="gallina-pictures")
        _DRAWING.add(task)
        task.add_done_callback(_DRAWING.discard)


_DRAWING: set["asyncio.Task[None]"] = set()
