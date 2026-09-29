from pathlib import Path

import pytest

from saia.ukri_evidence import UKRIQuery, parse_response, request_url


FIXTURES = Path(__file__).parent / "fixtures"


def test_ukri_query_is_bounded():
    query = UKRIQuery("agents", "agentic artificial intelligence", "2025-01-01", "2026-09-21", "2026-09-21", 10)
    url = request_url(query)
    assert "fetchSize=25" in url and "page=1" in url


def test_ukri_fixture_keeps_funding_separate_from_score():
    query = UKRIQuery("agents", "agentic artificial intelligence", "2025-01-01", "2026-09-21", "2026-09-21", 10)
    result = parse_response(query, (FIXTURES / "ukri_projects.json").read_bytes(),
                            "2026-09-21T00:00:00+00:00", request_url(query))
    assert result["source"] == "ukri_gtr"
    assert result["observed_project_count"] == 1
    assert result["observations"][0]["award_amount_gbp"] == 1250000
    assert result["scientific_score_modified"] is False
    assert result["source_result_is_exhaustive"] is False


def test_ukri_rejects_future_window():
    with pytest.raises(ValueError, match="интервал"):
        UKRIQuery("agents", "agentic AI", "2025-01-01", "2027-01-01", "2026-09-21").validate()
