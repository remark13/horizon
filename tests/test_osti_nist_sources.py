from datetime import date
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from saia import osti_evidence as osti, public_metadata as common, source_context
from saia.api import app
from saia.external_evidence_store import verify
from saia.external_sources import load_policy
from saia.hybrid import digest
from saia.rss_evidence import RSSQuery, parse_response as parse_rss, selected_feed
from saia.source_registry import load_registry, validate_runtime_sources

STAMP = "2026-09-28T12:00:00Z"


def query(**changes):
    return osti.OSTIQuery(**{"topic_id": "synthetic-topic", "query": "quantum sensor",
                            "start": "2021-09-28", "end": "2026-09-28", "as_of": "2026-09-28",
                            "max_records": 5, **changes})


def row(**changes):
    return {"osti_id": "3399445", "title": "Quantum sensor research",
            "product_type": "Technical Report", "publication_date": "2026-09-10T00:00:00Z",
            "entry_date": "2026-09-27T10:00:00Z", "doi": "10.2172/3399445",
            "authors": ["Synthetic Author"], "country_publication": "United States",
            "language": "English", "publisher": "Synthetic Lab", **changes}


def parse(rows, q=None):
    q = q or query()
    return osti.parse_response(q, json.dumps(rows).encode(), STAMP, osti.request_url(q))


def test_osti_keeps_dated_attributed_bibliography_not_abstracts_files_or_model_input():
    result = parse([row(description="Do not retain private abstract", links=[{"rel": "fulltext", "href": "https://evil.example/body"}], contacts=["private@example.org"])])
    assert verify(result) is result
    record = result["observations"][0]
    assert record["publication_date"] == "2026-09-10" and record["entry_date"] == "2026-09-27"
    assert record["url"] == "https://www.osti.gov/biblio/3399445"
    assert record["country_scope"] == "country_of_publication_not_authors_or_adoption"
    assert record["record_type"] == "research_report" and record["peer_review_verified"] is False
    assert result["reported_total_results"] is None and result["source_result_is_exhaustive"] is False
    assert result["observations"][0]["model_input_allowed"] is False
    assert result["model_training_allowed"] is False and result["bulk_reuse_approved"] is False
    assert result["scientific_score_modified"] is False
    assert not any(v in str(result) for v in ("private abstract", "evil.example", "private@example.org"))


def test_osti_request_has_official_title_date_and_page_bounds():
    target = urlsplit(osti.request_url(query()))
    args = parse_qs(target.query)
    assert target.scheme == "https" and target.netloc == "www.osti.gov" and target.path == "/api/v1/records"
    assert args["title"] == ['"quantum" AND "sensor"']
    assert args["publication_date_start"] == ["09/28/2021"]
    assert args["publication_date_end"] == ["09/28/2026"]
    assert args["rows"] == ["25"] and args["page"] == ["1"]


@pytest.mark.parametrize("product,expected", list(osti.PRODUCT_TYPES.items()))
def test_osti_publication_types_are_explicit_not_inferred_peer_review(product, expected):
    record = parse([row(product_type=product)])["observations"][0]
    assert record["record_type"] == expected and record["peer_review_verified"] is False


@pytest.mark.parametrize("product", ["Patent", "Patent Application", "Dataset", "Software", None, {"type": "Article"}])
def test_osti_does_not_mislabel_patents_datasets_or_software_as_scientific_papers(product):
    result = parse([row(product_type=product)])
    assert result["status"] == "empty_observed_response"
    assert result["rejected_records"] == {"not_supported_publication_type": 1}


def test_osti_never_backdates_future_publication_using_entry_or_doi_dates():
    result = parse([row(publication_date="2026-12-01T00:00:00Z", entry_date="2026-09-16T10:00:00Z"),
                    row(osti_id="3399446", publication_date=None, entry_date="2026-09-27T10:00:00Z"),
                    row(osti_id="3399447", publication_date="2026-09-10Tevil"),
                    row(osti_id="3399448", publication_date="2020-09-10")])
    assert result["observations"] == []
    assert result["rejected_records"] == {"outside_publication_interval": 2, "unknown_or_invalid_publication_date": 2}


def test_osti_literal_title_match_not_an_abstract_only_mention():
    result = parse([row(title="Quantum sensor research"), row(osti_id="3399446", title="Quantum device research", description="quantum sensor")])
    assert len(result["observations"]) == 1
    assert result["rejected_records"] == {"no_visible_title_match": 1}


def test_osti_limits_orders_and_deduplicates_only_exact_osti_ids():
    result = parse([row(osti_id="3399447", publication_date="2026-09-20"), row(), row(),
                    row(osti_id="3399448", publication_date="2026-09-25")], query(max_records=2))
    assert [r["osti_id"] for r in result["observations"]] == ["3399448", "3399447"]
    assert result["rejected_records"] == {"duplicate_osti_id": 1}
    assert result["result_cap_reached"] is True
    assert result["independent_publication_count_established"] is False


@pytest.mark.parametrize("payload", [b'{}', b'{"statusCode":500}', b'[null]', b'NaN', b'x'*2_000_001])
def test_osti_invalid_or_unbounded_response_is_not_empty_success(payload):
    with pytest.raises(ValueError):
        osti.parse_response(query(), payload, STAMP, osti.request_url(query()))


def test_osti_rejects_wrong_sized_page_and_all_broken_identities():
    with pytest.raises(ValueError):
        parse([row(osti_id=str(1000000+i)) for i in range(26)])
    with pytest.raises(ValueError):
        parse([row(osti_id="../../../private")])


