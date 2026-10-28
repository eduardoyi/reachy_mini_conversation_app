from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.maribel import MaribelReply
from reachy_mini_conversation_app.quick_commands import MARIBEL_PASSED_ON
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.ask_maribel import AskMaribel


class _Maribel:
    def __init__(self, reply: MaribelReply | None) -> None:
        self.reply = reply
        self.sent: list[str] = []

    async def send(self, text: str, *, reuse_repeat: bool = False) -> str | None:
        assert reuse_repeat  # a call repeated after an interruption must not reach her twice
        self.sent.append(text)
        return "r-1"

    async def first_reply(self, request_id: str, timeout_s: float = 10.0) -> MaribelReply | None:
        return self.reply

    def has_open_question(self) -> bool:
        return False


def _deps(maribel: Any) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), maribel=maribel)


@pytest.mark.asyncio
async def test_the_tool_passes_the_words_and_returns_only_what_she_says() -> None:
    """The tool sends the words and returns only her speak text."""
    maribel = _Maribel(MaribelReply("r-1", "c-1", "question", "For how many?", "q-1"))

    result = await AskMaribel()(_deps(maribel), request="book dinner for Friday")

    assert maribel.sent == ["book dinner for Friday"]
    assert result["say"] == "For how many?" and result["status"] == "question"
    assert "ask_maribel" in result["note"]  # the answer goes back through the tool


@pytest.mark.asyncio
async def test_the_tool_says_it_passed_the_request_on_when_she_is_slow() -> None:
    """No reply within the wait: the passed-on line."""
    result = await AskMaribel()(_deps(_Maribel(None)), request="what's on my calendar")
    assert result["status"] == "passed_on" and result["say"] == MARIBEL_PASSED_ON["en"]


@pytest.mark.asyncio
async def test_the_tool_needs_maribel_and_words() -> None:
    """Errors without Maribel or without words."""
    assert "error" in await AskMaribel()(_deps(None), request="hi")
    assert "error" in await AskMaribel()(_deps(_Maribel(None)), request="  ")
