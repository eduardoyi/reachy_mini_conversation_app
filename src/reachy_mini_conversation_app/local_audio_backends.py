"""Dependency-free local VAD and OpenAI-compatible STT backends."""

import io
import re
import json
import math
import wave
import asyncio
import logging
import ipaddress
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.streaming import resample_audio, audio_to_float32, audio_to_mono_int16
from reachy_mini_conversation_app.conversation_handler import AudioFrame


logger = logging.getLogger(__name__)

_TARGET_SAMPLE_RATE = 16_000
_DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024
_DEFAULT_MAX_TEXT_CHARS = 16_000
_DEFAULT_MAX_AUDIO_SECONDS = 20.0
_LANGUAGE_PATTERN = re.compile(r"[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*")


@dataclass(frozen=True, slots=True)
class RmsEnergyVAD:
    """Classify local PCM frames using a conservative RMS energy threshold."""

    threshold: float = 500.0

    def __post_init__(self) -> None:
        """Reject thresholds that would make local detection unpredictable."""
        if not math.isfinite(self.threshold) or self.threshold < 0:
            raise ValueError("threshold must be finite and non-negative")

    def is_speech(self, frame: AudioFrame) -> bool:
        """Return whether a valid frame has non-zero RMS at or above the threshold."""
        sample_rate, audio = _normalize_local_frame(frame)
        del sample_rate
        if audio.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float64, copy=False)))))
        return rms > 0.0 and rms >= self.threshold


class LocalSTTError(RuntimeError):
    """Report a local STT failure without exposing request or response content."""


class LocalSTTResponseTooLarge(LocalSTTError):
    """Report a local STT response that exceeded the configured memory bound."""


class OpenAICompatibleLocalSTT:
    """Transcribe in-memory PCM through a loopback-only OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "whisper-1",
        language: str | None = None,
        timeout_seconds: float = 15.0,
        target_sample_rate: int = _TARGET_SAMPLE_RATE,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS,
        max_audio_seconds: float = _DEFAULT_MAX_AUDIO_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Validate local endpoint and resource bounds before accepting audio."""
        self.endpoint_url = _build_transcription_url(base_url)
        self.model = model.strip()
        if not self.model:
            raise ValueError("model must not be empty")
        self.language = _normalize_language(language)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if isinstance(target_sample_rate, bool) or not isinstance(target_sample_rate, int) or target_sample_rate <= 0:
            raise ValueError("target_sample_rate must be positive")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if isinstance(max_text_chars, bool) or not isinstance(max_text_chars, int) or max_text_chars <= 0:
            raise ValueError("max_text_chars must be positive")
        if not math.isfinite(max_audio_seconds) or max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be finite and positive")
        self.timeout_seconds = timeout_seconds
        self.target_sample_rate = target_sample_rate
        self.max_response_bytes = max_response_bytes
        self.max_text_chars = max_text_chars
        self.max_audio_seconds = max_audio_seconds
        self._transport = transport

    async def transcribe(self, audio: AudioFrame) -> str:
        """Send a bounded in-memory WAV request and return derived text only."""
        wav_payload = _encode_wav(
            audio,
            target_sample_rate=self.target_sample_rate,
            max_audio_seconds=self.max_audio_seconds,
        )
        request_data = {"model": self.model}
        if self.language is not None:
            request_data["language"] = self.language
        request_files = {"file": ("audio.wav", wav_payload, "audio/wav")}

        client = httpx.AsyncClient(transport=self._transport, timeout=self.timeout_seconds, trust_env=False)
        try:
            async with client:
                try:
                    response_body = await asyncio.wait_for(
                        _request_transcription(
                            client,
                            self.endpoint_url,
                            request_data,
                            request_files,
                            self.max_response_bytes,
                        ),
                        timeout=self.timeout_seconds,
                    )
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError as error:
                    raise TimeoutError("Local STT request timed out") from error
                except LocalSTTError:
                    raise
                except httpx.TimeoutException as error:
                    raise TimeoutError("Local STT request timed out") from error
                except httpx.RequestError as error:
                    raise LocalSTTError("Local STT request failed") from error
                except OSError as error:
                    raise LocalSTTError("Local STT request failed") from error
        except asyncio.CancelledError:
            logger.info("Local STT request cancelled")
            raise

        return _parse_stt_response(response_body, max_text_chars=self.max_text_chars)


def _normalize_local_frame(frame: AudioFrame) -> tuple[int, NDArray[np.int16]]:
    """Validate and defensively normalize a local frame without retaining content."""
    sample_rate, audio = frame
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("Audio sample rate must be a positive integer")
    if not isinstance(audio, np.ndarray):
        raise TypeError("Audio must be a NumPy array")
    if audio.dtype == np.float32 and not np.all(np.isfinite(audio)):
        raise ValueError("Audio contains non-finite samples")
    normalized = audio_to_mono_int16(audio)
    return sample_rate, normalized


