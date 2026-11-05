"""Big Brain's TRMNL screens (800×480, four greys): joining, the board, a clue, the final round, the winner.

The e-ink redraws in a second or two, so each screen is one moment of the game, never a countdown. The screen has
only greys, so each team is a shape and a fill (red a black circle, blue a striped square, green a dark triangle,
yellow a light diamond) next to its name; their colors live on the phones. Sheeple and Scribble Rush share the join,
the winner and the team score tiles.
"""

import math
from typing import Final
from collections.abc import Collection

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.qr_code import qr_matrix
from reachy_mini_conversation_app.eink_display import (
    _GREY,
    WIDTH,
    HEIGHT,
    _LIGHT_GREY,
    Fill,
    Icon,
    _font,
    _pill,
    _badge,
    _paint,
    _fitted,
    _spaced,
    _wrapped,
    _big_words,
    _draw_star,
    encode_png,
    _draw_lines,
    _game_header,
    _display_font,
    _spaced_width,
    _fitted_display,
    _draw_glyphs_centered,
)
from reachy_mini_conversation_app.games.big_brain_lines import TEAMS


_TEXT: Final[dict[str, dict[str, str]]] = {
    "join": {"es": "Escanea para jugar", "en": "Scan to play"},
    "code": {"es": "Código", "en": "Code"},
    "nobody": {"es": "Nadie todavía", "en": "Nobody yet"},
    "kids": {"es": "Modo niños", "en": "Kids mode"},
    "final": {"es": "RONDA FINAL", "en": "FINAL ROUND"},
    "bet": {"es": "Apuesten en el teléfono", "en": "Bet on your phone"},
    "think": {"es": "Escriban su respuesta en el teléfono", "en": "Type your answer on your phone"},
    "winner": {"es": "¡GANADOR!", "en": "WINNER!"},
    "tie": {"es": "¡EMPATE!", "en": "IT'S A TIE!"},
    "bet_of": {"es": "Apostó", "en": "Bet"},
    "no_answer": {"es": "(sin respuesta)", "en": "(no answer)"},
    "answer_was": {"es": "Era", "en": "It was"},
    "double": {"es": "DOBLE O NADA", "en": "DOUBLE OR NOTHING"},
    "steal": {"es": "¡ROBO!", "en": "STEAL!"},
    "everyone": {"es": "¡TODOS RESPONDEN!", "en": "EVERYONE ANSWERS!"},
}

# Each team's shape and fill, told apart by both from across the room.
_TEAM_LOOKS: Final[dict[str, tuple[str, Fill]]] = {
    "red": ("circle", 0),
    "blue": ("square", "stripes"),
    "green": ("triangle", _GREY),
    "yellow": ("diamond", _LIGHT_GREY),
}
_LOOK_BY_NAME: Final[dict[str, tuple[str, Fill]]] = {
    name: _TEAM_LOOKS[team] for team, names in TEAMS.items() for name in names
}

Scores = list[tuple[str, int]]


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    return canvas, ImageDraw.Draw(canvas)


def _team_badge(canvas: Image.Image, name: str, x: float, y: float, size: int, *, halo: int = 0) -> None:
    """Draw the team called ``name`` ("Azul", "Blue") as its shape and fill."""
    shape, fill = _LOOK_BY_NAME.get(name.casefold(), ("star", "dots"))
    _badge(canvas, x, y, size, shape, fill, halo=halo)


def _bulb(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, fill: int) -> None:
    """Draw Big Brain's light bulb in a ``size`` square centered on ``(x, y)``."""
    radius = size * 0.3
    middle = y - size * 0.14
    draw.ellipse((x - radius, middle - radius, x + radius, middle + radius), fill=fill)
    draw.polygon(
        [(x - radius * 0.75, middle + radius * 0.6), (x + radius * 0.75, middle + radius * 0.6),
         (x + radius * 0.5, y + size * 0.2), (x - radius * 0.5, y + size * 0.2)],
        fill=fill,
    )  # fmt: skip
    for index, top in enumerate((y + size * 0.24, y + size * 0.36)):
        inset = radius * (0.5 + 0.1 * index)
        draw.rounded_rectangle((x - inset, top, x + inset, top + size * 0.08), radius=2, fill=fill)


