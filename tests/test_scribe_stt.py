"""Tests for game answers heard by ElevenLabs Scribe v2: the request it sends and the words it accepts."""

import json

import httpx
import numpy as np
import pytest

from reachy_mini_conversation_app.scribe_stt import SCRIBE_URL, MAX_KEYTERMS, ScribeTranscriber, keyterms


def test_keyterms_are_cleaned_unique_and_capped() -> None:
    """Refused characters go, long phrases and repeats are dropped, and at most MAX_KEYTERMS are kept."""
    assert keyterms(["Plutón", "pluTÓN", "  Mar   Caribe ", "<Úrsula>", "a b c d e f", ""]) == [
        "Plutón",
        "Mar Caribe",
        "Úrsula",
    ]
    assert len(keyterms(f"word{n}" for n in range(500))) == MAX_KEYTERMS
    assert keyterms(["x" * 80]) == ["x" * 50]


@pytest.mark.asyncio
async def test_a_clip_goes_as_16_khz_pcm_with_the_game_words() -> None:
    """Scribe v2 gets raw 16 kHz PCM, no audio-event tags, and the hints as keyterms; its text comes back."""
    sent: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"text": " Plutón ", "language_code": "es"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    scribe = ScribeTranscriber("key", client=client)
    one_second_at_24k = np.full(24_000, 1_000, dtype=np.int16)
    assert await scribe((24_000, one_second_at_24k), ["Plutón", "Pluto"]) == "Plutón"
    request = sent[0]
    assert str(request.url) == SCRIBE_URL and request.headers["xi-api-key"] == "key"
    body = request.read()
    for field in (b'name="model_id"', b"scribe_v2", b"pcm_s16le_16", b'name="tag_audio_events"'):
        assert field in body
    assert body.count(b'name="keyterms"') == 2 and b"Plut\xc3\xb3n" in body
    assert 31_000 < len(body) - 1_500 < 33_500  # about one second of 16 kHz 16-bit audio, plus the form
    await client.aclose()


@pytest.mark.asyncio
async def test_a_failed_request_raises_so_the_local_model_can_hear_it() -> None:
    """An error from Scribe is not an empty answer: it raises, and the runtime falls back."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, text=json.dumps({}))))
    with pytest.raises(httpx.HTTPStatusError):
        await ScribeTranscriber("key", client=client)((16_000, np.zeros(1_600, dtype=np.int16)))
    await client.aclose()
