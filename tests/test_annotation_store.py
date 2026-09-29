from __future__ import annotations

import copy
import os
import uuid

import pytest

psycopg = pytest.importorskip('psycopg')
if not os.environ.get('SAIA_DATABASE_URL'):
    pytest.skip('SAIA_DATABASE_URL не задан', allow_module_level=True)

from psycopg.types.json import Jsonb  # noqa: E402

from saia import annotation_store, db  # noqa: E402
from saia.annotation_protocol import current_packet, current_template  # noqa: E402


def completed(reviewer: str, weak_signal: str = 'uncertain') -> dict:
    value = copy.deepcopy(current_template())
    value['reviewer_id'] = reviewer
    value['independent_review_declared'] = True
    value['system_status_not_seen_declared'] = True
    for row in value['annotations']:
        row['answers'] = {name: ('uncertain' if name != 'weak_signal_at_cutoff'
                                 else weak_signal) for name in row['answers']}
        row['rationale'] = 'Synthetic integration fixture; not an expert assessment.'
        row['sources'] = []
    return value


def cleanup(reviewer_prefix: str) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        try:
            cur.execute('ALTER TABLE annotation_submission DISABLE TRIGGER annotation_submission_no_change')
            cur.execute('DELETE FROM annotation_submission WHERE reviewer_id LIKE %s',
                        (reviewer_prefix + '%',))
        finally:
            cur.execute('ALTER TABLE annotation_submission ENABLE TRIGGER annotation_submission_no_change')


def test_blinded_submission_store_is_append_only_idempotent_and_comparable():
    marker = 'synthetic-store-' + uuid.uuid4().hex
    cleanup(marker)
    try:
        operation = str(uuid.uuid4())
        left_raw = completed(marker + '-A', 'yes')
        first = annotation_store.record(left_raw, operation)
        replay = annotation_store.record(left_raw, operation)
        duplicate = annotation_store.record(left_raw, str(uuid.uuid4()))
        assert first['submission_id'] == replay['submission_id'] == duplicate['submission_id']
        assert first['replayed'] is False and replay['replayed'] is True
        assert duplicate['deduplicated_by_content'] is True
        assert first['calibration_eligible'] is False

        changed = copy.deepcopy(left_raw)
        changed['annotations'][0]['answers']['weak_signal_at_cutoff'] = 'no'
        with pytest.raises(ValueError, match='другой анкеты'):
            annotation_store.record(changed, operation)
        partial = copy.deepcopy(left_raw)
        partial['annotations'].pop()
        with pytest.raises(ValueError, match='только полные'):
            annotation_store.record(partial)

        right = annotation_store.record(completed(marker + '-B', 'no'), str(uuid.uuid4()))
        comparison = annotation_store.compare(first['submission_id'], right['submission_id'])
        assert comparison['consensus_created'] is False
        assert comparison['calibration_allowed'] is False
        assert comparison['adjudication_required'] is True
        assert len(comparison['disagreements']) == 24
        assert comparison['stored_submission_ids'] == [first['submission_id'], right['submission_id']]
        with pytest.raises(ValueError, match='две разные'):
            annotation_store.compare(first['submission_id'], first['submission_id'])

        saved = annotation_store.read(first['submission_id'])
        assert saved['validated_submission'] == first['validated_submission']
        history = annotation_store.history(500)
        assert {first['submission_id'], right['submission_id']} <= {
            row['submission_id'] for row in history['submissions']}

        with db.connect() as conn, conn.cursor() as cur:
            for sql in ('UPDATE annotation_submission SET reviewer_id=reviewer_id WHERE submission_id=%s',
                        'DELETE FROM annotation_submission WHERE submission_id=%s'):
                with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
                    with conn.transaction():
                        cur.execute(sql, (first['submission_id'],))
            packet = current_packet()
            with pytest.raises(psycopg.errors.RaiseException, match='must agree'):
                with conn.transaction():
                    cur.execute(
                        'INSERT INTO annotation_submission '
                        '(submission_id,package_id,packet_payload_sha256,reviewer_id,'
                        'submission_payload_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                        (str(uuid.uuid4()), packet['package_id'], packet['packet_payload_sha256'],
                         marker + '-invalid', '0' * 64, Jsonb({'complete': True})),
                    )
    finally:
        cleanup(marker)
