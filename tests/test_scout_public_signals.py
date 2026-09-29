"""Presentation references must never become detector evidence or gold labels."""
from copy import deepcopy
from datetime import date
import json

import pytest
from fastapi.testclient import TestClient

from saia import scout_public_signals as public, scout_results, candidate_assessment
from saia.api import app
from saia.public_signal_brief import render
from saia.scout_web import SCOUT_WEB_HTML


def test_broad_russian_bas_matches_real_published_titles_not_all_aerospace():
    value = public.match("беспилотные авиационные системы", included_phrases=["UAV airframe"], today=date(2026, 9, 29))
    assert {row["title"] for row in value["records"]} == {"edge computing for UAV", "truck drone", "Bio-inspired flapping-wing drones"}
    assert all(row["scientific_score"] is None and row["current_weak_signal_verified"] is False for row in value["records"])
    assert value["not_gold_labels"] and value["scientific_results_modified"] is False
    assert value["model_called"] is False and value["network_requested"] is False


def test_russian_ai_uses_report_categories_only_for_exact_broad_query():
    broad = public.match("искусственный интеллект", today=date(2026, 9, 29))
    assert any(row["title"] == "large language models" for row in broad["records"])
    narrow = public.match("ИИ для управления роботом", included_phrases=["robot control"], today=date(2026, 9, 29))
    assert not any(row["matching_basis"] == "source_category_for_exact_broad_query" for row in narrow["records"])
    assert not any(row["title"] == "large language models" for row in narrow["records"])


def test_full_narrow_phrase_must_match_and_exclusion_is_honoured():
    value = public.match("UAV airframe", included_phrases=[])
    assert value["records"] == []  # UAV alone is not the requested airframe topic.
    value = public.match("drone", included_phrases=[], excluded_phrases=["truck drone"])
    assert {row["title"] for row in value["records"]} == {"edge computing for UAV", "Bio-inspired flapping-wing drones"}
    assert public.match("not-a-real-tech-phrase", included_phrases=[])["records"] == []


def test_old_duplicate_editions_are_not_mixed_into_current_citations():
    value = public.match("space air ground integrated network", included_phrases=[])
    assert len(value["records"]) == 1
    row = value["records"][0]
    assert len(row["references"]) == 1
    assert {source["source_id"] for source in row["references"]} == {"jrc_weak_signals_2024"}
    assert all(source["source_year"] >= 2023 for source in row["references"])
    assert row["source_date"] == "2025-02-17"
    assert len(row["reference_content_sha256"]) == 64
    assert public.title_key("language model") != public.title_key("language")


def test_future_catalog_entries_and_unknown_short_ru_queries_are_not_invented():
    value = public.match("robot", included_phrases=[], today=date(2026, 9, 1))
    assert all(row["source_date"] < "2026-09-15" for row in value["records"])
    assert public.match("неизвестная тема без перевода", included_phrases=[])["records"] == []


def test_science_rank_and_card_are_unchanged_when_public_rows_are_added():
    refs = public.match("drone", included_phrases=[])
    queue = [{"candidate_id": 7, "rank": 1, "card": {"candidate_id": 7, "label": "Scientific topic", "metrics": {"count": 12}}}]
    before = deepcopy(queue)
    result = public.join({"queue": queue}, refs)
    assert result["queue"] == before
    assert result["result_counts"]["saia_candidates"] == 1
    assert result["result_counts"]["public_signals"] == 3
    assert result["result_items"][0]["item_kind"] == "saia_candidate"
    assert all(item["assessment"] is None and "card" not in item for item in result["result_items"][1:])


def test_identical_title_attaches_reference_without_duplicate_or_score_bonus():
    refs = public.match("drone", included_phrases=[])
    row = next(row for row in refs["records"] if row["title"] == "truck drone")
    card = {"candidate_id": 8, "label": "Truck-drone", "metrics": {"count": 7}}
    before = deepcopy(card)
    result = public.join({"queue": [{"card": card, "rank": 1, "assessment": {"overall_score": 12}}]}, {**refs, "records": [row]})
    assert len(result["result_items"]) == 1
    assert result["queue"][0]["assessment"]["overall_score"] == 12
    assert card == before
    assert result["result_counts"]["public_references_attached_to_candidates"] == 1


