"""Scribble Rush's race track on the TRMNL (800×480, four greys): a lane of squares per team and its token on each.

Joining and the winner reuse Big Brain's screens (games/big_brain_screens.py); what Richie saw is a photo screen.
"""

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    _GREY,
    WIDTH,
    HEIGHT,
    _LIGHT_GREY,
    _paint,
    _fitted,
    _wrapped,
    encode_png,
    _game_header,
    _fitted_display,
    _draw_glyphs_centered,
)
from reachy_mini_conversation_app.games.big_brain_screens import _pencil, _team_badge


def render_track(
    title: str,
    caption: str,
    lanes: list[tuple[str, int]],
    goal: int,
    *,
    turn: str | None = None,
    tag: str | None = None,
) -> bytes:
    """Render ``title`` and ``caption`` above a lane per team (name, squares so far) of ``goal`` squares.

    Squares already covered are black and the team's token stands on the last of them; the lane of the team named
    ``turn`` is outlined in black, and the last square of every lane is the checkered finish. ``tag`` (the word's
    category while a team draws) is a black label in front of the caption.
    """
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    top = _game_header(canvas, "SCRIBBLE RUSH", _pencil, "checker", height=52) + 6
    _draw_glyphs_centered(
        draw,
        title,
        _fitted_display(title, WIDTH - 40, 50 if tag else 58, 28),
        WIDTH / 2,
        top + 32 if tag else top + 36,
    )
    if tag:
        tag_font = _fitted(tag, (WIDTH - 40) * 2 // 3, 30, 18)
        font = _fitted(caption, (WIDTH - 40) // 3, 26, 16, bold=False)
        tag_width, gap = tag_font.getlength(tag) + 28, 16
        shown = _wrapped(caption, font, WIDTH - 40 - round(tag_width) - gap, 1)[0]
        left = (WIDTH - tag_width - gap - font.getlength(shown)) / 2
        draw.rounded_rectangle((left, top + 60, left + tag_width, top + 98), radius=12, fill=0)
        draw.text((left + tag_width / 2, top + 88), tag, font=tag_font, fill=255, anchor="ms")
        draw.text((left + tag_width + gap, top + 88), shown, font=font, fill=0, anchor="ls")
    else:
        font = _fitted(caption, WIDTH - 40, 28, 18, bold=False)
        shown = _wrapped(caption, font, WIDTH - 40, 1)[0]
        draw.text((WIDTH / 2, top + 88), shown, font=font, fill=0, anchor="ms")
    lanes_top, bottom = top + 104, HEIGHT - 8
    height = min(116, (bottom - lanes_top) // max(1, len(lanes)))
    lanes_top += (bottom - lanes_top - height * len(lanes)) // 2
    token = min(height - 30, 54)
    start = 128 + token  # the names, then a start box for tokens still at square 0
    square = (WIDTH - 30 - start) / goal
    cell = min(height - 44, 46)
    for index, (name, position) in enumerate(lanes):
        y = lanes_top + index * height
        middle = y + height / 2
        lane = (12, y + 5, WIDTH - 12, y + height - 5)
        draw.rounded_rectangle(lane, radius=18, fill=_LIGHT_GREY)
        if name == turn:
            draw.rounded_rectangle(lane, radius=18, outline=0, width=6)
        name_font = _fitted(name.upper(), 100, 28, 14)
        draw.text((28, middle), name.upper(), font=name_font, fill=0, anchor="lm")
        for step in range(goal):
            x = start + step * square
            box = (round(x), round(middle - cell / 2), round(x + square - 4), round(middle + cell / 2))
            if step == goal - 1:
                flag = (box[0], box[1] - 8, box[2] + 4, box[3] + 8)
                _paint(canvas, flag, "checker")
                draw.rectangle(flag, outline=0, width=3)
            elif step < position:
                draw.rectangle(box, fill=0)
            else:
                draw.rectangle(box, fill=255, outline=_GREY, width=2)
        if position > 0:
            x = min(start + (min(position, goal) - 0.5) * square - 2, WIDTH - 22 - token / 2)
        else:
            x = start - token / 2 - 4
        _team_badge(canvas, name, x, middle, token, halo=5)
    return encode_png(canvas)
