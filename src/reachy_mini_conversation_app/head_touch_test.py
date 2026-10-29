"""The head-touch test: spoken cues to tap, press and pat Reachy's head while the movement loop records it.

The Lite has no motion sensor in the head, so a touch can only show as the head being away from where the
motors put it. Only the app knows where that is, so the test runs inside it ("Richie, head test"); the
recording goes to a CSV file for offline analysis and decides whether a touch can end a voice message.
"""

import csv
import time
from typing import Final, Sequence
from pathlib import Path


HEAD_TEST_DIR: Final[Path] = Path.home() / ".cache" / "reachy-mini-conversation-app" / "head-test"
# Each cue starts a phase this long; the recording's ``phase`` column follows them.
HEAD_TEST_BEAT_S: Final[float] = 8.0
HEAD_TEST_PHASES: Final[tuple[str, ...]] = ("rest", "tap", "rest", "press", "rest", "pat", "rest")
HEAD_TEST_CUES: Final[dict[str, tuple[str, ...]]] = {
    "en": (
        "Head test. Don't touch me for now.",
        "Now tap the top of my head, a few times.",
        "Stop.",
        "Now press my head down gently, hold it for a second, and let go. A few times.",
        "Stop.",
        "Now pat my head, like a puppy.",
        "Stop. Don't touch me.",
    ),
    "es": (
        "Prueba de cabeza. No me toques todavía.",
        "Ahora dame unos toquecitos en la cabeza.",
        "Para.",
        "Ahora apriétame la cabeza hacia abajo con suavidad, un segundo, y suelta. Unas cuantas veces.",
        "Para.",
        "Ahora acaríciame la cabeza, como a un perrito.",
        "Para. No me toques.",
    ),
}
HEAD_TEST_DONE: Final[dict[str, str]] = {"en": "Done, thank you!", "es": "¡Listo, gracias!"}
HEAD_TEST_FAILED: Final[dict[str, str]] = {
    "en": "I couldn't record the head test.",
    "es": "No he podido grabar la prueba de cabeza.",
}
# Writing the file, once the last phase is over.
HEAD_TEST_STEP_TIMEOUT_S: Final[float] = 15.0
# Longest a test can run from its first cue to its end, with room for cues that speak past their beat.
HEAD_TEST_MAX_S: Final[float] = len(HEAD_TEST_PHASES) * HEAD_TEST_BEAT_S + HEAD_TEST_STEP_TIMEOUT_S + 15.0


def save_head_test(
    rows: Sequence[Sequence[float]], columns: Sequence[str], started_at: float, directory: Path
) -> Path | None:
    """Write ``rows`` (``t`` first, on the clock of ``started_at``) to a new CSV in ``directory``; None if empty.

    ``t`` becomes seconds since the first cue, and a ``phase`` column names the cue it falls under.
    """
    if not rows:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"head-test-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    with path.open("w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["phase", *columns])
        for row in rows:
            t = float(row[0]) - started_at
            index = min(len(HEAD_TEST_PHASES) - 1, max(0, int(t // HEAD_TEST_BEAT_S)))
            writer.writerow([HEAD_TEST_PHASES[index], f"{t:.3f}", *(f"{float(value):.6f}" for value in row[1:])])
    return path
