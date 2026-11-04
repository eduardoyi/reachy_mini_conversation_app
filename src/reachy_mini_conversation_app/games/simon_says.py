"""Simón dice ("juguemos a Simón dice"): Reachy names a pose, the children do it and hold still.

The poses are still ones Jev can judge from a single camera frame (chosen with the household 2026-10-31: no
sounds or movement). Each is said as "Simón dice: ¡tócate la nariz!" while the screen shows a drawing of a child
doing it (for the 2-year-old) beside the words. For POSE_S of play, camera frames that changed go to Jev ("is a
person in `frame` touching their nose?"); two sure frames out of the last three count, with a star and a cheer.
Nobody loses: when no one does it, Reachy says "¡Así!" and goes on. Poses that need the whole body come after
"¡Da un paso hacia atrás!", and "¡Ahora acércate!" brings them back. No trick rounds (without "Simón dice") yet.
Spanish is Venezuelan. With recorded audio (games/kids_audio.py) the lines are recorded takes with sounds, a
jingle opens the game and bouncy music plays quietly while the children hold a pose.
"""

import random
import asyncio
import logging
from typing import Any, Final
from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image
from numpy.typing import NDArray

from reachy_mini_conversation_app.jev import JevError, sized_jpeg
from reachy_mini_conversation_app.games.base import Game, GameContext
from reachy_mini_conversation_app.games.hunt import (
    STARS,
    THANKS,
    WELL_DONE,
    CHANGED_FRAME,
    CHECK_EVERY_S,
    RECHECK_STILL_S,
    ask_again,
    thumbnail,
    hunt_phrases,
)
from reachy_mini_conversation_app.eink_display import render_game_prompt, render_game_picture
from reachy_mini_conversation_app.games.catalog import SIMON_SAYS
from reachy_mini_conversation_app.games.kids_audio import KidsAudio


logger = logging.getLogger(__name__)

POSES_PER_GAME: Final[int] = 8
# Playing time to do a pose once it has been said.
POSE_S: Final[float] = 8.0
# Untested on real frames: a pose counts at this probability.
MIN_POSE: Final[float] = 0.6


@dataclass(frozen=True)
class Pose:
    """One pose: what Reachy says, what Jev is asked, the drawing shown, and how it is played."""

    spanish: str
    english: str
    looks_like: str  # "touching their nose with a finger", after "Is a person in `frame` ..."
    drawing: str  # what the image model draws (a child doing it)
    body: bool = False  # the whole body must be in view: a step back first
    gesture: str | None = None  # Reachy does it too: "up" or "left" with his head
    label_es: str = ""  # shorter words for the screen, when the spoken ones are long
    label_en: str = ""


def _child(doing: str) -> str:
    return f"a happy young child {doing}, seen from the front"


