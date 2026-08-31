"""Tests for the Gemini Live realtime handler."""

import time
import base64
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from collections.abc import Callable

import numpy as np
import pytest
from google.genai import types

from reachy_mini_conversation_app import gemini_live
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.tool_constants import ToolState
from reachy_mini_conversation_app.tools.background_tool_manager import ToolNotification


class FakeSession:
    """Small async fake implementing the public methods used by the handler."""

    def __init__(self) -> None:
        """Initialize empty event and outbound-message queues."""
        self.events: asyncio.Queue[object] = asyncio.Queue()
        self.realtime_inputs: list[dict[str, object]] = []
        self.client_contents: list[dict[str, object]] = []
        self.tool_responses: list[dict[str, object]] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False
        self.close_count = 0

    async def send_realtime_input(self, **kwargs: object) -> None:
        """Record one realtime input call."""
        if self.closed:
            raise RuntimeError("session closed")
        self.realtime_inputs.append(kwargs)
        self.calls.append(("realtime_input", kwargs))

    async def send_client_content(self, **kwargs: object) -> None:
        """Record one ordered client-content call."""
        if self.closed:
            raise RuntimeError("session closed")
        self.client_contents.append(kwargs)
        self.calls.append(("client_content", kwargs))

    async def send_tool_response(self, **kwargs: object) -> None:
        """Record one function-response call."""
        if self.closed:
            raise RuntimeError("session closed")
        self.tool_responses.append(kwargs)
        self.calls.append(("tool_response", kwargs))

    async def receive(self):
        """Yield queued events until the session is closed."""
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        """Close the fake and wake its receiver."""
        self.close_count += 1
        if not self.closed:
            self.closed = True
            await self.events.put(None)


class FakeLive:
    """Fake ``client.aio.live`` resource with an async context manager."""

    def __init__(self, session: FakeSession, *, enter_delay: float = 0.0) -> None:
        """Initialize the fake around one session."""
        self.session = session
        self.enter_delay = enter_delay
        self.enter_started = asyncio.Event()
        self.enter_callback: Callable[[], None] | None = None
        self.exit_count = 0
        self.model: str | None = None
        self.config: object | None = None

    def connect(self, *, model: str, config: object):
        """Record connect arguments and return an async session context."""
        self.model = model
        self.config = config
        live = self

        class SessionContext:
            async def __aenter__(self) -> FakeSession:
                live.enter_started.set()
                if live.enter_callback is not None:
                    live.enter_callback()
                if live.enter_delay:
                    await asyncio.sleep(live.enter_delay)
                return live.session

            async def __aexit__(self, *_args: object) -> None:
                live.exit_count += 1
                await live.session.close()

        return SessionContext()


class FakeAio:
    """Fake async client surface used by ``GeminiLiveHandler``."""

    def __init__(self, session: FakeSession, *, enter_delay: float = 0.0) -> None:
        """Initialize the async resource."""
        self.live = FakeLive(session, enter_delay=enter_delay)
        self.aclose_count = 0

    async def aclose(self) -> None:
        """Record client shutdown."""
        self.aclose_count += 1


class FakeClient:
    """Fake GenAI client with the public async Live resource."""

    def __init__(self, session: FakeSession, *, enter_delay: float = 0.0) -> None:
        """Initialize the fake client."""
        self.aio = FakeAio(session, enter_delay=enter_delay)


class FakeClock:
    """Controllable monotonic clock for provider latency tests."""

    def __init__(self) -> None:
        """Start the fake clock at zero seconds."""
        self.value = 0.0

    def __call__(self) -> float:
        """Return the current fake monotonic timestamp."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Advance the clock by a non-negative duration."""
        self.value += seconds


@pytest.fixture
def gemini_config(monkeypatch):
    """Install deterministic Gemini settings without reading local credentials."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key", raising=False)
    monkeypatch.setattr(config, "GEMINI_LIVE_MODEL", "fake-live-model", raising=False)
    monkeypatch.setattr(config, "GEMINI_VOICE", "Puck", raising=False)
    monkeypatch.setattr(config, "REALTIME_TRANSCRIPTION_LANGUAGE", "en-US", raising=False)
    monkeypatch.setattr(gemini_live, "get_session_greeting_prompt", lambda: "", raising=False)


@pytest.fixture
def deps() -> ToolDependencies:
    """Return fake robot dependencies for provider-boundary tests."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


def build_handler(deps: ToolDependencies, client: FakeClient | None = None) -> gemini_live.GeminiLiveHandler:
    """Build a handler with a fake client and deterministic voice."""
    return gemini_live.GeminiLiveHandler(deps, startup_voice="Kore", client=client)


def activate_session(handler: gemini_live.GeminiLiveHandler, session: FakeSession, generation: int = 1) -> None:
    """Attach a fake session as the active generation."""
    handler.connection = session
    handler._active_generation = generation


def queued_messages(handler: gemini_live.GeminiLiveHandler) -> list[object]:
    """Drain emitted items for concise assertions."""
    messages: list[object] = []
    while not handler.output_queue.empty():
        messages.append(handler.output_queue.get_nowait())
    return messages


def audio_event(sample: int = 1) -> types.LiveServerMessage:
    """Build one minimal Gemini model-audio event."""
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(
                            data=np.array([sample], dtype=np.int16).tobytes(), mime_type="audio/pcm"
                        )
                    )
                ]
            )
        )
    )


