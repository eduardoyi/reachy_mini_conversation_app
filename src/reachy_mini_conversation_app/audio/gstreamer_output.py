"""Native GStreamer speaker pipeline for the explicit opt-in backend."""

import importlib

import gi

from reachy_mini_conversation_app.audio.speaker_output import AudioFrame, _GStreamerPipeline


gi.require_version("Gst", "1.0")
Gst = importlib.import_module("gi.repository.Gst")


class NativeGStreamerPipeline:
    """Dedicated appsrc-to-speaker pipeline without the SDK mixer."""

    def __init__(self, sample_rate: int) -> None:
        """Create the native appsrc pipeline at the requested sample rate."""
        Gst.init([])
        pipeline = Gst.Pipeline.new("reachy_app_speaker")
        appsrc = Gst.ElementFactory.make("appsrc", "reachy_app_speaker_src")
        audio_queue = Gst.ElementFactory.make("queue", "reachy_app_speaker_queue")
        audioconvert = Gst.ElementFactory.make("audioconvert", "reachy_app_speaker_convert")
        audioresample = Gst.ElementFactory.make("audioresample", "reachy_app_speaker_resample")
        audiosink = Gst.ElementFactory.make("autoaudiosink", "reachy_app_speaker_sink")
        if pipeline is None or appsrc is None or audio_queue is None or audioconvert is None:
            raise RuntimeError("Required GStreamer speaker elements are unavailable")
        if audioresample is None or audiosink is None:
            raise RuntimeError("Required GStreamer speaker elements are unavailable")

        caps = Gst.Caps.from_string(f"audio/x-raw,format=F32LE,channels=1,rate={sample_rate},layout=interleaved")
        appsrc.set_property("caps", caps)
        appsrc.set_property("is-live", True)
        appsrc.set_property("format", Gst.Format.TIME)
        appsrc.set_property("do-timestamp", True)
        appsrc.set_property("block", False)
        appsrc.set_property("max-buffers", 8)
        appsrc.set_property("max-bytes", sample_rate * 4 // 2)
        if appsrc.find_property("leaky-type") is not None:
            appsrc.set_property("leaky-type", 2)
        audio_queue.set_property("max-size-buffers", 8)
        audio_queue.set_property("max-size-bytes", 0)
        audio_queue.set_property("max-size-time", 500_000_000)
        audio_queue.set_property("leaky", 2)

        for element in (appsrc, audio_queue, audioconvert, audioresample, audiosink):
            pipeline.add(element)
        if not appsrc.link(audio_queue):
            raise RuntimeError("Failed to link GStreamer speaker source")
        if not audio_queue.link(audioconvert):
            raise RuntimeError("Failed to link GStreamer speaker queue")
        if not audioconvert.link(audioresample):
            raise RuntimeError("Failed to link GStreamer speaker converter")
        if not audioresample.link(audiosink):
            raise RuntimeError("Failed to link GStreamer speaker sink")

        self._pipeline = pipeline
        self._appsrc = appsrc

    def start(self) -> None:
        """Set the dedicated pipeline to PLAYING."""
        result = self._pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer speaker pipeline failed to start")

    def push(self, audio: AudioFrame) -> None:
        """Push one copied frame into the non-blocking appsrc."""
        buffer = Gst.Buffer.new_wrapped(audio.tobytes())
        result = self._appsrc.emit("push-buffer", buffer)
        if result != Gst.FlowReturn.OK:
            raise RuntimeError("GStreamer speaker appsrc rejected audio")

    def flush(self) -> None:
        """Flush pending source data without touching the SDK pipeline."""
        self._appsrc.send_event(Gst.Event.new_flush_start())
        self._appsrc.send_event(Gst.Event.new_flush_stop(True))

    def close(self) -> None:
        """Stop the dedicated pipeline."""
        self._pipeline.set_state(Gst.State.NULL)


def build_gstreamer_pipeline(sample_rate: int) -> _GStreamerPipeline:
    """Build the native pipeline after explicit GStreamer selection."""
    return NativeGStreamerPipeline(sample_rate)


__all__ = ["NativeGStreamerPipeline", "build_gstreamer_pipeline"]
