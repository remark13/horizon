from datetime import date

import pytest

from saia.multi_branch_discovery import LEGACY_VERSION, RESULT_ROLE, VERSION, run


def payload():
    return {
        "contract_version": VERSION,
        "approved_query_plan_id": "00000000-0000-0000-0000-000000000001",
        "approved_plan_payload_sha256": "a" * 64,
        "date_from": "2020-01-01", "as_of_date": "2026-01-01",
        "limit_per_source": 5, "max_results": 4,
        "branches": [
            {"branch_id": "original-query", "query": "новые материалы"},
            {"branch_id": "materials/composites", "query": "advanced composites"},
        ],
    }


def work(key, title, source):
    return {
        "canonical_key": key, "title": title, "abstract": None,
        "published_at": "2025-01-01", "sources": [source],
        "source_ids": [key], "urls": [], "doi": None, "authors": [],
    }


def portable_payload():
    value = payload()
    value["branches"] = [{"branch_id": "original-query", "query": "advanced composites"}]
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["advanced composites"],
        "excluded_phrases": ["review article"],
    }]
    return value


def test_compiled_search_still_requires_mirror_without_explicit_portable_mode(monkeypatch):
    monkeypatch.delenv("SAIA_ARXIV_MIRROR_DIR", raising=False)
    monkeypatch.delenv("SAIA_ALLOW_LIVE_ARXIV", raising=False)
    with pytest.raises(ValueError, match="не подключён локальный arXiv"):
        run(portable_payload(), lambda *_: {"works": [], "errors": {}})


def test_portable_live_search_checks_both_sources_and_preserves_bounded_provenance(monkeypatch):
    monkeypatch.delenv("SAIA_ARXIV_MIRROR_DIR", raising=False)
    monkeypatch.setenv("SAIA_ALLOW_LIVE_ARXIV", "1")
    calls = []

    def collector(query, start, cutoff, limit):
        calls.append((query, start, cutoff, limit))
        return {"works": [
            work("ax-yes", "Advanced composites for sensors", "arxiv"),
            work("ax-noise", "Clinical trials", "arxiv"),
            work("ax-excluded", "Advanced composites review article", "arxiv"),
            work("oa-yes", "Advanced composites", "openalex"),
            work("oa-noise", "Graph models", "openalex"),
        ], "errors": {}}

    def forbidden_local(*_):
        raise AssertionError("No mirror should be touched in portable mode")

    value = portable_payload()
    value["max_results"] = 2
    result = run(value, collector, local_scanner=forbidden_local)
    assert calls == [("advanced composites", date(2020, 1, 1), date(2026, 1, 1), 5)]
    assert {row["canonical_key"] for row in result["works"]} == {"ax-yes", "oa-yes"}
    assert result["source_modes"]["arxiv"] == "live_api_bounded_compiled_metadata"
    assert result["local_arxiv_audit"] is None
    assert result["coverage_comparable"] is None
    assert result["scientific_score_calculated"] is False
    assert result["branches"][0]["arxiv_compiled_phrase_filter"] == {
        "bounded_input": 3, "accepted": 1, "live_recall_proven": False}
    assert result["branches"][0]["openalex_compiled_phrase_filter"] == {
        "bounded_input": 2, "accepted": 1, "live_recall_proven": False}


def test_portable_live_search_keeps_source_failures_visible(monkeypatch):
    monkeypatch.delenv("SAIA_ARXIV_MIRROR_DIR", raising=False)
    monkeypatch.setenv("SAIA_ALLOW_LIVE_ARXIV", "1")
    result = run(portable_payload(), lambda *_: {
        "works": [work("ax-only", "Advanced composites", "arxiv")],
        "errors": {"openalex": "rate limited"},
    })
    assert len(result["works"]) == 1
    assert result["errors"] == {"original-query/openalex": "rate limited"}
    assert result["coverage_comparable"] is None


def test_configured_mirror_still_takes_precedence_over_portable_flag(monkeypatch):
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")
    monkeypatch.setenv("SAIA_ALLOW_LIVE_ARXIV", "1")
    result = run(portable_payload(), lambda *_: {"works": [], "errors": {}},
                 local_scanner=lambda *_: {"branches": [{
                     "branch_id": "original-query", "works": [
                         work("ax-local", "Advanced composites", "arxiv")]}]})
    assert result["source_modes"]["arxiv"] == "pinned_local_metadata_snapshot"
    assert result["local_arxiv_audit"] is not None
    assert "arxiv_compiled_phrase_filter" not in result["branches"][0]


