import json
from pathlib import Path

import pytest

from scripts.probe_grounded_task_facets import _items, _prompt, _prompt_v2, _validate, run


CONFIG = Path("config/grounded-task-facets-pilot.v1.json")
HOLDOUT = Path("config/grounded-task-facets-holdout.v2.json")


def test_frozen_pilot_has_twelve_grounded_inputs():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    items = _items(config)
    assert len(items) == 12
    assert sum(row["source"] == "bas" for row in items) == 8
    assert sum(row["source"] == "cross" for row in items) == 4
    prompt = _prompt(items[0], config["allowed_task_families"])
    assert items[0]["abstract"] in prompt
    assert "developer_task_family" not in prompt


def test_holdout_is_disjoint_and_revised_prompt_keeps_labels_hidden():
    pilot = json.loads(CONFIG.read_text(encoding="utf-8"))
    holdout = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    pilot_keys = {(row["source"], row["id"]) for row in pilot["rows"]}
    holdout_keys = {(row["source"], row["id"]) for row in holdout["rows"]}
    assert len(holdout_keys) == 12
    assert not pilot_keys & holdout_keys
    items = _items(holdout)
    prompt = _prompt_v2(items[0], holdout["allowed_task_families"])
    assert items[0]["abstract"] in prompt
    assert "developer_task_family" not in prompt
    assert "OWN pose" in prompt


def test_facet_validation_rejects_paraphrased_quote():
    item = {"title": "Visual maps for UAV navigation",
            "abstract": "We propose matching aerial images to maps for self localization."}
    value = {"task_family": "uav_self_localization",
             "research_object": "UAV", "task_quote": "self positioning of aircraft",
             "method_name": "map matching",
             "method_quote": "matching aerial images to maps"}
    with pytest.raises(ValueError, match="Task quote"):
        _validate({"response": json.dumps(value)}, item,
                  ["uav_self_localization"])
    value["task_quote"] = "maps for self localization"
    assert _validate({"response": json.dumps(value)}, item,
                     ["uav_self_localization"]) == value


def test_mock_model_cannot_pass_gate_just_by_returning_valid_quotes():
    def post(_url, payload, timeout):
        title = payload["prompt"].split("Title: ", 1)[1].split("\nAbstract: ", 1)[0]
        words = title.split()
        quote = " ".join(words[:min(7, len(words))])
        return {"response": json.dumps({
            "task_family": "other_or_unclear", "research_object": "paper",
            "task_quote": quote, "method_name": "unknown", "method_quote": "",
        })}

    result = run(CONFIG, api_root="http://127.0.0.1:11434",
                 post=post, model_digest=lambda *_: "fixed-test-digest")
    assert result["counts"]["total"] == 12
    assert result["counts"]["task_family_agreement_with_developer"] == 0
    assert result["gate_passed"] is False
    assert result["production_changed"] is False
