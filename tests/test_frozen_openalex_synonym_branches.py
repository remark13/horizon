import json
from pathlib import Path

from scripts.probe_frozen_openalex_synonym_branches import CASE_IDS, run


def test_frozen_synonym_branches_are_bounded_and_auditable(tmp_path, monkeypatch):
    config = {"version": "ru-compound-multicase-pilot-v1",
              "date_from": "2021-01-01", "as_of_date": "2026-09-01",
              "cases": [{"case_id": case_id,
                         "concept_groups": [["one", "two"], ["alpha", "beta"]]}
                        for case_id in CASE_IDS]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    calls = []

    def fake_one(_client, **kwargs):
        calls.append(kwargs["query"])
        return {"status": "succeeded", "mode": "lexical", "results": [
            {"openalex_id": "https://openalex.org/W1", "abstract": "not archived",
             "title": "Example", "within_exact_period": True}]}

    monkeypatch.setattr("scripts.probe_frozen_openalex_synonym_branches._one", fake_one)
    report = run(path, client=object(), sleep=lambda _seconds: None)
    assert len(calls) == 12
    assert calls[:4] == ["one alpha", "one beta", "two alpha", "two beta"]
    assert all("abstract" not in row["results"][0] for row in report["rows"])
    assert report["limits"]["not_independent_gold"] is True
    with_abstracts = run(path, client=object(), sleep=lambda _seconds: None,
                         include_abstracts=True)
    assert with_abstracts["limits"]["abstracts_archived"] is True
    assert with_abstracts["rows"][0]["results"][0]["abstract"] == "not archived"


def test_frozen_live_probe_recovers_known_anchor_without_claiming_quality():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "outputs/frozen-openalex-synonym-branches-2026-09-27-v1.json")
                        .read_text(encoding="utf-8"))
    assert report["limits"]["not_independent_gold"] is True
    assert len(report["rows"]) == 22
    assert all(row["status"] == "succeeded" for row in report["rows"])
    archaeology = [row for row in report["rows"]
                   if row["case_id"] == "outside-quantum-archaeology"]
    matches = [(row["search_query"], work["rank_in_first_page"])
               for row in archaeology for work in row["results"]
               if work["openalex_id"] == "https://openalex.org/W4408486675"]
    assert matches == [("quantum gravity archaeology", 12),
                       ("quantum gravity archaeological", 12)]
