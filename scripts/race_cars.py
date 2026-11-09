"""Make the number race's two cars from the story's character sheets (docs/plans/number-race.md).

Tito and Flecha on the race screen are the cars of *La gran carrera*, shrunk from their sheets
(``stories/cast/tito.png`` and ``flecha.png``) to the screen's four greys and facing the finish (right). The
sheets are not shipped with the app, so the small pictures are kept in ``static/eink/`` (``race_tito.png``,
``race_flecha.png``), each greyscale with transparency around the car. Rerun after a sheet changes; no image
calls, nothing costs anything.

Usage: uv run python scripts/race_cars.py
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, ImageDraw


APP = Path(__file__).resolve().parents[1] / "src" / "reachy_mini_conversation_app"
SHEETS = APP / "stories" / "cast"
OUT = APP / "static" / "eink"
# Name: (height on the screen in pixels, mirrored so it faces right). Flecha is the low one.
CARS: dict[str, tuple[int, bool]] = {"tito": (88, True), "flecha": (72, False)}
# The screen's greys (eink_display.encode_png); the car's grey body falls on light grey, its shading on grey.
LEVELS = np.array([0, 85, 170, 255], dtype=np.uint8)
EDGES = (60, 118, 228)
_PAPER = 236  # lighter than this, at the edge of the sheet, is the paper around the car


def car(name: str, height: int, mirrored: bool) -> Image.Image:
    """Return ``name``'s sheet as a ``height``-pixel-tall car in the four greys, transparent around it."""
    sheet = Image.open(SHEETS / f"{name}.png").convert("L")
    ys, xs = np.nonzero(np.asarray(sheet) < _PAPER)
    sheet = sheet.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    if mirrored:
        sheet = ImageOps.mirror(sheet)
    small = sheet.resize((round(sheet.width * height / sheet.height), height), Image.Resampling.LANCZOS)
    grey = Image.fromarray(LEVELS[np.digitize(np.asarray(small), EDGES)])
    # The paper is the white reached from the border; white inside the car (eyes, windows, stripes) stays.
    paper = Image.fromarray(np.where(np.asarray(small) >= _PAPER, 255, 0).astype(np.uint8))
    framed = ImageOps.expand(paper, 1, fill=255)
    ImageDraw.floodfill(framed, (0, 0), 128)
    alpha = Image.fromarray(np.where(np.asarray(framed)[1:-1, 1:-1] == 128, 0, 255).astype(np.uint8))
    picture = Image.merge("LA", (grey, alpha))
    return picture


def main() -> None:
    """Write both cars into static/eink/."""
    for name, (height, mirrored) in CARS.items():
        picture = car(name, height, mirrored)
        picture.save(OUT / f"race_{name}.png", optimize=True)
        print(f"race_{name}.png: {picture.width}x{picture.height}")


if __name__ == "__main__":
    main()
