import pytest

from scripts.assess_concept_line_evidence import assess


def _inputs():
    inventory = {
        "version": "concept-line-evidence-card-v1", "line_id": "x", "title_ru": "X",
        "all_exact_matches": 1,
        "works": [{"arxiv_id": "1234.56789", "url": "https://arxiv.org/abs/1234.56789",
                   "title": "Title", "first_submission_date": "2025-01-02",
                   "in_background": True}],
        "annual": [{"from": "2024-09-01", "all_exact_matches": 1,
                    "background_unique_arxiv_ids": 10}],
    }
    review = {"version": "concept-line-developer-review-v1",
              "inventory_sha256": "hash", "reviewer_role": "developer",
              "independent_expert_review": False,
              "rows": [{"arxiv_id": "1234.56789", "role": "direct_technical_claim",
                        "reason": "Abstract states a new method"}]}
    return inventory, review


def test_assessment_reconciles_one_direct_work() -> None:
    inventory, review = _inputs()
    result = assess(inventory, review, "hash")
    assert result["developer_role_counts"] == {"direct_technical_claim": 1}
    assert result["annual"][0]["developer_direct_per_10000_background"] == 1000
    assert result["weak_signal_confirmed"] is False


def test_assessment_rejects_missing_or_stale_review() -> None:
    inventory, review = _inputs()
    review["rows"] = []
    with pytest.raises(ValueError):
        assess(inventory, review, "hash")
    review["rows"] = [{"arxiv_id": "1234.56789", "role": "direct_technical_claim",
                       "reason": "Abstract states a new method"}]
    with pytest.raises(ValueError):
        assess(inventory, review, "different")
