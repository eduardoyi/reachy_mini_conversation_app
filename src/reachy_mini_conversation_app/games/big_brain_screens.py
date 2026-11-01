"""Big Brain's TRMNL screens (800×480, four greys): joining, the board, a clue, the final round, the winner.

The e-ink redraws in a second or two, so each screen is one moment of the game, never a countdown. Teams are
named rather than colored (the screen has only greys); their colors live on the phones.
"""

from typing import Final

from PIL import Image, ImageDraw

from reachy_mini_conversation_app.qr_code import qr_matrix
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

Scores = list[tuple[str, int]]


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    return canvas, ImageDraw.Draw(canvas)


def _scores(draw: ImageDraw.ImageDraw, scores: Scores, top: int, *, control: str | None = None) -> None:
    """Draw the teams and their points in a row across the screen, the team in control boxed."""
    if not scores:
        return
    width = (WIDTH - 40) // len(scores)
    font, points_font = _font(24, bold=True), _font(30, bold=True)
    for index, (name, points) in enumerate(scores):
        left = 20 + index * width
        if name == control:
            draw.rounded_rectangle((left + 6, top - 4, left + width - 6, top + 70), radius=10, outline=0, width=3)
        _draw_lines(draw, [name], font, left=left, width=width, top=top, centered=True)
        _draw_lines(draw, [f"{points:,}".replace(",", ".")], points_font, left=left, width=width, top=top + 30, centered=True)  # fmt: skip


def render_join(
    url: str, code: str, teams: list[tuple[str, list[str]]], language: str, *, kids: bool = False
) -> bytes:
    """Render the join screen: the QR code on the left, the code and who is on each team on the right."""
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
    _draw_lines(draw, ["BIG BRAIN"], _font(52, bold=True), left=right, width=width, top=30, centered=True)
    _draw_lines(draw, [_TEXT["join"][language]], _font(28), left=right, width=width, top=96, centered=True)
    code_line = f"{_TEXT['code'][language]}: {code}"
    _draw_lines(draw, [code_line], _font(30, bold=True), left=right, width=width, top=136, centered=True)
    y = 196
    for name, players in teams:
        draw.text((right, y), name, font=_font(26, bold=True), fill=0)
        listed = ", ".join(players) if players else _TEXT["nobody"][language]
        lines = _wrapped(listed, _font(22), width - 10, 1)
        draw.text((right + 10, y + 32), lines[0] if lines else "", font=_font(22), fill=0 if players else _GREY)
        y += 64
    if kids:
        _draw_lines(draw, [_TEXT["kids"][language]], _font(24, bold=True), left=right, width=width, top=HEIGHT - 44, centered=True)  # fmt: skip
    return encode_png(canvas)


def render_board(
    titles: list[str],
    used: list[list[bool]],
    points: tuple[int, ...],
    scores: Scores,
    *,
    control: str | None = None,
) -> bytes:
    """Render the board: column titles on top, a square per clue (blank once used), and the scores below."""
    canvas, draw = _canvas()
    columns = len(titles)
    gap, left = 8, 12
    width = (WIDTH - 2 * left - (columns - 1) * gap) // columns
    for index, title in enumerate(titles):
        x = left + index * (width + gap)
        draw.rectangle((x, 10, x + width, 92), fill=0)
        longest = max(title.upper().split(), key=len, default="")
        title_font = _fitted(longest, width - 12, 20, 12)  # the longest word fits on one line
        lines = _wrapped(title.upper(), title_font, width - 12, 3)
        middle = 51 - len(lines) * int(title_font.size * 1.2) / 2
        _draw_lines(draw, lines, title_font, left=x + 6, width=width - 12, top=int(middle), fill=255, centered=True)
        for row, value in enumerate(points):
            y = 102 + row * 70
            if used[index][row]:
                draw.rectangle((x, y, x + width, y + 62), outline=_LIGHT_GREY, width=2)
            else:
                draw.rectangle((x, y, x + width, y + 62), fill=_LIGHT_GREY)
                _draw_glyphs_centered(draw, str(value), _font(38, bold=True), x + width / 2, y + 31)
    _scores(draw, scores, 392, control=control)
    return encode_png(canvas)


