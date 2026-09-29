import json

from scripts import probe_fixed_compound_openalex_cache as probe


def test_fixed_compound_openalex_cache_probe_preserves_coverage_limit(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": "ru-compound-multicase-pilot-v1",
        "date_from": "2021-09-01", "as_of_date": "2026-09-01",
        "cases": [{"case_id": "outside", "concept_groups": [["optical clock"],
                                                           ["underwater navigation"]]}],
    }), encoding="utf-8")
    captured = {}

    def fake_scan(specs, start, cutoff, limit):
        captured.update({"specs": specs, "start": start, "cutoff": cutoff,
                         "limit": limit})
        return {"version": "cache-test", "available_unique_ids_in_period": 17,
                "latest_cache_ingested_at": "2026-09-25T00:00:00+00:00",
                "branches": [{"branch_id": "outside", "eligible_matches": 0,
                              "selected_year_counts": {}, "works": []}]}

    monkeypatch.setattr(probe, "scan", fake_scan)
    result = probe.run(config)
    assert captured["specs"][0]["included_phrases"] == []
    assert captured["limit"] == 25
    assert result["rows"][0]["selected_count"] == 0
    assert result["policy"]["zero_not_absence_of_science"] is True
