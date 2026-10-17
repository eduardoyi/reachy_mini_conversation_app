"""Gemini Live household-memory lifecycle tests."""

import asyncio
from unittest.mock import MagicMock
from collections.abc import Callable

import numpy as np
import pytest
from google.genai import types
from test_gemini_live import FakeClient, FakeSession

from reachy_mini_conversation_app import gemini_live
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class FakeCapture:
    """Capture worker with observable lifecycle and fragment boundaries."""

    def __init__(self) -> None:
        """Initialize an empty capture worker."""
        self.started = False
        self.closed = False
        self.invalidated = False
        self.fragments: list[tuple[str, str, int, int]] = []
        self.speakers: list[str | None] = []
        self.mutation_generation: str | None = None

    async def start(self) -> None:
        """Mark capture as started."""
        self.started = True

    def bind_mutation_generation(self, generation: str) -> None:
        """Record the current provider activation generation."""
        self.mutation_generation = generation

    def add_fragment(self, role: str, text: str, start_ms: int, end_ms: int, speaker_id: str | None = None) -> None:
        """Record one finalized fragment."""
        if self.closed or self.invalidated:
            raise AssertionError("fragment arrived after capture invalidation")
        self.fragments.append((role, text, start_ms, end_ms))
        self.speakers.append(speaker_id)

    def invalidate(self) -> None:
        """Fence future fragments."""
        self.invalidated = True

    def rebase_after_review(self, _previous_revision: int, _current_revision: int) -> None:
        """Accept a review rebase in the fake worker."""

    async def close(self) -> None:
        """Close the worker."""
        self.closed = True


