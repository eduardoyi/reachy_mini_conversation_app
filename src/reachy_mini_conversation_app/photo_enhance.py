"""Touch up a photo before people see it: levels, brightness, colour, chroma noise and sharpness.

Reachy's camera exposes for the ceiling spots at night and lifts its blacks: the 2026-10-31 evening photo had
nothing darker than 38/255, a median of 61 and 99 % of it under 141, so it looked dark and grey at once. Unlike
``camera_frames.boost_dim_frame`` (for what Reachy looks at himself), only brightness gets the strong curve here:
colour is scaled by the plain stretch, the camera's faint green in the shadows is neutralised, and colour noise is
blurred away. A well exposed photo is barely changed (a slight lift in saturation and sharpness).
"""

import logging
from typing import Final

import numpy as np
from PIL import Image, ImageFilter
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

# Levels come from a quarter-size copy; the darkest and brightest 0.5 % may clip.
LEVELS_DOWNSCALE: Final[int] = 4
CLIP_PERCENT: Final[float] = 0.5
# A black point above this is the scene, not the camera's lift.
MAX_BLACK: Final[float] = 64.0
# The stretch never more than 2.5x brightness: beyond that a dim photo is mostly sensor noise.
MAX_STRETCH: Final[float] = 2.5
# Below this spread between the dark and bright ends the frame is a wall or a covered lens: left alone.
MIN_SPREAD: Final[float] = 32.0
# Mid-tones are brightened (never darkened) toward this share of the range, with a gamma of at least 0.55.
MID_TARGET: Final[float] = 0.40
MIN_GAMMA: Final[float] = 0.55
# The shadows' tint is measured on the darkest fifth and fades out halfway up the range, where colour
# is also turned down by up to a third: in the deep shadows it is mostly noise.
SHADOW_PERCENTILE: Final[float] = 20.0
SHADOW_REACH: Final[float] = 0.5
SHADOW_DESATURATE: Final[float] = 0.35
MAX_CHROMA_STRETCH: Final[float] = 2.0
SATURATION: Final[float] = 1.1
CHROMA_BLUR_RADIUS: Final[float] = 2.0
SHARPEN_RADIUS: Final[float] = 2.0
SHARPEN_PERCENT: Final[int] = 60
SHARPEN_THRESHOLD: Final[int] = 3


def enhance_photo(frame_bgr: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Return ``frame_bgr`` touched up for people to look at; a frame with almost no tonal range unchanged."""
    ycc = Image.fromarray(np.ascontiguousarray(frame_bgr[..., ::-1])).convert("YCbCr")
    small = np.asarray(ycc.reduce(LEVELS_DOWNSCALE), dtype=np.float32)
    luma = small[..., 0]
    black, white = (float(v) for v in np.percentile(luma, (CLIP_PERCENT, 100 - CLIP_PERCENT)))
    if white - black < MIN_SPREAD:
        return frame_bgr
    black = min(black, MAX_BLACK)
    white = min(255.0, max(white, black + 255.0 / MAX_STRETCH))
    span = white - black
    middle = float(np.clip((np.median(luma) - black) / span, 0.01, 0.99))
    gamma = float(np.clip(np.log(MID_TARGET) / np.log(middle), MIN_GAMMA, 1.0))
    cast = small[luma <= np.percentile(luma, SHADOW_PERCENTILE)][:, 1:].mean(axis=0) - 128.0
    chroma = min(255.0 / span, MAX_CHROMA_STRETCH) * SATURATION

    levels = np.arange(256, dtype=np.float32)
    luma_lut = np.round(np.clip((levels - black) / span, 0.0, 1.0) ** gamma * 255.0).astype(np.uint8)
    full = np.asarray(ycc)
    shadow = np.clip(1.0 - (full[..., 0].astype(np.float32) - black) / (SHADOW_REACH * span), 0.0, 1.0)
    gain = chroma * (1.0 - SHADOW_DESATURATE * shadow)
    planes = [luma_lut[full[..., 0]]]
    for channel in (1, 2):
        tinted = full[..., channel].astype(np.float32) - 128.0 - cast[channel - 1] * shadow
        planes.append(np.clip(128.0 + tinted * gain, 0, 255).astype(np.uint8))

    y, cb, cr = (Image.fromarray(plane) for plane in planes)
    y = y.filter(ImageFilter.UnsharpMask(SHARPEN_RADIUS, SHARPEN_PERCENT, SHARPEN_THRESHOLD))
    cb = cb.filter(ImageFilter.GaussianBlur(CHROMA_BLUR_RADIUS))
    cr = cr.filter(ImageFilter.GaussianBlur(CHROMA_BLUR_RADIUS))
    rgb = np.asarray(Image.merge("YCbCr", (y, cb, cr)).convert("RGB"))
    logger.debug("Photo enhanced black=%.0f white=%.0f gamma=%.2f chroma=%.2f", black, white, gamma, chroma)
    return np.ascontiguousarray(rgb[..., ::-1])
