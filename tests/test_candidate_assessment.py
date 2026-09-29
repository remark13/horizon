"""Contract examples are synthetic; never inserted as live signal evidence."""
import copy
from datetime import date

import pytest
from fastapi.testclient import TestClient

from saia import candidate_assessment as assessment, scout_results, source_context
from saia.api import app
from saia.hybrid import digest
from saia.scout_web import SCOUT_WEB_HTML

TODAY = date(2026, 9, 29)


def card(identifier=7, label="Vision-language-action models"):
    return {"candidate_id": identifier, "label": label, "composition_sha256": "a" * 64, "status": "watch",
            "gates": [{"gate": "G4_momentum", "severity": "block", "passed": True, "observed": 70, "threshold": 65}],
            "metrics": {"observed": {"doc_count": 12}, "normalized": {"momentum": .7, "novelty": .8, "persistence": .6, "independent_diffusion": None},
                        "publication_series": {"coverage_comparable": True, "share_slope_per_window": .01,
                            "points": [{"start": "2026-01-01", "topic_works": 2, "share": .02, "complete": True},
                                       {"start": "2026-04-01", "topic_works": 4, "share": .03, "complete": True},
                                       {"start": "2026-07-01", "topic_works": 1, "share": .01, "complete": False}]}}}


def material(axis="commercial", title="Vision language action models reach factories", **changes):
    return {"group": axis, "source": "google_news_rss" if axis == "commercial" else "epo_ops" if axis == "patents" else "dealroom_public_rounds",
            "title_original": title, "url": "https://example.org/story", "record_date": "2026-09-20", "date_kind": "publication",
            "observation_id": "observation", "publisher": "Research News", **changes}


def context(*materials, status="complete", **changes):
    return {"binding": {"composition_sha256": "a" * 64}, "reports": [{"status": status, "materials": list(materials), **changes}]}


def calc(*rows, **kwargs):
    return assessment.calculate(card(), context(*rows), today=TODAY, **kwargs)


def test_enrichment_api_rejects_boolean_candidate_id_before_source_calls():
    response = TestClient(app).post('/scout-results/m/enrich?score_run_id=12', json={"candidate_ids": [True]})
    assert response.status_code == 422


def test_updated_assessment_updates_header_and_defers_new_page_enrichment():
    assert 'id="card-overall-score"' in SCOUT_WEB_HTML
    assert "$('card-overall-score').textContent=scoreText(value)" in SCOUT_WEB_HTML
    assert 'if(enrichmentActive){enrichmentPending=true;return}' in SCOUT_WEB_HTML
    assert 'queueMicrotask(()=>enrichVisible())' in SCOUT_WEB_HTML


def test_visible_context_retains_automatic_scoring_channel(monkeypatch):
    scope = {"query": "robot manipulation"}
    news = {"source": "google_news_rss", "binding": scope, "materials": [], "status": "complete"}
    selected = {"source": "epo_ops", "materials": [], "status": "credentials_required"}
    monkeypatch.setattr(source_context, "read_saved_all", lambda *args: {"binding": scope, "reports": [news]})
    monkeypatch.setattr(source_context, "read", lambda *args, **kwargs: {"binding": scope, "reports": [selected]})
    packet = source_context.read_visible("m", 1, 7, ["epo_ops"])
    assert [r["source"] for r in packet["reports"]] == ["google_news_rss", "epo_ops"]
    assert packet["ready_count"] == 1


def test_narrow_topic_qualifier_cannot_be_replaced_by_generic_news():
    c = card(label="Reinforcement learning — manipulation")
    unrelated = material(title="Reinforcement learning boosts medical diagnosis")
    result = assessment.calculate(c, context(unrelated), today=TODAY)
    assert result["records"][0]["included"] is False
    assert result["contributions"]["market"] is None


def test_empty_external_sources_preserve_science_not_zero_fill():
    original = card(); before = copy.deepcopy(original)
    result = assessment.calculate(original, context(status="credentials_required"), today=TODAY)
    assert original == before
    assert result["scientific_baseline"] == pytest.approx(63.0, abs=.01)
    assert result["overall_score"] == result["scientific_baseline"]
    assert result["contributions"]["patents"] is None
    assert result["axes"]["market"]["score"] is None
    assert result["policy"]["missing_is_zero"] is False
    assert result["policy"]["calibrated_on_labeled_data"] is False
    assert result["validation"]["expert_required"] is False
    assert result["assessment_payload_sha256"] == digest({k: v for k, v in result.items() if k != "assessment_payload_sha256"})


