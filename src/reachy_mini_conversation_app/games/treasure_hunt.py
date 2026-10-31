"""The treasure hunt ("juguemos a buscar tesoros"): find the thing Reachy names around the house and show it.

Things at home (a ball, a shoe, a Lego) and simple groups (an animal, a fruit, something round), chosen with
the household on 2026-10-31; no letters and no combinations yet. The screen shows a drawing of the thing for the
2-year-old, who cannot read yet, beside the word for the 4-year-old. The drawings are made once by the image
model and kept (GAME_PICTURES in eink_display.py). Jev is asked whether the object held up is that thing; when
someone surely shows something else, Reachy says "Mmm, eso no" and the hunt goes on. Rounds last 60 s.
"""

import asyncio
from typing import Any, Final
from dataclasses import dataclass

from reachy_mini_conversation_app.games import hunt
from reachy_mini_conversation_app.games.base import GameContext
from reachy_mini_conversation_app.games.hunt import HELD_QUESTION, Hunt, Check, hunt_phrases
from reachy_mini_conversation_app.eink_display import render_game_picture
from reachy_mini_conversation_app.games.catalog import TREASURE_HUNT


ROUND_S: Final[float] = 60.0
# Untested on real frames: a thing counts at this probability; a frame held up below MAX_ITEM_FOR_WRONG is "eso no".
MIN_ITEM: Final[float] = 0.6
MAX_ITEM_FOR_WRONG: Final[float] = 0.15
MIN_HELD_FOR_WRONG: Final[float] = 0.7
_DRAWING: set["asyncio.Task[None]"] = set()


@dataclass(frozen=True)
class Treasure:
    """One thing to find: its names, what Jev is asked about, and the drawing shown (None: the word alone)."""

    spanish: str
    english: str
    looks_like: str
    drawing: str | None


TREASURES: Final[dict[str, Treasure]] = {
    "ball": Treasure("una pelota", "a ball", "a ball of any kind", "a ball"),
    "shoe": Treasure("un zapato", "a shoe", "a shoe, slipper, sandal or boot", "a shoe"),
    "sock": Treasure("una media", "a sock", "a sock", "a sock"),
    "book": Treasure("un libro", "a book", "a book, of any size", "a children's picture book"),
    "plush": Treasure("un peluche", "a stuffed toy", "a stuffed toy or plush animal", "a teddy bear"),
    "toy_car": Treasure("un carrito", "a toy car", "a toy car, truck or other toy vehicle", "a toy car"),
    "spoon": Treasure("una cuchara", "a spoon", "a spoon", "a spoon"),
    "cup": Treasure("un vaso", "a cup", "a cup, mug or drinking glass", "a cup"),
    "hat": Treasure("una gorra", "a cap", "a cap or hat", "a baseball cap"),
    "doll": Treasure("una muñeca", "a doll", "a doll or action figure", "a doll"),
    "lego": Treasure("un Lego", "a Lego", "a Lego or Duplo brick, or something built from them", "a Lego brick"),
    "crayon": Treasure(
        "un creyón o un lápiz",
        "a crayon or a pencil",
        "a crayon, marker, colored pencil or pencil",
        "a crayon and a pencil",
    ),  # fmt: skip
    "animal": Treasure(
        "un animal",
        "an animal",
        "an animal: a toy animal, a stuffed animal or a picture of one",
        "a toy dog, a toy cat and a toy duck",
    ),  # fmt: skip
    "fruit": Treasure("una fruta", "a fruit", "a fruit or vegetable, real or toy", "an apple, a banana and a pear"),
    "wheels": Treasure("algo con ruedas", "something with wheels", "something with wheels", "a wheel"),
    "round": Treasure("algo redondo", "something round", "something round or ball-shaped", "a circle"),
    "square": Treasure("algo cuadrado", "something square", "something square or box-shaped", "a square box"),
    "soft": Treasure(
        "algo blando",
        "something soft",
        "something soft, like a cushion, a blanket or a stuffed toy",
        "a fluffy cushion",
    ),  # fmt: skip
    "letters": Treasure("algo con letras", "something with letters", "something with letters or writing on it", None),
    "small": Treasure(
        "algo más pequeño que tu mano",
        "something smaller than your hand",
        "an object smaller than the hand holding it",
        None,
    ),  # fmt: skip
}

