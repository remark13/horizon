import copy
import hashlib
import json

import pytest

from saia.ingest import collection_content_hash, collection_coverage, ingest, validate_collection_input


def fixture(tmp_path):
    mission = {'mission_id': 'fixture', 'query_version': 'fixture/v1', 'query': {'terms': ['machine learning']}}
    text = json.dumps(mission)
    source = tmp_path / 'arxiv'
    source.mkdir()
    path = source / 'page.xml'
    path.write_bytes(b'fixture raw bytes')
    manifest = {'mission_id': 'fixture', 'query_version': 'fixture/v1', 'mission_snapshot_file': 'mission.json',
                'mission_file_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'sources': {'arxiv': {'files': [{'file': path.name, 'records': 1, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}}}
    (tmp_path / 'mission.json').write_text(text)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    return mission, text, manifest, path


def test_verified_files_and_frozen_mission_are_valid_input(tmp_path):
    mission, text, manifest, _ = fixture(tmp_path)
    validate_collection_input(tmp_path, manifest, mission, text)


@pytest.mark.parametrize('change', ['mission', 'query', 'config_digest', 'file_digest', 'missing', 'escape', 'empty'])
def test_invalid_provenance_is_rejected_before_any_database_write(tmp_path, monkeypatch, change):
    from saia import ingest as module
    _, _, manifest, path = fixture(tmp_path)
    entry = manifest['sources']['arxiv']['files'][0]
    if change == 'mission': manifest['mission_id'] = 'other'
    if change == 'query': manifest['query_version'] = 'fixture/v2'
    if change == 'config_digest': manifest['mission_file_sha256'] = 'wrong'
    if change == 'file_digest': entry['sha256'] = 'wrong'
    if change == 'missing': path.unlink()
    if change == 'escape': entry['file'] = '../page.xml'
    if change == 'empty': entry['file'] = None
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    def forbidden(): pytest.fail('Invalid collection must not open DB')
    monkeypatch.setattr(module.db, 'connect', forbidden)
    with pytest.raises(ValueError): ingest(tmp_path, verbose=False)


def test_partial_download_markers_are_allowed_but_absent_declared_files_are_not(tmp_path):
    mission, text, manifest, _ = fixture(tmp_path)
    partial = copy.deepcopy(manifest)
    partial['sources']['arxiv']['files'].append({'file': None, 'incomplete': True, 'reason': 'fixture timeout'})
    validate_collection_input(tmp_path, partial, mission, text)


def test_symlink_outside_source_cannot_be_imported(tmp_path):
    mission, text, manifest, path = fixture(tmp_path)
    other = tmp_path / 'outside.xml'
    other.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(other)
    with pytest.raises(ValueError): validate_collection_input(tmp_path, manifest, mission, text)


@pytest.mark.parametrize('change', ['connector', 'scope', 'partial', 'count', 'config'])
def test_collection_fingerprint_includes_source_policy_and_configuration(tmp_path, change):
    _, _, manifest, _ = fixture(tmp_path)
    before = collection_content_hash(manifest)
    if change == 'connector': manifest['connector_version'] = 'new'
    if change == 'scope': manifest['sources']['arxiv']['crosslist_completeness'] = 'not_proven'
    if change == 'partial': manifest['sources']['arxiv']['files'].append({'file': None, 'incomplete': True})
    if change == 'count': manifest['sources']['arxiv']['files'][0]['records'] = 2
    if change == 'config': manifest['mission_file_sha256'] = 'changed'
    assert collection_content_hash(manifest) != before


def test_collection_fingerprint_ignores_repeat_time_and_does_not_invent_independence(tmp_path):
    _, _, manifest, _ = fixture(tmp_path)
    before = collection_content_hash(manifest)
    manifest['fetch_started_utc'] = 'later'
    manifest['sources']['arxiv']['files'][0]['url'] = 'another mirror request URL'
    assert collection_content_hash(manifest) == before
    assert collection_coverage(manifest)['source_modes']['arxiv']['independent_discovery'] is None


def test_collection_coverage_preserves_thematic_selection_provenance(tmp_path):
    _, _, manifest, _ = fixture(tmp_path)
    block = manifest['sources']['arxiv']
    block['access_mode'] = 'local-arxiv-metadata-parquet'
    block['selection'] = {
        'controlled_search_plan_sha256': 'plan',
        'selection_engine': 'verified_thematic_target_pack',
        'cache_manifest_sha256': 'cache',
        'target_pack_manifest_sha256': 'packs',
        'selected_target_packs': ['health-preservation--tissue-engineering'],
        'selection_predicate_reapplied': True,
    }
    mode = collection_coverage(manifest)['source_modes']['arxiv']
    assert mode['selection_engine'] == 'verified_thematic_target_pack'
    assert mode['selected_target_packs'][0] == 'health-preservation--tissue-engineering'
    assert mode['selection_predicate_reapplied'] is True


def test_duplicate_manifest_file_is_rejected(tmp_path):
    mission, text, manifest, _ = fixture(tmp_path)
    manifest['sources']['arxiv']['files'] *= 2
    with pytest.raises(ValueError, match='Повторён файл'): validate_collection_input(tmp_path, manifest, mission, text)
