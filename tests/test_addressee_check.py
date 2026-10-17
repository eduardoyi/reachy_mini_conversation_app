import json
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from reachy_mini_conversation_app.addressee_check import AddresseeCheck


class _FakeResponses:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(output_text=self.outcome)


def _check(outcome: Any) -> tuple[AddresseeCheck, _FakeResponses]:
    responses = _FakeResponses(outcome)
    return AddresseeCheck(api_key="test", model="test-model", client=SimpleNamespace(responses=responses)), responses


@pytest.mark.asyncio
@pytest.mark.parametrize(("verdict", "expected"), [("yes", True), ("no", False), ("unsure", None)])
async def test_verdicts_map_to_true_false_or_unsure(verdict: str, expected: bool | None) -> None:
    """Only a clear "no" lets the runtime close early."""
    check, responses = _check(json.dumps({"to_reachy": verdict}))

    assert await check("It will be sunny tomorrow.", "ok so what should we have for dinner") is expected
    [request] = responses.requests
    assert request["model"] == "test-model" and request["store"] is False
    assert json.loads(request["input"]) == {
        "reachy_said": "It will be sunny tomorrow.",
        "people_said_after": "ok so what should we have for dinner",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [RuntimeError("down"), "not json", json.dumps({"other": 1})])
async def test_failures_count_as_unsure(outcome: Any) -> None:
    """A provider error or malformed answer never closes the conversation."""
    check, _ = _check(outcome)
    assert await check("Hi!", "hello there") is None


@pytest.mark.asyncio
async def test_long_text_is_trimmed_to_the_latest_part() -> None:
    """Only the most recent part of long speech is sent."""
    check, responses = _check(json.dumps({"to_reachy": "no"}))
    await check("a" * 2000, "b" * 1000 + "end")
    sent = json.loads(responses.requests[0]["input"])
    assert len(sent["reachy_said"]) == 800 and sent["people_said_after"].endswith("end")


@pytest.mark.asyncio
async def test_cancellation_propagates() -> None:
    """Closing the conversation cancels a check in flight."""

    class _Slow:
        async def create(self, **_kwargs: Any) -> Any:
            await asyncio.sleep(10)

    check = AddresseeCheck(api_key="test", model="m", client=SimpleNamespace(responses=_Slow()))
    task = asyncio.create_task(check("Hi!", "hello"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
