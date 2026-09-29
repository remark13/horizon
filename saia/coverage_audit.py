"""Audit declared mirror files and development references without adding targets.

This diagnoses an observed input corpus; it never establishes field-wide coverage
or silently binds an old normalize run to the latest collection batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from saia import hybrid, runs
from saia.evaluation_catalog import load, selected_cases
from saia.reference_audit import validate_report


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def categories(text: str) -> set[str]:
    return set(re.findall(r'\(([a-z-]+(?:\.[A-Za-z-]+)?)\)', text or ''))


def diagnose(reference: dict, present: set[str], selected: set[str]) -> str:
    if reference['requested_id'] in present:
        return 'present_in_declared_files'
    if not selected.intersection(reference['categories']):
        return 'outside_selected_categories'
    if reference['primary_category'] not in selected:
        return 'possible_primary_partition_crosslist_gap'
    return 'missing_despite_matching_primary_category'


def inspect_files(raw_dir: Path, manifest: dict) -> dict:
    import pyarrow.parquet as pq
    files = [f for f in manifest['sources']['arxiv']['files'] if f.get('file')]
    ids, records, primary_matches, crosslisted, unknown_primary = set(), 0, 0, 0, 0
    primary_counts, file_categories = Counter(), set()
    for entry in files:
        filename = entry['file']
        # A manifest must not read paths outside its own arxiv directory.
        if Path(filename).name != filename or not filename.endswith('.parquet'):
            raise ValueError('Expected a local Parquet basename')
        path = raw_dir / 'arxiv' / filename
        if file_hash(path) != entry['sha256']:
            raise ValueError('Manifest/file hash mismatch: ' + filename)
        partition = filename.rsplit('_', 2)[0]
        file_categories.add(partition)
        table = pq.read_table(path, columns=['arxiv_id', 'primary_subject', 'subjects'])
        if table.num_rows != entry['records']:
            raise ValueError('Manifest/file row count mismatch: ' + filename)
        for row in table.to_pylist():
            records += 1
            ids.add(row['arxiv_id'])
            primary = categories(row['primary_subject'])
            subjects = categories(row['subjects'])
            if len(primary) != 1:
                unknown_primary += 1
            else:
                code = next(iter(primary))
                primary_counts[code] += 1
                primary_matches += code == partition
                crosslisted += bool(subjects - {code})
    return {'file_count': len(files), 'raw_rows': records, 'unique_arxiv_ids': len(ids),
            'duplicate_rows': records - len(ids), 'primary_matches_file_partition': primary_matches,
            'unknown_primary_rows': unknown_primary, 'crosslisted_rows': crosslisted,
            'primary_category_counts': dict(primary_counts), 'file_partition_categories': sorted(file_categories),
            'present_ids': ids}


def audit(raw_dir: Path, mission: dict, catalog: dict, references: dict,
          mission_bytes_verified: bool = False) -> dict:
    validate_report(references, catalog)
    manifest_path = raw_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['mission_id'] != mission['mission_id']:
        raise ValueError('Mission and manifest differ')
    # A failed byte binding is kept visible; this read-only audit does not repair history.
    mission_hash = hybrid.digest(mission)
    profile = inspect_files(raw_dir, manifest)
    present = profile.pop('present_ids')
    selected = set(mission['query']['arxiv_categories'])
    if set(profile['file_partition_categories']) != selected:
        raise ValueError('Mission categories and manifest partitions differ')
    by_id = {r['requested_id']: r for r in references['records']}
    refs = sorted({ref for c in selected_cases(catalog, 'development')
                   if c['as_of_date'] == mission['as_of_date'] for ref in c['arxiv_ids']})
    rows = [{**{k: by_id[ref][k] for k in ('requested_id', 'title', 'primary_category', 'categories', 'request_url')},
             'coverage_status': diagnose(by_id[ref], present, selected)} for ref in refs]
    return {'version': 'mirror-coverage-audit-0.4.1', 'code_version': runs.code_version(),
            'runtime': runs.runtime_snapshot(), 'mission_id': mission['mission_id'],
            'manifest_sha256': file_hash(manifest_path),
            'manifest_mission_file_sha256': manifest.get('mission_file_sha256'),
            'mission_semantic_hash': mission_hash, 'selected_categories': sorted(selected),
            'mission_configuration_binding': 'verified_bytes' if mission_bytes_verified else 'not_verified_current_configuration',
            'catalog_hash': hybrid.digest(catalog), 'reference_audit_hash': hybrid.digest(references),
            'profile': profile, 'development_reference_rows': rows,
            'status_counts': dict(Counter(r['coverage_status'] for r in rows)),
            'scope': 'declared_manifest_files_not_backfilled_into_old_normalize_provenance',
            'field_coverage': 'unknown', 'crosslist_completeness': 'not_proven',
            'limitations': ['Current category metadata do not prove historical category membership.',
                            'Complete declared download is not complete category discovery.',
                            'Target references are diagnostics only; never injected into collection.',
                            'No full text or historical revision was recovered.']}


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit file hashes, partitions and development reference coverage')
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--mission', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--reference-audit', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--allow-unverified-mission', action='store_true',
                        help='Read-only diagnosis only; never binds current config to an old normalize run')
    args = parser.parse_args()
    mission = json.loads(args.mission.read_text())
    manifest = json.loads((args.raw / 'manifest.json').read_text())
    verified = file_hash(args.mission) == manifest.get('mission_file_sha256')
    if not verified and not args.allow_unverified_mission:
        raise ValueError('Mission bytes differ from the declared collection configuration')
    result = audit(args.raw, mission, load(args.catalog), json.loads(args.reference_audit.read_text()), verified)
    result['current_mission_file_sha256'] = file_hash(args.mission)
    args.export.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result['profile'])
    print(result['status_counts'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
