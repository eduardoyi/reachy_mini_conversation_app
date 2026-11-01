"""Sheeple's TRMNL board (800×480, four greys): the survey question and its answers, hidden until revealed.

Joining and the winner use Big Brain's screens (``render_join`` with the Sheeple title, ``render_winner``).
"""

from typing import Final

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    _GREY,
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


_TEXT: Final[dict[str, dict[str, str]]] = {
    "misses": {"es": "Fallos", "en": "Misses"},
    "turn": {"es": "Turno", "en": "Turn"},
}

# One answer slot: its text and count once revealed, None while hidden.
Slot = tuple[str, int] | None


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
    _draw_lines(draw, [round_label.upper()], _font(20, bold=True), left=20, width=WIDTH - 40, top=8, centered=True)
    for size in range(32, 18, -2):
        font = _font(size, bold=True)
        lines = _wrapped(question, font, WIDTH - 40, 3)
        if not lines[-1].endswith("…"):
            break
    _draw_lines(draw, lines, font, left=20, width=WIDTH - 40, top=36, centered=True)
    top = 36 + 3 * int(32 * 1.2) + 6
    rows = (len(answers) + 1) // 2
    height = min(58, (392 - top) // max(rows, 1) - 6)
    width = (WIDTH - 60) // 2
    for index, slot in enumerate(answers):
        column, row = (0, index) if index < rows else (1, index - rows)
        x, y = 20 + column * (width + 20), top + row * (height + 6)
        if slot is None:
            draw.rectangle((x, y, x + width, y + height), fill=_LIGHT_GREY)
            _draw_glyphs_centered(draw, str(index + 1), _font(30, bold=True), x + width / 2, y + height / 2)
            continue
        text, count = slot
        draw.rectangle((x, y, x + width, y + height), outline=0, width=3)
        draw.rectangle((x + width - 64, y, x + width, y + height), fill=0)
        _draw_glyphs_centered(draw, str(count), _font(30, bold=True), x + width - 32, y + height / 2, fill=255)
        label = f"{index + 1}. {text}"
        label_font = _fitted(label, width - 84, 26, 16)
        _draw_lines(draw, _wrapped(label, label_font, width - 84, 1), label_font, left=x + 10, width=width - 84,
                    top=int(y + height / 2 - label_font.size * 0.6))  # fmt: skip
    footer = []
    if turn:
        footer.append(f"{_TEXT['turn'][language]}: {turn}")
    if misses is not None:
        footer.append(f"{_TEXT['misses'][language]}: {'X' * misses}{'·' * (6 - misses)}")
    if footer:
        _draw_lines(draw, ["   ".join(footer)], _font(20, bold=True), left=20, width=WIDTH - 40, top=398, centered=True, fill=_GREY)  # fmt: skip
    if scores:
        width = (WIDTH - 40) // len(scores)
        for index, (name, points) in enumerate(scores):
            label = f"{name} {points}"
            _draw_lines(draw, [label], _fitted(label, width - 10, 30, 16), left=20 + index * width, width=width, top=430, centered=True)  # fmt: skip
    return encode_png(canvas)
