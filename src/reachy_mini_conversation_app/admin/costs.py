"""AI cost breakdown for the admin app, from the usage ledger: totals, by day or hour, by feature and model.

A conversation's cost is also split by what it paid for (hearing, seeing, speaking, thinking, reading), in
proportion to each token kind's price, so the parts add up to the recorded cost.
"""

from typing import Any, Final
from datetime import datetime, timedelta

from reachy_mini_conversation_app.usage_costs import load_prices, estimate_cost


PERIODS: Final[tuple[int, ...]] = (1, 7, 30)


def _cost(entry: dict[str, Any]) -> float | None:
    cost = entry.get("cost_usd")
    return float(cost) if isinstance(cost, int | float) else None


def _parts(entry: dict[str, Any], cost: float, prices: dict[str, dict[str, float]]) -> dict[str, float]:
    """Split ``cost`` over the entry's token kinds by what each kind costs at today's prices."""
    tokens = entry.get("tokens")
    if not isinstance(tokens, dict):
        return {}
    model = str(entry.get("model") or "")
    estimates = {kind: estimate_cost(model, {kind: int(count)}, prices) or 0.0 for kind, count in tokens.items()
                 if isinstance(count, int | float)}  # fmt: skip
    total = sum(estimates.values())
    return {kind: cost * value / total for kind, value in estimates.items() if value} if total else {}


def breakdown(entries: list[dict[str, Any]], now: datetime, days: int) -> dict[str, Any]:
    """Return what was spent in the last ``days`` days (1: today, by hour), against the period before."""
    today = now.date()
    start = today - timedelta(days=days - 1)
    previous_start = start - timedelta(days=days)
    prices = load_prices()
    total = previous = 0.0
    unpriced = 0
    features: dict[str, dict[str, float]] = {}
    models: dict[str, float] = {}
    buckets = [0.0] * (24 if days == 1 else days)
    talk = {"count": 0, "seconds": 0.0, "cost": 0.0}
    parts: dict[str, float] = {}
    for entry in entries:
        try:
            when = datetime.fromisoformat(str(entry["ts"]))  # its own day, as the ledger's summary counts it
        except (KeyError, ValueError):
            continue
        day = when.date()
        cost = _cost(entry)
        if previous_start <= day < start:
            previous += cost or 0.0
        if not start <= day <= today:
            continue
        if cost is None:
            unpriced += 1
            continue
        total += cost
        feature = str(entry.get("feature") or "other")
        slot = features.setdefault(feature, {"cost": 0.0, "count": 0})
        slot["cost"] += cost
        slot["count"] += 1
        model = str(entry.get("model") or "unknown")
        models[model] = models.get(model, 0.0) + cost
        buckets[when.hour if days == 1 else (day - start).days] += cost
        if feature == "conversation":
            talk["count"] += 1
            talk["cost"] += cost
            seconds = entry.get("duration_s")
            talk["seconds"] += float(seconds) if isinstance(seconds, int | float) else 0.0
            for kind, value in _parts(entry, cost, prices).items():
                parts[kind] = parts.get(kind, 0.0) + value
    labels = [f"{hour:02d}" for hour in range(24)] if days == 1 else [
        (start + timedelta(days=offset)).isoformat() for offset in range(days)
    ]  # fmt: skip

    def ranked(costs: dict[str, float]) -> list[dict[str, Any]]:
        return [
            {"name": name, "cost": round(value, 4)} for name, value in sorted(costs.items(), key=lambda kv: -kv[1])
        ]

    return {
        "days": days,
        "total": round(total, 4),
        "previous": round(previous, 4),
        "unpriced": unpriced,
        "series": [{"label": label, "cost": round(value, 4)} for label, value in zip(labels, buckets)],
        "features": [
            {"name": name, "cost": round(slot["cost"], 4), "count": int(slot["count"])}
            for name, slot in sorted(features.items(), key=lambda kv: -kv[1]["cost"])
        ],
        "models": ranked(models),
        "conversations": {
            "count": int(talk["count"]),
            "minutes": round(talk["seconds"] / 60, 1),
            "cost": round(talk["cost"], 4),
            "parts": ranked(parts),
        },
    }
