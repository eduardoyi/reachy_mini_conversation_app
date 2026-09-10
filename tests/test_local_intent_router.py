"""Tests for conservative local command classification."""

from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from reachy_mini_conversation_app.local_intent_router import (
    IntentRoute,
    LocalIntent,
    LocalIntentRouter,
    LocalIntentDecision,
    IntentDecisionReason,
)


@pytest.fixture
def router() -> LocalIntentRouter:
    """Return a router with the default Reachy wake word."""
    return LocalIntentRouter()


def test_default_wake_word_is_reachy(router: LocalIntentRouter) -> None:
    """The router uses the written product wake phrase unless overridden."""
    assert router.wake_word == "reachy"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("stop", LocalIntent.STOP),
        ("stop moving", LocalIntent.STOP),
        ("halt", LocalIntent.STOP),
        ("be quiet", LocalIntent.BE_QUIET),
        ("quiet", LocalIntent.BE_QUIET),
        ("shush", LocalIntent.BE_QUIET),
        ("stop talking", LocalIntent.BE_QUIET),
        ("look left", LocalIntent.LOOK_LEFT),
        ("look to the left", LocalIntent.LOOK_LEFT),
        ("look right", LocalIntent.LOOK_RIGHT),
        ("look to the right", LocalIntent.LOOK_RIGHT),
        ("look at me", LocalIntent.LOOK_AT_ME),
        ("look here", LocalIntent.LOOK_AT_ME),
    ],
)
def test_exact_wake_stripped_commands_are_local(
    router: LocalIntentRouter,
    command: str,
    expected: LocalIntent,
) -> None:
    """Classify each exact wake-stripped allowlist entry locally."""
    decision = router.route_wake_stripped(command)

    assert decision.route is IntentRoute.LOCAL_COMMAND
    assert decision.action is expected
    assert decision.intent is expected
    assert decision.is_local
    assert not decision.should_escalate
    assert decision.reason is IntentDecisionReason.EXACT_MATCH
    assert not decision.wake_word_detected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Reachy, stop!", LocalIntent.STOP),
        ("REACHY BE QUIET.", LocalIntent.BE_QUIET),
        ("reachy: look LEFT", LocalIntent.LOOK_LEFT),
        ("Reachy... look to the right?", LocalIntent.LOOK_RIGHT),
        ("Reachy, look at me!", LocalIntent.LOOK_AT_ME),
    ],
)
def test_wake_prefixed_contract_accepts_boundary_punctuation_and_case(
    router: LocalIntentRouter,
    utterance: str,
    expected: LocalIntent,
) -> None:
    """Allow punctuation and case changes at wake and command boundaries."""
    decision = router.route_wake_prefixed(utterance)

    assert decision.route is IntentRoute.LOCAL_COMMAND
    assert decision.action is expected
    assert decision.input_mode == "wake_prefixed"
    assert decision.wake_word_detected


@pytest.mark.parametrize(
    "utterance",
    [
        "please stop",
        "stop dancing",
        "look left please",
        "turn left",
        "look at everyone",
        "unstoppable",
        "be quiet-ish",
        "look left or right",
        "stop and look left",
    ],
)
def test_non_allowlisted_or_ambiguous_text_escalates(router: LocalIntentRouter, utterance: str) -> None:
    """Never guess when an utterance contains extra or conflicting words."""
    decision = router.route_wake_stripped(utterance)

    assert decision.route is IntentRoute.ESCALATE
    assert decision.action is None
    assert decision.should_escalate
    assert decision.reason in {
        IntentDecisionReason.UNSUPPORTED_COMMAND,
        IntentDecisionReason.AMBIGUOUS_COMMAND,
    }


@pytest.mark.parametrize(
    ("utterance", "reason"),
    [
        ("", IntentDecisionReason.EMPTY_INPUT),
        ("   !!!   ", IntentDecisionReason.EMPTY_INPUT),
        ("Reachy", IntentDecisionReason.COMMAND_MISSING),
        ("look", IntentDecisionReason.UNSUPPORTED_COMMAND),
        ("left", IntentDecisionReason.UNSUPPORTED_COMMAND),
    ],
)
def test_empty_or_incomplete_commands_escalate_with_reason(
    router: LocalIntentRouter,
    utterance: str,
    reason: IntentDecisionReason,
) -> None:
    """Return a useful fail-closed reason for empty and incomplete input."""
    decision = (
        router.route_wake_prefixed(utterance) if utterance == "Reachy" else router.route_wake_stripped(utterance)
    )

    assert decision.route is IntentRoute.ESCALATE
    assert decision.reason is reason


def test_wake_prefixed_and_wake_stripped_contracts_do_not_silently_overlap(router: LocalIntentRouter) -> None:
    """Keep the two caller contracts explicit instead of stripping implicitly."""
    missing_prefix = router.route_wake_prefixed("stop")
    unexpected_prefix = router.route_wake_stripped("Reachy stop")

    assert missing_prefix.reason is IntentDecisionReason.WAKE_WORD_MISSING
    assert not missing_prefix.wake_word_detected
    assert unexpected_prefix.reason is IntentDecisionReason.UNEXPECTED_WAKE_WORD
    assert unexpected_prefix.wake_word_detected


@pytest.mark.parametrize("malformed", [None, 42, b"stop", ["stop"], {"command": "stop"}])
def test_malformed_input_escalates_without_coercion(router: LocalIntentRouter, malformed: object) -> None:
    """Reject non-text values rather than coercing them into a command."""
    decision = router.route_wake_stripped(malformed)

    assert decision.route is IntentRoute.ESCALATE
    assert decision.action is None
    assert decision.reason is IntentDecisionReason.MALFORMED_INPUT


def test_invalid_input_mode_fails_closed(router: LocalIntentRouter) -> None:
    """Reject an unknown input contract without selecting a local action."""
    decision = router.route("stop", input_mode="provider_generated")

    assert decision.route is IntentRoute.ESCALATE
    assert decision.reason is IntentDecisionReason.MALFORMED_INPUT


def test_custom_wake_word_is_normalized_but_commands_remain_strict() -> None:
    """Normalize a custom wake word while retaining exact command matching."""
    router = LocalIntentRouter(wake_word="  Johnny! ")

    decision = router.route_wake_prefixed("JOHNNY, look right!")

    assert router.wake_word == "johnny"
    assert decision.action is LocalIntent.LOOK_RIGHT


def test_historical_tito_requires_explicit_router_override() -> None:
    """The default Reachy router does not silently retain the historical Tito wake."""
    assert LocalIntentRouter().route_wake_prefixed("Tito, stop").reason is IntentDecisionReason.WAKE_WORD_MISSING

    historical_router = LocalIntentRouter(wake_word="Tito")
    assert historical_router.route_wake_prefixed("Tito, stop").action is LocalIntent.STOP


def test_empty_wake_word_is_rejected() -> None:
    """Require a usable wake word at construction time."""
    with pytest.raises(ValueError, match="Wake word"):
        LocalIntentRouter(wake_word="!!!")


def test_decision_is_frozen_and_contains_metadata_only(router: LocalIntentRouter) -> None:
    """Keep decisions immutable and free from executable side effects."""
    decision = router.route_wake_stripped("look at me")

    assert is_dataclass(decision)
    assert isinstance(decision, LocalIntentDecision)
    assert decision.normalized_text == "look at me"
    assert decision.input_mode == "wake_stripped"
    with pytest.raises(FrozenInstanceError):
        decision.action = LocalIntent.STOP  # type: ignore[misc]
