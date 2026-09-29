import json

from saia import scout_slo_audit


def test_scout_slo_audit_does_not_call_candidates_confirmed(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "version": "scout-end-to-end-bas-smoke-v1", "status": "succeeded",
        "query_ru": "БАС", "elapsed_seconds": 270,
        "within_20_minutes": True, "discovery_job_id": "d",
        "analysis_job_id": "a", "mission_id": "m", "score_run_id": 1}))

    def get(_base, path):
        if path == "/jobs/d":
            return {"status": "succeeded", "result": {"works": [{}, {}]}}
        if path == "/jobs/a":
            return {"status": "succeeded", "mission_id": "m", "events": []}
        return {"mission_id": "m", "score_run_id": 1,
                "counts": {"all_cards": 1}, "parent_corpus_context": None,
                "queue": [{"rank": 1, "card": {"label": "line", "status": "watch",
                                                "metrics": {"observed": {}}}}]}

    monkeypatch.setattr(scout_slo_audit, "_get", get)
    result = scout_slo_audit.audit(state_path=state_path, base="http://test")
    assert result["automatic_cards"] == 1
    assert result["cards_with_known_momentum_percentile"] == 0
    assert result["limits"]["automatic_candidates_not_confirmed_weak_signals"] is True
