"""Sheeple's TRMNL board (800×480, four greys): the survey question and its answers, hidden until revealed.

Joining and the winner use Big Brain's screens (``render_join`` with the Sheeple title, ``render_winner``), and the
team score tiles are Big Brain's too.
"""

from typing import Final

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    _GREY,
    WIDTH,
    HEIGHT,
    _font,
    _fitted,
    _wrapped,
    encode_png,
    _draw_lines,
    _game_header,
    _display_font,
    _fitted_display,
    _draw_glyphs_centered,
)
from reachy_mini_conversation_app.games.big_brain_screens import _sheep, _scores


_TEXT: Final[dict[str, dict[str, str]]] = {
    "misses": {"es": "Fallos", "en": "Misses"},
    "turn": {"es": "Turno", "en": "Turn"},
}
_MISSES: Final[int] = 3  # the game's limit (sheeple.MISSES), one box each

# One answer slot: its text and count once revealed, None while hidden.
Slot = tuple[str, int] | None


def _misses(draw: ImageDraw.ImageDraw, misses: int, label: str, box: tuple[int, int, int, int]) -> None:
    """Draw ``label`` over a box per miss allowed, the first ``misses`` crossed out, inside ``box``."""
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=16, outline=0, width=4)
    draw.text(((left + right) / 2, top + 26), label.upper(), font=_display_font(22), fill=0, anchor="ms")
    side, gap = 30, 8
    first = (left + right - _MISSES * side - (_MISSES - 1) * gap) / 2
    y = top + 36
    for index in range(_MISSES):
        x = first + index * (side + gap)
        if index < misses:
            draw.rectangle((x, y, x + side, y + side), fill=0)
            reach = side * 0.26
            middle_x, middle_y = x + side / 2, y + side / 2
            for sign in (-1, 1):
                draw.line((middle_x - reach, middle_y - sign * reach, middle_x + reach, middle_y + sign * reach), fill=255, width=4)  # fmt: skip
        else:
            draw.rectangle((x, y, x + side, y + side), outline=_GREY, width=3)


def render_survey(
    question: str,
    answers: list[Slot],
    round_label: str,
    scores: list[tuple[str, int]],
    language: str,
    *,
    turn: str | None = None,
    misses: int | None = None,
) -> bytes:
    """Render a board: the round and question on top, a slot per answer (two columns), the scores below."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    top = _game_header(canvas, "SHEEPLE", _sheep, "wool", status=round_label, height=48) + 2
    for size in range(34, 18, -2):
        font = _font(size, bold=True)
        lines = _wrapped(question, font, WIDTH - 40, 3)
        if not lines[-1].endswith("…"):
            break
    top = _draw_lines(draw, lines, font, left=20, width=WIDTH - 40, top=top, centered=True) + 6
    footer = HEIGHT - 84 if scores or misses is not None else HEIGHT - 8
    rows = (len(answers) + 1) // 2
    gap = 7
    height = min(62, (footer - 22 - top - gap * (rows - 1)) // max(rows, 1))  # room for the turn tag
    top += (footer - 22 - top - rows * height - (rows - 1) * gap) // 2
    width = (WIDTH - 36) // 2
    disc = height - 14
    for index, slot in enumerate(answers):
        column, row = (0, index) if index < rows else (1, index - rows)
        x, y = 12 + column * (width + 12), top + row * (height + gap)
        middle = y + height / 2
        number = str(index + 1)
        if slot is None:
            draw.rounded_rectangle((x, y, x + width, y + height), radius=12, fill=_GREY)
            draw.ellipse((x + width / 2 - disc / 2, middle - disc / 2, x + width / 2 + disc / 2, middle + disc / 2), fill=255)  # fmt: skip
            _draw_glyphs_centered(draw, number, _display_font(round(disc * 0.7)), x + width / 2, middle)
            continue
        text, count = slot
        draw.rounded_rectangle((x, y, x + width, y + height), radius=12, fill=255, outline=0, width=4)
        draw.rounded_rectangle((x + width - 70, y, x + width, y + height), radius=12, fill=0)
        draw.rectangle((x + width - 70, y, x + width - 58, y + height), fill=0)
        _draw_glyphs_centered(draw, str(count), _fitted_display(str(count), 56, round(height * 0.62), 20), x + width - 35, middle, fill=255)  # fmt: skip
        draw.ellipse((x + 8, middle - disc / 2, x + 8 + disc, middle + disc / 2), fill=0)
        _draw_glyphs_centered(draw, number, _display_font(round(disc * 0.7)), x + 8 + disc / 2, middle, fill=255)
        room = width - disc - 98
        label_font = _fitted(text, room, 28, 22)
        lines = _wrapped(text, label_font, room, 1 if label_font.getlength(text) <= room else 2)
        if len(lines) > 1:  # a long answer: two smaller lines
            label_font = _font(min(22, int((height - 6) / 2.3)), bold=True)
            lines = _wrapped(text, label_font, room, 2)
        _draw_lines(draw, lines, label_font, left=x + disc + 18, width=room, top=round(middle - len(lines) * label_font.size * 0.6))  # fmt: skip
    right = WIDTH
    if misses is not None:
        right = WIDTH - 210
        _misses(draw, misses, _TEXT["misses"][language], (right, HEIGHT - 84, WIDTH - 12, HEIGHT - 8))
    _scores(canvas, scores, HEIGHT - 84, highlight=(turn,) if turn else (), tag=_TEXT["turn"][language], height=76, right=right)  # fmt: skip
    return encode_png(canvas)
