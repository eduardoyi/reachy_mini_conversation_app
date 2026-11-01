"""Big Brain's audio: the host's recorded lines, the sounds and music, and live speech in the host voice.

Recorded lines are the clips ``scripts/big_brain_voice.py`` made from ``big_brain_lines.py`` (``voice/<moment>/
<language>-NN.wav``); a moment never plays the same take twice in a row. Sounds and music are the takes
``scripts/big_brain_sounds.py`` made, as ``sounds/chosen.json`` picks them. Clues, answers and banter are said
live with Eleven v4 Turbo in the same voice and kept by text (``speech/``), so a clue costs once. All audio is
24 kHz mono 16-bit, what Reachy's speaker plays.
"""

import json
import wave
import random
import asyncio
import hashlib
import logging
from typing import Final
from pathlib import Path

import httpx
import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.games.big_brain_lines import LINES, shown


logger = logging.getLogger(__name__)

RATE: Final[int] = 24000
VOICE_ID: Final[str] = "PluKrefK4JWrlyoTgZkd"  # "Richie Big Brain host" (ElevenLabs), designed 2026-11-01
MODEL: Final[str] = "eleven_v4_turbo"
SPEECH_TIMEOUT_S: Final[float] = 20.0
# Five requests at a time on the Creator plan; one is left for anything else.
SPEECH_AT_ONCE: Final[int] = 4

Audio = NDArray[np.int16]


def read_wav(path: Path) -> Audio:
    """Return a 16-bit mono WAV's samples (resampled crudely when it is not 24 kHz)."""
    with wave.open(str(path), "rb") as file:
        rate, width, channels = file.getframerate(), file.getsampwidth(), file.getnchannels()
        data = file.readframes(file.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: {8 * width}-bit audio")
    samples = np.frombuffer(data, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != RATE and len(samples):
        positions = np.linspace(0, len(samples) - 1, int(len(samples) * RATE / rate))
        samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.int16)
    return samples


def write_wav(path: Path, samples: Audio) -> None:
    """Write 24 kHz mono 16-bit samples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(samples.astype(np.int16).tobytes())


def silence(seconds: float) -> Audio:
    """Return ``seconds`` of silence."""
    return np.zeros(int(RATE * seconds), dtype=np.int16)


def join(*pieces: Audio, gap_s: float = 0.15) -> Audio:
    """Return the pieces one after another with a short gap between them."""
    parts: list[Audio] = []
    for piece in pieces:
        if parts:
            parts.append(silence(gap_s))
        parts.append(piece)
    return np.concatenate(parts) if parts else silence(0)


def louder(samples: Audio, gain: float) -> Audio:
    """Return ``samples`` scaled by ``gain`` without wrapping around."""
    scaled: Audio = np.clip(samples.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
    return scaled


class Clips:
    """The host's recorded lines by moment and language."""

    def __init__(self, folder: Path, rng: random.Random | None = None) -> None:
        """Remember where the clips are; each is read when first played."""
        self._folder = folder
        self._rng = rng or random.Random()
        self._cache: dict[Path, Audio] = {}
        self._last: dict[tuple[str, str], int] = {}

    def has(self, moment: str, language: str) -> bool:
        """Whether a recorded take of ``moment`` exists."""
        return bool(self._takes(moment, language))

    def _takes(self, moment: str, language: str) -> list[int]:
        count = len(LINES.get(moment, {}).get(language, ()))
        return [take for take in range(1, count + 1) if self._path(moment, language, take).exists()]

    def _path(self, moment: str, language: str, take: int) -> Path:
        return self._folder / moment / f"{language}-{take:02d}.wav"

    def line(self, moment: str, language: str, take: int | None = None) -> tuple[str, Audio] | None:
        """Return a take of ``moment`` (its text without tags, and its samples), never the last one played."""
        takes = self._takes(moment, language)
        if not takes:
            return None
        if take is None:
            last = self._last.get((moment, language))
            choices = [number for number in takes if number != last] or takes
            take = self._rng.choice(choices)
        self._last[(moment, language)] = take
        path = self._path(moment, language, take)
        if path not in self._cache:
            self._cache[path] = read_wav(path)
        return shown(LINES[moment][language][take - 1]), self._cache[path]


class Sounds:
    """Sound effects and music by name, as ``chosen.json`` picks the takes."""

    def __init__(self, folder: Path) -> None:
        """Read which take each sound uses."""
        self._folder = folder
        try:
            self._chosen: dict[str, str] = json.loads((folder / "chosen.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._chosen = {}
        self._cache: dict[str, Audio] = {}

    def get(self, name: str) -> Audio | None:
        """Return the samples of sound ``name``, or None when it was not made."""
        if name not in self._cache:
            take = self._chosen.get(name)
            path = self._folder / name / take if take else None
            if path is None or not path.exists():
                return None
            self._cache[name] = read_wav(path)
        return self._cache[name]


class Speech:
    """Live speech in the host voice (Eleven v4 Turbo), kept on disk by text."""

    def __init__(self, folder: Path, api_key: str | None, client: httpx.AsyncClient | None = None) -> None:
        """Speak through ElevenLabs with ``api_key``; without one, nothing is said live."""
        self._folder = folder
        self._api_key = api_key
        self._client = client
        self._gate = asyncio.Semaphore(SPEECH_AT_ONCE)
        self._pending: dict[Path, asyncio.Task[Audio | None]] = {}

    def _path(self, text: str, language: str) -> Path:
        digest = hashlib.sha1(f"{VOICE_ID}|{MODEL}|{language}|{text}".encode()).hexdigest()[:20]
        return self._folder / f"{digest}.wav"

    def ready(self, text: str, language: str) -> bool:
        """Whether ``text`` is already kept on disk."""
        return self._path(text, language).exists()

    def prepare(self, text: str, language: str) -> None:
        """Start making ``text`` now, so it is ready when needed."""
        path = self._path(text, language)
        if not path.exists() and path not in self._pending and self._api_key:
            self._pending[path] = asyncio.get_running_loop().create_task(self._make(text, language, path))

    async def say(self, text: str, language: str) -> Audio | None:
        """Return ``text`` in the host voice, or None when it cannot be made."""
        path = self._path(text, language)
        if path.exists():
            return read_wav(path)
        if not self._api_key:
            return None
        task = self._pending.get(path)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._make(text, language, path))
            self._pending[path] = task
        try:
            return await asyncio.shield(task)
        except Exception as error:
            logger.warning("Big Brain speech failed: %s", type(error).__name__)
            return None

    async def _make(self, text: str, language: str, path: Path) -> Audio | None:
        client = self._client or httpx.AsyncClient(timeout=SPEECH_TIMEOUT_S)
        try:
            async with self._gate:
                for attempt in range(4):
                    response = await client.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
                        params={"output_format": f"pcm_{RATE}"},
                        headers={"xi-api-key": str(self._api_key)},
                        json={"text": text, "model_id": MODEL, "language_code": language},
                        timeout=SPEECH_TIMEOUT_S,
                    )
                    if response.status_code != 429:
                        break
                    await asyncio.sleep(2 * (attempt + 1))
                response.raise_for_status()
            samples = np.frombuffer(response.content, dtype=np.int16).copy()
            write_wav(path, samples)
            return samples
        except Exception as error:
            logger.warning("Big Brain speech not made: %s", type(error).__name__)
            return None
        finally:
            self._pending.pop(path, None)
            if self._client is None:
                await client.aclose()
