"""Tests for the in-memory conversation recap used after provider-ended sessions."""

from reachy_mini_conversation_app.conversation_recap import RECAP_HEADER, ConversationRecap


def test_recap_merges_cumulative_and_delta_chunks_per_turn() -> None:
    """Cumulative text replaces the turn, deltas append, and a role change starts a new turn."""
    recap = ConversationRecap()
    recap.observe("user", "play some jazz", True)
    recap.observe("assistant", "Sure", False)
    recap.observe("assistant", "thing", False)
    recap.observe("assistant", "!", False)
    recap.observe("assistant", "Sure thing!", True)
    recap.observe("user", "thanks", True)

    assert recap.render(2_000) == RECAP_HEADER + "\nUser: play some jazz\nYou: Sure thing!\nUser: thanks"


def test_recap_ignores_user_partials_errors_and_unknown_roles() -> None:
    """Revisable user partials, local error notices, and other roles stay out of the recap."""
    recap = ConversationRecap()
    recap.observe("user", "what's the wea", False)
    recap.observe("assistant", "[error] Gemini Live session time limit reached.", True)
    recap.observe("user_partial", "hello", True)

    assert recap.render(2_000) == ""


def test_recap_render_keeps_the_newest_turns_within_the_limit() -> None:
    """Older turns are dropped first so the rendered recap fits the context budget."""
    recap = ConversationRecap(max_turns=3)
    for index in range(5):
        recap.observe("user" if index % 2 == 0 else "assistant", f"turn {index}", True)

    assert recap.render(2_000) == RECAP_HEADER + "\nUser: turn 2\nYou: turn 3\nUser: turn 4"
    limited = recap.render(len(RECAP_HEADER) + len("\nUser: turn 4"))
    assert limited == RECAP_HEADER + "\nUser: turn 4"
    assert recap.render(len(RECAP_HEADER)) == ""


def test_recap_truncates_a_single_long_turn() -> None:
    """A newest turn longer than the budget is cut rather than dropped."""
    recap = ConversationRecap()
    recap.observe("assistant", "x" * 500, True)

    rendered = recap.render(len(RECAP_HEADER) + 50)

    assert len(rendered) == len(RECAP_HEADER) + 50
    assert rendered.endswith("…")
