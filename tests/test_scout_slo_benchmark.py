import json

from scripts import benchmark_scout_end_to_end as benchmark


def test_scout_smoke_resume_reuses_completed_operations(tmp_path, monkeypatch):
    calls = []

    def post(_base, route, payload):
        calls.append(route)
        if route == "/query-plan/preview":
            return {"plan_payload_sha256": "hash", "suggestions": [
                {"suggestion_id": identifier, "query_en": "phrase"}
                for identifier in benchmark.BRANCH_PHRASES]}
        if route == "/query-plan/approve":
            return {"plan_id": "plan", "approved_plan": {"branches": [
                {"branch_id": "original-query", "query": payload["query"]},
                *({"branch_id": identifier, "query": "phrase"}
                  for identifier in benchmark.BRANCH_PHRASES)]}}
        if route.endswith("/compile"):
            return {"compilation_id": "compiled"}
        if route.endswith("/jobs/discovery"):
            return {"job_id": "discovery"}
        if route.endswith("/full-analysis"):
            return {"analysis_job": {"job_id": "analysis"}}
        raise AssertionError(route)

    def get(_base, route):
        if route.endswith("discovery"):
            return {"status": "succeeded"}
        return {"status": "succeeded", "mission_id": "mission",
                "result": {"runs": {"score": 123}}}

    monkeypatch.setattr(benchmark, "_post", post)
    monkeypatch.setattr(benchmark, "_get", get)
    output = tmp_path / "run.json"
    result = benchmark.run(base="http://test", output=output, resume=False,
                           limit_seconds=1200)
    assert result["status"] == "succeeded"
    assert result["score_run_id"] == 123
    first_calls = list(calls)
    repeated = benchmark.run(base="http://test", output=output, resume=True,
                             limit_seconds=1200)
    assert repeated["status"] == "succeeded"
    assert calls == first_calls
    assert json.loads(output.read_text())["operation_ids"].keys() == {
        "approve", "compile", "discovery", "analysis"}