def _scores(
    canvas: Image.Image,
    scores: Scores,
    top: int,
    *,
    highlight: Collection[str] = (),
    tag: str = "",
    height: int = 88,
    right: int = WIDTH,
) -> None:
    """Draw a tile per team (shape, name, points) from the left to ``right``, the ``highlight`` ones in black.

    A ``tag`` ("Turno") sits on top of each highlighted tile.
    """
    if not scores:
        return
    draw = ImageDraw.Draw(canvas)
    gap = 10
    width = (right - 24 - gap * (len(scores) - 1)) / len(scores)
    wide = width >= 300
    badge = min(54 if wide else 44, height - 30, round(width * 0.28))
    for index, (name, points) in enumerate(scores):
        left = 12 + index * (width + gap)
        lit = name in highlight
        ink, paper = (255, 0) if lit else (0, 255)
        draw.rounded_rectangle((left, top, left + width, top + height), radius=16, fill=paper, outline=0, width=4)
        _team_badge(canvas, name, left + 14 + badge / 2, top + height / 2, badge, halo=4 if lit else 0)
        text_left = left + badge + 24
        room = round(width - badge - 36)
        shown = f"{points:,}".replace(",", ".")
        if wide:  # one row: the name, and the points at the right
            number = _fitted_display(shown, room // 2, 60, 24)
            draw.text((left + width - 18, top + height / 2), shown, font=number, fill=ink, anchor="rm")
            name_font = _fitted(name, room - round(number.getlength(shown)) - 12, 32, 16)
            draw.text((text_left, top + height / 2), name, font=name_font, fill=ink, anchor="lm")
        else:
            number = _fitted_display(shown, room, 46 if height >= 84 else 40, 24)
            draw.text((text_left, top + 6), name, font=_fitted(name, room, 22, 12), fill=ink)
            draw.text((text_left, top + height - 8), shown, font=number, fill=ink, anchor="ls")
        if lit and tag:
            _pill(draw, left + width - 16, top - 6, tag.upper(), _display_font(18), fill=255, text_fill=0, align="right", height=28, outline=0)  # fmt: skip


def _qr_panel(canvas: Image.Image, url: str) -> int:
    """Draw the join QR code on the left (white quiet zone kept) and return the x where the panel on its right starts."""
    draw = ImageDraw.Draw(canvas)
    modules = qr_matrix(url)
    scale = min(400 // (len(modules) + 4), 14)
    size = (len(modules) + 4) * scale
    left, top = 30, (HEIGHT - size) // 2
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                x0, y0 = left + (x + 2) * scale, top + (y + 2) * scale
                draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill=0)
    return left + size + 24


def _code_tiles(draw: ImageDraw.ImageDraw, code: str, left: float, width: float, top: float, *, dark: bool) -> float:
    """Draw the join code as letter tiles across ``width``; return the y below them."""
    gap = 6
    side = min(52, (width - gap * (len(code) - 1)) / max(len(code), 1))
    first = left + (width - side * len(code) - gap * (len(code) - 1)) / 2
    font = _display_font(round(side * 0.8))
    for index, letter in enumerate(code):
        x = first + index * (side + gap)
        draw.rounded_rectangle((x, top, x + side, top + side * 1.15), radius=8, fill=255 if dark else 0)
        _draw_glyphs_centered(draw, letter, font, x + side / 2, top + side * 0.575, fill=0 if dark else 255)
    return top + side * 1.15


def _join_heading(
    canvas: Image.Image, title: str, icon: Icon, left: int, width: int, language: str, edition: str | None = None
) -> float:
    """Draw a join panel's top (black): icon and title, the edition, "scan to play" with an arrow to the code."""
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((left, -20, WIDTH + 20, HEIGHT + 20), radius=26, fill=0)
    inner = left + 18
    room = width - 36
    font = _fitted_display(title, room - 54, 60, 30)
    title_width = font.getlength(title) + 54
    x = inner + (room - title_width) / 2
    icon(draw, x + 22, 52, 44, 255)
    draw.text((x + 54, 52), title, font=font, fill=255, anchor="lm")
    y = 96.0
    if edition:
        _pill(draw, inner + room / 2, y + 6, edition, _fitted(edition, room - 40, 24, 16), fill=_LIGHT_GREY, text_fill=0, height=36)  # fmt: skip
        y += 38
    scan = _TEXT["join"][language]
    scan_font = _font(26, bold=True)
    scan_left = inner + (room - scan_font.getlength(scan) - 34) / 2 + 34
    draw.polygon([(scan_left - 34, y + 16), (scan_left - 14, y + 4), (scan_left - 14, y + 28)], fill=255)
    draw.text((scan_left, y + 16), scan, font=scan_font, fill=255, anchor="lm")
    return y + 38


def render_join(
    url: str,
    code: str,
    teams: list[tuple[str, list[str]]],
    language: str,
    *,
    kids: bool = False,
    edition: str | None = None,
    title: str = "BIG BRAIN",
) -> bytes:
    """Render the join screen: the QR code on the left, ``title``, the code and who is on each team on the right."""
    canvas, draw = _canvas()
    right = _qr_panel(canvas, url)
    width = WIDTH - right
    icon = _JOIN_ICONS.get(title, _bulb)
    y = _join_heading(canvas, title, icon, right, width, language, edition)
    inner, room = right + 18, width - 36
    y = _code_tiles(draw, code, inner, room, y + 2, dark=True) + 14
    bottom = HEIGHT - (60 if kids else 14)
    step = min(64, (bottom - y) / max(len(teams), 1))
    name_font = _font(24, bold=True)
    for name, players in teams:
        middle = y + step / 2
        plate = min(44, step - 8)
        draw.rounded_rectangle((inner, middle - plate / 2, inner + plate, middle + plate / 2), radius=10, fill=255)
        _team_badge(canvas, name, inner + plate / 2, middle, round(plate) - 10)
        listed = ", ".join(players) if players else _TEXT["nobody"][language]
        if step >= 52:
            draw.text((inner + 56, middle - 2), name, font=name_font, fill=255, anchor="ls")
            player_font = _font(20, bold=bool(players))
            line = _wrapped(listed, player_font, room - 58, 1)[0]
            draw.text((inner + 56, middle + 2), line, font=player_font, fill=255 if players else _LIGHT_GREY, anchor="lt")  # fmt: skip
        else:
            line = _wrapped(f"{name}: {listed}", _font(20, bold=True), room - 58, 1)[0]
            draw.text((inner + 56, middle), line, font=_font(20, bold=True), fill=255, anchor="lm")
        y += step
    if kids:
        label = _TEXT["kids"][language].upper()
        box = _pill(draw, inner + room / 2 + 18, HEIGHT - 34, label, _font(22, bold=True), fill=255, text_fill=0, height=40)  # fmt: skip
        _draw_star(draw, box[0] - 24, HEIGHT - 34, 18, filled=False)
    return encode_png(canvas)


def render_board(
    titles: list[str],
    used: list[list[bool]],
    points: tuple[int, ...],
    scores: Scores,
    *,
    control: str | None = None,
) -> bytes:
    """Render the board: column titles on top, a square per clue (hatched once used), and the scores below."""
    canvas, draw = _canvas()
    top = _game_header(canvas, "BIG BRAIN", _bulb, "marquee", height=44)
    columns = len(titles)
    gap, left = 8, 12
    width = (WIDTH - 2 * left - (columns - 1) * gap) // columns
    title_top, title_height = top + 6, 62
    row_top = title_top + title_height + 6
    row_height = (372 - row_top - (len(points) - 1) * 6) // max(len(points), 1)
    value_font = _display_font(min(44, row_height - 8))
    for index, title in enumerate(titles):
        x = left + index * (width + gap)
        draw.rounded_rectangle((x, title_top, x + width, title_top + title_height), radius=8, fill=_LIGHT_GREY)
        longest = max(title.upper().split(), key=len, default="")
        title_font = _fitted(longest, width - 12, 20, 12)  # the longest word fits on one line
        lines = _wrapped(title.upper(), title_font, width - 12, 3)
        middle = title_top + title_height / 2 - len(lines) * int(title_font.size * 1.1) / 2
        for line_index, line in enumerate(lines):
            y = middle + (line_index + 0.5) * int(title_font.size * 1.1)
            draw.text((x + width / 2, y), line, font=title_font, fill=0, anchor="mm")
        for row, value in enumerate(points):
            y = row_top + row * (row_height + 6)
            box = (x, y, x + width, y + row_height)
            if used[index][row]:
                _paint(canvas, box, "hatch")
                draw.rounded_rectangle(box, radius=8, outline=_LIGHT_GREY, width=3)
            else:
                draw.rounded_rectangle(box, radius=8, fill=0)
                _draw_glyphs_centered(draw, str(value), value_font, x + width / 2, y + row_height / 2, fill=255)
    _scores(canvas, scores, 382, highlight=(control,) if control else ())
    return encode_png(canvas)


def _ribbon(draw: ImageDraw.ImageDraw, text: str, y: float, font_size: int) -> None:
    """Draw ``text`` in white on a black ribbon with notched ends, centered at height ``y``."""
    font = _display_font(font_size)
    width = font.getlength(text) + 120
    height = font_size * 1.3
    left, right = (WIDTH - width) / 2, (WIDTH + width) / 2
    top, bottom = y - height / 2, y + height / 2
    notch = height * 0.35
    draw.polygon(
        [(left - 30, top), (right + 30, top), (right + 30 - notch, y), (right + 30, bottom), (left - 30, bottom),
         (left - 30 + notch, y)],
        fill=0,
    )  # fmt: skip
    for side in (left + 22, right - 22):
        _draw_star(draw, side, y, height * 0.26, filled=False)
    _draw_glyphs_centered(draw, text, font, WIDTH / 2, y, fill=255)


def _category(draw: ImageDraw.ImageDraw, column: str, top: int) -> int:
    """Draw a clue's column on a light-grey band under the header; return the y below it."""
    draw.rectangle((0, top, WIDTH, top + 48), fill=_LIGHT_GREY)
    text = column.upper()
    font = _font(28, bold=True)
    tracking = 3.0
    while _spaced_width(text, font, tracking) > WIDTH - 60 and font.size > 16:
        font = _font(font.size - 2, bold=True)
    _spaced(draw, (WIDTH - _spaced_width(text, font, tracking)) / 2, top + 24, text, font, tracking, 0)
    return top + 48


def render_clue(column: str, points: int, text: str, language: str, *, hidden: str | None = None) -> bytes:
    """Render a clue large, with its column and points (or the hidden square's name) above it."""
    canvas, draw = _canvas()
    top = _game_header(canvas, "BIG BRAIN", _bulb, "marquee", status=str(points) if points else "")
    top = _category(draw, column, top)
    if hidden:
        _ribbon(draw, _TEXT[hidden][language], top + 42, 40)
        top += 76
    for size in range(56, 26, -2):
        font = _font(size, bold=True)
        lines = _wrapped(text, font, WIDTH - 70, 8)
        if len(lines) * int(size * 1.2) <= HEIGHT - top - 30 and not lines[-1].endswith("…"):
            break
    block = len(lines) * int(font.size * 1.2)
    _draw_lines(draw, lines, font, left=35, width=WIDTH - 70, top=top + (HEIGHT - top - block) // 2 - 4, centered=True)  # fmt: skip
    return encode_png(canvas)


def render_answer(column: str, answer: str, language: str) -> bytes:
    """Render the answer nobody got, under its column."""
    canvas, draw = _canvas()
    top = _category(draw, column, _game_header(canvas, "BIG BRAIN", _bulb, "marquee"))
    card = (40, top + 46, WIDTH - 40, HEIGHT - 26)
    draw.rounded_rectangle(card, radius=28, outline=0, width=6)
    _pill(draw, WIDTH / 2, card[1], _TEXT["answer_was"][language], _font(30, bold=True), height=50)
    _big_words(draw, answer, WIDTH / 2, WIDTH - 140, (card[1] + card[3]) / 2 + 8, card[3] - card[1] - 70, largest=120)
    return encode_png(canvas)


def render_final(category: str, scores: Scores, language: str, *, step: str = "bet") -> bytes:
    """Render the final round: its title, the category, what to do now and the scores."""
    canvas, draw = _canvas()
    top = _game_header(canvas, "BIG BRAIN", _bulb, "marquee")
    title = _TEXT["final"][language]
    font = _display_font(76)
    title_width = font.getlength(title)
    _draw_glyphs_centered(draw, title, font, WIDTH / 2, top + 58)
    for side in (-1, 1):
        _draw_star(draw, WIDTH / 2 + side * (title_width / 2 + 44), top + 58, 26, filled=True)
    text = category.upper()
    category_font = _fitted(text, WIDTH - 120, 46, 24)
    lines = _wrapped(text, category_font, WIDTH - 120, 2)
    height = len(lines) * int(category_font.size * 1.15) + 26
    box_top = top + 116
    draw.rounded_rectangle((40, box_top, WIDTH - 40, box_top + height), radius=height / 2 if len(lines) == 1 else 22, fill=0)  # fmt: skip
    _draw_lines(draw, lines, category_font, left=60, width=WIDTH - 120, top=round(box_top + 10), fill=255, centered=True)  # fmt: skip
    _phone_hint(draw, _TEXT[step][language], box_top + height + 42)
    _scores(canvas, scores, 382)
    return encode_png(canvas)


def _phone_hint(draw: ImageDraw.ImageDraw, text: str, y: float) -> None:
    """Draw ``text`` centered at height ``y`` after a small phone."""
    font = _fitted(text, WIDTH - 140, 32, 22)
    width = font.getlength(text) + 44
    left = (WIDTH - width) / 2
    draw.rounded_rectangle((left, y - 20, left + 24, y + 20), radius=5, fill=0)
    draw.rectangle((left + 4, y - 14, left + 20, y + 10), fill=255)
    draw.text((left + 44, y), text, font=font, fill=0, anchor="lm")


def _verdict(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float, right: bool) -> None:
    """Draw a tick (right) or a cross (wrong) in white on a black disc."""
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=0)
    stroke = round(radius * 0.22)
    if right:
        draw.line([(x - radius * 0.45, y + radius * 0.02), (x - radius * 0.12, y + radius * 0.35),
                   (x + radius * 0.48, y - radius * 0.32)], fill=255, width=stroke, joint="curve")  # fmt: skip
    else:
        reach = radius * 0.38
        draw.line((x - reach, y - reach, x + reach, y + reach), fill=255, width=stroke)
        draw.line((x - reach, y + reach, x + reach, y - reach), fill=255, width=stroke)


def render_reveal(team: str, answer: str, bet: int, right: bool | None, language: str) -> bytes:
    """Render one team's final answer and bet, and the points won or lost once judged."""
    canvas, draw = _canvas()
    top = _game_header(canvas, "BIG BRAIN", _bulb, "marquee", status=_TEXT["final"][language])
    name = team.upper()
    name_font = _display_font(56)
    name_width = name_font.getlength(name) + 70
    left = (WIDTH - name_width) / 2
    _team_badge(canvas, team, left + 25, top + 50, 50)
    draw.text((left + 70, top + 50), name, font=name_font, fill=0, anchor="lm")
    card = (40, top + 100, WIDTH - 40, top + 250)
    draw.rounded_rectangle(card, radius=28, outline=0, width=6)
    if answer:
        _big_words(draw, answer, WIDTH / 2, WIDTH - 140, (card[1] + card[3]) / 2, card[3] - card[1] - 30, largest=96)
    else:
        _draw_glyphs_centered(draw, _TEXT["no_answer"][language], _font(44, bold=True), WIDTH / 2, (card[1] + card[3]) / 2, fill=_GREY)  # fmt: skip
    y = (card[3] + HEIGHT) / 2
    bet_line = f"{_TEXT['bet_of'][language]}: {bet}"
    if right is None:
        _pill(draw, WIDTH / 2, y, bet_line, _font(34, bold=True), fill=_LIGHT_GREY, text_fill=0, height=58)
        return encode_png(canvas)
    _pill(draw, WIDTH / 2 - 30, y, bet_line, _font(30, bold=True), fill=_LIGHT_GREY, text_fill=0, align="right", height=54)  # fmt: skip
    change = f"+{bet}" if right else f"−{bet}"
    _verdict(draw, WIDTH / 2 + 34, y, 34, right)
    draw.text((WIDTH / 2 + 82, y), change, font=_display_font(70), fill=0, anchor="lm")
    return encode_png(canvas)


def _sunburst(canvas: Image.Image, x: float, y: float, box: tuple[int, int, int, int]) -> None:
    """Fill ``box`` with light-grey rays fanning out from ``(x, y)``."""
    rays = Image.new("L", canvas.size, 255)
    draw = ImageDraw.Draw(rays)
    reach = WIDTH * 1.5
    for index in range(0, 24, 2):
        start, end = index * math.pi / 12, (index + 1) * math.pi / 12
        draw.polygon(
            [(x, y), (x + reach * math.cos(start), y + reach * math.sin(start)),
             (x + reach * math.cos(end), y + reach * math.sin(end))],
            fill=_LIGHT_GREY,
        )  # fmt: skip
    canvas.paste(rays.crop(box), box)


def _trophy(draw: ImageDraw.ImageDraw, x: float, top: float, size: float) -> None:
    """Draw a black trophy cup ``size`` tall, its top center at ``(x, top)``."""
    half = size * 0.32
    stroke = max(4, round(size * 0.09))
    for side in (-1, 1):
        cx = x + side * half
        box = (cx - size * 0.2, top + size * 0.04, cx + size * 0.2, top + size * 0.4)
        draw.arc(box, 90 if side < 0 else 270, 270 if side < 0 else 90, fill=0, width=stroke)
    draw.chord((x - half, top - size * 0.52, x + half, top + size * 0.62), 0, 180, fill=0)
    draw.rectangle((x - size * 0.07, top + size * 0.58, x + size * 0.07, top + size * 0.8), fill=0)
    draw.rounded_rectangle((x - size * 0.28, top + size * 0.78, x + size * 0.28, top + size), radius=4, fill=0)
    _draw_star(draw, x, top + size * 0.2, size * 0.14, filled=False)


def render_winner(scores: Scores, language: str) -> bytes:
    """Render the end: the winner (or a tie) very large and every team's final points."""
    canvas, draw = _canvas()
    best = max((points for _, points in scores), default=0)
    winners = [name for name, points in scores if points == best]
    _sunburst(canvas, WIDTH / 2, 380, (0, 0, WIDTH, 370))
    title = _TEXT["tie" if len(winners) > 1 else "winner"][language]
    title_font = _display_font(54)
    title_width = title_font.getlength(title)
    _trophy(draw, WIDTH / 2 - title_width / 2 - 52, 18, 88)
    _pill(draw, WIDTH / 2 + 30, 60, title, title_font, height=84)
    names = " · ".join(name.upper() for name in winners)
    font = _fitted_display(names, WIDTH - 200, 130, 48)
    name_width = font.getlength(names)
    badge = min(96, round(font.size))
    _draw_glyphs_centered(draw, names, font, WIDTH / 2 + (badge / 2 + 14 if len(winners) == 1 else 0), 222)
    if len(winners) == 1:
        _team_badge(canvas, winners[0], WIDTH / 2 - name_width / 2 - 14, 222, badge, halo=6)
    _scores(canvas, sorted(scores, key=lambda item: -item[1]), 382, highlight=winners)
    return encode_png(canvas)


def _sheep(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, fill: int) -> None:
    """Draw Sheeple's sheep (a woolly cloud, head and legs) in a ``size`` square centered on ``(x, y)``."""
    paper = 0 if fill == 255 else 255
    leg = size * 0.08
    for offset in (-0.18, 0.14):
        draw.rectangle((x + offset * size, y + size * 0.1, x + offset * size + leg, y + size * 0.42), fill=fill)
    puff = size * 0.17
    for dx, dy in ((-0.2, -0.08), (0.0, -0.16), (0.2, -0.08), (-0.14, 0.1), (0.08, 0.12), (0.26, 0.08)):
        cx, cy = x + dx * size, y + dy * size
        draw.ellipse((cx - puff, cy - puff, cx + puff, cy + puff), fill=fill)
    head = (x - size * 0.5, y - size * 0.2, x - size * 0.2, y + size * 0.14)
    draw.ellipse(head, fill=fill, outline=paper, width=max(2, round(size * 0.05)))
    eye = size * 0.045
    draw.ellipse((x - size * 0.38 - eye, y - size * 0.06 - eye, x - size * 0.38 + eye, y - size * 0.06 + eye), fill=paper)  # fmt: skip


def _pencil(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, fill: int) -> None:
    """Draw Scribble Rush's pencil, pointing down-left, in a ``size`` square centered on ``(x, y)``."""
    paper = 0 if fill == 255 else 255
    along, across = (-math.sqrt(0.5), math.sqrt(0.5)), (math.sqrt(0.5), math.sqrt(0.5))

    def at(u: float, v: float) -> tuple[float, float]:
        return (x + (along[0] * u + across[0] * v) * size, y + (along[1] * u + across[1] * v) * size)

    half = 0.13
    draw.polygon([at(-0.5, -half), at(0.2, -half), at(0.2, half), at(-0.5, half)], fill=fill)
    draw.polygon([at(0.2, -half), at(0.5, 0), at(0.2, half)], fill=fill)
    draw.polygon([at(0.24, -half * 0.7), at(0.38, 0), at(0.24, half * 0.7)], fill=paper)
    draw.line([at(-0.34, -half), at(-0.34, half)], fill=paper, width=max(2, round(size * 0.06)))


def _range_mark(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, fill: int) -> None:
    """Draw Ballpark's mark, a range with its two ends and a pin on the answer, in a ``size`` square at ``(x, y)``."""
    half, line = size * 0.46, max(3, round(size * 0.09))
    bar_y = y + size * 0.2
    draw.line((x - half, bar_y, x + half, bar_y), fill=fill, width=line)
    for end in (x - half, x + half):
        draw.line((end, bar_y - size * 0.2, end, bar_y + size * 0.2), fill=fill, width=line)
    pin_x, head = x + size * 0.1, size * 0.17
    draw.polygon([(pin_x - head, y - size * 0.22), (pin_x + head, y - size * 0.22), (pin_x, bar_y - line)], fill=fill)
    draw.ellipse((pin_x - head, y - size * 0.22 - head, pin_x + head, y - size * 0.22 + head), fill=fill)


_JOIN_ICONS: Final[dict[str, Icon]] = {
    "BIG BRAIN": _bulb,
    "SHEEPLE": _sheep,
    "SCRIBBLE RUSH": _pencil,
    "BALLPARK": _range_mark,
}
