"""Ballpark's TRMNL screens (800×480, four greys): the question, and every team's range on one number line.

Joining and the winner use Big Brain's screens (``render_join`` with the Ballpark title, ``render_winner``), and the
team score tiles are Big Brain's too.
"""

import math
from typing import Final, NamedTuple

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    _GREY,
    WIDTH,
    HEIGHT,
    _LIGHT_GREY,
    _font,
    _pill,
    _fitted,
    _wrapped,
    encode_png,
    _draw_lines,
    _game_header,
    _display_font,
)
from reachy_mini_conversation_app.games.big_brain_screens import _scores, _range_mark, _team_badge


_TEXT: Final[dict[str, dict[str, str]]] = {
    "unit": {"es": "Respuesta en: {unit}", "en": "Answer in: {unit}"},
    "type": {"es": "Escriban su rango en el teléfono", "en": "Type your range on the phone"},
    "sent": {"es": "Listo", "en": "Sent"},
    "nail": {"es": "¡Sorpresa! Exacto = 5 puntos", "en": "Surprise! Exact = 5 points"},
    "double": {"es": "¡Sorpresa! Puntos dobles", "en": "Surprise! Double points"},
    "none": {"es": "sin rango", "en": "no range"},
}
# A range this many times wider than the smallest number puts the line on a log scale, so small ranges still show.
_LOG_SPREAD: Final[float] = 30.0


class Row(NamedTuple):
    """One team on the number line: its name, range (None: none), a note ("copia a Azul", "×2"), points and verdict."""

    name: str
    low: int | None
    high: int | None
    note: str
    points: int | None
    right: bool | None


def number(value: int, language: str) -> str:
    """Return ``value`` with thousands marked as each language writes them: "1.500" or "1,500"."""
    return f"{value:,}".replace(",", "." if language == "es" else ",")


def _span(low: int, high: int, language: str) -> str:
    return number(low, language) if low == high else f"{number(low, language)}–{number(high, language)}"


def _header(canvas: Image.Image, label: str) -> int:
    return _game_header(canvas, "BALLPARK", _range_mark, "ruler", status=label, height=48) + 4


def render_question(
    question: str,
    unit: str,
    label: str,
    sent: list[tuple[str, bool]],
    scores: list[tuple[str, int]],
    language: str,
    *,
    surprise: str | None = None,
) -> bytes:
    """Render a question: the surprise if it is one, the question large, its unit, and which teams have sent."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    top = _header(canvas, label)
    if surprise:
        text = _TEXT[surprise][language].upper()
        _pill(draw, WIDTH / 2, top + 22, text, _display_font(26), height=40)
        top += 48
    footer = HEIGHT - 84
    room = footer - 70 - top
    for size in range(44, 22, -2):
        font = _font(size, bold=True)
        lines = _wrapped(question, font, WIDTH - 48, 4)
        if not lines[-1].endswith("…") and len(lines) * size * 1.2 <= room:
            break
    height = round(len(lines) * font.size * 1.2)
    top = _draw_lines(draw, lines, font, left=24, width=WIDTH - 48, top=top + max(4, (room - height) // 2), centered=True)  # fmt: skip
    if unit:
        _pill(draw, WIDTH / 2, footer - 52, _TEXT["unit"][language].format(unit=unit), _font(22, bold=True), fill=_LIGHT_GREY, text_fill=0, height=36)  # fmt: skip
    hint = _TEXT["type"][language]
    draw.text((WIDTH / 2, footer - 14), hint, font=_fitted(hint, WIDTH - 40, 20, 14, bold=False), fill=0, anchor="ms")
    done = [name for name, ready in sent if ready]
    _scores(canvas, scores, footer, highlight=done, tag=_TEXT["sent"][language], height=76)
    return encode_png(canvas)


def _scale(values: list[int]) -> tuple[float, float, bool]:
    """Return the line's two ends (in scale units) and whether it is a log scale."""
    low, high = min(values), max(values)
    log = (high + 1) / (low + 1) > _LOG_SPREAD
    start, end = (math.log10(low + 1), math.log10(high + 1)) if log else (float(low), float(high))
    if end - start < 1e-9:
        start, end = start - 1, end + 1
    pad = (end - start) * 0.06
    return start - pad, end + pad, log


