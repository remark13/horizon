import copy

import pytest

from saia.ru_concept_cross_domain_eval import evaluate


def fixtures():
    gate = {
        "version": "test-gate", "source_cases_sha256": "cases",
        "arxiv_index_manifest_sha256": "index", "model": "local-model",
        "source_period": {"date_from": "2021-01-01", "as_of_date_exclusive": "2026-01-01"},
        "case_count": 1,
        "decision_gate_for_optional_product_suggestion": {
            "parsed_minimum": 1, "source_span_exact_minimum": 1,
            "structurally_index_executable_minimum": 1,
            "negative_controls_with_nonzero_arxiv_matches_maximum": 0,
            "critical_semantic_errors_maximum": 0,
        },
    }
    proposals = {"cases_sha256": "cases", "model": "local-model",
                 "model_digest": "model-digest", "rows": [{
                     "case_id": "one", "role": "national_search_area",
                     "status": "parsed", "seconds": 2.0,
                     "structural_validation": {"source_span_check_passed": True, "issues": []},
                 }]}
    period = {"from": "2021-01-01", "as_of_exclusive": "2026-01-01"}
    probe = {"index_manifest_sha256": "index", "proposals_sha256": "proposals",
             "period": period, "rows": [{
                 "case_id": "one", "status": "exact_index_probe_complete",
                 "arxiv_ids": 1, "matched_arxiv_ids": ["work-1"],
             }]}
    baseline = {"config_sha256": "cases", "index_manifest_sha256": "index",
                "period": period, "rows": [{
                    "case_id": "one", "status": "exact_index_probe_complete",
                    "arxiv_ids": ["work-1", "work-2"],
                }]}
    review = {"proposal_sha256": "proposals", "independent_expert_review": False,
              "rows": [{"case_id": "one", "judgement": "preserved"}]}
    return gate, proposals, probe, baseline, review


def run(values):
    gate, proposals, probe, baseline, review = values
    return evaluate(gate=gate, proposals=proposals, probe=probe,
                    baseline=baseline, review=review,
                    proposals_sha256="proposals", probe_sha256="probe")


def test_literal_overlap_is_not_mislabeled_accuracy_and_gate_is_explicit():
    report = run(fixtures())
    assert report["optional_product_suggestion_gate_passed"] is True
    assert report["counts"]["baseline_match_assignments"] == 2
    assert report["counts"]["shared_match_assignments"] == 1
    assert "not relevant-work recall" in report["comparison_scope"]
    assert report["independent_expert_labels"] is False


def test_critical_semantic_loss_blocks_product_gate():
    values = list(fixtures())
    values[4] = copy.deepcopy(values[4])
    values[4]["rows"][0]["judgement"] = "critical_loss"
    report = run(values)
    assert report["optional_product_suggestion_gate_passed"] is False
    assert report["gate_checks"]["critical_semantics"] is False


def test_different_period_or_unfrozen_id_set_is_rejected():
    values = list(fixtures())
    values[2] = copy.deepcopy(values[2])
    values[2]["period"]["from"] = "2022-01-01"
    with pytest.raises(ValueError, match="Frozen inputs"):
        run(values)
    values = list(fixtures())
    values[2] = copy.deepcopy(values[2])
    values[2]["rows"][0]["matched_arxiv_ids"] = []
    with pytest.raises(ValueError, match="incomplete"):
        run(values)