POSES: Final[dict[str, Pose]] = {
    # Face and head
    "nose": Pose("tócate la nariz", "touch your nose", "touching their nose with a finger", _child("touching their nose with one finger")),
    "head": Pose("tócate la cabeza", "touch your head", "touching the top of their head with a hand", _child("with one hand on top of their head")),
    "ears": Pose("tócate las orejas", "touch your ears", "touching their ears with their hands", _child("touching both ears with their hands")),
    "cover_eyes": Pose("tápate los ojos", "cover your eyes", "covering their eyes with their hands", _child("covering their eyes with both hands")),
    "cover_mouth": Pose("tápate la boca", "cover your mouth", "covering their mouth with a hand", _child("covering their mouth with both hands")),
    "cover_ears": Pose("tápate las orejas", "cover your ears", "covering their ears with their hands", _child("covering both ears with their hands")),
    "cheeks": Pose("pon las manos en los cachetes", "put your hands on your cheeks", "with their hands on their cheeks", _child("with both hands on their cheeks"), label_es="manos en los cachetes", label_en="hands on your cheeks"),
    "mouth_open": Pose("abre la boca bien grande", "open your mouth wide", "with their mouth wide open", _child("with their mouth wide open")),
    "smile": Pose("sonríe bien grande", "give a big smile", "smiling widely", _child("with a very big smile")),
    "surprised": Pose("pon cara de sorpresa", "make a surprised face", "making a surprised face", _child("with a surprised face, eyebrows up and mouth open")),
    "angry": Pose("pon cara de bravo", "make an angry face", "making an angry face, frowning", _child("pretending to be angry, frowning")),
    "sad": Pose("pon cara triste", "make a sad face", "making a sad face", _child("pretending to be sad, with a pout")),
    "tongue": Pose("saca la lengua", "stick out your tongue", "sticking out their tongue", _child("sticking out their tongue")),
    "eyes_closed": Pose("cierra los ojos", "close your eyes", "with their eyes closed", _child("with their eyes closed")),
    "tilt": Pose("inclina la cabeza", "tilt your head", "tilting their head to one side", _child("tilting their head to one side")),
    "look_up": Pose("mira hacia arriba", "look up", "looking up at the ceiling", _child("looking straight up"), gesture="up"),
    "look_side": Pose("mira hacia un lado", "look to the side", "looking to one side, head turned", _child("with their head turned to look to the side"), gesture="left"),
    "fish_face": Pose("pon cara de pez", "make a fish face", "making a fish face: cheeks sucked in, lips pushed out", _child("making a fish face, cheeks sucked in and lips pushed out")),
    "pufferfish": Pose("infla los cachetes como un pez globo", "puff up your cheeks like a pufferfish", "with their cheeks puffed full of air", _child("with cheeks puffed up full of air like a pufferfish"), label_es="cachetes de pez globo", label_en="pufferfish cheeks"),
    "sleep": Pose("hazte el dormido", "pretend to sleep", "pretending to sleep: eyes closed, head resting on their hands by one cheek", _child("pretending to sleep, eyes closed, head resting on both hands by one cheek")),
    # Arms
    "arms_up": Pose("levanta los brazos", "put both arms up", "with both arms raised above their head", _child("with both arms raised high")),
    "one_arm": Pose("levanta un brazo", "put one arm up", "with exactly one arm raised above their head", _child("with one arm raised high")),
    "plane": Pose("haz el avión", "be an airplane", "with both arms stretched out to the sides like airplane wings", _child("with both arms stretched out to the sides like airplane wings")),
    "crossed": Pose("cruza los brazos", "cross your arms", "with their arms crossed over their chest", _child("with arms crossed")),
    "shoulders": Pose("pon las manos en los hombros", "put your hands on your shoulders", "with their hands on their shoulders", _child("with both hands on their shoulders"), label_es="manos en los hombros", label_en="hands on your shoulders"),
    "behind_head": Pose("pon las manos detrás de la cabeza", "put your hands behind your head", "with their hands behind their head, elbows out", _child("with both hands behind their head"), label_es="manos detrás de la cabeza", label_en="hands behind your head"),
    "muscles": Pose("saca músculos", "show your muscles", "flexing their arm muscles like a strongman", _child("flexing both arms to show their muscles")),
    "hug": Pose("abrázate", "hug yourself", "hugging themselves with both arms", _child("hugging themselves")),
    "hands_together": Pose("junta las manos", "put your hands together", "with their palms pressed together in front of them", _child("with palms pressed together in front of the chest")),
    "zombie": Pose("pon los brazos hacia adelante, como un zombi", "arms forward like a zombie", "with both arms stretched straight forward like a sleepwalker", _child("with both arms stretched straight forward like a friendly zombie"), label_es="brazos de zombi", label_en="zombie arms"),
    "trex": Pose("haz de dinosaurio T-rex", "be a T-rex", "pretending to be a T-rex: elbows tucked in, short arms with claw hands in front, mouth open in a roar", _child("pretending to be a T-rex, elbows tucked in, little claw hands in front, roaring"), label_es="T-rex", label_en="T-rex"),
    "trunk": Pose("haz una trompa de elefante con el brazo", "make an elephant trunk with your arm", "with one arm hanging down from their nose like an elephant's trunk", _child("with one arm hanging down from the nose like an elephant's trunk"), label_es="trompa de elefante", label_en="elephant trunk"),
    "fly": Pose("vuela como superhéroe", "fly like a superhero", "with one fist stretched straight up high like a flying superhero", _child("with one fist stretched straight up high like a flying superhero")),
    # Hands and fingers
    "thumbs_up": Pose("pon el pulgar arriba", "thumbs up", "giving a thumbs up", _child("giving a thumbs up"), label_es="pulgar arriba"),
    "show_hands": Pose("enséñame las manos", "show me your hands", "showing both open hands, palms to the camera", _child("showing both open hands, palms forward")),
    "heart": Pose("haz un corazón con las manos", "make a heart with your hands", "making a heart shape with their hands", _child("making a heart shape with both hands"), label_es="un corazón con las manos", label_en="a heart with your hands"),
    "hello": Pose("saluda con la mano abierta", "say hi with an open hand", "holding up an open hand as if saying hello", _child("holding up one open hand to say hello"), label_es="saluda con la mano", label_en="say hi with your hand"),
    "glasses": Pose("haz unos lentes con los dedos", "make glasses with your fingers", "making circles with their fingers around their eyes like glasses", _child("making circles with their fingers around their eyes like glasses"), label_es="lentes con los dedos", label_en="finger glasses"),
    "horns": Pose("haz cachos de toro", "make bull horns", "holding their fingers up on their head like horns", _child("with index fingers pointing up from their head like bull horns")),
    "one_finger": Pose("enséñame un dedo", "show me one finger", "holding up exactly one finger", _child("holding up exactly one finger")),
    "two_fingers": Pose("enséñame dos dedos", "show me two fingers", "holding up exactly two fingers", _child("holding up exactly two fingers")),
    "three_fingers": Pose("enséñame tres dedos", "show me three fingers", "holding up exactly three fingers", _child("holding up exactly three fingers")),
    "peace": Pose("haz el signo de la paz", "make a peace sign", "making a peace sign (a V) with their fingers", _child("making a peace sign with two fingers")),
    "bunny": Pose("haz como un conejo, con las manos en la cabeza como orejas", "be a bunny, with your hands on your head as ears", "holding their hands up on their head like bunny ears", _child("holding both hands on top of the head like bunny ears"), label_es="orejas de conejo", label_en="bunny ears"),
    "spider": Pose("haz con las manos como Spider-Man", "make Spider-Man hands", "making Spider-Man's web-shooting hand: palm out, middle and ring fingers folded down, the other fingers straight", _child("holding out one hand like Spider-Man shooting a web, middle and ring fingers folded down"), label_es="manos de Spider-Man", label_en="Spider-Man hands"),
    "whiskers": Pose("hazte bigotes de gato con los dedos", "make cat whiskers with your fingers", "with spread fingers across their cheeks like cat whiskers", _child("with spread fingers across both cheeks like cat whiskers"), label_es="bigotes de gato", label_en="cat whiskers"),
    # Body: a step back first
    "crouch": Pose("agáchate", "crouch down", "crouching down low", _child("crouching down low"), body=True),
    "sit": Pose("siéntate en el piso", "sit on the floor", "sitting on the floor", _child("sitting cross-legged on the floor"), body=True),
    "one_foot": Pose("párate en un pie", "stand on one foot", "standing on one foot with the other foot lifted", _child("standing on one foot"), body=True),
    "hips": Pose("pon las manos en la cintura", "put your hands on your hips", "with their hands on their hips", _child("standing with both hands on the hips"), body=True, label_es="manos en la cintura", label_en="hands on your hips"),
    "knees": Pose("tócate las rodillas", "touch your knees", "bending down to touch their knees", _child("bending to touch the knees"), body=True),
    "toes": Pose("tócate los pies", "touch your toes", "bending down to touch their feet", _child("bending down to touch the toes"), body=True),
    "back": Pose("voltéate de espaldas", "turn your back to me", "standing with their back to the camera", "a young child standing with their back to us", body=True),
    "star": Pose("haz una estrella", "be a star", "standing with arms and legs spread wide like a star", _child("standing with arms and legs spread wide like a star"), body=True),
    "statue": Pose("quédate como una estatua", "freeze like a statue", "standing frozen in a pose like a statue", _child("frozen like a statue in a funny pose"), body=True),
    "superhero": Pose("haz la pose de superhéroe", "do a superhero pose", "standing in a superhero pose: fists on hips, chest out", _child("standing proudly in a superhero pose, fists on hips, chest out"), body=True, label_es="pose de superhéroe", label_en="superhero pose"),
    "flamingo": Pose("haz de flamenco: en un pie, con los brazos abiertos", "be a flamingo: on one foot, arms out", "standing on one foot with both arms stretched out to the sides like a flamingo", _child("standing on one foot with both arms out like a flamingo"), body=True, label_es="flamenco", label_en="flamingo"),
}  # fmt: skip

