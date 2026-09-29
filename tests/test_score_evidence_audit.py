import copy

import pytest

from saia.score_evidence_audit import build_packet


def packet():
    return {
        "mission_id": "m",
        "score_run_id": 9,
        "cards": [
            {"candidate_id": 1, "label": "one", "status": "watch"},
            {"candidate_id": 2, "label": "two", "status": "candidate"},
        ],
    }


def rows():
    return [
        {"candidate_id": 1, "work_id": 11, "title": "A", "abstract": "x",
         "effective_date": "2025-01-01", "publication_date": "2025-01-01",
         "type": "article", "language": "en", "identifiers": [], "text_sha256": "x"},
        {"candidate_id": 2, "work_id": 22, "title": "B", "abstract": "y",
         "effective_date": "2025-02-01", "publication_date": "2025-02-01",
         "type": "article", "language": "en", "identifiers": [], "text_sha256": "y"},
    ]


def test_build_packet_keeps_manual_review_empty_and_inputs_unchanged():
    cards, evidence = packet(), rows()
    before = copy.deepcopy((cards, evidence))
    result = build_packet(cards, [1, 2], evidence)
    assert (cards, evidence) == before
    assert result["case_count"] == 2 and result["unique_works"] == 2
    assert result["scientific_accuracy_evaluated"] is False
    assert result["cases"][0]["composition_review"]["single_coherent_technology"] is None


def test_candidate_must_belong_to_score_packet():
    with pytest.raises(ValueError, match="не принадлежит"):
        build_packet(packet(), [3], rows())


def test_duplicate_or_missing_composition_is_rejected():
    duplicate = rows() + [rows()[0]]
    with pytest.raises(ValueError, match="повторённую"):
        build_packet(packet(), [1, 2], duplicate)
    with pytest.raises(ValueError, match="не найден состав"):
        build_packet(packet(), [1, 2], rows()[:1])
