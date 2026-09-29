import sqlite3

import pytest

from scripts.build_bas_own_claim_index import build, extract, search


def _work(identifier, title, abstract):
    return {"arxiv_id": identifier, "first_submission_date": "2025-01-01",
            "title": title, "abstract": abstract}


def test_claim_extraction_preserves_sentence_and_distinguishes_review_hint():
    row = _work("1", "A Survey of UAV Task Allocation",
                "Task allocation is important. We present a survey of the literature. ")
    claims, reason = extract(row)
    assert reason == "claim_cues_found"
    assert claims[0]["span_id"] == "A2"
    assert claims[0]["text"] == "We present a survey of the literature."
    assert claims[0]["format_hint"] == "review_or_synthesis"


def test_bounded_index_stores_each_work_once_and_searches_claims(tmp_path):
    rows = [_work("1", "UAV mission", "Task allocation is a challenge. We propose a visual-inertial navigation method."),
            _work("2", "Wireless survey", "UAVs use visual-inertial navigation. This article reports a survey of spectrum sharing.")]
    path = tmp_path / "claims.sqlite3"
    result = build(rows, path, max_claims=10)
    assert result["parent_works"] == 2
    assert result["claim_sentences"] >= 1
    hits = search(path, "visual inertial")
    assert {hit["arxiv_id"] for hit in hits} == {"1"}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM works").fetchone()[0] == 2
    with pytest.raises(FileExistsError):
        build(rows, path, max_claims=10)


def test_duplicate_parent_id_is_rejected_before_write(tmp_path):
    row = _work("same", "Title", "We propose a method.")
    with pytest.raises(ValueError, match="duplicated"):
        build([row, row], tmp_path / "claims.sqlite3", max_claims=10)