def render_clue(column: str, points: int, text: str, language: str, *, hidden: str | None = None) -> bytes:
    """Render a clue large, with its column and points (or the hidden square's name) above it."""
    canvas, draw = _canvas()
    header = f"{column.upper()} · {points}" if points else column.upper()
    _draw_lines(draw, [header], _fitted(header, WIDTH - 40, 30, 18), left=20, width=WIDTH - 40, top=18, centered=True)
    if hidden:
        label = _TEXT[hidden][language]
        draw.rectangle((0, 58, WIDTH, 104), fill=0)
        _draw_glyphs_centered(draw, label, _font(34, bold=True), WIDTH / 2, 81, fill=255)
    top = 120 if hidden else 80
    for size in range(54, 26, -2):
        font = _font(size, bold=True)
        lines = _wrapped(text, font, WIDTH - 60, 8)
        if len(lines) * int(size * 1.2) <= HEIGHT - top - 20 and not lines[-1].endswith("…"):
            break
    block = len(lines) * int(font.size * 1.2)
    _draw_lines(draw, lines, font, left=30, width=WIDTH - 60, top=top + (HEIGHT - top - 20 - block) // 2, centered=True)  # fmt: skip
    return encode_png(canvas)


def render_answer(column: str, answer: str, language: str) -> bytes:
    """Render the answer nobody got, under its column."""
    canvas, draw = _canvas()
    _draw_lines(draw, [column.upper()], _font(28, bold=True), left=20, width=WIDTH - 40, top=30, centered=True)
    _draw_lines(draw, [_TEXT["answer_was"][language]], _font(34), left=20, width=WIDTH - 40, top=150, centered=True)
    font = _fitted(answer, WIDTH - 60, 90, 36)
    lines = _wrapped(answer, font, WIDTH - 60, 2)
    _draw_lines(draw, lines, font, left=30, width=WIDTH - 60, top=220, centered=True)
    return encode_png(canvas)


def render_final(category: str, scores: Scores, language: str, *, step: str = "bet") -> bytes:
    """Render the final round: its title, the category, what to do now and the scores."""
    canvas, draw = _canvas()
    _draw_glyphs_centered(draw, _TEXT["final"][language], _font(64, bold=True), WIDTH / 2, 70)
    font = _fitted(category.upper(), WIDTH - 60, 60, 30)
    _draw_lines(draw, _wrapped(category.upper(), font, WIDTH - 60, 2), font, left=30, width=WIDTH - 60, top=140, centered=True)  # fmt: skip
    _draw_lines(draw, [_TEXT[step][language]], _font(30), left=20, width=WIDTH - 40, top=290, centered=True)
    _scores(draw, scores, 380)
    return encode_png(canvas)


def render_reveal(team: str, answer: str, bet: int, right: bool | None, language: str) -> bytes:
    """Render one team's final answer and bet, and the points won or lost once judged."""
    canvas, draw = _canvas()
    _draw_lines(draw, [team.upper()], _font(48, bold=True), left=20, width=WIDTH - 40, top=30, centered=True)
    shown = answer or _TEXT["no_answer"][language]
    font = _fitted(shown, WIDTH - 80, 84, 36)
    _draw_lines(draw, _wrapped(shown, font, WIDTH - 80, 2), font, left=40, width=WIDTH - 80, top=150, centered=True)
    line = f"{_TEXT['bet_of'][language]}: {bet}"
    _draw_lines(draw, [line], _font(36), left=20, width=WIDTH - 40, top=330, centered=True)
    if right is not None:  # the font has no tick or cross: the points won or lost say it
        change = f"+{bet}" if right else f"−{bet}"
        _draw_glyphs_centered(draw, change, _font(72, bold=True), WIDTH / 2, 420)
    return encode_png(canvas)


def render_winner(scores: Scores, language: str) -> bytes:
    """Render the end: the winner (or a tie) very large and every team's final points."""
    canvas, draw = _canvas()
    best = max((points for _, points in scores), default=0)
    winners = [name for name, points in scores if points == best]
    title = _TEXT["tie" if len(winners) > 1 else "winner"][language]
    _draw_glyphs_centered(draw, title, _font(56, bold=True), WIDTH / 2, 60)
    names = " · ".join(name.upper() for name in winners)
    _draw_glyphs_centered(draw, names, _fitted(names, WIDTH - 60, 120, 40), WIDTH / 2, 190)
    _scores(draw, sorted(scores, key=lambda item: -item[1]), 360)
    return encode_png(canvas)