def test_each_branch_is_collected_separately_and_merge_is_balanced():
    seen = []
    def collector(query, start, cutoff, limit):
        seen.append((query, start, cutoff, limit))
        stem = "original" if query == "новые материалы" else "composite"
        return {
            "works": [
                work(f"{stem}-oa", f"{stem} OpenAlex", "openalex"),
                work(f"{stem}-ax", f"{stem} arXiv", "arxiv"),
            ],
            "source_counts": {"openalex": 1, "arxiv": 1}, "errors": {},
        }
    result = run(payload(), collector)
    assert result["result_role"] == RESULT_ROLE
    assert [item[0] for item in seen] == ["новые материалы", "advanced composites"]
    assert all(item[1:] == (date(2020, 1, 1), date(2026, 1, 1), 5) for item in seen)
    assert result["merge"]["branch_contributions"] == {
        "original-query": 2, "materials/composites": 2,
    }
    assert result["weak_signal_assessment_performed"] is False
    assert result["source_modes"]["local_arxiv"].startswith("not_used")


def test_branch_failure_is_visible_and_does_not_erase_other_branch():
    def collector(query, *_args):
        if query == "новые материалы":
            raise RuntimeError("temporary failure")
        return {
            "works": [work("ok", "Found", "openalex")],
            "source_counts": {"openalex": 1, "arxiv": 0},
            "errors": {"arxiv": "unavailable"},
        }
    result = run(payload(), collector)
    assert len(result["works"]) == 1
    assert "original-query/collector" in result["errors"]
    assert "materials/composites/arxiv" in result["errors"]
    assert result["coverage_comparable"] is None


def test_run_rejects_more_than_twelve_branches():
    value = payload()
    value["branches"] = [
        {"branch_id": f"b-{index}", "query": f"query {index}"}
        for index in range(13)
    ]
    try:
        run(value, lambda *_: {})
    except ValueError as error:
        assert "12" in str(error)
    else:
        raise AssertionError("13 branches must be rejected")


def test_explicit_compilation_replaces_live_arxiv_with_one_pass_local_results(monkeypatch):
    value = payload()
    value["compiled_query_plan_id"] = "00000000-0000-0000-0000-000000000099"
    value["compiled_plan_payload_sha256"] = "b" * 64
    value["compiled_branch_specs"] = [
        {"branch_id": branch["branch_id"], "included_phrases": [branch["query"]],
         "excluded_phrases": []}
        for branch in value["branches"]
    ]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")
    seen = []
    def local_scanner(directory, specs, start, cutoff, limit):
        seen.append((directory, specs, start, cutoff, limit))
        return {
            "version": "fixture", "scanned_rows": 4,
            "branches": [
                {"branch_id": spec["branch_id"], "works": [
                    work("local-" + spec["branch_id"], "Local arXiv", "arxiv")
                ], "eligible_matches": 1}
                for spec in specs
            ],
        }
    def collector(query, *_args):
        return {
            "works": [work("oa-" + query, query + " OpenAlex", "openalex")],
            "source_counts": {"openalex": 1, "arxiv": 0},
            "errors": {"arxiv": "HTTP 406"},
        }
    result = run(value, collector, local_scanner)
    assert seen[0][0] == "/pinned-arxiv"
    assert result["errors"] == {}
    assert result["source_modes"]["arxiv"] == "pinned_local_metadata_snapshot"
    assert result["merge"]["source_contributions"] == {"openalex": 2, "arxiv": 2}
    assert result["local_arxiv_audit"]["scanned_rows"] == 4


def test_explicit_cache_mode_uses_year_spread_openalex_without_live_api(monkeypatch):
    value = payload()
    value["openalex_collection_mode"] = "cache_year_spread"
    value["compiled_branch_specs"] = [
        {"branch_id": branch["branch_id"], "included_phrases": [branch["query"]],
         "excluded_phrases": []} for branch in value["branches"]]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")
    def local_scanner(_directory, specs, _start, _cutoff, _limit):
        return {"branches": [{"branch_id": spec["branch_id"], "works": [
            work("ax-" + spec["branch_id"], "arXiv", "arxiv")]} for spec in specs]}
    def cache_scanner(specs, _start, _cutoff, _limit):
        return {"selection_strategy": "year_balanced_hash_v1", "branches": [
            {"branch_id": spec["branch_id"], "works": [
                work("oa-" + spec["branch_id"], "OpenAlex", "openalex")]}
            for spec in specs]}
    def live_collector(*_args):
        raise AssertionError("cache mode must not spend live API budget")
    result = run(value, live_collector, local_scanner, cache_scanner)
    assert result["errors"] == {}
    assert result["source_modes"]["openalex"] == "previously_ingested_local_cache_with_live_fallback"
    assert result["merge"]["source_contributions"] == {"openalex": 2, "arxiv": 2}


