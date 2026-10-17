import json
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from reachy_mini_conversation_app import usage_costs
from reachy_mini_conversation_app.usage_costs import (
    UsageLedger,
    estimate_cost,
    openai_tokens,
    gemini_turn_tokens,
)


def _detail(modality: str, count: int) -> SimpleNamespace:
    return SimpleNamespace(modality=SimpleNamespace(value=modality), token_count=count)


def test_gemini_usage_splits_by_modality_and_prices_each_kind() -> None:
    """A turn's prompt and response split by modality; unattributed prompt tokens count as text."""
    usage = SimpleNamespace(
        prompt_token_count=3030,
        prompt_tokens_details=[_detail("TEXT", 2631), _detail("AUDIO", 274)],
        response_token_count=63,
        response_tokens_details=[_detail("AUDIO", 63)],
        thoughts_token_count=30,
    )
    tokens = gemini_turn_tokens(usage)
    assert tokens == {"input_text": 2756, "input_audio": 274, "output_audio": 63, "thinking": 30}

    cost = estimate_cost("gemini-3.8-live-extended-thinking", tokens)
    expected = (2756 * 0.75 + 274 * 3.00 + 63 * 12.00 + 30 * 12.00) / 1_000_000
    assert cost == pytest.approx(expected)
    assert estimate_cost("some-new-model", tokens) is None


def test_openai_usage_separates_cached_and_reasoning_tokens() -> None:
    """Cached input and reasoning output are priced at their own rates."""
    usage = SimpleNamespace(
        input_tokens=1000,
        input_tokens_details=SimpleNamespace(cached_tokens=400),
        output_tokens=150,
        output_tokens_details=SimpleNamespace(reasoning_tokens=100),
    )
    tokens = openai_tokens(usage)
    assert tokens == {"input_text": 600, "input_cached": 400, "output_text": 50, "thinking": 100}
    assert estimate_cost("gpt-5.6-luna", tokens) == pytest.approx((600 * 0.20 + 400 * 0.02 + 150 * 1.20) / 1e6)
    assert openai_tokens(None) == {}


def test_price_overrides_come_from_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """USAGE_PRICES_FILE updates or adds rates without code changes."""
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"gpt-5.6-luna": {"input_text": 1.0}, "new-model": {"input_text": 2.0}}))
    monkeypatch.setenv("USAGE_PRICES_FILE", str(prices))
    assert estimate_cost("gpt-5.6-luna", {"input_text": 1_000_000}) == pytest.approx(1.0)
    assert estimate_cost("new-model-v2", {"input_text": 1_000_000}) == pytest.approx(2.0)


def test_ledger_records_and_summarizes_by_period_feature_and_conversation(tmp_path: Path) -> None:
    """Totals cover today, 7 and 30 days; conversations are listed newest first; unpriced records are counted."""
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    clock = {"now": now - timedelta(days=10)}
    ledger = UsageLedger(tmp_path / "usage.jsonl", clock=lambda: clock["now"])

    ledger.record("conversation", "gemini-3.8-live", {"input_audio": 1_000_000}, turns=4, duration_s=60.0)
    clock["now"] = now - timedelta(days=3)
    ledger.record("memory_capture", "gpt-5.6-luna", {"input_text": 1_000_000})
    clock["now"] = now
    ledger.record("conversation", "gemini-3.8-live", {"output_audio": 100_000}, turns=2, duration_s=20.0)
    ledger.record("addressee_check", "mystery-model", {"input_text": 10})
    assert ledger.record("conversation", "gemini-3.8-live", {}) is None  # nothing to record

    summary = ledger.summary()
    assert summary["totals"] == {"today": 1.2, "last_7_days": 1.4, "last_30_days": 4.4}
    assert summary["by_feature"] == {"conversation": 4.2, "memory_capture": 0.2, "addressee_check": 0.0}
    assert [item["turns"] for item in summary["recent_conversations"]] == [2, 4]
    assert summary["unpriced_records"] == 1


def test_tests_never_write_to_the_real_ledger() -> None:
    """The autouse fixture points the process ledger at a temporary file."""
    assert "reachy_mini_conversation_app" not in str(usage_costs.get_ledger().path)


def test_settings_route_serves_the_summary_to_this_computer_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/usage returns the ledger summary uncached, and refuses requests from another machine."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from reachy_mini_conversation_app.config import config
    from reachy_mini_conversation_app.console import LocalStream

    monkeypatch.setattr(config, "AUDIO_OUTPUT_BACKEND", "sdk")
    usage_costs.get_ledger().record("conversation", "gemini-3.8-live", {"input_audio": 1000}, turns=1, duration_s=5.0)
    app = FastAPI()
    LocalStream(MagicMock(), MagicMock(), settings_app=app, instance_path=str(tmp_path))._init_settings_ui_if_needed()

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        local = client.get("/api/usage")
    with TestClient(app, base_url="http://192.168.1.50", client=("192.168.1.50", 50000)) as client:
        remote = client.get("/api/usage")

    assert local.status_code == 200 and local.headers["cache-control"] == "no-store"
    assert local.json()["recent_conversations"][0]["turns"] == 1
    assert remote.status_code == 403