def test_build_session_config_uses_audio_transcription_voice_and_tools(gemini_config, deps) -> None:
    """The setup uses Gemini's audio modality and maps shared tool schemas."""
    handler = build_handler(deps)

    session_config = handler._build_session_config([])

    assert session_config.response_modalities == [types.Modality.AUDIO]
    assert session_config.speech_config is not None
    assert session_config.speech_config.voice_config is not None
    assert session_config.speech_config.voice_config.prebuilt_voice_config is not None
    assert session_config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert session_config.input_audio_transcription is not None
    assert session_config.input_audio_transcription.language_codes == ["en-US"]
    assert session_config.output_audio_transcription is not None
    assert session_config.system_instruction is not None
    assert session_config.tools == []


def test_build_session_config_declares_nonempty_tools(gemini_config, deps) -> None:
    """A non-empty shared tool registry becomes one Gemini declaration group."""
    handler = build_handler(deps)
    tool_spec = {
        "type": "function",
        "name": "move_head",
        "description": "Move the head.",
        "parameters": {"type": "object", "properties": {}},
    }

    session_config = handler._build_session_config([tool_spec])

    assert session_config.tools is not None
    assert len(session_config.tools) == 1
    assert session_config.tools[0].function_declarations is not None
    assert session_config.tools[0].function_declarations[0].name == "move_head"


def test_build_session_config_uses_initial_history_for_local_context(gemini_config, deps) -> None:
    """Derived ambient text uses Gemini's initial client-content history mode."""
    handler = build_handler(deps)
    handler.set_initial_context("We should stay indoors tomorrow. Maybe the aquarium.")

    session_config = handler._build_session_config([])

    assert session_config.history_config is not None
    assert session_config.history_config.initial_history_in_client_content is True
    assert session_config.system_instruction is not None
    instruction = session_config.system_instruction.parts[0].text
    assert instruction is not None
    assert "untrusted conversational background" in instruction
    assert "We should stay indoors tomorrow. Maybe the aquarium." not in instruction
    assert "JSON-encoded text" not in instruction


def test_build_session_config_omits_empty_local_context_wrapper(gemini_config, deps) -> None:
    """An empty local context leaves the ordinary provider prompt unchanged."""
    handler = build_handler(deps)
    handler.set_initial_context("   ")

    session_config = handler._build_session_config([])

    assert session_config.history_config is None
    assert session_config.system_instruction is not None
    instruction = session_config.system_instruction.parts[0].text
    assert instruction is not None
    assert "JSON-encoded text" not in instruction
    assert "untrusted conversational background" not in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_key", [None, ""])
