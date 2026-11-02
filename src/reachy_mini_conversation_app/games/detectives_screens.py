"""Detectives' TRMNL screens (800×480, four greys): joining, the evidence board, a card, and the solution.

The board is the shared table: the case, its suspects, the evidence found and whose turn it is. A card shows one
moment large (a clue, a testimony, a step of the solution). Every screen wears the case-file look: a magnifying
glass, crime-scene tape under the header, suspects as silhouettes and evidence on numbered markers.
"""

from typing import Final

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.eink_display import (
    _GREY,
    WIDTH,
    HEIGHT,
    _LIGHT_GREY,
    _font,
    _pill,
    _paint,
    _fitted,
    _wrapped,
    _baseline,
    encode_png,
    _draw_lines,
    _game_header,
    _display_font,
    _fitted_display,
    _draw_glyphs_centered,
)
from reachy_mini_conversation_app.games.big_brain_screens import _qr_panel, _code_tiles, _join_heading


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
_MAX_EVIDENCE: Final[int] = 8  # the latest ones when more were found


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    return canvas, ImageDraw.Draw(canvas)


def _magnifier(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, fill: int) -> None:
    """Draw a magnifying glass in a ``size`` square centered on ``(x, y)``."""
    radius = size * 0.3
    cx, cy = x - size * 0.1, y - size * 0.1
    stroke = max(3, round(size * 0.11))
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=fill, width=stroke)
    reach = radius * 0.7
    draw.line((cx + reach, cy + reach, x + size * 0.45, y + size * 0.45), fill=fill, width=round(stroke * 1.5))


def _suspect(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, fill: int) -> None:
    """Draw a suspect's silhouette (head and shoulders) in a ``size`` square centered on ``(x, y)``."""
    head = size * 0.22
    draw.ellipse((x - head, y - size * 0.46, x + head, y - size * 0.46 + 2 * head), fill=fill)
    draw.chord((x - size * 0.42, y + size * 0.02, x + size * 0.42, y + size * 0.9), 180, 360, fill=fill)


def _marker(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, number: int) -> None:
    """Draw a numbered evidence marker (a folded tent) centered on ``(x, y)``."""
    half = size / 2
    draw.polygon(
        [(x - half * 0.55, y - half), (x + half * 0.55, y - half), (x + half, y + half), (x - half, y + half)],
        fill=_LIGHT_GREY,
        outline=0,
        width=3,
    )
    _draw_glyphs_centered(draw, str(number), _display_font(round(size * 0.62)), x, y + size * 0.06)