def test_cache_mode_falls_back_to_live_api_for_uncached_branch(monkeypatch):
    value = payload()
    value["openalex_collection_mode"] = "cache_year_spread"
    value["compiled_branch_specs"] = [
        {"branch_id": branch["branch_id"], "included_phrases": [branch["query"]],
         "excluded_phrases": []} for branch in value["branches"]]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")
    def local_scanner(_directory, specs, _start, _cutoff, _limit):
        return {"branches": [{"branch_id": spec["branch_id"], "works": []}
                             for spec in specs]}
    def cache_scanner(specs, _start, _cutoff, _limit):
        return {"branches": [
            {"branch_id": spec["branch_id"], "works": (
                [work("oa-cache", "Cached", "openalex")]
                if spec["branch_id"] == "materials/composites" else [])}
            for spec in specs]}
    seen = []
    def live_collector(query, *_args):
        seen.append(query)
        return {"works": [work("oa-live", query + " Live", "openalex")], "errors": {}}
    result = run(value, live_collector, local_scanner, cache_scanner)
    assert seen == ["новые материалы"]
    assert result["merge"]["source_contributions"] == {"openalex": 2}
    assert result["branches"][0]["openalex_origin"] == "live_api_current_metadata"


def _live_first_fixture(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["openalex_collection_mode"] = "live_with_cache_fallback"
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "excluded_phrases": []}]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")
    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}
    def cache_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": [
            work("oa-cache", "Cached", "openalex")]}]}
    return value, local_scanner, cache_scanner


def test_live_first_mode_does_not_replace_many_live_works_with_one_cached_work(monkeypatch):
    value, local_scanner, cache_scanner = _live_first_fixture(monkeypatch)
    value["max_results"] = 25
    def collector(*_args):
        return {"works": [work(f"oa-live-{number}", "Новые материалы Live", "openalex")
                          for number in range(25)], "errors": {}}
    result = run(value, collector, local_scanner, cache_scanner)
    assert len(result["works"]) == 25
    assert result["branches"][0]["source_counts"]["openalex"] == 25
    assert result["branches"][0]["openalex_origin"] == "live_api_current_metadata"
    assert result["errors"] == {}


def test_live_first_mode_uses_marked_cache_only_after_explicit_api_failure(monkeypatch):
    value, local_scanner, cache_scanner = _live_first_fixture(monkeypatch)
    def collector(*_args):
        return {"works": [], "errors": {"openalex": "HTTP 503"}}
    result = run(value, collector, local_scanner, cache_scanner)
    assert [item["canonical_key"] for item in result["works"]] == ["oa-cache"]
    assert result["errors"]["original-query/openalex"] == "HTTP 503"
    assert result["branches"][0]["openalex_origin"] == (
        "incomplete_local_cache_after_live_api_failure")


def test_live_first_mode_keeps_valid_empty_response_empty(monkeypatch):
    value, local_scanner, cache_scanner = _live_first_fixture(monkeypatch)
    result = run(value, lambda *_args: {"works": [], "errors": {}},
                 local_scanner, cache_scanner)
    assert result["works"] == []
    assert result["branches"][0]["openalex_origin"] == "live_api_current_metadata"
    assert result["errors"] == {}


def test_live_first_mode_still_uses_live_api_if_cache_scan_fails(monkeypatch):
    value, local_scanner, _cache_scanner = _live_first_fixture(monkeypatch)
    def cache_scanner(*_args):
        raise RuntimeError("cache unavailable")
    result = run(value, lambda *_args: {"works": [
        work("oa-live", "Новые материалы Live", "openalex")], "errors": {}},
        local_scanner, cache_scanner)
    assert [item["canonical_key"] for item in result["works"]] == ["oa-live"]
    assert "cache unavailable" in result["errors"]["openalex_cache_scan"]


