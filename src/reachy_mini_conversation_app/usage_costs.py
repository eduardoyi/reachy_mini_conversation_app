"""Record AI API token usage and estimate what it costs.

Every Gemini Live conversation, every OpenAI call (memory capture, daily tidy-up, addressee
check) and every e-ink drawing, web picture search and picture check appends one JSON line to ``usage.jsonl`` next to the household memory file. Costs are
estimates: token counts reported by the providers times the price table below. The real bill is
in Google AI Studio / Cloud Billing and the OpenAI usage dashboard.

Gemini Live reports one ``usage_metadata`` per turn, and its prompt count covers the whole
conversation so far; Google bills every turn's full context, so a session costs the sum of its
turns (measured 2026-10-18: two text turns reported prompts of 2,951 then 3,030 tokens).
"""

from __future__ import annotations
import os
import json
import logging
import threading
from typing import Any, Final
from pathlib import Path
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)

USAGE_FILENAME: Final[str] = "usage.jsonl"

# USD per 1M tokens. Gemini 3.8 Live: third-party reports of Google's paid tier (no official rate card
# found, 2026-10-18); thinking is billed like output audio. gpt-5.6-luna: standard tier after the
# 2026-07-30 price cut. Override with USAGE_PRICES_FILE ({"model-prefix": {"rate": price, ...}}).
DEFAULT_PRICES: Final[dict[str, dict[str, float]]] = {
    "gemini-3.8-live": {
        "input_text": 0.75,
        "input_audio": 3.00,
        "input_image": 1.00,
        "input_video": 1.00,
        "output_text": 4.50,
        "output_audio": 12.00,
        "thinking": 12.00,
    },
    "gpt-5.6-luna": {"input_text": 0.20, "input_cached": 0.02, "output_text": 1.20, "thinking": 1.20},
    # Quick-command replies (local_speech.py); third-party listings of Google's rate, 2026-10-18.
    "gemini-3.8-flash-lite-tts": {"input_text": 0.50, "output_audio": 6.00},
    # E-ink drawings (eink_display.py): Google's $0.0336 per 1K image over the 1,120 image tokens one
    # drawing used (2026-10-24); the input rate is an assumption.
    "gemini-3.1-flash-lite-image": {"input_text": 0.25, "output_image": 30.00},
    # OpenAI's published rates (2026-10-27); a drawing used 301 output image tokens, about $0.009.
    "gpt-image-2.5-flare": {"input_text": 5.00, "input_image": 8.00, "output_image": 30.00},
    # Drawing lesson captions (eink_display.py): third-party listings of Google's introductory rate through
    # 2026-12-31 (2026-10-30), said to double in 2027; thinking is billed as output.
    "gemini-3.8-flash": {"input_text": 0.75, "output_text": 3.75, "thinking": 3.75},
    # E-ink web pictures (web_pictures.py). Jev: $0.042 per 1M input tokens, output free (TypeSafe's
    # published rate, 2026-10-25). Brave Image Search bills per request, recorded as "request": 1 at
    # $5 per 1,000 (the price per 1M requests is 5,000); Brave's $5 monthly credit is not deducted.
    "jev": {"input_text": 0.042, "output_text": 0.0},
    "brave-image-search": {"request": 5000.0},
}


def usage_path() -> Path:
    """Return the ledger path beside the household memory file (``$XDG_DATA_HOME`` aware)."""
    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_mini_conversation_app" / USAGE_FILENAME


def load_prices() -> dict[str, dict[str, float]]:
    """Return the default prices merged with ``USAGE_PRICES_FILE`` overrides, if set."""
    prices = {model: dict(rates) for model, rates in DEFAULT_PRICES.items()}
    override = os.getenv("USAGE_PRICES_FILE")
    if override:
        try:
            raw = json.loads(Path(override).expanduser().read_text())
            for model, rates in raw.items():
                prices.setdefault(str(model), {}).update({str(k): float(v) for k, v in rates.items()})
        except (OSError, ValueError, AttributeError) as error:
            logger.warning("Usage prices file ignored: %s", type(error).__name__)
    return prices


def estimate_cost(
    model: str, tokens: dict[str, int], prices: dict[str, dict[str, float]] | None = None
) -> float | None:
    """Return the estimated USD cost of ``tokens`` for ``model``, or None when its prices are unknown."""
    table = prices if prices is not None else load_prices()
    rates = next((table[prefix] for prefix in sorted(table, key=len, reverse=True) if model.startswith(prefix)), None)
    if rates is None:
        return None
    total = 0.0
    for kind, count in tokens.items():
        rate = rates.get(kind)
        if rate is None and kind.startswith("input_"):
            rate = rates.get("input_text")  # unpriced input modality: price as text
        if rate is None and kind.startswith("output_"):
            rate = rates.get("output_text")
        total += count * (rate or 0.0) / 1_000_000
    return total


def add_tokens(total: dict[str, int], more: dict[str, int]) -> dict[str, int]:
    """Add token counts by kind into ``total`` and return it."""
    for kind, count in more.items():
        total[kind] = total.get(kind, 0) + int(count)
    return total


