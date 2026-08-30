"""GI-free tests for the app-owned GStreamer event contract."""

import pytest

from reachy_mini_conversation_app.audio.gstreamer_contract import (
    GSTREAMER_BUFFER_SECONDS,
    GSTREAMER_BUFFER_TIME_NS,
    send_flush_events,
    configure_bounded_audio_buffers,
    configure_nonblocking_audio_source,
)


class FakePropertyTarget:
    """Record native element properties without importing GI."""

    def __init__(self, *, properties: set[str] | None = None) -> None:
        """Initialize an empty property map."""
        self.properties: dict[str, object] = {}
        self.available_properties = properties if properties is not None else {"leaky-type"}

    def find_property(self, name: str) -> object | None:
        """Return a descriptor only for properties supported by the fake."""
        return object() if name in self.available_properties else None

    def set_property(self, name: str, value: object) -> None:
        """Record one configured element property."""
        self.properties[name] = value


class FakeEventSink:
    """Record event order and optionally reject either flush event."""

    def __init__(self, *, accept_start: bool = True, accept_stop: bool = True) -> None:
        """Initialize deterministic event acceptance."""
        self.accept_start = accept_start
        self.accept_stop = accept_stop
        self.events: list[object] = []

    def send_event(self, event: object) -> bool:
        """Record an event and return the configured result for its position."""
        self.events.append(event)
        return self.accept_start if len(self.events) == 1 else self.accept_stop


def test_bounded_audio_buffer_contract_uses_thirty_seconds_without_buffer_count_caps() -> None:
    """Native queues stay bounded by time/bytes while accepting bursty chunks."""
    appsrc = FakePropertyTarget()
    audio_queue = FakePropertyTarget()

    configure_bounded_audio_buffers(appsrc, audio_queue, 16_000)

    assert GSTREAMER_BUFFER_SECONDS == 30
    assert appsrc.properties == {
        "max-buffers": 0,
        "max-bytes": 16_000 * 4 * GSTREAMER_BUFFER_SECONDS,
    }
    assert audio_queue.properties == {
        "max-size-buffers": 0,
        "max-size-bytes": 0,
        "max-size-time": GSTREAMER_BUFFER_TIME_NS,
    }


def test_bounded_audio_buffer_contract_rejects_invalid_sample_rate() -> None:
    """Queue byte sizing fails closed for an invalid sample rate."""
    with pytest.raises(ValueError, match="sample_rate"):
        configure_bounded_audio_buffers(FakePropertyTarget(), FakePropertyTarget(), 0)


def test_nonblocking_audio_source_fails_closed_without_leaky_type() -> None:
    """An appsrc without leaky support is rejected before non-blocking setup."""
    appsrc = FakePropertyTarget(properties=set())

    with pytest.raises(RuntimeError, match="leaky-type"):
        configure_nonblocking_audio_source(appsrc)

    assert appsrc.properties == {}


def test_nonblocking_audio_source_requires_and_enables_leaky_type() -> None:
    """A compatible appsrc is explicitly non-blocking and downstream-leaky."""
    appsrc = FakePropertyTarget()

    configure_nonblocking_audio_source(appsrc)

    assert appsrc.properties == {"block": False, "leaky-type": 2}


def test_flush_event_contract_sends_start_then_stop() -> None:
    """Both native flush events are sent in order when accepted."""
    sink = FakeEventSink()

    send_flush_events(sink, "start", "stop")

    assert sink.events == ["start", "stop"]


def test_flush_event_contract_rejects_flush_start() -> None:
    """A rejected flush-start fails closed without sending flush-stop."""
    sink = FakeEventSink(accept_start=False)

    with pytest.raises(RuntimeError, match="flush-start"):
        send_flush_events(sink, "start", "stop")

    assert sink.events == ["start"]


def test_flush_event_contract_rejects_flush_stop() -> None:
    """A rejected flush-stop fails closed after the start event."""
    sink = FakeEventSink(accept_stop=False)

    with pytest.raises(RuntimeError, match="flush-stop"):
        send_flush_events(sink, "start", "stop")

    assert sink.events == ["start", "stop"]
