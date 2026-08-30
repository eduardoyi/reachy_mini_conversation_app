"""GI-free contracts for app-owned GStreamer event handling."""

from typing import Protocol


GSTREAMER_BUFFER_SECONDS = 30
GSTREAMER_BUFFER_TIME_NS = GSTREAMER_BUFFER_SECONDS * 1_000_000_000
GSTREAMER_BYTES_PER_SAMPLE = 4


class _EventSink(Protocol):
    """Native event sink surface needed by the speaker pipeline."""

    def send_event(self, event: object) -> bool:
        """Send one event and report whether the sink accepted it."""


class _PropertyTarget(Protocol):
    """Native element surface needed for bounded queue configuration."""

    def find_property(self, name: str) -> object | None:
        """Return a native property descriptor when it exists."""

    def set_property(self, name: str, value: object) -> None:
        """Set one native element property."""


def configure_nonblocking_audio_source(appsrc: _PropertyTarget) -> None:
    """Require a leaky appsrc before enabling non-blocking audio pushes."""
    if appsrc.find_property("leaky-type") is None:
        raise RuntimeError("GStreamer appsrc lacks required leaky-type property")
    appsrc.set_property("block", False)
    appsrc.set_property("leaky-type", 2)


def configure_bounded_audio_buffers(
    appsrc: _PropertyTarget,
    audio_queue: _PropertyTarget,
    sample_rate: int,
) -> None:
    """Configure 30 seconds of bounded, non-blocking native audio buffering."""
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")

    appsrc.set_property("max-buffers", 0)
    appsrc.set_property("max-bytes", sample_rate * GSTREAMER_BYTES_PER_SAMPLE * GSTREAMER_BUFFER_SECONDS)
    audio_queue.set_property("max-size-buffers", 0)
    audio_queue.set_property("max-size-bytes", 0)
    audio_queue.set_property("max-size-time", GSTREAMER_BUFFER_TIME_NS)


def send_flush_events(event_sink: _EventSink, flush_start: object, flush_stop: object) -> None:
    """Send both flush events or raise when the native sink rejects one."""
    if not event_sink.send_event(flush_start):
        raise RuntimeError("GStreamer flush-start event was rejected")
    if not event_sink.send_event(flush_stop):
        raise RuntimeError("GStreamer flush-stop event was rejected")


__all__ = [
    "GSTREAMER_BUFFER_SECONDS",
    "GSTREAMER_BUFFER_TIME_NS",
    "GSTREAMER_BYTES_PER_SAMPLE",
    "configure_bounded_audio_buffers",
    "configure_nonblocking_audio_source",
    "send_flush_events",
]
