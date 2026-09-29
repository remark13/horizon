from pathlib import Path

from saia.eu_programme_evidence import EUProgrammeQuery, multipart_body, parse_response, request_url


FIXTURES = Path(__file__).parent / "fixtures"


def test_eu_request_uses_public_api_and_multipart_filter():
    query = EUProgrammeQuery("agents", "agentic artificial intelligence", "2025-01-01", "2026-09-21", "2026-09-21", 10)
    assert "apiKey=SEDIA" in request_url(query)
    body, content_type = multipart_body()
    assert b'name="query"; filename="query.json"' in body
    assert b'"language":["en"]' in body
    assert "multipart/form-data" in content_type


def test_eu_fixture_filters_mixed_index_and_keeps_programme_role():
    query = EUProgrammeQuery("agents", "agentic artificial intelligence", "2025-01-01", "2026-09-21", "2026-09-21", 10)
    result = parse_response(query, (FIXTURES / "eu_funding_topics.json").read_bytes(),
                            "2026-09-21T00:00:00+00:00", request_url(query))
    assert result["source"] == "eu_funding_tenders"
    assert result["role"] == "research_programme_only"
    assert result["observed_topic_count"] == 1
    assert result["observations"][0]["programme_topic_id"] == "HORIZON-CL4-2026-AI-01"
    assert result["scientific_score_modified"] is False
