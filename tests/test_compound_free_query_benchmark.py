from scripts import benchmark_compound_free_query as module


def test_finished_analysis_without_cards_is_reported_explicitly(tmp_path, monkeypatch):
    def post(_base, route, _payload):
        if route == "/query-plan/preview":
            return {"plan_payload_sha256": "a" * 64, "suggestions": []}
        if route == "/query-plan/approve":
            return {"plan_id": "plan"}
        if route == "/query-plans/plan/compile":
            return {"compilation_id": "compiled",
                    "compiled_plan": {"version": "compound-v1"}}
        if route == "/query-plans/plan/jobs/discovery":
            return {"job_id": "discovery"}
        if route == "/jobs/discovery/full-analysis":
            return {"analysis_job": {"job_id": "analysis"}}
        raise AssertionError(route)

    def poll(_base, _state, _output, _job_id, stage, _deadline):
        return ({"result": {"works": [{"title": "Scientific work"}],
                            "errors": {}, "branches": []}}
                if stage == "collect" else
                {"mission_id": "mission", "result": {"runs": {"score": 1}}})

    monkeypatch.setattr(module, "_post", post)
    monkeypatch.setattr(module, "_poll", poll)
    monkeypatch.setattr(module, "_get", lambda *_: {"cards": []})
    result = module.run(base="http://local", output=tmp_path / "result.json",
                        resume=False)
    assert result["status"] == "succeeded"
    assert result["card_count"] == 0
    assert result["candidate_status"] == "analysis_succeeded_no_candidates"


def test_legacy_variant_keeps_original_literal_only(tmp_path, monkeypatch):
    compiled_specs = []

    def post(_base, route, payload):
        if route == "/query-plan/preview":
            return {"plan_payload_sha256": "a" * 64, "suggestions": []}
        if route == "/query-plan/approve":
            return {"plan_id": "plan"}
        if route == "/query-plans/plan/compile":
            compiled_specs.extend(payload["branch_specs"])
            return {"compilation_id": "compiled",
                    "compiled_plan": {"version": "legacy-v1"}}
        if route == "/query-plans/plan/jobs/discovery":
            return {"job_id": "discovery"}
        raise AssertionError(route)

    monkeypatch.setattr(module, "_post", post)
    monkeypatch.setattr(module, "_poll", lambda *_: {"result": {
        "works": [], "errors": {}, "branches": []}})
    result = module.run(base="http://local", output=tmp_path / "legacy.json",
                        resume=False, variant="legacy")
    assert result["status"] == "no_works"
    assert result["variant"] == "legacy"
    assert compiled_specs == [{"branch_id": "original-query",
                               "included_phrases": [module.QUERY],
                               "excluded_phrases": []}]