async def test_missing_api_key_fails_before_connect(gemini_config, deps, monkeypatch, missing_key) -> None:
    """A production handler refuses to construct a client without a key."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", missing_key, raising=False)
    handler = gemini_live.GeminiLiveHandler(deps)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await handler.start_up()


@pytest.mark.asyncio
async def test_startup_uses_client_injection_and_closes_idempotently(gemini_config, deps) -> None:
    """Startup connects through the public async context and shutdown is repeatable."""
    session = FakeSession()
    session.events.put_nowait(types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")))
    client = FakeClient(session)
    handler = build_handler(deps, client)

    await handler.start_up()
    assert client.aio.live.model == "fake-live-model"
    assert client.aio.live.config is not None
    assert client.aio.live.config.history_config is None
    assert session.client_contents == []
    assert handler.connection is None
    await handler.shutdown()
    await handler.shutdown()

    assert client.aio.aclose_count == 1
    assert session.close_count >= 1


@pytest.mark.asyncio
async def test_startup_seeds_initial_context_before_connected_without_model_turn(gemini_config, deps, caplog) -> None:
    """Initial context is one ordered user content message before readiness is announced."""
    sentinel = "We should stay indoors tomorrow. Maybe the aquarium."
    session = FakeSession()
    session.events.put_nowait(types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")))
    client = FakeClient(session)
    handler = build_handler(deps, client)
    handler.set_initial_context(sentinel)
    connection_states: list[bool] = []
    handler.set_connection_observer(connection_states.append)
    caplog.set_level("DEBUG", logger=gemini_live.logger.name)

    await handler.start_up()

    assert [kind for kind, _ in session.calls] == ["client_content"]
    assert len(session.client_contents) == 1
    content = session.client_contents[0]["turns"]
    assert isinstance(content, types.Content)
    assert content.role == "user"
    assert content.parts is not None
    assert [part.text for part in content.parts] == [sentinel]
    assert session.client_contents[0]["turn_complete"] is True
    assert connection_states == [True]
    assert handler.output_queue.empty()
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_initial_context_seed_is_bounded(gemini_config, deps) -> None:
    """The provider boundary enforces the configured context character bound."""
    session = FakeSession()
    session.events.put_nowait(types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")))
    client = FakeClient(session)
    handler = build_handler(deps, client)
    oversized_context = "x" * (config.LOCAL_CONTEXT_MAX_CHARS + 1)
    handler.set_initial_context(oversized_context)

    await handler.start_up()

    content = session.client_contents[0]["turns"]
    assert isinstance(content, types.Content)
    assert content.parts is not None
    assert content.parts[0].text == oversized_context[: config.LOCAL_CONTEXT_MAX_CHARS]


@pytest.mark.asyncio
async def test_initial_context_seed_failure_cleans_up_before_connected(gemini_config, deps, monkeypatch) -> None:
    """A seed transport failure closes setup without reporting a connected session."""
    session = FakeSession()
    client = FakeClient(session)
    handler = build_handler(deps, client)
    handler.set_initial_context("private context")
    connection_states: list[bool] = []
    handler.set_connection_observer(connection_states.append)

    async def fail_seed(**_kwargs: object) -> None:
        raise RuntimeError("seed transport unavailable")

    monkeypatch.setattr(session, "send_client_content", fail_seed)

    with pytest.raises(RuntimeError, match="seed transport unavailable"):
        await handler.start_up()

    assert handler.connection is None
    assert not handler._is_connected()
    assert connection_states == []
    assert session.closed
    assert client.aio.live.exit_count == 1
    await handler.shutdown()


@pytest.mark.asyncio
async def test_initial_context_seed_cancellation_cleans_up_before_connected(gemini_config, deps, monkeypatch) -> None:
    """Cancellation while seeding closes setup without leaking a connected state."""
    session = FakeSession()
    client = FakeClient(session)
    handler = build_handler(deps, client)
    handler.set_initial_context("private context")
    connection_states: list[bool] = []
    handler.set_connection_observer(connection_states.append)
    seed_started = asyncio.Event()

    async def block_seed(**_kwargs: object) -> None:
        seed_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(session, "send_client_content", block_seed)
    startup = asyncio.create_task(handler.start_up())

    await asyncio.wait_for(seed_started.wait(), timeout=1.0)
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup

    assert handler.connection is None
    assert not handler._is_connected()
    assert connection_states == []
    assert session.closed
    assert client.aio.live.exit_count == 1
    await handler.shutdown()


@pytest.mark.asyncio
async def test_shutdown_racing_initial_context_seed_never_reports_connected(gemini_config, deps, monkeypatch) -> None:
    """Shutdown winning a blocked seed cannot be followed by a stale connected event."""
    session = FakeSession()
    client = FakeClient(session)
    handler = build_handler(deps, client)
    handler.set_initial_context("private context")
    connection_states: list[bool] = []
    handler.set_connection_observer(connection_states.append)
    seed_started = asyncio.Event()
    release_seed = asyncio.Event()

    async def block_seed(**_kwargs: object) -> None:
        seed_started.set()
        await release_seed.wait()

    monkeypatch.setattr(session, "send_client_content", block_seed)
    startup = asyncio.create_task(handler.start_up())

    await asyncio.wait_for(seed_started.wait(), timeout=1.0)
    await handler.shutdown()
    release_seed.set()
    with pytest.raises(asyncio.CancelledError):
        await startup

    assert handler.connection is None
    assert not handler._is_connected()
    assert connection_states == []
    assert session.closed
    assert client.aio.live.exit_count == 1
    await handler.shutdown()


@pytest.mark.asyncio
async def test_setup_timeout_cleans_context_without_connected_state(gemini_config, deps, monkeypatch) -> None:
    """Only the websocket setup handshake is bounded and always cleaned up."""
    session = FakeSession()
    client = FakeClient(session, enter_delay=0.05)
    handler = build_handler(deps, client)
    monkeypatch.setattr(gemini_live, "_SESSION_SETUP_TIMEOUT_S", 0.001)

    with pytest.raises(asyncio.TimeoutError):
        await handler.start_up()

    assert handler.connection is None
    assert not handler._is_connected()
    assert session.closed
    assert client.aio.live.exit_count == 1
    await handler.shutdown()
    assert client.aio.aclose_count == 1


@pytest.mark.asyncio
async def test_setup_cancellation_cleans_context_without_connected_state(gemini_config, deps) -> None:
    """Cancellation during setup propagates after closing the pending context."""
    session = FakeSession()
    client = FakeClient(session, enter_delay=10.0)
    handler = build_handler(deps, client)
    task = asyncio.create_task(handler.start_up())

    await asyncio.wait_for(client.aio.live.enter_started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handler.connection is None
    assert not handler._is_connected()
    assert session.closed
    assert client.aio.live.exit_count == 1
    await handler.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_blocked_receive(gemini_config, deps) -> None:
    """Cancellation and shutdown close a receive loop without leaking it."""
    session = FakeSession()
    client = FakeClient(session)
    handler = build_handler(deps, client)
    task = asyncio.create_task(handler.start_up())

    for _ in range(100):
        if handler.connection is session:
            break
        await asyncio.sleep(0)
    assert handler.connection is session

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await handler.shutdown()

    assert session.closed
    assert client.aio.aclose_count == 1


@pytest.mark.asyncio
async def test_receive_normalizes_mono_stereo_and_ignores_empty(gemini_config, deps) -> None:
    """Input audio is mono little-endian PCM and empty frames are skipped."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler.receive((16_000, np.array([[1, 10], [2, 20], [3, 30]], dtype=np.int16)))
    await handler.receive((16_000, np.array([], dtype=np.int16)))
    await handler.receive((16_000, np.array([[0.25, 0.5]], dtype=np.float32)))

    assert len(session.realtime_inputs) == 2
    first_blob = session.realtime_inputs[0]["audio"]
    second_blob = session.realtime_inputs[1]["audio"]
    assert isinstance(first_blob, types.Blob)
    assert first_blob.mime_type == "audio/pcm;rate=16000"
    assert first_blob.data == np.array([1, 2, 3], dtype=np.int16).tobytes()
    assert isinstance(second_blob, types.Blob)
    assert second_blob.data == np.array([8191, 16383], dtype=np.int16).tobytes()


