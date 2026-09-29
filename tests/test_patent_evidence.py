import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from saia.patent_evidence import PatentQuery, cql, fetch, parse_response, search_url


FIXTURES = Path(__file__).parent / "fixtures"


def query(**changes):
    values = dict(topic_id="agents", phrase="agentic artificial intelligence",
                  start="2025-01-01", end="2026-09-21", as_of="2026-09-21",
                  max_records=10)
    values.update(changes)
    return PatentQuery(**values)


def test_epo_query_is_bounded_and_excludes_unknown_dates():
    value = cql(query())
    assert 'ta="agentic artificial intelligence"' in value
    assert "pd>=20250101" in value and "pd<=20260921" in value
    assert "pd>00000000" in value
    assert "Range=1-10" in search_url(query())


def test_epo_rejects_cql_control_characters_and_future_window():
    with pytest.raises(ValueError, match="недопустимые"):
        query(phrase='agentic" OR pa=anything').validate()
    with pytest.raises(ValueError, match="Некорректный"):
        query(end="2026-09-22").validate()


def test_epo_fixture_extracts_bibliography_without_claiming_signal():
    payload = (FIXTURES / "epo_ops_biblio.xml").read_bytes()
    result = parse_response(query(), payload, "2026-09-21T10:00:00+00:00",
                            search_url(query()))
    assert result["status"] == "complete"
    assert result["reported_total_results"] == 27
    assert result["observed_publication_count"] == 2
    assert result["unique_family_ids_observed"] == 2
    assert result["result_cap_reached"] is True
    assert result["observations"][0]["publication_id"] == "EP4000001A1"
    assert result["observations"][0]["applicants"] == ["Example Research Ltd"]
    assert result["stores_full_text"] is False
    assert result["family_deduplication_applied"] is False
    assert result["scientific_score_modified"] is False


def test_epo_without_credentials_is_explicitly_blocked(monkeypatch):
    monkeypatch.delenv("EPO_OPS_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("EPO_OPS_CONSUMER_SECRET", raising=False)
    result = fetch(query())
    assert result["status"] == "credentials_required"
    assert result["observations"] is None
    assert result["observed_publication_count"] is None
    assert result["request"]["credentials_stored"] is False


def test_epo_rejected_credentials_are_not_empty_results(monkeypatch):
    def rejected(*_args, **_kwargs):
        raise HTTPError("https://example.test", 403, "forbidden", {}, io.BytesIO(b"denied"))
    monkeypatch.setattr("saia.patent_evidence.urlopen", rejected)
    result = fetch(query(), consumer_key="key", consumer_secret="secret")
    assert result["status"] == "credentials_rejected"
    assert result["observations"] is None
    assert result["raw_response_bytes_sha256"]


def test_epo_rejects_unbounded_payload():
    from saia.patent_evidence import MAX_RESPONSE_BYTES
    with pytest.raises(ValueError, match="bounded connector"):
        parse_response(query(), b"x" * (MAX_RESPONSE_BYTES + 1),
                       "2026-09-21T10:00:00+00:00", search_url(query()))
