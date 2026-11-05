"""The local conversation record: what it writes, where, for how long, and that it never stops a conversation."""

import json
import stat
import wave
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pytest
from google.genai import types
from test_gemini_live import FakeClient, FakeSession
from test_hybrid_runtime import FakeHandler, frame, wait_until

from reachy_mini_conversation_app import gemini_live
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.hybrid_runtime import HybridRuntime
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.conversation_record import ConversationRecorder


class Clock:
    """Seconds that only move when a test moves them."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current time."""
        return self.now


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _only(directory: Path, pattern: str) -> Path:
    found = sorted(directory.glob(f"*/{pattern}"))
    assert len(found) == 1, found
    return found[0]


def test_a_conversation_records_how_it_started_each_turn_and_how_it_ended(tmp_path: Path) -> None:
    """Pieces join into turns; a reply cut off says after how long; an unfinished reply is kept at the end."""
    clock = Clock()
    recorder = ConversationRecorder(tmp_path, keep_days=14, clock=clock, now=lambda: datetime(2026, 11, 5, 18, 10, 58))
    record = recorder.start(wake="acoustic_wake", request_heard_locally="pon música", room_context="(nada)")
    assert record is not None

    clock.now = 1.0
    record.heard("pon ")
    record.heard("música")
    clock.now = 2.0
    record.tool("spotify_control")
    record.said("Claro, ")
    clock.now = 2.4
    record.said("pongo música")
    clock.now = 2.6
    record.cut_off()
    clock.now = 4.0
    record.heard("no, para")
    record.said("Vale.")
    record.turn_complete()
    clock.now = 6.0
    record.said("Algo más")
    record.close("follow_up_window")
    record.heard("too late")
    record.close("again")

    path = tmp_path / "2026-11-05" / "181058.jsonl"
    assert _events(path) == [
        {
            "t": 0.0,
            "event": "start",
            "at": "2026-11-05T18:10:58",
            "wake": "acoustic_wake",
            "request_heard_locally": "pon música",
            "room_context": "(nada)",
        },
        {"t": 1.0, "event": "heard", "text": "pon música"},
        {"t": 2.0, "event": "tool", "name": "spotify_control"},
        {"t": 2.0, "event": "said", "text": "Claro, pongo música", "cut_off": True},
        {"t": 2.6, "event": "cut_off", "into_reply_s": 0.6},
        {"t": 4.0, "event": "heard", "text": "no, para"},
        {"t": 4.0, "event": "said", "text": "Vale.", "cut_off": False},
        {"t": 6.0, "event": "said", "text": "Algo más", "cut_off": False, "unfinished": True},
        {"t": 6.0, "event": "end", "reason": "follow_up_window", "audio_s": 0.0},
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert record.closed


def test_the_audio_sent_is_saved_at_its_first_rate(tmp_path: Path) -> None:
    """Frames at another rate are skipped; the end line gives the audio's length."""
    recorder = ConversationRecorder(tmp_path, keep_days=14)
    record = recorder.start(wake="keyboard")
    assert record is not None
    record.audio(16_000, np.arange(8_000, dtype=np.int16))
    record.audio(24_000, np.ones(100, dtype=np.int16))
    record.audio(16_000, np.full(8_000, 7, dtype=np.int16))
    record.close("test_end")

    with wave.open(str(_only(tmp_path, "*.wav")), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16_000, 1, 2)
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16)
    np.testing.assert_array_equal(samples, np.concatenate([np.arange(8_000), np.full(8_000, 7)]))
    assert _events(_only(tmp_path, "*.jsonl"))[-1] == {"t": 0.0, "event": "end", "reason": "test_end", "audio_s": 1.0}
    assert stat.S_IMODE(_only(tmp_path, "*.wav").stat().st_mode) == 0o600


def test_two_conversations_in_the_same_second_get_their_own_files(tmp_path: Path) -> None:
    """A second record started the same second does not overwrite the first."""
    recorder = ConversationRecorder(tmp_path, keep_days=14, now=lambda: datetime(2026, 11, 5, 9, 0, 0))
    first, second = recorder.start(wake="a"), recorder.start(wake="b")
    assert first is not None and second is not None
    first.close("x")
    second.close("y")
    assert sorted(p.name for p in (tmp_path / "2026-11-05").iterdir()) == ["090000-2.jsonl", "090000.jsonl"]