def gemini_turn_tokens(usage: Any) -> dict[str, int]:
    """Split one Gemini ``UsageMetadata`` into token counts by kind (input_audio, output_audio, thinking...)."""
    tokens: dict[str, int] = {}
    prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
    attributed = 0
    for detail in getattr(usage, "prompt_tokens_details", None) or []:
        modality = getattr(getattr(detail, "modality", None), "value", None) or "text"
        count = int(getattr(detail, "token_count", 0) or 0)
        add_tokens(tokens, {f"input_{str(modality).lower()}": count})
        attributed += count
    if prompt > attributed:
        add_tokens(tokens, {"input_text": prompt - attributed})  # unattributed prompt tokens
    response = int(getattr(usage, "response_token_count", 0) or 0)
    attributed = 0
    for detail in getattr(usage, "response_tokens_details", None) or []:
        modality = getattr(getattr(detail, "modality", None), "value", None) or "text"
        count = int(getattr(detail, "token_count", 0) or 0)
        add_tokens(tokens, {f"output_{str(modality).lower()}": count})
        attributed += count
    if response > attributed:
        add_tokens(tokens, {"output_text": response - attributed})
    thoughts = int(getattr(usage, "thoughts_token_count", 0) or 0)
    if thoughts:
        add_tokens(tokens, {"thinking": thoughts})
    return tokens


def openai_tokens(usage: Any) -> dict[str, int]:
    """Split an OpenAI Responses ``usage`` into token counts by kind."""
    if usage is None:
        return {}
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    cached = int(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    reasoning = int(getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0)
    tokens = {"input_text": max(0, input_tokens - cached), "input_cached": cached}
    tokens["output_text"] = max(0, output_tokens - reasoning)
    tokens["thinking"] = reasoning
    return {kind: count for kind, count in tokens.items() if count}


class UsageLedger:
    """Append-only JSON-lines record of AI usage with cost estimates."""

    def __init__(self, path: Path | None = None, *, clock: Any = None) -> None:
        """Write to ``path`` (default: beside the memory file)."""
        self._path = path or usage_path()
        self._lock = threading.Lock()
        self._clock = clock or (lambda: datetime.now().astimezone())

    @property
    def path(self) -> Path:
        """Where records are written."""
        return self._path

    def record(self, feature: str, model: str, tokens: dict[str, int], **extra: Any) -> float | None:
        """Append one usage record and return its estimated cost (None when the model has no prices)."""
        if not tokens:
            return None
        cost = estimate_cost(model, tokens)
        entry = {
            "ts": self._clock().isoformat(timespec="seconds"),
            "feature": feature,
            "model": model,
            "tokens": tokens,
            "cost_usd": None if cost is None else round(cost, 6),
            **extra,
        }
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as error:
            logger.warning("Usage record not saved: %s", type(error).__name__)
        return cost

    def entries(self) -> list[dict[str, Any]]:
        """Read every record; unreadable lines are skipped."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("ts"), str):
                entries.append(item)
        return entries

    def summary(self, *, recent: int = 10) -> dict[str, Any]:
        """Return spend for today, the last 7 and 30 days, by feature, by day, and the latest conversations."""
        now = self._clock()
        today = now.date()
        periods = {
            "today": today,
            "last_7_days": today - timedelta(days=6),
            "last_30_days": today - timedelta(days=29),
        }
        totals = dict.fromkeys(periods, 0.0)
        by_feature: dict[str, float] = {}
        by_day: dict[str, float] = {}
        conversations = []
        unpriced = 0
        for item in self.entries():
            try:
                day = datetime.fromisoformat(item["ts"]).date()
            except ValueError:
                continue
            cost = item.get("cost_usd")
            if not isinstance(cost, int | float):
                unpriced += 1
                cost = 0.0
            for name, start in periods.items():
                if day >= start:
                    totals[name] += cost
            if day >= periods["last_30_days"]:
                feature = str(item.get("feature") or "other")
                by_feature[feature] = by_feature.get(feature, 0.0) + cost
                by_day[day.isoformat()] = by_day.get(day.isoformat(), 0.0) + cost
            if item.get("feature") == "conversation":
                conversations.append(item)
        conversations = conversations[-recent:][::-1]
        return {
            "totals": {name: round(value, 4) for name, value in totals.items()},
            "by_feature": {k: round(v, 4) for k, v in sorted(by_feature.items(), key=lambda kv: -kv[1])},
            "by_day": {k: round(v, 4) for k, v in sorted(by_day.items())},
            "recent_conversations": [
                {
                    "ts": item["ts"],
                    "duration_s": item.get("duration_s"),
                    "turns": item.get("turns"),
                    "cost_usd": item.get("cost_usd"),
                    "tokens": item.get("tokens"),
                }
                for item in conversations
            ],
            "unpriced_records": unpriced,
            "estimate_note": "Estimates from reported tokens and list prices; the provider bill is authoritative.",
        }


_ledger: UsageLedger | None = None


def get_ledger() -> UsageLedger:
    """Return the process-wide ledger."""
    global _ledger
    if _ledger is None:
        _ledger = UsageLedger()
    return _ledger


def record_openai_usage(feature: str, model: str, response: Any) -> None:
    """Record one OpenAI Responses call; never raises."""
    try:
        get_ledger().record(feature, model, openai_tokens(getattr(response, "usage", None)))
    except Exception as error:  # usage tracking must never break a feature
        logger.debug("Usage tracking failed: %s", type(error).__name__)