def render_join(url: str, code: str, players: list[str], language: str, *, kids: bool = False) -> bytes:
    """Render the join screen: the QR code on the left, the title, the code and who has joined on the right."""
    canvas, draw = _canvas()
    right = _qr_panel(canvas, url)
    width = WIDTH - right
    y = _join_heading(canvas, "DETECTIVES", _magnifier, right, width, language)
    inner, room = right + 18, width - 36
    y = _code_tiles(draw, code, inner, room, y + 2, dark=True) + 20
    _paint(canvas, (right, round(y), WIDTH, round(y) + 14), "tape")
    y += 34
    bottom = HEIGHT - (60 if kids else 14)
    if not players:
        draw.text((inner + room / 2, y + 16), _TEXT["nobody"][language], font=_font(26, bold=True), fill=_LIGHT_GREY, anchor="mm")  # fmt: skip
    font = _font(24, bold=True)
    x: float = inner
    for player in players:
        if x + font.getlength(player) + 36 > inner + room and x > inner:
            x, y = inner, y + 48
        if y + 40 > bottom:
            break
        box = _pill(draw, x, y + 18, _wrapped(player, font, room - 36, 1)[0], font, fill=255, text_fill=0, align="left", height=40)  # fmt: skip
        x = box[2] + 8
    if kids:
        label = _TEXT["kids"][language].upper()
        _pill(draw, inner + room / 2, HEIGHT - 34, label, _font(22, bold=True), fill=255, text_fill=0, height=40)
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
    status = f"{_TEXT['round'][language]} {round_number}/{rounds}"
    top = _game_header(canvas, title.upper(), _magnifier, "tape", status=status)
    if turn:
        line = f"{_TEXT['turn'][language]} {turn}"
        _pill(draw, WIDTH / 2, top + 30, line, _fitted(line, WIDTH - 120, 28, 18), height=46)
        top += 54
    half = WIDTH // 2
    rows_top = top + 58
    for column, heading in enumerate((_TEXT["people"][language], _TEXT["evidence"][language])):
        left = 20 + column * half
        draw.text((left, _baseline(_display_font(28), top + 26)), heading, font=_display_font(28), fill=0, anchor="ls")
        draw.rectangle((left, top + 44, left + half - 40, top + 48), fill=0)
    draw.line((half, top + 14, half, HEIGHT - 14), fill=_LIGHT_GREY, width=4)
    step = min(52, (HEIGHT - 10 - rows_top) // max(len(people), 1))
    for index, (name, witness) in enumerate(people):
        middle = rows_top + index * step + step / 2
        _suspect(draw, 38, middle, step - 12, _GREY if witness else 0)
        font = _fitted(name, half - 100, 24, 16)
        text_left = 62
        if witness:
            tag = _TEXT["witness"][language].upper()
            box = _pill(draw, half - 24, middle, tag, _display_font(16), fill=_LIGHT_GREY, text_fill=0, align="right", height=26)  # fmt: skip
            font = _fitted(name, round(box[0] - text_left - 8), 24, 14)
        draw.text((text_left, middle), _wrapped(name, font, WIDTH, 1)[0], font=font, fill=0, anchor="lm")
    items = evidence[-_MAX_EVIDENCE:]
    if not items:
        draw.text((half + 20, rows_top + 20), _TEXT["none"][language], font=_font(24, bold=True), fill=_GREY, anchor="lm")  # fmt: skip
    step = min(64, (HEIGHT - 10 - rows_top) // max(len(items), 1))
    first = len(evidence) - len(items) + 1
    room = half - 90
    for index, item in enumerate(items):
        middle = rows_top + index * step + step / 2
        _marker(draw, half + 38, middle, min(step - 10, 40), first + index)
        font = _font(24 if step >= 40 else 20, bold=True)
        lines = _wrapped(item, font, room, 1)
        if lines[0].endswith("…") and step >= 56:  # a long name gets two smaller lines
            font = _font(21, bold=True)
            lines = _wrapped(item, font, room, 2)
        _draw_lines(draw, lines, font, left=half + 68, width=room, top=round(middle - len(lines) * font.size * 0.6))
    return encode_png(canvas)


def render_card(header: str, body: str, *, footer: str = "") -> bytes:
    """Render one moment large: a header bar, the body as big as it fits, and a small footer."""
    canvas, draw = _canvas()
    top = _game_header(canvas, header.upper(), _magnifier, "tape") + 14
    bottom = HEIGHT - (64 if footer else 18)
    for size in range(52, 20, -2):
        font = _font(size, bold=size >= 30)
        lines = _wrapped(body, font, WIDTH - 70, 12)
        if len(lines) * int(size * 1.22) <= bottom - top and not lines[-1].endswith("…"):
            break
    block = len(lines) * int(font.size * 1.2)
    _draw_lines(draw, lines, font, left=35, width=WIDTH - 70, top=top + max(0, (bottom - top - block) // 2), centered=True)  # fmt: skip
    if footer:
        font = _fitted(footer, WIDTH - 140, 26, 16)
        shown = _wrapped(footer, font, WIDTH - 140, 1)[0]
        box = _pill(draw, WIDTH / 2 + 20, HEIGHT - 34, shown, font, height=46)
        _suspect(draw, box[0] - 24, HEIGHT - 34, 36, 0)
    return encode_png(canvas)


def render_solution(culprit: str, motive: str, solvers: list[str], language: str) -> bytes:
    """Render the end: the culprit very large, the motive, and who solved it."""
    canvas, draw = _canvas()
    top = _game_header(canvas, "DETECTIVES", _magnifier, "tape")
    mugshot = (24, top + 22, 224, top + 262)
    draw.rounded_rectangle(mugshot, radius=16, fill=_LIGHT_GREY)
    for y in range(mugshot[1] + 30, mugshot[3], 30):  # the height chart behind a mugshot
        draw.line((mugshot[0], y, mugshot[2], y), fill=255, width=3)
    _suspect(draw, (mugshot[0] + mugshot[2]) / 2, mugshot[3] - 96, 170, 0)
    draw.rounded_rectangle(mugshot, radius=16, outline=0, width=5)
    left, room = 252, WIDTH - 276
    stamp = _TEXT["culprit"][language]
    stamp_font = _display_font(34)
    stamp_box = (left, top + 22, left + stamp_font.getlength(stamp) + 36, top + 76)
    draw.rectangle(stamp_box, outline=0, width=5)
    draw.text((stamp_box[0] + 18, _baseline(stamp_font, (stamp_box[1] + stamp_box[3]) / 2)), stamp, font=stamp_font, fill=0, anchor="ls")  # fmt: skip
    name = culprit.upper()
    font = _fitted_display(name, room, 84, 40)
    lines = _wrapped(name, font, room, 2)
    y = top + 96
    for line in lines:
        draw.text((left, y), line, font=font, fill=0)
        y += round(font.size * 1.18)
    motive_top = max(y + 6, top + 196)
    motive_font = _font(28, bold=True)
    motive_lines = _wrapped(motive, motive_font, room, 3)
    _draw_lines(draw, motive_lines, motive_font, left=left, width=room, top=motive_top)
    line = f"{_TEXT['solved'][language]}: {', '.join(solvers)}" if solvers else _TEXT["unsolved"][language]
    solved_font = _fitted(line, WIDTH - 100, 30, 18)
    _pill(draw, WIDTH / 2, HEIGHT - 44, _wrapped(line, solved_font, WIDTH - 100, 1)[0], solved_font,
          fill=0 if solvers else _LIGHT_GREY, text_fill=255 if solvers else 0, height=56)  # fmt: skip
    return encode_png(canvas)
