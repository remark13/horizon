import json
from pathlib import Path

from scripts.probe_free_ru_subtype_openalex import run


def test_subtype_probe_keeps_all_other_required_concepts(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    seen = []

    def fake_one(_client, **kwargs):
        seen.append(kwargs["query"])
        return {"status": "succeeded", "results": [{"openalex_id": "W1",
                "abstract": "not archived"}]}

    monkeypatch.setattr("scripts.probe_free_ru_subtype_openalex._one", fake_one)
    report = run(root / "outputs/free-ru-subtype-retrieval-proposals-2026-09-27-v1.json",
                 client=object(), sleep=lambda _seconds: None)
    assert len(report["rows"]) == 8
    assert any("Cold-atom gravimeters" in query and "archaeological research" in query
               for query in seen)
    assert all("hydrogen leaks" in query and "pipelines" in query
               for query in seen if "archaeological research" not in query)
    assert all("abstract" not in row["results"][0] for row in report["rows"])
    assert report["limits"]["not_executed_in_user_route"] is True


def test_saved_subtype_probe_does_not_masquerade_as_anchor_recovery():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "outputs/free-ru-subtype-openalex-probe-2026-09-27-v1.json")
                        .read_text(encoding="utf-8"))
    assert len(report["rows"]) == 8
    assert all(row["status"] == "succeeded" for row in report["rows"])
    assert not any(work["openalex_id"] == "https://openalex.org/W4408486675"
                   for row in report["rows"] for work in row["results"])
    assert report["limits"]["model_subtypes_not_semantically_verified"] is True
    assert report["limits"]["not_independent_retrieval_or_signal_accuracy"] is True