_START: Final[dict[str, str]] = {"es": "¡Vamos a jugar a Simón dice!", "en": "Let's play Simon says!"}
_SAYS: Final[dict[str, str]] = {"es": "Simón dice: ¡{pose}!", "en": "Simon says: {pose}!"}
_STEP_BACK: Final[dict[str, str]] = {"es": "¡Da un paso hacia atrás!", "en": "Take a step back!"}
_COME_CLOSER: Final[dict[str, str]] = {"es": "¡Ahora acércate!", "en": "Now come closer!"}
_PRAISE: Final[dict[str, tuple[str, ...]]] = {
    "es": ("¡Muy bien!", "¡Eso!", "¡Chévere!", "¡Perfecto!"),
    "en": ("Great!", "Yes!", "Nice one!", "Perfect!"),
}
_LIKE_THIS: Final[dict[str, str]] = {"es": "¡Así! Mira la pantalla.", "en": "Like this! Look at the screen."}
_CAPTION: Final[dict[str, str]] = {"es": "Simón dice…", "en": "Simon says…"}


def simon_says_phrases() -> frozenset[str]:
    """Every line Simón dice says, in both languages, for the speech cache."""
    lines: set[str] = set(hunt_phrases())
    for language in ("es", "en"):
        lines |= {_START[language], _STEP_BACK[language], _COME_CLOSER[language], _LIKE_THIS[language]}
        lines |= set(_PRAISE[language])
        for pose in POSES.values():
            lines.add(_SAYS[language].format(pose=pose.spanish if language == "es" else pose.english))
    return frozenset(lines)


