"""Bounded read-only text-source audit; stdlib-only replay of a frozen export."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from saia import canonical_text


ROOT = Path(__file__).resolve().parents[1]
VERSION = "canonical-text-source-audit-input-v1"


def content_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def export_payload(cfg):
    # Keep database/normalizer dependencies out of the offline replay path.
    from saia import db, normalize
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SET LOCAL statement_timeout='60s'")
        cur.execute('SELECT mission_id,kind,status,as_of_date,query_version_id,notes '
                    'FROM analysis_run WHERE run_id=%s', (cfg['normalize_run_id'],))
        run = cur.fetchone()
        if (not run or run[:3] != (cfg['mission_id'], 'normalize', 'done')
                or run[3].isoformat() != cfg['historical_cutoff_exclusive']):
            raise ValueError('Wrong frozen normalization or historical date')
        query_id, batch_id = run[4], (run[5] or {}).get('collection_batch_id')
        cur.execute('SELECT count(*) FROM work_version WHERE run_id=%s', (cfg['normalize_run_id'],))
        version_count = cur.fetchone()[0]
        if version_count != cfg['expected_work_versions']:
            raise ValueError('Frozen linked version count changed')
        cur.execute('SELECT work_id,canonical_title,abstract FROM work WHERE run_id=%s ORDER BY work_id',
                    (cfg['normalize_run_id'],))
        work_rows = cur.fetchall()
        if len(work_rows) != cfg['expected_works'] or len(work_rows) > cfg['max_works']:
            raise ValueError('Frozen work count changed or resource cap exceeded')
        # Include duplicate raw IDs for an already linked source identity, as
        # normalization consumes them even if work_version keeps one link.
        cur.execute('SELECT v.work_id,r.raw_record_id,r.source,r.source_record_id,r.payload,'
                    'r.content_sha256,s.snapshot_id,s.file_name,s.file_sha256,s.fetched_at '
                    'FROM work_version v JOIN raw_record r '
                    'ON r.source=v.source AND r.source_record_id=v.source_record_id '
                    'JOIN source_snapshot s ON s.snapshot_id=r.snapshot_id '
                    'WHERE v.run_id=%s AND s.mission_id=%s AND '
                    '((%s::bigint IS NULL AND s.query_version_id=%s) OR EXISTS '
                    '(SELECT 1 FROM collection_batch_snapshot bs '
                    'WHERE bs.snapshot_id=s.snapshot_id AND bs.batch_id=%s)) '
                    'ORDER BY v.work_id,CASE r.source WHEN \'openalex\' THEN 0 ELSE 1 END,r.raw_record_id '
                    'LIMIT %s', (cfg['normalize_run_id'], cfg['mission_id'], batch_id, query_id,
                                 batch_id, cfg['max_candidates'] + 1))
        raw_rows = cur.fetchall()
        if len(raw_rows) > cfg['max_candidates']:
            raise ValueError('Candidate resource cap exceeded')
    by_work, snapshots = {}, {}
    for work_id, raw_id, source, record_id, raw, stored_hash, sid, filename, file_hash, observed in raw_rows:
        if source not in normalize.PARSERS:
            raise ValueError('Unexpected source in canonical scientific work')
        parsed = normalize.PARSERS[source](raw)
        candidate = {'raw_record_id': raw_id, 'source': source, 'source_record_id': record_id,
                     'title_key': normalize.title_key(parsed['title']), 'abstract': parsed['abstract'],
                     'abstract_sha256': canonical_text.text_sha256(parsed['abstract']),
                     'arxiv_ids': [value for kind, value in parsed['identifiers'] if kind == 'arxiv'],
                     'observed_at': observed.isoformat(), 'snapshot_id': sid,
                     'stored_raw_content_sha256': stored_hash,
                     'exported_raw_payload_json_sha256': content_sha256(raw),
                     'metadata': {key: raw[key] for key in
                                  ('id', 'created', 'published', 'updated', '_arxiv_versions',
                                   '_source_format', '_submission_date_raw') if key in raw}}
        by_work.setdefault(work_id, []).append(candidate)
        snapshots[sid] = {'snapshot_id': sid, 'source': source, 'file_name': filename,
                          'recorded_file_sha256': file_hash, 'fetched_at': observed.isoformat()}
    works = []
    for work_id, title, abstract in work_rows:
        if not by_work.get(work_id):
            raise ValueError('A frozen work has no source-bound raw candidates')
        works.append({'work_id': work_id, 'canonical_title': title,
                      'canonical_title_key': normalize.title_key(title),
                      'old_abstract_sha256': canonical_text.text_sha256(abstract),
                      'candidates': by_work[work_id]})
    body = {'version': VERSION, 'mission_id': cfg['mission_id'],
            'normalize_run_id': cfg['normalize_run_id'], 'query_version_id': query_id,
            'collection_batch_id': batch_id, 'work_version_count': version_count,
            'candidate_count': len(raw_rows), 'canonical_text_exported_as_checksums_only': True,
            'source_snapshots': [snapshots[k] for k in sorted(snapshots)],
            'works': works}
    return {**body, 'content_sha256': content_sha256(body)}


def verify_payload(payload, cfg):
    if payload.get('version') != VERSION or payload.get('content_sha256') != content_sha256(
            {key: value for key, value in payload.items() if key != 'content_sha256'}):
        raise ValueError('Frozen input schema or checksum differs')
    if (payload['mission_id'] != cfg['mission_id'] or payload['normalize_run_id'] != cfg['normalize_run_id']
            or len(payload['works']) != cfg['expected_works']
            or payload['work_version_count'] != cfg['expected_work_versions']):
        raise ValueError('Frozen input does not match the configured corpus')
    ids = [row['work_id'] for row in payload['works']]
    if ids != sorted(set(ids)) or len(ids) > cfg['max_works']:
        raise ValueError('Duplicate/unsorted works or resource cap exceeded')
    snapshots = {row['snapshot_id']: row for row in payload['source_snapshots']}
    raw_ids = []
    for work in payload['works']:
        checksum = work['old_abstract_sha256']
        if checksum is not None and (not isinstance(checksum, str) or len(checksum) != 64):
            raise ValueError('Invalid canonical text checksum')
        for row in work['candidates']:
            if canonical_text.text_sha256(row['abstract']) != row['abstract_sha256']:
                raise ValueError('Source text checksum differs')
            snapshot = snapshots.get(row['snapshot_id'])
            if (not snapshot or snapshot['source'] != row['source']
                    or snapshot['fetched_at'] != row['observed_at']):
                raise ValueError('Source snapshot binding differs')
            raw_ids.append(row['raw_record_id'])
    if (len(raw_ids) != payload['candidate_count'] or len(set(raw_ids)) != len(raw_ids)
            or len(raw_ids) > cfg['max_candidates']):
        raise ValueError('Duplicate source bindings or resource cap exceeded')


def analyze_payload(payload, cfg):
    verify_payload(payload, cfg)
    counters = {name: Counter() for name in ('historical', 'current_text')}
    selections = []
    for work in payload['works']:
        shared = dict(canonical_title_key=work['canonical_title_key'])
        legacy = canonical_text.select_abstract(work['candidates'],
            cutoff=cfg['historical_cutoff_exclusive'], mode=canonical_text.LEGACY, **shared)
        variants = {}
        for name, cutoff in [('historical', cfg['historical_cutoff_exclusive']),
                             ('current_text', cfg['current_text_cutoff_exclusive'])]:
            chosen = canonical_text.select_abstract(work['candidates'], cutoff=cutoff, **shared)
            tally = counters[name]
            tally['works'] += 1
            tally['different_source_texts'] += chosen['different_abstract_values']
            tally['native_preferred'] += chosen['chosen_source'] == 'arxiv' and chosen['basis'] != 'original_source_order_fallback'
            tally['abstracts_changed_from_frozen_work'] += chosen['abstract_sha256'] != work['old_abstract_sha256']
            tally['legacy_choice_differs_from_frozen_work'] += legacy['abstract_sha256'] != work['old_abstract_sha256']
            tally['legacy_revision_not_verified_candidates'] += sum(
                item.get('revision_date_basis') == 'legacy_snapshot_revision_not_verified'
                for item in chosen['candidates'])
            tally['native_candidates_with_unknown_text_date'] += sum(
                item['source'] == 'arxiv' and item['historical_text_available'] is None
                for item in chosen['candidates'])
            variants[name] = {key: value for key, value in chosen.items() if key != 'abstract'}
        selections.append({'work_id': work['work_id'], 'canonical_title': work['canonical_title'],
                           'old_abstract_sha256': work['old_abstract_sha256'], 'variants': variants})
    # Known debugging examples cannot influence any preceding source choice.
    example_ids = set(cfg.get('detail_example_work_ids', []))
    examples = [row for row in selections if row['work_id'] in example_ids]
    per_source = Counter(row['source'] for work in payload['works'] for row in work['candidates'])
    return {'version': 'canonical-text-source-audit-v1', 'input_content_sha256': payload['content_sha256'],
            'protocol_sha256': content_sha256(cfg), 'policy': canonical_text.policy(),
            'counts': {name: dict(sorted(value.items())) for name, value in counters.items()},
            'source_candidate_counts': dict(sorted(per_source.items())),
            'debug_examples_not_accuracy_labels': examples,
            'new_signal_detection_or_precision_measured': False, 'completed_runs_rewritten': False,
            'limitations': ['Different literal abstracts are not automatically errors.',
                            'Current-text comparison does not establish historical novelty or growth.',
                            'Legacy mirror updated dates inferred from submission do not date the abstract.',
                            'This export traces stored records, not an independently re-fetched upstream API.']}


def write_new_json(path, value, cap, *, compact=False):
    data = (json.dumps(value, ensure_ascii=False, indent=None if compact else 2,
                       separators=(",", ":") if compact else None) + '\n').encode()
    if len(data) > cap:
        raise ValueError('Output resource cap exceeded')
    with path.open('xb') as stream:
        stream.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/canonical-text-audit-v1.json')
    parser.add_argument('--input', type=Path, help='Replay, without database imports or source downloads')
    parser.add_argument('--export', action='store_true', help='New read-only frozen database export')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.export == bool(args.input):
        parser.error('Choose exactly one of --export or --input')
    cfg = json.loads(args.config.read_text())
    if date.fromisoformat(cfg['historical_cutoff_exclusive']) >= date.fromisoformat(cfg['current_text_cutoff_exclusive']):
        raise ValueError('Invalid date comparison')
    if shutil.disk_usage(args.output.parent.resolve()).free < cfg['min_free_disk_reserve_bytes']:
        raise ValueError('Free disk reserve not met')
    if args.output.exists():
        raise ValueError('Output directory already exists; frozen artifacts are never overwritten')
    if args.input and args.input.stat().st_size > cfg['max_input_bytes']:
        raise ValueError('Input resource cap exceeded')
    started = time.perf_counter()
    payload = export_payload(cfg) if args.export else json.loads(args.input.read_text())
    report = analyze_payload(payload, cfg)
    args.output.mkdir()
    file_hash = (write_new_json(args.output / 'input.json', payload, cfg['max_input_bytes'], compact=True)
                 if args.export else hashlib.sha256(args.input.read_bytes()).hexdigest())
    result_hash = write_new_json(args.output / 'audit.json', report, cfg['max_input_bytes'])
    validation = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                  'input_path': str((args.output / 'input.json') if args.export else args.input),
                  'input_file_sha256': file_hash, 'audit_file_sha256': result_hash,
                  'elapsed_seconds': round(time.perf_counter() - started, 3),
                  'database_read_only': bool(args.export), 'offline_replay': not args.export,
                  'source_downloads': 0, 'data_changes': 0}
    write_new_json(args.output / 'validation.json', validation, cfg['max_input_bytes'])
    print(json.dumps({'output': str(args.output), 'counts': report['counts'],
                      'validation': validation}, ensure_ascii=False))


if __name__ == '__main__':
    main()
