import pytest

from scripts.compare_title_anchor_paper_llm import compare


def _inputs():
    probe = {"version": "title-anchor-paper-llm-probe-v1", "complete": True,
             "blind_packet_sha256": "blind", "rows": [
                 {"case_id": "c", "status": "valid_grounded",
                  "proposed_line_en": "example line", "prediction": {"own_result": "yes"}}]}
    review = {"version": "title-anchor-developer-review-v1",
              "blind_packet_sha256": "blind", "independent_expert_review": False,
              "cases": [{"case_id": "c", "own_result": "no"}]}
    return probe, review


def test_comparison_keeps_false_promotion_visible_and_rejects_probe():
    probe, review = _inputs()
    result = compare(probe, review, probe_sha="p", review_sha="r")
    assert result["by_developer_label"] == {"no": {"yes": 1}}
    assert result["developer_negative_model_positive_cases"][0]["case_id"] == "c"
    assert result["production_recommendation"] == "reject_current_model_probe"
    assert result["independent_accuracy_measured"] is False


def test_comparison_rejects_missing_case():
    probe, review = _inputs()
    review["cases"] = []
    with pytest.raises(ValueError, match="Missing or repeated"):
        compare(probe, review, probe_sha="p", review_sha="r")
