import json

import pytest

from saia.priority_relevance_llm import _choose, _parse


def test_choose_bounded_per_topic_without_using_labels():
    rows = [{"item_id": name, "target_topic": topic}
            for name, topic in [("a1", "A"), ("a2", "A"), ("b1", "B"),
                                ("a3", "A"), ("b2", "B")]]
    assert [row["item_id"] for row in _choose(rows, 2)] == ["a1", "a2", "b1", "b2"]
    assert _choose(rows, None) == rows
    with pytest.raises(ValueError):
        _choose(rows, 7)


def test_parse_rejects_unstructured_or_invalid_label():
    valid = {"label": "partial", "reason": "The essential qualifier is absent.",
             "missing_qualifiers": ["edge deployment"]}
    assert _parse({"response": json.dumps(valid)}) == valid
    with pytest.raises(ValueError):
        _parse({"response": json.dumps({**valid, "label": "confirmed_signal"})})
    with pytest.raises(ValueError):
        _parse({"response": json.dumps({**valid, "extra": True})})