@pytest.mark.asyncio
async def test_successful_media_sends_notify_traffic_observer(gemini_config, deps) -> None:
    """Traffic hooks observe successful audio sends at the provider boundary."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    observed: list[tuple[str, int]] = []
    handler.set_traffic_observer(lambda kind, byte_count: observed.append((kind, byte_count)))

    await handler.receive((16_000, np.array([1, 2], dtype=np.int16)))

    assert observed == [("audio", 4)]


@pytest.mark.asyncio
async def test_failed_audio_send_does_not_notify_traffic_observer(gemini_config, deps) -> None:
    """A Gemini transport failure is not counted as cloud audio traffic."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    observed: list[tuple[str, int]] = []
    handler.set_traffic_observer(lambda kind, byte_count: observed.append((kind, byte_count)))
    session.send_realtime_input = AsyncMock(side_effect=RuntimeError("transport unavailable"))

    await handler.receive((16_000, np.array([1, 2], dtype=np.int16)))

    assert observed == []


@pytest.mark.asyncio
async def test_startup_greeting_can_be_suppressed_for_hybrid_sessions(gemini_config, deps, monkeypatch) -> None:
    """The provider-neutral greeting control suppresses only this session's greeting."""
    monkeypatch.setattr(gemini_live, "get_session_greeting_prompt", lambda: "Say hello")
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    handler.set_startup_greeting_enabled(False)

    await handler._send_startup_greeting(session, 1)

    assert session.realtime_inputs == []