@pytest.fixture
def gemini_memory_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install deterministic Gemini and memory settings."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key", raising=False)
    monkeypatch.setattr(config, "GEMINI_LIVE_MODEL", "fake-live-model", raising=False)
    monkeypatch.setattr(config, "GEMINI_VOICE", "Puck", raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_REVIEW_ENABLED", False, raising=False)
    monkeypatch.setattr(gemini_live, "get_session_greeting_prompt", lambda: "", raising=False)
    monkeypatch.setattr(gemini_live, "get_tool_specs", lambda: [], raising=False)


def _deps() -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


def _handler(
    session: FakeSession,
    *,
    instance_path: str,
    capture_factory: Callable[[str | None], FakeCapture] | None = None,
) -> gemini_live.GeminiLiveHandler:
    """Build a Gemini handler around one deterministic fake session."""
    return gemini_live.GeminiLiveHandler(
        _deps(),
        instance_path=instance_path,
        startup_voice="Puck",
        client=FakeClient(session),
        memory_capture_factory=capture_factory,
    )


@pytest.mark.asyncio
async def test_disabled_memory_does_not_construct_capture_worker(
    tmp_path, gemini_memory_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabled memory creates no optional capture or OpenAI work."""
    calls: list[str] = []

    def factory(_instance_path: str | None) -> FakeCapture:
        calls.append("capture")
        raise AssertionError("disabled memory must not construct capture")

    session = FakeSession()
    session.events.put_nowait(types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")))
    handler = _handler(session, instance_path=str(tmp_path), capture_factory=factory)

    await handler.start_up()
    await handler.shutdown()

    assert calls == []


def _content(**fields: object) -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(**fields))


def _piece(text: str) -> types.Transcription:
    # Gemini 3.8 Live sends consecutive pieces and leaves finished unset (recorded trial, 2026-10-17).
    return types.Transcription(text=text)


async def _run_events(tmp_path, events: list[types.LiveServerMessage], *, speaker: str | None = None) -> FakeCapture:
    capture = FakeCapture()
    session = FakeSession()
    for event in events:
        session.events.put_nowait(event)
    session.events.put_nowait(types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")))
    deps = _deps()
    deps.current_person_id = (lambda: speaker) if speaker is not None else None
    handler = gemini_live.GeminiLiveHandler(
        deps,
        instance_path=str(tmp_path),
        startup_voice="Puck",
        client=FakeClient(session),
        memory_capture_factory=lambda _path: capture,
    )
    await handler.start_up()
    await handler.shutdown()
    return capture


@pytest.mark.asyncio
async def test_spoken_turns_reach_capture_whole_once_and_tagged_with_the_speaker(
    tmp_path, gemini_memory_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User pieces join into one utterance before the answer; the complete answer follows; both once."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True, raising=False)

    capture = await _run_events(
        tmp_path,
        [
            _content(input_transcription=_piece("mi hermano Pablo empieza un trabajo")),
            _content(input_transcription=_piece(" nuevo en Lisboa"), output_transcription=_piece("Eso es una ")),
            _content(output_transcription=_piece("excelente noticia.")),
            _content(turn_complete=True),
            _content(input_transcription=_piece("Gracias")),
            _content(turn_complete=True),
        ],
        speaker="eduardo",
    )

    assert [(role, text) for role, text, _start, _end in capture.fragments] == [
        ("user", "mi hermano Pablo empieza un trabajo nuevo en Lisboa"),
        ("assistant", "Eso es una excelente noticia."),
        ("user", "Gracias"),
    ]
    assert capture.speakers == ["eduardo", None, "eduardo"]
    assert capture.started and capture.invalidated and capture.closed


@pytest.mark.asyncio
async def test_interrupted_answers_and_unfinished_turns_stay_out_of_memory(
    tmp_path, gemini_memory_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cut-off answer is dropped; a turn the session ends before completing is never flushed."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True, raising=False)

    capture = await _run_events(
        tmp_path,
        [
            _content(input_transcription=_piece("tell me a long story")),
            _content(output_transcription=_piece("Once upon a ")),
            _content(interrupted=True),
            _content(input_transcription=_piece("actually never mind")),
            _content(turn_complete=True),
            _content(input_transcription=_piece("one more thing")),
        ],
    )

    assert [(role, text) for role, text, _start, _end in capture.fragments] == [
        ("user", "tell me a long story"),
        ("user", "actually never mind"),
    ]
    assert capture.speakers == [None, None]


@pytest.mark.asyncio
async def test_spoken_turns_skip_capture_when_memory_capture_is_off(
    tmp_path, gemini_memory_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With capture disabled no worker exists and transcripts go nowhere."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True, raising=False)

    capture = await _run_events(
        tmp_path,
        [_content(input_transcription=_piece("hello")), _content(turn_complete=True)],
    )

    assert capture.fragments == [] and not capture.started


@pytest.mark.asyncio
async def test_accepted_say_text_reaches_capture_once(tmp_path, gemini_memory_config, monkeypatch) -> None:
    """One accepted addressed text submission is captured exactly once."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True, raising=False)
    capture = FakeCapture()
    session = FakeSession()
    handler = _handler(session, instance_path=str(tmp_path), capture_factory=lambda _path: capture)
    startup = asyncio.create_task(handler.start_up())

    for _ in range(100):
        if handler.connection is session and capture.started:
            break
        await asyncio.sleep(0)
    assert handler.connection is session
    assert capture.started

    await handler.say("remember this addressed request")
    await handler.shutdown()
    await startup

    assert len(capture.fragments) == 1
    assert capture.fragments[0][0:2] == ("user", "remember this addressed request")


@pytest.mark.asyncio
async def test_stop_background_work_fences_memory_and_media_admission(
    tmp_path, gemini_memory_config, monkeypatch
) -> None:
    """Leaving cloud mode synchronously fences memory, output and provider sends."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", True, raising=False)
    capture = FakeCapture()
    session = FakeSession()
    handler = _handler(session, instance_path=str(tmp_path), capture_factory=lambda _path: capture)
    startup = asyncio.create_task(handler.start_up())

    for _ in range(100):
        if handler.connection is session and capture.started:
            break
        await asyncio.sleep(0)
    assert handler.connection is session

    handler.output_queue.put_nowait((24_000, np.array([1], dtype=np.int16)))
    handler.stop_background_work()
    await handler.receive((16_000, np.array([1], dtype=np.int16)))

    assert handler._memory_session.fenced
    assert handler._memory_invalidated
    assert handler.output_queue.empty()
    assert session.realtime_inputs == []
    with pytest.raises(RuntimeError, match="no active session"):
        await handler.say("late request")

    await handler.shutdown()
    await startup
    assert capture.invalidated
    assert capture.closed


@pytest.mark.asyncio
async def test_memory_revision_blocks_tool_dispatch_before_watcher_tick(
    tmp_path, gemini_memory_config, monkeypatch
) -> None:
    """A same-ID external edit blocks a tool before its manager task starts."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True, raising=False)
    store = HouseholdMemoryStore(tmp_path)
    entry = store.remember("old context", subject_id="person:alex")
    session = FakeSession()
    handler = _handler(session, instance_path=str(tmp_path))
    startup = asyncio.create_task(handler.start_up())

    for _ in range(100):
        if handler.connection is session:
            break
        await asyncio.sleep(0)
    assert handler.connection is session

    store.update(entry.id, text="changed context")
    start_tool = MagicMock()
    monkeypatch.setattr(type(handler.tool_manager), "start_tool", start_tool)
    await handler._handle_tool_call(
        types.LiveServerToolCall(function_calls=[types.FunctionCall(id="call-1", name="move_head", args={})]),
        session,
        handler._active_generation or 1,
    )

    assert start_tool.call_count == 0
    await handler.shutdown()
    await startup


@pytest.mark.asyncio
async def test_extended_thinking_hybrid_fences_interrupted_session_before_ambient_resume(
    tmp_path, gemini_memory_config, monkeypatch
) -> None:
    """Hybrid mode follows Gemini busy state and denies stale media after stop."""
    monkeypatch.setattr(config, "GEMINI_LIVE_MODEL", "gemini-3.8-live-extended-thinking", raising=False)
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "medium", raising=False)
    session = FakeSession()
    handler = _handler(session, instance_path=str(tmp_path))
    runtime = HybridRuntime(lambda: handler)

    assert await runtime.activate("keyboard")
    for _ in range(100):
        if runtime.status.connected:
            break
        await asyncio.sleep(0)
    assert runtime.status.connected
    generation = handler._active_generation
    assert generation is not None

    await runtime.handle_frame((16_000, np.ones(160, dtype=np.int16)))
    sent_before_stop = len(session.realtime_inputs)
    assert sent_before_stop == 1

    session.events.put_nowait(
        types.LiveServerMessage(
            server_content=types.LiveServerContent(
                interaction_status=types.InteractionStatus.IN_PROGRESS,
            )
        )
    )
    for _ in range(100):
        if runtime.interaction_busy:
            break
        await asyncio.sleep(0)
    assert runtime.interaction_busy

    handler.output_queue.put_nowait((24_000, np.array([1], dtype=np.int16)))
    session.events.put_nowait(types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True)))
    for _ in range(100):
        if handler.output_queue.empty():
            break
        await asyncio.sleep(0)
    assert handler.output_queue.empty()
    handler.deps.movement_manager.set_speaking.assert_called_with(False)

    assert await runtime.deactivate_cloud("interrupted")
    assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
    assert runtime.interaction_busy is False

    await runtime.handle_frame((16_000, np.ones(160, dtype=np.int16)))
    await handler.receive((16_000, np.ones(160, dtype=np.int16)))
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
        generation,
    )

    assert len(session.realtime_inputs) == sent_before_stop
    assert handler.output_queue.empty()
    await runtime.close()
