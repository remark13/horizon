"""Read-only evidence export for reviewing explicitly selected frozen candidates.

This is an analyst tool, not a detector or gold-label generator. No candidate
names or evaluation IDs are used to change generation, ranking or gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

from saia import db
from saia.hybrid import digest


def build_packet(snapshot: dict, candidate_ids: list[str], works: list[dict]) -> dict:
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError('Select distinct candidate IDs explicitly.')
    by_candidate = {c['candidate_id']: c for c in snapshot['candidates']}
    if not set(candidate_ids) <= by_candidate.keys():
        raise ValueError('A candidate does not belong to this frozen snapshot.')
    requested = {i for cid in candidate_ids for i in by_candidate[cid]['work_ids']}
    by_work = {w['work_id']: w for w in works}
    if len(by_work) != len(works) or set(by_work) != requested:
        raise ValueError('Evidence must cover exactly the requested canonical works.')
    provenance = snapshot['provenance']
    if any(w['run_id'] != provenance['normalize_run_id'] or
           w['mission_id'] != provenance['mission_id'] for w in works):
        raise ValueError('Work evidence belongs to another frozen input.')
    cases = []
    for cid in candidate_ids:
        c = by_candidate[cid]
        roster = sorted((by_work[i] for i in c['work_ids']),
                        key=lambda w: (w['effective_date'], w['work_id']))
        selected = sorted(set([0, 1, len(roster)//2 - 1, len(roster)//2,
                               len(roster)-2, len(roster)-1]))
        selected = [i for i in selected if 0 <= i < len(roster)]
        cases.append({
            'candidate_id': cid, 'label': c['label'], 'channels': c['channels'],
            'work_ids': c['work_ids'], 'document_support': len(roster),
            'publication_series': c['publication_series'],
            'first_found_in_corpus': c['first_observed_in_corpus'],
            'selection_rule': 'earliest_two_median_two_latest_two_by_effective_date',
            'suggested_reading_work_ids': [roster[i]['work_id'] for i in selected],
            'works': roster, 'analytical_label': None, 'independent_gold': False,
            'full_content_review_completed': False,
            'limitations': ['Suggested reading is a deterministic sample, not proof that every paper was reviewed.',
                           'First found in this corpus is not the worldwide first publication.',
                           'Current normalized text is historical reconstruction, not necessarily the text available at the cutoff.'],
        })
    return {'version': 'candidate-evidence-audit-0.4.4',
            'snapshot_id': snapshot['snapshot_id'], 'snapshot_payload_sha256': digest(snapshot),
            'provenance': provenance, 'case_count': len(cases),
            'unique_works': len(requested), 'cases': cases,
            'accuracy_evaluated': False, 'detector_modified_by_this_export': False}


def export(snapshot_id: str, candidate_ids: list[str]) -> dict:
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT payload, content_sha256 FROM hybrid_snapshot WHERE snapshot_id=%s',
                        (snapshot_id,))
            stored = cur.fetchone()
            if not stored or digest(stored['payload']) != stored['content_sha256']:
                raise ValueError('Frozen snapshot missing or its payload checksum is invalid.')
            snapshot = stored['payload']
            by_candidate = {c['candidate_id']: c for c in snapshot['candidates']}
            if not candidate_ids or not set(candidate_ids) <= by_candidate.keys():
                raise ValueError('Select candidates from this frozen snapshot.')
            ids = sorted({i for cid in candidate_ids for i in by_candidate[cid]['work_ids']})
            cur.execute('SELECT work_id, mission_id, run_id, canonical_title AS title, abstract, '
                        'type, language, publication_date, effective_date, date_is_imprecise '
                        'FROM work WHERE work_id=ANY(%s) ORDER BY work_id', (ids,))
            works = cur.fetchall()
            by_work = {w['work_id']: w for w in works}
            for w in works:
                w['publication_date'] = str(w['publication_date']) if w['publication_date'] else None
                w['effective_date'] = str(w['effective_date'])
                w['text_sha256'] = hashlib.sha256(
                    (w['title'] + '\n' + (w['abstract'] or '')).encode()).hexdigest()
                w['identifiers'], w['source_records'] = [], []
            cur.execute('SELECT work_id, kind, value FROM identifier WHERE work_id=ANY(%s) '
                        'ORDER BY work_id, kind, value', (ids,))
            for r in cur.fetchall():
                by_work[r['work_id']]['identifiers'].append({'kind': r['kind'], 'value': r['value']})
            cur.execute('SELECT work_id, source, source_record_id, raw_record_id, version_kind, '
                        'version_date FROM work_version WHERE work_id=ANY(%s) '
                        'ORDER BY work_id, source, source_record_id', (ids,))
            for r in cur.fetchall():
                wid = r.pop('work_id')
                r['version_date'] = str(r['version_date']) if r['version_date'] else None
                by_work[wid]['source_records'].append(r)
            result = build_packet(snapshot, candidate_ids, works)
    result['exported_at'] = datetime.now(timezone.utc).isoformat()
    result['exporter_code_bytes_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result['report_payload_sha256'] = digest(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot-id', required=True)
    parser.add_argument('--candidate', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Exclusive creation protects prior evidence exports; the database is read-only.
    if args.output.exists():
        parser.error('Output exists; select a new versioned path.')
    result = export(args.snapshot_id, args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'output': str(args.output), 'cases': result['case_count'],
                      'unique_works': result['unique_works'],
                      'report_payload_sha256': result['report_payload_sha256']}))


if __name__ == '__main__':
    main()