@pytest.mark.asyncio
async def test_multi_part_audio_output_is_24khz(gemini_config, deps) -> None:
    """Every inline audio part is emitted at Gemini's 24 kHz output rate."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    event = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(data=np.array([1, 2], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                    ),
                    types.Part(inline_data=types.Blob(data=b"image-bytes", mime_type="image/jpeg")),
                    types.Part(text="transcript"),
                    types.Part(
                        inline_data=types.Blob(data=np.array([3], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                    ),
                ],
            ),
        ),
    )

    await handler._handle_server_message(event, session, 1)

    outputs = queued_messages(handler)
    audio_outputs = [item for item in outputs if isinstance(item, tuple)]
    assert [(rate, samples.tolist()) for rate, samples in audio_outputs] == [(24_000, [1, 2]), (24_000, [3])]


@pytest.mark.asyncio
async def test_transcripts_emit_interim_and_final_updates(gemini_config, deps) -> None:
    """Input/output transcription events reach both the queue and observer."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    observed: list[tuple[str, str, bool]] = []
    handler.set_transcript_observer(lambda role, text, final: observed.append((role, text, final)))
    event = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            interim_input_transcription=types.Transcription(text="hel", finished=False),
            input_transcription=types.Transcription(text="hello", finished=True),
            output_transcription=types.Transcription(text="Hi!", finished=True),
        ),
    )

    await handler._handle_server_message(event, session, 1)
    outputs = queued_messages(handler)

    assert observed == [("user", "hel", False), ("user", "hello", True), ("assistant", "Hi!", True)]
    assert [item.args[0]["role"] for item in outputs if isinstance(item, gemini_live.AdditionalOutputs)] == [
        "user_partial",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_server_interruption_flushes_playback(gemini_config, deps) -> None:
    """Gemini interruption events clear queued playback and speaking state."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    handler.output_queue.put_nowait((24_000, np.array([1], dtype=np.int16)))
    clear_queue = MagicMock()
    handler._clear_queue = clear_queue
    event = types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))

    await handler._handle_server_message(event, session, 1)

    clear_queue.assert_called_once_with()
    deps.movement_manager.set_speaking.assert_called_with(False)


@pytest.mark.asyncio
async def test_latency_snapshot_uses_monotonic_milliseconds(gemini_config, deps, monkeypatch, caplog) -> None:
    """The benchmark seam records bounded numeric timings without payloads."""
    clock = FakeClock()
    real_perf_counter = time.perf_counter
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    assert time.perf_counter is real_perf_counter
    caplog.set_level("INFO", logger=gemini_live.logger.name)
    session = FakeSession()
    session.events.put_nowait(types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")))
    client = FakeClient(session)
    client.aio.live.enter_callback = lambda: clock.advance(0.25)
    handler = build_handler(deps, client)

    await handler.start_up()
    assert handler.latency_snapshot.latest_connection_ready_ms == pytest.approx(250.0)

    session = FakeSession()
    activate_session(handler, session)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="hello", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.4)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([1], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )
    assert handler.latency_snapshot.latest_first_audio_ms == pytest.approx(400.0)

    start_tool = AsyncMock(return_value=SimpleNamespace(tool_id="tool-id"))
    monkeypatch.setattr(type(handler.tool_manager), "start_tool", start_tool)
    await handler._handle_tool_call(
        types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id="call-1", name="move_head", args={"value": 1})]
        ),
        session,
        1,
    )
    clock.advance(0.125)
    await handler._handle_tool_result(
        ToolNotification(
            id="call-1",
            tool_name="move_head",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"ok": True},
        ),
        session,
        1,
    )
    assert handler.latency_snapshot.latest_tool_response_ms == pytest.approx(125.0)

    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    clock.advance(0.2)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="next", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.3)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([2], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )
    assert handler.latency_snapshot.latest_first_audio_ms == pytest.approx(500.0)

    handler._clear_queue = lambda: clock.advance(0.03)
    await handler._handle_server_message(
        types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True)),
        session,
        1,
    )
    assert handler.latency_snapshot.latest_interruption_flush_ms == pytest.approx(30.0)
    assert all("Gemini latency" in record.message for record in caplog.records if "Gemini latency" in record.message)


@pytest.mark.asyncio
async def test_first_audio_before_voice_activity_end_is_reported_without_negative_latency(
    gemini_config, deps, monkeypatch, caplog
) -> None:
    """Audio that precedes the independent end marker is not presented as a latency sample."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    caplog.set_level("INFO", logger=gemini_live.logger.name)
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([1], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )
    clock.advance(0.2)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == ()
    assert snapshot.first_audio_unavailable_count == 1
    assert snapshot.first_audio_reordered_count == 1
    assert "first_audio_unavailable reason=audio_before_speech_end_marker reordered=True" in caplog.text


@pytest.mark.asyncio
async def test_first_audio_before_final_transcript_is_reported_once(gemini_config, deps, monkeypatch) -> None:
    """A late final input transcript cannot create a misleading negative sample."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    audio_event = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(data=np.array([2], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                    )
                ]
            )
        )
    )
    await handler._handle_server_message(audio_event, session, 1)
    clock.advance(0.2)
    transcript_event = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="hello", finished=True),
        )
    )
    await handler._handle_server_message(transcript_event, session, 1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == ()
    assert snapshot.first_audio_unavailable_count == 1
    assert snapshot.first_audio_reordered_count == 1


@pytest.mark.asyncio
async def test_first_audio_measurement_handles_repeated_turns_without_double_count(
    gemini_config, deps, monkeypatch
) -> None:
    """Valid and reordered turns remain independent and produce one diagnostic each."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([3], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([4], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    clock.advance(0.2)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([5], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == pytest.approx((100.0, 200.0))
    assert snapshot.first_audio_unavailable_count == 1
    assert snapshot.first_audio_reordered_count == 1


@pytest.mark.asyncio
async def test_late_final_transcript_after_valid_sample_does_not_rearm(gemini_config, deps, monkeypatch) -> None:
    """A final transcript arriving after audio cannot start a second measurement."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(audio_event(), session, 1)
    clock.advance(0.2)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="late transcript", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(audio_event(2), session, 1)

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == pytest.approx((100.0,))
    assert snapshot.first_audio_unavailable_count == 0
    assert snapshot.first_audio_ambiguous_count == 1


@pytest.mark.asyncio
async def test_same_text_late_final_is_ambiguous_without_rearming(gemini_config, deps, monkeypatch) -> None:
    """A repeated final transcript stays ambiguous while duplicate output remains suppressed."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="same", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(audio_event(), session, 1)
    first_turn_outputs = queued_messages(handler)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(audio_event(2), session, 1)
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="same", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(audio_event(3), session, 1)

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == pytest.approx((100.0, 100.0))
    assert snapshot.first_audio_unavailable_count == 0
    assert snapshot.first_audio_ambiguous_count == 1

    first_turn_user_messages = [
        item.args[0]
        for item in first_turn_outputs
        if isinstance(item, gemini_live.AdditionalOutputs) and item.args[0]["role"] == "user"
    ]
    second_turn_user_messages = [
        item.args[0]
        for item in queued_messages(handler)
        if isinstance(item, gemini_live.AdditionalOutputs) and item.args[0]["role"] == "user"
    ]
    assert first_turn_user_messages == [{"role": "user", "content": "same"}]
    assert second_turn_user_messages == []


@pytest.mark.asyncio
async def test_differing_final_transcript_starts_next_turn_after_completed_audio(
    gemini_config, deps, monkeypatch
) -> None:
    """A new final transcript can start the next turn without VAD events."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="first turn", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(audio_event(), session, 1)

    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="first turn", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="second turn", finished=True),
            )
        ),
        session,
        1,
    )
    clock.advance(0.2)
    await handler._handle_server_message(audio_event(2), session, 1)

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == pytest.approx((100.0, 200.0))
    assert snapshot.first_audio_unavailable_count == 0


@pytest.mark.asyncio
async def test_interim_transcript_without_activity_start_tracks_first_audio(gemini_config, deps, monkeypatch) -> None:
    """A partial user transcript opens a turn when no voice-start event arrived."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                interim_input_transcription=types.Transcription(text="hel", finished=False),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )
    clock.advance(0.2)
    await handler._handle_server_message(audio_event(), session, 1)

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == pytest.approx((200.0,))
    assert snapshot.first_audio_unavailable_count == 0


@pytest.mark.asyncio
async def test_partial_transcript_and_audio_event_records_reordered_diagnostic(
    gemini_config, deps, monkeypatch
) -> None:
    """A combined partial-and-audio event still tracks the reordered boundary."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                interim_input_transcription=types.Transcription(text="hel", finished=False),
                model_turn=types.Content(
                    parts=[types.Part(inline_data=types.Blob(data=b"\x01\x00", mime_type="audio/pcm"))]
                ),
            )
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
        ),
        session,
        1,
    )

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == ()
    assert snapshot.first_audio_unavailable_count == 1
    assert snapshot.first_audio_reordered_count == 1


@pytest.mark.asyncio
async def test_startup_greeting_audio_does_not_open_first_audio_measurement(gemini_config, deps, monkeypatch) -> None:
    """Provider startup audio is not attributed to a user turn."""
    monkeypatch.setattr(gemini_live, "get_session_greeting_prompt", lambda: "Say hello")
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    await handler._send_startup_greeting(session, 1)
    await handler._handle_server_message(audio_event(), session, 1)

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == ()
    assert snapshot.first_audio_unavailable_count == 0


@pytest.mark.asyncio
async def test_first_audio_markers_clear_on_interruption_and_shutdown(gemini_config, deps, monkeypatch) -> None:
    """Interrupted and closed sessions discard an incomplete measurement exactly once."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    await handler._handle_server_message(
        types.LiveServerMessage(
            voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
        ),
        session,
        1,
    )
    clock.advance(0.1)
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=np.array([6], dtype=np.int16).tobytes(), mime_type="audio/pcm")
                        )
                    ]
                )
            )
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True)), session, 1
    )
    await handler.shutdown()

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_ms == ()
    assert snapshot.first_audio_unavailable_count == 1
    assert snapshot.first_audio_reordered_count == 0


