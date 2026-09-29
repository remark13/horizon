import json

import pytest

from saia.ru_core_qualifier_pilot import _parse, _validation


def _proposal():
    return {"translation_en": "LLM compression on edge hardware",
            "core_groups": [{"source_span_ru": "сжатие моделей",
                             "alternatives_en": ["model compression"]}],
            "qualifier_groups": [
                {"source_span_ru": "LLM", "alternatives_en": ["LLM"]},
                {"source_span_ru": "edge-железе",
                 "alternatives_en": ["edge hardware"]}],
            "unresolved_ru": []}


def test_core_and_qualifier_roles_are_separate_and_unapproved():
    query = "сжатие моделей для запуска LLM на edge-железе"
    proposal = _parse({"response": json.dumps(_proposal(), ensure_ascii=False)})
    validation = _validation(proposal, query)
    assert validation["issues"] == []
    assert validation["qualifiers_remain_mandatory_for_relevance"] is True
    assert validation["not_approved_for_execution"] is True


def test_missing_source_acronym_is_flagged():
    proposal = _proposal()
    proposal["qualifier_groups"] = proposal["qualifier_groups"][1:]
    validation = _validation(proposal, "сжатие моделей для запуска LLM на edge-железе")
    assert validation["missing_source_acronyms"] == ["LLM"]
    assert "source_acronyms_missing_from_plan_or_unresolved" in validation["issues"]


def test_added_or_noncontiguous_ru_span_is_flagged():
    proposal = _proposal()
    proposal["core_groups"][0]["source_span_ru"] = "ускорение моделей"
    validation = _validation(proposal, "сжатие моделей для запуска LLM на edge-железе")
    assert "group_1_source_span_not_exact_query_substring" in validation["issues"]
    with pytest.raises(ValueError):
        _parse({"response": json.dumps({**proposal, "extra": True})})


def test_long_core_clause_is_not_silently_approved():
    proposal = _proposal()
    proposal["core_groups"][0]["alternatives_en"] = [
        "running compressed language models on edge hardware"]
    result = _validation(proposal, "сжатие моделей для запуска LLM на edge-железе")
    assert "group_1_core_is_clause_or_overloaded" in result["issues"]
