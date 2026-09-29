import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from saia.api import app
from saia.europe_pmc_evidence import EuropePmcQuery, parse_response, request_url, unavailable
from saia.external_evidence_store import verify


def _query(max_records=10):
    return EuropePmcQuery("gene-editing", "gene editing", "2024-01-01",
                          "2026-09-23", "2026-09-23", max_records)


def test_europe_pmc_query_limits_sources_and_dates():
    parsed = urlparse(request_url(_query()))
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.ebi.ac.uk"
    args = parse_qs(parsed.query)
    assert 'TITLE_ABS:"gene editing"' in args["query"][0]
    assert "FIRST_PDATE:[2024-01-01 TO 2026-09-23]" in args["query"][0]
    assert "SRC:PPR" in args["query"][0]
    assert "SRC:PAT" not in args["query"][0]
    assert args["pageSize"] == ["100"]
    with pytest.raises(ValueError, match="лимит"):
        _query(51).validate()
    with pytest.raises(ValueError, match="служебные"):
        EuropePmcQuery("x", 'gene editing" OR SRC:PAT', "2024-01-01",
                       "2026-09-23", "2026-09-23").validate()


def test_europe_pmc_deduplicates_its_sources_but_not_with_openalex():
    payload = {"hitCount": 120, "resultList": {"result": [
        {"id": "42000001", "source": "MED", "title": "Gene editing therapy",
         "firstPublicationDate": "2025-01-15", "doi": "10.1234/GENE.1",
         "pmid": "42000001", "pmcid": "PMC10000001"},
        {"id": "PMC10000001", "source": "PMC", "title": "Gene editing therapy",
         "firstPublicationDate": "2025-01-15", "doi": "10.1234/gene.1",
         "pmid": "42000001", "pmcid": "PMC10000001"},
        {"id": "PPR1000001", "source": "PPR", "title": "Gene editing preprint",
         "firstPublicationDate": "2026-02-20", "doi": "10.5555/preprint.1"},
        {"id": "PAT1000001", "source": "PAT", "title": "Gene editing patent",
         "firstPublicationDate": "2025-05-01"},
        {"id": "40000002", "source": "MED", "title": "Undated paper",
         "firstPublicationDate": "2025"},
    ]}}
    report = parse_response(_query(), json.dumps(payload).encode(),
                            "2026-09-23T10:00:00+00:00", request_url(_query()))
    assert [item["source_collection"] for item in report["observations"]] == ["MED", "PPR"]
    assert report["observations"][1]["is_preprint"] is True
    assert report["duplicate_source_records_collapsed"] == 1
    assert report["excluded_nonpublication_records"] == 1
    assert report["excluded_unknown_dates"] == 1
    assert report["reported_total_search_hits"] == 120
    assert report["source_result_is_exhaustive"] is False
    assert report["overlap_with_openalex_and_preprints_requires_dedup"] is True
    assert report["independent_publication_count_established"] is False
    assert report["retrospective_safe"] is False
    assert report["scientific_score_modified"] is False
    assert verify(report) is report


def test_europe_pmc_unavailable_does_not_mean_no_publications():
    report = unavailable(_query(), "rate_limited", "2026-09-23T10:00:00+00:00",
                         request_url(_query()), 429, retry_after="60")
    assert report["observations"] is None
    assert report["observed_paper_count"] is None
    assert report["missing_is_zero"] is False
    assert verify(report) is report


def test_europe_pmc_api_and_form_are_available_without_saving(monkeypatch):
    from saia import europe_pmc_evidence

    def fake_fetch(query):
        return parse_response(query, b'{"hitCount":0,"resultList":{"result":[]}}',
                              "2026-09-23T10:00:00+00:00", request_url(query))

    monkeypatch.setattr(europe_pmc_evidence, "fetch", fake_fetch)
    client = TestClient(app)
    response = client.post("/external-evidence/biomedical/europe-pmc", json={
        "topic_id": "gene-editing", "query": "gene editing",
        "start": "2024-01-01", "end": "2026-09-23",
        "as_of": "2026-09-23", "max_records": 5,
    })
    assert response.status_code == 200
    assert response.json()["source"] == "europe_pmc"
    assert response.json()["status"] == "empty_observed_response"
    assert "saved" not in response.json()
    page = client.get("/external-evidence")
    assert 'id="europe-pmc"' in page.text

