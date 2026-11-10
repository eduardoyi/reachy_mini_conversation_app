"""The intercom's pieces: the chime, the phone's voice, and the bridge between the two loops."""

import asyncio

import numpy as np
import pytest

from reachy_mini_conversation_app import intercom
from reachy_mini_conversation_app.intercom import (
    HEARD_QUEUE_FRAMES,
    INTERCOM_SAMPLE_RATE,
    MAX_TALK_CHUNK_BYTES,
    Talk,
    IntercomSession,
    chime,
    talk_samples,
)


def test_the_chime_is_short_soft_and_ends_quiet() -> None:
    """Under half a second, well below full scale, fading out so it does not click."""
    sound = chime()
    assert sound.dtype == np.int16 and len(sound) / INTERCOM_SAMPLE_RATE < 0.6
    assert np.abs(sound).max() < 0.35 * 32767
    assert np.abs(sound[-40:]).max() < 0.1 * np.abs(sound).max()


@pytest.mark.parametrize(
    ("chunk", "ok"),
    [(b"\x01\x00\x02\x00", True), (b"", False), (b"\x01", False), (b"\x00" * (MAX_TALK_CHUNK_BYTES + 2), False)],
)
def test_only_whole_bounded_pcm_is_taken_from_the_phone(chunk: bytes, ok: bool) -> None:
    """Empty, odd-length or oversized pieces are dropped."""
    samples = talk_samples(chunk)
    assert (samples is not None) == ok
    if samples is not None:
        assert samples.tolist() == [1, 2]


@pytest.mark.asyncio
async def test_a_slow_phone_hears_the_latest_frames() -> None:
    """When the phone falls behind, the oldest frames go, so what it hears stays live."""
    session = IntercomSession(asyncio.get_running_loop())
    for level in range(HEARD_QUEUE_FRAMES + 5):
        session.hear((16_000, np.full(160, level * 100, dtype=np.int16)))
    await asyncio.sleep(0)
    heard = session.heard()
    first = np.frombuffer(await anext(heard), dtype="<i2")
    assert int(first[0]) == pytest.approx(500, abs=1)


@pytest.mark.asyncio
async def test_a_talk_ends_when_the_phone_lets_go_or_goes_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pieces play in order until the end; a dropped connection ends the talk after TALK_GAP_S."""
    loop = asyncio.get_running_loop()
    talk = Talk(loop)
    talk.say(np.full(4, 1, dtype=np.int16))
    talk.say(np.full(4, 2, dtype=np.int16))
    talk.end()
    assert [int(piece[0]) async for piece in talk.pieces()] == [1, 2]

    monkeypatch.setattr(intercom, "TALK_GAP_S", 0.05)
    quiet = Talk(loop)
    quiet.say(np.full(4, 3, dtype=np.int16))
    assert [int(piece[0]) async for piece in quiet.pieces()] == [3]
