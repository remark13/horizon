"""Synthetic contracts; live probes are reported separately, not mocked accuracy."""
import json
from datetime import date, datetime
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from saia import datacite_evidence as datacite, nsf_evidence as nsf, openaire_project_evidence as openaire
from saia import public_metadata as common, source_context
from saia.api import app
from saia.external_evidence_store import verify
from saia.external_sources import load_policy
from saia.source_registry import load_registry, validate_runtime_sources

MODULES = [(nsf, nsf.NSFQuery), (datacite, datacite.DataCiteQuery), (openaire, openaire.OpenAIREProjectQuery)]
STAMP = "2026-09-28T12:00:00Z"


def query(cls, **changes):
    return cls(**{"topic_id": "synthetic-test-topic", "query": "robot control", "start": "2021-09-24",
                  "end": "2026-09-28", "as_of": "2026-09-28", "max_records": 5, **changes})


def nsf_row(**changes):
    return {"id": "2600001", "title": "Robot control research", "date": "08/01/2026",
            "startDate": "01/01/2027", "expDate": "12/31/2029", "awardeeName": "Synthetic Institute",
            "awardeeCountryCode": "US", "estimatedTotalAmt": "500000", "fundsObligatedAmt": "200000",
            "abstractText": "Synthetic private body", "piEmail": "do-not-save@example.invalid", **changes}


def datacite_row(**changes):
    attrs = {"doi": "10.1234/synthetic.dataset.1", "titles": [{"title": "Robot control dataset"}],
             "types": {"resourceTypeGeneral": "Dataset"}, "state": "findable", "isActive": True,
             "publicationYear": 2026, "dates": [{"date": "2026-05-01", "dateType": "Issued"}],
             "created": "2026-07-01T00:00:00Z", "publisher": "Synthetic repository", "language": "en",
             "descriptions": [{"description": "Synthetic unpublished body"}],
             "rightsList": [{"rights": "Restricted resource files"}], **changes}
    return {"id": attrs["doi"], "type": "dois", "attributes": attrs}


def openaire_row(**changes):
    return {"id": "corda__he::synthetic", "code": "101000001", "title": "Robot control project",
            "startDate": "2026-08-01", "endDate": "2029-07-31",
            "fundings": [{"shortName": "EC", "name": "European Commission", "jurisdiction": "EU"}],
            "granted": {"currency": "EUR", "fundedAmount": 100000, "totalCost": 0},
            "links": [{"header": {"relationClass": "hasParticipant"}, "legalname": "Synthetic Partner", "country": {"code": "FR"}}],
            "summary": "Synthetic full summary must not be stored", **changes}


def parse(module, cls, rows, **changes):
    q = query(cls, **changes)
    envelopes = {nsf: {"response": {"award": rows, "metadata": {"totalCount": len(rows)}}},
                 datacite: {"data": rows, "meta": {"total": len(rows)}},
                 openaire: {"results": rows, "header": {"numFound": len(rows)}}}
    return module.parse_response(q, json.dumps(envelopes[module]).encode(), STAMP, module.request_url(q))


@pytest.mark.parametrize("module,cls", MODULES)
@pytest.mark.parametrize("change", [{"query": "x"}, {"query": "robot:control"}, {"query": "robot\ncontrol"},
                                  {"query": "----"}, {"end": "2026-09-29"}, {"start": "2027-01-01"},
                                  {"max_records": 16}, {"max_records": True}, {"start": datetime(2021, 1, 1)}])
def test_queries_are_plain_bounded_and_not_after_cutoff(module, cls, change):
    with pytest.raises(ValueError):
        query(cls, **change).validate()


