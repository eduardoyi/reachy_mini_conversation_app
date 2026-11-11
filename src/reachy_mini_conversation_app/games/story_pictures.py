"""Story pictures: the characters drawn the same way in every scene, made ahead of time (docs/plans/story-mode.md).

Following OpenAI's image prompting guide for picture books: each character gets a sheet first (a plain full-body
drawing, ``stories/cast/<role>.png``, approved by Eduardo), and every scene is an edit that takes the sheets of the
characters in it as reference images, restates each one's look from cast.yaml and says not to redesign them. One
style for everything, chosen for the TRMNL's four greys: black outlines, flat fills, no soft shading.

Scenes are drawn wide (3:2) and cut to the screen's 5:3, so the prompt keeps the action away from the top and
bottom edges. The screen version is kept next to the drawing, ready to show, so nothing is drawn while a story plays.
"""

from io import BytesIO
from typing import Final
from collections.abc import Mapping, Sequence

import numpy as np
from PIL import Image, ImageOps, ImageFilter

from reachy_mini_conversation_app.eink_display import WIDTH, HEIGHT, _LEVEL_EDGES, encode_png


STYLES: Final[dict[str, str]] = {
    # Picture-book: bold outlines and flat greys (the first set, 2026-11-07; kept in the "-book" folders).
    "book": (
        "Style: a warm, cute children's picture-book illustration for 2 to 4 year olds, drawn as bold clean black "
        "ink outlines with flat fills in white, light grey and dark grey only, like a page from a black-and-white "
        "coloring storybook that has been neatly colored in grey. No color, no gradients, no soft shading, no "
        "hatching, no texture, no tiny details. Big simple shapes and friendly round faces with clear expressions. "
        "Simple background with a few big shapes and plenty of white. No text, no letters, no numbers, no speech "
        "bubbles, no watermark, no border."
    ),
    # A gentle hand-drawn children's anime film look, kept readable in four greys: the default for every story
    # (Eduardo picked it over the picture-book set, 2026-11-07).
    "anime": (
        "Style: a gentle, hand-drawn Japanese animated film look for young children, soft, warm and whimsical: "
        "rounded characters with big expressive shining eyes and rosy cheeks, delicate clean ink linework, simple "
        "two-tone cel shading, cozy countryside with rolling hills, big fluffy clouds and leafy trees, a sense of "
        "calm wonder. Rendered in black and white only, with white, light grey, mid grey and black, every area one "
        "flat grey tone with soft cel-shaded shadows as a second flat tone, so it reads clearly on a small e-ink "
        "screen: no color, no gradients, no painterly texture, no film grain, no tiny details. No text, no letters, "
        "no numbers, no speech bubbles, no watermark, no border."
    ),
}
DEFAULT_STYLE: Final[str] = "anime"
STYLE: Final[str] = STYLES[DEFAULT_STYLE]
# The screen is 5:3; drawings are 3:2, so a little is cut from the top and bottom.
DRAWING_SIZE: Final[str] = "1536x1024"
SHEET_SIZE: Final[str] = "1024x1024"


def sheet_prompt(role: str, look: str, style: str = DEFAULT_STYLE) -> str:
    """Return the prompt for ``role``'s character sheet: the reference every scene with it is drawn from."""
    return (
        f"A character reference sheet for a children's storybook character: {look}. One single full-body view of "
        "the character standing in a friendly relaxed pose, three-quarter view, centered, the whole body visible "
        "with space around it, on a pure white background (#FFFFFF) all the way to the edges: no dark background, no vignette, no glow, no shadow, nothing else. This drawing is the reference for "
        f"the same character in every scene of the book, so draw the defining details clearly. {STYLES[style]}"
    )


def turnaround_prompt(role: str, look: str, style: str = DEFAULT_STYLE) -> str:
    """Return the prompt for ``role``'s turnaround sheet, drawn from its single sheet (the reference image).

    One pose on the sheet made every scene copy its head angle and smile (Eduardo, 2026-11-11); the turnaround shows
    the character from every side and with several expressions, so a scene keeps who it is but not how it stands.
    """
    return (
        f"A character turnaround sheet for a children's storybook character: {look}. The reference image is this "
        "character: keep exactly the same face, hair, proportions, outfit, colours of grey and accessories. Top row: "
        "four full-body views side by side, front, three-quarter, side profile and back, standing neutral. Bottom row: "
        "five head close-ups with different expressions and head angles: laughing looking up, surprised looking to the "
        "side, worried looking down, determined in profile, sleepy with eyes closed. Pure white background (#FFFFFF) "
        f"all the way to the edges, no text, no labels, no frames. {STYLES[style]}"
    )