def test_saved_scope_keeps_original_query_and_only_selected_phrases():
    value = public.scope_from_payload({"controlled_search_plan": {"original_query": "БАС", "included_terms": ["UAV airframe"], "exclusions": ["military"]}}, ["old term"])
    assert value == {"query": "БАС", "included_phrases": ["UAV airframe"], "excluded_phrases": ["military"]}


@pytest.mark.parametrize("value", ["", "invented", "a-b,a-b", "a/b", "a-b, bad-id", "a-b?x=1", 4])
def test_reference_identifiers_are_strict(value):
    with pytest.raises(ValueError):
        public.parse_ids(value)


def test_public_selection_cannot_export_or_dispatch_outside_saved_query(monkeypatch):
    refs = public.match("drone", included_phrases=[])
    monkeypatch.setattr(public, "for_packet", lambda packet: refs)
    assert public.select({}, [refs["records"][0]["id"]])[1][0]["id"] == refs["records"][0]["id"]
    with pytest.raises(ValueError):
        public.select({}, ["jrc-2024-p122-081"])


def test_public_brief_is_cited_escaped_without_fake_metrics_or_scripts():
    row = public.lookup("jrc-2024-p113-041")
    html = render(row)
    assert "Из публичного источника" in html and "#page=113" in html
    assert "балл и динамика не рассчитаны" in html.casefold()
    assert "<script" not in html
    row["title"] = '<script>alert("noise")</script>'
    assert "<script" not in render(row)
    response = TestClient(app).get("/public-signals/jrc-2024-p113-041/brief")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert TestClient(app).get("/public-signals/invented-record/brief").status_code == 422


def test_legacy_catalogue_and_archived_brief_remain_readable_without_current_merge():
    from pathlib import Path
    legacy = json.loads((Path(__file__).resolve().parents[1] / "data/reference/public_signals/catalog.v5.json").read_text())
    old = next(row for row in legacy["records"] if row["source_id"] == "jrc_weak_signals_2021")
    reference = {**old, "catalog_version": legacy["version"], "references": [deepcopy(old)]}
    before = deepcopy(reference)
    assert "Архивная подборка до 2023 года" in render(reference)
    assert "Год подборки: 2021" in render(reference)
    assert reference == before  # do not rewrite frozen expert dispatches
    assert public.lookup(old["id"])["archived"] is True
    assert all(row["source_year"] >= 2023 for row in public.match("искусственный интеллект")["records"])


def test_html_references_have_year_type_and_section_not_fake_pdf_pages():
    row = public.lookup("kribb-2025-07")
    html = render(row)
    assert "Год подборки: 2025" in html and "Перспективная технология" in html
    assert "Таблица: технология 7" in html and "Точный день публикации не указан" in html
    assert "страница None" not in html and "2025-01-01" not in html
    wef = public.lookup("wef-2026-09")
    assert wef["scientific_score"] is None and wef["source_type"] == "technology_selection"
    assert public.match("модели мира", included_phrases=[])["records"][0]["id"] == "wef-2026-09"


def test_ui_never_enriches_or_scores_a_public_identifier_as_scientific():
    assert 'id="origin"' in SCOUT_WEB_HTML
    assert "data-public-open" in SCOUT_WEB_HTML and "data-public-select" in SCOUT_WEB_HTML
    assert "publicIds=[...(selection?.publicIds??state.selectedPublic)]" in SCOUT_WEB_HTML
    assert "public_signal_ids:publicIds" in SCOUT_WEB_HTML
    assert ".filter(q=>!isPublic(q)).map(q=>q.card.candidate_id)" in SCOUT_WEB_HTML
    assert "for(const q of all()){if(isPublic(q))continue" in SCOUT_WEB_HTML
    assert "const items=(state.packet?.queue||[]).slice(0,30)" in SCOUT_WEB_HTML
    assert "include_scientific','false'" in SCOUT_WEB_HTML
    assert "Из публичного источника" in SCOUT_WEB_HTML
    assert "fallback.href=path" in SCOUT_WEB_HTML and "fallback.textContent='Скачать файл'" in SCOUT_WEB_HTML
