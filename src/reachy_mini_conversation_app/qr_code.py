"""A small QR code encoder: byte mode, low error correction, versions 1 to 5 (up to 106 bytes).

Enough for a link on the e-ink screen (Big Brain's join address). Written here because adding a QR library
would mean re-resolving the project's lock, which on 2026-11-01 failed on an unrelated package's build. It
follows the QR standard (ISO/IEC 18004) as Project Nayuki's reference encoder lays it out: data and Reed-Solomon
codewords in one block, the usual finder, timing and alignment patterns, and the mask with the lowest penalty.
"""

from typing import Final, Callable


# Data and error-correction codewords at level L, by version.
_DATA: Final[dict[int, int]] = {1: 19, 2: 34, 3: 55, 4: 80, 5: 108}
_EC: Final[dict[int, int]] = {1: 7, 2: 10, 3: 15, 4: 20, 5: 26}
_ALIGNMENT: Final[dict[int, int]] = {2: 18, 3: 22, 4: 26, 5: 30}
_LEVEL_L: Final[int] = 1


def _multiply(x: int, y: int) -> int:
    """Multiply in GF(256) modulo x^8 + x^4 + x^3 + x^2 + 1."""
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _reed_solomon(data: list[int], degree: int) -> list[int]:
    """Return the ``degree`` error-correction codewords for ``data``."""
    divisor = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            divisor[j] = _multiply(divisor[j], root)
            if j + 1 < degree:
                divisor[j] ^= divisor[j + 1]
        root = _multiply(root, 0x02)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder.pop(0)
        remainder.append(0)
        for i, coefficient in enumerate(divisor):
            remainder[i] ^= _multiply(coefficient, factor)
    return remainder


def _codewords(text: str) -> tuple[int, list[int]]:
    """Return the smallest version that holds ``text`` and its data plus error-correction codewords."""
    data = text.encode("utf-8")
    version = next((v for v, capacity in _DATA.items() if len(data) + 2 <= capacity), None)
    if version is None:
        raise ValueError(f"too long for a small QR code: {len(data)} bytes")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in range(7, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    capacity = _DATA[version] * 8
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < _DATA[version]:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    return version, words + _reed_solomon(words, _EC[version])


class _Grid:
    def __init__(self, version: int) -> None:
        self.size = 17 + 4 * version
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True


def _format_bits(mask: int) -> int:
    data = _LEVEL_L << 3 | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return (data << 10 | remainder) ^ 0x5412


def _draw_format(grid: _Grid, mask: int) -> None:
    bits = _format_bits(mask)
    size = grid.size

    def bit(i: int) -> bool:
        return bool((bits >> i) & 1)

    for i in range(6):
        grid.set(8, i, bit(i))
    grid.set(8, 7, bit(6))
    grid.set(8, 8, bit(7))
    grid.set(7, 8, bit(8))
    for i in range(9, 15):
        grid.set(14 - i, 8, bit(i))
    for i in range(8):
        grid.set(size - 1 - i, 8, bit(i))
    for i in range(8, 15):
        grid.set(8, size - 15 + i, bit(i))
    grid.set(8, size - 8, True)


def _draw_patterns(grid: _Grid, version: int) -> None:
    size = grid.size
    for i in range(size):
        grid.set(6, i, i % 2 == 0)
        grid.set(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    grid.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    if version in _ALIGNMENT:
        center = _ALIGNMENT[version]
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                grid.set(center + dx, center + dy, max(abs(dx), abs(dy)) != 1)
    _draw_format(grid, 0)  # reserves the format areas; redrawn with the chosen mask


def _place(grid: _Grid, codewords: list[int]) -> None:
    size, index = grid.size, 0
    total = len(codewords) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vertical in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vertical if upward else vertical
                if not grid.fixed[y][x] and index < total:
                    grid.dark[y][x] = bool((codewords[index >> 3] >> (7 - (index & 7))) & 1)
                    index += 1
        right -= 2


_MASKS: Final[tuple[Callable[[int, int], bool], ...]] = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _masked(grid: _Grid, mask: int) -> list[list[bool]]:
    test = _MASKS[mask]
    return [
        [grid.dark[y][x] ^ (not grid.fixed[y][x] and test(x, y)) for x in range(grid.size)] for y in range(grid.size)
    ]


def _penalty(modules: list[list[bool]]) -> int:
    """Score a masked symbol by the standard's four rules (lower reads better)."""
    size, score = len(modules), 0
    lines = modules + [list(column) for column in zip(*modules)]
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
            else:
                score += run - 2 if run >= 5 else 0
                run = 1
        text = "".join("1" if dark else "0" for dark in line)
        score += 40 * (text.count("10111010000") + text.count("00001011101"))
    for y in range(size - 1):
        for x in range(size - 1):
            if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                score += 3
    dark = sum(sum(row) for row in modules)
    score += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
    return score


def qr_matrix(text: str) -> list[list[bool]]:
    """Return the QR code for ``text`` as rows of modules (True is dark), without the quiet zone."""
    version, codewords = _codewords(text)
    grid = _Grid(version)
    _draw_patterns(grid, version)
    _place(grid, codewords)
    best: list[list[bool]] | None = None
    best_score = 0
    for mask in range(8):
        _draw_format(grid, mask)
        modules = _masked(grid, mask)
        score = _penalty(modules)
        if best is None or score < best_score:
            best, best_score = modules, score
    assert best is not None
    return best


def qr_svg(text: str, quiet: int = 2) -> str:
    """Return the QR code for ``text`` as an SVG drawing, dark modules on white with a ``quiet``-module margin."""
    modules = qr_matrix(text)
    size = len(modules) + 2 * quiet
    path = "".join(
        f"M{x + quiet} {y + quiet}h1v1h-1z" for y, row in enumerate(modules) for x, dark in enumerate(row) if dark
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>'
    )
