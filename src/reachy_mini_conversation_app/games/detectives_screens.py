"""Detectives' TRMNL screens (800×480, four greys): joining, the evidence board, a card, and the solution.

The board is the shared table: the case, its suspects, the evidence found and whose turn it is. A card shows one
moment large (a clue, a testimony, a step of the solution).
"""

from typing import Final

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.qr_code import qr_matrix
from reachy_mini_conversation_app.eink_display import (
    _GREY,
    WIDTH,
    HEIGHT,
    _font,
    _fitted,
    _wrapped,
    encode_png,
    _draw_lines,
)


_TEXT: Final[dict[str, dict[str, str]]] = {
    "join": {"es": "Escanea para jugar", "en": "Scan to play"},
    "code": {"es": "Código", "en": "Code"},
    "nobody": {"es": "Nadie todavía", "en": "Nobody yet"},
    "kids": {"es": "Caso para niños", "en": "Kids' case"},
    "round": {"es": "Ronda", "en": "Round"},
    "turn": {"es": "Turno de", "en": "Turn:"},
    "people": {"es": "SOSPECHOSOS", "en": "SUSPECTS"},
    "evidence": {"es": "PRUEBAS", "en": "EVIDENCE"},
    "none": {"es": "Nada todavía", "en": "Nothing yet"},
    "witness": {"es": "testigo", "en": "witness"},
    "culprit": {"es": "EL CULPABLE", "en": "THE CULPRIT"},
    "solved": {"es": "Lo resolvieron", "en": "Solved by"},
    "unsolved": {"es": "Nadie lo resolvió", "en": "Nobody solved it"},
}


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    return canvas, ImageDraw.Draw(canvas)


def render_join(url: str, code: str, players: list[str], language: str, *, kids: bool = False) -> bytes:
    """Render the join screen: the QR code on the left, the title, the code and who has joined on the right."""
    canvas, draw = _canvas()
    modules = qr_matrix(url)
    scale = min(400 // (len(modules) + 4), 14)
    size = (len(modules) + 4) * scale
    left, top = 30, (HEIGHT - size) // 2
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                x0, y0 = left + (x + 2) * scale, top + (y + 2) * scale
                draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill=0)
    right = left + size + 30
    width = WIDTH - right - 20
    _draw_lines(draw, ["DETECTIVES"], _fitted("DETECTIVES", width, 52, 30), left=right, width=width, top=30, centered=True)  # fmt: skip
    _draw_lines(draw, [_TEXT["join"][language]], _font(28), left=right, width=width, top=96, centered=True)
    code_line = f"{_TEXT['code'][language]}: {code}"
    _draw_lines(draw, [code_line], _font(30, bold=True), left=right, width=width, top=136, centered=True)
    listed = ", ".join(players) if players else _TEXT["nobody"][language]
    lines = _wrapped(listed, _font(26), width - 10, 6)
    _draw_lines(draw, lines, _font(26), left=right, width=width, top=200, fill=0 if players else _GREY, centered=True)
    if kids:
        _draw_lines(draw, [_TEXT["kids"][language]], _font(24, bold=True), left=right, width=width, top=HEIGHT - 44, centered=True)  # fmt: skip
    return encode_png(canvas)


def render_board(
    title: str,
    people: list[tuple[str, bool]],
    evidence: list[str],
    round_number: int,
    rounds: int,
    turn: str | None,
    language: str,
) -> bytes:
    """Render the evidence board: the case and the round on top, suspects on the left, evidence on the right."""
    canvas, draw = _canvas()
    draw.rectangle((0, 0, WIDTH, 64), fill=0)
    _draw_lines(draw, [title.upper()], _fitted(title.upper(), WIDTH - 40, 34, 20), left=20, width=WIDTH - 40, top=12, fill=255, centered=True)  # fmt: skip
    status = f"{_TEXT['round'][language]} {round_number}/{rounds}"
    if turn:
        status += f" · {_TEXT['turn'][language]} {turn}"
    _draw_lines(draw, [status], _fitted(status, WIDTH - 40, 28, 18), left=20, width=WIDTH - 40, top=76, centered=True)
    half = WIDTH // 2
    for column, (heading, items) in enumerate(((_TEXT["people"][language], None), (_TEXT["evidence"][language], evidence))):  # fmt: skip
        left = 20 + column * half
        draw.text((left, 124), heading, font=_font(24, bold=True), fill=0)
        draw.line((left, 154, left + half - 40, 154), fill=0, width=2)
        y = 166
        if items is None:
            for name, witness in people:
                shown = f"{name} ({_TEXT['witness'][language]})" if witness else name
                draw.text((left, y), _wrapped(shown, _font(24), half - 40, 1)[0], font=_font(24), fill=_GREY if witness else 0)  # fmt: skip
                y += 40
        else:
            font = _font(22 if len(items) > 6 else 24)
            step = 34 if len(items) > 6 else 40
            for item in items[-8:]:
                draw.text((left, y), _wrapped(f"• {item}", font, half - 40, 1)[0], font=font, fill=0)
                y += step
            if not items:
                draw.text((left, y), _TEXT["none"][language], font=_font(24), fill=_GREY)
    return encode_png(canvas)


def render_card(header: str, body: str, *, footer: str = "") -> bytes:
    """Render one moment large: a header bar, the body as big as it fits, and a small footer."""
    canvas, draw = _canvas()
    draw.rectangle((0, 0, WIDTH, 70), fill=0)
    _draw_lines(draw, [header.upper()], _fitted(header.upper(), WIDTH - 40, 36, 20), left=20, width=WIDTH - 40, top=14, fill=255, centered=True)  # fmt: skip
    bottom = HEIGHT - (50 if footer else 20)
    top = 90
    for size in range(48, 20, -2):
        font = _font(size, bold=size >= 34)
        lines = _wrapped(body, font, WIDTH - 60, 12)
        if len(lines) * int(size * 1.25) <= bottom - top and not lines[-1].endswith("…"):
            break
    block = len(lines) * int(font.size * 1.2)
    _draw_lines(draw, lines, font, left=30, width=WIDTH - 60, top=top + max(0, (bottom - top - block) // 2), centered=True)  # fmt: skip
    if footer:
        _draw_lines(draw, [footer], _fitted(footer, WIDTH - 40, 24, 16), left=20, width=WIDTH - 40, top=HEIGHT - 40, fill=_GREY, centered=True)  # fmt: skip
    return encode_png(canvas)


def render_solution(culprit: str, motive: str, solvers: list[str], language: str) -> bytes:
    """Render the end: the culprit very large, the motive, and who solved it."""
    canvas, draw = _canvas()
    _draw_lines(draw, [_TEXT["culprit"][language]], _font(36, bold=True), left=20, width=WIDTH - 40, top=24, centered=True)  # fmt: skip
    font = _fitted(culprit.upper(), WIDTH - 60, 80, 36)
    _draw_lines(draw, _wrapped(culprit.upper(), font, WIDTH - 60, 2), font, left=30, width=WIDTH - 60, top=90, centered=True)  # fmt: skip
    _draw_lines(draw, _wrapped(motive, _font(30), WIDTH - 80, 2), _font(30), left=40, width=WIDTH - 80, top=250, centered=True)  # fmt: skip
    line = f"{_TEXT['solved'][language]}: {', '.join(solvers)}" if solvers else _TEXT["unsolved"][language]
    _draw_lines(draw, _wrapped(line, _font(30, bold=True), WIDTH - 60, 2), _font(30, bold=True), left=30, width=WIDTH - 60, top=360, centered=True)  # fmt: skip
    return encode_png(canvas)