def test_new_requests_use_documented_date_filters_and_one_bounded_page():
    n = parse_qs(urlsplit(nsf.request_url(query(nsf.NSFQuery))).query)
    assert n["dateStart"] == ["09/24/2021"] and "startDateStart" not in n
    assert n["keyword"] == ["robot AND control"] and n["rpp"] == ["25"] and n["offset"] == ["0"]
    literal = parse_qs(urlsplit(nsf.request_url(query(nsf.NSFQuery, query="robot OR control"))).query)
    assert literal["keyword"] == ['robot AND "or" AND control']
    d = parse_qs(urlsplit(datacite.request_url(query(datacite.DataCiteQuery))).query)
    assert d["resource-type-id"] == ["dataset,software"]
    assert '"robot" AND "control"' in d["query"][0] and "publicationYear:[2021 TO 2026]" in d["query"][0]
    assert "created" not in d["query"][0] and d["page[size]"] == ["25"]
    o = parse_qs(urlsplit(openaire.request_url(query(openaire.OpenAIREProjectQuery))).query)
    assert o["fromStartDate"] == ["2021-09-24"] and o["toStartDate"] == ["2026-09-28"]
    assert o["page"] == ["1"] and o["pageSize"] == ["25"]
    # A caller's operator-looking word cannot become executable query syntax.
    assert common.quoted_and("robot OR control") == '"robot" AND "or" AND "control"'


def test_nsf_award_date_is_not_project_start_or_paid_expenditure():
    result = parse(nsf, nsf.NSFQuery, [nsf_row()])
    assert verify(result) is result
    row = result["observations"][0]
    assert row["award_date"] == "2026-08-01" and row["project_start_planned"] is True
    assert row["funding_amount"] == 200000 and row["estimated_total_amount_usd"] == 500000
    assert row["funding_amount_basis"] == "reported_obligation_not_paid_expenditure"
    assert "Synthetic private body" not in str(result) and "do-not-save@" not in str(result)
    assert result["contains_private_investment_deals"] is False


def test_nsf_local_filter_removes_source_or_search_noise_and_future_awards():
    result = parse(nsf, nsf.NSFQuery, [nsf_row(id="2600002", title="Robot physics"),
                                    nsf_row(id="2600003", date="09/29/2026"), nsf_row(), nsf_row()])
    assert len(result["observations"]) == 1
    assert result["rejected_records"]["query_terms"] == result["rejected_records"]["outside_award_interval"] == result["rejected_records"]["duplicate"] == 1
    assert result["source_result_is_exhaustive"] is False


def test_nsf_zero_reported_obligation_is_not_missing_but_unknown_is_none():
    rows = parse(nsf, nsf.NSFQuery, [nsf_row(fundsObligatedAmt="0"), nsf_row(id="2600002", fundsObligatedAmt=None)])["observations"]
    assert sorted(r["funding_amount"] == 0 for r in rows) == [False, True]
    assert any(r["funding_amount"] is None for r in rows)


@pytest.mark.parametrize("notice", [{"message": "ERROR invalid query"}, [{"type": "FATAL"}]])
def test_nsf_error_inside_http_200_is_not_empty_success(notice):
    q = query(nsf.NSFQuery)
    raw = json.dumps({"response": {"metadata": {"totalCount": 0}}, "serviceNotification": notice}).encode()
    with pytest.raises(ValueError):
        nsf.parse_response(q, raw, STAMP, nsf.request_url(q))


def test_nsf_documented_zero_envelope_can_omit_award_list():
    q = query(nsf.NSFQuery)
    result = nsf.parse_response(q, b'{"response":{"metadata":{"totalCount":0}}}', STAMP, nsf.request_url(q))
    assert result["status"] == "empty_observed_response" and result["observations"] == []
    assert result["missing_is_zero"] is False


def test_datacite_is_a_research_artifact_with_separate_metadata_and_resource_rights():
    result = parse(datacite, datacite.DataCiteQuery, [datacite_row()])
    assert verify(result) is result
    row = result["observations"][0]
    assert row["resource_publication_date"] == "2026-05-01" and row["date_precision"] == "day"
    assert row["doi_created_at"] == "2026-07-01T00:00:00Z"
    assert row["metadata_licence"] == "CC0" and row["resource_rights"][0]["rights"] == "Restricted resource files"
    assert result["resource_licence_inferred"] is False and result["stores_files"] is False
    assert "Synthetic unpublished body" not in str(result)
    assert row["source_country"] is None and row["independent_confirmation"] is False