@pytest.mark.parametrize("error,expected", [(HTTPError("https://www.osti.gov",429,"limited",{},None), "rate_limited"),
                                           (HTTPError("https://www.osti.gov",503,"down",{},None), "source_http_error"),
                                           (URLError("unavailable"), "source_unavailable"),
                                           (TimeoutError(), "source_unavailable")])
def test_osti_unavailable_is_unknown_and_retains_no_model_permission(monkeypatch, error, expected):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(common, "build_opener", lambda *a: SimpleNamespace(open=fail))
    result = osti.fetch(query())
    assert result["status"] == expected and result["observations"] is None
    assert result["observed_record_count"] is None and result["missing_is_zero"] is False
    assert verify(result) is result and result["model_input_allowed"] is False


def test_osti_http200_wrong_schema_is_a_failure_with_checksum(monkeypatch):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            assert limit == 2_000_001
            return b'{"statusCode":500,"errorDescription":"unavailable"}'
    monkeypatch.setattr(common, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **kw: Response()))
    result = osti.fetch(query())
    assert result["status"] == "invalid_source_response" and len(result["raw_response_bytes_sha256"]) == 64
    assert result["observations"] is None and result["request"]["http_status"] == 200


@pytest.mark.parametrize("flag", ["model_input_allowed", "model_training_allowed", "bulk_reuse_approved"])
def test_osti_store_rejects_implicit_permission_expansion(flag):
    value = parse([row()])
    value.pop("report_payload_sha256")
    value[flag] = True
    value["report_payload_sha256"] = digest(value)
    with pytest.raises(ValueError, match="OSTI"):
        verify(value)


def test_osti_adapter_failure_preserves_restrictions_and_exact_card_binding(monkeypatch):
    monkeypatch.setattr(osti, "fetch", lambda *a, **kw: (_ for _ in ()).throw(ValueError("unavailable")))
    stored = []
    monkeypatch.setattr(source_context.external_evidence_store, "record", lambda v: stored.append(verify(v)) or {"observation_id":"synthetic"})
    scope = source_context.binding("m",12,7,{"composition_sha256":"a"*64},"quantum sensor",date(2026,9,28))
    result = source_context._fetch_one("osti_gov",scope)
    assert result["payload"]["candidate_context_binding"] == scope
    assert stored[0]["observations"] is None and stored[0]["model_input_allowed"] is False


def test_two_sources_registered_and_other_restricted_feeds_not_executable():
    registry = load_registry(source_context.REGISTRY_PATH)
    gate = validate_runtime_sources(registry, load_policy())
    assert len(gate["configured_source_ids"]) == len(source_context.SOURCE_INFO)
    assert {"osti_gov","nist_news_rss"} <= set(gate["configured_source_ids"])
    assert "aist_press_rss" in gate["configured_source_ids"]
    for identifier in ["science_japan_rss", "riken_press_rss"]:
        item = next(s for s in registry["sources"] if s["id"] == identifier)
        assert item["decision"] == "defer" and identifier not in source_context.ADAPTERS
        assert identifier not in gate["configured_source_ids"]
    assert source_context.DEFAULT_SOURCES == ("google_news_rss","gdelt_doc_2_0","dealroom_marketmaps","dealroom_public_rounds","epo_ops","ukri_gtr")


def nist_query(text="quantum sensor"):
    return RSSQuery("synthetic-topic", text, "2021-09-28", "2026-09-28", "2026-09-28", "nist_news_rss", 5)


def test_nist_is_attributed_announcement_not_independent_market_evidence():
    payload = b'<rss><channel><language>en</language><item><title>Quantum sensor improved</title><link>https://www.nist.gov/news/quantum</link><pubDate>Thu, 10 Sep 2026 12:00:00 +0000</pubDate><description>Do not store body</description></item></channel></rss>'
    result = parse_rss(nist_query(), payload, STAMP)
    assert verify(result) is result
    record = result["observations"][0]
    assert record["publisher_organisation"] == "NIST" and record["independent_confirmation"] is False
    assert record["record_type"] == "institutional_announcement"
    assert "Do not store body" not in str(result)
    assert result["source_result_is_exhaustive"] is False


@pytest.mark.parametrize("text,path", [("battery materials", "/news-events/materials/rss.xml"),
                                       ("nuclear energy", "/news-events/energy/rss.xml"),
                                       ("industrial robotics", "/news-events/manufacturing/rss.xml"),
                                       ("machine learning", "/news-events/information%20technology/rss.xml"),
                                       ("quantum sensor", "/news-events/news/rss.xml")])
def test_nist_selects_only_official_topic_feed_or_general(text, path):
    selected, _ = selected_feed(nist_query(text))
    assert urlsplit(selected).netloc == "www.nist.gov" and urlsplit(selected).path == path


def test_new_api_validates_and_does_not_save_without_explicit_request(monkeypatch):
    calls = []
    monkeypatch.setattr(osti,"fetch",lambda q:calls.append(q) or {"scientific_score_modified":False})
    client = TestClient(app)
    request = {"topic_id":"synthetic","query":"quantum sensor","start":"2021-09-28","end":"2026-09-28","as_of":"2026-09-28","max_records":5}
    assert client.post("/external-evidence/papers/osti",json=request).json() == {"scientific_score_modified":False}
    assert len(calls)==1 and isinstance(calls[0],osti.OSTIQuery)
    assert client.post("/external-evidence/papers/osti",json={**request,"max_records":16}).status_code==422


def test_osti_passport_retains_human_only_boundary_and_report_type():
    report = parse([row()])
    saved={"payload":report,"observation_id":"synthetic","created_at":STAMP}
    passport=source_context.passport("osti_gov",report["observations"][0],saved)
    assert passport["record_type"]=="research_report" and passport["model_input_allowed"] is False
    assert passport["date_kind"]=="publication" and passport["expert_validated"] is False