def test_days_past_the_keep_period_are_deleted(tmp_path: Path) -> None:
    """Starting a record deletes day folders older than the keep period and leaves other folders alone."""
    for name in ("2026-10-20", "2026-10-22", "2026-11-04", "notes"):
        (tmp_path / name).mkdir()
    recorder = ConversationRecorder(tmp_path, keep_days=14, now=lambda: datetime(2026, 11, 5, 9, 0, 0))
    record = recorder.start(wake="keyboard")
    assert record is not None
    record.close("x")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-22", "2026-11-04", "2026-11-05", "notes"]


def test_an_unwritable_folder_records_nothing_and_never_raises(tmp_path: Path) -> None:
    """When the folder cannot be made, start returns None so the conversation goes on unrecorded."""
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert ConversationRecorder(blocker / "conversations", keep_days=14).start(wake="keyboard") is None


@pytest.mark.asyncio
async def test_a_cloud_conversation_is_recorded_from_start_to_end(tmp_path: Path) -> None:
    """The runtime opens the record with the request, adds the audio Gemini accepted and closes it at the end."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler, conversation_recorder=ConversationRecorder(tmp_path, keep_days=14))

    assert await runtime.activate("keyboard", addressed_text="what do you think?")
    await handler.started.wait()
    assert handler._conversation_record is not None
    handler.set_connected(True)
    await handler.say_called.wait()
    await runtime.handle_frame(frame(9))
    await wait_until(lambda: len(handler.receive_frames) == 1)
    await runtime.deactivate_cloud("test_end")

    events = _events(_only(tmp_path, "*.jsonl"))
    start = events[0]
    assert start["event"] == "start"
    assert start["wake"] == "keyboard"
    assert start["request_heard_locally"] == "what do you think?"
    assert start["request_sent_as"] == "text"
    assert start["request_clip_s"] is None
    assert events[-1]["event"] == "end"
    assert events[-1]["reason"] == "test_end"
    with wave.open(str(_only(tmp_path, "*.wav")), "rb") as audio:
        assert np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).tolist() == [9]
    assert handler._conversation_record is None
    await runtime.close()


@pytest.mark.asyncio
async def test_nothing_is_recorded_without_a_recorder_or_a_conversation(tmp_path: Path) -> None:
    """Records are opt-in, and ambient listening alone never opens one."""
    handler = FakeHandler()
    runtime = HybridRuntime(lambda: handler)
    assert await runtime.activate("keyboard")
    await handler.started.wait()
    assert handler._conversation_record is None
    await runtime.close()

    recorded = HybridRuntime(lambda: FakeHandler(), conversation_recorder=ConversationRecorder(tmp_path, keep_days=14))
    await recorded.handle_frame(frame(1))
    await recorded.close()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_gemini_adds_what_it_heard_said_and_cut_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini's transcription pieces, interruptions and finished turns reach the record."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key", raising=False)
    monkeypatch.setattr(config, "GEMINI_LIVE_MODEL", "fake-live-model", raising=False)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False, raising=False)
    monkeypatch.setattr(gemini_live, "get_session_greeting_prompt", lambda: "", raising=False)
    monkeypatch.setattr(gemini_live, "get_tool_specs", lambda exclusion_list=None: [], raising=False)

    def content(**fields: object) -> types.LiveServerMessage:
        return types.LiveServerMessage(server_content=types.LiveServerContent(**fields))

    session = FakeSession()
    for event in (
        content(input_transcription=types.Transcription(text="cuéntame ")),
        content(input_transcription=types.Transcription(text="un cuento")),
        content(output_transcription=types.Transcription(text="Había una ")),
        content(interrupted=True),
        content(input_transcription=types.Transcription(text="en español")),
        content(output_transcription=types.Transcription(text="Claro.")),
        content(turn_complete=True),
        types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="0s")),
    ):
        session.events.put_nowait(event)
    handler = gemini_live.GeminiLiveHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        instance_path=str(tmp_path),
        startup_voice="Puck",
        client=FakeClient(session),
    )
    record = ConversationRecorder(tmp_path / "records", keep_days=14).start(wake="acoustic_wake")
    assert record is not None
    handler.set_conversation_record(record)

    await handler.start_up()
    await handler.shutdown()
    record.close("test_end")

    turns = [(e["event"], e.get("text"), e.get("cut_off")) for e in _events(_only(tmp_path / "records", "*.jsonl"))]
    assert turns == [
        ("start", None, None),
        ("heard", "cuéntame un cuento", None),
        ("said", "Había una", True),
        ("cut_off", None, None),
        ("heard", "en español", None),
        ("said", "Claro.", False),
        ("end", None, None),
    ]
