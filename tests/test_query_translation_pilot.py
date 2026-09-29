import json

import pytest

from saia.query_translation_pilot import _validated_response


def test_translation_response_is_strictly_parsed_and_not_scored():
    value = _validated_response({"response": json.dumps({
        "translation_en": "  sodium-ion battery materials ",
        "core_phrases_en": ["sodium-ion batteries", "sodium-ion batteries"],
        "uncertainty": "none",
    })})
    assert value["core_phrases_en"] == ["sodium-ion batteries"]
    assert value["translation_en"] == "sodium-ion battery materials"
    with pytest.raises(ValueError):
        _validated_response({"response": json.dumps({
            "translation_en": "invented", "core_phrases_en": [],
            "uncertainty": "", "confidence": 100,
        })})
