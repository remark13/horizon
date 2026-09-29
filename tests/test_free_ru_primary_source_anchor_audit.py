import json
from pathlib import Path


def test_developer_anchors_match_frozen_semantic_pages_without_gold_claims():
    root = Path(__file__).resolve().parents[1]
    audit = json.loads((root / "evaluation/free-ru-primary-source-anchor-audit-2026-09-27-v1.json")
                       .read_text(encoding="utf-8"))
    assert audit["reviewer_kind"] == "developer"
    assert audit["not_independent_gold"] is True
    anchors = audit["anchors"]
    assert len(anchors) == 5
    assert len({row["doi"] for row in anchors}) == len(anchors)
    source_rows = {}
    for path in audit["source_first_pages"]:
        report = json.loads((root / path).read_text(encoding="utf-8"))
        for row in report["rows"]:
            if row["mode"] == "semantic":
                source_rows[row["case_id"]] = row["results"]
    for anchor in anchors:
        matching = [row for row in source_rows[anchor["case_id"]]
                    if row["openalex_id"] == anchor["openalex_id"]]
        if anchor["frozen_semantic_rank"] is None:
            assert matching == []
        else:
            assert len(matching) == 1
            assert matching[0]["rank_in_first_page"] == anchor["frozen_semantic_rank"]
            assert matching[0]["doi"] == "https://doi.org/" + anchor["doi"]
