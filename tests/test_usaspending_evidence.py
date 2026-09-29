import json

import pytest
from fastapi.testclient import TestClient

from saia.api import app
from saia.external_evidence_store import verify
from saia.usaspending_evidence import (
    USAspendingQuery, parse_response, request_payload, unavailable,
)


def _query(max_records=10):
    return USAspendingQuery("unmanned-aircraft", "unmanned aerial vehicle",
                           "2024-01-01", "2026-09-23", "2026-09-23", max_records)


def _row(identifier="CONT_AWD_1232SA26P0628_12H2_-NONE-_-NONE-",
         description="American-made unmanned aerial vehicle for crop spraying"):
    return {"generated_internal_id": identifier, "Award ID": "1232SA26P0628",
            "Recipient Name": "FRONTIER PRECISION, INC", "Description": description,
            "Start Date": "2026-09-07", "Award Amount": 63526.0,
            "Awarding Agency": "Department of Agriculture"}


def test_usaspending_request_is_bounded_and_contract_only():
    payload = request_payload(_query())
    assert payload["filters"]["keywords"] == ["unmanned aerial vehicle"]
    assert payload["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert payload["filters"]["time_period"][0]["end_date"] == "2026-09-23"
    assert payload["limit"] == 50
    assert payload["page"] == 1
    with pytest.raises(ValueError, match="лимит"):
        _query(21).validate()
    with pytest.raises(ValueError, match="01.10.2007"):
        USAspendingQuery("x", "drone", "2000-01-01", "2026-09-23", "2026-09-23").validate()


def test_usaspending_filters_invisible_matches_and_unknown_identity():
    payload = {"results": [
        _row(),
        _row("CONT_AWD_OTHER_1", "Unrelated equipment"),
        {**_row("CONT_AWD_BAD_2"), "Start Date": "2027-01-01"},
        {**_row("bad-id"), "Start Date": "2025-01-01"},
    ], "page_metadata": {"page": 1, "hasNext": True}}
    report = parse_response(_query(), json.dumps(payload).encode(),
                            "2026-09-23T12:00:00Z",
                            "https://api.usaspending.gov/api/v2/search/spending_by_award/")
    assert report["status"] == "complete"
    assert report["observed_award_count"] == 1
    assert report["excluded_without_visible_query_match"] == 1
    assert report["excluded_without_valid_award_date"] == 1
    assert report["excluded_without_valid_identity"] == 1
    assert report["source_result_is_exhaustive"] is False
    assert report["private_investment_established"] is False
    assert report["scientific_score_modified"] is False
    assert report["observations"][0]["url"].startswith("https://www.usaspending.gov/award/CONT_AWD_")
    assert verify(report) is report


def test_usaspending_invalid_and_unavailable_never_mean_zero():
    with pytest.raises(ValueError, match="ожидаемую страницу"):
        parse_response(_query(), b'{"results":[]}', "2026-09-23T12:00:00Z", "https://api.usaspending.gov/")
    report = unavailable(_query(), "source_unavailable", "2026-09-23T12:00:00Z",
                         "https://api.usaspending.gov/")
    assert report["observations"] is None
    assert report["observed_award_count"] is None
    assert report["missing_is_zero"] is False
    assert verify(report) is report


def test_usaspending_endpoint_and_form(monkeypatch):
    from saia import usaspending_evidence
    monkeypatch.setattr(usaspending_evidence, "fetch", lambda query: parse_response(
        query, b'{"results":[],"page_metadata":{"page":1,"hasNext":false}}',
        "2026-09-23T12:00:00Z", "https://api.usaspending.gov/api/v2/search/spending_by_award/",
    ))
    client = TestClient(app)
    result = client.post("/external-evidence/procurement/usaspending", json={
        "topic_id": "unmanned-aircraft", "query": "unmanned aerial vehicle",
        "start": "2024-01-01", "end": "2026-09-23", "as_of": "2026-09-23",
        "max_records": 5,
    })
    assert result.status_code == 200
    assert result.json()["source"] == "usaspending"
    assert result.json()["status"] == "empty_observed_response"
    page = client.get("/external-evidence").text
    assert 'id="usaspending"' in page
    assert "/external-evidence/procurement/usaspending" in page
