import importlib.util
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import pyarrow as pa
import pyarrow.parquet as pq


@pytest.fixture
def fetch(monkeypatch):
    spec = importlib.util.spec_from_file_location('openalex_collection_fixture', Path(__file__).parents[1] / 'scripts' / 'fetch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    return module


def page(ids, count, cursor):
    return json.dumps({'meta': {'count': count, 'next_cursor': cursor},
                       'results': [{'id': f'https://openalex.org/W{i}'} for i in ids]}).encode()


def test_full_cursor_count_audit_and_byte_exact_cache_resume(fetch, tmp_path, monkeypatch):
    bodies = [page([1, 2], 3, 'second'), page([3], 3, 'third'), page([], 3, None)]
    requests = []
    def get(url, **kwargs):
        requests.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
        return bodies[len(requests) - 1], 200
    monkeypatch.setattr(fetch, 'http_get', get)
    result = fetch.openalex_cursor_pages({'filter': 'publication_year:2016'}, tmp_path, 'page_', 5, False)
    assert len(result) == 3 and result[-1]['cursor_audit_complete'] is True
    assert result[-1]['unique_records_in_query'] == 3
    assert [p['cursor'][0] for p in requests] == ['*', 'second', 'third']
    assert all(p['per_page'] == ['100'] and 'api_key' not in p for p in requests)
    assert [(tmp_path / r['file']).read_bytes() for r in result] == bodies
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: pytest.fail('cache must not refetch'))
    repeated = fetch.openalex_cursor_pages({'filter': 'publication_year:2016'}, tmp_path, 'page_', 5, False)
    assert all(p['reused'] for p in repeated) and repeated[-1]['cursor_audit_complete']
    assert [p['sha256'] for p in repeated] == [p['sha256'] for p in result]


@pytest.mark.parametrize('bodies,reason', [
    ([page([1], 2, None)], 'не совпало'),
    ([page([1], 3, 'second'), page([2], 4, 'third')], 'изменил'),
    ([page([1], 2, 'second'), page([1], 2, None)], 'повторил'),
    ([page([1, 1], 2, None)], 'повторил'),
    ([page([1], 3, 'second'), page([2], 3, 'second')], 'повторил cursor'),
    ([page([], 0, 'second')], 'Пустая'),
    ([page([1], 0, None)], 'превысило'),
])
def test_incomplete_cursor_or_count_is_never_complete(fetch, tmp_path, monkeypatch, bodies, reason):
    iterator = iter(bodies)
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (next(iterator), 200))
    result = fetch.openalex_cursor_pages({}, tmp_path, 'page_', 5, False)
    assert any(r.get('incomplete') for r in result)
    assert reason in result[-1]['reason']
    assert not any(r.get('cursor_audit_complete') for r in result)


def test_page_limit_preserves_partial_files(fetch, tmp_path, monkeypatch):
    body = page([1], 2, 'second')
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (body, 200))
    result = fetch.openalex_cursor_pages({}, tmp_path, 'page_', 1, False)
    assert result[-1]['incomplete'] and 'лимит' in result[-1]['reason']
    assert (tmp_path / result[0]['file']).read_bytes() == body


def test_disk_reserve_stops_without_writing_partial_openalex_page(fetch, tmp_path, monkeypatch):
    body = page([1], 1, None)
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (body, 200))
    monkeypatch.setattr(fetch.shutil, 'disk_usage', lambda path: type('Disk', (), {'free': 100})())
    result = fetch.openalex_cursor_pages({}, tmp_path, 'page_', 2, False,
                                         min_free_bytes=100, max_new_bytes=1000)
    assert result[-1]['incomplete'] is True
    assert result[-1]['unique_records'] == 0
    assert not (tmp_path / 'page_0001.json').exists()


@pytest.mark.parametrize('body', [b'not-json', b'{}', b'{"meta":{"count":true,"next_cursor":null},"results":[]}',
                                 b'{"meta":{"count":1,"next_cursor":null},"results":[{}]}'])