def _normalize_language(language: str | None) -> str | None:
    """Validate an optional bounded ISO/BCP-47-style language hint."""
    candidate = (language or "").strip()
    if not candidate:
        return None
    if len(candidate) > 35 or _LANGUAGE_PATTERN.fullmatch(candidate) is None:
        raise ValueError("language must be a valid language tag")
    return candidate


async def _request_transcription(
    client: httpx.AsyncClient,
    endpoint_url: str,
    request_data: dict[str, str],
    request_files: dict[str, tuple[str, bytes, str]],
    max_response_bytes: int,
) -> bytes:
    """Issue one request and close its response body at the request boundary."""
    async with client.stream(
        "POST",
        endpoint_url,
        data=request_data,
        files=request_files,
    ) as response:
        if response.status_code < 200 or response.status_code >= 300:
            raise LocalSTTError(f"Local STT endpoint returned HTTP {response.status_code}")
        return await _read_bounded_response(response, max_response_bytes)


def _encode_wav(frame: AudioFrame, *, target_sample_rate: int, max_audio_seconds: float) -> bytes:
    """Encode a defensive mono PCM16 frame as an in-memory WAV payload."""
    sample_rate, audio = _normalize_local_frame(frame)
    if audio.size / sample_rate > max_audio_seconds:
        raise ValueError("Local STT audio exceeded the configured duration limit")
    if sample_rate != target_sample_rate and audio.size:
        normalized = audio_to_float32(audio)
        resampled = resample_audio(normalized, sample_rate, target_sample_rate)
        audio = np.array(
            np.clip(np.rint(resampled.astype(np.float64) * 32768.0), -32768, 32767),
            dtype=np.int16,
            copy=True,
        )
        sample_rate = target_sample_rate
    elif sample_rate != target_sample_rate:
        sample_rate = target_sample_rate

    pcm_bytes = audio.astype("<i2", copy=True).tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return output.getvalue()


async def _read_bounded_response(response: httpx.Response, max_response_bytes: int) -> bytes:
    """Read a streaming response while enforcing its byte bound."""
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise LocalSTTError("Local STT response had an invalid length") from error
        if declared_length < 0:
            raise LocalSTTError("Local STT response had an invalid length")
        if declared_length > max_response_bytes:
            raise LocalSTTResponseTooLarge("Local STT response exceeded the configured byte limit")

    chunks: list[bytes] = []
    response_size = 0
    async for chunk in response.aiter_bytes():
        response_size += len(chunk)
        if response_size > max_response_bytes:
            raise LocalSTTResponseTooLarge("Local STT response exceeded the configured byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_stt_response(response_body: bytes, *, max_text_chars: int) -> str:
    """Parse JSON text or plain text without logging response content."""
    try:
        decoded_body = response_body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LocalSTTError("Local STT response was not valid UTF-8") from error

    try:
        parsed = json.loads(decoded_body)
    except json.JSONDecodeError:
        text = decoded_body
    else:
        if isinstance(parsed, dict):
            text_value = parsed.get("text")
            if not isinstance(text_value, str):
                raise LocalSTTError("Local STT JSON response did not contain text")
            text = text_value
        elif isinstance(parsed, str):
            text = parsed
        else:
            raise LocalSTTError("Local STT response did not contain text")

    if len(text) > max_text_chars:
        raise LocalSTTResponseTooLarge("Local STT text exceeded the configured character limit")
    return text.strip()


def _build_transcription_url(base_url: str) -> str:
    """Build and validate the loopback-only transcription endpoint URL."""
    candidate = base_url.strip()
    if not candidate:
        raise ValueError("Local STT base URL must not be empty")
    parsed = urlsplit(candidate)
    _validate_loopback_url(parsed)
    base_path = parsed.path.rstrip("/")
    transcription_suffix = "/audio/transcriptions"
    if base_path.endswith(transcription_suffix):
        endpoint_path = base_path
    elif base_path.endswith("/v1"):
        endpoint_path = f"{base_path}{transcription_suffix}"
    else:
        endpoint_path = f"{base_path}/v1{transcription_suffix}"
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))


def _validate_loopback_url(parsed: SplitResult) -> None:
    """Reject non-loopback, credentialed, or ambiguous endpoint URLs."""
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Local STT base URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Local STT base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Local STT base URL must not contain a query or fragment")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("Local STT base URL has an invalid host or port") from error
    if hostname is None:
        raise ValueError("Local STT base URL must contain a host")
    normalized_hostname = hostname.lower()
    if normalized_hostname == "localhost":
        return
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError as error:
        raise ValueError("Local STT base URL must resolve to loopback") from error
    if not address.is_loopback:
        raise ValueError("Local STT base URL must resolve to loopback")


__all__ = [
    "LocalSTTError",
    "LocalSTTResponseTooLarge",
    "OpenAICompatibleLocalSTT",
    "RmsEnergyVAD",
]