def test_datacite_keeps_year_only_precision_without_january_first_imputation():
    old = datacite_row(publicationYear=2025, dates=[{"date": "2025", "dateType": "Issued"}], created="2026-09-28T00:00:00Z")
    result = parse(datacite, datacite.DataCiteQuery, [old])
    assert result["observations"][0]["resource_publication_date"] == "2025"
    assert result["observations"][0]["date_precision"] == "year"
    for year in [2021, 2026]:
        result = parse(datacite, datacite.DataCiteQuery, [datacite_row(publicationYear=year, dates=[])])
        assert result["observations"] == [] and result["rejected_records"]["year_only_boundary_unknown"] == 1


def test_datacite_future_or_conflicting_issue_dates_are_excluded():
    future = datacite_row(dates=[{"date": "2026-10-01", "dateType": "Issued"}])
    mismatch = datacite_row(dates=[{"date": "2025-10-01", "dateType": "Issued"}])
    result = parse(datacite, datacite.DataCiteQuery, [future, mismatch])
    assert result["observations"] == []
    assert result["rejected_records"] == {"outside_issue_interval": 1, "ambiguous_issue_date": 1}


def test_datacite_exact_doi_dedup_does_not_claim_family_or_version_dedup():
    version = datacite_row(doi="10.1234/synthetic.dataset.2", version="2", relatedIdentifiers=[
        {"relatedIdentifierType": "DOI", "relatedIdentifier": "10.1234/synthetic.dataset.1", "relationType": "IsVersionOf"}])
    result = parse(datacite, datacite.DataCiteQuery, [datacite_row(), datacite_row(doi="10.1234/SYNTHETIC.DATASET.1"), version])
    assert len(result["observations"]) == 2 and result["rejected_records"]["duplicate_doi"] == 1
    assert result["research_family_deduplication_applied"] is False
    assert result["observations"][1]["related_dois"][0]["relation"] == "IsVersionOf"


def test_datacite_does_not_mix_journal_articles_into_dataset_channel():
    result = parse(datacite, datacite.DataCiteQuery, [datacite_row(), datacite_row(doi="10.1234/article", types={"resourceTypeGeneral": "JournalArticle"})])
    assert len(result["observations"]) == 1
    assert result["rejected_records"]["invalid_identity_type_or_state"] == 1


def test_openaire_keeps_upstream_funders_amounts_and_participant_geography_separate():
    result = parse(openaire, openaire.OpenAIREProjectQuery, [openaire_row()])
    assert verify(result) is result
    row = result["observations"][0]
    assert row["url"] == "https://cordis.europa.eu/project/id/101000001"
    assert row["funding_amount"] == 100000 and row["funding_currency"] == "EUR"
    assert row["source_country"] is None and row["participant_country_codes"] == ["FR"]
    assert row["funders"][0]["shortName"] == "EC" and result["attribution"] == "OpenAIRE Graph"
    assert result["upstream_grant_deduplication_applied"] is False
    assert "Synthetic full summary" not in str(result)
    assert result["request"]["url"].startswith("https://api.openaire.eu/graph/v3/projects?")


@pytest.mark.parametrize("granted", [{"currency": "EUR", "fundedAmount": 0}, {"fundedAmount": 2000}, {"currency": "EUR", "fundedAmount": "NaN"}])
def test_openaire_ambiguous_zero_or_unknown_currency_is_not_a_known_financing_amount(granted):
    row = parse(openaire, openaire.OpenAIREProjectQuery, [openaire_row(granted=granted)])["observations"][0]
    assert row["funding_amount"] is None


