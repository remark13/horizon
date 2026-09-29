from datetime import date
import json

import httpx

from scripts.probe_openalex_russian_search_modes import _one, run


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params, headers):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


def response(status, publication_date="2024-01-01"):
    return httpx.Response(status, json={"results": [{
        "id": "https://openalex.org/W1", "doi": None,
        "display_name": "A relevant research paper",
        "publication_date": publication_date,
        "abstract_inverted_index": {"Research": [0], "paper": [1]},
        "relevance_score": 1.2, "type": "article"}],
        "meta": {"count": 500, "cost_usd": 0.001}},
        request=httpx.Request("GET", "https://api.openalex.org/works"))


def test_modes_are_separate_and_exact_date_postfilter_is_not_denominator(tmp_path):
    config = tmp_path / "probe.json"
    config.write_text(json.dumps({
        "version": "openalex-russian-search-modes-probe-v1",
        "per_page": 25, "date_from": "2021-09-01",
        "as_of_date": "2026-09-01",
        "cases": [{"case_id": "boundary", "role": "boundary", "query_ru": "ферменты"},
                  {"case_id": "outside", "role": "outside", "query_ru": "сенсоры"}],
    }), encoding="utf-8")
    client = FakeClient([response(200), response(200, "2021-01-01"),
                         response(200), response(200)])
    report = run(config, client=client, sleep=lambda _: None)
    assert len(report["rows"]) == 4
    assert [row["mode"] for row in report["rows"]] == [
        "lexical", "semantic", "lexical", "semantic"]
    assert report["rows"][1]["within_exact_period_first_page"] == 0
    assert report["rows"][0]["results"][0]["abstract"] == "Research paper"
    assert all(call[1]["filter"] == "publication_year:>2020"
               for call in client.calls)
    assert "search.semantic" in client.calls[1][1]
    assert "search.semantic" not in client.calls[0][1]
    assert report["limits"]["not_full_temporal_coverage_or_growth"]


def test_semantic_query_retries_a_transient_rate_limit_once():
    client = FakeClient([response(429), response(200)])
    result = _one(client, query="сенсоры", mode="semantic",
                  start=date(2021, 9, 1), cutoff=date(2026, 9, 1),
                  per_page=25, sleep=lambda _: None)
    assert result["status"] == "succeeded"
    assert len(client.calls) == 2


def test_exact_date_filter_uses_exclusive_cutoff():
    client = FakeClient([response(200)])
    result = _one(client, query="сенсоры", mode="lexical",
                  start=date(2021, 9, 1), cutoff=date(2026, 9, 1),
                  per_page=25, sleep=lambda _: None, exact_date_filter=True)
    assert result["status"] == "succeeded"
    assert client.calls[0][1]["filter"] == (
        "from_publication_date:2021-09-01,to_publication_date:2026-08-31")


def test_single_case_v2_keeps_lexical_and_semantic_separate(tmp_path):
    config = tmp_path / "one.json"
    config.write_text(json.dumps({
        "version": "openalex-russian-search-modes-probe-v2-single-case",
        "per_page": 25, "date_from": "2021-09-01",
        "as_of_date": "2026-09-01",
        "cases": [{"case_id": "outside", "role": "outside_priority_map",
                   "query_ru": "водородные оптические волокна"}],
    }), encoding="utf-8")
    client = FakeClient([response(200), response(200)])
    report = run(config, client=client, sleep=lambda _: None)
    assert report["version"].endswith("v2-single-case")
    assert [row["mode"] for row in report["rows"]] == ["lexical", "semantic"]
    assert len(client.calls) == 2
