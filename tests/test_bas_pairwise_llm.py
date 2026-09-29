import json
from pathlib import Path

import pytest

from scripts.probe_bas_pairwise_llm import _prompt, _protocol, _validate, run


CONFIG = Path("config/bas-pairwise-llm.v1.json")


def test_frozen_pair_prompt_does_not_expose_developer_labels():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    _pair_config, _source, works, pairs = _protocol(config)
    assert len(pairs) == 20
    text = _prompt(works[pairs[0]["a"]], works[pairs[0]["b"]])
    assert works[pairs[0]["a"]]["abstract"] in text
    assert works[pairs[0]["b"]]["abstract"] in text
    assert "developer_same_task" not in text
    assert "same specific task" in text


def test_pair_validation_checks_each_quote_against_its_own_paper():
    a = {"title": "UAV self localization by map matching", "abstract": ""}
    b = {"title": "Locating target objects in aerial imagery", "abstract": ""}
    value = {"same_task": False, "task_quote_a": "UAV self localization by map matching",
             "task_quote_b": "Locating target objects in aerial imagery",
             "reason": "Different objects are localized."}
    assert _validate({"response": json.dumps(value)}, a, b) == value
    value["task_quote_b"] = value["task_quote_a"]
    with pytest.raises(ValueError, match="task_quote_b"):
        _validate({"response": json.dumps(value)}, a, b)


def test_mock_model_cannot_pass_gate_with_generic_negative_answers():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    _pair_config, _source, works, pairs = _protocol(config)
    titles = {work["title"]: work for work in works.values()}

    def post(_url, payload, timeout):
        prompt = payload["prompt"]
        a_title = prompt.split("Paper A title: ", 1)[1].split("\nPaper A abstract:", 1)[0]
        b_title = prompt.split("Paper B title: ", 1)[1].split("\nPaper B abstract:", 1)[0]
        assert a_title in titles and b_title in titles
        return {"response": json.dumps({
            "same_task": False,
            "task_quote_a": " ".join(a_title.split()[:5]),
            "task_quote_b": " ".join(b_title.split()[:5]),
            "reason": "Mock answer.",
        })}

    result = run(CONFIG, api_root="http://127.0.0.1:11434", post=post,
                 model_digest=lambda *_: "test-digest")
    assert len(result["rows"]) == len(pairs) == 20
    assert result["gate_passed"] is False
    assert result["production_changed"] is False
