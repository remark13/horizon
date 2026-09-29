import copy
import json
from datetime import date

import pytest

pa = pytest.importorskip('pyarrow')
import pyarrow.parquet as pq

from saia.arxiv_local import extract
from saia.package_observations import monthly_bounds, observe_package, payload_hash

REVISION = 'b' * 40


def package(tmp_path, *, cap=None, corrupt=False):
    source = tmp_path / REVISION / 'data'
    source.mkdir(parents=True)
    rows = []
    for i, month in enumerate((1, 2, 3, 4)):
        rows.append({'id': '2601.0000' + str(i), 'title': 'Active learning for classification',
                     'abstract': 'A test contribution in active learning.', 'categories': 'cs.LG',
                     'authors_parsed': [['Smith', 'Alice'], ['Li', 'Bo']] if i != 3 else [['Li', 'Bo']],
                     'versions': [{'version': 'v1', 'created': f'01 {("Jan", "Feb", "Mar", "Apr")[i]} 2026 12:00:00 GMT'}],
                     'comments': 'This paper has been withdrawn' if i == 0 else '',
                     'doi': '10.1000/collision'})
    if corrupt:
        r = copy.deepcopy(rows[-1]); r['id'] = '2604.99999'; r['versions'] = []; rows.append(r)
    pq.write_table(pa.Table.from_pylist(rows), source/'train-00000-of-00001.parquet')
    mission = {'mission_id': 'observations-fixture', 'query_version': 'observations-fixture/v1',
               'title': 'Test', 'sources': ['arxiv'], 'as_of_date': '2026-05-01',
               'period': {'from': '2026-01-01', 'to': '2026-04-30'},
               'query': {'terms': ['active learning'], 'arxiv_categories': ['cs.LG'],
                         'arxiv_local_text_scope': {'mode': 'any_exact_phrase', 'fields': ['title', 'abstract'],
                                                    'phrases': ['active learning', 'sample selection']},
                         'arxiv_local_snapshot': {'revision': REVISION, 'dataset': 'fixture/arxiv',
                                                  'expected_files': 1, 'expected_rows': len(rows),
                                                  'record_error_policy': 'quarantine'}}}
    mission_path = tmp_path/'mission.json'; mission_path.write_text(json.dumps(mission))
    target = tmp_path/'package'
    extract(source, mission_path, target, max_records=cap, min_free_gib=0, verbose=False)
    return target


def test_file_observations_keep_partial_unknown_and_do_not_connect_database(tmp_path, monkeypatch):
    target = package(tmp_path, corrupt=True)
    from saia import db
    monkeypatch.setattr(db, 'connect', lambda *a, **k: pytest.fail('File observations must not use the DB'))
    report = observe_package(target)
    assert not report['database_written'] and not report['models_used']
    assert report['input']['coverage']['incomplete']['arxiv']
    assert report['counts']['valid_selected_source_identities'] == 4
    assert report['counts']['independent_studies'] is None
    assert report['counts']['confirmed_weak_signals'] is None
    assert report['counts']['ambiguous_doi_count'] == 1
    assert len(report['parent_publication_series']['points']) == 4
    assert report['parent_publication_series']['coverage_comparable'] is None
    assert report['parent_publication_series']['direction'] == 'not_established'
    assert report['parent_publication_series']['observed_sample']['count']['available']
    assert payload_hash({k: v for k, v in report.items() if k != 'report_payload_sha256'}) == report['report_payload_sha256']


def test_current_withdrawal_projection_not_historical_status(tmp_path):
    report = observe_package(package(tmp_path))
    assert report['counts']['reported_withdrawn_or_retracted'] == 1
    assert report['counts']['quality_include_texts'] == 3
    assert report['counts']['quality_quarantine_texts'] == 1
    first = report['records'][0]
    assert first['status_evidence']['status_effective_date'] is None
    assert first['status_evidence']['observation_time_source'] == 'file_report.created_at'
    assert first['quality']['flags']['deposit_status_evidence'][0]['observation_time_source'] == 'file_report.created_at'
    assert report['parent_publication_series']['points'][0]['topic_works'] == 1
    assert report['parent_current_status_projection_series']['points'][0]['topic_works'] == 0


def test_empty_phrase_and_author_proxies_do_not_invent_independence(tmp_path):
    report = observe_package(package(tmp_path))
    absent = report['phrases'][1]
    assert absent['source_record_ids'] == []
    assert all(p['topic_works'] == 0 for p in absent['publication_series']['points'])
    author = report['phrases'][0]['author_signature_observations']
    assert author['independent_group_count'] is None and author['organisation_diffusion'] is None
    assert author['points'][0]['new_signature_count_in_this_package'] == 1
    assert author['points'][1]['continuing_signature_count'] == 1
    assert author['points'][-1]['new_signature_count_in_this_package'] == 1


def test_capped_observations_are_visible_not_complete(tmp_path):
    report = observe_package(package(tmp_path, cap=2))
    assert report['counts']['valid_selected_source_identities'] == 2
    assert report['input']['coverage']['incomplete']['arxiv']
    assert report['input']['local_audit']['inventory_traversal_complete'] is False
    assert report['parent_publication_series']['observed_sample']['scope'] == 'retrieved_sample_only'


def test_over_bound_refused_without_hidden_subset(tmp_path):
    target = package(tmp_path)
    with pytest.raises(ValueError, match='bound'):
        observe_package(target, max_records=2)


@pytest.mark.parametrize('value', [0, 2001, True, 1.5])
def test_observation_bound_validated(value, tmp_path):
    with pytest.raises(ValueError, match='bound'):
        observe_package(tmp_path, max_records=value)


def test_changed_parquet_refused(tmp_path):
    target = package(tmp_path)
    with (target/'arxiv/train-00000-of-00001.parquet').open('ab') as f:
        f.write(b'corruption')
    with pytest.raises(ValueError):
        observe_package(target)


def test_month_grid_keeps_empty_months_and_leap_year():
    assert monthly_bounds(date(2024, 1, 1), date(2024, 4, 1)) == [
        (date(2024, 1, 1), date(2024, 2, 1)), (date(2024, 2, 1), date(2024, 3, 1)),
        (date(2024, 3, 1), date(2024, 4, 1))]
    with pytest.raises(ValueError):
        monthly_bounds(date(2024, 1, 2), date(2024, 4, 1))
