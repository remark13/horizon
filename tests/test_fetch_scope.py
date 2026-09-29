import importlib.util
import json
import sys
from pathlib import Path


def test_snapshot_manifest_preserves_crosslist_and_historical_text_limits(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch.py'
    spec = importlib.util.spec_from_file_location('saia_fetch_scope_fixture', script)
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    mission = {'mission_id': 'fixture', 'sources': ['arxiv'], 'query_version': 'fixture/v1',
               'as_of_date': '2017-01-01', 'period': {'from': '2010-01-01', 'to': '2016-12-31'},
               'query': {'terms': ['machine learning'], 'arxiv_categories': ['cs.LG'],
                         'arxiv_snapshot': {'dataset': 'fixture', 'revision': 'pinned'}}}
    mission_path = tmp_path / 'mission.json'
    mission_path.write_text(json.dumps(mission))
    output = tmp_path / 'raw'
    monkeypatch.setattr(fetch, 'fetch_arxiv_snapshot', lambda *_: [
        {'file': 'fixture.parquet', 'records': 1, 'sha256': 'fixture'}])
    monkeypatch.setattr(sys, 'argv', ['fetch.py', str(mission_path), '--out', str(output)])
    assert fetch.main() == 0
    manifest = json.loads((output / 'manifest.json').read_text())
    source = manifest['sources']['arxiv']
    assert manifest['connector_version'] == '0.4.2'
    assert source['category_scope'] == 'file_partitions_not_official_category_query'
    assert source['crosslist_completeness'] == 'not_proven' and source['field_coverage'] == 'unknown'
    assert source['historical_text_scope'] == 'current_metadata_not_recovered_versions'
    assert source['dataset_revision'] == 'pinned'
    assert (output / 'mission.json').read_bytes() == mission_path.read_bytes()
    assert manifest['mission_snapshot_file'] == 'mission.json'
    assert fetch.sha256_of(output / 'mission.json') == manifest['mission_file_sha256']
    # A later edit must never relabel or overwrite the old raw package.
    before = (output / 'manifest.json').read_bytes()
    mission['query']['terms'] = ['different query']
    mission_path.write_text(json.dumps(mission))
    assert fetch.main() == 3
    assert (output / 'manifest.json').read_bytes() == before


def test_collection_uses_one_atomic_configuration_snapshot_even_if_input_is_edited(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch.py'
    spec = importlib.util.spec_from_file_location('saia_fetch_atomic_fixture', script)
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    mission = {'mission_id': 'fixture', 'sources': ['arxiv'], 'query_version': 'fixture/v1',
               'period': {}, 'query': {'terms': ['original'], 'arxiv_snapshot': {'revision': 'pinned'}}}
    mission_path = tmp_path / 'mission.json'
    mission_path.write_text(json.dumps(mission))
    original = mission_path.read_bytes()
    output = tmp_path / 'raw'
    def edit_input(*_):
        mission['query']['terms'] = ['edited while collecting']
        mission_path.write_text(json.dumps(mission))
        return [{'file': 'fixture.parquet', 'records': 1, 'sha256': 'fixture'}]
    monkeypatch.setattr(fetch, 'fetch_arxiv_snapshot', edit_input)
    monkeypatch.setattr(sys, 'argv', ['fetch.py', str(mission_path), '--out', str(output)])
    assert fetch.main() == 0
    manifest = json.loads((output / 'manifest.json').read_text())
    assert (output / 'mission.json').read_bytes() == original
    assert manifest['mission_file_sha256'] == fetch.sha256_of(output / 'mission.json')
    assert manifest['mission_file_sha256'] != fetch.sha256_of(mission_path)
