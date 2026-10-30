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
    assert chunks and all(chunk.size == 3_200 for chunk in chunks)  # only the ringback, while it rang
    assert not link.connected and link.reason == "no_answer"


@pytest.mark.asyncio
async def test_an_unreachable_service_raises_os_error() -> None:
    """No service on the port: the caller hears that the call could not be placed."""
    with pytest.raises(OSError):
        await CallLink.open("ws://127.0.0.1:9/call", "secret", "Test Person")


def test_the_gate_turns_down_a_quiet_room_and_opens_for_speech() -> None:
    """Hiss (about 0.01 RMS) goes out at a tenth; speech opens the gate at once and holds it briefly."""
    from reachy_mini_conversation_app.whatsapp_calls import GATE_FLOOR, NoiseGate

    gate = NoiseGate()
    rng = np.random.default_rng(0)
    hiss = (rng.standard_normal(320) * 0.01).astype(np.float32)
    quiet = gate.process(hiss)
    assert np.allclose(quiet, hiss * GATE_FLOOR, atol=1e-6)
    speech = np.full(320, 0.1, dtype=np.float32)
    opened = gate.process(speech)
    assert opened[-1] == pytest.approx(0.1) and opened[0] < 0.02  # ramped open, no click
    assert np.allclose(gate.process(hiss), hiss)  # held open just after speech
    for _ in range(25):  # 0.5 s of quiet closes it again
        gate.process(hiss)
    assert np.allclose(gate.process(hiss), hiss * GATE_FLOOR, atol=1e-6)


def test_the_limiter_turns_shouting_down_and_eases_back() -> None:
    """A shout is capped near LIMIT_PEAK; normal speech afterwards comes back up step by step."""
    from reachy_mini_conversation_app.whatsapp_calls import LIMIT_PEAK, Limiter

    limiter = Limiter()
    shout = np.full(960, 30_000, dtype=np.int16)
    once = limiter.process(shout)
    again = limiter.process(shout)
    assert int(np.abs(again).max()) <= LIMIT_PEAK * 32768 + 1
    assert once[0] == 30_000 and once[-1] < once[0]  # ramped down within the first chunk
    normal = np.full(960, 3_000, dtype=np.int16)
    levels = [int(limiter.process(normal)[-1]) for _ in range(30)]
    assert levels[0] < 3_000 and levels[-1] == 3_000 and levels == sorted(levels)


def test_a_handed_off_call_is_asked_for_in_the_conversation_language() -> None:
    """Gemini's hand-off becomes the request the call command understands."""
    from reachy_mini_conversation_app.quick_commands import parse
    from reachy_mini_conversation_app.whatsapp_calls import call_request

    for language in ("en", "es", "it", "pt", "fr"):
        text = call_request(language, "Eduardo")
        assert text.startswith("Reachy, ")
        command = parse(text.removeprefix("Reachy, "))
        assert command is not None and command.kind == "call" and command.args["to"] == "eduardo"


def test_the_ringback_rings_one_second_in_five() -> None:
    """425 Hz for the first second of each five, silence otherwise, with soft edges."""
    from reachy_mini_conversation_app.whatsapp_calls import ringback

    on, off, next_ring = ringback(2), ringback(10), ringback(25)
    assert on.size == 3_200 and 2_000 < int(np.abs(on).max()) < 5_000
    assert not off.any()
    assert np.array_equal(next_ring, ringback(0))
    assert abs(int(ringback(0)[0])) < 50  # starts from silence, no click


@pytest.mark.asyncio
async def test_it_rings_until_answered_then_plays_the_other_side() -> None:
    """The ringback plays while it rings and stops when the other side answers."""

    async def service(socket: Any) -> None:
        await socket.recv()
        await socket.send(json.dumps({"event": "ringing"}))
        await asyncio.sleep(0.5)
        await socket.send(json.dumps({"event": "connected"}))
        await socket.send(np.full(960, 500, dtype="<i2").tobytes())
        await socket.send(json.dumps({"event": "ended", "reason": "ended"}))

    server, url = await _with_service(service)
    try:
        link = await CallLink.open(url, "secret", "Test Person")
        chunks = [chunk async for chunk in link.received()]
        await link.close()
    finally:
        server.close()
        await server.wait_closed()
    tones = [chunk for chunk in chunks if chunk.size == 3_200]
    assert 2 <= len(tones) <= 6  # about 0.5 s of ringing, plus the lead
    assert chunks[-1].size == 960 and chunks[: len(tones)] == tones


