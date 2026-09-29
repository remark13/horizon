from pathlib import Path

from saia.huggingface_evidence import HuggingFaceQuery, parse_response, request_urls


FIXTURES = Path(__file__).parent / "fixtures"


def test_huggingface_queries_all_three_surfaces():
    query = HuggingFaceQuery("agents", "agentic AI", "2025-01-01", "2026-09-21", "2026-09-21", 5)
    urls = request_urls(query)
    assert set(urls) == {"models", "datasets", "spaces"}
    assert all("limit=5" in value for value in urls.values())


def test_huggingface_fixture_does_not_treat_current_engagement_as_history():
    query = HuggingFaceQuery("agents", "agentic AI", "2025-01-01", "2026-09-21", "2026-09-21", 5)
    payloads = {surface: (FIXTURES / f"huggingface_{surface}.json").read_bytes()
                for surface in ("models", "datasets", "spaces")}
    result = parse_response(query, payloads, "2026-09-21T00:00:00+00:00", request_urls(query))
    assert result["observed_artifact_count"] == 3
    assert result["observed_counts_by_type"] == {"model": 1, "dataset": 1, "space": 1}
    assert result["current_engagement_is_historical_evidence"] is False
    assert result["retrospective_safe"] is False
    assert result["scientific_score_modified"] is False
