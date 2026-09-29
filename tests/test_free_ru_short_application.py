import json
from pathlib import Path

from scripts.propose_free_ru_short_application import _validate, run
from scripts.probe_free_ru_short_application import run as probe_run


def test_short_application_rejects_ungrounded_or_verbose_terms():
    query = "Квантовые гравиметры для археологических исследований"
    response = {
        "literal_translation_en": "Quantum gravimeters for archaeological research",
        "variants": [{
            "technology_source_span_ru": "Квантовые гравиметры",
            "technology_noun_en": "quantum gravimeter",
            "application_source_span_ru": "археологических исследований",
            "application_noun_en": "archaeology",
        }],
        "unresolved_source_spans_ru": [],
    }
    assert _validate(response, query) == []
    response["variants"][0]["application_source_span_ru"] = "космических исследований"
    assert "variant_1_application_source_span_ru_ungrounded" in _validate(response, query)
    response["variants"][0]["application_noun_en"] = "a much too long phrase for an application noun"
    assert "variant_1_application_noun_en_not_short" in _validate(response, query)


def test_short_application_frozen_report(monkeypatch, tmp_path: Path):
    config = {"version": "free-ru-short-application-pilot-v1",
              "cases": [{"case_id": f"c{i}", "role": "holdout", "query_ru": "Датчики для воды"}
                        for i in range(6)],
              "predeclared_gates": {"max_english_search_variants_per_case": 2}}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    proposal = {"literal_translation_en": "Sensors for water", "variants": [],
                "unresolved_source_spans_ru": []}
    report = run(path, post=lambda *_args, **_kwargs: {"response": json.dumps(proposal)},
                 model_digest=lambda *_args: "frozen-digest")
    assert len(report["rows"]) == 6
    assert all(row["structural_issues"] == [] for row in report["rows"])
    assert report["predeclared_gates"]["max_english_search_variants_per_case"] == 2


def test_short_application_probe_skips_invalid_before_source(monkeypatch, tmp_path: Path):
    import scripts.probe_free_ru_short_application as probe

    seen = []
    monkeypatch.setattr(probe, "_one", lambda _client, *, query, **_kw: (
        seen.append(query) or {"status": "succeeded", "results": []}))
    data = {
        "version": "free-ru-short-application-proposal-v1", "model_digest": "fixed",
        "predeclared_gates": {"max_english_search_variants_per_case": 2},
        "rows": [
            {"case_id": f"c{i}", "role": "holdout", "query_ru": "Запрос",
             "status": "parsed", "structural_issues": ["invalid"] if i == 0 else [],
             "proposal_unreviewed": {"variants": [{
                 "technology_source_span_ru": "Запрос", "technology_noun_en": "battery",
                 "application_source_span_ru": "Запрос", "application_noun_en": "transport",
             }] if i == 1 else []}}
            for i in range(6)],
    }
    path = tmp_path / "proposals.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    report = probe_run(path, client=object(), sleep=lambda _seconds: None)
    assert seen == ["battery transport"]
    assert report["rows"][0]["status"] == "skipped_invalid"
    assert report["rows"][1]["search_query"] == "battery transport"
