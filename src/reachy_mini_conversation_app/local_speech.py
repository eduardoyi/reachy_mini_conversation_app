"""Speak short replies without a cloud conversation: Gemini text-to-speech in Reachy's Live voice.

Quick commands ("turn off the lights", "what time is it?") answer with a sentence or a word. The
Gemini TTS model streams the first audio in about 0.4-0.6 s (measured 2026-10-18 with
``gemini-3.8-flash-lite-tts``) in the same prebuilt voice the Live conversation uses, so Reachy
sounds the same either way. Short fixed phrases ("Okay.", "Listo.") are cached on disk and play at
once. Without the network, macOS ``say`` speaks instead.
"""

from __future__ import annotations
import sys
import wave
import asyncio
import hashlib
import logging
import tempfile
from typing import Any, Final
from pathlib import Path
from collections.abc import AsyncIterator

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

SAMPLE_RATE: Final[int] = 24000
DEFAULT_TTS_MODEL: Final[str] = "gemini-3.8-flash-lite-tts"
# Phrases up to this length (and without digits) are kept on disk after the first time they are spoken.
CACHE_MAX_CHARS: Final[int] = 40
FIRST_AUDIO_TIMEOUT_S: Final[float] = 4.0
# A stream that stops sending for this long is abandoned.
CHUNK_TIMEOUT_S: Final[float] = 3.0
CHUNK_SAMPLES: Final[int] = SAMPLE_RATE // 10
# Gemini TTS pads short phrases with up to a second of silence ("¡Dos!": 0.3 s before, 1.05 s after,
# measured 2026-10-29); cached phrases play without it, keeping a little around the voice.
SILENCE_LEVEL: Final[int] = 328  # 1% of full scale
SILENCE_MARGIN_SAMPLES: Final[int] = SAMPLE_RATE // 20
_SAY_VOICES: Final[dict[str, str]] = {"en": "Samantha", "es": "Mónica", "it": "Alice", "pt": "Joana"}


def default_cache_dir() -> Path:
    """Return the on-disk phrase cache."""
    return Path.home() / ".cache" / "reachy-mini-conversation-app" / "speech"


def _pcm16(data: bytes) -> NDArray[np.int16]:
    usable = len(data) - len(data) % 2
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.int16)


def _trim_silence(audio: NDArray[np.int16]) -> NDArray[np.int16]:
    voiced = np.flatnonzero(np.abs(audio.astype(np.int32)) > SILENCE_LEVEL)
    if not voiced.size:
        return audio
    start = max(0, int(voiced[0]) - SILENCE_MARGIN_SAMPLES)
    return audio[start : int(voiced[-1]) + 1 + SILENCE_MARGIN_SAMPLES]


def beep() -> NDArray[np.int16]:
    """Return a short rising two-note beep ("recording now") at ``SAMPLE_RATE``."""
    notes = []
    for frequency in (880.0, 1320.0):
        time_s = np.arange(int(SAMPLE_RATE * 0.12)) / SAMPLE_RATE
        fade = np.minimum(1.0, np.minimum(time_s, time_s[-1] - time_s) / 0.01)
        notes.append(np.sin(2 * np.pi * frequency * time_s) * fade * 0.35 * 32767)
    return np.concatenate(notes).astype(np.int16)


def _chunks(audio: NDArray[np.int16]) -> list[NDArray[np.int16]]:
    return [audio[start : start + CHUNK_SAMPLES] for start in range(0, audio.size, CHUNK_SAMPLES)]


