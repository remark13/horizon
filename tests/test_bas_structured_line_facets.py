import pytest

from scripts.probe_bas_structured_line_facets import schema, validate


def test_structured_facets_resolve_source_ids_without_model_quotes():
    spans = {"A1": "We propose a visual-inertial estimator for UAV pose."}
    payload = {"object": "UAV pose", "object_id": "A1",
               "task": "estimate pose", "task_id": "A1",
               "mechanism": "visual-inertial fusion", "mechanism_id": "A1",
               "role": "primary_result", "role_id": "A1"}
    result = validate({"response": __import__("json").dumps(payload)}, spans)
    assert result["source_sentences"]["mechanism"] == spans["A1"]
    assert result["role_source_sentence"] == spans["A1"]
    assert "T" not in schema(spans)["properties"]["object_id"]["enum"]


def test_structured_facets_allow_explicit_unknown_without_inventing_evidence():
    import json
    spans = {"A1": "We present a study with an unspecified method."}
    payload = {"object": "unknown", "object_id": "none",
               "task": "unknown", "task_id": "none",
               "mechanism": "unknown", "mechanism_id": "none",
               "role": "unclear", "role_id": "none"}
    result = validate({"response": json.dumps(payload)}, spans)
    assert all(value is None for value in result["source_sentences"].values())


@pytest.mark.parametrize("field,value", [
    ("task_id", "T"), ("mechanism_id", "none"),
    ("mechanism", "unknown"), ("role", "invented")])
def test_structured_facets_reject_invalid_or_ungrounded_fields(field, value):
    import json
    spans = {"A1": "We propose a visual-inertial estimator for UAV pose."}
    payload = {"object": "UAV pose", "object_id": "A1",
               "task": "estimate pose", "task_id": "A1",
               "mechanism": "visual-inertial fusion", "mechanism_id": "A1",
               "role": "primary_result", "role_id": "A1"}
    payload[field] = value
    with pytest.raises(ValueError):
        validate({"response": json.dumps(payload)}, spans)
