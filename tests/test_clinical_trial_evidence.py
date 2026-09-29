import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from saia.api import app
from saia.clinical_trial_evidence import ClinicalTrialQuery, parse_response, request_url, unavailable
from saia.external_evidence_store import verify


def _study(nct_id, title, first_posted, *, summary="", intervention=None):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id, "briefTitle": title},
            "statusModule": {
                "studyFirstPostDateStruct": {"date": first_posted},
                "overallStatus": "RECRUITING",
            },
            "descriptionModule": {"briefSummary": summary},
            "armsInterventionsModule": {"interventions": [
                {"name": intervention}] if intervention else []},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "University"}},
            "conditionsModule": {"conditions": ["Rare disease"]},
        },
        "hasResults": False,
    }


def _query():
    return ClinicalTrialQuery(
        "gene-editing", "gene editing", "2024-01-01", "2026-09-23",
        "2026-09-23", 5,
    )


def test_clinical_trial_request_is_bounded_and_uses_official_endpoint():
    url = request_url(_query())
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "clinicaltrials.gov"
    assert parsed.path == "/api/v2/studies"
    assert parse_qs(parsed.query)["query.term"] == ["gene editing"]
    assert parse_qs(parsed.query)["pageSize"] == ["100"]
    with pytest.raises(ValueError, match="лимит"):
        ClinicalTrialQuery("x", "gene editing", "2024-01-01", "2026-09-23",
                           "2026-09-23", 51).validate()


def test_clinical_trial_keeps_only_visible_matches_and_never_scores():
    payload = {"totalCount": 182, "studies": [
        _study("NCT00000001", "Gene Editing Therapy", "2025-01-02"),
        _study("NCT00000002", "Unrelated Platform", "2025-02-02"),
        _study("NCT00000003", "Rare Disease Protocol", "2025-03-02",
               summary="This protocol tests gene editing."),
        _study("NCT00000004", "Older Gene Editing", "2023-03-02"),
        _study("NCT00000005", "No Date Gene Editing", "2025-03"),
        _study("NCT00000006", "Cell Treatment", "2025-04-02",
               intervention="Gene Editing Product"),
    ]}
    result = parse_response(_query(), json.dumps(payload).encode(),
                            "2026-09-23T10:00:00+00:00", request_url(_query()))
    assert [item["nct_id"] for item in result["observations"]] == [
        "NCT00000001", "NCT00000003", "NCT00000006",
    ]
    assert result["observations"][0]["query_match_fields"] == ["title"]
    assert result["observations"][1]["query_match_fields"] == ["brief_summary"]
    assert result["observations"][2]["query_match_fields"] == ["intervention"]
    assert result["excluded_without_visible_query_match"] == 1
    assert result["unknown_first_posted_dates"] == 1
    assert result["reported_total_search_hits"] == 182
    assert result["examined_source_records"] == 6
    assert result["source_page_records"] == 6
    assert result["source_result_is_exhaustive"] is False
    assert result["local_match_is_exhaustive"] is False
    assert result["retrospective_safe"] is False
    assert result["scientific_score_modified"] is False
    assert verify(result) is result


def test_clinical_trial_empty_and_unavailable_are_not_absence_of_activity():
    result = parse_response(_query(), b'{"studies":[],"totalCount":0}',
                            "2026-09-23T10:00:00+00:00", request_url(_query()))
    assert result["status"] == "empty_observed_response"
    assert result["observed_study_count"] == 0
    assert result["missing_is_zero"] is False
    failed = unavailable(_query(), "rate_limited", "2026-09-23T10:00:00+00:00",
                         request_url(_query()), 429, retry_after="60")
    assert failed["observations"] is None
    assert failed["observed_study_count"] is None
    assert failed["missing_is_zero"] is False
    assert verify(failed) is failed


def test_clinical_trial_route_keeps_save_optional(monkeypatch):
    from saia import clinical_trial_evidence

    def fake_fetch(query):
        return parse_response(query, b'{"studies":[],"totalCount":0}',
                              "2026-09-23T10:00:00+00:00", request_url(query))

    monkeypatch.setattr(clinical_trial_evidence, "fetch", fake_fetch)
    response = TestClient(app).post(
        "/external-evidence/clinical-trials/clinicaltrials-gov",
        json={"topic_id": "gene-editing", "query": "gene editing",
              "start": "2024-01-01", "end": "2026-09-23",
              "as_of": "2026-09-23", "max_records": 5},
    )
    assert response.status_code == 200
    assert response.json()["source"] == "clinicaltrials_gov"
    assert "saved" not in response.json()
    page = TestClient(app).get("/external-evidence")
    assert page.status_code == 200
    assert 'id="clinical-trials"' in page.text
    assert "Совпадение в записи" in page.text
