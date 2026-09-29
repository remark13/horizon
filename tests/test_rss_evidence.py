from datetime import date

import pytest

from saia.external_evidence_store import verify
from saia.rss_evidence import RSSQuery, _OfficialRedirects, parse_response, selected_feed


def query(**kwargs):
    return RSSQuery("topic", "robot control", "2026-01-01", "2026-09-28", "2026-09-28", **kwargs)


def feed(items):
    return ('<rss><channel><language>en</language>' + items + '</channel></rss>').encode()


def item(title="Robot control improved", link="https://news.mit.edu/2026/robot-control", published="Mon, 28 Sep 2026 09:00:00 +0000", extra=""):
    return f'<item><title>{title}</title><link>{link}</link><pubDate>{published}</pubDate>{extra}</item>'


def test_official_rss_is_dated_attributed_bounded_metadata_not_story_body():
    value = parse_response(query(), feed(item(extra='<description>Private full story</description><author>Research Office</author>') + item()), "2026-09-28T12:00:00Z")
    assert verify(value) is value
    assert len(value["observations"]) == 1
    record = value["observations"][0]
    assert record["published_at"] == "2026-09-28"
    assert record["author"] == "Research Office"
    assert record["matched_terms"] == ["robot", "control"]
    assert "description" not in record
    assert "Private full story" not in str(value)
    assert value["source_result_is_exhaustive"] is False
    assert value["scientific_score_modified"] is False


def test_unrelated_unknown_date_future_and_foreign_links_are_excluded():
    payload = feed(item(title="Robot graduates") + item(published="") +
                   item(published="Tue, 29 Sep 2026 09:00:00 +0000") +
                   item(link="https://evil.example/robot-control"))
    value = parse_response(query(), payload, "2026-09-28T12:00:00Z")
    assert value["status"] == "empty_observed_response"
    assert value["observations"] == []
    assert value["records_without_publication_date"] == 1
    assert value["missing_is_zero"] is False


def test_atom_uses_publication_date_not_updated_date():
    payload = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Robot control</title><link href="https://news.mit.edu/2026/robot-control"/><published>2026-09-10T00:00:00Z</published><updated>2026-09-28T00:00:00Z</updated></entry></feed>'
    result = parse_response(query(), payload, "2026-09-28T12:00:00Z")
    assert result["observations"][0]["published_at"] == "2026-09-10"


@pytest.mark.parametrize("payload", [b'<!DOCTYPE rss [<!ENTITY x "a">]><rss/>', b'<html/>', b'x' * 2_000_001])
def test_rss_rejects_unsafe_or_unbounded_xml(payload):
    with pytest.raises(ValueError):
        parse_response(query(), payload, "2026-09-28T12:00:00Z")


def test_rss_source_is_allowlisted_and_redirect_does_not_leak_to_other_hosts():
    with pytest.raises(ValueError):
        query(source="https://127.0.0.1").validate()
    handler = _OfficialRedirects("news.mit.edu")
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://127.0.0.1/private")


def test_nasa_jpl_do_not_count_as_independent_publishers():
    nasa = parse_response(query(source="nasa_news_rss"), feed(item(link="https://www.nasa.gov/robot-control")), "2026-09-28T12:00:00Z")
    jpl = parse_response(query(source="jpl_news_rss"), feed(item(link="https://www.jpl.nasa.gov/robot-control")), "2026-09-28T12:00:00Z")
    assert nasa["publisher_organisation"] == jpl["publisher_organisation"] == "NASA"


def test_query_limits_and_date_order_are_checked():
    with pytest.raises(ValueError):
        query(max_records=16).validate()
    with pytest.raises(ValueError):
        RSSQuery("t", "robot", date(2026, 10, 1), date(2026, 9, 28), date(2026, 9, 28)).validate()


def test_feed_selection_is_allowlisted_and_unknown_topics_keep_general_feed():
    assert selected_feed(query())[0] == 'https://news.mit.edu/topic/mitrobotics-rss.xml'
    assert selected_feed(RSSQuery('t', 'new unknown topic', '2026-01-01', '2026-09-28', '2026-09-28'))[0] == 'https://news.mit.edu/rss/feed'
    assert selected_feed(RSSQuery('t', 'autonomous flight', '2026-01-01', '2026-09-28', '2026-09-28', 'nasa_news_rss'))[0] == 'https://www.nasa.gov/aeronautics/feed/'
