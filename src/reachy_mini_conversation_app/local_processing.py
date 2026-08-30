"""Local-only audio processing boundary for the hybrid runtime."""

from typing import Protocol, TypeAlias
from collections.abc import Awaitable

from reachy_mini_conversation_app.conversation_handler import AudioFrame


LocalProcessingResult: TypeAlias = None | Awaitable[None]


class LocalProcessingBackend(Protocol):
    """Observe copied ambient frames without requiring a cloud provider."""

    def observe_frame(self, frame: AudioFrame) -> LocalProcessingResult:
        """Observe one local frame; implementations may return an awaitable."""


class NullLocalProcessingBackend:
    """No-op local processor used until VAD, wake, or STT is selected."""

    def observe_frame(self, frame: AudioFrame) -> None:
        """Ignore one copied local frame."""


__all__ = ["LocalProcessingBackend", "LocalProcessingResult", "NullLocalProcessingBackend"]
