import json

import pytest

from scripts.probe_title_anchor_paper_llm import parse_response, prompt


CASE = {"proposed_line_en": "task allocation",
        "paper": {"title": "Multi-UAV Task Allocation",
                  "abstract": "We propose a multi-UAV assignment framework."}}


def test_probe_prompt_keeps_line_and_own_contribution_separate():
    text = prompt(CASE)
    assert "Proposed line: task allocation" in text
    assert "OWN proposed method" in text
    assert "market or application label" in text


def test_probe_requires_verbatim_quote_and_categorical_answer():
    answer = {"own_result": "yes", "bas_core": "yes",
              "role": "primary_result",
              "source_quote": "multi-UAV assignment framework",
              "reason": "The own method concerns multi-UAV assignment."}
    assert parse_response({"response": json.dumps(answer)}, CASE) == answer
    answer["source_quote"] = "invented primary result"
    with pytest.raises(ValueError, match="Ungrounded"):
        parse_response({"response": json.dumps(answer)}, CASE)
