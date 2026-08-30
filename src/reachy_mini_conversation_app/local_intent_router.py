"""Conservative local command classification for the hybrid runtime."""

import re
import unicodedata
from enum import Enum
from typing import Literal, TypeAlias
from dataclasses import dataclass


InputMode: TypeAlias = Literal["wake_prefixed", "wake_stripped"]


class LocalIntent(str, Enum):
    """Allowlisted actions that can run without a cloud session."""

    STOP = "stop"
    BE_QUIET = "be_quiet"
    LOOK_LEFT = "look_left"
    LOOK_RIGHT = "look_right"
    LOOK_AT_ME = "look_at_me"


class IntentRoute(str, Enum):
    """Destination selected by the local classifier."""

    LOCAL_COMMAND = "local_command"
    ESCALATE = "escalate"


class IntentDecisionReason(str, Enum):
    """Reason metadata explaining a local or escalation decision."""

    EXACT_MATCH = "exact_match"
    EMPTY_INPUT = "empty_input"
    COMMAND_MISSING = "command_missing"
    WAKE_WORD_MISSING = "wake_word_missing"
    UNEXPECTED_WAKE_WORD = "unexpected_wake_word"
    UNSUPPORTED_COMMAND = "unsupported_command"
    AMBIGUOUS_COMMAND = "ambiguous_command"
    MALFORMED_INPUT = "malformed_input"


@dataclass(frozen=True, slots=True)
class LocalIntentDecision:
    """Typed, side-effect-free result from the local intent router."""

    route: IntentRoute
    action: LocalIntent | None
    normalized_text: str
    input_mode: InputMode
    wake_word_detected: bool
    reason: IntentDecisionReason

    @property
    def is_local(self) -> bool:
        """Return whether the decision is safe to execute locally."""
        return self.route is IntentRoute.LOCAL_COMMAND

    @property
    def should_escalate(self) -> bool:
        """Return whether the caller should continue through the cloud path."""
        return self.route is IntentRoute.ESCALATE

    @property
    def intent(self) -> LocalIntent | None:
        """Return the action under the alternate intent-oriented name."""
        return self.action


_VALID_INPUT_MODES = frozenset(("wake_prefixed", "wake_stripped"))
_PUNCTUATION_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_COMMAND_ALIASES: dict[LocalIntent, frozenset[str]] = {
    LocalIntent.STOP: frozenset(("stop", "stop moving", "halt")),
    LocalIntent.BE_QUIET: frozenset(("be quiet", "quiet", "shush", "stop talking")),
    LocalIntent.LOOK_LEFT: frozenset(("look left", "look to the left")),
    LocalIntent.LOOK_RIGHT: frozenset(("look right", "look to the right")),
    LocalIntent.LOOK_AT_ME: frozenset(("look at me", "look here")),
}


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    return " ".join(normalized.split())