def test_prepared_openalex_supplements_live_without_replacing_it(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "excluded_phrases": []}]
    value["openalex_collection_mode"] = "live_with_prepared_supplement"
    value["max_results"] = 5
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}

    def prepared_scanner(_directory, _specs, _start, _cutoff, _limit):
        return {"manifest_sha256": "f" * 64, "branches": [{
            "branch_id": "original-query",
            "eligible_matches_in_prepared_query_cohorts": 3,
            "works": [work("oa-shared", "Новые материалы совместная", "openalex"),
                      work("oa-prepared", "Новые материалы новые", "openalex")],
        }]}

    result = run(value, lambda *_args: {"works": [
        work("oa-live", "Новые материалы Live", "openalex"),
        work("oa-shared", "Новые материалы совместная", "openalex"),
    ], "errors": {}}, local_scanner, prepared_openalex_scanner=prepared_scanner)
    assert [row["canonical_key"] for row in result["works"]] == [
        "oa-live", "oa-shared", "oa-prepared"]
    assert result["branches"][0]["prepared_openalex"]["added_after_live_dedup"] == 1
    assert result["branches"][0]["prepared_openalex"]["duplicate_of_live_or_prepared"] == 1
    assert result["branches"][0]["openalex_compiled_phrase_filter"] == {
        "bounded_input": 2, "accepted": 2, "live_recall_proven": False,
    }
    assert result["source_modes"]["openalex"] == \
        "live_api_plus_pinned_query_cohort_supplement"
    assert result["coverage_comparable"] is None


def test_prepared_openalex_zero_or_failure_does_not_mask_live(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "excluded_phrases": []}]
    value["openalex_collection_mode"] = "live_with_prepared_supplement"
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}

    def live_collector(*_args):
        return {"works": [work("oa-live", "Новые материалы Live", "openalex")],
                "errors": {}}

    def empty_prepared(*_args):
        return {"manifest_sha256": "f" * 64, "branches": [{
            "branch_id": "original-query", "works": [],
            "eligible_matches_in_prepared_query_cohorts": 0}]}

    empty = run(value, live_collector, local_scanner,
                prepared_openalex_scanner=empty_prepared)
    assert [row["canonical_key"] for row in empty["works"]] == ["oa-live"]
    assert empty["branches"][0]["prepared_openalex"]["complete_for_arbitrary_query"] is False

    def failing_prepared(*_args):
        raise RuntimeError("pinned cohort unavailable")

    failed = run(value, live_collector, local_scanner,
                 prepared_openalex_scanner=failing_prepared)
    assert [row["canonical_key"] for row in failed["works"]] == ["oa-live"]
    assert "pinned cohort unavailable" in failed["errors"]["prepared_openalex_scan"]


def test_prepared_openalex_is_marked_as_incomplete_fallback_after_live_failure(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "excluded_phrases": []}]
    value["openalex_collection_mode"] = "live_with_prepared_supplement"
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}

    def prepared_scanner(*_args):
        return {"manifest_sha256": "f" * 64, "branches": [{
            "branch_id": "original-query", "works": [
                work("oa-prepared", "Новые материалы", "openalex")],
            "eligible_matches_in_prepared_query_cohorts": 1}]}

    def failing_live(*_args):
        raise RuntimeError("OpenAlex unavailable")

    result = run(value, failing_live, local_scanner,
                 prepared_openalex_scanner=prepared_scanner)
    assert [row["canonical_key"] for row in result["works"]] == ["oa-prepared"]
    assert result["branches"][0]["openalex_origin"] == \
        "pinned_query_cohort_after_live_api_failure"
    assert "OpenAlex unavailable" in result["errors"]["original-query/openalex"]
    assert result["coverage_comparable"] is None


def test_prepared_openalex_reaches_default_top15_limit_without_hiding_live(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "excluded_phrases": []}]
    value["openalex_collection_mode"] = "live_with_prepared_supplement"
    value["limit_per_source"] = 25
    value["max_results"] = 15
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}

    def prepared_scanner(*_args):
        return {"manifest_sha256": "f" * 64, "branches": [{
            "branch_id": "original-query",
            "eligible_matches_in_prepared_query_cohorts": 25,
            "works": [work(f"prepared-{i}", "Новые материалы", "openalex")
                      for i in range(25)]}]}

    def live_collector(*_args):
        return {"works": [work(f"live-{i}", "Новые материалы", "openalex")
                          for i in range(25)], "errors": {}}

    result = run(value, live_collector, local_scanner,
                 prepared_openalex_scanner=prepared_scanner)
    assert len(result["works"]) == 15
    assert result["merge"]["retrieval_origin_counts_in_selected_results"] == {
        "live_openalex_api": 10,
        "pinned_query_specific_openalex_cohort": 5,
    }
    assert result["works"][0]["retrieval_origins"] == ["live_openalex_api"]
    assert result["works"][2]["retrieval_origins"] == [
        "pinned_query_specific_openalex_cohort"]