class LocalSpeech:
    """Turn reply text into 24 kHz mono PCM16 frames."""

    def __init__(
        self,
        api_key: str | None,
        voice: str,
        *,
        model: str = DEFAULT_TTS_MODEL,
        cache_dir: Path | None = None,
        cacheable: frozenset[str] | None = None,
        client: Any = None,
    ) -> None:
        """Speak with Gemini ``voice`` through ``model``; ``client`` replaces google-genai in tests.

        ``cacheable`` lists the fixed phrases to keep on disk ("Okay.", "Listo."); without it, any short
        phrase without digits is cached.
        """
        self._cacheable = cacheable
        self._voice = voice
        self._model = model
        self._cache_dir = cache_dir or default_cache_dir()
        self._client = client
        if self._client is None and api_key:
            from google import genai

            self._client = genai.Client(api_key=api_key)

    def _cache_path(self, text: str) -> Path:
        digest = hashlib.sha256(f"{self._model}|{self._voice}|{text}".encode()).hexdigest()[:24]
        return self._cache_dir / f"{digest}.pcm"

    async def stream(self, text: str, language: str = "en") -> AsyncIterator[tuple[int, NDArray[np.int16]]]:
        """Yield ``(sample_rate, frame)`` pieces of ``text`` spoken, as soon as they exist."""
        text = text.strip()
        if not text:
            return
        # Fixed phrases only: times, dates and song titles would fill the cache with one-offs.
        if self._cacheable is not None:
            cacheable = text in self._cacheable
        else:
            cacheable = len(text) <= CACHE_MAX_CHARS and not any(character.isdigit() for character in text)
        cache_path = self._cache_path(text)
        if cacheable and cache_path.is_file():
            for frame in _chunks(_trim_silence(_pcm16(cache_path.read_bytes()))):
                yield SAMPLE_RATE, frame
            return
        spoken = bytearray()
        emitted = 0  # bytes of ``spoken`` already yielded; chunks may split a sample
        try:
            async for data in self._gemini(text):
                spoken.extend(data)
                usable = len(spoken) - len(spoken) % 2
                frame = _pcm16(bytes(spoken[emitted:usable]))
                emitted = usable
                if frame.size:
                    yield SAMPLE_RATE, frame
        except Exception as error:  # network, quota or SDK errors: fall back to the Mac's voice
            logger.warning("Gemini speech failed (%s); using the local voice", type(error).__name__)
            if spoken:
                return  # part of the reply was already heard; do not repeat it in another voice
            for frame in _chunks(await asyncio.to_thread(_say, text, language)):
                yield SAMPLE_RATE, frame
            return
        if cacheable and spoken:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(bytes(spoken))
            except OSError as error:
                logger.debug("Speech cache not written: %s", type(error).__name__)

    async def _gemini(self, text: str) -> AsyncIterator[bytes]:
        if self._client is None:
            raise RuntimeError("no Gemini API key")
        from google.genai import types

        from reachy_mini_conversation_app.usage_costs import get_ledger

        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice))
            ),
        )
        stream = await asyncio.wait_for(
            self._client.aio.models.generate_content_stream(model=self._model, contents=text, config=config),
            FIRST_AUDIO_TIMEOUT_S,
        )
        usage = None
        iterator = stream.__aiter__()
        first = True
        while True:
            try:
                chunk = await asyncio.wait_for(
                    iterator.__anext__(), FIRST_AUDIO_TIMEOUT_S if first else CHUNK_TIMEOUT_S
                )
            except StopAsyncIteration:
                break
            usage = getattr(chunk, "usage_metadata", None) or usage
            candidates = getattr(chunk, "candidates", None) or []
            content = getattr(candidates[0], "content", None) if candidates else None
            for part in getattr(content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    first = False
                    yield bytes(inline.data)
        if usage is not None:
            try:
                get_ledger().record("quick_reply_speech", self._model, _tts_tokens(usage))
            except Exception as error:  # usage tracking must never break speech
                logger.debug("Usage tracking failed: %s", type(error).__name__)


def _tts_tokens(usage: Any) -> dict[str, int]:
    """Token counts of one TTS call (``generate_content`` names them prompt and candidates)."""
    tokens = {
        "input_text": int(getattr(usage, "prompt_token_count", 0) or 0),
        "output_audio": int(getattr(usage, "candidates_token_count", 0) or 0),
    }
    return {kind: count for kind, count in tokens.items() if count}


def _say(text: str, language: str) -> NDArray[np.int16]:
    """Speak with macOS ``say`` into 24 kHz PCM16 (empty elsewhere or on failure)."""
    if sys.platform != "darwin":
        return np.zeros(0, dtype=np.int16)
    import subprocess

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "reply.wav"
        command = ["say", "-o", str(path), f"--data-format=LEI16@{SAMPLE_RATE}"]
        voice = _SAY_VOICES.get(language)
        if voice:
            command += ["-v", voice]
        try:
            subprocess.run([*command, text], check=True, capture_output=True, timeout=10)
            with wave.open(str(path), "rb") as reader:
                return _pcm16(reader.readframes(reader.getnframes()))
        except (OSError, subprocess.SubprocessError, wave.Error) as error:
            logger.warning("Local voice failed: %s", type(error).__name__)
            return np.zeros(0, dtype=np.int16)
