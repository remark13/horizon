from datetime import date, datetime, timezone

import pytest

from saia.openalex_cache_search import scan


def test_cache_expanded_pilot_limit_is_bounded():
    spec = [{"branch_id": "robotics", "included_phrases": ["robotic manipulation"]}]
    assert scan(spec, date(2024, 1, 1), date(2025, 1, 1), 100, rows=[])[
        "branches"][0]["works"] == []
    with pytest.raises(ValueError, match="лимит"):
        scan(spec, date(2024, 1, 1), date(2025, 1, 1), 101, rows=[])


def test_cache_spreads_selection_over_years_without_claiming_coverage():
    rows = [
        (index + 1, f"W{year}{index}", "Vertical federated learning", "A method",
         date(year, 3, index + 1), None, datetime(2026, 9, 1, tzinfo=timezone.utc))
        for year in (2022, 2023, 2024) for index in range(4)
    ]
    specs = [{"branch_id": "federated", "included_phrases": ["federated learning"],
              "excluded_phrases": []}]
    first = scan(specs, date(2022, 1, 1), date(2025, 1, 1), 6, rows=rows)
    second = scan(specs, date(2022, 1, 1), date(2025, 1, 1), 6, rows=list(reversed(rows)))
    branch = first["branches"][0]
    assert branch["eligible_matches"] == 12
    assert branch["selected_year_counts"] == {"2022": 2, "2023": 2, "2024": 2}
    assert [work["source_ids"] for work in branch["works"]] == [
        work["source_ids"] for work in second["branches"][0]["works"]]
    assert first["coverage_comparable"] is None


def test_cache_respects_exact_phrase_and_exclusion():
    rows = [
        (1, "W1", "Federated-learning for robots", "Study", date(2024, 1, 1),
         "10.1000/test", datetime(2026, 9, 1, tzinfo=timezone.utc)),
        (2, "W2", "Federated learning survey", "Review", date(2024, 1, 2),
         None, datetime(2026, 9, 1, tzinfo=timezone.utc)),
    ]
    specs = [{"branch_id": "federated", "included_phrases": ["federated learning"],
              "excluded_phrases": ["survey"],
              "matching_version": "orthographic-separators-v1"}]
    branch = scan(specs, date(2024, 1, 1), date(2025, 1, 1), 10,
                  rows=rows)["branches"][0]
    assert branch["eligible_matches"] == 1
    assert branch["works"][0]["canonical_key"] == "doi:10.1000/test"


def test_cache_compound_group_semantics_match_local_arxiv():
    stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [
        (1, "W1", "Neuromorphic chips", "Inference on edge devices", date(2024, 1, 1), None, stamp),
        (2, "W2", "Neuromorphic chips", "Cloud deployment", date(2024, 1, 2), None, stamp),
        (3, "W3", "Edge devices", "Conventional processors", date(2024, 1, 3), None, stamp),
        (4, "W4", "Узкая русская тема", "Original literal path", date(2024, 1, 4), None, stamp),
        (5, "W5", "Neuromorphic chips survey", "Edge devices", date(2024, 1, 5), None, stamp),
    ]
    spec = {"branch_id": "original-query", "included_phrases": ["узкая русская тема"],
            "concept_groups": [["neuromorphic chips"], ["edge devices"]],
            "excluded_phrases": ["survey"]}
    result = scan([spec], date(2024, 1, 1), date(2025, 1, 1), 10, rows=rows)
    branch = result["branches"][0]
    assert branch["eligible_matches"] == 2
    assert {work["source_ids"][0] for work in branch["works"]} == {
        "https://openalex.org/W1", "https://openalex.org/W4"}
