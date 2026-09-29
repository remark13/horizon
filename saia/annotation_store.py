"""Append-only PostgreSQL storage for validated blinded-review submissions."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from saia import db, hybrid
from saia.annotation_protocol import (compare_validated_submissions, current_packet,
                                      validate_submission,
                                      verify_validated_submission)


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except ValueError:
        raise ValueError('Ключ отправки должен быть UUID.') from None


def record(submission: dict, operation_id: str | None = None) -> dict:
    packet = current_packet()
    validated = validate_submission(packet, submission)
    if not validated['complete']:
        raise ValueError('В неизменяемую историю сохраняются только полные анкеты.')
    submission_id = _uuid(operation_id)
    expected = (packet['package_id'], packet['packet_payload_sha256'],
                validated['reviewer_id'], validated['submission_payload_sha256'], validated)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO annotation_submission '
            '(submission_id,package_id,packet_payload_sha256,reviewer_id,'
            'submission_payload_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT DO NOTHING RETURNING created_at',
            (submission_id, *expected[:4], Jsonb(validated)),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        deduplicated_by_content = False
        if inserted:
            created = inserted[0]
        else:
            cur.execute(
                'SELECT submission_id,package_id::text,packet_payload_sha256,reviewer_id,'
                'submission_payload_sha256,payload,created_at FROM annotation_submission '
                'WHERE submission_id=%s', (submission_id,),
            )
            old = cur.fetchone()
            if old is None:
                cur.execute(
                    'SELECT submission_id,package_id::text,packet_payload_sha256,reviewer_id,'
                    'submission_payload_sha256,payload,created_at FROM annotation_submission '
                    'WHERE package_id=%s AND lower(btrim(reviewer_id))=lower(btrim(%s)) '
                    'AND submission_payload_sha256=%s',
                    (packet['package_id'], validated['reviewer_id'],
                     validated['submission_payload_sha256']),
                )
                old = cur.fetchone()
                deduplicated_by_content = old is not None
            if old is None:
                raise ValueError('Конфликт отправки не удалось сопоставить с сохранённой анкетой.')
            if str(old[0]) == submission_id and tuple(old[1:6]) != expected:
                raise ValueError('Этот ключ уже использован для другой анкеты; история не изменена.')
            submission_id, created = str(old[0]), old[6]
            validated = verify_validated_submission(packet, old[5])
    return {
        'submission_id': submission_id,
        'package_id': packet['package_id'],
        'packet_payload_sha256': packet['packet_payload_sha256'],
        'reviewer_id': validated['reviewer_id'],
        'submission_payload_sha256': validated['submission_payload_sha256'],
        'created_at': created.isoformat(),
        'replayed': replayed,
        'deduplicated_by_content': deduplicated_by_content,
        'validated_submission': validated,
        'individual_opinion_not_gold': True,
        'calibration_eligible': False,
        'interpretation': ('Полная ослеплённая анкета сохранена append-only. '
                           'Она не меняет score/status и не является консенсусом.'),
    }


def read(submission_id: str) -> dict:
    identifier = _uuid(submission_id)
    packet = current_packet()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT package_id::text,packet_payload_sha256,reviewer_id,'
            'submission_payload_sha256,payload,created_at FROM annotation_submission '
            'WHERE submission_id=%s', (identifier,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError('Сохранённая экспертная анкета не найдена.')
    if row[0] != packet['package_id'] or row[1] != packet['packet_payload_sha256']:
        raise ValueError('Анкета относится к другой версии экспертного пакета.')
    payload = verify_validated_submission(packet, row[4])
    if (row[2], row[3]) != (payload['reviewer_id'], payload['submission_payload_sha256']):
        raise ValueError('Колонки истории не совпали с проверенной анкетой.')
    return {'submission_id': identifier, 'package_id': row[0],
            'packet_payload_sha256': row[1], 'reviewer_id': row[2],
            'submission_payload_sha256': row[3], 'created_at': row[5].isoformat(),
            'validated_submission': payload, 'individual_opinion_not_gold': True}


def history(limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError('Лимит истории должен быть от 1 до 500.')
    packet = current_packet()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM annotation_submission WHERE package_id=%s',
                    (packet['package_id'],))
        total = cur.fetchone()[0]
        cur.execute(
            'SELECT submission_id,reviewer_id,submission_payload_sha256,created_at '
            'FROM annotation_submission WHERE package_id=%s '
            'ORDER BY created_at DESC,submission_id DESC LIMIT %s',
            (packet['package_id'], limit),
        )
        rows = [{'submission_id': str(identifier), 'reviewer_id': reviewer,
                 'submission_payload_sha256': checksum, 'created_at': created.isoformat()}
                for identifier, reviewer, checksum, created in cur.fetchall()]
    return {'package_id': packet['package_id'],
            'packet_payload_sha256': packet['packet_payload_sha256'],
            'total': total, 'returned': len(rows), 'submissions': rows,
            'identity': 'self_declared_not_authenticated',
            'interpretation': ('История отдельных полных анкет. '
                               'Число записей не означает консенсус или качество экспертов.')}


def compare(left_submission_id: str, right_submission_id: str) -> dict:
    if left_submission_id == right_submission_id:
        raise ValueError('Для сравнения нужны две разные сохранённые анкеты.')
    packet = current_packet()
    left, right = read(left_submission_id), read(right_submission_id)
    result = compare_validated_submissions(
        packet, left['validated_submission'], right['validated_submission'])
    result.pop('comparison_payload_sha256')
    result['stored_submission_ids'] = [left['submission_id'], right['submission_id']]
    result['comparison_payload_sha256'] = hybrid.digest(result)
    return result
