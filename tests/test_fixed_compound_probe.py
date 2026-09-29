import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import probe_fixed_compound_queries as probe


def test_fixed_compound_probe_records_source_and_exact_work(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": "ru-compound-multicase-pilot-v1",
        "date_from": "2021-09-01", "as_of_date": "2026-09-01",
        "cases": [{"case_id": "outside", "role": "outside_priority_map",
                   "concept_groups": [["optical clock"], ["underwater navigation"]]}],
    }), encoding="utf-8")
    index = tmp_path / "index"
    index.mkdir()
    (index / "manifest.json").write_text(json.dumps({
        "version": "arxiv-trigram-index-v4", "source": {"revision": "pinned-test"},
    }), encoding="utf-8")
    with sqlite3.connect(index / "index.sqlite3") as conn:
        conn.execute("CREATE TABLE works (arxiv_id TEXT, title TEXT, abstract TEXT, "
                     "first_submission_date TEXT, categories TEXT)")
        conn.execute("INSERT INTO works VALUES (?,?,?,?,?)",
                     ("2401.00001", "Optical clock navigation", "Underwater test",
                      "2024-01-01", "physics.atom-ph"))
    monkeypatch.setattr(probe, "exact_concept_search", lambda *_args, **_kwargs: {
        "arxiv_ids": ["2401.00001"], "audit": {"exact_unique_ids": 1}})
    result = probe.run(config, index)
    assert result["source_revision"] == "pinned-test"
    assert result["rows"][0]["match_count"] == 1
    assert result["rows"][0]["sample_works"][0]["url"] == \
        "https://arxiv.org/abs/2401.00001"
    assert result["policy"]["matches_are_not_weak_signals"] is True


def test_fixed_compound_probe_rejects_duplicate_cases(tmp_path):
    config = tmp_path / "config.json"
    case = {"case_id": "repeat", "concept_groups": [["optical clock"],
                                                     ["underwater navigation"]]}
    config.write_text(json.dumps({"version": "ru-compound-multicase-pilot-v1",
                                  "cases": [case, case]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate case ID"):
        probe.run(config, tmp_path)


def test_goal_pilot_is_frozen_across_ten_directions_and_six_customer_areas():
    config = json.loads(Path("config/goal-cross-domain-compound-pilot.v1.json")
                        .read_text(encoding="utf-8"))
    catalog = Path("data/reference/priority_catalog/v1/catalog.json")
    assert len(config["cases"]) == 22
    assert len(probe._validate_goal_cases(config, catalog)) == 64
    invalid = deepcopy(config)
    invalid["cases"][0]["direction_id"] = "artificial-intelligence"
    with pytest.raises(ValueError, match="National area mapping mismatch"):
        probe._validate_goal_cases(invalid, catalog)
    invalid = deepcopy(config)
    invalid["cases"] = invalid["cases"][:-1]
    with pytest.raises(ValueError, match="does not cover"):
        probe._validate_goal_cases(invalid, catalog)
