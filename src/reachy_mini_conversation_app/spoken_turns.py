"""Join Gemini Live transcript pieces into whole utterances for household memory capture.

Gemini 3.8 Live sends both input and output transcriptions as consecutive pieces of one turn
(" nuevo en Lisboa" continues "mi hermano Pablo empieza un trabajo") and never marks them finished.
Observed in a recorded trial on 2026-10-17 (docs/features/household-memory.md, "Gemini spoken capture").
A user utterance therefore ends when the model starts answering or its turn completes; an answer
ends when its turn completes, and an interrupted answer is dropped, unfinished.
Each utterance is handed to capture exactly once.
"""

from __future__ import annotations
import time
from typing import Literal, Callable
from dataclasses import field, dataclass


Role = Literal["user", "assistant"]
# role, text, start_ms, end_ms, speaker_id
Emit = Callable[[Role, str, int, int, "str | None"], None]


@dataclass
class _Utterance:
    pieces: list[str] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0
    speaker_id: str | None = None

    def text(self) -> str:
        # Pieces carry their own leading spaces; join them as sent.
        return "".join(self.pieces).strip()


class SpokenTurnAssembler:
    """Collect transcript pieces per turn and emit each finished utterance once."""

    def __init__(
        self,
        emit: Emit,
        *,
        speaker: Callable[[], str | None] | None = None,
        clock_ms: Callable[[], int] = lambda: int(time.monotonic() * 1000),
    ) -> None:
        """Send utterances to ``emit``; ``speaker`` names who the camera recognizes when the user starts."""
        self._emit = emit
        self._speaker = speaker
        self._clock_ms = clock_ms
        self._user: _Utterance | None = None
        self._assistant: _Utterance | None = None

    def user_piece(self, text: str | None) -> None:
        """Add a piece of what the user said."""
        if not text:
            return
        now = self._clock_ms()
        if self._user is None:
            self._user = _Utterance(start_ms=now, end_ms=now, speaker_id=self._current_speaker())
        self._user.pieces.append(text)
        self._user.end_ms = now

    def assistant_piece(self, text: str | None) -> None:
        """Add a piece of the model's answer; its first piece ends the user's utterance."""
        if not text:
            return
        self._flush_user()
        now = self._clock_ms()
        if self._assistant is None:
            self._assistant = _Utterance(start_ms=now, end_ms=now)
        self._assistant.pieces.append(text)
        self._assistant.end_ms = now

    def interrupted(self) -> None:
        """Drop the cut-off answer and keep what the user said."""
        self._assistant = None

    def turn_complete(self) -> None:
        """End the model's turn: emit the user's utterance, then the complete answer."""
        self._flush_user()
        assistant, self._assistant = self._assistant, None
        if assistant is not None and assistant.text():
            self._emit("assistant", assistant.text(), assistant.start_ms, assistant.end_ms, None)

    def reset(self) -> None:
        """Forget unfinished utterances (reconnect or session end; no flush)."""
        self._user = None
        self._assistant = None

    def _flush_user(self) -> None:
        user, self._user = self._user, None
        if user is not None and user.text():
            self._emit("user", user.text(), user.start_ms, user.end_ms, user.speaker_id)

    def _current_speaker(self) -> str | None:
        if self._speaker is None:
            return None
        try:
            return self._speaker()
        except Exception:
            return None
