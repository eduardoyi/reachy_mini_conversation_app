"""Tests for quick-reply speech (Gemini TTS replaced by a fake client)."""

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import numpy as np
import pytest

from reachy_mini_conversation_app import local_speech
from reachy_mini_conversation_app.local_speech import SAMPLE_RATE, LocalSpeech


def _chunk(data: bytes) -> Any:
    part = SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type="audio/l16; rate=24000"))
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))], usage_metadata=None)


class _Client:
    def __init__(self, chunks: list[bytes] | None = None, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._chunks = chunks or []
        self._error = error
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content_stream=self._stream))

    async def _stream(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append(contents)
        if self._error is not None:
            raise self._error
        chunks = self._chunks

        async def generate() -> Any:
            for data in chunks:
                yield _chunk(data)

        return generate()


async def _collect(speech: LocalSpeech, text: str) -> np.ndarray:
    frames = [frame async for rate, frame in speech.stream(text, "en") if rate == SAMPLE_RATE]
    return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)


@pytest.mark.asyncio
async def test_streamed_audio_is_yielded_and_short_phrases_are_cached(tmp_path: Path) -> None:
    """The first "Okay." comes from Gemini; the second from disk, without a request."""
    pcm = np.arange(10, dtype="<i2").tobytes()
    client = _Client([pcm[:8], pcm[8:]])
    speech = LocalSpeech(None, "Puck", cache_dir=tmp_path, client=client)

    first = await _collect(speech, "Okay.")
    second = await _collect(speech, "Okay.")

    assert first.tolist() == list(range(10)) == second.tolist()
    assert client.calls == ["Okay."]


@pytest.mark.asyncio
async def test_long_replies_are_not_cached(tmp_path: Path) -> None:
    """Weather and time sentences change; only short fixed phrases without numbers are kept."""
    client = _Client([np.zeros(4, dtype="<i2").tobytes()])
    speech = LocalSpeech(None, "Puck", cache_dir=tmp_path, client=client)
    text = "In Lisbon it's 18 degrees and partly cloudy today."
    await _collect(speech, text)
    await _collect(speech, text)
    await _collect(speech, "It's 9:05 AM.")
    await _collect(speech, "It's 9:05 AM.")
    assert client.calls == [text, text, "It's 9:05 AM.", "It's 9:05 AM."]


@pytest.mark.asyncio
async def test_the_mac_voice_speaks_when_gemini_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A network error falls back to macOS say."""
    monkeypatch.setattr(local_speech, "_say", lambda text, language: np.ones(5, dtype=np.int16))
    speech = LocalSpeech(None, "Puck", cache_dir=tmp_path, client=_Client(error=OSError("offline")))
    assert (await _collect(speech, "Okay.")).tolist() == [1] * 5


@pytest.mark.asyncio
async def test_chunks_that_split_a_sample_stay_aligned(tmp_path: Path) -> None:
    """Odd-sized chunks are joined so no sample is shifted into static."""
    pcm = np.array([1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000], dtype="<i2").tobytes()
    client = _Client([pcm[:5], pcm[5:12], pcm[12:]])
    speech = LocalSpeech(None, "Puck", cache_dir=tmp_path, client=client)
    assert (await _collect(speech, "A longer sentence that is not cached.")).tolist() == list(range(1000, 10001, 1000))


@pytest.mark.asyncio
async def test_only_listed_phrases_are_cached(tmp_path: Path) -> None:
    """With a phrase list, a short one-off reply ("Playing X.") is not kept."""
    client = _Client([np.zeros(4, dtype="<i2").tobytes()])
    speech = LocalSpeech(None, "Puck", cache_dir=tmp_path, cacheable=frozenset({"Okay."}), client=client)
    for _ in range(2):
        await _collect(speech, "Playing Bluey.")
        await _collect(speech, "Okay.")
    assert client.calls == ["Playing Bluey.", "Okay.", "Playing Bluey."]


@pytest.mark.asyncio
async def test_a_cached_phrase_plays_without_the_silence_around_it(tmp_path: Path) -> None:
    """Gemini's padding before and after a short phrase is cut from the cached copy, with a small margin."""
    margin = local_speech.SILENCE_MARGIN_SAMPLES
    voice = np.full(4800, 8000, dtype="<i2")
    padded = np.concatenate([np.zeros(7200, dtype="<i2"), voice, np.zeros(24000, dtype="<i2")])
    speech = LocalSpeech(None, "Puck", cache_dir=tmp_path, client=_Client([padded.tobytes()]))

    first = await _collect(speech, "Two!")
    second = await _collect(speech, "Two!")

    assert first.size == padded.size  # the first time plays as it streams
    assert second.size == voice.size + 2 * margin
    assert np.count_nonzero(second) == voice.size
