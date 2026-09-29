import json
from pathlib import Path

import pytest

from saia.external_attention import (
    WikimediaMapping, fetch_wikimedia, gdelt_doc_assessment, parse_wikimedia,
    parse_wikimedia_resolution, wikimedia_not_found,
    wikimedia_resolution_url, wikimedia_url,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, payload, url, status=200):
        self.payload = payload
        self.url = url
        self.status = status

    def read(self):
        return self.payload

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_wikimedia_requires_explicit_unambiguous_mapping():
    mapping = WikimediaMapping("topic-1", "Artificial intelligence",
                               candidate_titles=("Artificial intelligence", "Machine learning"))
    with pytest.raises(ValueError, match="Ambiguous"):
        wikimedia_url(mapping, "2026-09-01", "2026-09-02")


def test_wikimedia_rejects_unsupported_historical_period():
    with pytest.raises(ValueError, match="2015-07-01"):
        wikimedia_url(WikimediaMapping("topic-1", "AI"), "2014-01-01", "2014-01-02")


def test_wikimedia_resolution_fixture_records_canonical_redirect():
    mapping = WikimediaMapping("topic-1", "AI")
    payload = (FIXTURES / "wikimedia_resolution_redirect.json").read_bytes()
    request_url = wikimedia_resolution_url(mapping)
    result = parse_wikimedia_resolution(
        mapping, payload, "2026-09-21T00:00:00+00:00", request_url,
        final_url=request_url,
    )
    assert result["status"] == "resolved"
    assert result["resolved_article_title"] == "Artificial intelligence"
    assert result["redirect_resolved"] is True
    assert result["raw_response_bytes_sha256"]


def test_wikimedia_resolution_fixture_distinguishes_missing_title():
    mapping = WikimediaMapping("topic-1", "Definitely missing SAIA page")
    payload = (FIXTURES / "wikimedia_resolution_missing.json").read_bytes()
    result = parse_wikimedia_resolution(
        mapping, payload, "2026-09-21T00:00:00+00:00",
        wikimedia_resolution_url(mapping),
    )
    assert result["status"] == "not_found"
    assert result["resolved_article_title"] is None
    assert result["page_id"] is None


def test_wikimedia_partial_does_not_fill_missing_day_with_zero():
    mapping = WikimediaMapping("topic-1", "Artificial intelligence")
    payload = json.dumps({"items": [
        {"timestamp": "2026090100", "views": 10},
        {"timestamp": "2026090300", "views": 30},
    ]}).encode()
    report = parse_wikimedia(mapping, "2026-09-01", "2026-09-03", payload,
                             "2026-09-20T00:00:00+00:00", "https://example.test")
    assert report["status"] == "partial"
    assert report["missing_days"] == ["2026-09-02"]
    assert report["total_views_observed"] == 40
    assert report["missing_is_zero"] is False
    assert report["scientific_score_modified"] is False


def test_wikimedia_redirect_is_recorded_from_resolution_not_transport():
    mapping = WikimediaMapping("topic-1", "AI")
    resolution = parse_wikimedia_resolution(
        mapping,
        (FIXTURES / "wikimedia_resolution_redirect.json").read_bytes(),
        "2026-09-20T00:00:00+00:00",
        wikimedia_resolution_url(mapping),
    )
    payload = json.dumps({"items": [
        {"timestamp": "2026090100", "views": 10},
    ]}).encode()
    report = parse_wikimedia(
        mapping, "2026-09-01", "2026-09-01", payload,
        "2026-09-20T00:00:00+00:00", "https://requested", "https://final", 200,
        resolution=resolution,
    )
    assert report["mapping"]["redirect_resolved"] is True
    assert report["mapping"]["transport_redirected"] is True
    assert report["mapping"]["resolved_article_title"] == "Artificial intelligence"
    assert report["request"]["final_url"] == "https://final"


def test_wikimedia_not_found_is_not_a_zero_series():
    mapping = WikimediaMapping("topic-1", "No such page")
    report = wikimedia_not_found(
        mapping, "2026-09-01", "2026-09-02", b'{"type":"not_found"}',
        "2026-09-20T00:00:00+00:00", "https://requested",
    )
    assert report["status"] == "unavailable_or_zero"
    assert report["status_reason"] == "pageviews_404_cannot_distinguish_zero_from_unloaded_data"
    assert report["observations"] is None
    assert report["total_views_observed"] is None
    assert report["missing_is_zero"] is False


def test_wikimedia_resolved_missing_title_is_not_conflated_with_pageview_404():
    mapping = WikimediaMapping("topic-1", "Definitely missing SAIA page")
    resolution = parse_wikimedia_resolution(
        mapping,
        (FIXTURES / "wikimedia_resolution_missing.json").read_bytes(),
        "2026-09-21T00:00:00+00:00",
        wikimedia_resolution_url(mapping),
    )
    report = wikimedia_not_found(
        mapping, "2026-09-01", "2026-09-02", b"",
        "2026-09-21T00:00:01+00:00", None, http_status=None,
        resolution=resolution,
    )
    assert report["status"] == "not_found"
    assert report["status_reason"] == "title_resolution_not_found"
    assert report["raw_response_bytes_sha256"] is None
    assert report["title_resolution"]["raw_response_bytes_sha256"]


def test_wikimedia_fixture_flow_resolves_title_before_pageviews(monkeypatch):
    resolution_payload = (FIXTURES / "wikimedia_resolution_redirect.json").read_bytes()
    pageviews_payload = json.dumps({"items": [
        {"timestamp": "2026090100", "views": 10},
        {"timestamp": "2026090200", "views": 20},
    ]}).encode()
    called = []

    def fake_urlopen(request, timeout):
        called.append(request.full_url)
        if "/w/api.php?" in request.full_url:
            return FakeResponse(resolution_payload, request.full_url)
        return FakeResponse(pageviews_payload, request.full_url)

    monkeypatch.setattr("saia.external_attention.urlopen", fake_urlopen)
    result = fetch_wikimedia(
        WikimediaMapping("topic-1", "AI"), "2026-09-01", "2026-09-02"
    )
    assert len(called) == 2
    assert "titles=AI" in called[0]
    assert "/Artificial_intelligence/daily/" in called[1]
    assert result["status"] == "complete"
    assert result["mapping"]["resolved_article_title"] == "Artificial intelligence"
    assert result["mapping"]["redirect_resolved"] is True


def test_wikimedia_fixture_flow_stops_when_title_is_missing(monkeypatch):
    payload = (FIXTURES / "wikimedia_resolution_missing.json").read_bytes()
    called = []

    def fake_urlopen(request, timeout):
        called.append(request.full_url)
        return FakeResponse(payload, request.full_url)

    monkeypatch.setattr("saia.external_attention.urlopen", fake_urlopen)
    result = fetch_wikimedia(
        WikimediaMapping("topic-1", "Definitely missing SAIA page"),
        "2026-09-01", "2026-09-02",
    )
    assert len(called) == 1
    assert result["status"] == "not_found"
    assert result["request"]["url"] is None
    assert result["observations"] is None


def test_gdelt_historical_window_is_explicitly_blocked():
    report = gdelt_doc_assessment("2010-01-01", "2017-12-31", "2026-09-20")
    assert report["status"] == "blocked_by_source_window"
    assert report["observations"] is None
    assert report["historical_archive_required"] is True
    assert report["missing_is_zero"] is False


def test_gdelt_recent_window_is_only_eligible_not_fake_observation():
    report = gdelt_doc_assessment("2026-09-01", "2026-09-10", "2026-09-20")
    assert report["status"] == "eligible_recent_window"
    assert report["observations"] is None
