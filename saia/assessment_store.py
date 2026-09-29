"""Versioned, immutable assessments of exact hybrid compositions.

Only a replay-verified report is imported. Reading never recalculates scores
and no semantic topic's score is copied onto a lexical/hybrid composition.
"""
from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from pathlib import Path

from psycopg.types.json import Jsonb

from saia import composition_assessment, db, hybrid, methodology


def validate_report(snapshot: dict, report: dict) -> None:
    try:
        digest = report['assessment_content_sha256']
        body = {k: v for k, v in report.items() if k != 'assessment_content_sha256'}
        if hybrid.digest(body) != digest:
            raise ValueError('Отпечаток оценки не совпал.')
        if (report['snapshot_id'] != snapshot['snapshot_id'] or
                report['snapshot_content_sha256'] != hybrid.digest(snapshot) or
                report['provenance'] != snapshot['provenance'] or
                report['input_text_hash'] != snapshot['input_text_hash'] or
                report['as_of_date'] != snapshot['as_of_date']):
            raise ValueError('Оценка относится к другому снимку или входу.')
        for field, hash_field in [('effective_policy', 'policy_hash'),
                                  ('effective_methodology', 'methodology_hash'),
                                  ('effective_measurement_policy', 'measurement_policy_hash')]:
            actual = (methodology.from_mapping(report[field]).config_hash if field == 'effective_methodology'
                      else hybrid.digest(report[field]))
            if actual != report[hash_field]:
                raise ValueError('Паспорт оценки изменился.')
        expected = {c['candidate_id']: c for c in snapshot['candidates']}
        observed = {c['candidate_id']: c for c in report['candidates']}
        if len(observed) != len(report['candidates']) or set(observed) != set(expected):
            raise ValueError('Список кандидатов оценки не совпал со снимком.')
        for cid, row in observed.items():
            if (row['work_ids'] != expected[cid]['work_ids'] or
                    row['composition_sha256'] != hybrid.digest(sorted(expected[cid]['work_ids'])) or
                    row['channels'] != expected[cid]['channels']):
                raise ValueError('Состав или канал кандидата оценки изменился.')
            if row['assessment']['status'] not in {'candidate', 'watch', 'forming', 'widespread', 'mature'}:
                raise ValueError('Неизвестный статус оценки.')
        if dict(Counter(r['assessment']['status'] for r in observed.values())) != report['counts']:
            raise ValueError('Свод статусов не совпал с карточками.')
    except (KeyError, TypeError) as error:
        raise ValueError('Неполный или некорректный паспорт оценки.') from error


def save(report: dict) -> dict:
    snapshot = hybrid.read(report.get('snapshot_id'))
    validate_report(snapshot, report)
    # A caller cannot legitimize fabricated scores merely by recomputing SHA.
    composition_assessment.replay(report)
    new_id = str(uuid.uuid4())
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO composition_assessment_snapshot '
                    '(assessment_id, snapshot_id, snapshot_content_sha256, payload, content_sha256, policy_version, policy_hash) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id,content_sha256) DO NOTHING RETURNING assessment_id',
                    (new_id, report['snapshot_id'], report['snapshot_content_sha256'], Jsonb(report),
                     report['assessment_content_sha256'], report['version'], report['policy_hash']))
        inserted = cur.fetchone()
        cur.execute('SELECT assessment_id, created_at FROM composition_assessment_snapshot '
                    'WHERE snapshot_id=%s AND content_sha256=%s',
                    (report['snapshot_id'], report['assessment_content_sha256']))
        identifier, created = cur.fetchone()
    return {'assessment_id': str(identifier), 'snapshot_id': report['snapshot_id'],
            'created_at': created.isoformat(), 'replayed': inserted is None,
            'content_sha256': report['assessment_content_sha256']}


def read(assessment_id: str) -> dict:
    try:
        uuid.UUID(assessment_id)
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Некорректный номер сохранённой оценки.') from None
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT snapshot_id,payload,content_sha256,created_at '
                    'FROM composition_assessment_snapshot WHERE assessment_id=%s', (assessment_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError('Сохранённая оценка не найдена.')
    snapshot_id, report, sha, created = row
    if sha != report.get('assessment_content_sha256'):
        raise ValueError('Отпечаток сохранённой оценки изменился.')
    validate_report(hybrid.read(str(snapshot_id)), report)
    return {'assessment_id': assessment_id, 'snapshot_id': str(snapshot_id),
            'created_at': created.isoformat(), 'report': report}


def history(snapshot_id: str, limit: int = 50) -> dict:
    hybrid.read(snapshot_id)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError('Размер истории должен быть от 1 до 200.')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM composition_assessment_snapshot WHERE snapshot_id=%s', (snapshot_id,))
        total = cur.fetchone()[0]
        cur.execute('SELECT assessment_id,created_at,policy_version,policy_hash,content_sha256,payload->%s '
                    'FROM composition_assessment_snapshot WHERE snapshot_id=%s '
                    'ORDER BY created_at DESC,assessment_id DESC LIMIT %s', ('counts', snapshot_id, limit))
        rows = cur.fetchall()
    return {'snapshot_id': snapshot_id, 'total': total, 'returned': len(rows),
            'assessments': [{'assessment_id': str(i), 'created_at': t.isoformat(), 'version': v,
                             'policy_hash': p, 'content_sha256': h, 'counts': counts}
                            for i, t, v, p, h, counts in rows],
            'interpretation': 'Версии оценки точного состава; время сохранения не дата исторического обнаружения.'}


def main():
    parser = argparse.ArgumentParser(description='Сохранить проверенную оценку без изменения снимка и мнений')
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    print(json.dumps(save(json.loads(args.report.read_text())), ensure_ascii=False))


if __name__ == '__main__':
    main()
