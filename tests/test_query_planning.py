import uuid

import pytest

from saia.query_planning import preview
from saia.query_plan_store import validate_approval, verify_payload


def test_specific_ai_request_is_not_expanded_to_every_generic_ai_branch():
    for query in (
        "Нейроморфные чипы для ИИ-нагрузок",
        "ИИ в медицинской диагностике",
        "Искусственный интеллект в медицинской диагностике",
        "Беспилотные авиационные системы для мониторинга пожаров",
    ):
        plan = preview(query)
        assert plan["suggestions"] == []
        assert plan["original_query"] == query
        assert plan["status"] == "original_query_only"


def test_broad_ai_request_still_offers_controlled_ai_subareas():
    assert len(preview("ИИ")["suggestions"]) == 8
    assert len(preview("технологии в ИИ")["suggestions"]) == 8


def test_broad_known_query_keeps_original_and_only_suggests_unselected_branches():
    plan = preview("новые материалы")
    assert plan["original_query"] == "новые материалы"
    assert plan["original_query_preserved"] is True
    assert plan["automatic_execution"] is False
    assert plan["automatic_query_replacement"] is False
    assert len(plan["matched_profiles"]) == 1
    assert len(plan["suggestions"]) == 8
    assert all(item["selected"] is False for item in plan["suggestions"])
    assert all(item["requires_user_confirmation"] is True for item in plan["suggestions"])


def test_known_subarea_suggests_only_that_branch_without_claiming_translation():
    plan = preview("тканевая инженерия")
    assert [item["suggestion_id"] for item in plan["suggestions"]] == [
        "health-preservation/tissue-engineering"
    ]
    assert "не являются переводом" in plan["interpretation"]


def test_unknown_free_query_remains_valid_without_invented_profile():
    plan = preview("quantum error correction")
    assert plan["status"] == "original_query_only"
    assert plan["matched_profiles"] == []
    assert plan["suggestions"] == []
    assert plan["original_query_preserved"] is True


def test_exact_robot_manipulation_bridge_preserves_scope_and_needs_confirmation():
    shown = preview("Роботизированные манипуляции")
    assert shown["original_query"] == "Роботизированные манипуляции"
    assert [row["suggestion_id"] for row in shown["suggestions"]] == [
        "robotics/robotic-manipulation-broad"]
    suggestion = shown["suggestions"][0]
    assert suggestion["phrases_en"] == [
        "robotic manipulation", "robot manipulation", "robot arm manipulation"]
    assert suggestion["selected"] is False
    assert suggestion["requires_user_confirmation"] is True
    assert preview("Роботизированные манипуляции в хирургии")["suggestions"] == []
    approved = validate_approval(
        shown["original_query"], 12, shown["plan_payload_sha256"],
        [suggestion["suggestion_id"]], "test-scout", str(uuid.uuid4()))
    assert approved["branches"][0]["query"] == shown["original_query"]
    assert approved["branches"][1]["query"] == "robotic manipulation"


def test_explicit_approval_preserves_original_and_only_accepts_offered_branches():
    shown = preview("новые материалы", 12)
    selected = [shown["suggestions"][0]["suggestion_id"]]
    payload = validate_approval(
        "новые материалы", 12, shown["plan_payload_sha256"], selected,
        "test-analyst", str(uuid.uuid4()),
    )
    assert payload["branches"][0] == {
        "branch_id": "original-query", "query": "новые материалы",
        "origin": "user_query", "mandatory": True,
    }
    assert payload["selected_branch_ids"] == selected
    assert payload["automatic_execution"] is False
    assert payload["scientific_result"] is False
    assert verify_payload(payload) == payload


def test_approval_rejects_stale_preview_unoffered_and_duplicate_branches():
    shown = preview("новые материалы", 12)
    common = ("новые материалы", 12, shown["plan_payload_sha256"])
    with pytest.raises(ValueError, match="Предпросмотр изменился"):
        validate_approval(
            common[0], common[1], "0" * 64, [], "reviewer", str(uuid.uuid4()))
    with pytest.raises(ValueError, match="не было"):
        validate_approval(
            *common, ["invented/branch"], "reviewer", str(uuid.uuid4()))
    branch = shown["suggestions"][0]["suggestion_id"]
    with pytest.raises(ValueError, match="дважды"):
        validate_approval(
            *common, [branch, branch], "reviewer", str(uuid.uuid4()))


def test_original_only_query_can_be_explicitly_approved_without_invented_expansion():
    shown = preview("quantum error correction")
    payload = validate_approval(
        shown["original_query"], 12, shown["plan_payload_sha256"], [],
        "reviewer", str(uuid.uuid4()),
    )
    assert [branch["branch_id"] for branch in payload["branches"]] == ["original-query"]


@pytest.mark.parametrize("query", [
    "Индустриальный ИИ", "Инфраструктура ИИ", "Роботы", "Финтех",
    "Защита ИИ", "Edge",
])
def test_customer_direction_has_explicit_scientific_search_branches(query):
    shown = preview(query)
    assert shown["original_query"] == query
    assert shown["status"] == "suggestions_available"
    assert 5 <= len(shown["suggestions"]) <= 12
    assert all(item["requires_user_confirmation"] is True
               and item["selected"] is False
               and 1 <= len(item["phrases_en"]) <= 10
               for item in shown["suggestions"])
    selected = [shown["suggestions"][0]["suggestion_id"]]
    approved = validate_approval(
        query, 12, shown["plan_payload_sha256"], selected,
        "test-scout", str(uuid.uuid4()),
    )
    assert approved["branches"][0]["query"] == query
    assert approved["branches"][1]["branch_id"] == selected[0]


def test_robotics_preview_offers_concept_phrase_without_requiring_model_suffix():
    shown = preview("Роботы")
    branch = next(item for item in shown["suggestions"]
                  if item["suggestion_id"] == "robotics/robot-foundation-models")
    assert "vision language action" in branch["phrases_en"]


def test_unknown_russian_free_query_can_still_be_approved_as_original():
    query = "необычная технология без словаря"
    shown = preview(query)
    assert shown["status"] == "original_query_only"
    approved = validate_approval(
        query, 12, shown["plan_payload_sha256"], [],
        "test-scout", str(uuid.uuid4()),
    )
    assert [branch["branch_id"] for branch in approved["branches"]] == ["original-query"]


def test_free_form_ai_abbreviation_offers_broad_internal_topics_without_replacement():
    shown = preview("технологии в ИИ")
    assert shown["original_query"] == "технологии в ИИ"
    assert shown["status"] == "suggestions_available"
    assert len(shown["suggestions"]) >= 5
    assert all(item["suggestion_id"].startswith("artificial-intelligence/")
               for item in shown["suggestions"])


def test_broad_ai_uses_short_alternative_phrases_not_concatenated_query():
    shown = preview("Искусственный интеллект")
    assert len(shown["suggestions"]) == 8
    for item in shown["suggestions"]:
        assert 2 <= len(item["phrases_en"]) <= 3
        assert item["query_en"] not in item["phrases_en"] or len(
            item["query_en"].split()) <= 4
    assert "large language model" in shown["suggestions"][0]["phrases_en"]


def test_fintech_suggestions_fit_compiled_branch_limit():
    shown = preview("Финтех", max_suggestions=12)
    assert len(shown["suggestions"]) == 11
    assert len(shown["suggestions"]) + 1 <= 12  # mandatory original branch
    assert any(item["suggestion_id"] == "fintech/digital-money"
               for item in shown["suggestions"])