def test_invalid_source_shape_cannot_pass_cursor_audit(fetch, tmp_path, monkeypatch, body):
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (body, 200))
    with pytest.raises(fetch.FetchError): fetch.openalex_cursor_pages({}, tmp_path, 'page_', 5, False)


def test_empty_success_and_key_never_allowed_in_public_url(fetch, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (page([], 0, None), 200))
    assert fetch.openalex_cursor_pages({}, tmp_path, 'page_', 1, False)[0]['cursor_audit_complete']
    with pytest.raises(fetch.FetchError): fetch.openalex_cursor_pages({'api_key': 'secret'}, tmp_path, 'other_', 1, False)


@pytest.mark.parametrize('url,authorized', [('https://api.openalex.org/works', True),
    ('https://huggingface.co/data', False), ('https://export.arxiv.org/api/query', False),
    ('http://api.openalex.org/works', False), ('https://api.openalex.org.other/works', False)])
def test_bearer_key_only_sent_to_exact_https_openalex_host(fetch, monkeypatch, url, authorized):
    monkeypatch.setenv('OPENALEX_API_KEY', 'test-secret')
    requests = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{}'
    def opened(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(fetch.HTTP_OPENER, 'open', opened)
    assert fetch.http_get(url, accept='application/json') == (b'{}', 200)
    assert requests[0].get_header('Authorization') == ('Bearer test-secret' if authorized else None)
    assert 'test-secret' not in requests[0].full_url


@pytest.mark.parametrize('target,retained', [('https://api.openalex.org/next', True),
    ('https://other.example/next', False), ('http://api.openalex.org/next', False)])
def test_redirect_does_not_leak_bearer_key(fetch, target, retained):
    request = urllib.request.Request('https://api.openalex.org/works', headers={'Authorization': 'Bearer test-secret'})
    redirected = fetch.CredentialSafeRedirect().redirect_request(request, None, 302, 'Found', {}, target)
    assert redirected.get_header('Authorization') == ('Bearer test-secret' if retained else None)


def test_errors_do_not_echo_env_key(fetch, monkeypatch):
    monkeypatch.setenv('OPENALEX_API_KEY', 'test-secret')
    def failed(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'echo test-secret'))
    monkeypatch.setattr(fetch.HTTP_OPENER, 'open', failed)
    with pytest.raises(fetch.FetchError) as error: fetch.http_get('https://api.openalex.org/works', accept='application/json')
    assert 'test-secret' not in str(error.value) and '[REDACTED]' in str(error.value)


def enriched_page(ids, next_cursor=None, count=None):
    return json.dumps({'meta': {'count': len(ids) if count is None else count, 'next_cursor': next_cursor},
                       'results': [{'id': f'https://openalex.org/W{i}', 'doi': f'https://doi.org/10.48550/arxiv.1001.{i:05d}',
                                    'authorships': [{'author': {'id': 'https://openalex.org/A1'}, 'institutions': []}]} for i in ids]}).encode()


def enrichment_mission(**limits):
    return {'period': {}, 'query': {'openalex_enrich_arxiv_ids': {'batch_size': 2, **limits}}}


def test_enrichment_follows_all_pages_and_counts_requested_ids_once(fetch, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'arxiv_ids_from_parquet', lambda *args: ['1001.00001', '1001.00002'])
    responses = iter([enriched_page([1], 'second', 2), enriched_page([2], None, 2)])
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (next(responses), 200))
    result = fetch.fetch_openalex_arxiv_enrichment(enrichment_mission(), tmp_path, tmp_path / 'openalex', None, False)
    assert len(result) == 2 and result[-1]['cursor_audit_complete']
    assert sum(p['requested_ids'] for p in result) == 2
    assert sum(p['records'] for p in result) == 2
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: pytest.fail('cache must not refetch'))
    repeated = fetch.fetch_openalex_arxiv_enrichment(enrichment_mission(), tmp_path, tmp_path / 'openalex', None, False)
    assert all(p['reused'] for p in repeated)


