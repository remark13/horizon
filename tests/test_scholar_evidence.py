import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

from saia.scholar_evidence import ScholarQuery, fetch, parse_response, request_url


FIXTURES = Path(__file__).parent / "fixtures"


def query(**changes):
    values = dict(topic_id="agents", query="agentic artificial intelligence",
                  start="2025-01-01", end="2026-09-21", as_of="2026-09-21",
                  max_records=10)
    values.update(changes)
    return ScholarQuery(**values)


def test_semantic_scholar_url_is_bounded_and_year_scoped():
    url = request_url(query())
    assert "limit=10" in url and "offset=0" in url
    assert "year=2025-2026" in url
    assert "citationCount" in url


def test_semantic_scholar_rejects_future_window():
    with pytest.raises(ValueError, match="Некорректный"):
        query(end="2026-09-22").validate()


def test_semantic_scholar_fixture_is_enrichment_not_independent_count():
    payload = (FIXTURES / "semantic_scholar_search.json").read_bytes()
    result = parse_response(query(), payload, "2026-09-21T10:00:00+00:00",
                            request_url(query()))
    assert result["status"] == "complete"
    assert result["observed_paper_count"] == 1
    assert result["observations"][0]["external_ids"]["ArXiv"] == "2601.00001"
    assert result["overlap_with_primary_corpus_unknown"] is True
    assert result["citation_counts_as_of_retrieval_only"] is True
    assert result["retrospective_safe"] is False
    assert result["scientific_score_modified"] is False


def test_semantic_scholar_anonymous_rate_limit_is_unknown(monkeypatch):
    def limited(*_args, **_kwargs):
        raise HTTPError("https://example.test", 429, "limited", {}, io.BytesIO(b"limited"))
    monkeypatch.setattr("saia.scholar_evidence.urlopen", limited)
    result = fetch(query(), api_key="")
    assert result["status"] == "rate_limited"
    assert result["observations"] is None
    assert result["request"]["authenticated"] is False
    assert result["missing_is_zero"] is False