_LINES: Final[dict[str, dict[str, str]]] = {
    "start": {"es": "¡Vamos a buscar tesoros!", "en": "Let's go on a treasure hunt!"},
    "find": {"es": "¡Busca {item}!", "en": "Find {item}!"},
    "found": {"es": "¡Sí! ¡{item}!", "en": "Yes! {item}!"},
    "wrong": {"es": "Mmm, eso no. ¡Busca {item}!", "en": "Hmm, not that one. Find {item}!"},
    "time_up": {"es": "¡Se acabó el tiempo! Era {item}.", "en": "Time's up! It was {item}."},
}


def _name(item: str, language: str) -> str:
    treasure = TREASURES[item]
    return treasure.spanish if language == "es" else treasure.english


def _line(kind: str, item: str, language: str) -> str:
    name = _name(item, language) if item else ""
    if kind == "found":
        name = name[:1].upper() + name[1:]
    return _LINES[kind][language].format(item=name)


def treasure_hunt_phrases() -> frozenset[str]:
    """Every line the treasure hunt says, in both languages, for the speech cache."""
    lines: set[str] = set(hunt_phrases())
    for language in ("es", "en"):
        lines.add(_line("start", "", language))
        for item in TREASURES:
            lines |= {_line(kind, item, language) for kind in ("find", "found", "wrong", "time_up")}
    return frozenset(lines)


def treasure_drawings() -> list[str]:
    """Return the drawing subjects to make once and keep (in English, as the image model is asked)."""
    return [treasure.drawing for treasure in TREASURES.values() if treasure.drawing is not None]


class TreasureHunt(Hunt):
    """Five rounds of finding things around the house."""

    info = TREASURE_HUNT
    round_s = ROUND_S

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line, for the speech cache."""
        return treasure_hunt_phrases()

    def targets(self) -> list[str]:
        """Every thing to find."""
        return list(TREASURES)

    def questions(self, target: str) -> dict[str, dict[str, Any]]:
        """Held up, and whether it is the thing asked for."""
        return {
            "held": HELD_QUESTION,
            "item": {
                "type": "noul",
                "instructions": (
                    "Is the object someone is holding up or showing to the camera in `frame` "
                    f"{TREASURES[target].looks_like}?"
                ),
            },
        }

    def judge(self, answers: dict[str, dict[str, Any]], target: str) -> Check:
        """Count it when Jev is fairly sure it is the thing; surely held up and surely not it is "eso no"."""
        held = float(answers["held"]["noul"])
        item = float(answers["item"]["noul"])
        match = item >= MIN_ITEM
        return Check(
            held=held,
            seen=target if match else "something else",
            match=match,
            wrong=held >= MIN_HELD_FOR_WRONG and item <= MAX_ITEM_FOR_WRONG,
            probability=item,
        )

    def line(self, kind: str, target: str, language: str, seen: str = "") -> str:
        """Return what Reachy says."""
        return _line(kind, target, language)

    def name(self, target: str, language: str) -> str:
        """Return the thing's name."""
        return _name(target, language)

    def prepare(self, context: GameContext) -> None:
        """Draw the pictures not made yet in the background; rounds before they are ready show the word alone."""
        draw = context.services.draw_pictures
        if draw is None or any(not task.done() for task in _DRAWING):
            return
        task: asyncio.Task[None] = asyncio.get_running_loop().create_task(
            draw(treasure_drawings()), name="treasure-pictures"
        )
        _DRAWING.add(task)  # kept past the game: the drawings are made once, for every later game
        task.add_done_callback(_DRAWING.discard)

    async def screen(self, context: GameContext, target: str, stars: int) -> bytes:
        """Return the thing's drawing beside its name; the name alone when there is no drawing (yet)."""
        subject = TREASURES[target].drawing
        picture = context.services.picture(subject) if subject and context.services.picture else None
        if picture is None:
            return await super().screen(context, target, stars)
        caption = "Busca…" if context.language == "es" else "Find…"
        return render_game_picture(picture, self.name(target, context.language).upper(), stars, hunt.ROUNDS, caption)
