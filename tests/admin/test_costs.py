"""The AI cost breakdown: periods, the period before, features, the chart's buckets and a conversation's parts."""

from typing import Any
from datetime import datetime, timezone, timedelta

import pytest

from reachy_mini_conversation_app.admin.costs import breakdown


NOW = datetime(2026, 10, 31, 15, 30, tzinfo=timezone.utc)


def _entry(days_ago: float, feature: str, cost: float | None, **extra: Any) -> dict[str, Any]:
    ts = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"ts": ts, "feature": feature, "model": extra.pop("model", "gemini-3.8-live"), "cost_usd": cost, **extra}


def test_seven_days_against_the_seven_before() -> None:
    """Costs land in their day; the week before is the comparison; features are ranked with their counts."""
    entries = [
        _entry(0, "conversation", 0.5, duration_s=120),
        _entry(1, "display_drawing", 0.1, model="gpt-image-2.5-flare"),
        _entry(6, "conversation", 0.3, duration_s=60),
        _entry(7, "conversation", 2.0),  # the week before
        _entry(20, "conversation", 9.0),  # outside both
        _entry(0, "memory_capture", None, model="unknown-model"),
    ]
    result = breakdown(entries, NOW, 7)
    assert result["total"] == pytest.approx(0.9) and result["previous"] == pytest.approx(2.0)
    assert result["unpriced"] == 1
    assert [(f["name"], f["count"]) for f in result["features"]] == [("conversation", 2), ("display_drawing", 1)]
    assert [point["label"] for point in result["series"]][::6] == ["2026-10-25", "2026-10-31"]
    assert [point["cost"] for point in result["series"]] == [0.3, 0, 0, 0, 0, 0.1, 0.5]
    assert result["conversations"]["count"] == 2 and result["conversations"]["minutes"] == 3.0


def test_today_is_by_hour() -> None:
    """Today's chart has 24 hours."""
    result = breakdown([_entry(0, "conversation", 0.2)], NOW, 1)
    assert len(result["series"]) == 24 and result["series"][15]["cost"] == 0.2


def test_a_conversations_parts_add_up_to_what_it_cost() -> None:
    """Hearing, seeing, speaking, thinking and reading share the recorded cost by their prices."""
    tokens = {
        "input_text": 100_000,
        "input_audio": 20_000,
        "input_image": 50_000,
        "output_audio": 2_000,
        "thinking": 1_000,
    }
    result = breakdown([_entry(0, "conversation", 0.4, tokens=tokens)], NOW, 7)
    parts = {part["name"]: part["cost"] for part in result["conversations"]["parts"]}
    assert set(parts) == set(tokens)
    assert sum(parts.values()) == pytest.approx(0.4, abs=1e-3)
