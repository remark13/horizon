import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from saia.api import app
from saia.external_evidence_store import verify
from saia.nasa_ntrs_evidence import NasaNtrsQuery, parse_response, request_url, unavailable


def _query(max_records=10):
    return NasaNtrsQuery("small-satellites", "small satellite", "2024-01-01",
                         "2026-09-23", "2026-09-23", max_records)


def test_nasa_query_is_bounded_and_date_filtered():
    parsed = urlparse(request_url(_query()))
    assert parsed.netloc == "ntrs.nasa.gov"
    args = parse_qs(parsed.query)
    assert args["q"] == ["small satellite"]
    assert args["published.gte"] == ["2024-01-01"]
    assert args["published.lte"] == ["2026-09-23"]
    assert args["page.size"] == ["100"]
    with pytest.raises(ValueError, match="лимит"):
        _query(51).validate()
    with pytest.raises(ValueError, match="служебные"):
        NasaNtrsQuery("x", 'small satellite" OR q:*', "2024-01-01",
                      "2026-09-23", "2026-09-23").validate()


def test_nasa_response_filters_visible_phrase_and_patent_and_keeps_date_basis():
    data = {"stats": {"total": 70}, "results": [
        {"id": 20250000001, "title": "Small satellite swarm research",
         "stiType": "TECHNICAL_REPORT", "distributionDate": "2025-03-05T00:00:00Z",
         "publications": [{"publicationDate": "2025-02-20T00:00:00Z"}]},
        {"id": 20250000002, "title": "Small satellite control invention",
         "stiType": "PATENT", "distributionDate": "2025-03-05T00:00:00Z"},
        {"id": 20250000003, "title": "Unrelated research",
         "stiType": "TECHNICAL_REPORT", "distributionDate": "2025-03-05T00:00:00Z"},
        {"id": 20250000004, "title": "Small satellite without date",
         "stiType": "TECHNICAL_REPORT"},
        {"id": 20250000001, "title": "Small satellite swarm research",
         "stiType": "TECHNICAL_REPORT", "distributionDate": "2025-03-05T00:00:00Z"},
    ]}
    report = parse_response(_query(), json.dumps(data).encode(),
                            "2026-09-23T08:00:00Z", request_url(_query()))
    assert report["observed_paper_count"] == 1
    assert report["observations"][0]["date_basis"] == "publication"
    assert report["excluded_nonpublication_records"] == 1
    assert report["excluded_without_visible_query_match"] == 1
    assert report["excluded_unknown_dates"] == 1
    assert report["examined_source_records"] == 5
    assert report["eligible_source_records"] == 1
    assert report["source_result_is_exhaustive"] is False
    assert report["independent_publication_count_established"] is False
    assert report["scientific_score_modified"] is False
    assert verify(report) is report


def test_nasa_excludes_abstract_only_even_if_ranked_first_by_source():
    data = {"stats": {"total": 2}, "results": [
        {"id": 20250000010, "title": "General flight study", "abstract": "Small satellite experiment.",
         "distributionDate": "2025-02-01T00:00:00Z"},
        {"id": 20250000011, "title": "Small satellite experiment",
         "distributionDate": "2025-01-01T00:00:00Z"},
    ]}
    query = _query(1)
    report = parse_response(query, json.dumps(data).encode(),
                            "2026-09-23T08:00:00Z", request_url(query))
    assert report["observations"][0]["citation_id"] == 20250000011
    assert report["eligible_source_records"] == 1
    assert report["excluded_abstract_only_match"] == 1
    assert report["source_result_is_exhaustive"] is True


def test_nasa_unavailable_never_means_zero():
    report = unavailable(_query(), "rate_limited", "2026-09-23T08:00:00Z",
                         request_url(_query()), 429)
    assert report["observations"] is None
    assert report["observed_paper_count"] is None
    assert report["missing_is_zero"] is False
    assert verify(report) is report


def test_nasa_api_and_form(monkeypatch):
    from saia import nasa_ntrs_evidence

    monkeypatch.setattr(nasa_ntrs_evidence, "fetch", lambda query: parse_response(
        query, b'{"stats":{"total":0},"results":[]}',
        "2026-09-23T08:00:00Z", request_url(query),
    ))
    client = TestClient(app)
    result = client.post("/external-evidence/aerospace/nasa-ntrs", json={
        "topic_id": "small-satellites", "query": "small satellite",
        "start": "2024-01-01", "end": "2026-09-23", "as_of": "2026-09-23",
        "max_records": 5,
    })
    assert result.status_code == 200
    assert result.json()["source"] == "nasa_ntrs"
    assert result.json()["status"] == "empty_observed_response"
    assert 'id="nasa-ntrs"' in client.get("/external-evidence").text
