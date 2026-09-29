"""Регрессии P0: исторический поиск и запрет спорной склейки DOI."""

from datetime import date

import httpx

from saia.discovery import collect_arxiv, discover
from saia.normalize import find_existing


EMPTY_FEED = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'


def test_arxiv_date_is_applied_before_limit():
    seen = []
    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, content=EMPTY_FEED)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        collect_arxiv(client, 'machine learning', date(2015, 1, 1), date(2017, 1, 1), 50)
    assert 'submittedDate:[201501010000 TO 201612312359]' in seen[0]['search_query']
    assert seen[0]['max_results'] == '50'


def test_later_arxiv_abstract_is_not_historical_evidence():
    feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>https://arxiv.org/abs/1601.00001v3</id>
      <published>2016-01-01T00:00:00Z</published>
      <updated>2020-02-01T00:00:00Z</updated><title>Changed title</title>
      <summary>Later knowledge</summary></entry></feed>'''
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=feed))) as client:
        assert collect_arxiv(client, 'learning', date(2015, 1, 1), date(2017, 1, 1), 50) == []


def test_openalex_key_is_used_but_not_exposed(monkeypatch):
    monkeypatch.setenv('OPENALEX_API_KEY', 'secret-test-key')
    def handler(request):
        if request.url.host == 'api.openalex.org':
            assert 'api_key' not in request.url.params
            assert request.headers['authorization'] == 'Bearer secret-test-key'
            assert request.url.params['per_page'] == '50'
            return httpx.Response(200, json={'results': []})
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=EMPTY_FEED)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = discover('learning', date(2015, 1, 1), date(2017, 1, 1), client=client)
    assert 'secret-test-key' not in str(result.to_dict())
    assert result.collection_policy == 'preview-date-and-version-v2'
    assert result.limitations


class DedupCursor:
    def __init__(self, existing_doi):
        self.results = iter([None, [(42,)], [('ivanov i',)], [(existing_doi,)]])
    def execute(self, sql, params):
        pass
    def fetchone(self):
        return next(self.results)
    def fetchall(self):
        return next(self.results)


def test_identical_title_authors_and_different_registrants_are_not_merged():
    parsed = {'title': 'Same title', 'authors': [{'display_name': 'Ivan Ivanov'}],
              'identifiers': [('doi', '10.1007/other-work')]}
    result = find_existing(DedupCursor('10.1109/first-work'), 1, parsed)
    assert result == (None, 'blocked_different_doi', '10.1007/other-work')


def test_same_registrant_different_dois_are_not_merged_either():
    parsed = {'title': 'Same title', 'authors': [{'display_name': 'Ivan Ivanov'}],
              'identifiers': [('doi', '10.1109/other-work')]}
    assert find_existing(DedupCursor('10.1109/first-work'), 1, parsed)[0] is None


class ArxivDedupCursor(DedupCursor):
    def __init__(self, existing_arxiv):
        self.results = iter([None, [(42,)], [('ivanov i',)], [],
                             [(existing_arxiv,)] if existing_arxiv else []])


def test_different_arxiv_deposits_are_not_merged_by_title_and_name():
    parsed = {'title': 'Same title', 'authors': [{'display_name': 'Ivan Ivanov'}],
              'identifiers': [('arxiv', '1408.2044')]}
    assert find_existing(ArxivDedupCursor('1004.2008'), 1, parsed) == (
        None, 'blocked_conflicting_arxiv_ids', '1408.2044')


def test_cross_source_title_match_without_conflicting_arxiv_id_is_preserved():
    parsed = {'title': 'Same title', 'authors': [{'display_name': 'Ivan Ivanov'}],
              'identifiers': [('arxiv', '1408.2044')]}
    assert find_existing(ArxivDedupCursor(None), 1, parsed)[:2] == (42, 'title_author_match')