def render_reveal(
    question: str,
    unit: str,
    rows: list[Row],
    answer: int | None,
    scores: list[tuple[str, int]],
    label: str,
    language: str,
) -> bytes:
    """Render the reveal: the question small, a bar per team on one number line, the answer's marker once known."""
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    top = _header(canvas, label)
    font = _fitted(question, WIDTH - 40, 24, 18)
    lines = _wrapped(question, font, WIDTH - 40, 2)
    top = _draw_lines(draw, lines, font, left=20, width=WIDTH - 40, top=top, centered=True) + 4
    footer = HEIGHT - 84
    values = [v for row in rows for v in (row.low, row.high) if v is not None] + (
        [answer] if answer is not None else []
    )
    left, right = 168, 586  # the number line; names on its left, ranges and points on its right
    marker_top = top + 4
    rows_top = top + 40
    step = min(58, (footer - 34 - rows_top) / max(len(rows), 1))
    axis_y = rows_top + step * len(rows) + 4
    start, end, log = _scale(values) if values else (0.0, 1.0, False)

    def at(value: int) -> float:
        scaled = math.log10(value + 1) if log else float(value)
        return left + (scaled - start) / (end - start) * (right - left)

    draw.line((left, axis_y, right, axis_y), fill=0, width=3)
    if values:
        for value, anchor in ((min(values), "lt"), (max(values), "rt")):
            draw.text((at(value), axis_y + 6), number(value, language), font=_font(16, bold=True), fill=_GREY, anchor=anchor)  # fmt: skip
    for index, row in enumerate(rows):
        middle = rows_top + step * index + step / 2
        badge = min(34, round(step) - 16)
        _team_badge(canvas, row.name, 14 + badge / 2, middle, badge)
        draw.text(
            (24 + badge, middle), row.name, font=_fitted(row.name, left - 36 - badge, 22, 12), fill=0, anchor="lm"
        )
        bar = min(26, step * 0.45)
        if row.low is None or row.high is None:
            draw.text(((left + right) / 2, middle), _TEXT["none"][language], font=_font(18), fill=_GREY, anchor="mm")
        else:
            fill = _GREY if row.right is None else 0 if row.right else 255
            outline = 0
            x1, x2 = at(row.low), at(row.high)
            if x2 - x1 < 8:  # an exact number, or a range too small to see: a diamond
                mid = (x1 + x2) / 2
                draw.polygon([(mid, middle - bar / 2 - 3), (mid + 9, middle), (mid, middle + bar / 2 + 3), (mid - 9, middle)], fill=fill, outline=outline, width=3)  # fmt: skip
            else:
                draw.rounded_rectangle((x1, middle - bar / 2, x2, middle + bar / 2), radius=6, fill=fill, outline=outline, width=3)  # fmt: skip
            span = _span(row.low, row.high, language)
            draw.text((right + 14, middle - (8 if row.note else 0)), span, font=_fitted(span, 120, 22, 12), fill=0, anchor="lm")  # fmt: skip
        if row.note:
            draw.text((right + 14, middle + 13), row.note, font=_fitted(row.note, 120, 16, 10, bold=False), fill=0, anchor="lm")  # fmt: skip
        if row.points is not None:
            text = f"+{row.points}" if row.points else "0"
            _pill(draw, WIDTH - 12, middle, text, _display_font(24), fill=0 if row.points else 255, text_fill=255 if row.points else 0, align="right", height=36, outline=0)  # fmt: skip
    if answer is not None and values:
        x = at(answer)
        for y in range(round(marker_top + 30), round(axis_y), 12):
            draw.line((x, y, x, min(y + 7, axis_y)), fill=0, width=3)
        shown = f"{number(answer, language)} {unit}".strip()
        _pill(draw, min(max(x, 100), WIDTH - 100), marker_top + 15, shown, _display_font(24), height=34)
    _scores(canvas, scores, footer, height=76)
    return encode_png(canvas)