def test_live_openalex_exact_branch_rechecks_loose_api_hits(monkeypatch):
    value, local_scanner, cache_scanner = _live_first_fixture(monkeypatch)
    def collector(*_args):
        return {"works": [
            work("oa-relevant", "Новые материалы для сенсоров", "openalex"),
            work("oa-noise", "Medical training simulation", "openalex"),
        ], "errors": {}}
    result = run(value, collector, local_scanner, cache_scanner)
    assert [item["canonical_key"] for item in result["works"]] == ["oa-relevant"]
    assert result["branches"][0]["openalex_compiled_phrase_filter"] == {
        "bounded_input": 2, "accepted": 1, "live_recall_proven": False}
    value["contract_version"] = LEGACY_VERSION
    old = run(value, collector, local_scanner, cache_scanner)
    assert {item["canonical_key"] for item in old["works"]} == {
        "oa-relevant", "oa-noise"}


def test_live_openalex_concept_branch_rechecks_bounded_results(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "concept_groups": [["neuromorphic chips"], ["edge devices"]],
        "excluded_phrases": [],
    }]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}

    def collector(*_args):
        records = [
            work("oa-both", "Neuromorphic chips for edge devices", "openalex"),
            work("oa-one", "Neuromorphic chips in cloud", "openalex"),
        ]
        return {"works": records, "errors": {}}

    result = run(value, collector, local_scanner)
    assert [item["canonical_key"] for item in result["works"]] == ["oa-both"]
    assert result["branches"][0]["openalex_compound_filter"] == {
        "bounded_input": 2, "accepted": 1, "live_recall_proven": False}


def test_explicit_boolean_mode_sends_group_logic_to_openalex(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["openalex_collection_mode"] = "compound_boolean_live"
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "concept_groups": [["neuromorphic chips", "neuromorphic processors"],
                           ["edge devices"]], "excluded_phrases": [],
    }]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": []}]}

    seen = []

    def collector(query, *_args):
        seen.append(query)
        return {"works": [work("oa-both", "Neuromorphic chips for edge devices",
                               "openalex")], "errors": {}}

    def cache_scanner(*_args):
        raise AssertionError("Explicit live mode must not be masked by cache")

    result = run(value, collector, local_scanner, cache_scanner)
    expected = ('("neuromorphic chips" OR "neuromorphic processors") '
                'AND ("edge devices")')
    assert seen == [expected]
    assert result["branches"][0]["openalex_api_query"] == expected
    assert result["branches"][0]["openalex_origin"] == \
        "live_api_boolean_concept_preview"
    assert result["source_modes"]["openalex"] == \
        "live_api_boolean_concept_preview"
    assert len(result["works"]) == 1


def test_openalex_failure_preserves_local_arxiv_in_compiled_run(monkeypatch):
    value = payload()
    value["branches"] = value["branches"][:1]
    value["openalex_collection_mode"] = "compound_boolean_live"
    value["compiled_branch_specs"] = [{
        "branch_id": "original-query", "included_phrases": ["новые материалы"],
        "concept_groups": [["neuromorphic chips"], ["edge devices"]],
        "excluded_phrases": [],
    }]
    monkeypatch.setenv("SAIA_ARXIV_MIRROR_DIR", "/pinned-arxiv")

    def local_scanner(*_args):
        return {"branches": [{"branch_id": "original-query", "works": [
            work("ax-valid", "Neuromorphic chips for edge devices", "arxiv")]}]}

    def failing_collector(*_args):
        raise RuntimeError("OpenAlex temporarily unavailable")

    result = run(value, failing_collector, local_scanner)
    assert [item["canonical_key"] for item in result["works"]] == ["ax-valid"]
    assert "original-query/collector" in result["errors"]
    assert result["branches"][0]["source_counts"] == {"openalex": 0, "arxiv": 1}
