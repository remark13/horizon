from datetime import date
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import pytest
import yaml

from saia import candidate_brief, rss_evidence as rss, source_context
from saia.api import app
from saia.external_evidence_store import verify
from saia.external_sources import load_policy
from saia.hybrid import digest
from saia.source_registry import load_registry, validate_runtime_sources

STAMP = "2026-09-28T12:00:00Z"
LINK = "https://www.aist.go.jp/aist_j/press_release/pr2026/synthetic-test.html"
TITLE = "水に溶ける材料をイメージングとMRIに応用"


def query(text="imaging materials", **changes):
    return rss.RSSQuery(**{"topic_id": "synthetic-topic", "query": text,
                          "start": "2026-01-01", "end": "2026-09-28", "as_of": "2026-09-28",
                          "source": "aist_press_rss", "max_records": 5, **changes})


def feed(*rows, language=None):
    root = ET.Element("rss")
    channel = ET.SubElement(root, "channel")
    if language:
        ET.SubElement(channel, "language").text = language
    for row in rows:
        item = ET.SubElement(channel, "item")
        fields = {"title": TITLE, "link": LINK, "pubDate": "Mon, 28 Sep 2026 00:00:00 +0900", **row}
        for key, value in fields.items():
            ET.SubElement(item, key).text = value
    return ET.tostring(root, encoding="utf-8")


def parse(*rows, text="imaging materials", **changes):
    return rss.parse_response(query(text, **changes), feed(*rows), STAMP)


def test_aist_is_dated_original_japanese_reader_metadata_not_article_or_model_input():
    value = parse({"description": "Do not retain article body", "enclosure": "Do not retain image"})
    assert verify(value) is value and value["status"] == "complete"
    record = value["observations"][0]
    assert record["title"] == TITLE and record["title_preserved_from_feed"] is True
    assert record["published_at"] == "2026-09-28" and record["language"] == "ja"
    assert record["publisher_organisation"] == "AIST" and record["source_country"] == "Japan"
    assert record["country_scope"] == "publisher_origin_only"
    assert record["independent_confirmation"] is False
    assert record["record_type"] == "institutional_announcement"
    assert all(value[k] is v if isinstance(v, bool) else value[k] == v
               for k, v in rss.personal_reader_flags().items())
    assert record["model_input_allowed"] is False
    assert value["stores_article_text"] is value["stores_images"] is False
    assert not any(text in str(value) for text in ("Do not retain", "enclosure", "description"))
    assert value["source_result_is_exhaustive"] is False and value["missing_is_zero"] is False


@pytest.mark.parametrize("text", ["imaging materials", "materials imaging", "материалы", "визуализация материалы", "материалы для визуализации", "визуализация материалов", "材料", "MRI"])
def test_explicit_aliases_and_original_terms_match_visible_title(text):
    value = parse({}, text=text)
    assert value["observed_article_count"] == 1
    assert value["query_match_terms"] == rss.aist_search_terms(text)
    assert value["observations"][0]["match_basis"] == "explicit_curated_literal_japanese_aliases"


@pytest.mark.parametrize("text", ["imaging materials missing", "battery materials", "quantum", "AI", "machine learning"])
def test_unknown_qualifiers_are_not_discarded_and_no_semantic_confirmation_is_invented(text):
    value = parse({}, text=text)
    assert value["status"] == "empty_observed_response" and value["observations"] == []
    assert value["missing_is_zero"] is False


@pytest.mark.parametrize("text,title", [("artificial intelligence", "人工知能の新技術"),
                                        ("искусственный интеллект", "人工知能の新技術"),
                                        ("machine learning", "機械学習の新技術"),
                                        ("машинное обучение", "機械学習の新技術")])
def test_two_word_aliases_remain_one_required_concept(text, title):
    assert len(rss.aist_search_terms(text)) == 1
    assert parse({"title": title}, text=text)["observed_article_count"] == 1


def test_latin_ai_does_not_match_train_or_rail_in_title():
    assert parse({"title": "New train and rail research"}, text="AI")["observations"] == []
    assert parse({"title": "New AI research"}, text="AI")["observed_article_count"] == 1


