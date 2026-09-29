import json

import pytest

from saia.bounded_discovery_package import build_package
from saia.ingest import validate_collection_input, openalex_records, arxiv_records


def fixture():
    profile = {
        "mission_id": "bounded-ai-test", "query_version": "bounded-ai-test/v1",
        "title": "Broad AI", "as_of_date": "2026-09-01",
        "period": {"from": "2021-10-01", "to": "2026-08-31"},
        "sources": ["arxiv", "openalex"],
        "query": {"terms": ["language model"], "exclusions": []},
        "collection_profile": {
            "decision": "bounded_balanced_openalex_arxiv_candidates",
            "provenance": {"balanced_discovery_job_id": "source-job",
                           "balanced_result_sha256": "a" * 64},
        },
    }
    job = {
        "job_id": "source-job", "job_kind": "approved_balanced_discovery",
        "status": "succeeded", "result_sha256": "a" * 64,
        "result": {"works": [
            {"title": "A new language model", "abstract": "new language model",
             "published_at": "2024-03-10", "sources": ["openalex"],
             "openalex_type": "conference-abstract",
             "source_provenance": ["openalex"],
             "source_ids": ["https://openalex.org/W123"],
             "doi": "10.1000/example", "authors": ["Ada Researcher"]},
            {"title": "Robot learning", "abstract": "robot learning",
             "published_at": "2025-04-10", "sources": ["arxiv"],
             "source_provenance": ["arxiv"],
             "source_ids": ["https://arxiv.org/abs/2504.01234"],
             "doi": None, "authors": ["Bob Researcher"]},
        ], "errors": {"openalex": "upstream rate limit"}},
    }
    return profile, job


def test_bounded_package_preserves_both_sources_without_fake_metrics(tmp_path):
    profile, job = fixture()
    result = build_package(profile, tmp_path / "package", source_job=job)
    package = tmp_path / "package"
    manifest = result["manifest"]
    text = (package / "mission.json").read_text(encoding="utf-8")
    validate_collection_input(package, manifest, json.loads(text), text)
    assert result["audit"]["source_records"] == {"openalex": 1, "arxiv": 1}
    assert result["audit"]["source_year_counts"] == {
        "openalex": {"2024": 1}, "arxiv": {"2025": 1}}
    assert result["audit"]["coverage_comparable"] is None
    assert manifest["upstream_source_errors"] == {"openalex": "upstream rate limit"}
    openalex = list(openalex_records(package / "openalex" / "bounded.json"))[0][1]
    assert openalex["cited_by_count"] is None
    assert openalex["abstract_inverted_index"]["language"] == [1]
    assert openalex["type"] == "conference-abstract"
    arxiv = list(arxiv_records(package / "arxiv" / "bounded.xml"))[0][1]
    assert arxiv["title"] == "Robot learning"


def test_bounded_package_rejects_changed_source_job(tmp_path):
    profile, job = fixture()
    job["result_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="not bound"):
        build_package(profile, tmp_path / "package", source_job=job)


def test_bounded_package_excludes_original_query_preview_only(tmp_path):
    profile, job = fixture()
    profile["controlled_search_plan"] = {"original_query_used_for_preview_only": True}
    profile["sources"] = ["arxiv"]
    job["result"]["works"][0]["branch_provenance"] = ["original-query"]
    job["result"]["works"][1]["branch_provenance"] = ["artificial-intelligence/ai-agents"]
    result = build_package(profile, tmp_path / "package", source_job=job)
    assert result["audit"]["omitted_original_only_works"] == 1
    assert result["audit"]["source_records"] == {"openalex": 0, "arxiv": 1}


def test_cached_openalex_is_not_marked_independent_discovery(tmp_path):
    profile, job = fixture()
    job["result"]["source_modes"] = {
        "openalex": "previously_ingested_local_cache_with_live_fallback",
        "arxiv": "pinned_local_metadata_snapshot",
    }
    manifest = build_package(profile, tmp_path / "package", source_job=job)["manifest"]
    assert manifest["sources"]["openalex"]["independent_discovery"] is False
    assert manifest["sources"]["arxiv"]["independent_discovery"] is True


def test_compound_bounded_package_can_contain_only_arxiv(tmp_path):
    profile, job = fixture()
    profile["collection_profile"]["decision"] = "bounded_compound_concept_candidates"
    profile["sources"] = ["arxiv"]
    job["result"]["works"] = job["result"]["works"][1:]
    result = build_package(profile, tmp_path / "package", source_job=job)
    assert result["audit"]["source_records"] == {"openalex": 0, "arxiv": 1}
    assert result["manifest"]["sources"].keys() == {"arxiv"}
