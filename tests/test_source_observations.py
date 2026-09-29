import hashlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def test_empirical_source_observations_are_not_signal_gold_or_heldout():
    catalog = yaml.safe_load((ROOT / 'evaluation/source-anomalies.v0.4.2.yaml').read_text())
    assert catalog['split'] == 'development_only_not_heldout'
    assert all(catalog[k] is None for k in ('weak_signal_label', 'future_growth_label', 'market_success_label'))
    assert len(catalog['observations']) == 3
    for case in catalog['observations']:
        assert case['scientific_noise_label'] is None
        assert case['doi'] == '10.48550/arxiv.' + case['arxiv_id']
        assert len(set(case['openalex_ids'])) == 2
        assert case['source_urls'] and case['system_test_expectation'] and case['limitation']
    chronology = next(c for c in catalog['observations'] if c['observation_id'] == 'older-publication-later-arxiv-posting')
    assert chronology['earlier_publication_year_reported_in_arxiv_journal_reference'] == 2008
    assert chronology['first_arxiv_submission'] == '2010-01-23'


def test_pilot_asset_coverage_and_rights_are_not_field_or_fulltext_permissions():
    registry = yaml.safe_load((ROOT / 'evaluation/openalex-pilot-assets.v0.4.2.yaml').read_text())
    asset = registry['assets'][0]
    assert asset['status'] == 'partial' and asset['independent_discovery'] is False
    assert asset['requested_arxiv_ids'] == asset['unique_arxiv_dois'] + asset['missing_doi_matches']
    assert asset['returned_records'] == asset['unique_arxiv_dois'] + asset['multiple_record_dois']
    assert asset['license'] == 'CC0-1.0' and asset['license_scope'] == 'openalex_bibliographic_metadata_only'
    assert asset['evaluation_split'].endswith('not_heldout')


def test_source_link_case_is_an_identity_control_not_signal_gold():
    catalog = yaml.safe_load((ROOT / 'evaluation/source-link-observations.v0.4.3.yaml').read_text())
    assert catalog['split'] == 'development_only_not_heldout'
    assert all(catalog[k] is None for k in ('weak_signal_label', 'future_growth_label', 'market_success_label', 'scientific_noise_label'))
    case = catalog['observations'][0]
    assert case['primary_doi'] == '10.48550/arxiv.' + case['primary_arxiv_id']
    assert case['conflicting_link_arxiv_id'] != case['primary_arxiv_id']
    assert case['scientific_noise_label'] is None


def test_frozen_source_link_case_has_preserved_primary_identity():
    from saia.normalize import parse_openalex
    from saia.source_identity import openalex_arxiv_identity
    catalog = yaml.safe_load((ROOT / 'evaluation/source-link-observations.v0.4.3.yaml').read_text())
    case = catalog['observations'][0]
    path = ROOT / catalog['source_package'] / 'openalex' / case['raw_file']
    if not path.exists():
        pytest.skip('Frozen development raw not bundled')
    body = path.read_bytes()
    assert hashlib.sha256(body).hexdigest() == case['raw_file_sha256']
    row = next(r for r in json.loads(body)['results'] if r['id'].rsplit('/', 1)[-1] == case['openalex_id'])
    assert openalex_arxiv_identity(row)['conflicting_arxiv_links']
    ids = parse_openalex(row)['identifiers']
    assert ('arxiv', case['primary_arxiv_id']) in ids
    assert ('arxiv', case['conflicting_link_arxiv_id']) not in ids


def test_frozen_real_pilot_matches_asset_and_observation_metadata_when_present():
    registry = yaml.safe_load((ROOT / 'evaluation/openalex-pilot-assets.v0.4.2.yaml').read_text())
    asset = registry['assets'][0]
    root = ROOT / asset['local_package']
    if not root.exists():
        pytest.skip('Frozen empirical API pilot is not distributed with the source-only checkout')
    manifest = json.loads((root / 'manifest.json').read_text())
    rows = []
    for f in manifest['sources']['openalex']['files']:
        if not f.get('file'): continue
        body = (root / 'openalex' / f['file']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == f['sha256']
        payload = json.loads(body)
        assert len(payload['results']) == f['records']
        rows.extend(payload['results'])
    assert len(rows) == asset['returned_records']
    assert len({r['id'] for r in rows}) == asset['unique_openalex_work_ids']
    assert len({r['doi'] for r in rows}) == asset['unique_arxiv_dois']
    by_id = {r['id'].rsplit('/', 1)[-1]: r for r in rows}
    catalog = yaml.safe_load((ROOT / 'evaluation/source-anomalies.v0.4.2.yaml').read_text())
    for case in catalog['observations']:
        assert all(by_id[i]['doi'].lower() == 'https://doi.org/' + case['doi'] for i in case['openalex_ids'])
