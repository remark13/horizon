"""Descriptive arithmetic on retrieved records, never a scientific verdict."""
from __future__ import annotations

import hashlib
import json

POLICY = {
    "version": "observed-series-0.4.6",
    "count_basis": "retrieved_topic_works",
    "share_basis": "retrieved_topic_works / supplied_corpus_works",
    "comparison": "last_required_full_windows_on_comparable_calendar_scale",
    "unknown_coverage": "describe_sample_only",
    "zero_baseline": "ratio_unknown_not_infinite",
    "partial_window": "exclude_from_comparison",
}


def _descriptor(values: list[float | int] | None, reason: str | None) -> dict:
    if values is None:
        return {"available": False, "reason": reason, "slope_per_window": None,
                "change": None, "relative_change": None, "direction": "not_established"}
    middle = (len(values) - 1) / 2
    mean = sum(values) / len(values)
    slope = sum((i - middle) * (v - mean) for i, v in enumerate(values)) / sum(
        (i - middle) ** 2 for i in range(len(values)))
    change = values[-1] - values[0]
    return {"available": True, "reason": None, "slope_per_window": slope,
            "change": change, "relative_change": change / values[0] if values[0] else None,
            "relative_change_note": "zero_baseline" if not values[0] else None,
            "direction": "increasing" if slope > 0 and change > 0 else (
                "decreasing" if slope < 0 and change < 0 else "flat_or_mixed")}


def describe_observed(recent: list[dict], history: int, same_scale: bool,
                      coverage: bool | None, max_duration_difference_days: int) -> dict:
    reason = ("insufficient_full_windows" if len(recent) != history else
              "incomparable_window_durations" if not same_scale else None)
    counts = None if reason else [p["topic_works"] for p in recent]
    share_reason = reason or ("missing_or_zero_denominator" if any(
        p["share"] is None for p in recent) else None)
    shares = None if share_reason else [p["share"] for p in recent]
    effective_policy = {**POLICY, "required_history_windows": history,
                        "max_calendar_duration_difference_days": max_duration_difference_days}
    return {"version": POLICY["version"], "policy": effective_policy,
            "policy_sha256": hashlib.sha256(json.dumps(effective_policy, sort_keys=True).encode()).hexdigest(),
            "count": _descriptor(counts, reason), "share": _descriptor(shares, share_reason),
            "coverage_comparable": coverage,
            "scope": "retrieved_sample_only" if coverage is not True else "supplied_comparable_corpus",
            "scientific_signal_confirmed": None,
            "interpretation": "Рост/спад найденных работ и их доли — отдельно. При неизвестном или различном покрытии это описание выборки, а не доказательство роста темы. Дубли и изменение сбора могут объяснять динамику."}
