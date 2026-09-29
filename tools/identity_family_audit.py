"""Read-only metadata evidence for an explicitly supplied identity review queue."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

from saia import db
from saia.hybrid import digest


def export(checkpoint_path: Path) -> dict:
    checkpoint_bytes = checkpoint_path.read_bytes()
    checkpoint = json.loads(checkpoint_bytes)
    queue = checkpoint['multiple_arxiv_id_work_review_queue']
    ids = [item['work_id'] for item in queue]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Review queue must contain distinct work IDs.')
    expected_run = checkpoint['embedding']['normalize_run_id']
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT work_id, run_id, mission_id, canonical_title, effective_date '
                        'FROM work WHERE work_id=ANY(%s) ORDER BY work_id', (ids,))
            works = cur.fetchall()
            if set(w['work_id'] for w in works) != set(ids) or any(w['run_id'] != expected_run for w in works):
                raise ValueError('Review works do not match the checkpoint generation.')
            by_work = {w['work_id']: w for w in works}
            for w in works:
                w['effective_date'] = str(w['effective_date'])
                w['identifiers'], w['deposits'] = [], []
            cur.execute('SELECT work_id, kind, value FROM identifier WHERE work_id=ANY(%s) '
                        'ORDER BY work_id, kind, value', (ids,))
            for item in cur.fetchall():
                by_work[item.pop('work_id')]['identifiers'].append(item)
            cur.execute('SELECT v.work_id, v.source_record_id, v.raw_record_id, r.content_sha256, '
                        'r.payload, r.snapshot_id FROM work_version v JOIN raw_record r '
                        'ON r.raw_record_id=v.raw_record_id WHERE v.work_id=ANY(%s) AND v.source=%s '
                        'ORDER BY v.work_id, v.source_record_id, v.raw_record_id', (ids, 'arxiv'))
            for item in cur.fetchall():
                by_work[item.pop('work_id')]['deposits'].append(item)
    for entry in queue:
        work = by_work[entry['work_id']]
        actual = {i['value'] for i in work['identifiers'] if i['kind'] == 'arxiv'}
        deposits = {i['source_record_id'] for i in work['deposits']}
        if actual != set(entry['arxiv_ids']) or deposits != actual:
            raise ValueError('Identity roster differs from the frozen review queue.')
        work['previous_quality_decision'] = entry['quality_decision']
        work['analyst_identity_decision'] = None
    report = {'version': 'identity-family-metadata-audit-0.4.4',
              'exported_at': datetime.now(timezone.utc).isoformat(),
              'checkpoint_file_bytes_sha256': hashlib.sha256(checkpoint_bytes).hexdigest(),
              'exporter_code_bytes_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'normalize_run_id': expected_run, 'family_count': len(works),
              'deposit_count': sum(len(w['deposits']) for w in works), 'families': works,
              'database_mutated': False, 'scientific_labels_assigned': False,
              'limitations': ['Metadata triage cannot establish that a research finding is novel or valid.',
                              'Shared DOI is evidence to check, not proof that deposits are one publication.',
                              'Current metadata cannot date historical withdrawals or recover earlier texts.']}
    report['report_payload_sha256'] = digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserved output exists; select a new versioned path.')
    report = export(args.checkpoint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'family_count': report['family_count'], 'deposit_count': report['deposit_count'],
                      'report_payload_sha256': report['report_payload_sha256']}))


if __name__ == '__main__':
    main()