@pytest.mark.parametrize('limit', [{'batch_size': 0}, {'batch_size': 101}, {'max_batches': 0},
                                   {'max_batches': True}, {'max_pages_per_batch': 0}])
def test_invalid_enrichment_limits_do_not_make_requests(fetch, tmp_path, monkeypatch, limit):
    monkeypatch.setattr(fetch, 'arxiv_ids_from_parquet', lambda *args: ['1001.00001'])
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: pytest.fail('invalid limits must not request'))
    with pytest.raises(fetch.FetchError):
        fetch.fetch_openalex_arxiv_enrichment(enrichment_mission(**limit), tmp_path, tmp_path / 'openalex', None, False)


def test_enrichment_rejects_returned_doi_outside_requested_batch(fetch, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'arxiv_ids_from_parquet', lambda *args: ['1001.00001'])
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (enriched_page([99]), 200))
    result = fetch.fetch_openalex_arxiv_enrichment(enrichment_mission(), tmp_path, tmp_path / 'openalex', None, False)
    assert result[-1]['incomplete'] and 'DOI вне' in result[-1]['reason']


def base_package(tmp_path):
    import hashlib
    base = tmp_path / 'base'
    raw = base / 'arxiv' / 'part.parquet'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b'synthetic fixture bytes')
    mission = {'mission_id': 'fixture', 'query_version': 'fixture/v1', 'sources': ['arxiv'],
               'query': {'terms': ['machine learning']}, 'period': {}}
    text = json.dumps(mission)
    (base / 'mission.json').write_text(text)
    manifest = {'mission_id': 'fixture', 'query_version': 'fixture/v1', 'mission_snapshot_file': 'mission.json',
                'mission_file_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'sources': {'arxiv': {'files': [{'file': raw.name, 'records': 1,
                                               'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}]}}}
    (base / 'manifest.json').write_text(json.dumps(manifest))
    return base


def test_budget_pilot_is_explicitly_partial_and_preserves_base(fetch, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from scripts import openalex_enrichment_pilot as pilot
    monkeypatch.setattr(pilot, 'fetch', fetch)
    base = base_package(tmp_path)
    before = (base / 'manifest.json').read_bytes()
    monkeypatch.setattr(fetch, 'arxiv_ids_from_parquet', lambda *args: [f'1001.{i:05d}' for i in range(1, 6)])
    responses = iter([enriched_page([1, 2]), enriched_page([3])])
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (next(responses), 200))
    output = tmp_path / 'pilot'
    result = pilot.collect(base, output, max_batches=2, batch_size=2)
    assert result['partial'] and result['requested_ids'] == 4
    assert result['returned_records'] == 3 and result['works_with_author_ids'] == 3
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['incomplete'] and manifest['sources']['openalex']['independent_discovery'] is False
    assert (base / 'manifest.json').read_bytes() == before
    with pytest.raises(ValueError, match='не перезаписываем'): pilot.collect(base, output, 2, 2)


def test_pilot_rejects_old_package_directory_and_unverified_files(fetch, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from scripts import openalex_enrichment_pilot as pilot
    monkeypatch.setattr(pilot, 'fetch', fetch)
    base = base_package(tmp_path)
    with pytest.raises(ValueError): pilot.collect(base, base)
    with pytest.raises(ValueError): pilot.collect(base, base / 'nested')
    with pytest.raises(ValueError): pilot.collect(base, tmp_path)
    (base / 'arxiv' / 'part.parquet').write_bytes(b'changed')
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: pytest.fail('invalid base must not request'))
    with pytest.raises(ValueError): pilot.collect(base, tmp_path / 'pilot')


def test_refetch_cannot_overwrite_a_package_with_manifest(fetch, tmp_path, monkeypatch):
    base = base_package(tmp_path)
    before = {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}
    monkeypatch.setattr('sys.argv', ['fetch', str(base / 'mission.json'), '--out', str(base), '--refetch'])
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: pytest.fail('old package must not refetch'))
    assert fetch.main() == 3
    assert {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()} == before


