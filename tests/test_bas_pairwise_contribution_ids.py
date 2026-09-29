import json

import pytest

from scripts.probe_bas_pairwise_contribution_ids import (
    _prompt, _validate, contribution_ids,
)


def test_contribution_ids_exclude_background_and_keep_exact_source():
    spans = {"T": "UAV localization study",
             "A1": "Earlier approaches estimated the position from a map.",
             "A2": "This paper presents a new self-positioning method for UAVs."}
    assert contribution_ids(spans) == ["A2"]
    prompt = _prompt(spans, spans, ["A2"], ["A2"])
    assert "Earlier approaches" in prompt
    assert "Allowed contribution IDs A: A2" in prompt


def test_pair_validation_rejects_background_id_even_if_source_text_exists():
    spans = {"T": "UAV localization study",
             "A1": "Earlier approaches estimated the position from a map.",
             "A2": "This paper presents a new self-positioning method for UAVs."}
    response = {"same_task": True, "mechanism_relation": "different",
                "evidence_id_a": "A2", "evidence_id_b": "A2",
                "reason": "Both estimate position but use different methods."}
    value = _validate({"response": json.dumps(response)}, spans, spans,
                      ["A2"], ["A2"])
    assert value["evidence_text_a"] == spans["A2"]
    response["evidence_id_b"] = "A1"
    with pytest.raises(ValueError, match="Invalid or unsupported"):
        _validate({"response": json.dumps(response)}, spans, spans,
                  ["A2"], ["A2"])
