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


@pytest.mark.parametrize(
    ("text", "expected", "echoed"),
    [
        ("Maribel, Richie, Reachy.", "", True),
        ("Maribel, Richie", "", True),
        ("Turn off the lights. Reachy, Richie, Maribel.", "Turn off the lights.", True),
        ("Richie.", "Richie.", False),
        ("Richie, Richie!", "Richie, Richie!", False),
        ("Reachy, pregúntale a Maribel qué hora es", "Reachy, pregúntale a Maribel qué hora es", False),
        ("", "", False),
    ],
)
def test_a_run_of_different_hint_words_is_removed_but_a_single_name_is_kept(
    text: str, expected: str, echoed: bool
) -> None:
    """Qwen sometimes returns its hint on unclear audio; a real call says one name, with words around it."""
    assert whisper_server.strip_hint_echo(text, "Reachy, Richie, Maribel") == (expected, echoed)


class _FakeQwenSession:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text, language="Spanish")


def _qwen(session: _FakeQwenSession, hint: str = "Reachy, Richie, Maribel") -> Any:
    qwen = object.__new__(whisper_server.QwenTranscriber)
    qwen.model_id, qwen.name, qwen.hint, qwen.session = "qwen-1.7b", "openai/whisper-large-v3-turbo", hint, session
    return qwen


def test_qwen_gets_the_names_as_a_hint_and_its_echo_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hint goes in as context; text that is only the hint comes back empty and is logged as an echo."""
    monkeypatch.setattr(whisper_server, "load_audio", lambda _bytes: ("audio", 2.0))
    session = _FakeQwenSession("Maribel, Richie, Reachy.")

    text, details = _qwen(session).transcribe(b"wav", language="es")

    assert text == "" and details["echo"] is True and details["language"] == "spanish"
    assert session.calls == [{"context": "Reachy, Richie, Maribel", "language": "Spanish"}]
    assert "Maribel" not in str(details)  # the log line never carries words


def test_qwen_detects_the_language_when_none_is_given_and_skips_empty_clips(monkeypatch: pytest.MonkeyPatch) -> None:
    """No language means auto; a clip too short for Qwen returns no text without reaching it."""
    session = _FakeQwenSession("Apaga las luces.")
    monkeypatch.setattr(whisper_server, "load_audio", lambda _bytes: ("audio", 1.5))
    assert _qwen(session).transcribe(b"wav")[0] == "Apaga las luces."
    assert session.calls[-1]["language"] is None

    monkeypatch.setattr(whisper_server, "load_audio", lambda _bytes: ("audio", 0.01))
    assert _qwen(session).transcribe(b"wav")[0] == ""
    assert len(session.calls) == 1


def test_qwen_that_cannot_load_falls_back_to_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without mlx-qwen3-asr (or with a broken model) the slot loads its Whisper model and keeps listening."""

    class _Whisper:
        device = "mps"

        def __init__(self, model_id: str, languages: tuple[str, ...]) -> None:
            self.name = self.model_id = model_id

    def broken(*_args: Any) -> None:
        raise ModuleNotFoundError("No module named 'mlx_qwen3_asr'")

    monkeypatch.setattr(whisper_server, "Transcriber", _Whisper)
    monkeypatch.setattr(whisper_server, "QwenTranscriber", broken)

    engine = whisper_server.load_engine("qwen", "openai/whisper-small", "qwen-0.6b", ("en",), "Reachy")

    assert isinstance(engine, _Whisper) and engine.model_id == "openai/whisper-small"


def test_qwen_loads_in_place_of_whisper_and_answers_to_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine setting picks Qwen; the app keeps asking for the Whisper name."""

    class _Qwen:
        def __init__(self, model_id: str, name: str, hint: str) -> None:
            self.model_id, self.name, self.hint = model_id, name, hint

    monkeypatch.setattr(whisper_server, "QwenTranscriber", _Qwen)

    engine = whisper_server.load_engine("qwen", "openai/whisper-small", "qwen-0.6b", ("en",), "Reachy")

    assert (engine.model_id, engine.name, engine.hint) == ("qwen-0.6b", "openai/whisper-small", "Reachy")


def test_a_request_naming_the_whisper_request_model_reaches_the_qwen_standing_in_for_it() -> None:
    """Routing is by the name the app sends, whichever engine answers to it."""
    from fastapi.testclient import TestClient

    class _Engine:
        def __init__(self, name: str, model_id: str) -> None:
            self.name, self.model_id, self.device = name, model_id, "mlx"
            self.clips = 0

        def transcribe(self, audio_bytes: bytes, language: str | None = None) -> tuple[str, dict[str, Any]]:
            self.clips += 1
            return self.model_id, {"took_s": 0.0}

    room = _Engine("openai/whisper-small", "qwen-0.6b")
    request = _Engine("openai/whisper-large-v3-turbo", "qwen-1.7b")
    client = TestClient(whisper_server.build_app(room, request))

    def post(model: str) -> str:
        response = client.post("/v1/audio/transcriptions", data={"model": model}, files={"file": ("a.wav", b"x")})
        return str(response.json()["text"])

    assert post("openai/whisper-large-v3-turbo") == "qwen-1.7b"
    assert post("openai/whisper-small") == "qwen-0.6b"
    assert [model["id"] for model in client.get("/v1/models").json()["data"]] == [room.name, request.name]