def test_aist_only_uses_title_not_categories_and_preserves_xml_text():
    value = parse({"title": "異なる研究", "category": "imaging materials"})
    assert value["observations"] == []
    marked = "MRI materials <script>alert(1)</script>"
    assert parse({"title": marked}, text="MRI materials")["observations"][0]["title"] == marked


def test_events_future_releases_unknown_dates_and_foreign_urls_are_excluded_separately():
    rows = [{"link": "https://www.aist.go.jp/kansai/ja/news/e20261103.html"},
            {"link": LINK + "?future", "pubDate": "Tue, 29 Sep 2026 00:00:00 +0900"},
            {"link": LINK + "?unknown", "pubDate": ""},
            {"link": "https://evil.example/aist_j/press_release/synthetic"},
            {"link": "http://www.aist.go.jp/aist_j/press_release/synthetic"},
            {"link": "https://www.aist.go.jp:8443/aist_j/press_release/synthetic"},
            {"link": "https://private@www.aist.go.jp/aist_j/press_release/synthetic"}]
    value = parse(*rows)
    assert value["observations"] == []
    assert value["records_outside_research_release_paths"] == 1
    assert value["records_outside_publication_interval"] == 1
    assert value["records_without_publication_date"] == 1


def test_aist_skips_long_titles_instead_of_rewriting_them_and_bounds_duplicates():
    value = parse({"title": "materials " + "長" * 1000}, {}, {}, text="materials")
    assert len(value["observations"]) == 1 and value["observations"][0]["title"] == TITLE
    limited = parse(*[{"link": LINK + "?id=" + str(i)} for i in range(10)], max_records=2)
    assert limited["observed_article_count"] == 2 and limited["result_cap_reached"] is True
    examined = parse(*[{"link": LINK + "?id=" + str(i)} for i in range(205)])
    assert examined["response_items_examined"] == 200


def test_empty_published_feed_is_empty_response_not_proof_of_no_research():
    value = rss.parse_response(query(), feed(), STAMP)
    assert verify(value) is value and value["status"] == "empty_observed_response"
    assert value["response_items_examined"] == 0
    assert value["observed_article_count"] == 0 and value["missing_is_zero"] is False


@pytest.mark.parametrize("text", ["and", "для", "и для", "!!!"])
def test_stop_only_query_is_rejected_before_fetch(text):
    with pytest.raises(ValueError, match="содержательный"):
        query(text).validate()


@pytest.mark.parametrize("flag", ["public_republication_allowed", "model_input_allowed", "model_training_allowed", "bulk_reuse_approved", "model_inputs_modified"])
@pytest.mark.parametrize("invalid", [True, "false", None])
def test_store_requires_actual_false_permissions_not_strings_or_missing(flag, invalid):
    value = parse({})
    value.pop("report_payload_sha256")
    value[flag] = invalid
    value["report_payload_sha256"] = digest(value)
    with pytest.raises(ValueError, match="AIST"):
        verify(value)


def test_store_rejects_public_usage_scope():
    value = parse({})
    value.pop("report_payload_sha256")
    value["usage_scope"] = "public_service"
    value["report_payload_sha256"] = digest(value)
    with pytest.raises(ValueError, match="AIST"):
        verify(value)


@pytest.mark.parametrize("error,status", [(HTTPError(LINK, 429, "limited", {}, None), "rate_limited"),
                                          (HTTPError(LINK, 503, "unavailable", {}, None), "source_http_error"),
                                          (URLError("unavailable"), "source_unavailable"),
                                          (TimeoutError(), "source_unavailable")])
def test_failure_is_unknown_and_does_not_relax_scope(monkeypatch, error, status):
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(rss, "build_opener", lambda *a: SimpleNamespace(open=fail))
    result = rss.fetch(query())
    assert result["status"] == status and result["observations"] is None
    assert result["observed_article_count"] is None and verify(result) is result
    assert all(result[k] is v if isinstance(v, bool) else result[k] == v
               for k, v in rss.personal_reader_flags().items())


def test_adapter_fallback_preserves_personal_scope_and_exact_card_binding(monkeypatch):
    monkeypatch.setattr(rss, "fetch", lambda *a, **kw: (_ for _ in ()).throw(ValueError("unavailable")))
    saved = []
    monkeypatch.setattr(source_context.external_evidence_store, "record", lambda v: saved.append(verify(v)) or {"observation_id": "synthetic"})
    scope = source_context.binding("synthetic-mission", 12, 7, {"composition_sha256": "a" * 64}, "materials", date(2026, 9, 28))
    result = source_context._fetch_one("aist_press_rss", scope)
    assert saved[0]["observations"] is None and result["payload"]["candidate_context_binding"] == scope
    assert result["payload"]["model_input_allowed"] is False and result["payload"]["usage_scope"] == "personal_research"


