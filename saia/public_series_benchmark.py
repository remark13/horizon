"""Isolated public-series pilot; no writes to SAIA database or model config."""
from __future__ import annotations

import math
from statistics import mean

AI_TERMS = {
    "artificial neural networks", "neural networks", "perceptron",
    "machine learning", "language model", "graph neural network",
    "genetic algorithm", "HMM",
}


def features(series: dict[int, float], freeze: int) -> dict:
    years = range(freeze - 5, freeze + 1)
    if any(y not in series for y in years):
        raise ValueError("Missing feature year")
    if any(not math.isfinite(series[y]) or series[y] < 0 for y in years):
        raise ValueError("Invalid feature value")
    previous = mean(series[y] for y in range(freeze - 5, freeze - 2))
    recent = mean(series[y] for y in range(freeze - 2, freeze + 1))
    growth = recent / previous if previous > 0 else None
    increases = sum(series[y] > series[y-1] for y in range(freeze - 2, freeze + 1))
    current = series[freeze]
    yoy = current / series[freeze-1] if series[freeze-1] > 0 else None
    return {
        "current_per_100k": current, "past_mean_per_100k": previous,
        "recent_mean_per_100k": recent, "past_growth_ratio": growth,
        "positive_year_changes": increases, "eligible": recent >= 1,
        "low_visibility": current <= 100,
        "candidate": bool(recent >= 1 and current <= 100 and growth is not None
                          and growth >= 1.5 and increases >= 2),
        "last_year_baseline": bool(yoy is not None and yoy >= 1.15),
    }


def outcomes(series: dict[int, float], freeze: int, recent: float) -> dict:
    years = range(freeze + 1, freeze + 6)
    if any(y not in series for y in years):
        raise ValueError("Missing outcome year")
    if any(not math.isfinite(series[y]) or series[y] < 0 for y in years):
        raise ValueError("Invalid outcome value")
    future = mean(series[y] for y in years)
    ratio = future / recent if recent > 0 else None
    endpoint = series[freeze+5] / series[freeze] if series[freeze] > 0 else None
    return {
        "future_mean_per_100k": future, "future_mean_ratio": ratio,
        "grew_mean_50pct": bool(ratio is not None and ratio >= 1.5),
        "future_endpoint_ratio": endpoint,
        "grew_endpoint_15pct": None if endpoint is None else endpoint >= 1.15,
    }


def metrics(rows: list[dict], prediction: str, label: str) -> dict:
    valid = [r for r in rows if r[label] is not None]
    tp = sum(bool(r[prediction]) and bool(r[label]) for r in valid)
    fp = sum(bool(r[prediction]) and not r[label] for r in valid)
    fn = sum(not r[prediction] and bool(r[label]) for r in valid)
    tn = len(valid) - tp - fp - fn
    precision = tp / (tp+fp) if tp+fp else None
    recall = tp / (tp+fn) if tp+fn else None
    specificity = tn / (tn+fp) if tn+fp else None
    base = (tp+fn) / len(valid) if valid else None
    return {"n": len(valid), "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": precision, "recall": recall,
            "F1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
            "balanced_accuracy": (recall+specificity)/2
            if recall is not None and specificity is not None else None,
            "base_rate": base,
            "lift": precision/base if precision is not None and base else None}