def test_bulk_query_manifest_marks_independent_discovery_and_full_record_shape(fetch, tmp_path, monkeypatch):
    mission = {'mission_id': 'fixture', 'query_version': 'fixture/v1', 'sources': ['openalex'],
               'query': {'terms': ['machine learning']}, 'period': {}}
    path = tmp_path / 'mission-input.json'
    path.write_text(json.dumps(mission))
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (page([1], 1, None), 200))
    monkeypatch.setattr('sys.argv', ['fetch', str(path), '--out', str(tmp_path / 'raw')])
    assert fetch.main() == 0
    manifest = json.loads((tmp_path / 'raw' / 'manifest.json').read_text())
    source = manifest['sources']['openalex']
    assert source['independent_discovery'] is True and source['record_shape'] == 'full'
    assert source['access_mode'] == 'cursor-paged-query'
    assert source['files'][-1]['cursor_audit_complete']


def test_independent_openalex_collection_passes_explicit_stable_sort(fetch, tmp_path, monkeypatch):
    mission = {
        'period': {},
        'query': {
            'terms': ['active learning'],
            'openalex_sort': 'publication_date:desc',
            'openalex_max_pages': 2,
        },
    }
    captured = {}
    monkeypatch.setattr(fetch, 'guard_query_hash', lambda *args: None)
    def pages(params, *args):
        captured.update(params)
        return []
    monkeypatch.setattr(fetch, 'openalex_cursor_pages', pages)
    assert fetch.fetch_openalex(mission, tmp_path, None, False) == []
    assert captured['sort'] == 'publication_date:desc'


def test_independent_openalex_collection_rejects_arbitrary_sort(fetch, tmp_path, monkeypatch):
    mission = {
        'period': {},
        'query': {
            'terms': ['active learning'],
            'openalex_sort': 'relevance_score:desc',
        },
    }
    monkeypatch.setattr(fetch, 'guard_query_hash', lambda *args: None)
    monkeypatch.setattr(fetch, 'openalex_cursor_pages', lambda *args: pytest.fail('invalid sort'))
    with pytest.raises(fetch.FetchError, match='openalex_sort'):
        fetch.fetch_openalex(mission, tmp_path, None, False)


def test_monthly_bounded_openalex_covers_each_month_and_is_explicitly_partial(fetch, tmp_path, monkeypatch):
    mission = {
        'period': {'from': '2025-01-15', 'to': '2025-02-10'},
        'query': {
            'terms': ['active learning'],
            'openalex_monthly_bounded': {'per_month': 2, 'sort': 'relevance_score:desc'},
        },
    }
    requested = []

    def get(url, **kwargs):
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        requested.append(params)
        filt = params['filter'][0]
        january = 'from_publication_date:2025-01-15' in filt
        identifier = 1 if january else 2
        publication_date = '2025-01-20' if january else '2025-02-05'
        body = json.dumps({
            'meta': {'count': 8 if january else 5, 'next_cursor': None},
            'results': [{'id': f'https://openalex.org/W{identifier}',
                         'publication_date': publication_date}],
        }).encode()
        return body, 200

    monkeypatch.setattr(fetch, 'http_get', get)
    result = fetch.fetch_openalex(mission, tmp_path, None, False)
    pages = [item for item in result if item.get('file')]
    assert [item['file'] for item in pages] == ['month_2025_01.json', 'month_2025_02.json']
    assert [(item['month_from'], item['month_to']) for item in pages] == [
        ('2025-01-15', '2025-01-31'), ('2025-02-01', '2025-02-10')]
    assert all(params['per_page'] == ['2'] and params['page'] == ['1'] for params in requested)
    assert all(params['sort'] == ['relevance_score:desc'] for params in requested)
    assert result[-1]['incomplete'] and result[-1]['unique_records'] == 2
    assert result[-1]['source_reported_count_disjoint_months'] == 13


