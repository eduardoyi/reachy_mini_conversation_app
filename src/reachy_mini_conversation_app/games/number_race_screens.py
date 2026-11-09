"""The number race's screen: two straight roads of squares 1 to 10, Tito's over Flecha's, each car over its square.

Both roads line up, so the car farther along is on the bigger number. The squares being counted are grey. The
cars are Tito and Flecha as drawn in the story La gran carrera, shrunk from their character sheets by
scripts/race_cars.py (no image calls during a game).
"""

import functools
from typing import Final
from pathlib import Path
from collections.abc import Collection

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    WIDTH,
    HEIGHT,
    _LIGHT_GREY,
    _paint,
    _fitted,
    encode_png,
    _kids_frame,
    _display_font,
    _draw_glyphs_centered,
)


TRACK: Final[int] = 10
TITO: Final[str] = "Tito"
FLECHA: Final[str] = "Flecha"
_START_LEFT, _START_WIDTH = 30, 82  # the start box, with the car's name
_SQUARES_LEFT, _SQUARE = 118, 63  # ten squares, then the checkered finish
_FINISH_WIDTH = 22
# Each lane: where its car's wheels stand, and its squares under them.
_LANES: Final[dict[str, tuple[int, int]]] = {TITO: (160, 166), FLECHA: (346, 352)}
_SQUARE_HEIGHT = 70


def _middle(square: int) -> float:
    """Return the x of square ``square``'s middle (0 is the start box)."""
    if square <= 0:
        return _START_LEFT + _START_WIDTH / 2
    return _SQUARES_LEFT + (square - 0.5) * _SQUARE


_CARS: Final[Path] = Path(__file__).resolve().parent.parent / "static" / "eink"
_FRAME_INSIDE = 22  # how close to the screen's sides a car may come, inside the frame


@functools.cache
def _car(car: str) -> Image.Image:
    """Return ``car`` as drawn in the story (scripts/race_cars.py), facing the finish, transparent around it."""
    return Image.open(_CARS / f"race_{car.lower()}.png").convert("LA")


def _put_car(canvas: Image.Image, draw: ImageDraw.ImageDraw, car: str, square: int, ground: int) -> None:
    """Put ``car`` with its wheels on ``ground`` over square ``square``; speed lines trail Flecha once it moves."""
    picture = _car(car)
    middle = _middle(min(square, TRACK))
    left = round(min(max(middle - picture.width / 2, _FRAME_INSIDE), WIDTH - _FRAME_INSIDE - picture.width))
    top = ground - picture.height
    if car == FLECHA and square > 0:
        for row, length in ((0.3, 22), (0.5, 30), (0.7, 18)):
            y = top + picture.height * row
            draw.line((left - 6 - length, y, left - 4, y), fill=0, width=4)
    grey, alpha = picture.split()
    canvas.paste(grey, (left, top), alpha)


def _lane(canvas: Image.Image, draw: ImageDraw.ImageDraw, car: str, square: int, counting: Collection[int]) -> None:
    """Draw one road: the start box with the car's name, squares 1 to 10, the finish, and the car on ``square``."""
    ground, top = _LANES[car]
    bottom = top + _SQUARE_HEIGHT
    draw.rounded_rectangle((_START_LEFT, top, _START_LEFT + _START_WIDTH, bottom), radius=12, fill=0)
    name_font = _fitted(car, _START_WIDTH - 10, 30, 18)
    _draw_glyphs_centered(draw, car, name_font, _START_LEFT + _START_WIDTH / 2, (top + bottom) / 2, fill=255)
    number_font = _display_font(40)
    for number in range(1, TRACK + 1):
        left = _SQUARES_LEFT + (number - 1) * _SQUARE
        box = (left, top, left + _SQUARE, bottom)
        draw.rectangle(box, fill=_LIGHT_GREY if number in counting else 255, outline=0, width=4)
        _draw_glyphs_centered(draw, str(number), number_font, left + _SQUARE / 2, (top + bottom) / 2)
    finish = _SQUARES_LEFT + TRACK * _SQUARE
    _paint(canvas, (finish, top, finish + _FINISH_WIDTH, bottom), "checker")
    draw.rectangle((finish, top, finish + _FINISH_WIDTH, bottom), outline=0, width=3)
    _put_car(canvas, draw, car, square, ground)


def render_number_race(
    tito: int, flecha: int, caption: str, counting: tuple[str, Collection[int]] = ("", ())
) -> bytes:
    """Render the race: Tito on square ``tito`` and Flecha on ``flecha`` (0 is the start), ``caption`` on the tab.

    ``counting`` is a car and the squares it is moving over, shown grey.
    """
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    _kids_frame(draw, caption, WIDTH / 2)
    counted_car, squares = counting
    _lane(canvas, draw, TITO, tito, squares if counted_car == TITO else ())
    _lane(canvas, draw, FLECHA, flecha, squares if counted_car == FLECHA else ())
    draw.line((40, 262, WIDTH - 40, 262), fill=_LIGHT_GREY, width=4)  # between the two roads
    return encode_png(canvas)
