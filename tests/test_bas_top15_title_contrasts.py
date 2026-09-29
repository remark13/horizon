import pytest

from scripts.audit_bas_top15_title_contrasts import assess


def _review():
    return {
        "version": "bas-top15-title-contrast-review-v1",
        "annotation_role": "single_developer_title_diagnostic_not_expert_gold",
        "question": "one narrow line?", "limitations": [],
        "decisions": [{"rank": 1, "candidate_id": 11,
                       "decision": "visible_mixture",
                       "contrast_work_ids": [101, 102], "reason": "different tasks"}],
    }


def _source():
    return {"cards": [{"rank": 1, "candidate_id": 11, "label": "Broad",
                       "member_count": 2,
                       "screening": {"state": "growth_observed",
                                     "scope_warning": "too broad"},
                       "works": [{"work_id": 101, "title": "One",
                                  "identifiers": [{"kind": "arxiv", "value": "2601.00001"}]},
                                 {"work_id": 102, "title": "Two",
                                  "identifiers": [{"kind": "openalex", "value": "W2"}]}]}]}


def test_title_contrast_audit_preserves_evidence_and_does_not_claim_truth():
    report = assess(_review(), _source())
    assert report["counts"] == {"visible_mixture": 1}
    assert report["visible_mixtures_with_existing_scope_warning"] == 1
    assert report["rows"][0]["contrasting_works"][0]["source_url"] == \
        "https://arxiv.org/abs/2601.00001"
    assert report["scope_warning_is_a_label_only_diagnostic_not_a_coherence_test"] is True


def test_title_contrast_audit_rejects_stale_or_foreign_work():
    review = _review()
    review["decisions"][0]["contrast_work_ids"] = [101, 999]
    with pytest.raises(ValueError, match="outside its card"):
        assess(review, _source())
    review = _review()
    review["decisions"][0]["candidate_id"] = 12
    with pytest.raises(ValueError, match="aligned"):
        assess(review, _source())
