import json

from scripts import probe_cross_cluster_diffusion as probe


def test_broad_phrase_control_recovery_is_not_distinct_signal_detection(tmp_path, monkeypatch):
    mission_dir = tmp_path / "missions"
    mission_dir.mkdir()
    (mission_dir / "test.json").write_text(json.dumps({
        "benchmark": {"target_arxiv_ids": ["c1", "c2", "c3", "c4"],
                      "acceptance": {"min_target_share_in_dominant_topic": 0.1}}
    }), encoding="utf-8")
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    payload = {"mission_id": "test", "documents": [
        {"id": str(i), "identifiers": [{"kind": "arxiv", "value": f"c{i + 1}"}]}
        for i in range(4)]}
    result = {"rows": [{"phrase": "broad phrase", "recent_active_areas": 20,
                        "evidence": [{"work_id": str(i)} for i in [0, 1, *range(4, 102)]]}]}
    acceptance = {"maximum_review_rank": 15, "minimum_eligible_controls_in_one_proposal": 2,
                  "minimum_eligible_control_fraction": 0.5}
    evaluation = probe.evaluate_after_generation(result, payload, acceptance)
    assert evaluation["predeclared_control_recovery_gate_passed"] is True
    assert evaluation["original_mission_distinct_target_gate_passed"] is False
    assert evaluation["top15_weak_signal_precision"] is None


def test_empty_control_set_never_passes_recovery(tmp_path, monkeypatch):
    (tmp_path / "missions").mkdir()
    (tmp_path / "missions" / "test.json").write_text(json.dumps({
        "benchmark": {"target_arxiv_ids": ["absent"],
                      "acceptance": {"min_target_share_in_dominant_topic": 0.1}}
    }), encoding="utf-8")
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    evaluation = probe.evaluate_after_generation({"rows": []}, {"mission_id": "test", "documents": []},
        {"maximum_review_rank": 15, "minimum_eligible_controls_in_one_proposal": 2,
         "minimum_eligible_control_fraction": 0.5})
    assert evaluation["eligible_control_works"] == 0
    assert evaluation["original_mission_distinct_target_gate_passed"] is False