def scene_prompt(
    scene: str, cast: Sequence[str], looks: Mapping[str, str], style: str = DEFAULT_STYLE, *, turnaround: bool = False
) -> str:
    """Return the prompt for a scene showing ``scene`` with the characters ``cast``, drawn from their sheets."""
    if cast and turnaround:
        references = " ".join(
            f"Image {number} is the turnaround sheet of {looks[role].split(':')[0].split(',')[0]}: {looks[role]}."
            for number, role in enumerate(cast, 1)
        )
        keep = (
            f"{references} Each sheet shows who the character is from every side and with several expressions: keep "
            "the same face, proportions, hair, outfit and accessories, and the same drawing style, but never copy a "
            "pose, head angle or expression from the sheet. Act the moment out: choose for each character the body "
            "pose, the head turn and tilt, where the eyes look and the expression that this moment calls for. "
            "Characters look at each other or at what they are doing, not at the viewer; they turn, lean, crouch, "
            "reach and run; seen from the side, from behind or from above when the moment suits it; surprised, "
            "worried, sleepy or laughing as the scene says, not the same smile every time. Do not redesign the "
            "characters, do not add or swap clothes and do not add other characters. Keep every body as on its sheet: "
            "the same number of arms, legs, paws, wings and hands, never an extra limb. Draw each character once."
        )
    elif cast:
        references = " ".join(
            f"Image {number} is the character sheet of {looks[role].split(':')[0].split(',')[0]}: {looks[role]}."
            for number, role in enumerate(cast, 1)
        )
        keep = (
            f"{references} Draw these characters exactly as on their sheets: the same face, proportions, ears, "
            "outfit and accessories. Do not redesign the characters, do not add or swap clothes, and do not add "
            "other characters. Use the same drawing style as the sheets. Keep every body exactly as on its sheet: "
            "the same number of arms, legs, paws, wings and hands, never an extra limb, paw or hand. Animals drawn "
            "on four legs stay on four legs and hold nothing in hands they do not have; a bird holds things in the "
            "tips of its wings."
        )
    else:
        keep = "There are no characters in this picture."
    return (
        f"An illustration for a children's storybook, one scene: {scene}. {keep} Wide landscape composition with "
        f"the characters large and the action in the middle band, nothing important near the top or bottom edge. "
        f"{STYLES[style]}"
    )


# Styles with soft shading are dithered to the four greys, as photos are; flat ones are snapped to solid greys.
DITHERED: Final[frozenset[str]] = frozenset({"anime"})


def screen_png(drawing: bytes | Image.Image, style: str = DEFAULT_STYLE) -> bytes:
    """Return ``drawing`` as the TRMNL's full screen: cut to 5:3 from the middle, in its four greys, no words."""
    image = drawing if isinstance(drawing, Image.Image) else Image.open(BytesIO(drawing))
    fitted = ImageOps.fit(image.convert("L"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    fitted = ImageOps.autocontrast(fitted, cutoff=0.5)
    if style in DITHERED:  # snapping soft shading to four flat greys turned it to mud (2026-11-07)
        palette = Image.new("P", (1, 1))
        palette.putpalette([level for level in (0, 85, 170, 255) for _ in range(3)])
        return encode_png(fitted.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG))
    # The drawings' fills have a faint texture, and a fill whose shade sits near the edge between two of the four
    # greys came out speckled (a pig's face, 2026-11-07). The fills are smoothed before they are snapped to the
    # greys, while the black outlines are kept sharp from the drawing itself.
    sharp = np.asarray(fitted)
    dark = sharp < _LEVEL_EDGES[1]  # outlines with their soft edges, and dark fills: never speckled, kept as drawn
    # Each fill is averaged over itself only: the outlines are left out, so they leave no grey halo around them.
    fill = Image.fromarray(np.where(dark, 0, sharp).astype(np.uint8), mode="L").filter(ImageFilter.GaussianBlur(4))
    weight = Image.fromarray(np.where(dark, 0, 255).astype(np.uint8), mode="L").filter(ImageFilter.GaussianBlur(4))
    smooth = np.asarray(fill, dtype=np.float32) * 255 / np.maximum(np.asarray(weight, dtype=np.float32), 1)
    levels = np.digitize(smooth, _LEVEL_EDGES).astype(np.uint8) * 85
    levels[dark] = np.digitize(sharp[dark], _LEVEL_EDGES).astype(np.uint8) * 85
    solid = Image.fromarray(levels, mode="L").filter(ImageFilter.ModeFilter(3))
    return encode_png(solid)


def styled(name: str, style: str) -> str:
    """Return the folder ``name`` for ``style``: the default style's is ``name``, another's ``name-<style>``."""
    return name if style == DEFAULT_STYLE else f"{name}-{style}"
