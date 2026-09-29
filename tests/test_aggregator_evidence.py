import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from saia import aggregator_evidence as a, external_evidence_store, external_material_groups, source_context
from saia.hybrid import digest
from saia.source_registry import load_registry, validate_runtime_sources
from saia.external_sources import load_policy


def query(source="dealroom_marketmaps", text="robotics"):
    today = datetime.now(timezone.utc).date()
    return a.AggregatorQuery("test", source, text, today - timedelta(days=366), today, today, 5)


def parse(source, payload, text="robotics"):
    return a.parse_response(query(source, text), json.dumps(payload).encode(), datetime.now(timezone.utc).isoformat())


def test_all_aggregators_are_registered_and_have_exact_typed_store_roles():
    registry = load_registry(source_context.REGISTRY_PATH)
    gate = validate_runtime_sources(registry, load_policy())
    assert gate["all_runtime_sources_registered"]
    for source in a.CHANNELS:
        report = a.unavailable(query(source), "credentials_required", "now")
        assert external_evidence_store.verify(report) == report
        assert report["observations"] is None and report["observed_count"] is None
        assert report["scientific_score_modified"] is False


@pytest.mark.parametrize("source,key", list(a.CREDENTIALS.items()))
def test_missing_credentials_never_calls_network_or_returns_zero(monkeypatch, source, key):
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(a, "build_opener", lambda *args: pytest.fail("network with no credentials"))
    report = a.fetch(query(source))
    assert report["status"] == "credentials_required"
    assert report["observed_count"] is None


def test_media_cloud_requires_bounded_explicit_collections(monkeypatch):
    monkeypatch.setenv("MEDIACLOUD_API_KEY", "private-test-token")
    for ids in ("", "1,1", "hello", ",".join(str(i) for i in range(1, 12))):
        monkeypatch.setenv("MEDIACLOUD_COLLECTION_IDS", ids)
        assert a.fetch(query("mediacloud_news"))["status"] == "collection_ids_required"
    monkeypatch.setenv("MEDIACLOUD_COLLECTION_IDS", "123,456")
    url, headers, body = a.request_spec(query("mediacloud_news"))
    assert "cs=123%2C456" in url and body is None
    assert headers["Authorization"] == "Token private-test-token"
    assert "private-test-token" not in str(a.unavailable(query("mediacloud_news"), "source_unavailable", "now"))


def test_event_registry_no_article_bodies_and_no_token_in_url_or_report(monkeypatch):
    monkeypatch.setenv("EVENT_REGISTRY_API_KEY", "private-test-token")
    url, headers, body = a.request_spec(query("event_registry"))
    request = json.loads(body)
    assert request["apiKey"] == "private-test-token"
    assert request["articleBodyLen"] == 0 and request["includeArticleBody"] is False
    assert "private-test-token" not in url
    assert "private-test-token" not in str(a.unavailable(query("event_registry"), "source_unavailable", "now"))


def test_error_body_and_secrets_are_never_retained(monkeypatch):
    monkeypatch.setenv("LENS_API_TOKEN", "private-test-token")
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError("https://api.lens.org", 403, "private-test-token", {}, None)
    monkeypatch.setattr(a, "build_opener", lambda *args: Opener())
    report = a.fetch(query("lens_patents"))
    assert report["status"] == "credentials_rejected"
    assert "private-test-token" not in str(report)
    assert a.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test") is None


def test_market_maps_preserve_unknown_dates_and_counts_are_not_downloaded_companies():
    report = parse("dealroom_marketmaps", {"results": [
        {"id": "landscape-1", "title": "Robotics", "url": "https://dealroom.co/maps/1", "companyCount": 1500},
        {"id": "landscape-1", "title": "Robotics duplicate", "url": "https://dealroom.co/maps/1"}]})
    assert report["observed_count"] == 1
    row = report["observations"][0]
    assert row["publication_date"] is None and row["date_precision"] == "unknown"
    assert row["reported_company_count"] == 1500 and row["company_records_downloaded"] is False
    assert report["source_result_is_exhaustive"] is False


