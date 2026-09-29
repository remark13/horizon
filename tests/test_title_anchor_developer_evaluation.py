import pytest

from scripts.evaluate_title_anchor_developer_review import evaluate


def _fixture():
    blind = {"version": "title-anchor-paper-review-v2", "case_count": 1,
             "cases": [{"case_id": "c1", "proposed_line_en": "task allocation",
                        "paper": {"arxiv_id": "1234.56789",
                                  "title": "Multi-UAV Task Allocation",
                                  "abstract": "We propose a new method."}}]}
    manifest = {"version": "title-anchor-paper-review-v2-selection-manifest",
                "rows": [{"case_id": "c1", "phrase_en": "task allocation",
                          "arxiv_id": "1234.56789", "selection_stratum": "title",
                          "selection_role": "top_display_group"}]}
    review = {"version": "title-anchor-developer-review-v1",
              "blind_packet_sha256": "b", "independent_expert_review": False,
              "line_review": {"task allocation": {"specific_technology": "unclear"}},
              "cases": [{"case_id": "c1", "own_result": "yes", "bas_core": "yes",
                         "role": "primary_result", "quote": "We propose a new method.",
                         "reason": "Own result"}]}
    return blind, manifest, review


def test_developer_review_reconciles_quotes_and_denies_precision_claim():
    blind, manifest, review = _fixture()
    result = evaluate(blind, manifest, review, blind_sha256="b",
                      manifest_sha256="m", review_sha256="r")
    assert result["own_result_by_selection_stratum"] == {"title": {"yes": 1}}
    assert result["population_precision_estimated"] is False
    assert result["weak_signal_accuracy_measured"] is False


def test_developer_review_rejects_unsupported_quote():
    blind, manifest, review = _fixture()
    review["cases"][0]["quote"] = "Invented evidence"
    with pytest.raises(ValueError, match="Unsupported quote"):
        evaluate(blind, manifest, review, blind_sha256="b",
                 manifest_sha256="m", review_sha256="r")
