"""Scribble Rush's race track on the TRMNL (800×480, four greys): a lane of squares per team and a marker on each.

Joining and the winner reuse Big Brain's screens (games/big_brain_screens.py); what Richie saw is a photo screen.
"""

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    WIDTH,
    HEIGHT,
    _LIGHT_GREY,
    _font,
    _fitted,
    _wrapped,
    encode_png,
    _draw_lines,
    _draw_glyphs_centered,
)


def render_track(
    title: str, caption: str, lanes: list[tuple[str, int]], goal: int, *, turn: str | None = None
) -> bytes:
    """Render ``title`` and ``caption`` above a lane per team (name, squares so far) of ``goal`` squares.

    The lane of the team named ``turn`` is boxed; the last square of every lane is the finish, drawn in black.
    """
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    _draw_lines(draw, [title], _fitted(title, WIDTH - 40, 50, 26), left=20, width=WIDTH - 40, top=14, centered=True)
    font = _fitted(caption, WIDTH - 40, 30, 20, bold=False)
    _draw_lines(draw, _wrapped(caption, font, WIDTH - 40, 1), font, left=20, width=WIDTH - 40, top=80, centered=True)
    top, bottom = 140, HEIGHT - 16
    height = min(100, (bottom - top) // max(1, len(lanes)))
    name_width = 130
    square = (WIDTH - 30 - name_width) / goal
    for index, (name, position) in enumerate(lanes):
        y = top + index * height
        middle = y + height / 2
        if name == turn:
            draw.rounded_rectangle((8, y + 4, WIDTH - 8, y + height - 4), radius=10, outline=0, width=3)
        name_font = _fitted(name.upper(), name_width - 50, 28, 14)
        draw.text((20, middle), name.upper(), font=name_font, fill=0, anchor="lm")
        side = min(square - 3, height - 30)
        for step in range(goal):
            x = 10 + name_width + step * square
            box = (x, middle - side / 2, x + side, middle + side / 2)
            last = step == goal - 1
            filled = 0 if last else _LIGHT_GREY if step < position else 255
            draw.rectangle(box, fill=filled, outline=0, width=1)
        radius = side / 2 + 6
        start = 10 + name_width
        x = start + (min(position, goal) - 1) * square + side / 2 if position > 0 else start - radius + 4
        draw.ellipse((x - radius, middle - radius, x + radius, middle + radius), fill=0, outline=255, width=2)
        _draw_glyphs_centered(draw, name[:1].upper(), _font(int(radius * 1.3), bold=True), x, middle, fill=255)
    return encode_png(canvas)
