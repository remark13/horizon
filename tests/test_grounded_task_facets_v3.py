import json
from pathlib import Path

import pytest

from scripts.probe_grounded_task_facets_v3 import (
    _prompt, _validate, run, source_spans,
)
from scripts.probe_grounded_task_facets import _items


CONFIG = Path("config/grounded-task-facets-sentence-id-development-v3.json")
HOLDOUT = Path("config/grounded-task-facets-sentence-id-holdout-v3.json")


def test_sentence_ids_resolve_to_exact_source_text_and_hide_developer_label():
    config = json.loads(CONFIG.read_text())
    items = _items(config)
    assert len(items) == 12
    spans = source_spans(items[0])
    assert spans["T"] == items[0]["title"]
    assert any(key.startswith("A") for key in spans)
    prompt = _prompt(spans, config["allowed_task_families"])
    assert "developer_task_family" not in prompt
    assert "[T] " + items[0]["title"] in prompt


def test_new_holdout_papers_are_not_in_earlier_facet_probes():
    frozen = json.loads(HOLDOUT.read_text())
    rows = _items(frozen)
    assert len(rows) == 12
    assert sum(row["developer_task_family"] == "other_or_unclear"
               for row in rows) == 6
    prior = set()
    for path in (Path("config/grounded-task-facets-pilot.v1.json"),
                 Path("config/grounded-task-facets-holdout.v2.json")):
        prior.update((row["source"], row["id"])
                     for row in json.loads(path.read_text())["rows"])
    assert not prior.intersection((row["source"], row["id"]) for row in rows)


def test_sentence_id_validator_rejects_unknown_or_unbacked_ids():
    spans = {"T": "A method for UAV localization",
             "A1": "We estimate the UAV position against aerial maps."}
    value = {"task_family": "uav_self_localization", "research_object": "UAV",
             "task_evidence_id": "A1", "method_name": "unknown",
             "method_evidence_id": "none"}
    result = _validate({"response": json.dumps(value)}, spans,
                       ["uav_self_localization"])
    assert result["task_evidence_text"] == spans["A1"]
    value["task_evidence_id"] = "A9"
    with pytest.raises(ValueError, match="outside supplied source"):
        _validate({"response": json.dumps(value)}, spans,
                  ["uav_self_localization"])
    value["task_evidence_id"] = "T"
    value["method_name"] = "map matching"
    with pytest.raises(ValueError, match="unknown method"):
        _validate({"response": json.dumps(value)}, spans,
                  ["uav_self_localization"])


def test_structurally_valid_citations_alone_do_not_pass_task_gate():
    def post(_url, _payload, timeout):
        return {"response": json.dumps({
            "task_family": "other_or_unclear", "research_object": "paper",
            "task_evidence_id": "T", "method_name": "unknown",
            "method_evidence_id": "none",
        })}

    report = run(CONFIG, api_root="http://127.0.0.1:11434",
                 post=post, model_digest=lambda *_: "fixed-test-digest")
    assert report["counts"]["valid_source_ids"] == 12
    assert report["counts"]["task_family_agreement_with_developer"] < 10
    assert report["gate_passed"] is False
    assert report["production_changed"] is False
    assert report["production_ready"] is False
