from datetime import date

import httpx

from saia.discovery import collect_arxiv, collect_openalex, discover, discover_openalex_only


ARXIV_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/1609.02907</id>
    <published>2016-09-09T12:00:00Z</published>
    <title>Semi-Supervised Classification with Graph Convolutional Networks</title>
    <summary>We introduce a graph convolutional model.</summary>
    <author><name>Thomas Kipf</name></author>
  </entry>
  <entry>
    <id>https://arxiv.org/abs/future</id>
    <published>2017-01-02T12:00:00Z</published>
    <title>Future paper</title>
    <summary>Must be excluded.</summary>
  </entry>
</feed>'''


def transport(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.openalex.org":
        return httpx.Response(200, json={"results": [
            {
                "id": "https://openalex.org/W1",
                "doi": None,
                "display_name": "Semi-Supervised Classification with Graph Convolutional Networks",
                "publication_date": "2016-09-09",
                "type": "article",
                "abstract_inverted_index": {"Graph": [0], "model": [1]},
                "authorships": [{"author": {"display_name": "Thomas Kipf"}}],
                "primary_location": {"landing_page_url": "https://openalex.org/W1"},
            },
            {
                "id": "https://openalex.org/W2",
                "display_name": "Future leakage",
                "publication_date": "2017-01-01",
                "authorships": [],
                "primary_location": {},
            },
        ]})
    if request.url.host == "export.arxiv.org":
        return httpx.Response(200, content=ARXIV_XML)
    return httpx.Response(404)


def test_discovery_deduplicates_sources_and_respects_strict_as_of():
    client = httpx.Client(transport=httpx.MockTransport(transport))
    result = discover(
        "graph convolutional networks", date(2015, 1, 1), date(2017, 1, 1),
        client=client,
    )
    assert len(result.works) == 1
    assert result.works[0].sources == ("arxiv", "openalex")
    assert result.works[0].published_at == "2016-09-09"
    assert result.works[0].openalex_type == "article"
    assert result.errors == {}
    assert result.query_hash


def test_one_failed_source_does_not_destroy_other_source():
    def partial(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openalex.org":
            return httpx.Response(503)
        return httpx.Response(200, content=ARXIV_XML)

    client = httpx.Client(transport=httpx.MockTransport(partial))
    result = discover("gnn", date(2015, 1, 1), date(2017, 1, 1), client=client)
    assert len(result.works) == 1
    assert "openalex" in result.errors
    assert "arxiv" not in result.errors


def test_openalex_only_mode_never_calls_public_arxiv():
    seen = []
    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return transport(request)
    client = httpx.Client(transport=httpx.MockTransport(capture))
    result = discover_openalex_only(
        "graph convolutional networks", date(2015, 1, 1), date(2017, 1, 1),
        client=client,
    )
    assert seen == ["api.openalex.org"]
    assert result.source_counts == {"openalex": 2, "arxiv": 0}
    assert len(result.works) == 1
    assert result.works[0].sources == ("openalex",)
    assert "намеренно не запрашивался" in result.limitations[1]


def test_invalid_date_range_is_rejected_before_network():
    try:
        discover("gnn", date(2017, 1, 1), date(2017, 1, 1))
    except ValueError as exc:
        assert "date_from" in str(exc)
    else:
        raise AssertionError("invalid range accepted")


def test_arxiv_url_uses_lowercase_percent_escapes_required_by_live_endpoint():
    seen = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=ARXIV_XML)

    client = httpx.Client(transport=httpx.MockTransport(capture))
    collect_arxiv(
        client, "artificial intelligence", date(2024, 9, 21),
        date(2026, 9, 21), 1,
    )
    assert "%3a" in seen[0]
    assert "%5b" in seen[0] and "%5d" in seen[0]
    assert "%3A" not in seen[0] and "%5B" not in seen[0]


def test_arxiv_406_falls_back_to_one_visibly_partial_record(monkeypatch):
    monkeypatch.setattr("saia.discovery.time.sleep", lambda seconds: None)

    def partial_volume(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": []})
        if "max_results=1" in str(request.url):
            return httpx.Response(200, content=ARXIV_XML)
        return httpx.Response(406)

    client = httpx.Client(transport=httpx.MockTransport(partial_volume))
    result = discover(
        "artificial intelligence", date(2015, 1, 1), date(2017, 1, 1),
        limit_per_source=5, client=client,
    )
    assert result.source_counts["arxiv"] == 1
    assert len(result.works) == 1
    assert "частичное покрытие" in result.errors["arxiv"]


def test_openalex_429_is_retried_with_bounded_delay(monkeypatch):
    waits = []
    monkeypatch.setattr("saia.discovery.time.sleep", waits.append)
    attempts = []

    def throttled(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"results": [{"id": "W1"}]})

    client = httpx.Client(transport=httpx.MockTransport(throttled))
    rows = collect_openalex(client, "industrial AI", date(2024, 1, 1),
                            date(2025, 1, 1), 5)
    assert len(attempts) == 2
    assert waits == [2.0]
    assert rows == [{"id": "W1"}]
