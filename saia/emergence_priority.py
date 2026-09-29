"""Predeclared observed-emergence ranking; no target labels or probability claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class EmergencePriorityPolicy:
    version: str = "observed-emergence-priority-v1"
    support_cap: int = 20
    area_gain_cap: int = 5
    first_observed_recency_power: int = 2
    score_is_probability: bool = False
    formula: str = ""  # Documentary string is not executable code.

    def validate(self):
        if (self.version != "observed-emergence-priority-v1" or self.score_is_probability is not False
                or any(type(value) is not int or value < 1 for value in
                       (self.support_cap, self.area_gain_cap, self.first_observed_recency_power))):
            raise ValueError("Invalid observed-emergence ranking policy")


def rerank_observed_candidates(result: dict, policy: EmergencePriorityPolicy | None = None) -> dict:
    """Preserve every proposal, composition and unknown verification flag.

    Requires the COMPLETE candidate set, not a reranking of a truncated top-200.
    Recent appearance is left-censored corpus evidence, never real-world novelty.
    """
    policy = policy or EmergencePriorityPolicy()
    policy.validate()
    rows, windows = result["rows"], result["windows"]
    if not windows or len(rows) != result["candidate_compositions"]:
        raise ValueError("Complete proposals and observation windows required")
    new_rows = []
    for row in rows:
        recent, prior = row["recent_share"], row["prior_share"]
        if (not all(math.isfinite(value) for value in (recent, prior))
                or not 0 <= prior < recent <= 1
                or type(row["recent_works"]) is not int or row["recent_works"] < 1
                or type(row["active_area_gain"]) is not int or row["active_area_gain"] < 1
                or row["first_observed_window"] not in windows):
            raise ValueError("Invalid observed candidate metrics")
        components = {
            "relative_share_gain": (recent - prior) / recent,
            "capped_support": min(row["recent_works"], policy.support_cap),
            "capped_active_area_gain": min(row["active_area_gain"], policy.area_gain_cap),
            "first_observed_recency_factor": ((windows.index(row["first_observed_window"]) + 1)
                                              / len(windows)) ** policy.first_observed_recency_power}
        score = (components["relative_share_gain"] * math.log1p(components["capped_support"])
                 * math.log1p(components["capped_active_area_gain"])
                 * components["first_observed_recency_factor"])
        new_rows.append(dict(row, observed_emergence_priority_not_probability=round(score, 12),
                             emergence_priority_components=components))
    new_rows.sort(key=lambda row: (-row["observed_emergence_priority_not_probability"], row["phrase"]))
    output = dict(result, rows=new_rows, ranking_policy=asdict(policy))
    output["limitations"] = list(result.get("limitations", [])) + [
        "Observed emergence priority is a heuristic, not calibrated accuracy or real-world novelty.",
        "Late first observation can reflect incomplete earlier coverage; rare noise may be promoted."]
    return output
