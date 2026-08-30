"""GI-free contracts for app-owned GStreamer event handling."""

from typing import Protocol


class _EventSink(Protocol):
    """Native event sink surface needed by the speaker pipeline."""

    def send_event(self, event: object) -> bool:
        """Send one event and report whether the sink accepted it."""


def send_flush_events(event_sink: _EventSink, flush_start: object, flush_stop: object) -> None:
    """Send both flush events or raise when the native sink rejects one."""
    if not event_sink.send_event(flush_start):
        raise RuntimeError("GStreamer flush-start event was rejected")
    if not event_sink.send_event(flush_stop):
        raise RuntimeError("GStreamer flush-stop event was rejected")


__all__ = ["send_flush_events"]
