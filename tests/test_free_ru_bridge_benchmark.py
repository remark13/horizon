import json

import pytest

from scripts import benchmark_free_ru_query_bridge as module


def test_benchmark_keeps_model_proposal_diagnostic_and_output_immutable(tmp_path, monkeypatch):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({
        "version": "free-ru-query-bridge-pilot-v1",
        "frozen_before_run": True,
        "date_from": "2021-09-01", "as_of_date": "2026-09-01",
        "cases": [{"role": "outside", "query_ru": "акустическая левитация",
                   "reference_en": "acoustic levitation"}],
    }), encoding="utf-8")
    index = tmp_path / "index"
    index.mkdir()
    (index / "manifest.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "result.json"
    monkeypatch.setattr(module, "_model_digest", lambda *_: "pinned-digest")
    monkeypatch.setattr(module, "_post", lambda *_args, **_kwargs: {
        "response": json.dumps({"translation_en": "acoustic levitation",
                                "core_phrases_en": ["acoustic levitation"],
                                "uncertainty": ""})})
    monkeypatch.setattr(module, "supports_plan", lambda plan: plan["included_terms"][0].isascii())
    monkeypatch.setattr(module, "exact_search", lambda *_args: {
        "audit": {"exact_unique_ids": 5}})

    report = module.benchmark(cases_path=cases, index_dir=index,
                              model="local-test", output=output)
    assert report["model_digest"] == "pinned-digest"
    assert report["cases"][0]["observations"][0]["supported"] is False
    assert report["cases"][0]["observations"][1]["exact_unique_ids"] == 5
    assert "not executed in production" in " ".join(report["limitations"])
    with pytest.raises(FileExistsError):
        module.benchmark(cases_path=cases, index_dir=index,
                         model="local-test", output=output)
