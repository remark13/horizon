import json

import pytest

from saia.priority_relevance_semantic import _cosine, _english_drafts, _pairwise


def test_pairwise_compares_only_within_topic():
    rows = [
        {"target_topic": "A", "item_id": "y", "label": "yes", "cosine": .9},
        {"target_topic": "A", "item_id": "p", "label": "partial", "cosine": .5},
        {"target_topic": "A", "item_id": "n", "label": "no", "cosine": .1},
        {"target_topic": "B", "item_id": "n2", "label": "no", "cosine": .99},
    ]
    counts = _pairwise(rows)["counts"]
    assert counts == {"partial_over_no_correct": 1, "partial_over_no_pairs": 1,
                      "yes_over_no_correct": 1, "yes_over_no_pairs": 1,
                      "yes_over_partial_correct": 1, "yes_over_partial_pairs": 1}


def test_pairwise_records_inversion_without_claiming_calibration():
    result = _pairwise([
        {"target_topic": "A", "item_id": "relevant", "label": "yes", "cosine": .2},
        {"target_topic": "A", "item_id": "noise", "label": "no", "cosine": .7},
    ])
    assert result["counts"] == {"yes_over_no_pairs": 1}
    assert result["sample_inversions"][0]["better_item_id"] == "relevant"
    assert result["no_threshold_or_calibration"] is True


def test_cosine_rejects_invalid_vectors():
    assert _cosine([1, 0], [1, 0]) == 1
    with pytest.raises(ValueError):
        _cosine([1], [1, 0])
    with pytest.raises(ValueError):
        _cosine([0, 0], [1, 0])


def test_english_draft_map_is_explicit_and_rejects_ambiguous_title(tmp_path):
    catalog = {"national_search_areas": [{"title_ru": "A",
                                           "query_draft": {"en_terms": ["alpha", "beta"]}}],
               "customer_examples": [{"title_original": "B",
                                      "query_draft": {"en_terms": ["gamma"]}}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    assert _english_drafts(path) == {"A": "alpha; beta", "B": "gamma"}
    catalog["customer_examples"][0]["title_original"] = "A"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ValueError):
        _english_drafts(path)