def test_registry_adds_one_existing_identity_and_keeps_historical_decision():
    current = load_registry(source_context.REGISTRY_PATH)
    old = load_registry(source_context.REGISTRY_PATH.with_name("source-adoption.v0.4.48.yaml"))
    aist = [s for s in current["sources"] if s["id"] == "aist_press_rss"]
    assert len(aist) == 1 and aist[0]["decision"] == "adapt"
    assert next(s for s in old["sources"] if s["id"] == "aist_press_rss")["decision"] == "defer"
    gate = validate_runtime_sources(current, load_policy())
    assert len(gate["configured_source_ids"]) == len(source_context.SOURCE_INFO)
    assert "aist_press_rss" in gate["configured_source_ids"]
    assert len(current["_registry_lineage"]) == 17
    assert "aist_press_rss" not in source_context.DEFAULT_SOURCES
    assert rss.selected_feed(query())[0] == "https://www.aist.go.jp/ctl/module/mid/27/tid/75/rss.php"


@pytest.mark.parametrize("override", [{"not_an_existing_id": {"decision": "adapt"}},
                                      {"aist_press_rss": {"id": "new-id"}},
                                      {"aist_press_rss": {}},
                                      {"aist_press_rss": None},
                                      {"aist_press_rss": {"decision": "not_a_decision"}},
                                      ["aist_press_rss"]])
def test_invalid_registry_override_cannot_change_identity_or_bypass_gate(tmp_path, override):
    current = load_registry(source_context.REGISTRY_PATH)
    value = {k: v for k, v in current.items() if k not in ("extends", "_registry_lineage", "source_overrides")}
    value["source_overrides"] = override
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError):
        load_registry(path)


def test_rss_api_accepts_aist_without_saving_and_validates_before_network(monkeypatch):
    calls = []
    monkeypatch.setattr(rss, "fetch", lambda q: calls.append(q) or parse({}))
    client = TestClient(app)
    body = {"topic_id": "synthetic-topic", "query": "imaging materials", "start": "2026-01-01", "end": "2026-09-28", "as_of": "2026-09-28", "source": "aist_press_rss", "max_records": 5}
    response = client.post("/external-evidence/news/rss", json=body)
    assert response.status_code == 200 and response.json()["usage_scope"] == "personal_research"
    assert len(calls) == 1 and calls[0].source == "aist_press_rss"
    for change in ({"max_records": 16}, {"query": "x" * 161}):
        assert client.post("/external-evidence/news/rss", json={**body, **change}).status_code == 422
    assert len(calls) == 1


def test_rss_api_unknown_source_and_wrong_date_are_validation_errors_not_server_errors():
    client = TestClient(app)
    body = {"topic_id": "synthetic-topic", "query": "imaging materials", "start": "2026-01-01", "end": "2026-09-28", "as_of": "2026-09-28", "source": "aist_press_rss"}
    for change in ({"source": "unknown_source"}, {"end": "2026-10-01"}, {"query": "для"}):
        assert client.post("/external-evidence/news/rss", json={**body, **change}).status_code == 422


def test_passport_and_brief_expose_scope_aliases_and_escape_original_title():
    report = parse({"title": "MRI materials <script>alert(1)</script>"}, text="MRI materials")
    saved = {"payload": report, "observation_id": "synthetic", "created_at": STAMP}
    material = source_context.passport("aist_press_rss", report["observations"][0], saved)
    assert material["usage_scope"] == "personal_research" and material["query_match_terms"] == report["query_match_terms"]
    assert material["model_input_allowed"] is False and material["expert_validated"] is False
    html = candidate_brief._external_material(material)
    assert "Личный исследовательский просмотр" in html and "材料" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    malformed = {**material, "query_match_terms": [{"alternatives": None}, {"alternatives": "not a list"}, None]}
    assert "Личный исследовательский просмотр" in candidate_brief._external_material(malformed)
