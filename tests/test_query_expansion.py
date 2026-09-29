from datetime import date
import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from saia.api import app
from saia.discovery import discover
from saia.query_expansion import compile_plan, literal, suggest
from saia.terminology import PublicationText


def plan():
    return compile_plan('machine learning', ['training data'], ['clinical study'],
                        date(2010, 1, 1), date(2017, 1, 1))


def test_compiler_uses_quoted_or_and_source_specific_exclusions():
    p = plan()
    assert p['logical_expressions']['openalex'] == '("machine learning" OR "training data") NOT ("clinical study")'
    assert p['logical_expressions']['arxiv'] == '(all:"machine learning" OR all:"training data") ANDNOT (all:"clinical study")'
    assert p['policy_hash'] and p['effective_policy']['version'] == p['version']


@pytest.mark.parametrize('value', ['bad" OR all:secret', 'cat:cs.AI', '(wild*)', 'broken\nterm', '123'])
def test_literal_does_not_accept_expression_injection(value):
    with pytest.raises(ValueError):
        literal(value)


def test_inclusion_exclusion_conflict_rejected():
    with pytest.raises(ValueError, match='одновременно'):
        compile_plan('machine learning', [], ['Machine Learning'], date(2010, 1, 1), date(2017, 1, 1))


def test_encoded_url_budget_rejects_oversized_unicode_expression():
    with pytest.raises(ValueError, match='длинное'):
        compile_plan('技'*80, ['术'*80, '学'*80, '习'*80, '网'*80], [], date(2010, 1, 1), date(2017, 1, 1))


def test_terms_are_supported_by_seed_publications_not_future_or_unrelated_texts():
    docs = [PublicationText(1, date(2015, 1, 1), 'Machine learning with training data'),
            PublicationText(2, date(2016, 1, 1), 'Training data improves machine learning'),
            PublicationText(3, date(2018, 1, 1), 'Machine learning future phantom'),
            PublicationText(4, date(2016, 2, 1), 'Unrelated phantom unrelated phantom')]
    r = suggest(docs, 'machine learning', date(2017, 1, 1))
    assert r['seed_works'] == 2
    candidate = next(t for t in r['suggestions'] if t['phrase'] == 'training data')
    assert candidate['document_support'] == 2
    assert candidate['work_ids'] == [1, 2]
    assert not any('phantom' in t['phrase'] for t in r['suggestions'])
    assert candidate['relation'] == 'literal_cooccurrence_not_verified_synonym'
    c = candidate['contexts'][0]
    assert docs[0].title[c['start']:c['end']] == c['matched_text']


def test_historical_bounds_apply_before_seed_and_suggestions():
    docs = [PublicationText(1, date(y, 1, 2), 'Machine learning obsolete term') for y in (2005, 2006)]
    r = suggest(docs, 'machine learning', date(2017, 1, 1), date(2010, 1, 1))
    assert r['seed_works'] == 0 and r['suggestions'] == []
    assert 'выдуманные' in r['warnings'][-1]


def test_repeated_single_work_cannot_supply_minimum_support():
    r = suggest([PublicationText(1, date(2015, 1, 1), 'Machine learning training data',
                                'Training data ' * 20)], 'machine learning', date(2017, 1, 1))
    assert r['suggestions'] == []


def test_quantifier_fragments_are_not_proposed_as_technical_terms():
    r = suggest([PublicationText(i, date(2015, i, 1), 'Many machine learning algorithms')
                 for i in (1, 2)], 'machine learning', date(2017, 1, 1))
    phrases = {t['phrase'] for t in r['suggestions']}
    assert 'learning algorithms' in phrases
    assert not any('many' in p for p in phrases)
    assert '+query-context-clean-1' in r['term_generator_version']


def test_request_params_really_use_approved_plan_before_source_limits():
    seen = {}
    def handler(request):
        seen[request.url.host] = dict(request.url.params)
        return (httpx.Response(200, json={'results': []}) if request.url.host == 'api.openalex.org' else
                httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>'))
    p = plan()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = discover(p['original_query'], date(2010, 1, 1), date(2017, 1, 1), 5, client, p)
    assert seen['api.openalex.org']['search'] == p['logical_expressions']['openalex']
    assert seen['api.openalex.org']['per_page'] == '5'
    assert 'ANDNOT' in seen['export.arxiv.org']['search_query']
    assert 'submittedDate:[201001010000 TO 201612312359]' in seen['export.arxiv.org']['search_query']
    assert r.search_plan == p and r.collection_policy == 'preview-controlled-query-v3'


def test_arbitrary_expression_cannot_override_saved_literals():
    p = copy.deepcopy(plan())
    p['logical_expressions']['arxiv'] = 'all:secret'
    with pytest.raises(ValueError, match='не совпадает'):
        discover(p['original_query'], date(2010, 1, 1), date(2017, 1, 1), search_plan=p)


def test_api_passes_explicit_generation_and_original_phrase(monkeypatch):
    from saia import query_expansion
    seen = []
    monkeypatch.setattr(query_expansion, 'create_proposal', lambda *args: seen.append(args) or {'suggestions': []})
    r = TestClient(app).post('/corpus/demo/query-proposals', json={'query': 'machine learning', 'quality_generation_id': 84})
    assert r.status_code == 200 and seen == [('demo', 'machine learning', 84)]


def test_api_reports_stale_approval_conflict(monkeypatch):
    from saia import query_expansion
    def stale(*_):
        raise query_expansion.QueryConflict('stale fixture')
    monkeypatch.setattr(query_expansion, 'approve', stale)
    r = TestClient(app).post('/query-proposals/fixture/approve', json={
        'selected_ids': ['one'], 'reviewed_by': 'test reviewer'})
    assert r.status_code == 409


def test_api_unknown_persistence_can_be_assessed():
    r = TestClient(app).post('/assess', json={'doc_count': 6, 'primary_sources': 2,
                                           'windows_present': None})
    assert r.status_code == 200
    gate = next(g for g in r.json()['gates'] if g['gate'] == 'G3_persistence')
    assert gate['observed'] is None and gate['passed'] is None


def test_api_can_reopen_approved_packet(monkeypatch):
    from saia import query_expansion
    seen = []
    monkeypatch.setattr(query_expansion, 'read_approved', lambda q: seen.append(q) or {'decision': {'query_version_id': q}})
    r = TestClient(app).get('/queries/approved', params={'query_version_id': 'demo/v2'})
    assert r.status_code == 200 and seen == ['demo/v2']
