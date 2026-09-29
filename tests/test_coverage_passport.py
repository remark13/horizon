import hashlib
import json

import pytest

from saia.coverage_passport import summarize_evidence, verify
from saia.package_observations import payload_hash


def fixture(tmp_path, derived=False):
    package = tmp_path / 'package'
    package.mkdir()
    base_manifest_sha = 'b' * 64
    base_mission_sha = 'c' * 64
    mission = {
        'mission_id': 'fixture-enriched' if derived else 'fixture',
        'as_of_date': '2026-09-01',
        'period': {'from': '2024-09-01', 'to': '2026-08-31'},
    }
    manifest = {'mission_id': mission['mission_id']}
    if derived:
        mission['base_package'] = {
            'mission_id': 'fixture', 'manifest_sha256': base_manifest_sha,
            'mission_sha256': base_mission_sha,
        }
        manifest.update({
            'input_base_manifest_sha256': base_manifest_sha,
            'sources': {
                'arxiv': {'reused_from_base_manifest_sha256': base_manifest_sha},
                'openalex': {'independent_discovery': False},
            },
        })
    mission_bytes = json.dumps(mission).encode()
    manifest_bytes = json.dumps(manifest).encode()
    (package / 'mission.json').write_bytes(mission_bytes)
    (package / 'manifest.json').write_bytes(manifest_bytes)
    evidence = {
        'mission_id': 'fixture', 'as_of_date': '2026-09-01',
        'period_from': '2024-09-01', 'period_end_exclusive': '2026-09-01',
        'input': {
            'selected_package_manifest_bytes_sha256': (
                base_manifest_sha if derived else hashlib.sha256(manifest_bytes).hexdigest()
            ),
            'mission_bytes_sha256': (
                base_mission_sha if derived else hashlib.sha256(mission_bytes).hexdigest()
            ),
        },
        'coverage': {
            'inventory_traversal_complete': True,
            'within_frozen_snapshot_same_query_window_comparable': True,
        },
        'accuracy_evaluated': False,
        'confirmed_weak_signals': None,
    }
    evidence['report_payload_sha256'] = payload_hash(evidence)
    evidence_path = tmp_path / 'evidence.json'
    evidence_path.write_text(json.dumps(evidence))
    return package, evidence_path


@pytest.mark.parametrize('derived', [False, True])
def test_verified_direct_and_exact_enriched_derivative_bindings(tmp_path, derived):
    package, evidence = fixture(tmp_path, derived)
    manifest, mission, report = verify(package, evidence)
    assert manifest['mission_id'] == mission['mission_id']
    assert report['coverage']['inventory_traversal_complete'] is True


def test_unproven_temporal_comparability_is_rejected(tmp_path):
    package, evidence_path = fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text())
    evidence['coverage']['within_frozen_snapshot_same_query_window_comparable'] = None
    evidence.pop('report_payload_sha256')
    evidence['report_payload_sha256'] = payload_hash(evidence)
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match='comparability'):
        verify(package, evidence_path)


def test_guarded_index_passport_requires_separate_provenance(tmp_path):
    package, evidence_path = fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text())
    evidence['version'] = 'arxiv-parent-corpus-audit-guarded-index-v1'
    evidence['input'].update({
        'index_manifest_sha256': 'a' * 64,
        'index_file_sha256': 'b' * 64,
        'duplicate_guard_sha256': 'c' * 64,
    })
    evidence['coverage'].update({
        'inventory_traversal_method': 'guarded_index_built_from_complete_pinned_mirror',
        'same_source_index_checksum_verified': True,
        'selected_work_predicate_and_metadata_rechecked': True,
    })
    evidence['report_payload_sha256'] = payload_hash({
        key: value for key, value in evidence.items()
        if key != 'report_payload_sha256'})
    evidence_path.write_text(json.dumps(evidence))
    assert verify(package, evidence_path)[2]['version'] == evidence['version']
    evidence['coverage']['selected_work_predicate_and_metadata_rechecked'] = False
    evidence['report_payload_sha256'] = payload_hash({
        key: value for key, value in evidence.items()
        if key != 'report_payload_sha256'})
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match='provenance'):
        verify(package, evidence_path)


def test_summary_separates_parent_backdrop_from_candidate_growth():
    result = summarize_evidence({
        'period_from': '2024-09-01', 'period_end_exclusive': '2026-09-01',
        'counts': {'parent_native_ids_in_period': 200, 'phrase_scope_native_ids_in_period': 10},
        'coverage': {'unit': 'native_ids'},
        'full_period_descriptive': {
            'window_count': 24, 'phrase_count_slope_per_month': 1,
            'parent_count_slope_per_month': 10, 'share_slope_per_month': -0.01,
            'share_first_to_last_change': -0.1,
        },
        'publication_series': {'observed_sample': {
            'count': {'direction': 'increasing', 'change': 2},
            'share': {'direction': 'decreasing', 'change': -0.01},
        }},
        'limitations': ['bounded parent'],
    }, 4)
    assert result['coverage_passport_id'] == 4
    assert result['full_period']['phrase_share_slope_per_month'] == -0.01
    assert result['recent_window']['phrase_count_direction'] == 'increasing'
    assert 'не означает' in result['interpretation']
