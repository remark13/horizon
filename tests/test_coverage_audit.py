import pytest

from saia.coverage_audit import diagnose, file_hash, inspect_files
from saia.ingest import upsert_collection_batch


def test_missing_crosslisted_work_is_not_mislabeled_as_outside_field():
    ref = {'requested_id': '1506.02516', 'primary_category': 'cs.NE', 'categories': ['cs.NE', 'cs.CL', 'cs.LG']}
    assert diagnose(ref, set(), {'cs.LG', 'stat.ML', 'cs.AI'}) == 'possible_primary_partition_crosslist_gap'
    assert diagnose(ref, {'1506.02516'}, {'cs.LG'}) == 'present_in_declared_files'


def test_outside_selected_category_is_separate_from_missing_matching_primary():
    ref = {'requested_id': '1410.5401', 'primary_category': 'cs.NE', 'categories': ['cs.NE']}
    assert diagnose(ref, set(), {'cs.LG'}) == 'outside_selected_categories'
    assert diagnose(ref, set(), {'cs.NE'}) == 'missing_despite_matching_primary_category'


def fixture_file(tmp_path):
    pq = pytest.importorskip('pyarrow.parquet')
    import pyarrow as pa
    path = tmp_path / 'arxiv' / 'cs.LG_2015_11.parquet'
    path.parent.mkdir()
    pq.write_table(pa.Table.from_pylist([
        {'arxiv_id': '1511.06279', 'primary_subject': 'Machine Learning (cs.LG)',
         'subjects': 'Machine Learning (cs.LG); Neural and Evolutionary Computing (cs.NE)'},
        {'arxiv_id': '1511.06279', 'primary_subject': 'Machine Learning (cs.LG)',
         'subjects': 'Machine Learning (cs.LG)'}]), path)
    return path, {'sources': {'arxiv': {'files': [{'file': path.name, 'sha256': file_hash(path), 'records': 2}]}}}


def test_partition_audit_reports_duplicates_and_crosslist_without_asserting_completeness(tmp_path):
    _, manifest = fixture_file(tmp_path)
    result = inspect_files(tmp_path, manifest)
    assert result['raw_rows'] == 2 and result['unique_arxiv_ids'] == 1 and result['duplicate_rows'] == 1
    assert result['crosslisted_rows'] == 1 and result['primary_matches_file_partition'] == 2


@pytest.mark.parametrize('bad_field', ['sha256', 'records', 'file'])
def test_file_audit_rejects_tampering_and_directory_escape(tmp_path, bad_field):
    _, manifest = fixture_file(tmp_path)
    manifest['sources']['arxiv']['files'][0][bad_field] = {'sha256': 'bad', 'records': 3, 'file': '../outside.parquet'}[bad_field]
    with pytest.raises(ValueError): inspect_files(tmp_path, manifest)


class Cursor:
    def execute(self, sql, args):
        self.args = args
    def fetchone(self):
        return (1,)


@pytest.mark.parametrize('with_scope', [False, True])
def test_complete_download_never_becomes_proven_field_coverage(with_scope):
    block = {'files': [], 'total_records': 10, 'access_mode': 'hf-arxiv-parquet-snapshot'}
    if with_scope:
        block.update(category_scope='file_partitions_not_official_category_query',
                     crosslist_completeness='not_proven', field_coverage='unknown', dataset_revision='pinned')
    manifest = {'mission_id': 'fixture', 'query_version': 'fixture/v1', 'connector_version': '0.1',
                'fetch_started_utc': '2026-09-17T12:00:00Z', 'sources': {'arxiv': block}}
    cur = Cursor()
    upsert_collection_batch(cur, manifest, 'fixture/v1')
    assert cur.args[-2] == 'complete'  # technical completion of declared download only
    coverage = cur.args[-1].obj['source_modes']['arxiv']
    assert coverage['field_coverage'] == 'unknown'
    assert coverage['crosslist_completeness'] == ('not_proven' if with_scope else 'unknown')
    assert coverage['dataset_revision'] == ('pinned' if with_scope else None)