@pytest.mark.asyncio
async def test_first_audio_unavailable_diagnostic_is_bounded(gemini_config, deps, monkeypatch) -> None:
    """Repeated reordered events cannot grow latency diagnostics without bound."""
    clock = FakeClock()
    monkeypatch.setattr(gemini_live, "time", SimpleNamespace(perf_counter=clock))
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    for _ in range(gemini_live._LATENCY_SAMPLE_LIMIT * 2):
        await handler._handle_server_message(
            types.LiveServerMessage(
                voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_START)
            ),
            session,
            1,
        )
        await handler._handle_server_message(
            types.LiveServerMessage(
                server_content=types.LiveServerContent(
                    model_turn=types.Content(
                        parts=[
                            types.Part(
                                inline_data=types.Blob(
                                    data=np.array([7], dtype=np.int16).tobytes(), mime_type="audio/pcm"
                                )
                            )
                        ]
                    )
                )
            ),
            session,
            1,
        )
        await handler._handle_server_message(
            types.LiveServerMessage(
                voice_activity=types.VoiceActivity(voice_activity_type=types.VoiceActivityType.ACTIVITY_END)
            ),
            session,
            1,
        )

    snapshot = handler.latency_snapshot
    assert snapshot.first_audio_unavailable_count == gemini_live._LATENCY_SAMPLE_LIMIT
    assert snapshot.first_audio_reordered_count == gemini_live._LATENCY_SAMPLE_LIMIT
    assert len(snapshot.first_audio_ms) <= gemini_live._LATENCY_SAMPLE_LIMIT


