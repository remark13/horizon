import pytest

from saia.source_audit import summarize, audit


def row(identifier, author, day='2016-01-01'):
    return {'id': identifier, 'doi': 'https://doi.org/10.48550/arxiv.1601.00001',
            'publication_date': day, 'authorships': [{'author': {'id': author}}]}


def test_duplicate_doi_and_identity_dates_are_observations_not_labels():
    report = summarize([row('W1', 'A1'), row('W2', 'A2', '2015-12-31')])
    assert report['unique_dois'] == 1
    assert report['extra_records_for_same_doi'] == 1
    assert report['author_identity_conflict_groups'] == 1
    assert report['publication_date_disagreement_groups'] == 1
    assert 'weak_signal' not in report


def test_repeated_source_id_is_not_extra_evidence():
    with pytest.raises(ValueError, match='пагинацию'):
        summarize([row('W1', 'A1'), row('W1', 'A1')])


@pytest.mark.parametrize('change', ['bytes', 'count', 'total', 'mission', 'path'])
def test_audit_rejects_changed_input(tmp_path, change):
    import hashlib
    import json
    mission = {'mission_id': 'audit-fixture', 'query_version': 'audit-fixture/v1'}
    text = json.dumps(mission)
    source = tmp_path / 'openalex'
    source.mkdir()
    page = source / 'page.json'
    page.write_text(json.dumps({'results': [row('W1', 'A1')]}))
    manifest = {'mission_id': mission['mission_id'], 'query_version': mission['query_version'],
                'mission_snapshot_file': 'mission.json',
                'mission_file_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'sources': {'openalex': {'total_records': 1, 'files': [
                    {'file': page.name, 'records': 1, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest()}]}}}
    if change == 'bytes': page.write_text('{}')
    if change == 'count': manifest['sources']['openalex']['files'][0]['records'] = 2
    if change == 'total': manifest['sources']['openalex']['total_records'] = 2
    if change == 'mission': manifest['mission_file_sha256'] = 'changed'
    if change == 'path': manifest['mission_snapshot_file'] = '../mission.json'
    (tmp_path / 'mission.json').write_text(text)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): audit(tmp_path)


def test_real_pilot_is_partial_and_preserves_evidence():
    from pathlib import Path
    package = Path(__file__).resolve().parents[1] / 'data/raw/ai-area-2017-v041-openalex-pilot'
    if not package.exists():
        pytest.skip('Local development probe not bundled')
    report = audit(package)
    assert report['partial']
    assert report['scientific_labels'] is None
    assert report['observations']['unique_dois'] == 84
    assert report['observations']['author_identity_conflict_groups'] == 2