@pytest.mark.asyncio
async def test_an_incoming_call_rings_then_is_answered_without_a_ringback() -> None:
    """The events socket reports the caller; answering opens /call for that call id and plays no ringback."""
    import threading

    from reachy_mini_conversation_app.whatsapp_calls import Incoming, CallManager

    requests: list[Any] = []
    rang = threading.Event()
    seen: list[Incoming] = []

    async def service(socket: Any) -> None:
        if socket.request.path == "/events":
            await socket.send(json.dumps({"event": "incoming", "call_id": "C1", "name": "Test Person"}))
            await socket.wait_closed()
            return
        requests.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({"event": "connected"}))
        await socket.send(np.full(960, 500, dtype="<i2").tobytes())
        await socket.send(json.dumps({"event": "ended", "reason": "ended"}))

    server, url = await _with_service(service)
    calls = CallManager(url, "secret")
    calls.on_incoming = lambda incoming: (seen.append(incoming), rang.set())
    try:
        calls.start()
        assert await asyncio.to_thread(rang.wait, 3)
        assert calls.ringing == Incoming("C1", "Test Person") and seen == [calls.ringing]
        assert calls.busy  # ringing: antenna touches do nothing
        link = await calls.answer()
        assert link is not None and calls.ringing is None and calls.on_a_call
        chunks = [chunk async for chunk in link.received()]
        await link.close()
        calls.finished(link)
    finally:
        server.close()
        await server.wait_closed()
    assert requests == [{"op": "answer", "call_id": "C1"}]
    assert [chunk.size for chunk in chunks] == [960]  # the caller's audio only, no ringback
    assert not calls.on_a_call and not calls.busy


@pytest.mark.asyncio
async def test_a_declined_or_abandoned_incoming_call_stops_ringing() -> None:
    """Declining sends the call id back on the events socket; the caller hanging up clears the ringing too."""
    import threading

    from reachy_mini_conversation_app.whatsapp_calls import CallManager

    declines: list[Any] = []
    first, second, ended = threading.Event(), threading.Event(), threading.Event()

    async def service(socket: Any) -> None:
        await socket.send(json.dumps({"event": "incoming", "call_id": "C1", "name": "Test Person"}))
        declines.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({"event": "incoming", "call_id": "C2", "name": "Test Person"}))
        await asyncio.sleep(0.1)
        await socket.send(json.dumps({"event": "incoming_ended", "call_id": "C2", "reason": "caller_hung_up"}))
        await socket.wait_closed()

    server, url = await _with_service(service)
    calls = CallManager(url, "secret")
    calls.on_incoming = lambda incoming: (first if incoming.call_id == "C1" else second).set()
    calls.on_ringing_ended = lambda incoming: ended.set() if incoming.call_id == "C2" else None
    try:
        calls.start()
        assert await asyncio.to_thread(first.wait, 3)
        assert calls.decline() is not None and calls.ringing is None
        assert await asyncio.to_thread(second.wait, 3)
        assert await asyncio.to_thread(ended.wait, 3)
        assert calls.ringing is None and calls.decline() is None
    finally:
        server.close()
        await server.wait_closed()
    assert declines == [{"op": "decline", "call_id": "C1"}]


@pytest.mark.asyncio
async def test_hanging_up_from_reachy_sends_the_hang_up_and_ends_as_hung_up() -> None:
    """A pat's hang-up reaches the service, ends the other side's audio, and is not a failure."""
    from reachy_mini_conversation_app.whatsapp_calls import CallManager

    got: list[Any] = []

    async def service(socket: Any) -> None:
        if socket.request.path == "/events":
            await socket.wait_closed()
            return
        await socket.recv()
        await socket.send(json.dumps({"event": "connected"}))
        got.append(json.loads(await asyncio.wait_for(socket.recv(), 2)))

    server, url = await _with_service(service)
    calls = CallManager(url, "secret")
    try:
        link = await calls.place("Test Person")
        while not link.connected:
            await asyncio.sleep(0.01)
        assert calls.busy
        await link.hang_up()
        assert [chunk async for chunk in link.received()] == []
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.02)
    finally:
        server.close()
        await server.wait_closed()
    assert got == [{"op": "hangup"}] and link.reason == "hung_up"


@pytest.mark.asyncio
async def test_quiet_counts_from_the_last_audible_chunk_played() -> None:
    """The ringback tone and the other side's voice reset it; their near-silence does not."""
    from reachy_mini_conversation_app.whatsapp_calls import CALL_VOICE_RMS, CallLink

    class Socket:
        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            await asyncio.sleep(3600)

        async def send(self, _data: Any) -> None:
            pass

        async def close(self) -> None:
            pass

    link = CallLink(Socket(), ringback=False)
    link._sound_at -= 10.0
    assert link.quiet_for() >= 10.0
    hush = np.full(960, int(CALL_VOICE_RMS * 32768 * 0.5), dtype=np.int16)
    assert link._played(hush) is hush and link.quiet_for() >= 10.0
    link._played(np.full(960, 2000, dtype=np.int16))
    assert link.quiet_for() < 1.0
    await link.close()
