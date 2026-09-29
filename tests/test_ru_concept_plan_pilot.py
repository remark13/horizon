import json
from pathlib import Path

import pytest

from saia.ru_concept_plan_pilot import _cases_from_config, _parse, _validation, _validation_v2, _validation_v3, _validation_v4


def _proposal():
    return {"translation_en": "Long-range fixed-wing unmanned aircraft",
            "concept_groups": [
                {"source_span_ru": "самолётного типа",
                 "alternatives_en": ["fixed-wing UAV", "fixed-wing unmanned aircraft"]},
                {"source_span_ru": "большой дальности",
                 "alternatives_en": ["long-range"]}],
            "ambiguities_ru": [], "needs_human_review": False}


def test_parse_and_structural_check_do_not_approve_model_plan():
    query = "Беспилотники самолётного типа большой дальности"
    proposal = _parse({"response": json.dumps(_proposal(), ensure_ascii=False)})
    result = _validation(proposal, query)
    assert result["source_span_check_passed"] is True
    assert result["index_plan_shape_supported"] is True
    assert result["not_approved_for_execution"] is True


def test_source_span_catches_added_concept_without_claiming_translation_correctness():
    proposal = _proposal()
    proposal["concept_groups"][1]["source_span_ru"] = "автономное управление"
    result = _validation(proposal, "Беспилотники самолётного типа большой дальности")
    assert result["source_span_check_passed"] is False
    assert "group_2_source_span_not_exact_query_substring" in result["issues"]


def test_single_group_or_unsafe_phrase_stays_unexecutable():
    proposal = _proposal()
    proposal["concept_groups"] = [{"source_span_ru": "Беспилотники",
                                   "alternatives_en": ["UAV"]}]
    result = _validation(proposal, "Беспилотники самолётного типа")
    assert not result["index_plan_shape_supported"]
    assert "concept_search_shape_unsupported_or_single_group" in result["issues"]
    with pytest.raises(ValueError):
        _parse({"response": json.dumps({**proposal, "extra": True})})


@pytest.mark.parametrize("phrase", ["for AI workloads", "from food packaging",
                                      "using laser", "extraction and separation",
                                      "one two three four five six seven"])
def test_v2_flags_poor_literal_search_phrase(phrase):
    proposal = _proposal()
    proposal["concept_groups"][0]["alternatives_en"] = [phrase]
    result = _validation_v2(
        proposal, "Беспилотники самолётного типа большой дальности")
    assert "group_1_poor_literal_search_phrase" in result["issues"]
    assert result["phrase_shape_check_is_not_relevance_validation"] is True


def test_v2_allows_atomic_phrases_but_does_not_approve_them():
    result = _validation_v2(
        _proposal(), "Беспилотники самолётного типа большой дальности")
    assert result["issues"] == []
    assert result["not_approved_for_execution"] is True


def test_frozen_cross_domain_cases_retain_original_russian_text_and_roles():
    cases, version = _cases_from_config({
        "version": "goal-cross-domain-compound-pilot-v1",
        "cases": [{"case_id": "outside", "role": "outside_priority_map",
                   "query_ru": "Квантовые сенсоры для археологии",
                   "concept_groups": [["quantum sensor"], ["archaeology"]]}],
    })
    assert version == "goal-cross-domain-compound-pilot-v1"
    assert cases == [{"id": "outside", "role": "outside_priority_map",
                      "query_ru": "Квантовые сенсоры для археологии"}]
    with pytest.raises(ValueError, match="duplicate"):
        _cases_from_config({"version": "ru-concept-plan-pilot-v1",
                            "cases": [cases[0], cases[0]]})


def test_v3_rejects_lost_explicit_acronym_without_changing_v2_report_policy():
    proposal = {"translation_en": "Quantum-inspired model compression on edge",
                "concept_groups": [
                    {"source_span_ru": "сжатие моделей", "alternatives_en": ["model compression"]},
                    {"source_span_ru": "LLM на edge", "alternatives_en": ["edge deployment"]},
                ], "ambiguities_ru": [], "needs_human_review": False}
    query = "Квантово-инспирированное сжатие моделей для запуска LLM на edge"
    assert _validation_v2(proposal, query)["issues"] == []
    assert "explicit_acronym_LLM_not_preserved" in _validation_v3(proposal, query)["issues"]


def test_holdout_queries_are_exact_second_catalog_rows():
    path = Path(__file__).resolve().parents[1] / "config/ru-concept-holdout-16-v1.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    cases, version = _cases_from_config(config)
    assert version == "ru-concept-plan-holdout-v1"
    assert len(cases) == 16
    assert {case["role"] for case in cases} == {
        "national_search_area", "customer_supplied_example",
    }
    changed = {**config, "cases": [*config["cases"]]}
    changed["cases"][0] = {**changed["cases"][0], "query_ru": "подменённый запрос"}
    with pytest.raises(ValueError, match="Holdout cases differ"):
        _cases_from_config(changed)


def test_v4_allows_exact_english_user_term_but_not_unrelated_connector_phrase():
    proposal = {"translation_en": "Identity and access management for AI agents",
                "concept_groups": [
                    {"source_span_ru": "Identity & access management",
                     "alternatives_en": ["identity and access management", "IAM"]},
                    {"source_span_ru": "ИИ-агентов", "alternatives_en": ["AI agents"]},
                ], "ambiguities_ru": [], "needs_human_review": False}
    query = "Identity & access management для ИИ-агентов"
    assert "group_1_poor_literal_search_phrase" in _validation_v3(proposal, query)["issues"]
    result = _validation_v4(proposal, query)
    assert result["issues"] == []
    assert result["literalized_user_english_groups_accepted"] == [1]
    proposal["concept_groups"][0]["source_span_ru"] = "Извлечение и разделение"
    query = "Извлечение и разделение для ИИ-агентов"
    assert "group_1_poor_literal_search_phrase" in _validation_v4(proposal, query)["issues"]
    proposal["concept_groups"][0] = {
        "source_span_ru": "for robots", "alternatives_en": ["for robots"]}
    query = "for robots для ИИ-агентов"
    assert "group_1_poor_literal_search_phrase" in _validation_v4(proposal, query)["issues"]


def test_v4_never_silently_drops_a_negative_technology_constraint():
    proposal = {"translation_en": "Battery-powered NPU for IoT",
                "concept_groups": [
                    {"source_span_ru": "NPU", "alternatives_en": ["NPU"]},
                    {"source_span_ru": "IoT", "alternatives_en": ["IoT"]},
                ], "ambiguities_ru": [], "needs_human_review": False}
    result = _validation_v4(proposal, "NPU для IoT (не для AI PC)")
    assert "negative_scope_requires_explicit_exclusion" in result["issues"]