def test_rounds_keep_month_precision_stale_flag_and_unverified_amounts():
    month = datetime.now(timezone.utc).strftime("%b %Y")
    report = parse("dealroom_public_rounds", {"source": "dealroom-api", "cacheStatus": "stale", "fetchedAt": "2026-09-01T00:00:00Z", "signals": [
        {"title": "Robot startup", "meta": "robotics", "value": "$10m", "time": month},
        {"title": "Other robot startup", "meta": "robotics", "value": "$20m", "time": month},
        {"title": "Unrelated", "meta": "biotech", "value": "$20m", "time": month}]})
    assert report["status"] == "source_data_stale" and report["observed_count"] == 2
    assert report["query_method"] == "local_literal_filter_of_global_sample"
    assert all(row["funding_amount"] is None and row["date_precision"] == "month" for row in report["observations"])
    saved = {"payload": report, "created_at": "now", "observation_id": "1"}
    materials = [source_context.passport("dealroom_public_rounds", r, saved) for r in report["observations"]]
    grouped = external_material_groups.group_materials(materials)
    assert grouped["display_group_count"] == 2  # same provider landing page is not same deal


def test_no_matches_in_round_sample_is_not_zero_global_investment():
    report = parse("dealroom_public_rounds", {"signals": []})
    assert report["observed_count"] == 0 and report["missing_is_zero"] is False
    assert report["market_totals_established"] is False


@pytest.mark.parametrize("source,payload", [("dealroom_marketmaps", {}), ("lens_patents", {"data": "bad"}), ("event_registry", {"articles": []})])
def test_missing_provider_list_is_not_successful_empty(source, payload):
    with pytest.raises(ValueError):
        parse(source, payload)


@pytest.mark.parametrize("link", ["javascript:alert(1)", "https://user:secret@x.test", "https://x.test/\nwrong"])
def test_unsafe_material_links_are_rejected(link):
    assert parse("dealroom_marketmaps", {"results": [{"title": "Bad", "url": link}]})["observations"] == []


def test_news_publication_date_and_language_are_preserved_not_invented():
    day = datetime.now(timezone.utc).date().isoformat()
    event = parse("event_registry", {"articles": {"results": [{"uri": "123", "title": "Robots", "url": "https://publisher.test/robot", "date": day, "lang": "eng", "source": {"title": "Publisher"}, "eventUri": "event-12", "body": "must not retain"}]}})
    row = event["observations"][0]
    assert row["publication_date"] == day and row["language"] == "eng"
    assert "must not retain" not in str(event)
    media = parse("mediacloud_news", {"sample": [{"id": 1, "title": "Robots", "url": "https://publisher.test/robot", "publish_date": day, "language": "en", "media_name": "Publisher"}]})
    assert media["observations"][0]["publication_date"] == day


def test_patent_metadata_projection_and_ids_remain_patents(monkeypatch):
    monkeypatch.setenv("LENS_API_TOKEN", "private-test-token")
    _, _, body = a.request_spec(query("lens_patents"))
    fields = json.loads(body)["include"]
    assert "claims" not in fields and "abstract" not in fields
    report = parse("lens_patents", {"data": [{"lens_id": "123-456-789-012-345", "date_published": datetime.now(timezone.utc).date().isoformat(), "biblio": {"invention_title": [{"text": "Robot patent", "lang": "en"}]}}]})
    assert report["observations"][0]["record_type"] == "patent_publication"
    assert report["report_payload_sha256"] == digest({k: v for k, v in report.items() if k != "report_payload_sha256"})


def test_google_news_is_public_broad_search_and_locale_is_not_article_language():
    from email.utils import format_datetime
    q = query("google_news_rss", "беспилотные авиационные системы")
    url, headers, body = a.request_spec(q)
    assert "news.google.com/rss/search?" in url and "ceid=RU%3Aru" in url
    assert body is None and "Authorization" not in headers
    published = format_datetime(datetime.now(timezone.utc))
    raw = f'<rss><channel><item><title>Robotics — Publisher</title><link>https://news.google.com/rss/articles/123</link><guid>123</guid><pubDate>{published}</pubDate><source url="https://publisher.test">Publisher</source><description>Do not retain this article body</description></item></channel></rss>'.encode()
    report = a.parse_response(q, raw, "now")
    assert report["status"] == "complete" and report["observed_count"] == 1
    row = report["observations"][0]
    assert row["publisher"] == "Publisher" and row["language"] is None
    assert row["original_article_url_available"] is False and row["aggregator_redirect_url"] is True
    assert "Do not retain" not in str(report)


@pytest.mark.parametrize("raw", [b'<html>not RSS</html>', b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>', b'<rss><channel>'])
def test_google_news_rejects_wrong_format_and_unsafe_xml(raw):
    with pytest.raises((ValueError, a.ET.ParseError)):
        a.parse_response(query("google_news_rss"), raw, "now")
