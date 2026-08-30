"""Tests for the local VAD and loopback-only STT backends."""

import io
import wave
import asyncio
import logging
from email import policy
from typing import Any
from email.parser import BytesParser

import httpx
import numpy as np
import pytest

import reachy_mini_conversation_app.local_audio_backends as local_audio_backends
from reachy_mini_conversation_app.conversation_handler import AudioFrame
from reachy_mini_conversation_app.local_audio_backends import (
    RmsEnergyVAD,
    LocalSTTError,
    LocalSTTResponseTooLarge,
    OpenAICompatibleLocalSTT,
)


def _frame(samples: np.ndarray, sample_rate: int = 16_000) -> AudioFrame:
    """Return a typed test audio frame."""
    return sample_rate, samples


class RecordingTransport(httpx.AsyncBaseTransport):
    """Return a configured response while retaining the request in memory."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        """Initialize the response and request capture."""
        self.response = response or httpx.Response(200, json={"text": "local result"})
        self.request: httpx.Request | None = None
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Capture a request and return the configured response."""
        self.request = request
        return self.response

    async def aclose(self) -> None:
        """Record client cleanup."""
        self.closed = True


class WaitingTransport(httpx.AsyncBaseTransport):
    """Hold a request until cancellation or explicit release."""

    def __init__(self) -> None:
        """Initialize lifecycle events."""
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Wait indefinitely so timeout and cancellation can be tested."""
        del request
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        """Record client cleanup."""
        self.closed = True


async def _file_part(request: httpx.Request) -> tuple[dict[str, str], bytes]:
    """Decode the multipart request's file part for assertions."""
    body = await request.aread()
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + body
    )
    for part in message.iter_attachments():
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        return dict(part.items()), payload
    raise AssertionError("file part missing")


def test_rms_vad_uses_conservative_energy_and_accepts_stereo() -> None:
    """RMS classification downmixes stereo and keeps silence below threshold."""
    detector = RmsEnergyVAD(threshold=500)

    assert not detector.is_speech(_frame(np.zeros(160, dtype=np.int16)))
    assert not detector.is_speech(_frame(np.full(160, 499, dtype=np.int16)))
    assert detector.is_speech(_frame(np.full(160, 500, dtype=np.int16)))
    stereo = np.column_stack((np.full(160, 1_000, dtype=np.int16), np.full(160, -1_000, dtype=np.int16)))
    assert not detector.is_speech(_frame(stereo))


@pytest.mark.parametrize("threshold", [-1.0, float("nan"), float("inf")])
def test_rms_vad_rejects_invalid_threshold(threshold: float) -> None:
    """Invalid thresholds fail closed during construction."""
    with pytest.raises(ValueError, match="threshold"):
        RmsEnergyVAD(threshold=threshold)


@pytest.mark.parametrize(
    "frame, error",
    [
        ((0, np.zeros(1, dtype=np.int16)), ValueError),
        ((16_000, np.zeros(1, dtype=np.float64)), TypeError),
        ((16_000, np.zeros((1, 1, 1), dtype=np.int16)), ValueError),
        ((16_000, np.array([np.nan], dtype=np.float32)), ValueError),
    ],
)
def test_rms_vad_rejects_invalid_audio_frames(frame: AudioFrame, error: type[Exception]) -> None:
    """Invalid rates, dtypes, dimensions, and samples do not reach the classifier."""
    with pytest.raises(error):
        RmsEnergyVAD().is_speech(frame)


