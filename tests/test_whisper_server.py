"""Tests for the local Whisper server's per-clip limits, single encoding and request priority."""

import asyncio
import contextlib
import importlib.util
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "whisper_server", Path(__file__).resolve().parents[1] / "scripts" / "whisper_server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
whisper_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(whisper_server)


def test_token_budget_grows_with_clip_length_and_stops_at_whisper_limit() -> None:
    """A longer clip may produce more text, never past Whisper's decoder limit."""
    assert whisper_server.token_budget(0) == 16
    assert whisper_server.token_budget(3.0) == 46
    assert whisper_server.token_budget(5.9) == 75
    assert whisper_server.token_budget(60.0) == whisper_server.MAX_NEW_TOKENS
    assert whisper_server.token_budget(float("nan")) == 16


def test_token_budget_leaves_room_for_fast_speech() -> None:
    """About six tokens a second is fast talk; the budget must not cut it off."""
    for seconds in (1.0, 4.0, 10.0, 25.0):
        assert whisper_server.token_budget(seconds) > seconds * 6


def test_languages_default_to_household_and_auto_means_any() -> None:
    """Unset uses the household's languages; auto lets Whisper detect any."""
    assert whisper_server.parse_languages(None) == ("en", "es", "it", "pt")
    assert whisper_server.parse_languages(" ") == ("en", "es", "it", "pt")
    assert whisper_server.parse_languages("ES, en,es") == ("es", "en")
    assert whisper_server.parse_languages("auto") == ()


class _FakeWhisper:
    """A Whisper model that counts encoder passes and records what generate is given."""

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        self.encoded = 0
        self.generate_calls: list[dict[str, Any]] = []

    def get_encoder(self) -> Any:
        def encode(features: Any) -> str:
            self.encoded += 1
            return "encoded"

        return encode

    def generate(self, features: Any, **kwargs: Any) -> Any:
        self.generate_calls.append(kwargs)
        return SimpleNamespace(shape=(1, self.tokens))


def _transcriber(model: _FakeWhisper) -> Any:
    transcriber = object.__new__(whisper_server.Transcriber)
    transcriber._torch = SimpleNamespace(inference_mode=contextlib.nullcontext)
    transcriber.model = model
    transcriber.processor = SimpleNamespace(batch_decode=lambda *_args, **_kwargs: ["hola"])
    transcriber._features = lambda _audio: ("features", 1.0)
    return transcriber


@pytest.mark.parametrize("tokens", [3, 26])
def test_each_clip_is_encoded_once(tokens: int) -> None:
    """The text, and a retry after hitting the budget, reuse the one encoder pass instead of encoding again."""
    model = _FakeWhisper(tokens)

    text, details = _transcriber(model).transcribe(b"", language="es")

    assert text == "hola" and details["capped"] is (tokens >= whisper_server.token_budget(1.0))
    assert model.encoded == 1
    assert len(model.generate_calls) == (2 if details["capped"] else 1)
    assert all(call["encoder_outputs"] == "encoded" for call in model.generate_calls)


@pytest.mark.asyncio
async def test_request_clips_go_before_waiting_background_clips() -> None:
    """While one clip is transcribed, a request clip that arrives later still goes before queued background."""
    lock = whisper_server.TurnLock()
    order: list[str] = []
    release = asyncio.Event()

    async def clip(name: str, urgent: bool, hold: bool = False) -> None:
        async with lock.turn(urgent=urgent):
            order.append(name)
            if hold:
                await release.wait()

    running = asyncio.create_task(clip("running", False, hold=True))
    await asyncio.sleep(0)
    background = [asyncio.create_task(clip(f"background-{i}", False)) for i in range(2)]
    await asyncio.sleep(0)
    request = asyncio.create_task(clip("request", True))
    await asyncio.sleep(0)

    release.set()
    await asyncio.gather(running, request, *background)

    assert order[:2] == ["running", "request"]
    assert sorted(order[2:]) == ["background-0", "background-1"]


@pytest.mark.asyncio
async def test_a_request_that_gives_up_does_not_hold_back_background_clips() -> None:
    """A cancelled request clip leaves the queue, so background clips run again."""
    lock = whisper_server.TurnLock()
    release = asyncio.Event()

    async def hold() -> None:
        async with lock.turn(urgent=False):
            await release.wait()

    async def quick(urgent: bool) -> str:
        async with lock.turn(urgent=urgent):
            return "done"

    running = asyncio.create_task(hold())
    await asyncio.sleep(0)
    request = asyncio.create_task(quick(True))
    await asyncio.sleep(0)
    request.cancel()
    await asyncio.sleep(0)
    background = asyncio.create_task(quick(False))
    release.set()

    assert await asyncio.wait_for(background, 1) == "done"
    await running