def pose_drawings() -> list[str]:
    """Return the drawing subjects to make once and keep."""
    return [pose.drawing for pose in POSES.values()]


_DRAWING: set["asyncio.Task[None]"] = set()


class SimonSays(Game):
    """Eight poses a game: say it, show it, look for it."""

    info = SIMON_SAYS

    def __init__(self, rng: random.Random | None = None, audio: KidsAudio | None = None) -> None:
        """Pick poses with ``rng`` (seeded in tests); speak with ``audio`` when it is given."""
        self._rng = rng or random.Random()
        self._audio = audio
        self._stars = 0

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line, for the speech cache."""
        return simon_says_phrases()

    def poses(self) -> list[str]:
        """Every pose key (tests narrow it)."""
        return list(POSES)

    async def _say(self, context: GameContext, moment: str, text: str, *, sounds: tuple[str, ...] = ()) -> None:
        """Say a recorded take of ``moment`` with ``sounds``; ``text`` in Richie's own voice without audio."""
        if self._audio is None:
            await context.say(text)
        else:
            await self._audio.say(context, moment, sounds=sounds)

    async def play(self, context: GameContext) -> None:
        """Play sets of eight poses until nobody wants another (or another kids' game is asked for)."""
        self._draw_missing(context)
        language = context.language
        audio = self._audio
        await self._say(context, "simon_start", _START[language], sounds=("intro_simon",))
        while True:
            self._stars = 0
            chosen = self._rng.sample(self.poses(), min(POSES_PER_GAME, len(self.poses())))
            # The close-up poses first, then the whole-body ones: one step back per game, not one per pose.
            chosen.sort(key=lambda key: POSES[key].body)
            back = False
            for number, key in enumerate(chosen, start=1):
                pose = POSES[key]
                if pose.body and not back:
                    await self._say(context, "simon_step_back", _STEP_BACK[language], sounds=("tiptoe",))
                    back = True
                if await self._pose(context, key, number, len(chosen)):
                    self._stars += 1
            context.show(render_game_prompt(WELL_DONE[language], self._stars, len(chosen)))
            celebrate = ("celebrate",) if self._stars else ()
            await self._say(context, f"stars_{self._stars}", STARS[language][self._stars], sounds=celebrate)
            if back:
                # Back in close for "¿Otra vez?"
                await self._say(context, "simon_come_closer", _COME_CLOSER[language])
            following = await ask_again(context, audio)
            if following == self.info.key:
                continue
            context.next_game = following
            if following is None:
                await self._say(context, "thanks", THANKS[language])
            return

    def _draw_missing(self, context: GameContext) -> None:
        """Draw the poses not drawn yet in the background; until then a pose shows its words alone."""
        draw = context.services.draw_pictures
        if draw is None or any(not task.done() for task in _DRAWING):
            return
        task: asyncio.Task[None] = asyncio.get_running_loop().create_task(draw(pose_drawings()), name="simon-pictures")
        _DRAWING.add(task)  # kept past the game: the drawings are made once, for every later game
        task.add_done_callback(_DRAWING.discard)

    def _screen(self, context: GameContext, key: str, stars: int, total: int) -> bytes:
        pose = POSES[key]
        words = (pose.label_es or pose.spanish) if context.language == "es" else (pose.label_en or pose.english)
        words = words[:1].upper() + words[1:]
        picture = context.services.picture(pose.drawing) if context.services.picture else None
        if picture is None:
            return render_game_prompt(words, stars, total, _CAPTION[context.language])
        return render_game_picture(picture, words, stars, total, _CAPTION[context.language])

    async def _pose(self, context: GameContext, key: str, number: int, total: int) -> bool:
        """One pose: say it (Reachy doing it too where he can), then look for it for POSE_S."""
        language = context.language
        pose = POSES[key]
        context.progress = f"Pose {number} of {total}: {pose.english}"
        context.show(self._screen(context, key, self._stars, total))
        says = _SAYS[language].format(pose=pose.spanish if language == "es" else pose.english)
        await self._say(context, f"simon_{key}", says, sounds=("round",))
        if pose.gesture is not None and context.services.gesture is not None:
            await context.services.gesture(pose.gesture)
        started = context.played_s
        music = None
        if self._audio is not None:
            music = asyncio.get_running_loop().create_task(
                self._audio.play_music(context, lambda _: "pose", lambda: context.played_s - started >= POSE_S),
                name="simon-music",
            )
        try:
            done = await self._look(context, key)
        finally:
            if music is not None:
                music.cancel()
            if pose.gesture is not None and context.services.gesture is not None:
                await context.services.gesture("front")
        if done:
            context.played()
            context.services.emotion("happy")
            praise = self._rng.choice(_PRAISE[language])
            if self._audio is None:
                await context.say(praise)
            else:
                await self._audio.say(context, "simon_praise", sounds=("snap",), after=("star",))
            context.show(self._screen(context, key, self._stars + 1, total))
            await context.sleep(1.0)
            return True
        await self._say(context, "simon_like_this", _LIKE_THIS[language], sounds=("boing",))
        await context.sleep(1.5)
        return False

    async def _look(self, context: GameContext, key: str) -> bool:
        """Check camera frames until two of the last three show the pose; False after POSE_S."""
        question = {
            "pose": {
                "type": "noul",
                "instructions": f"Is a person in `frame` {POSES[key].looks_like}?",
            }
        }
        recent: deque[bool] = deque(maxlen=3)
        last_small: NDArray[np.float32] | None = None
        last_checked_at = -RECHECK_STILL_S
        started = context.played_s
        while context.played_s - started < POSE_S:
            await context.sleep(CHECK_EVERY_S)
            frame = await asyncio.to_thread(context.services.camera)
            decoded = await asyncio.to_thread(thumbnail, frame) if frame else None
            if decoded is None:
                continue
            picture, small = decoded
            still = last_small is not None and float(np.abs(small - last_small).mean()) < CHANGED_FRAME
            if still and context.played_s - last_checked_at < RECHECK_STILL_S:
                continue
            last_small, last_checked_at = small, context.played_s
            probability = await self._check(context, picture, question)
            if probability is None:
                continue
            logger.debug("Simón dice %s: %.2f", key, probability)
            recent.append(probability >= MIN_POSE)
            if sum(recent) >= 2:
                logger.info("Simón dice: %s done (%.2f)", key, probability)
                return True
        return False

    async def _check(self, context: GameContext, picture: Image.Image, question: dict[str, Any]) -> float | None:
        """Ask Jev whether the frame shows the pose; None when it cannot answer."""
        jev = context.services.jev
        if jev is None:
            return None
        jpeg = await asyncio.to_thread(sized_jpeg, picture)
        try:
            answers = await jev({}, question, {"frame": jpeg})
            return float(answers["pose"]["noul"])
        except (JevError, KeyError, TypeError, ValueError) as error:
            logger.info("Pose check failed: %s", type(error).__name__)
            return None