@pytest.mark.asyncio
async def test_stt_posts_mono_pcm16_wav_without_authentication() -> None:
    """The request contains only the model and a normalized in-memory WAV."""
    transport = RecordingTransport()
    client = OpenAICompatibleLocalSTT(
        "http://127.0.0.1:8765/v1",
        model="local-whisper",
        transport=transport,
    )
    stereo = np.array([[1_000, 3_000], [-1_000, -3_000]], dtype=np.int16)

    assert await client.transcribe(_frame(stereo, sample_rate=16_000)) == "local result"
    assert transport.request is not None
    assert transport.request.url.path == "/v1/audio/transcriptions"
    assert "authorization" not in transport.request.headers
    headers, wav_payload = await _file_part(transport.request)
    assert headers["Content-Type"] == "audio/wav"
    assert headers["Content-Disposition"].startswith('form-data; name="file"')
    with wave.open(io.BytesIO(wav_payload), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == np.array([2_000, -2_000], dtype="<i2").tobytes()
    assert transport.closed


@pytest.mark.asyncio
async def test_stt_rejects_audio_over_twenty_seconds_before_transport() -> None:
    """The upload remains below the default local multipart RAM-spool boundary."""
    transport = RecordingTransport()
    client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765/v1", transport=transport)

    with pytest.raises(ValueError, match="duration limit"):
        await client.transcribe(_frame(np.zeros(20 * 16_000 + 1, dtype=np.int16)))

    assert transport.request is None


@pytest.mark.asyncio
async def test_stt_includes_optional_language_hint_only_when_configured() -> None:
    """The language hint is a bounded form field and is omitted by default."""
    configured_transport = RecordingTransport()
    configured_client = OpenAICompatibleLocalSTT(
        "http://127.0.0.1:8765",
        language="pt-BR",
        transport=configured_transport,
    )
    await configured_client.transcribe(_frame(np.ones(160, dtype=np.int16)))
    assert configured_transport.request is not None
    configured_body = await configured_transport.request.aread()
    assert b'name="language"' in configured_body
    assert b"\r\n\r\npt-BR\r\n" in configured_body

    omitted_transport = RecordingTransport()
    omitted_client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765", transport=omitted_transport)
    await omitted_client.transcribe(_frame(np.ones(160, dtype=np.int16)))
    assert omitted_transport.request is not None
    omitted_body = await omitted_transport.request.aread()
    assert b'name="language"' not in omitted_body


@pytest.mark.asyncio
async def test_stt_disables_environment_proxies_for_loopback_requests(monkeypatch: Any) -> None:
    """Loopback audio cannot be routed through an environment-configured proxy."""
    monkeypatch.setenv("HTTP_PROXY", "http://public-proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://public-proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://public-proxy.invalid:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    captured: dict[str, object] = {}
    original_client = local_audio_backends.httpx.AsyncClient

    class InspectingClient(original_client):
        """Capture proxy-related constructor settings while preserving behavior."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Capture trust_env before constructing the real client."""
            captured["trust_env"] = kwargs.get("trust_env")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(local_audio_backends.httpx, "AsyncClient", InspectingClient)
    transport = RecordingTransport()
    client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765", transport=transport)

    assert await client.transcribe(_frame(np.ones(160, dtype=np.int16))) == "local result"
    assert captured["trust_env"] is False
    assert transport.request is not None
    assert transport.request.url.host == "127.0.0.1"


@pytest.mark.asyncio
async def test_stt_resamples_to_16khz_and_defensively_copies() -> None:
    """Non-16-kHz input is resampled locally before the request is made."""
    transport = RecordingTransport()
    client = OpenAICompatibleLocalSTT("http://localhost:8765", transport=transport)
    source = np.arange(80, dtype=np.int16)
    source_copy = source.copy()

    await client.transcribe(_frame(source, sample_rate=8_000))
    source[:] = -1
    assert np.array_equal(source_copy, np.arange(80, dtype=np.int16))
    assert transport.request is not None
    _headers, wav_payload = await _file_part(transport.request)
    with wave.open(io.BytesIO(wav_payload), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnframes() == 160


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected",
    [
        (httpx.Response(200, text="plain transcript"), "plain transcript"),
        (httpx.Response(200, json={"text": " json transcript "}), "json transcript"),
        (httpx.Response(200, json="string transcript"), "string transcript"),
    ],
)
async def test_stt_accepts_json_or_plain_text(response: httpx.Response, expected: str) -> None:
    """Compatible endpoints may return JSON text or a plain UTF-8 body."""
    transport = RecordingTransport(response)
    client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765", transport=transport)
    assert await client.transcribe(_frame(np.ones(160, dtype=np.int16))) == expected


@pytest.mark.asyncio
async def test_stt_rejects_status_and_malformed_responses_without_logging_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP and parsing failures expose metadata only, never server content."""
    sentinel = "private-response-sentinel"
    caplog.set_level(logging.INFO)
    status_transport = RecordingTransport(httpx.Response(503, text=sentinel))
    status_client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765", transport=status_transport)
    with pytest.raises(LocalSTTError, match="HTTP 503"):
        await status_client.transcribe(_frame(np.ones(160, dtype=np.int16)))

    malformed_transport = RecordingTransport(httpx.Response(200, text='{"wrong": 1}'))
    malformed_client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765", transport=malformed_transport)
    with pytest.raises(LocalSTTError, match="did not contain text"):
        await malformed_client.transcribe(_frame(np.ones(160, dtype=np.int16)))
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_stt_enforces_response_byte_and_text_limits() -> None:
    """Large declared bodies and large derived text fail before unbounded retention."""
    declared_transport = RecordingTransport(httpx.Response(200, headers={"content-length": "1000"}, content=b"x"))
    declared_client = OpenAICompatibleLocalSTT(
        "http://127.0.0.1:8765",
        max_response_bytes=100,
        transport=declared_transport,
    )
    with pytest.raises(LocalSTTResponseTooLarge, match="byte limit"):
        await declared_client.transcribe(_frame(np.ones(160, dtype=np.int16)))

    text_transport = RecordingTransport(httpx.Response(200, json={"text": "x" * 20}))
    text_client = OpenAICompatibleLocalSTT(
        "http://127.0.0.1:8765",
        max_text_chars=10,
        transport=text_transport,
    )
    with pytest.raises(LocalSTTResponseTooLarge, match="character limit"):
        await text_client.transcribe(_frame(np.ones(160, dtype=np.int16)))


@pytest.mark.asyncio
async def test_stt_timeout_is_bounded_and_closes_client() -> None:
    """A stalled local endpoint is bounded and its client is closed."""
    transport = WaitingTransport()
    client = OpenAICompatibleLocalSTT(
        "http://127.0.0.1:8765",
        timeout_seconds=0.01,
        transport=transport,
    )
    with pytest.raises(TimeoutError, match="timed out"):
        await client.transcribe(_frame(np.ones(160, dtype=np.int16)))
    assert transport.cancelled.is_set()
    assert transport.closed


@pytest.mark.asyncio
async def test_stt_cancellation_propagates_and_closes_request() -> None:
    """Caller cancellation stops the local request rather than leaking it."""
    transport = WaitingTransport()
    client = OpenAICompatibleLocalSTT("http://127.0.0.1:8765", transport=transport)
    task = asyncio.create_task(client.transcribe(_frame(np.ones(160, dtype=np.int16))))
    await transport.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.cancelled.is_set()
    assert transport.closed


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://192.168.1.20:8000/v1",
        "http://10.0.0.2/v1",
        "http://172.16.0.2/v1",
        "http://[::2]/v1",
        "http://localhost.example/v1",
        "http://localhost./v1",
        "http://user@localhost/v1",
        "http://localhost/v1?token=secret",
    ],
)
def test_stt_rejects_non_loopback_or_ambiguous_urls(url: str) -> None:
    """Public, private, DNS, credentialed, and query-bearing endpoints fail closed."""
    with pytest.raises(ValueError, match="loopback|credentials|query"):
        OpenAICompatibleLocalSTT(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8765/v1",
        "http://127.0.0.1:8765/v1",
        "http://127.20.30.40:8765/v1",
        "https://[::1]:8765/v1",
    ],
)
def test_stt_accepts_loopback_urls(url: str) -> None:
    """Only exact localhost and IP loopback addresses are accepted."""
    client = OpenAICompatibleLocalSTT(url)
    assert client.endpoint_url.endswith("/v1/audio/transcriptions")


def test_stt_configuration_bounds_are_validated() -> None:
    """Empty model and invalid resource bounds fail before any request."""
    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleLocalSTT("http://localhost", model=" ")
    with pytest.raises(ValueError, match="timeout_seconds"):
        OpenAICompatibleLocalSTT("http://localhost", timeout_seconds=0)
    with pytest.raises(ValueError, match="max_response_bytes"):
        OpenAICompatibleLocalSTT("http://localhost", max_response_bytes=0)
    with pytest.raises(ValueError, match="max_text_chars"):
        OpenAICompatibleLocalSTT("http://localhost", max_text_chars=0)
    with pytest.raises(ValueError, match="max_audio_seconds"):
        OpenAICompatibleLocalSTT("http://localhost", max_audio_seconds=0)
    with pytest.raises(ValueError, match="target_sample_rate"):
        OpenAICompatibleLocalSTT("http://localhost", target_sample_rate=0)


@pytest.mark.parametrize("language", ["pt BR", "secret?", "a", "123", "x" * 36])
def test_stt_rejects_invalid_language_hint(language: str) -> None:
    """Language form fields accept bounded language tags only."""
    with pytest.raises(ValueError, match="language"):
        OpenAICompatibleLocalSTT("http://localhost", language=language)


def test_public_symbols_do_not_require_runtime_model_dependencies() -> None:
    """The baseline backends construct with only existing application dependencies."""
    assert RmsEnergyVAD().threshold == 500.0
    assert OpenAICompatibleLocalSTT("http://localhost").model == "whisper-1"
