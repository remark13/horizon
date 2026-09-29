"""Publisher identities, bounded querying and honest connection observations."""
import copy
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from saia import aggregator_evidence as a, external_evidence_store as store, news_publishers as p, source_context as c
from saia.hybrid import digest
from saia.source_registry import load_registry, validate_runtime_sources
from saia.external_sources import load_policy
from saia.scout_web import SCOUT_WEB_HTML

SOURCE = "publisher_news_phys_org"


def query(source=SOURCE, text="robotics"):
    today = datetime.now(timezone.utc).date()
    return a.AggregatorQuery("publisher-test", source, text, today - timedelta(days=29), today, today, 5)


def feed(homepage="https://phys.org", title="Robot systems; new methods — Phys.org"):
    return (f'<rss><channel><item><title>{title}</title><link>https://news.google.com/rss/articles/example</link>'
            f'<guid>article-1</guid><pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate>'
            f'<source url="{homepage}">Phys.org</source><description>Never keep article bodies</description>'
            '</item></channel></rss>').encode()


def test_directory_adds_over_100_unique_publishers_not_topic_duplicates():
    directory = p.read_directory()
    rows = directory["publishers"]
    assert len(rows) == len({row["id"] for row in rows}) == len({row["domain"] for row in rows}) == 169
    assert directory["independence_verified"] is False
    assert directory["automatic_default_polling"] is False
    assert len({row["category"] for row in rows}) == 9
    assert all(";" not in row["trust_comment_ru"] and "—" not in row["trust_comment_ru"] for row in rows)


@pytest.mark.parametrize("field,value", [("domain", "127.0.0.1"), ("domain", "localhost.local"),
    ("domain", "evil.test"), ("homepage", "http://phys.org/"), ("id", "publisher_news_unregistered"),
    ("search_locale", "unknown"), ("source_kind", "independent_confirmation"), ("name", "name\nsecret")])
def test_manifest_rejects_invalid_identity_and_unsafe_metadata(field, value):
    directory = copy.deepcopy(p.read_directory())
    directory["publishers"][0][field] = value
    with pytest.raises(ValueError):
        p.validate_directory(directory)


def test_duplicate_publisher_is_not_allowed():
    directory = copy.deepcopy(p.read_directory())
    directory["publishers"].append(copy.deepcopy(directory["publishers"][0]))
    with pytest.raises(ValueError):
        p.validate_directory(directory)


@pytest.mark.parametrize("url", ["https://phys.org.evil.example/", "https://evilphys.org/", "https://phys.org@evil.example/",
    "https://secret@phys.org/", "https://phys.org:8080/", "javascript:alert(1)", "http://127.0.0.1/"])
def test_publisher_domain_is_not_a_prefix_or_substring_match(url):
    assert p.matches_publisher_url(SOURCE, url) is False


def test_domain_filter_is_fixed_and_no_key_or_unbounded_search_is_sent():
    url, headers, body = a.request_spec(query())
    assert urlsplit(url).netloc == "news.google.com" and body is None and "Authorization" not in headers
    parameters = parse_qs(urlsplit(url).query)
    assert '"robotics" site:phys.org after:' in parameters["q"][0]
    assert parameters["ceid"] == ["US:en"]
    ru_url, _, _ = a.request_spec(query("publisher_news_tass_ru", "технологии"))
    assert parse_qs(urlsplit(ru_url).query)["ceid"] == ["RU:ru"]
    for text in ("robotics site:evil.example", "https://internal/", "robotics\nsecret"):
        with pytest.raises(ValueError):
            a.request_spec(query(text=text))
    with pytest.raises(ValueError):
        a.request_spec(query("publisher_news_unknown_org"))


def test_rss_provenance_filter_keeps_original_title_not_body():
    report = a.parse_response(query(), feed(), "now")
    assert store.verify(report) is report and report["observed_count"] == 1
    row = report["observations"][0]
    assert row["title"] == "Robot systems; new methods — Phys.org"
    assert row["publisher_homepage"] == "https://phys.org"
    assert report["publisher_filter_domain"] == "phys.org" and report["publisher_backend"] == "google_news_rss"
    assert row["original_article_url_available"] is False and report["independent_evidence_verified"] is False
    assert "Never keep" not in str(report)
    saved = {"observation_id": "synthetic", "payload": report, "created_at": "now"}
    material = c.passport(SOURCE, row, saved)
    assert material["model_input_allowed"] is False and material["usage_scope"] == "personal_research"
    assert material["publisher_backend"] == "google_news_rss" and material["publisher_homepage"] == "https://phys.org"
    assert a.parse_response(query(), feed("https://phys.org.evil.example"), "now")["observations"] == []


