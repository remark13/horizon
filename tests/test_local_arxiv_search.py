from datetime import date, timezone, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from saia.local_arxiv_search import _substring_mask, search, validate_inventory
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.query_expansion import compile_plan


def row(identifier, title, abstract, created):
    stamp = datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc)
    return {
        'id': identifier, 'title': title, 'abstract': abstract, 'categories': 'cs.LG',
        'versions': [{'version': 'v1', 'created': stamp.strftime('%a, %d %b %Y %H:%M:%S %Z')}],
        'authors_parsed': [['Doe', 'Jane', '']], 'doi': None, 'journal-ref': None,
        'comments': None, 'update_date': created, 'authors': 'Doe, Jane', 'license': None,
    }


def test_local_search_uses_exact_terms_exclusions_v1_date_and_limit(tmp_path):
    path = tmp_path / 'train-00000-of-00001.parquet'
    pq.write_table(pa.Table.from_pylist([
        row('1601.00001', 'Graph neural method', 'Machine learning method.', date(2016, 1, 1)),
        row('1602.00001', 'Clinical study', 'Machine learning in a clinical study.', date(2016, 2, 1)),
        row('1701.00001', 'Future machine learning', 'Future.', date(2017, 1, 2)),
        row('1603.00001', 'Machinery', 'Learning is separate, not the exact phrase.', date(2016, 3, 1)),
    ]), path)
    validate_inventory.cache_clear()
    plan = compile_plan('machine learning', [], ['clinical study'],
                        date(2015, 1, 1), date(2017, 1, 1))
    result = search(tmp_path, plan, 1, expected_files=1, expected_rows=4)
    assert [work.source_ids[0] for work in result.works] == ['https://arxiv.org/abs/1601.00001']
    assert result.audit['scanned_rows'] == 4
    assert result.audit['eligible_matches'] == 1
    assert result.audit['coverage_comparable'] is None


def test_coarse_filter_does_not_turn_exclusion_substring_into_false_negative(tmp_path):
    path = tmp_path / 'train-00000-of-00001.parquet'
    pq.write_table(pa.Table.from_pylist([
        row('1601.00001', 'A network method', 'Machine\n learning result.', date(2016, 1, 1)),
    ]), path)
    validate_inventory.cache_clear()
    plan = compile_plan('machine learning', [], ['net'],
                        date(2015, 1, 1), date(2017, 1, 1))
    result = search(tmp_path, plan, 10, expected_files=1, expected_rows=1)
    assert len(result.works) == 1


def test_multi_phrase_coarse_filter_keeps_title_abstract_and_whitespace_matches():
    table = pa.table({
        'title': ['UAV swarm operation', None, 'Other research'],
        'abstract': [None, 'GNSS\n denied navigation for drones', 'Unrelated'],
    })
    mask = _substring_mask(table, ['UAV swarm', 'GNSS denied navigation'])
    assert mask.to_pylist() == [True, True, False]


def test_local_search_finds_orthographic_variant_and_legacy_plan_remains_literal(tmp_path):
    path = tmp_path / 'train-00000-of-00001.parquet'
    pq.write_table(pa.Table.from_pylist([
        row('2604.24447', 'Vision-Language-Action Models', 'On-robot deployment',
            date(2026, 4, 27)),
    ]), path)
    validate_inventory.cache_clear()
    plan = compile_plan('vision language action', [], [],
                        date(2026, 1, 1), date(2026, 9, 1))
    assert plan['matching_version'] == ORTHOGRAPHIC_MATCHING_VERSION
    result = search(tmp_path, plan, 10, expected_files=1, expected_rows=1)
    assert result.audit['eligible_matches'] == 1
    assert result.works[0].source_ids == ('https://arxiv.org/abs/2604.24447',)
    legacy_plan = dict(plan)
    legacy_plan.pop('matching_version')
    old = search(tmp_path, legacy_plan, 10, expected_files=1, expected_rows=1)
    assert old.audit['eligible_matches'] == 0


def test_fixture_inventory_does_not_inherit_worker_index(tmp_path, monkeypatch):
    path = tmp_path / 'train-00000-of-00001.parquet'
    pq.write_table(pa.Table.from_pylist([
        row('2501.00001', 'Sovereign cloud prototype', 'A local test.', date(2025, 1, 1)),
    ]), path)
    validate_inventory.cache_clear()
    monkeypatch.setenv('SAIA_ARXIV_TRIGRAM_INDEX_DIR', str(tmp_path / 'nonexistent-index'))
    result = search(tmp_path, {'included_terms': ['sovereign cloud'], 'exclusions': [],
                               'date_from': '2024-01-01', 'as_of_date': '2026-01-01'},
                    10, expected_files=1, expected_rows=1)
    assert result.audit['eligible_matches'] == 1
    assert result.audit['adapter_version'] != 'arxiv-trigram-index-v3'