def test_openaire_future_start_filtered_even_if_remote_filter_is_ignored():
    result = parse(openaire, openaire.OpenAIREProjectQuery, [openaire_row(startDate="2027-01-01"), openaire_row(id="corda__he::valid")])
    assert len(result["observations"]) == 1 and result["rejected_records"]["outside_start_interval"] == 1


def test_openaire_non_ec_project_uses_safe_openaire_source_link():
    row = parse(openaire, openaire.OpenAIREProjectQuery, [openaire_row(fundings=[{"shortName": "NSF", "name": "NSF"}])])["observations"][0]
    assert row["url"] == "https://explore.openaire.eu/search/project?projectId=corda__he%3A%3Asynthetic"
    assert row["independent_confirmation"] is False


@pytest.mark.parametrize("module,cls,row", [(nsf, nsf.NSFQuery, nsf_row), (datacite, datacite.DataCiteQuery, datacite_row), (openaire, openaire.OpenAIREProjectQuery, openaire_row)])
def test_response_records_are_capped_and_overlarge_pages_rejected(module, cls, row):
    result = parse(module, cls, [row()] * 3, max_records=1)
    assert len(result["observations"]) <= 1 and result["scientific_score_modified"] is False
    with pytest.raises(ValueError):
        parse(module, cls, [row()] * 26)
    with pytest.raises(ValueError):
        module.parse_response(query(cls), b"x" * 2_000_001, STAMP, module.request_url(query(cls)))


@pytest.mark.parametrize("module,cls", MODULES)
def test_invalid_json_or_missing_schema_is_not_empty_success(module, cls):
    for raw in [b"<html>blocked</html>", b"[]", b"{}", b'{"num":NaN}']:
        with pytest.raises(ValueError):
            module.parse_response(query(cls), raw, STAMP, module.request_url(query(cls)))


@pytest.mark.parametrize("number", [None, "NaN", "inf", float("inf"), True, -10])
def test_non_finite_or_missing_money_never_becomes_zero(number):
    assert common.amount(number) is None


def test_metadata_transport_rejects_foreign_http_private_host_and_credentials():
    q = query(nsf.NSFQuery)
    for target in ["http://api.nsf.gov/services/v1/awards.json", "https://127.0.0.1/services/v1/awards.json", "https://user:secret@api.nsf.gov/services/v1/awards.json"]:
        with pytest.raises(ValueError):
            common.fetch_bounded(q, target, nsf.parse_response)
    redirects = common.SameHostRedirects("api.nsf.gov")
    with pytest.raises(ValueError):
        redirects.redirect_request(None, None, 302, "Found", {}, "https://private.invalid/token")


@pytest.mark.parametrize("error,status", [(HTTPError("https://api.nsf.gov", 429, "Limited", {}, None), "rate_limited"),
                                         (HTTPError("https://api.nsf.gov", 403, "Forbidden", {}, None), "source_http_error"),
                                         (URLError("sensitive connection error"), "source_unavailable")])
def test_transport_errors_are_unknown_not_empty_and_do_not_leak_error_text(monkeypatch, error, status):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(common, "build_opener", lambda *args: SimpleNamespace(open=fail))
    result = nsf.fetch(query(nsf.NSFQuery))
    assert verify(result) is result
    assert result["status"] == status and result["observations"] is None and result["observed_record_count"] is None
    assert result["missing_is_zero"] is False and "sensitive" not in str(result)


def test_http_200_invalid_body_preserves_checksum_and_unknown_count(monkeypatch):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            assert limit == 2_000_001
            return b'{"serviceNotification":{"type":"ERROR"},"response":{}}'
    monkeypatch.setattr(common, "build_opener", lambda *args: SimpleNamespace(open=lambda *a, **kw: Response()))
    result = nsf.fetch(query(nsf.NSFQuery))
    assert result["status"] == "invalid_source_response" and result["observations"] is None
    assert result["request"]["http_status"] == 200 and len(result["raw_response_bytes_sha256"]) == 64


