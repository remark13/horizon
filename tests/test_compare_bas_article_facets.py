import pytest

from scripts.compare_bas_article_facets import score


def test_score_counts_false_core_and_warning_cost():
    config = {"work_ids": [1, 2],
              "pre_run_developer_core_uav_labels": {"1": False, "2": True}}
    baseline = {"model_digest": "frozen", "rows": [
        {"work_id": 1, "status": "valid_source_ids", "seconds": 1,
         "facets": {"uav_role": "core_uav_research"}},
        {"work_id": 2, "status": "valid_source_ids", "seconds": 1,
         "facets": {"uav_role": "core_uav_research"}},
    ]}
    strict = {"model_digest": "frozen", "rows": [
        {"work_id": 1, "status": "valid_source_ids", "seconds": 2,
         "facets": {"uav_role": "core_uav_research",
                    "core_without_uav_in_own_claim_warning": True}},
        {"work_id": 2, "status": "valid_source_ids", "seconds": 2,
         "facets": {"uav_role": "core_uav_research",
                    "core_without_uav_in_own_claim_warning": True}},
    ]}
    result = score(config, baseline, strict)
    assert result["baseline"]["false_core"] == 1
    assert result["own_claim_warning_as_abstention"]["abstentions"] == 2
    assert result["own_claim_warning_as_abstention"]["true_core_abstained"] == 1
    strict["model_digest"] = "changed"
    with pytest.raises(ValueError, match="Different model"):
        score(config, baseline, strict)
