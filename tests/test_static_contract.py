"""Deterministic source contracts for the browser activation controls."""

from pathlib import Path


STATIC_JS = Path(__file__).parents[1] / "src" / "reachy_mini_conversation_app" / "static" / "js"


def test_talk_view_guards_keyboard_activation_and_recovers_hybrid_state() -> None:
    """The browser binding remains opt-in and handles runtime state recovery."""
    source = (STATIC_JS / "views" / "talk.js").read_text(encoding="utf-8")

    assert "getStatus" in source
    assert "hybrid_runtime_enabled === true" in source
    assert "if (hybridEnabled) document.addEventListener" in source
    assert "event.repeat" in source
    assert "isContentEditable" in source
    assert "conversation.hybrid_state" in source
    assert 'state === "LOCAL_AMBIENT"' in source


def test_api_exposes_the_durable_activation_method() -> None:
    """The UI uses the dedicated RPC method instead of the mute control."""
    source = (STATIC_JS / "api.js").read_text(encoding="utf-8")

    assert 'rpcCall("conversation.activate", {})' in source
