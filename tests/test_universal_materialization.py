from __future__ import annotations

import pytest

from saia.universal_materialization import build_candidate_profile, build_profile
from saia.arxiv_metadata import LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION


def inputs():
    job = {
        "job_id": "00000000-0000-0000-0000-000000000131",
        "approved_query_plan_id": "00000000-0000-0000-0000-000000000132",
        "job_kind": "approved_balanced_discovery", "status": "succeeded",
        "result_sha256": "b" * 64,
        "payload": {
            "date_from": "2020-01-01", "as_of_date": "2026-01-01",
            "compiled_query_plan_id": "00000000-0000-0000-0000-000000000133",
            "compiled_plan_payload_sha256": "c" * 64,
            "branches": [
                {"branch_id": "original-query", "query": "тканевая инженерия"},
                {"branch_id": "tissue", "query": "tissue engineering"},
            ],
            "compiled_branch_specs": [
                {"branch_id": "original-query", "included_phrases": ["тканевая инженерия"],
                 "excluded_phrases": []},
                {"branch_id": "tissue", "included_phrases": ["tissue engineering", "bioprinting"],
                 "excluded_phrases": ["review article"]},
            ],
        },
    }
    adjudication = {
        "adjudication_id": "00000000-0000-0000-0000-000000000134",
        "adjudication": {
            "source_job_id": job["job_id"], "complete": True,
            "precision_available": True, "weak_signal_accuracy_measured": False,
            "adjudication_payload_sha256": "d" * 64,
            "precision_scope": "returned_work_branch_assignments_in_one_immutable_job",
            "metrics": {"precision_on_returned_assignments": 0.8},
        },
    }
    return job, adjudication


def test_adjudicated_compiled_query_becomes_explicit_arxiv_profile():
    job, adjudication = inputs()
    mission, profile = build_profile(
        job, adjudication, "analyst",
        acknowledge_arxiv_only=True, acknowledge_phrase_union=True,
    )
    assert mission["sources"] == ["arxiv"]
    assert profile["sources"] == ["arxiv"]
    assert profile["expansion_source"] == "controlled_source_profile"
    assert profile["controlled_search_plan"]["included_terms"] == [
        "tissue engineering", "bioprinting",
    ]
    assert profile["controlled_search_plan"]["exclusions"] == ["review article"]
    assert profile["controlled_search_plan"]["matching_version"] == LITERAL_MATCHING_VERSION
    assert profile["controlled_search_plan"]["relation"] == (
        "explicit_union_of_adjudicated_selected_branches_original_preview_only"
    )
    assert profile["controlled_search_plan"]["original_query_used_for_preview_only"] is True
    assert profile["controlled_search_plan"]["original_query"] == "тканевая инженерия"
    provenance = profile["collection_profile"]["provenance"]
    assert provenance["balanced_discovery_job_id"] == job["job_id"]
    assert provenance["retrieval_adjudication_id"] == adjudication["adjudication_id"]
    assert profile["materialization"]["automatic_execution"] is False
    assert profile["materialization"]["weak_signal_decision"] is False


def test_portable_arxiv_only_results_remain_bounded_when_openalex_is_unavailable():
    job, _ = inputs()
    job["result"] = {
        "source_modes": {"arxiv": "live_api_bounded_compiled_metadata"},
        "works": [{"published_at": "2025-01-01", "sources": ["arxiv"],
                   "branch_provenance": ["tissue"]}],
    }
    _, profile = build_candidate_profile(job, "repository-user")
    assert profile["sources"] == ["arxiv"]
    assert profile["collection_profile"]["decision"] == "bounded_balanced_openalex_arxiv_candidates"
    assert profile["parent_field"]["scope"] == "nonrepresentative_bounded_sample"
    # An explicit request for the complete mirror never silently downgrades.
    _, full_profile = build_candidate_profile(job, "repository-user", source_mode="complete_arxiv")
    assert full_profile["parent_field"]["scope"] == "all_categories"