@pytest.mark.parametrize('config', [
    {'per_month': 0}, {'per_month': 101}, {'per_month': True},
    {'per_month': 10, 'sort': 'cited_by_count:desc'},
])
def test_monthly_bounded_openalex_rejects_invalid_limits(fetch, tmp_path, monkeypatch, config):
    mission = {
        'period': {'from': '2025-01-01', 'to': '2025-01-31'},
        'query': {'terms': ['active learning'], 'openalex_monthly_bounded': config},
    }
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: pytest.fail('invalid config'))
    with pytest.raises(fetch.FetchError):
        fetch.fetch_openalex(mission, tmp_path, None, False)


def test_full_enrichment_copies_baseline_and_requests_all_batches(fetch, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from scripts import openalex_enrichment_pilot as pilot
    monkeypatch.setattr(pilot, 'fetch', fetch)
    base = base_package(tmp_path)
    before = {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    monkeypatch.setattr(fetch, 'arxiv_ids_from_parquet', lambda *args: [f'1001.{i:05d}' for i in range(1, 6)])
    responses = iter([enriched_page([1, 2]), enriched_page([3, 4]), enriched_page([5])])
    monkeypatch.setattr(fetch, 'http_get', lambda *a, **kw: (next(responses), 200))
    output = tmp_path / 'enriched'
    # A pilot max_batches=1 must not truncate explicitly selected full mode.
    result = pilot.collect(base, output, max_batches=1, batch_size=2, full_enrichment=True)
    assert not result['partial'] and result['requested_ids'] == 5 and result['returned_records'] == 5
    manifest = json.loads((output / 'manifest.json').read_text())
    mission = json.loads((output / 'mission.json').read_text())
    assert set(manifest['sources']) == set(mission['sources']) == {'arxiv', 'openalex'}
    assert mission['mission_id'] == 'fixture-openalex-enriched'
    assert 'max_batches' not in mission['query']['openalex_enrich_arxiv_ids']
    assert manifest['sources']['openalex']['independent_discovery'] is False
    assert (output / 'arxiv' / 'part.parquet').read_bytes() == before[Path('arxiv/part.parquet')]
    assert (output / 'arxiv' / 'part.parquet').stat().st_ino != (base / 'arxiv' / 'part.parquet').stat().st_ino
    assert before == {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}


def test_arxiv_id_reader_supports_native_snapshot_schema(fetch, tmp_path):
    path = tmp_path / 'native.parquet'
    pq.write_table(pa.table({
        'id': ['2501.00001', '2502.00002', '2503.00003'],
        'versions': [
            [{'version': 'v1', 'created': 'Wed, 15 Jan 2025 12:00:00 GMT'}],
            [{'version': 'v1', 'created': 'Sat, 15 Feb 2025 12:00:00 GMT'}],
            [{'version': 'v2', 'created': 'Sat, 15 Mar 2025 12:00:00 GMT'}],
        ],
    }), path)
    assert fetch.arxiv_ids_from_parquet([path], '2025-02-01', '2025-02-28') == ['2502.00002']


def test_arxiv_id_reader_supports_legacy_adapter_schema(fetch, tmp_path):
    path = tmp_path / 'legacy.parquet'
    pq.write_table(pa.table({
        'arxiv_id': ['2501.00001', '2502.00002'],
        'submission_date': ['15 Jan 2025', '15 Feb 2025'],
    }), path)
    assert fetch.arxiv_ids_from_parquet([path], '2025-01-01', '2025-01-31') == ['2501.00001']
