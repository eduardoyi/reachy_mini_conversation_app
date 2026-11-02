import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.photo_enhance import enhance_photo


def _scene(
    low: float, high: float, tint: tuple[float, float, float] = (0.0, 0.0, 0.0), curve: float = 2.0
) -> NDArray[np.uint8]:
    """Return a BGR room: a gradient (mostly dark at ``curve`` 2) with a red patch, in ``low``..``high``, plus a shadow tint."""
    rng = np.random.default_rng(0)
    height, width = 240, 320
    ramp = np.linspace(0.0, 1.0, width, dtype=np.float32) ** curve
    frame = np.repeat(np.repeat(ramp[None, :, None], height, 0), 3, 2)
    frame[60:120, 200:260] = (0.25, 0.3, 0.7)  # BGR: a red shirt
    frame = low + frame * (high - low) + rng.normal(0.0, 1.0, frame.shape)
    frame += np.asarray(tint, dtype=np.float32) * (1.0 - ramp[None, :, None])
    return np.clip(frame, 0, 255).astype(np.uint8)


def test_a_dark_grey_photo_gets_real_blacks_and_brighter_mid_tones() -> None:
    """The evening look (blacks lifted to ~40, little above 150) is stretched and brightened."""
    night = _scene(40.0, 150.0)

    out = enhance_photo(night)

    assert out.shape == night.shape and out.dtype == np.uint8
    assert np.percentile(out, 1) < 10 < np.percentile(night, 1)
    assert np.median(out) > np.median(night) + 25


def test_colours_stay_in_bgr_order() -> None:
    """The red patch is still red afterwards, and redder than before."""
    night = _scene(40.0, 150.0)

    patch = enhance_photo(night)[70:110, 210:250].astype(np.float32).mean(axis=(0, 1))
    before = night[70:110, 210:250].astype(np.float32).mean(axis=(0, 1))

    assert patch[2] > patch[1] and patch[2] > patch[0]
    assert patch[2] - patch[0] > before[2] - before[0]


def test_a_green_tint_in_the_shadows_is_neutralised() -> None:
    """The camera's faint green in the shadows is not magnified by the brightening."""
    night = _scene(40.0, 150.0, tint=(-2.0, 2.0, -1.0))

    shadows = enhance_photo(night)[:, :40].astype(np.float32).mean(axis=(0, 1))

    assert shadows[1] - max(shadows[0], shadows[2]) < 2.0


def test_a_well_exposed_photo_is_barely_changed() -> None:
    """Full range and mid-tones in the middle: levels and brightness are left alone."""
    day = _scene(5.0, 250.0, curve=1.0)

    out = enhance_photo(day)

    assert np.abs(out.astype(np.float32) - day.astype(np.float32)).mean() < 8.0


def test_a_frame_with_almost_no_range_is_left_alone() -> None:
    """A wall or a covered lens is not stretched into grey noise."""
    flat = _scene(29.0, 36.0)

    assert enhance_photo(flat) is flat
