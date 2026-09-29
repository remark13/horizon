import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from saia.news_evidence import NewsQuery, fetch, parse_response, request_url, story_fingerprint


FIXTURES = Path(__file__).parent / "fixtures"


def test_story_fingerprint_supports_non_latin_titles():
    assert story_fingerprint("Новая модель ИИ!") == story_fingerprint("новая модель ии")
    assert story_fingerprint("人工智能的新模型")


def query(**changes):
    values = dict(topic_id="agents", query='"agentic AI"', start="2026-09-14",
                  end="2026-09-21", as_of="2026-09-21", max_records=10)
    values.update(changes)
    return NewsQuery(**values)


def test_gdelt_url_is_bounded_and_reproducible():
    url = request_url(query())
    assert "mode=artlist" in url and "maxrecords=10" in url
    assert "startdatetime=20260914000000" in url
    assert "enddatetime=20260921235959" in url


def test_gdelt_rejects_old_or_future_window():
    with pytest.raises(ValueError, match="свежего"):
        query(start="2026-01-01").validate()
    with pytest.raises(ValueError, match="Некорректный"):
        query(end="2026-09-22").validate()


def test_gdelt_fixture_keeps_metadata_outside_scientific_score():
    payload = (FIXTURES / "gdelt_articles.json").read_bytes()
    result = parse_response(query(), payload, "2026-09-21T10:00:00+00:00",
                            request_url(query()))
    assert result["status"] == "complete"
    assert result["observed_article_count"] == 2
    assert result["observed_domain_count"] == 2
    assert result["unique_story_count"] == 2
    assert result["independent_publishers_established"] is False
    assert result["observations"][0]["seen_date"] == "2026-09-20"
    assert result["stores_article_text"] is False
    assert result["scientific_score_modified"] is False
    assert len(result["report_payload_sha256"]) == 64


def test_gdelt_empty_success_is_not_missing_source():
    result = parse_response(query(), json.dumps({"articles": []}).encode(),
                            "2026-09-21T10:00:00+00:00", request_url(query()))
    assert result["status"] == "empty_observed_response"
    assert result["observed_article_count"] == 0
    assert result["missing_is_zero"] is False


def test_gdelt_groups_syndicated_titles_without_dropping_provenance():
    payload = json.dumps({"articles": [
        {"url": "https://a.example/story", "title": "Same AI Story!",
         "seendate": "20260920T100000Z", "domain": "a.example"},
        {"url": "https://b.example/reprint", "title": "same ai story",
         "seendate": "20260920T110000Z", "domain": "b.example"},
    ]}).encode()
    result = parse_response(query(), payload, "2026-09-21T10:00:00+00:00",
                            request_url(query()))
    assert result["observed_article_count"] == 2
    assert result["unique_story_count"] == 1
    assert result["syndicated_story_groups"][0]["article_count"] == 2
    assert result["syndicated_story_groups"][0]["domains"] == ["a.example", "b.example"]


def test_gdelt_rate_limit_is_not_zero_observations(monkeypatch):
    def limited(*_args, **_kwargs):
        raise HTTPError("https://example.test", 429, "limited", {"Retry-After": "60"}, None)
    monkeypatch.setattr("saia.news_evidence.urlopen", limited)
    result = fetch(query())
    assert result["status"] == "rate_limited"
    assert result["observations"] is None
    assert result["observed_article_count"] is None
    assert result["request"]["retry_after"] == "60"


def test_gdelt_rejects_unbounded_payload():
    from saia.news_evidence import MAX_RESPONSE_BYTES
    with pytest.raises(ValueError, match="bounded connector"):
        parse_response(query(), b"x" * (MAX_RESPONSE_BYTES + 1),
                       "2026-09-21T10:00:00+00:00", request_url(query()))