class LocalIntentRouter:
    """Classify only exact, safe local commands and escalate everything else."""

    def __init__(self, *, wake_word: str = "Reachy") -> None:
        """Configure the wake word used by the prefixed input contract."""
        normalized_wake_word = _normalize_text(wake_word)
        if not normalized_wake_word:
            raise ValueError("Wake word must contain at least one word")
        self._wake_word = normalized_wake_word

    @property
    def wake_word(self) -> str:
        """Return the normalized wake word."""
        return self._wake_word

    def route(self, text: object, *, input_mode: InputMode = "wake_stripped") -> LocalIntentDecision:
        """Route wake-prefixed or wake-stripped text without side effects."""
        if input_mode not in _VALID_INPUT_MODES:
            return self._escalation(
                normalized_text="",
                input_mode=input_mode,
                wake_word_detected=False,
                reason=IntentDecisionReason.MALFORMED_INPUT,
            )
        if not isinstance(text, str):
            return self._escalation(
                normalized_text="",
                input_mode=input_mode,
                wake_word_detected=False,
                reason=IntentDecisionReason.MALFORMED_INPUT,
            )

        normalized_text = _normalize_text(text)
        if not normalized_text:
            return self._escalation(
                normalized_text="",
                input_mode=input_mode,
                wake_word_detected=False,
                reason=IntentDecisionReason.EMPTY_INPUT,
            )

        if input_mode == "wake_prefixed":
            return self._route_prefixed(normalized_text)
        return self._route_stripped(normalized_text)

    def route_wake_prefixed(self, text: object) -> LocalIntentDecision:
        """Classify text whose first words must be the configured wake word."""
        return self.route(text, input_mode="wake_prefixed")

    def route_wake_stripped(self, text: object) -> LocalIntentDecision:
        """Classify text after a local wake detector removed the wake word."""
        return self.route(text, input_mode="wake_stripped")

    def _route_prefixed(self, normalized_text: str) -> LocalIntentDecision:
        if normalized_text == self._wake_word:
            return self._escalation(
                normalized_text="",
                input_mode="wake_prefixed",
                wake_word_detected=True,
                reason=IntentDecisionReason.COMMAND_MISSING,
            )
        wake_prefix = f"{self._wake_word} "
        if not normalized_text.startswith(wake_prefix):
            return self._escalation(
                normalized_text=normalized_text,
                input_mode="wake_prefixed",
                wake_word_detected=False,
                reason=IntentDecisionReason.WAKE_WORD_MISSING,
            )
        command_text = normalized_text[len(wake_prefix) :].strip()
        return self._classify_command(command_text, "wake_prefixed", True)

    def _route_stripped(self, normalized_text: str) -> LocalIntentDecision:
        if normalized_text == self._wake_word or normalized_text.startswith(f"{self._wake_word} "):
            return self._escalation(
                normalized_text=normalized_text,
                input_mode="wake_stripped",
                wake_word_detected=True,
                reason=IntentDecisionReason.UNEXPECTED_WAKE_WORD,
            )
        return self._classify_command(normalized_text, "wake_stripped", False)

    def _classify_command(
        self,
        normalized_text: str,
        input_mode: InputMode,
        wake_word_detected: bool,
    ) -> LocalIntentDecision:
        for action, aliases in _COMMAND_ALIASES.items():
            if normalized_text in aliases:
                return LocalIntentDecision(
                    route=IntentRoute.LOCAL_COMMAND,
                    action=action,
                    normalized_text=normalized_text,
                    input_mode=input_mode,
                    wake_word_detected=wake_word_detected,
                    reason=IntentDecisionReason.EXACT_MATCH,
                )

        reason = (
            IntentDecisionReason.AMBIGUOUS_COMMAND
            if self._contains_multiple_command_phrases(normalized_text)
            else IntentDecisionReason.UNSUPPORTED_COMMAND
        )
        return self._escalation(
            normalized_text=normalized_text,
            input_mode=input_mode,
            wake_word_detected=wake_word_detected,
            reason=reason,
        )

    @staticmethod
    def _contains_multiple_command_phrases(normalized_text: str) -> bool:
        matched_commands = {
            action
            for action, aliases in _COMMAND_ALIASES.items()
            if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized_text) for alias in aliases)
        }
        return len(matched_commands) > 1

    @staticmethod
    def _escalation(
        *,
        normalized_text: str,
        input_mode: InputMode,
        wake_word_detected: bool,
        reason: IntentDecisionReason,
    ) -> LocalIntentDecision:
        return LocalIntentDecision(
            route=IntentRoute.ESCALATE,
            action=None,
            normalized_text=normalized_text,
            input_mode=input_mode,
            wake_word_detected=wake_word_detected,
            reason=reason,
        )


__all__ = [
    "InputMode",
    "IntentDecisionReason",
    "IntentRoute",
    "LocalIntent",
    "LocalIntentDecision",
    "LocalIntentRouter",
]
