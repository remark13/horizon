import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

from saia.funding_evidence import FundingQuery, fetch, parse_response, request_body


FIXTURES = Path(__file__).parent / "fixtures"


def query(**changes):
    values = dict(topic_id="agents", query="agentic artificial intelligence",
                  start="2025-01-01", end="2026-09-21", as_of="2026-09-21",
                  max_records=10)
    values.update(changes)
    return FundingQuery(**values)


def test_nih_request_is_bounded_and_date_scoped():
    body = request_body(query())
    assert body["limit"] == 10 and body["offset"] == 0
    assert body["criteria"]["project_start_date"] == {
        "from_date": "2025-01-01", "to_date": "2026-09-21",
    }
    assert body["criteria"]["advanced_text_search"]["search_field"] == "all"


def test_nih_query_rejects_future_window_and_control_text():
    with pytest.raises(ValueError, match="Некорректный"):
        query(end="2026-09-22").validate()
    with pytest.raises(ValueError, match="управляющие"):
        query(query="agentic\nai").validate()


def test_nih_fixture_preserves_project_year_and_core_identity():
    payload = (FIXTURES / "nih_reporter_projects.json").read_bytes()
    result = parse_response(query(), payload, "2026-09-21T10:00:00+00:00",
                            "https://api.reporter.nih.gov/v2/projects/search")
    assert result["status"] == "complete"
    assert result["observed_project_record_count"] == 2
    assert result["observed_core_project_count"] == 1
    assert result["observed_award_amount_sum_usd"] == 950000
    assert result["observations"][0]["organization"] == "Example University"
    assert result["observations"][0]["url"].endswith("/10000002")
    assert result["project_family_deduplication_applied"] is False
    assert result["result_cap_reached"] is True
    assert result["scientific_score_modified"] is False


def test_nih_rate_limit_is_unknown_not_zero(monkeypatch):
    def limited(*_args, **_kwargs):
        raise HTTPError("https://example.test", 429, "limited",
                        {"Retry-After": "60"}, io.BytesIO(b"limited"))
    monkeypatch.setattr("saia.funding_evidence.urlopen", limited)
    result = fetch(query())
    assert result["status"] == "rate_limited"
    assert result["observations"] is None
    assert result["observed_project_record_count"] is None
    assert result["missing_is_zero"] is False