@pytest.mark.parametrize("field,value", [("publisher_filter_domain", "phys-org"), ("publisher_backend", "independent_api"),
    ("publisher_directory_version", "old"), ("usage_scope", "commercial"), ("model_input_allowed", True),
    ("model_training_allowed", True), ("stores_full_text", True), ("stores_images", True), ("public_republication_allowed", True)])
def test_metadata_reader_flags_cannot_be_silently_widened(field, value):
    report = a.parse_response(query(), feed(), "now")
    report[field] = value
    report.pop("report_payload_sha256")
    report["report_payload_sha256"] = digest(report)
    with pytest.raises(ValueError):
        store.verify(report)


def test_failure_is_unknown_not_zero_and_adapter_preserves_binding_and_policy(monkeypatch):
    monkeypatch.setattr(a, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("not a public error")))
    monkeypatch.setattr(c.external_evidence_store, "record", lambda value: store.verify(value) and {"observation_id": "synthetic"})
    scope = c.binding("synthetic", 12, 7, {"composition_sha256": "a" * 64}, "robotics")
    report = c._fetch_one(SOURCE, scope)["payload"]
    assert report["observations"] is None and report["status"] == "invalid_source_response"
    assert report["candidate_context_binding"] == scope and report["publisher_filter_domain"] == "phys.org"
    assert "not a public error" not in str(report)


def test_new_channels_use_same_bounded_recent_window_not_five_year_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(a, "fetch", lambda q, **kw: calls.append(q) or a.unavailable(q, "source_unavailable", "now"))
    monkeypatch.setattr(c.external_evidence_store, "record", lambda value: store.verify(value) and {"observation_id": "synthetic"})
    scope = c.binding("synthetic", 12, 7, {"composition_sha256": "a" * 64}, "robotics")
    c._fetch_one(SOURCE, scope)
    assert calls[0].end - calls[0].start == timedelta(days=29)
    assert c.MAX_SOURCES == 8 and c.MAX_PARALLEL == 3
    assert not any(source in p.publishers() for source in c.DEFAULT_SOURCES)


def test_catalog_integration_does_not_call_network_or_call_probe_a_candidate_run(monkeypatch):
    monkeypatch.setattr(c, "_last_observations", lambda: {})
    monkeypatch.setattr(c, "connection_checks", lambda: {SOURCE: {"source": SOURCE, "basis": "directory_connection_probe",
                         "status": "complete", "observed_count": 1, "retrieved_at": "2026-09-29T10:00:00+00:00"}})
    monkeypatch.setattr(a, "fetch", lambda *args, **kwargs: pytest.fail("catalog must not run a query"))
    catalog = c.catalog()
    assert len(catalog["sources"]) == 196 and catalog["publisher_channels"] == 169
    assert catalog["independent_aggregators_added"] == 0
    row = next(row for row in catalog["sources"] if row["source"] == SOURCE)
    assert row["last_observation"] is None
    assert row["last_connection_check"]["basis"] == "directory_connection_probe"
    assert row["publisher_domain"] == "phys.org" and row["default_selected"] is False
    gate = validate_runtime_sources(load_registry(c.REGISTRY_PATH), load_policy())
    assert set(p.publishers()).issubset(gate["configured_source_ids"])


def test_connection_checks_keep_empty_and_unknown_separate(tmp_path, monkeypatch):
    value = {"directory_version": p.VERSION, "checks": [{"source": SOURCE, "status": "source_unavailable",
             "basis": "directory_connection_probe", "retrieved_at": "2026-09-29T10:00:00+00:00", "observed_count": None}]}
    path = tmp_path / "checks.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(p, "CHECKS_PATH", path)
    assert p.connection_checks()[SOURCE]["observed_count"] is None
    value["checks"][0]["observed_count"] = 0
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        p.connection_checks()


def test_news_groups_fold_and_date_is_under_status_not_description():
    html = SCOUT_WEB_HTML
    assert 'class="source-group" data-source-group=' in html
    assert 'class="source-subgroup" data-source-group=' in html
    assert "saia-source-groups-v2" in html and "addEventListener('toggle'" in html
    assert 'Последняя попытка:' not in html
    assert '${sourceConfigBadge(s)}<small>Обращение:' in html
    assert 'class="block source-content-group"><summary>Новости и коммерческие публикации' in html
    assert 'esc(readableSourceDescription(s.trust_comment))' in html
    assert 'esc(material.title_original)' in html
    assert 'esc(readableSourceDescription(material.title_original))' not in html
