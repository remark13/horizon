import json

from scripts import benchmark_scout_no_suggestions as benchmark


def test_ui_equivalent_no_suggestions_stops_without_fabricating_cards(tmp_path, monkeypatch):
    config = tmp_path / "cases.json"
    config.write_text(json.dumps({
        "version": "scout-no-suggestions-slo-pilot-v1",
        "date_from": "2021-09-01", "as_of_date": "2026-09-01",
        "limit_per_source": 25, "max_results": 300,
        "openalex_collection_mode": "cache_year_spread",
        "cases": [{"case_id": "boundary", "role": "boundary", "query_ru": "ферменты"},
                  {"case_id": "outside", "role": "outside", "query_ru": "сенсоры"}],
    }), encoding="utf-8")
    calls = []

    def post(_base, route, payload):
        calls.append(route)
        if route == "/query-plan/preview":
            return {"plan_payload_sha256": "hash", "suggestions": []}
        if route == "/query-plan/approve":
            assert payload["selected_branch_ids"] == []
            return {"plan_id": "plan", "approved_plan": {"branches": [
                {"branch_id": "original-query", "query": "ферменты"}]}}
        if route.endswith("/compile"):
            assert payload["branch_specs"][0]["included_phrases"] == ["ферменты"]
            return {"compilation_id": "compiled"}
        if route.endswith("/jobs/discovery"):
            return {"job_id": "discovery"}
        raise AssertionError("No analysis expected after zero works")

    monkeypatch.setattr(benchmark, "_post", post)
    monkeypatch.setattr(benchmark, "_poll", lambda *_args: {
        "job_id": "discovery", "status": "succeeded",
        "result": {"works": [], "errors": {}}})
    output = tmp_path / "state.json"
    result = benchmark.run(config_path=config, case_id="boundary",
                           base="http://test", output=output)
    assert result["status"] == "no_works"
    assert result["bounded_discovery_works"] == 0
    assert result["within_20_minutes"]
    assert len(calls) == 4
    assert benchmark.run(config_path=config, case_id="boundary",
                         base="http://test", output=output, resume=True) == result
    assert len(calls) == 4


def test_single_broad_russian_case_keeps_live_ui_source_mode(tmp_path, monkeypatch):
    config = tmp_path / "robotic.json"
    config.write_text(json.dumps({
        "version": "scout-no-suggestions-slo-pilot-v2",
        "date_from": "2021-09-01", "as_of_date": "2026-09-01",
        "limit_per_source": 25, "max_results": 300,
        "openalex_collection_mode": "live_with_cache_fallback",
        "cases": [{"case_id": "robot", "role": "broad",
                   "query_ru": "Роботизированные манипуляции"}],
    }), encoding="utf-8")
    calls = []

    def post(_base, route, payload):
        calls.append((route, payload))
        if route == "/query-plan/preview":
            return {"plan_payload_sha256": "hash", "suggestions": []}
        if route == "/query-plan/approve":
            return {"plan_id": "plan", "approved_plan": {"branches": [
                {"branch_id": "original-query", "query": "Роботизированные манипуляции"}]}}
        if route.endswith("/compile"):
            return {"compilation_id": "compiled"}
        if route.endswith("/jobs/discovery"):
            assert payload["openalex_collection_mode"] == "live_with_cache_fallback"
            return {"job_id": "discovery"}
        raise AssertionError(route)

    monkeypatch.setattr(benchmark, "_post", post)
    monkeypatch.setattr(benchmark, "_poll", lambda *_args: {
        "job_id": "discovery", "status": "succeeded",
        "result": {"works": [], "errors": {}}})
    result = benchmark.run(config_path=config, case_id="robot",
                           base="http://test", output=tmp_path / "state.json")
    assert result["status"] == "no_works"
    assert result["policy"]["openalex_collection_mode"] == "live_with_cache_fallback"
    assert [route for route, _ in calls] == [
        "/query-plan/preview", "/query-plan/approve",
        "/query-plans/plan/compile", "/query-plans/plan/jobs/discovery"]
