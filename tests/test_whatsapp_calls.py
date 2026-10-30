"""Tests for the app's side of WhatsApp calls through Reachy (whatsapp_calls.py)."""

import json
import asyncio
from typing import Any
from pathlib import Path

import numpy as np
import pytest
from websockets.asyncio.server import serve

from reachy_mini_conversation_app.whatsapp_calls import CallLink, call_pcm, load_callees
from reachy_mini_conversation_app.whatsapp_contacts import find_contact


def test_callees_are_names_only_and_match_what_is_said(tmp_path: Path) -> None:
    """The approved list gives names and spoken names; numbers stay with the service."""
    path = tmp_path / "calls.json"
    path.write_text(
        json.dumps(
            {
                "approved": [
                    {"name": "Test Person", "phone": "+15550100000", "say": ["papa", "dad"]},
                    {"phone": "+15550100001"},
                ]
            }
        )
    )
    callees = load_callees(path, str.lower)
    assert [callee.name for callee in callees] == ["Test Person"]
    assert "15550100000" not in repr(callees)
    assert find_contact("a papa", callees) is callees[0]
    assert find_contact("test person", callees) is callees[0]
    assert find_contact("someone else", callees) is None


def test_a_missing_approved_list_means_nobody(tmp_path: Path) -> None:
    """No file, no calls."""
    assert load_callees(tmp_path / "missing.json", str.lower) == []


def test_mic_frames_become_16_khz_mono_pcm() -> None:
    """A stereo 48 kHz frame is sent as its first channel at 16 kHz."""
    stereo = np.zeros((4_800, 2), dtype=np.int16)
    stereo[:, 0] = 8_000
    data = call_pcm((48_000, stereo))
    samples = np.frombuffer(data, dtype="<i2")
    assert samples.size == 1_600
    assert abs(int(samples[800]) - 8_000) < 10


async def _with_service(handler: Any) -> tuple[Any, str]:
    server = await serve(handler, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/call"


@pytest.mark.asyncio
async def test_a_call_carries_audio_both_ways_once_answered() -> None:
    """Nothing is sent before the answer; then the mic goes out and the other side comes in, until it ends."""
    seen: dict[str, Any] = {"audio": []}

    async def service(socket: Any) -> None:
        seen["auth"] = socket.request.headers.get("Authorization")
        seen["request"] = json.loads(await socket.recv())
        await socket.send(json.dumps({"event": "ringing"}))
        await asyncio.sleep(0.05)
        await socket.send(json.dumps({"event": "connected"}))
        await socket.send(np.full(960, 500, dtype="<i2").tobytes())
        seen["audio"].append(await asyncio.wait_for(socket.recv(), 1))
        await socket.send(json.dumps({"event": "ended", "reason": "ended"}))

    server, url = await _with_service(service)
    try:
        link = await CallLink.open(url, "secret", "Test Person")
        link.send_frame((16_000, np.full(320, 1_000, dtype=np.int16)))  # still ringing: dropped
        while not link.connected:
            await asyncio.sleep(0.01)
        link.send_frame((16_000, np.full(320, 1_000, dtype=np.int16)))
        chunks = [chunk async for chunk in link.received()]
        await link.close()
    finally:
        server.close()
        await server.wait_closed()
    assert seen["auth"] == "Bearer secret"
    assert seen["request"] == {"op": "call", "name": "Test Person"}
    assert len(seen["audio"]) == 1 and len(seen["audio"][0]) == 640
    assert len(chunks) == 1 and chunks[0].size == 960
    assert link.connected and link.reason == "ended"


@pytest.mark.asyncio
async def test_an_unanswered_call_reports_why() -> None:
    """The service's reason comes back when nobody answers."""

    async def service(socket: Any) -> None:
        await socket.recv()
        await socket.send(json.dumps({"event": "ended", "reason": "no_answer"}))

    server, url = await _with_service(service)
    try:
        link = await CallLink.open(url, "secret", "Test Person")
        chunks = [chunk async for chunk in link.received()]
        await link.close()
    finally:
        server.close()
        await server.wait_closed()
    assert chunks == [] and not link.connected and link.reason == "no_answer"


@pytest.mark.asyncio
async def test_an_unreachable_service_raises_os_error() -> None:
    """No service on the port: the caller hears that the call could not be placed."""
    with pytest.raises(OSError):
        await CallLink.open("ws://127.0.0.1:9/call", "secret", "Test Person")