def test_registry_retains_three_metadata_sources_and_defers_riken():
    registry = load_registry(source_context.REGISTRY_PATH)
    gate = validate_runtime_sources(registry, load_policy())
    assert len(gate["configured_source_ids"]) == len(source_context.SOURCE_INFO)
    assert {"nsf_awards", "datacite", "openaire_projects"} <= set(gate["configured_source_ids"])
    riken = next(s for s in registry["sources"] if s["id"] == "riken_press_rss")
    assert riken["decision"] == "defer" and riken["id"] not in source_context.ADAPTERS


@pytest.mark.parametrize("module,cls,endpoint", [(nsf, nsf.NSFQuery, "/external-evidence/funding/nsf"),
                                                (datacite, datacite.DataCiteQuery, "/external-evidence/artifacts/datacite"),
                                                (openaire, openaire.OpenAIREProjectQuery, "/external-evidence/funding/openaire")])
def test_public_api_passes_bounded_query_and_does_not_save_without_request(monkeypatch, module, cls, endpoint):
    calls = []
    monkeypatch.setattr(module, "fetch", lambda q: calls.append(q) or {"scientific_score_modified": False})
    client = TestClient(app)
    request = {"topic_id": "test", "query": "robot control", "start": "2021-09-24", "end": "2026-09-28", "as_of": "2026-09-28", "max_records": 5}
    assert client.post(endpoint, json=request).json() == {"scientific_score_modified": False}
    assert len(calls) == 1 and isinstance(calls[0], cls) and calls[0].start == date(2021, 9, 24)
    assert client.post(endpoint, json={**request, "max_records": 16}).status_code == 422


def test_new_passports_preserve_date_kind_and_real_amount_semantics():
    for module, cls, row, kind in [(nsf, nsf.NSFQuery, nsf_row(), "grant_award"),
                                   (datacite, datacite.DataCiteQuery, datacite_row(), "resource_publication"),
                                   (openaire, openaire.OpenAIREProjectQuery, openaire_row(), "project_start")]:
        report = parse(module, cls, [row])
        saved = {"payload": report, "observation_id": "synthetic", "created_at": STAMP}
        passport = source_context.passport(report["source"], report["observations"][0], saved)
        assert passport["date_kind"] == kind and passport["expert_validated"] is False
        assert passport["match_status"] == "automatic_search_match"


@pytest.mark.parametrize("source,module,cls,row", [("nsf_awards", nsf, nsf.NSFQuery, nsf_row()),
                                                ("datacite", datacite, datacite.DataCiteQuery, datacite_row()),
                                                ("openaire_projects", openaire, openaire.OpenAIREProjectQuery, openaire_row())])
def test_new_adapters_persist_exact_candidate_binding_with_requested_caps(monkeypatch, source, module, cls, row):
    seen = []
    def fake(q, timeout):
        assert isinstance(q, cls) and q.max_records == 5 and timeout == source_context.PER_REQUEST_TIMEOUT
        report = parse(module, cls, [row], topic_id=q.topic_id)
        return report
    monkeypatch.setattr(module, "fetch", fake)
    monkeypatch.setattr(source_context.external_evidence_store, "record", lambda value: seen.append(verify(value)) or {"observation_id": "synthetic"})
    scope = source_context.binding("m", 12, 7, {"composition_sha256": "a" * 64}, "robot control", date(2026, 9, 28))
    result = source_context._fetch_one(source, scope)
    assert result["payload"]["candidate_context_binding"] == scope
    assert seen[0]["scientific_score_modified"] is False and seen[0]["source"] == source


def test_metadata_exact_dates_do_not_accept_invalid_timestamp_suffixes():
    assert common.iso_day("2026-08-01T12:00:00Z") == "2026-08-01"
    assert common.iso_day("2026-08-01Tevil") is None
    assert common.iso_day("2026-02-31") is None