@pytest.mark.asyncio
async def test_tool_call_uses_shared_manager_and_cancellation(gemini_config, deps, monkeypatch) -> None:
    """Gemini calls use ToolCallRoutine and server cancellation reaches the manager."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    started = SimpleNamespace(tool_id="camera-call-1")
    start_tool = AsyncMock(return_value=started)
    cancel_tool = AsyncMock(return_value=True)
    monkeypatch.setattr(type(handler.tool_manager), "start_tool", start_tool)
    monkeypatch.setattr(type(handler.tool_manager), "cancel_tool", cancel_tool)
    call = types.FunctionCall(id="call-1", name="camera", args={"question": "what is here?"})

    await handler._handle_server_message(
        types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[call])),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(tool_call_cancellation=types.LiveServerToolCallCancellation(ids=["call-1"])),
        session,
        1,
    )

    start_tool.assert_awaited_once()
    routine = start_tool.await_args.kwargs["tool_call_routine"]
    assert routine.tool_name == "camera"
    assert routine.args_json_str == '{"question": "what is here?"}'
    cancel_tool.assert_awaited_once_with("camera-call-1")


@pytest.mark.asyncio
async def test_parallel_tool_calls_share_one_ordered_function_response(gemini_config, deps, monkeypatch) -> None:
    """Parallel Gemini calls are answered together in the server-provided order."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    start_tool = AsyncMock(
        side_effect=[SimpleNamespace(tool_id="tool-a"), SimpleNamespace(tool_id="tool-b")],
    )
    monkeypatch.setattr(type(handler.tool_manager), "start_tool", start_tool)

    await handler._handle_server_message(
        types.LiveServerMessage(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="call-a", name="move_head", args={"value": 1}),
                    types.FunctionCall(id="call-b", name="move_head", args={"value": 2}),
                ]
            )
        ),
        session,
        1,
    )
    await handler._handle_tool_result(
        ToolNotification(
            id="call-b",
            tool_name="move_head",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"value": 2},
        ),
        session,
        1,
    )
    assert session.tool_responses == []
    await handler._handle_tool_result(
        ToolNotification(
            id="call-a",
            tool_name="move_head",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"value": 1},
        ),
        session,
        1,
    )

    responses = session.tool_responses[0]["function_responses"]
    assert isinstance(responses, list)
    assert [response.id for response in responses] == ["call-a", "call-b"]
    assert start_tool.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_parallel_call_does_not_strand_completed_result(gemini_config, deps, monkeypatch) -> None:
    """A completed sibling is flushed when its remaining batch call is cancelled."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    start_tool = AsyncMock(
        side_effect=[SimpleNamespace(tool_id="tool-a"), SimpleNamespace(tool_id="tool-b")],
    )
    cancel_tool = AsyncMock(return_value=True)
    monkeypatch.setattr(type(handler.tool_manager), "start_tool", start_tool)
    monkeypatch.setattr(type(handler.tool_manager), "cancel_tool", cancel_tool)

    await handler._handle_tool_call(
        types.LiveServerToolCall(
            function_calls=[
                types.FunctionCall(id="call-a", name="move_head", args={"value": 1}),
                types.FunctionCall(id="call-b", name="move_head", args={"value": 2}),
            ]
        ),
        session,
        1,
    )
    await handler._handle_tool_result(
        ToolNotification(
            id="call-a",
            tool_name="move_head",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"value": 1},
        ),
        session,
        1,
    )
    await handler._handle_tool_cancellation(
        types.LiveServerToolCallCancellation(ids=["call-b"]),
        1,
    )

    responses = session.tool_responses[0]["function_responses"]
    assert isinstance(responses, list)
    assert [response.id for response in responses] == ["call-a"]
    cancel_tool.assert_awaited_once_with("tool-b")


@pytest.mark.asyncio
async def test_tool_result_sends_function_response_and_camera(gemini_config, deps) -> None:
    """Successful tools return sanitized JSON and camera bytes as an on-demand frame."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    jpeg = b"fake-jpeg"
    result = {"b64_im": base64.b64encode(jpeg).decode("ascii"), "image_width": 10}
    notification = ToolNotification(
        id="call-1",
        tool_name="camera",
        is_idle_tool_call=False,
        status=ToolState.COMPLETED,
        result=result,
    )

    await handler._handle_tool_result(notification, session, 1)

    assert len(session.tool_responses) == 1
    response = session.tool_responses[0]["function_responses"]
    assert isinstance(response, list)
    function_response = response[0]
    assert isinstance(function_response, types.FunctionResponse)
    assert function_response.id == "call-1"
    assert function_response.response == {"output": {"image_attached": True, "image_width": 10}}
    assert session.realtime_inputs == [{"video": types.Blob(data=jpeg, mime_type="image/jpeg")}]
    assert [name for name, _ in session.calls] == ["realtime_input", "tool_response"]


@pytest.mark.asyncio
async def test_failed_camera_send_does_not_notify_video_traffic(gemini_config, deps, monkeypatch) -> None:
    """A camera transport failure is not counted as a successful cloud frame."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    observed: list[tuple[str, int]] = []
    handler.set_traffic_observer(lambda kind, byte_count: observed.append((kind, byte_count)))
    monkeypatch.setattr(session, "send_realtime_input", AsyncMock(side_effect=RuntimeError("transport unavailable")))
    notification = ToolNotification(
        id="call-1",
        tool_name="camera",
        is_idle_tool_call=False,
        status=ToolState.COMPLETED,
        result={"b64_im": base64.b64encode(b"fake-jpeg").decode("ascii")},
    )

    await handler._handle_tool_result(notification, session, 1)

    assert observed == []


@pytest.mark.asyncio
async def test_invalid_camera_base64_stays_json_only(gemini_config, deps) -> None:
    """Invalid camera payloads never reach the media channel or logs as raw data."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    notification = ToolNotification(
        id="call-1",
        tool_name="camera",
        is_idle_tool_call=False,
        status=ToolState.COMPLETED,
        result={"b64_im": "not-base64"},
    )

    await handler._handle_tool_result(notification, session, 1)

    assert session.realtime_inputs == []
    response = session.tool_responses[0]["function_responses"][0]
    assert isinstance(response, types.FunctionResponse)
    assert response.response == {"output": {"image_attached": False}}


@pytest.mark.asyncio
async def test_tool_errors_are_returned_gracefully(gemini_config, deps) -> None:
    """Tool failures become model-visible error responses instead of exceptions."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    notification = ToolNotification(
        id="call-1",
        tool_name="move_head",
        is_idle_tool_call=False,
        status=ToolState.FAILED,
        error="tool failed",
    )

    await handler._handle_tool_result(notification, session, 1)

    response = session.tool_responses[0]["function_responses"][0]
    assert isinstance(response, types.FunctionResponse)
    assert response.response == {"output": {"error": "tool failed"}}


@pytest.mark.asyncio
async def test_stale_session_tool_result_is_ignored(gemini_config, deps) -> None:
    """A result from an old session cannot be sent into a replacement session."""
    old_session = FakeSession()
    new_session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, new_session, generation=2)
    handler._in_flight_tool_calls.add("new-call")
    handler._tool_ids_by_call_id["new-call"] = "new-tool-id"
    notification = ToolNotification(
        id="old-call",
        tool_name="move_head",
        is_idle_tool_call=False,
        status=ToolState.COMPLETED,
        result={"ok": True},
    )

    await handler._handle_tool_result(notification, old_session, 1)

    assert old_session.tool_responses == []
    assert new_session.tool_responses == []
    assert handler._in_flight_tool_calls == {"new-call"}
    assert handler._tool_ids_by_call_id == {"new-call": "new-tool-id"}


@pytest.mark.asyncio
async def test_audio_transport_error_is_dropped_but_cancellation_propagates(gemini_config, deps, monkeypatch) -> None:
    """A transient send failure does not stop recording, while cancellation remains cooperative."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    send = AsyncMock(side_effect=RuntimeError("transport unavailable"))
    monkeypatch.setattr(session, "send_realtime_input", send)

    await handler.receive((16_000, np.array([1], dtype=np.int16)))
    send.assert_awaited_once()

    send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await handler.receive((16_000, np.array([1], dtype=np.int16)))


