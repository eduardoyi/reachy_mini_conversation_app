"""Game answers heard by ElevenLabs Scribe v2, with the local request model as the fallback.

Only what is said right after a game's beep or question (a quiz answer, a guess, a name, "¿otra vez?") goes to
the cloud: Eduardo agreed on 2026-11-03, since these are game words, not the household's conversation. Everything
else Reachy hears stays on the Mac mini. Qwen3-ASR misheard short answers often enough that the room overruled
it a few times a game; Scribe v2 led the 2026 benchmarks on Latin American Spanish and on Spanish-English mixing.

A game names the words it expects (``keyterms``: the board's answers, the survey's, the characters', the kids'
names), which biases Scribe toward them. At most MAX_KEYTERMS go with a request: more would bill each answer as
at least 20 s. The audio goes as raw 16 kHz PCM, Scribe's fastest input. Only counts are logged, never words.
"""

import re
import time
import logging
from typing import Final
from collections.abc import Iterable, Sequence

import httpx
import numpy as np

from reachy_mini_conversation_app.streaming import resample_audio, audio_to_float32, audio_to_mono_int16
from reachy_mini_conversation_app.conversation_handler import AudioFrame


logger = logging.getLogger(__name__)

SCRIBE_URL: Final[str] = "https://api.elevenlabs.io/v1/speech-to-text"
SCRIBE_MODEL: Final[str] = "scribe_v2"
SCRIBE_RATE: Final[int] = 16_000
MAX_KEYTERMS: Final[int] = 100
# Scribe's own limits on a keyterm, and the characters it refuses.
_KEYTERM_CHARS: Final[int] = 50
_KEYTERM_WORDS: Final[int] = 5
_REFUSED: Final[re.Pattern[str]] = re.compile(r"[<>{}\[\]\\]")


def keyterms(words: Iterable[str]) -> list[str]:
    """Return the words as Scribe accepts them: cleaned, unique (ignoring case), the first MAX_KEYTERMS only."""
    kept: list[str] = []
    seen: set[str] = set()
    for word in words:
        term = " ".join(_REFUSED.sub(" ", str(word)).split())[:_KEYTERM_CHARS].strip()
        if not term or len(term.split()) > _KEYTERM_WORDS or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        kept.append(term)
        if len(kept) == MAX_KEYTERMS:
            break
    return kept


def _pcm16k(audio: AudioFrame) -> bytes:
    """Return the frame as mono 16 kHz little-endian 16-bit PCM."""
    rate, samples = audio
    mono = audio_to_mono_int16(samples)
    if rate != SCRIBE_RATE and mono.size:
        resampled = resample_audio(audio_to_float32(mono), rate, SCRIBE_RATE)
        mono = np.clip(np.rint(resampled.astype(np.float64) * 32768.0), -32768, 32767).astype(np.int16)
    return mono.astype("<i2").tobytes()


class ScribeTranscriber:
    """Transcribe one short game answer with Scribe v2; raises on any failure, so the caller can fall back."""

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None, timeout_s: float = 4.0) -> None:
        """Use ``api_key`` (ElevenLabs); ``client`` is for tests."""
        self._api_key = api_key
        self._client = client
        self._timeout_s = timeout_s

    async def __call__(self, audio: AudioFrame, hints: Sequence[str] = ()) -> str:
        """Return what was said in ``audio`` ("" for nothing), biased toward ``hints``."""
        terms = keyterms(hints)
        started = time.monotonic()
        data: dict[str, str | list[str]] = {
            "model_id": SCRIBE_MODEL,
            "file_format": "pcm_s16le_16",
            "tag_audio_events": "false",
        }
        if terms:
            data["keyterms"] = terms
        files = {"file": ("answer.pcm", _pcm16k(audio), "application/octet-stream")}
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        try:
            response = await client.post(
                SCRIBE_URL, headers={"xi-api-key": self._api_key}, data=data, files=files, timeout=self._timeout_s
            )
            response.raise_for_status()
            text = str(response.json().get("text") or "").strip()
        finally:
            if self._client is None:
                await client.aclose()
        logger.info(
            "Game answer heard by Scribe audio_s=%.1f chars=%d hints=%d ms=%d",
            audio[1].shape[-1] / audio[0],
            len(text),
            len(terms),
            (time.monotonic() - started) * 1000,
        )
        return text
