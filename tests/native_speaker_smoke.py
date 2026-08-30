"""Optional native cycles for the app-owned GStreamer speaker boundary."""

import sys
import time
import importlib

import gi
import numpy as np

from reachy_mini_conversation_app.audio.gstreamer_output import NativeGStreamerPipeline


gi.require_version("Gst", "1.0")
Gst = importlib.import_module("gi.repository.Gst")


SAMPLE_RATE = 16_000
FRAME_DURATION_NS = 100_000_000
FRAME_SAMPLES = SAMPLE_RATE // 10
BURST_FRAME_COUNT = 20
BURST_WAIT_SECONDS = 4.0
POST_FLUSH_MARKERS = (101, 102)


def _check_native_burst(pipeline: NativeGStreamerPipeline) -> tuple[int, list[tuple[int, int, int, bool]]]:
    """Push a realtime burst and return native queue overruns and output timing."""
    native_queue = pipeline._pipeline.get_by_name("reachy_app_speaker_queue")
    if native_queue is None:
        raise RuntimeError("GStreamer speaker queue is unavailable")

    overrun_count = 0
    output_timing: list[tuple[int, int, int, bool]] = []

    def on_overrun(_queue: object) -> None:
        nonlocal overrun_count
        overrun_count += 1

    def on_buffer(_pad: Gst.Pad, probe_info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        buffer = probe_info.get_buffer()
        if buffer is not None:
            marker = int(np.frombuffer(buffer.extract_dup(0, 4), dtype="<f4")[0])
            output_timing.append(
                (marker, int(buffer.pts), int(buffer.duration), bool(buffer.has_flags(Gst.BufferFlags.DISCONT)))
            )
        return Gst.PadProbeReturn.OK

    native_queue.connect("overrun", on_overrun)
    src_pad = native_queue.get_static_pad("src")
    if src_pad is None:
        raise RuntimeError("GStreamer speaker queue source pad is unavailable")
    src_pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)

    frame = np.empty(FRAME_SAMPLES, dtype=np.float32)
    pipeline.start()
    time.sleep(0.1)
    for marker in range(1, BURST_FRAME_COUNT + 1):
        frame.fill(marker)
        pipeline.push(frame)

    deadline = time.monotonic() + BURST_WAIT_SECONDS
    while time.monotonic() < deadline and len(output_timing) < BURST_FRAME_COUNT:
        time.sleep(0.01)
    pipeline.flush()
    for marker in POST_FLUSH_MARKERS:
        frame.fill(marker)
        pipeline.push(frame)

    deadline = time.monotonic() + BURST_WAIT_SECONDS
    expected_buffer_count = BURST_FRAME_COUNT + len(POST_FLUSH_MARKERS)
    while time.monotonic() < deadline and len(output_timing) < expected_buffer_count:
        time.sleep(0.01)
    return overrun_count, output_timing


def main() -> int:
    """Run native retention, cadence, flush, and close cycles."""
    try:
        pipeline = NativeGStreamerPipeline(SAMPLE_RATE)
    except ImportError as error:
        module_name = error.name if isinstance(error, ModuleNotFoundError) else "unknown"
        print(f"native speaker smoke import failed: module={module_name}", file=sys.stderr)
        return 1

    try:
        overrun_count, output_timing = _check_native_burst(pipeline)
        pipeline.flush()
    finally:
        pipeline.close()

    if overrun_count != 0:
        print(f"native speaker burst overran queue: count={overrun_count}", file=sys.stderr)
        return 1
    expected_buffer_count = BURST_FRAME_COUNT + len(POST_FLUSH_MARKERS)
    if len(output_timing) != expected_buffer_count:
        print(
            f"native speaker burst lost buffers: expected={expected_buffer_count} actual={len(output_timing)}",
            file=sys.stderr,
        )
        return 1
    expected_markers = list(range(1, BURST_FRAME_COUNT + 1)) + list(POST_FLUSH_MARKERS)
    actual_markers = [timing[0] for timing in output_timing]
    if actual_markers != expected_markers:
        print(
            f"native speaker burst marker order changed: expected={expected_markers} actual={actual_markers}",
            file=sys.stderr,
        )
        return 1

    for previous, current in zip(output_timing[:BURST_FRAME_COUNT], output_timing[1:BURST_FRAME_COUNT]):
        if previous[2] != FRAME_DURATION_NS or current[1] != previous[1] + previous[2]:
            print("native speaker burst timing was not contiguous", file=sys.stderr)
            return 1
    post_flush_timing = output_timing[BURST_FRAME_COUNT:]
    if not post_flush_timing[0][3]:
        print("native speaker post-flush burst did not reset its timeline", file=sys.stderr)
        return 1
    for previous, current in zip(post_flush_timing, post_flush_timing[1:]):
        if previous[2] != FRAME_DURATION_NS or current[1] != previous[1] + previous[2]:
            print("native speaker post-flush timing was not contiguous", file=sys.stderr)
            return 1
    if post_flush_timing[-1][2] != FRAME_DURATION_NS:
        print("native speaker burst duration was not sample-derived", file=sys.stderr)
        return 1

    print("native speaker smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
