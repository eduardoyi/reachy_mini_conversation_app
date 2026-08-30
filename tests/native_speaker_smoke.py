"""Optional native cycles for the app-owned GStreamer speaker boundary."""

import sys
import time

import numpy as np

from reachy_mini_conversation_app.audio.speaker_output import GStreamerSpeakerOutput


def main() -> int:
    """Run short native output bursts and interruption cycles."""
    try:
        output = GStreamerSpeakerOutput(16_000)
    except ImportError as error:
        module_name = error.name if isinstance(error, ModuleNotFoundError) else "unknown"
        print(f"native speaker smoke import failed: module={module_name}", file=sys.stderr)
        return 1

    frame = np.zeros(320, dtype=np.float32)
    try:
        output.start()
        for _ in range(3):
            for _ in range(24):
                output.push(frame)
            output.flush()
            output.push(frame)
            time.sleep(0.05)
    finally:
        output.close()

    print("native speaker smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