@pytest.mark.asyncio
async def test_change_voice_closes_active_session_for_reconnect(gemini_config, deps) -> None:
    """Voice changes update the override and close the immutable-config session."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)

    result = await handler.change_voice("Fenrir")

    assert result == "Voice changed to Fenrir. Reconnecting Gemini Live session."
    assert handler.get_current_voice() == "Fenrir"
    assert session.closed
    assert handler.connection is None


@pytest.mark.asyncio
async def test_apply_personality_closes_active_session_for_reconnect(gemini_config, deps, monkeypatch) -> None:
    """Personality changes refresh tools and close the immutable-config session."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", previous_profile, raising=False)
    monkeypatch.setattr(gemini_live, "get_session_instructions", lambda _instance_path: "new instructions")
    initialize_tools = MagicMock()
    monkeypatch.setattr(gemini_live.core_tools, "initialize_tools", initialize_tools)
    monkeypatch.setattr(
        gemini_live,
        "set_custom_profile",
        lambda profile: setattr(config, "REACHY_MINI_CUSTOM_PROFILE", profile),
    )

    result = await handler.apply_personality("astronomer")

    assert result == "Applied personality. Reconnecting Gemini Live session."
    assert config.REACHY_MINI_CUSTOM_PROFILE == "astronomer"
    initialize_tools.assert_called_once_with(force=True)
    assert session.closed
    assert handler.connection is None


@pytest.mark.asyncio
async def test_apply_personality_rolls_back_when_resolution_fails(gemini_config, deps, monkeypatch) -> None:
    """A failed profile validation restores the prior profile and active session."""
    previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", previous_profile, raising=False)
    handler = build_handler(deps)
    monkeypatch.setattr(
        gemini_live,
        "set_custom_profile",
        lambda profile: setattr(config, "REACHY_MINI_CUSTOM_PROFILE", profile),
    )
    monkeypatch.setattr(
        gemini_live,
        "get_session_instructions",
        lambda _instance_path: (_ for _ in ()).throw(RuntimeError("sensitive failure")),
    )

    result = await handler.apply_personality("broken")

    assert result == "Failed to apply personality: RuntimeError"
    assert config.REACHY_MINI_CUSTOM_PROFILE == previous_profile


@pytest.mark.asyncio
async def test_sensitive_payloads_are_not_logged(gemini_config, deps, monkeypatch, caplog) -> None:
    """Credentials and conversational payloads stay out of provider logs."""
    sentinel = "do-not-log-gemini-payload"
    monkeypatch.setattr(config, "GEMINI_API_KEY", sentinel, raising=False)
    caplog.set_level("DEBUG", logger=gemini_live.logger.name)
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    send = AsyncMock(side_effect=RuntimeError(sentinel))
    monkeypatch.setattr(session, "send_realtime_input", send)
    start_tool = AsyncMock(return_value=SimpleNamespace(tool_id="tool-id"))
    monkeypatch.setattr(type(handler.tool_manager), "start_tool", start_tool)

    await handler.receive((16_000, np.array([1], dtype=np.int16)))
    await handler._handle_server_message(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text=sentinel, finished=True),
            ),
        ),
        session,
        1,
    )
    await handler._handle_tool_result(
        ToolNotification(
            id="call-1",
            tool_name="camera",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"b64_im": base64.b64encode(sentinel.encode()).decode("ascii")},
        ),
        session,
        1,
    )
    await handler._handle_server_message(
        types.LiveServerMessage(
            tool_call=types.LiveServerToolCall(
                function_calls=[types.FunctionCall(id="call-2", name="move_head", args={"value": sentinel})]
            )
        ),
        session,
        1,
    )
    await handler._handle_tool_result(
        ToolNotification(
            id="call-2",
            tool_name="move_head",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"value": sentinel},
        ),
        session,
        1,
    )

    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_server_error_is_user_visible_without_raw_event(gemini_config, deps, caplog) -> None:
    """Server errors expose compact metadata without logging response text."""
    session = FakeSession()
    handler = build_handler(deps)
    activate_session(handler, session)
    event = SimpleNamespace(
        setup_complete=None,
        voice_activity=None,
        server_content=None,
        tool_call=None,
        tool_call_cancellation=None,
        go_away=None,
        error=SimpleNamespace(code="bad_request", message="sensitive response text"),
    )

    await handler._handle_server_message(event, session, 1)

    outputs = queued_messages(handler)
    assert len(outputs) == 1
    assert isinstance(outputs[0], gemini_live.AdditionalOutputs)
    assert outputs[0].args[0] == {"role": "assistant", "content": "[error] sensitive response text"}
    assert "sensitive response text" not in caplog.text
