"""GI-free tests for the app-owned GStreamer event contract."""

import pytest

from reachy_mini_conversation_app.audio.gstreamer_contract import send_flush_events


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
