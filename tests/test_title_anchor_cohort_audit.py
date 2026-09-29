from scripts.audit_title_anchor_cohorts import audit, display_groups


def _row(phrase, ids):
    return {"phrase_en": phrase, "expanded_ids": ids,
            "expanded_unique_arxiv_ids": len(ids)}


def test_display_deduplicates_nested_high_overlap_without_transitive_chaining():
    rows = [_row("altitude wireless", ["1", "2", "3", "4", "5"]),
            _row("low altitude wireless", ["1", "2", "3", "4", "5"]),
            _row("wireless networks", ["1", "2", "3", "4", "5"]),
            _row("low altitude wireless networks", ["1", "2", "3", "4", "5"])]
    groups = display_groups(rows)
    assert len(groups) == 2
    assert groups[0]["member_phrases"] == [
        "altitude wireless", "low altitude wireless", "low altitude wireless networks"]
    assert groups[1]["member_phrases"] == ["wireless networks"]
    assert [row["display_group_rank"] for row in rows] == [1, 1, 2, 1]


def test_title_evidence_is_not_mistaken_for_paper_relevance_or_primary_result():
    expansion = {"version": "title-anchor-expansion-pilot-v1",
                 "complete_candidate_scan": True, "production_changed": False,
                 "index_manifest_sha256": "fixture",
                 "rows": [_row("task allocation", ["1", "2"])]}
    works = {"1": {"title": "Task-Allocation in UAV Swarms",
                   "first_submission_date": "2025-01-01"},
             "2": {"title": "Wireless Spectrum Sharing in UAV Networks",
                   "first_submission_date": "2022-01-01"}}
    result = audit(expansion, works, expansion_sha256="fixture")
    assert result["rows"][0]["title_anchored_ids"] == ["1"]
    assert result["rows"][0]["abstract_only_count"] == 1
    assert result["rows"][0]["title_recent_two_year_count"] == 1
    assert result["rows"][0]["title_evidence_is_primary_result"] is None
    assert result["paper_relevance_measured"] is False
