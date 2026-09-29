"""Append-only scout requests; not expert validation and not external delivery."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.candidates import export_cards
from saia import scout_public_signals


def create(mission_id: str, score_run_id: int, candidate_ids: list[int],
           requested_by: str, recipient: str, note: str = '',
           operation_id: str | None = None, *, public_signal_ids: list[str] | None = None) -> dict:
    public_signal_ids = [] if public_signal_ids is None else public_signal_ids
    if (not isinstance(candidate_ids, list) or not isinstance(public_signal_ids, list)
            or not 1 <= len(candidate_ids) + len(public_signal_ids) <= 15):
        raise ValueError('Выберите от 1 до 15 разных сигналов.')
    if any(not isinstance(item, int) or isinstance(item, bool) for item in candidate_ids):
        raise ValueError('Идентификаторы кандидатов должны быть целыми числами.')
    if len(set(candidate_ids)) != len(candidate_ids) or any(not isinstance(item, str) for item in public_signal_ids):
        raise ValueError('Выберите разные сигналы с корректными идентификаторами.')
    if public_signal_ids:
        scout_public_signals.parse_ids(','.join(public_signal_ids), maximum=15)
    actor, target, comment = requested_by.strip(), recipient.strip(), note.strip()
    if not 1 <= len(actor) <= 120 or not 1 <= len(target) <= 120 or len(comment) > 2000:
        raise ValueError('Укажите скаута и получателя (до 120 символов), комментарий — до 2000.')
    try:
        request_id = str(uuid.UUID(operation_id)) if operation_id else str(uuid.uuid4())
    except (ValueError, TypeError):
        raise ValueError('Некорректный ключ запроса.') from None
    packet = export_cards(mission_id, score_run_id)
    found = {card['candidate_id']: card for card in packet['cards']}
    if any(item not in found for item in candidate_ids):
        raise ValueError('Некоторые кандидаты отсутствуют в выбранном прогоне.')
    snapshot = [{
        'candidate_id': item,
        'label': found[item]['label'],
        'composition_sha256': found[item]['composition_sha256'],
    } for item in candidate_ids]
    if public_signal_ids:
        _references, published = scout_public_signals.select(packet, public_signal_ids)
        snapshot.extend({'item_kind': 'public_signal', 'public_signal_id': row['id'],
                         'label': row['title'], 'reference_content_sha256': row['reference_content_sha256'],
                         'source_badge': scout_public_signals.BADGE, 'public_reference': row} for row in published)
    expected = (mission_id, packet['score_run_id'], candidate_ids, snapshot, actor, target, comment)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO expert_validation_request '
            '(request_id,mission_id,score_run_id,candidate_ids,candidate_snapshot,requested_by,recipient,note) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (request_id) DO NOTHING '
            'RETURNING created_at',
            (request_id, mission_id, packet['score_run_id'], Jsonb(candidate_ids),
             Jsonb(snapshot), actor, target, comment),
        )
        inserted = cur.fetchone()
        if inserted is None:
            cur.execute('SELECT mission_id,score_run_id,candidate_ids,candidate_snapshot,'
                        'requested_by,recipient,note,created_at '
                        'FROM expert_validation_request WHERE request_id=%s', (request_id,))
            row = cur.fetchone()
            if row is None or tuple(row[:7]) != expected:
                raise ValueError('Ключ запроса уже использован для другой отправки.')
            created_at = row[7]
        else:
            created_at = inserted[0]
    return {
        'request_id': request_id, 'mission_id': mission_id,
        'score_run_id': packet['score_run_id'], 'candidate_ids': candidate_ids,
        'public_signal_ids': public_signal_ids,
        'candidate_snapshot': snapshot, 'requested_by': actor, 'recipient': target,
        'note': comment, 'created_at': created_at.isoformat(),
        'replayed': inserted is None, 'status': 'awaiting_expert_review',
        'delivery': 'internal_queue_only',
        'interpretation': 'Заявка сохранена во внутренней очереди; мнение эксперта ещё не получено.',
    }


def history(limit: int = 50) -> dict:
    if not 1 <= limit <= 200:
        raise ValueError('Лимит должен быть от 1 до 200.')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT request_id,mission_id,score_run_id,candidate_ids,candidate_snapshot,'
                    'requested_by,recipient,note,created_at '
                    'FROM expert_validation_request ORDER BY created_at DESC,request_id DESC LIMIT %s',
                    (limit,))
        rows = cur.fetchall()
        all_hashes = list({item['composition_sha256'] for row in rows
                           for item in row[4] if item.get('composition_sha256')})
        public_hashes = list({item['reference_content_sha256'] for row in rows
                              for item in row[4] if item.get('reference_content_sha256')})
        latest_review_by_hash: dict[str, object] = {}
        if all_hashes:
            cur.execute('SELECT composition_sha256, MAX(created_at) FROM score_candidate_review '
                        'WHERE composition_sha256 = ANY(%s) GROUP BY composition_sha256',
                        (all_hashes,))
            latest_review_by_hash = dict(cur.fetchall())
        latest_public_review_by_request = {}
        if public_hashes:
            cur.execute('SELECT request_id::text, reference_content_sha256, MAX(created_at) FROM public_signal_review '
                        'WHERE request_id = ANY(%s::uuid[]) AND reference_content_sha256 = ANY(%s) '
                        'GROUP BY request_id,reference_content_sha256',
                        ([str(row[0]) for row in rows], public_hashes))
            latest_public_review_by_request = {(request_id, reference_hash): created
                                               for request_id, reference_hash, created in cur.fetchall()}
    result = []
    for row in rows:
        latest = [latest_public_review_by_request.get((str(row[0]), item['reference_content_sha256']))
                  if item.get('item_kind') == 'public_signal' else
                  latest_review_by_hash.get(item['composition_sha256']) for item in row[4]]
        reviewed = sum(timestamp is not None and timestamp >= row[8] for timestamp in latest)
        status = ('reviewed' if reviewed == len(row[4]) else
                  'partially_reviewed' if reviewed else 'awaiting_expert_review')
        result.append({
            'request_id': str(row[0]), 'mission_id': row[1], 'score_run_id': row[2],
            'candidate_ids': row[3], 'candidate_snapshot': row[4],
            'public_signal_ids': [item['public_signal_id'] for item in row[4] if item.get('item_kind') == 'public_signal'],
            'requested_by': row[5], 'recipient': row[6], 'note': row[7],
            'created_at': row[8].isoformat(), 'status': status,
            'reviewed_count': reviewed, 'total_count': len(row[4]),
            'delivery': 'internal_queue_only',
        })
    return {'requests': result}
