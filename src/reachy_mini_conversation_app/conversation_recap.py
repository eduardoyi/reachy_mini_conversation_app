"""Bounded in-memory recap of recent turns, used to continue after a provider ends a session."""

from collections import deque


RECAP_MAX_TURNS = 12
RECAP_HEADER = (
    "The previous live session ended automatically (a provider time limit), and this is a new session "
    "continuing the same conversation. Do not greet the user or reintroduce yourself; wait for the user "
    "and continue naturally. Recent turns, oldest first (transcripts may be imperfect):"
)
_SPEAKER_LABELS = {"user": "User", "assistant": "You"}


class ConversationRecap:
    """Keep the last few transcript turns in memory only; never logged or persisted."""

    def __init__(self, max_turns: int = RECAP_MAX_TURNS) -> None:
        """Start with no turns."""
        self._turns: deque[tuple[str, str]] = deque(maxlen=max_turns)

    def observe(self, role: str, text: str, final: bool) -> None:
        """Merge one transcript event into the current turn for that role.

        User partials can be revised, so only final user text is kept. Providers may
        send cumulative text or deltas, so a chunk that extends the current turn
        replaces it, and anything else is appended.
        """
        if role not in _SPEAKER_LABELS or (role == "user" and not final):
            return
        chunk = " ".join(text.split())
        if not chunk or chunk.startswith("[error]"):
            return
        if not self._turns or self._turns[-1][0] != role:
            self._turns.append((role, chunk))
            return
        current = self._turns[-1][1]
        if chunk.startswith(current):
            merged = chunk
        elif current.endswith(chunk):
            merged = current
        else:
            merged = current + ("" if chunk[0] in ".,!?;:" else " ") + chunk
        self._turns[-1] = (role, merged)

    def clear(self) -> None:
        """Forget all turns."""
        self._turns.clear()

    def render(self, max_chars: int) -> str:
        """Return the header plus the most recent turns that fit within ``max_chars``."""
        if not self._turns or max_chars <= len(RECAP_HEADER):
            return ""
        lines: list[str] = []
        budget = max_chars - len(RECAP_HEADER)
        for role, text in reversed(self._turns):
            line = f"\n{_SPEAKER_LABELS[role]}: {text}"
            if len(line) > budget:
                if not lines:
                    lines.append(line[: budget - 1] + "…")
                break
            lines.append(line)
            budget -= len(line)
        body = "".join(reversed(lines))
        return RECAP_HEADER + body if body else ""