@pytest.mark.parametrize("work", [
    {"published_at": "2025-01-01", "sources": ["arxiv"],
     "branch_provenance": ["original-query"]},
    {"published_at": "2019-12-31", "sources": ["arxiv"],
     "branch_provenance": ["tissue"]},
])
def test_portable_preview_without_eligible_works_never_switches_to_full_mirror(work):
    job, _ = inputs()
    job["result"] = {
        "source_modes": {"arxiv": "live_api_bounded_compiled_metadata"},
        "works": [work],
    }
    with pytest.raises(ValueError, match="не осталось публикаций для выбранных подтем"):
        build_candidate_profile(job, "repository-user")
    # Full-mirror requests and older discovery modes retain their own contract.
    _, explicit = build_candidate_profile(job, "repository-user", source_mode="complete_arxiv")
    assert explicit["parent_field"]["scope"] == "all_categories"
    job["result"]["source_modes"]["arxiv"] = "pinned_local_metadata_snapshot"
    _, existing = build_candidate_profile(job, "repository-user")
    assert existing["parent_field"]["scope"] == "all_categories"


def test_materialization_requires_compilation_adjudication_and_two_acknowledgements():
    job, adjudication = inputs()
    with pytest.raises(ValueError, match="ограничен arXiv"):
        build_profile(job, adjudication, "analyst",
                      acknowledge_arxiv_only=False, acknowledge_phrase_union=True)
    with pytest.raises(ValueError, match="правилу OR"):
        build_profile(job, adjudication, "analyst",
                      acknowledge_arxiv_only=True, acknowledge_phrase_union=False)
    job["payload"].pop("compiled_branch_specs")
    with pytest.raises(ValueError, match="compiled exact-phrase"):
        build_profile(job, adjudication, "analyst",
                      acknowledge_arxiv_only=True, acknowledge_phrase_union=True)


def test_automatic_candidates_do_not_require_retrieval_or_expert_validation():
    job, _adjudication = inputs()
    mission, profile = build_candidate_profile(job, "system")
    assert mission["mission_id"] == f"universal-monthly-v2-{job['job_id']}"
    assert profile["controlled_search_plan"]["relation"] == (
        "explicit_union_of_selected_branch_phrases_original_query_preview_only"
    )
    assert "retrieval_adjudication_id" not in profile["collection_profile"]["provenance"]
    assert profile["expert_validation"] == {
        "required_for_candidate_generation": False,
        "signal_validation_status": "not_requested",
        "retrieval_validation_status": "not_requested",
        "interpretation": (
            "Система формирует кандидатов автоматически. Пользователь может "
            "позже добровольно направить выбранные карточки экспертам."
        ),
    }
    assert profile["materialization"]["automatic_candidate_generation"] is True


def test_automatic_profile_keeps_frozen_openalex_candidate_input():
    job, _adjudication = inputs()
    job["result"] = {"works": [
        {"published_at": "2023-04-01", "source_provenance": ["openalex"]},
        {"published_at": "2024-05-01", "source_provenance": ["arxiv"]},
    ]}
    mission, profile = build_candidate_profile(job, "system")
    assert mission["sources"] == ["arxiv", "openalex"]
    assert profile["collection_profile"]["decision"] == (
        "bounded_balanced_openalex_arxiv_candidates"
    )
    assert profile["parent_field"]["scope"] == "nonrepresentative_bounded_sample"
    assert profile["collection_profile"]["provenance"]["balanced_result_sha256"] == "b" * 64