def test_four_evidence_channels_name_market_as_news_not_market_size():
    result = calc(material())
    assert set(result["axes"]) == {"science", "market", "patents", "investment"}
    assert result["axes"]["market"]["label"] == "Новости"
    assert "Количество новостных публикаций с поправкой на связь с темой" in result["axes"]["market"]["components"]
    assert "Объём с поправкой на связь с темой" not in result["axes"]["market"]["components"]
    assert "новостные публикации" in result["visualization_html"]
    assert "Наука, Рынок" not in result["visualization_html"]
    assert "Рынок здесь" not in result["visualization_html"]
    # Only presentation changes; the four-channel score and caps are unchanged.
    assert result["axes"]["market"]["score"] == pytest.approx(13.67)
    assert result["contributions"]["market"] == pytest.approx(1.37)


def test_news_and_patents_really_change_priority_and_validation_not_science():
    news = material()
    patent = material("patents", "Vision-language-action robot model", family_id="family1", applicants=["Lab One"])
    result = calc(news, patent)
    assert result["overall_score"] > result["scientific_baseline"]
    assert 0 < result["contributions"]["market"] <= 10
    assert 0 < result["contributions"]["patents"] <= 10
    assert result["validation"]["label"] == "Тема прослеживается в науке, СМИ и патентах"
    assert result["scientific_score_modified"] is False
    assert result["validation"]["independent_primary_confirmation"] is False


@pytest.mark.parametrize("row,status,report_changes", [
    (material(title="Neural networks for quantum communication"), "complete", {}),
    (material(record_date="2026-12-01"), "complete", {}),
    (material(record_date="2026-09-30"), "complete", {}),
    (material(record_date=None), "complete", {}),
    (material(provider_duplicate_flag=True), "complete", {}),
    (material(), "source_data_stale", {}),
    (material(), "complete", {"cache_fresh": False}),
    (material(provider_cache_status="stale"), "complete", {}),
    (material("investment", original_record_url_available=False), "complete", {}),
])
def test_noise_missing_dates_future_dates_stale_and_unverified_deals_cannot_inflate_score(row, status, report_changes):
    result = assessment.calculate(card(), context(row, status=status, **report_changes), today=TODAY)
    assert result["overall_score"] == result["scientific_baseline"]
    assert result["records"][0]["included"] is False
    assert result["records"][0]["exclusion_reason"]


def test_company_catalog_and_grants_are_not_market_or_investment_support():
    result = calc(material(source="dealroom_marketmaps"), material("funding", funding_amount=1000000))
    assert result["records"] == []
    assert result["contributions"]["market"] is None and result["contributions"]["investment"] is None


def test_repeat_news_and_same_patent_family_not_multiple_confirmations():
    news = material()
    same = {**news, "url": "https://other.example/story", "publisher": "Another Publisher"}
    patent = material("patents", family_id="42")
    family = {**patent, "url": "https://example.org/second-patent", "title_original": "Vision language action new patent"}
    result = calc(news, same, patent, family)
    assert result["axes"]["market"]["included_records"] == 1
    assert result["axes"]["patents"]["included_records"] == 1
    assert result["validation"]["excluded_external_records"] == 2


def test_unknown_family_is_not_treated_as_known_independent_family():
    unknown = calc(material("patents"))["axes"]["patents"]
    known = calc(material("patents", family_id="42"))["axes"]["patents"]
    assert unknown["score"] < known["score"]
    assert unknown["metrics"]["Записи без семейства"] == 1


def test_explicit_analyst_link_changes_relevance_not_expert_validation():
    row = material(title="A robot deployment in a warehouse")
    links = {"links": [{"observation_id": "observation", "record_url": row["url"], "assessment": "relevant_to_topic"}]}
    result = calc(row, links=links)
    assert result["axes"]["market"]["score"] is not None
    assert result["records"][0]["quality_weight"] == .65
    assert result["records"][0]["expert_validated"] is False
    links["links"][0]["assessment"] = "background_only"
    assert calc(material(), links=links)["axes"]["market"]["score"] is None


def test_no_comparable_science_does_not_get_momentum_credit():
    c = card(); c["metrics"]["publication_series"]["coverage_comparable"] = False
    result = assessment.calculate(c, context(material()), today=TODAY)
    momentum = result["axes"]["science"]["components"][0]
    assert momentum["value"] is None
    assert "сопоставимым" in result["validation"]["label"]


def test_unknown_science_cannot_be_invented_by_news():
    c = {**card(), "metrics": {}}
    result = assessment.calculate(c, context(material()), today=TODAY)
    assert result["overall_score"] is None and result["scientific_baseline"] is None
    assert result["axes"]["market"]["score"] is not None


def test_volume_only_science_does_not_extrapolate_to_high_signal_priority():
    c = {**card(), "metrics": {"observed": {"doc_count": 16}}}
    result = assessment.calculate(c, today=TODAY)
    assert result["scientific_baseline"] == 8
    assert result["axes"]["science"]["known_weight_percent"] == 10
    assert result["axes"]["science"]["unmeasured_weight_percent"] == 90
    assert result["axes"]["science"]["possible_upper_score_if_all_unknown_maximal"] == 98


