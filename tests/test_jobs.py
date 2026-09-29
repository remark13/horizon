from saia.discovery import Publication
from saia.jobs import apply_local_arxiv_fallback
from saia.local_arxiv_search import LocalSearchResult
from saia.full_analysis import CONTRACT_VERSION, job_payload


def test_local_arxiv_fallback_replaces_only_failed_channel_and_keeps_provenance():
    result = {
        'query_hash': 'old', 'works': [{
            'canonical_key': 'title:openalex work', 'title': 'OpenAlex work',
            'abstract': None, 'published_at': '2015-01-01', 'sources': ['openalex'],
            'source_ids': ['W1'], 'urls': ['https://openalex.org/W1'],
            'doi': None, 'authors': [],
        }],
        'source_counts': {'openalex': 1, 'arxiv': 0},
        'errors': {'arxiv': 'HTTP 406'}, 'limitations': ['bounded'],
    }
    local = LocalSearchResult((Publication(
        canonical_key='title:arxiv work', title='arXiv work', abstract='abstract',
        published_at='2016-01-01', sources=('arxiv',), source_ids=('A1',),
        urls=('https://arxiv.org/abs/A1',), doi=None, authors=('Author',)),), {
        'revision': 'abc', 'adapter_version': 'fallback-v1',
        'invalid_selected_records': 0, 'limitations': ['current metadata'],
    })
    seen = []
    def searcher(*args):
        seen.append(args)
        return local
    payload = {'search_plan': {'included_terms': ['x']}, 'limit_per_source': 7}
    changed = apply_local_arxiv_fallback(result, payload, '/mirror', searcher)
    assert seen == [('/mirror', payload['search_plan'], 7)]
    assert changed['errors'] == {}
    assert changed['source_counts'] == {'openalex': 1, 'arxiv': 1}
    assert changed['source_modes']['arxiv'] == 'pinned_local_metadata_snapshot'
    assert changed['coverage_comparable'] is None
    assert len(changed['works']) == 2
    assert changed['query_hash'] != 'old'


def test_full_analysis_payload_is_bounded_and_content_addressed():
    profile = {
        'controlled_search_plan': {'included_terms': ['machine learning']},
    }
    payload = job_payload('demo', 'demo/v2', 'demo/v3', profile, 5000, 15)
    assert payload['contract_version'] == CONTRACT_VERSION
    assert payload['source_scope'] == ['arxiv']
    assert payload['max_records'] == 5000
    assert len(payload['request_sha256']) == 64
    assert payload['openalex_role'].endswith('not_complete_corpus')


def test_full_analysis_payload_refuses_unbounded_input():
    import pytest
    profile = {'controlled_search_plan': {'included_terms': ['x']}}
    with pytest.raises(ValueError, match='от 100'):
        job_payload('demo', 'demo/v2', 'demo/v3', profile, 50, 15)
    with pytest.raises(ValueError, match='от 100'):
        job_payload('demo', 'demo/v2', 'demo/v3', profile, 20_001, 15)
