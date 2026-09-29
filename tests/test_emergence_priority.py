from copy import deepcopy
from dataclasses import replace

import pytest

from saia.emergence_priority import EmergencePriorityPolicy, rerank_observed_candidates


def proposal(name="specific method", *, recent=3, gain=3, first="2016", prior_share=0):
    return {"phrase": name, "recent_works": recent, "active_area_gain": gain,
            "first_observed_window": first, "recent_share": 0.001, "prior_share": prior_share,
            "publication_composition_sha256": name, "evidence": [{"work_id": name}],
            "weak_signal_verified": None, "primary_result_verified": None,
            "review_priority_not_probability": 0.001}


def result(*rows):
    return {"rows": list(rows), "candidate_compositions": len(rows),
            "windows": [str(year) for year in range(2010, 2017)], "limitations": []}


def test_reranking_keeps_compositions_and_verification_unknown_without_mutating_input():
    original = result(proposal(), proposal("large old phrase", recent=200, gain=20, first="2010"))
    snapshot = deepcopy(original)
    ranked = rerank_observed_candidates(original)
    assert original == snapshot
    assert ranked["rows"][0]["phrase"] == "specific method"
    assert {row["publication_composition_sha256"] for row in ranked["rows"]} == {"specific method", "large old phrase"}
    assert all(row["weak_signal_verified"] is None for row in ranked["rows"])


def test_support_and_area_gain_are_capped_before_ranking():
    ranked = rerank_observed_candidates(result(proposal("a", recent=20, gain=5),
                                               proposal("b", recent=1000, gain=1000)))
    assert ranked["rows"][0]["observed_emergence_priority_not_probability"] == ranked["rows"][1]["observed_emergence_priority_not_probability"]


def test_growth_is_relative_not_absolute_volume_and_first_observed_is_explicit():
    ranked = rerank_observed_candidates(result(proposal("new"), proposal("nearly flat", prior_share=0.0009)))
    assert ranked["rows"][0]["phrase"] == "new"
    assert ranked["rows"][0]["emergence_priority_components"]["first_observed_recency_factor"] == 1
    assert "not calibrated" in ranked["limitations"][0]


def test_order_is_stable_and_all_proposals_are_required():
    a, b = proposal("a"), proposal("b")
    assert rerank_observed_candidates(result(a, b)) == rerank_observed_candidates(result(b, a))
    truncated = result(a)
    truncated["candidate_compositions"] = 2
    with pytest.raises(ValueError, match="Complete proposals"):
        rerank_observed_candidates(truncated)


@pytest.mark.parametrize("patch", [{"recent_share": float("nan")}, {"prior_share": 0.1},
                                  {"active_area_gain": 0}, {"first_observed_window": "2025"}])
def test_invalid_metrics_fail(patch):
    row = proposal()
    row.update(patch)
    with pytest.raises(ValueError, match="Invalid observed"):
        rerank_observed_candidates(result(row))


def test_invalid_policy_cannot_claim_probability():
    with pytest.raises(ValueError, match="ranking policy"):
        rerank_observed_candidates(result(), replace(EmergencePriorityPolicy(), score_is_probability=True))

