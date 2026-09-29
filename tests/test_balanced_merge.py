import pytest

from saia.balanced_merge import merge


def item(key: str, title: str) -> dict:
    return {"canonical_key": key, "title": title}


def test_large_branch_cannot_displace_small_branch_and_sources_rotate():
    result = merge([
        {"branch_id": "large", "sources": {
            "openalex": [item(f"oa-{index}", f"OA {index}") for index in range(10)],
            "arxiv": [item(f"ax-{index}", f"AX {index}") for index in range(10)],
        }},
        {"branch_id": "small", "sources": {
            "openalex": [item("small-1", "Small 1"), item("small-2", "Small 2")],
        }},
    ], max_results=4)
    assert result["branch_contributions"] == {"large": 2, "small": 2}
    assert result["source_contributions"] == {"openalex": 3, "arxiv": 1}
    assert result["complete_nonempty_branch_coverage"] is True
    assert result["scientific_score_calculated"] is False


def test_duplicate_merges_provenance_without_consuming_a_second_slot():
    result = merge([
        {"branch_id": "a", "sources": {"openalex": [item("same", "One")] }},
        {"branch_id": "b", "sources": {"arxiv": [
            item("same", "One duplicate"), item("other", "Other"),
        ]}},
    ], max_results=2)
    assert [row["canonical_key"] for row in result["results"]] == ["same", "other"]
    assert result["results"][0]["branch_provenance"] == ["a", "b"]
    assert result["results"][0]["source_provenance"] == ["arxiv", "openalex"]


def test_duplicate_merges_retrieval_origins_without_calling_them_sources():
    live = {**item("same", "One"), "retrieval_origins": ["live_openalex_api"]}
    pinned = {**item("same", "One"), "retrieval_origins": [
        "pinned_query_specific_openalex_cohort"]}
    result = merge([{"branch_id": "a", "sources": {"openalex": [live, pinned]}}],
                   max_results=2)
    assert len(result["results"]) == 1
    assert result["results"][0]["retrieval_origins"] == [
        "live_openalex_api", "pinned_query_specific_openalex_cohort"]


def test_arxiv_first_duplicate_keeps_later_openalex_record_type():
    arxiv = {**item("same", "One"), "openalex_type": None}
    openalex = {**item("same", "One"), "openalex_type": "conference-abstract"}
    result = merge([
        {"branch_id": "a", "sources": {"arxiv": [arxiv]}},
        {"branch_id": "b", "sources": {"openalex": [openalex]}},
    ])
    assert len(result["results"]) == 1
    assert result["results"][0]["openalex_type"] == "conference-abstract"


def test_duplicate_only_branch_counts_as_represented_by_merged_result():
    result = merge([
        {"branch_id": "one", "sources": {"openalex": [
            {**item("doi:10.1000/same", "Shared research"),
             "doi": "10.1000/same", "authors": ["Ada Smith"],
             "published_at": "2024-01-01"}]}},
        {"branch_id": "two", "sources": {"arxiv": [
            {**item("title:shared research", "Shared research"),
             "authors": ["Ada Smith"], "published_at": "2024-01-02"}]}},
    ], max_results=15)
    assert len(result["results"]) == 1
    assert result["branch_contributions"] == {"one": 1, "two": 0}
    assert result["results"][0]["branch_provenance"] == ["one", "two"]
    assert result["represented_branches"] == ["one", "two"]
    assert result["complete_nonempty_branch_coverage"] is True
    assert result["warnings"] == []


def test_limit_smaller_than_nonempty_branches_is_visible_not_silent():
    branches = [
        {"branch_id": f"b{index}", "sources": {"openalex": [item(f"k{index}", "x")]}}
        for index in range(3)
    ]
    result = merge(branches, max_results=2)
    assert result["complete_nonempty_branch_coverage"] is False
    assert result["warnings"]


def test_invalid_candidate_without_identity_is_rejected():
    with pytest.raises(ValueError, match="canonical_key"):
        merge([{"branch_id": "a", "sources": {"arxiv": [{"title": "x"}]}}])