def test_complete_arxiv_candidate_mode_is_separate_and_has_parent_corpus():
    job, _adjudication = inputs()
    job["result"] = {"works": [
        {"published_at": "2023-04-01", "source_provenance": ["openalex"]},
        {"published_at": "2024-05-01", "source_provenance": ["arxiv"]},
    ]}
    bounded_mission, bounded = build_candidate_profile(job, "system")
    full_mission, full = build_candidate_profile(
        job, "system", source_mode="complete_arxiv"
    )
    assert full_mission["mission_id"] != bounded_mission["mission_id"]
    assert full["query_version"] != bounded["query_version"]
    assert full["sources"] == ["arxiv"]
    assert full["parent_field"]["scope"] == "all_categories"
    assert full["collection_profile"]["decision"] == (
        "automatic_candidate_generation_from_compiled_local_arxiv_scope"
    )
    assert full["controlled_search_plan"]["included_terms"] == (
        bounded["controlled_search_plan"]["included_terms"]
    )
    assert full["collection_profile"]["provenance"]["balanced_result_sha256"] == "b" * 64
    assert full["collection_profile"]["provenance"]["candidate_source_mode"] == "complete_arxiv"
    assert full["expert_validation"]["required_for_candidate_generation"] is False
    assert "candidate_source_mode" not in bounded["materialization"]
    with pytest.raises(ValueError, match="Unknown candidate source mode"):
        build_candidate_profile(job, "system", source_mode="unsupported")


def test_new_compiled_matching_version_flows_into_full_corpus_profile():
    job, _adjudication = inputs()
    for spec in job["payload"]["compiled_branch_specs"]:
        spec["matching_version"] = ORTHOGRAPHIC_MATCHING_VERSION
    _mission, profile = build_candidate_profile(job, "system")
    assert profile["controlled_search_plan"]["matching_version"] == ORTHOGRAPHIC_MATCHING_VERSION


def test_original_only_query_remains_in_full_corpus():
    job, _adjudication = inputs()
    job["payload"]["branches"] = job["payload"]["branches"][:1]
    job["payload"]["compiled_branch_specs"] = job["payload"]["compiled_branch_specs"][:1]
    _mission, profile = build_candidate_profile(job, "system")
    assert profile["controlled_search_plan"]["included_terms"] == ["тканевая инженерия"]
    assert profile["controlled_search_plan"]["original_query_used_for_preview_only"] is False
    assert profile["controlled_search_plan"]["relation"] == "original_query_exact_phrases_only"


def test_automatic_profile_excludes_partial_boundary_months_but_keeps_request():
    job, _adjudication = inputs()
    job["payload"]["date_from"] = "2020-01-15"
    job["payload"]["as_of_date"] = "2026-09-22"
    mission, profile = build_candidate_profile(job, "system")
    assert mission["period_from"].isoformat() == "2020-02-01"
    assert mission["period_to"].isoformat() == "2026-08-31"
    assert mission["as_of_date"].isoformat() == "2026-09-01"
    assert profile["requested_bounds"] == {
        "date_from": "2020-01-15",
        "as_of_date": "2026-09-22",
        "policy": "complete_calendar_months_only",
        "partial_boundary_days_excluded": True,
    }


def test_compound_plan_keeps_predicate_and_bounded_mode_even_with_only_arxiv():
    job, _adjudication = inputs()
    job["payload"]["branches"] = job["payload"]["branches"][:1]
    job["payload"]["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["нейроморфные чипы"],
        "concept_groups": [["neuromorphic chips"], ["edge devices"]],
        "excluded_phrases": [],
    }]
    job["result"] = {"works": [{"published_at": "2024-05-01",
                               "source_provenance": ["arxiv"]}]}
    mission, profile = build_candidate_profile(job, "system")
    plan = profile["controlled_search_plan"]
    assert mission["sources"] == ["arxiv"]
    assert profile["collection_profile"]["decision"] == "bounded_compound_concept_candidates"
    assert profile["parent_field"]["scope"] == "nonrepresentative_bounded_sample"
    assert plan["compiled_branch_specs"][0]["concept_groups"] == [
        ["neuromorphic chips"], ["edge devices"]]
    assert plan["relation"] == "original_query_literal_or_required_concepts"