def test_sparse_external_history_has_no_fake_zero_or_growth():
    result = calc(material(record_date="2026-01-20"), material(title="New vision-language-action models", url="https://example.org/new", record_date="2026-04-15"))
    history = result["histories"]["market"]
    assert [r["period"] for r in history["points"]] == ["2026-01", "2026-04"]
    assert history["growth_rate"] is None
    assert all(p["count"] == 1 for p in history["points"])
    assert "Неполный" in result["histories"]["science"]["note"]
    assert "нет данных" in result["visualization_html"]


def test_chart_and_links_are_escaped_and_unknown_axes_do_not_make_polygon():
    result = calc(material(title='<script>vision language action</script>', url="javascript:alert(1)"))
    html = result["visualization_html"]
    assert '<script>' not in html and 'href="javascript:' not in html
    assert '&lt;script&gt;' in html
    assert 'fill="#3156ad18"' not in html
    assert "Исходные значения по периодам" in html


def test_composition_binding_mismatch_is_rejected():
    with pytest.raises(ValueError):
        assessment.calculate(card(), {"binding": {"composition_sha256": "b" * 64}})


def test_lexical_variants_are_orthographic_not_guessed_semantic_aliases():
    assert assessment.tokens("Vision-language-action models") == assessment.tokens("vision language action model")
    assert "uav" in assessment.tokens("UAVs")
    assert "drone" not in assessment.tokens("unmanned aircraft")


def test_ranking_keeps_contradicted_science_below_supported_growth():
    packet = {"queue": [
        {"candidate_id": 1, "rank": 2, "screening": {"state": "mixed_evidence"}, "failed_checks": ["novelty"], "assessment": {"overall_score": 100}},
        {"candidate_id": 2, "rank": 1, "screening": {"state": "growth_observed"}, "failed_checks": [], "assessment": {"overall_score": 50}}]}
    assert [q["candidate_id"] for q in scout_results.rank(packet)["queue"]] == [2, 1]


def test_score_cap_is_explicit_and_applied_contributions_sum_to_total():
    c = card(); c["metrics"]["normalized"] = {k: 1 for k in assessment.SCIENCE_WEIGHTS}; c["metrics"]["observed"]["doc_count"] = 20
    result = assessment.calculate(c, context(material()), today=TODAY)
    assert result["overall_score"] == 100
    assert sum(v for v in result["contributions"].values() if v is not None) == 100
    assert result["uncapped_bonuses"]["market"] > 0 and result["contributions"]["market"] == 0


def test_api_scoped_assessment_and_bounded_enrichment(monkeypatch):
    client = TestClient(app); calls = []
    monkeypatch.setattr(assessment, "for_candidate", lambda *args: calls.append(args) or calc())
    monkeypatch.setattr(scout_results, "enrich", lambda *args: calls.append(args) or {"expert_validation_required": False})
    assert client.get('/signals/m/7/assessment?score_run_id=12').status_code == 200
    assert calls[0] == ("m", 12, 7)
    assert client.post('/scout-results/m/enrich?score_run_id=12', json={"candidate_ids": [7]}).status_code == 200
    assert calls[1] == ("m", 12, [7], None)
    assert client.post('/scout-results/m/enrich?score_run_id=12', json={"candidate_ids": list(range(1, 17))}).status_code == 422


def test_enrichment_rejects_wrong_candidates_and_funding_as_investment(monkeypatch):
    monkeypatch.setattr(scout_results.candidates, "export_cards", lambda *args: {"cards": [card()]})
    for ids, sources in [([99], None), ([7, 7], None), ([7], ["usaspending"]), ([7], ["osti_gov"])]:
        with pytest.raises(ValueError):
            scout_results.enrich("m", 12, ids, sources)


def test_ui_single_search_action_contains_profile_trends_and_next_page_enrichment():
    assert "await launchSearch()" in SCOUT_WEB_HTML
    assert "$('search-submit').onclick=prepareSearch" in SCOUT_WEB_HTML
    assert "assessmentControls(q)" in SCOUT_WEB_HTML
    assert "/scout-results/" in SCOUT_WEB_HTML
    assert "state.page++;render();enrichVisible()" in SCOUT_WEB_HTML
    assert "Оценка и графики" in SCOUT_WEB_HTML
    assert "candidate_ids:ids" in SCOUT_WEB_HTML


def test_ops_records_get_exact_publication_link_and_family_passport():
    saved = {"observation_id": "obs", "created_at": "2026-09-29T10:00:00Z", "payload": {}}
    record = source_context.passport("epo_ops", {"publication_id": "EP1234567A1", "title": "Vision language action", "family_id": "42", "applicants": ["Lab"]}, saved)
    assert record["url"] == "https://worldwide.espacenet.com/patent/search?q=pn%3DEP1234567A1"
    assert record["family_id"] == "42" and record["applicants"] == ["Lab"]
