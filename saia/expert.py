"""Append-only opinions, not labels of truth, consensus, or automatic gates."""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from saia import db, hybrid

CHOICES = {'needs_review', 'research_line_supported', 'noise', 'possible_duplicate', 'insufficient_evidence'}


def validate(decision: str, reviewed_by: str, rationale: str, sources: list[str], *, choices: set[str] | None = None) -> tuple:
    actor, reason = reviewed_by.strip(), rationale.strip()
    if decision not in (CHOICES if choices is None else choices) or not 1 <= len(actor) <= 120 or not 20 <= len(reason) <= 5000:
        raise ValueError('Укажите допустимое решение, имя автора и объяснение от 20 до 5000 знаков.')
    if len(sources) > 10:
        raise ValueError('Не более 10 ссылок в одном мнении.')
    clean = []
    for source in sources:
        link = source.strip()
        parsed = urlsplit(link)
        if (len(link) > 2048 or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password or any(ch.isspace() for ch in link)):
            raise ValueError('Источники должны быть HTTPS-ссылками без учётных данных и пробелов.')
        if link not in clean:
            clean.append(link)
    return actor, reason, clean


def _candidate(snapshot: dict, candidate_id: str) -> tuple[dict, str]:
    matches = [candidate for candidate in snapshot['candidates']
               if candidate['candidate_id'] == candidate_id]
    if len(matches) != 1:
        raise ValueError('Кандидат отсутствует в выбранном снимке.')
    candidate = matches[0]
    work_ids = list(candidate.get('work_ids') or [])
    if not work_ids or len(work_ids) != len(set(work_ids)):
        raise ValueError('Кандидат не имеет корректного состава публикаций.')
    expected_composition = hybrid.digest(sorted(work_ids))
    composition = candidate.get('composition_sha256') or expected_composition
    if not isinstance(composition, str) or len(composition) != 64:
        raise ValueError('Стабильная идентичность состава повреждена.')
    if composition != expected_composition:
        raise ValueError('Стабильная идентичность не соответствует составу публикаций.')
    return candidate, composition


def record(snapshot_id: str, candidate_id: str, decision: str,
           reviewed_by: str, rationale: str, sources: list[str], operation_id: str | None = None) -> dict:
    actor, reason, links = validate(decision, reviewed_by, rationale, sources)
    try:
        identifier = str(uuid.UUID(str(operation_id))) if operation_id is not None else str(uuid.uuid4())
    except ValueError:
        raise ValueError('Ключ повторной отправки должен быть UUID.') from None
    snapshot = hybrid.read(snapshot_id)
    _, composition = _candidate(snapshot, candidate_id)
    canonical_snapshot_id = str(uuid.UUID(snapshot_id))
    expected = (canonical_snapshot_id, candidate_id, composition, hybrid.digest(snapshot),
                actor, decision, reason, links)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO expert_review (review_id, snapshot_id, candidate_id, composition_sha256, '
                    'snapshot_content_sha256, reviewed_by, decision, rationale, sources) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                    'ON CONFLICT (review_id) DO NOTHING RETURNING created_at',
                    (identifier, *expected[:3], expected[3], actor, decision, reason, Jsonb(links)))
        inserted = cur.fetchone()
        replayed = inserted is None
        if replayed:
            cur.execute('SELECT snapshot_id,candidate_id,composition_sha256,snapshot_content_sha256,'
                        'reviewed_by,decision,rationale,sources,created_at '
                        'FROM expert_review WHERE review_id=%s', (identifier,))
            old = cur.fetchone()
            if old is None or (str(old[0]), *old[1:8]) != expected:
                raise ValueError('Этот ключ уже использован для другого мнения; прежняя запись не изменена.')
            created = old[8]
        else:
            created = inserted[0]
    return {'review_id': identifier, 'snapshot_id': canonical_snapshot_id, 'candidate_id': candidate_id,
            'composition_sha256': composition,
            'created_at': created.isoformat(), 'decision': decision,
            'replayed': replayed, 'review_policy_version': 'expert-review-0.4.28-composition',
            'interpretation': 'Сохранено отдельное мнение, не автоматический статус или истинная метка.',
            'identity': 'self_declared_not_authenticated'}


def history(snapshot_id: str, candidate_id: str, limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError('Лимит истории должен быть от 1 до 500.')
    snapshot = hybrid.read(snapshot_id)
    _, composition = _candidate(snapshot, candidate_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM expert_review WHERE composition_sha256=%s',
                    (composition,))
        total = cur.fetchone()[0]
        cur.execute('SELECT review_id,snapshot_id,candidate_id,snapshot_content_sha256,'
                    'reviewed_by,decision,rationale,sources,created_at '
                    'FROM expert_review WHERE composition_sha256=%s '
                    'ORDER BY created_at DESC,review_id DESC LIMIT %s', (composition, limit))
        opinions = [{
            'review_id': str(identifier), 'source_snapshot_id': str(source_snapshot),
            'source_candidate_id': source_candidate, 'snapshot_content_sha256': snapshot_hash,
            'reviewed_by': actor, 'decision': decision, 'rationale': reason,
            'sources': links, 'created_at': created.isoformat(),
            'matches_current_composition': True,
        } for (identifier, source_snapshot, source_candidate, snapshot_hash, actor,
               decision, reason, links, created) in cur.fetchall()]
    return {'snapshot_id': snapshot_id, 'snapshot_content_sha256': hybrid.digest(snapshot),
            'candidate_id': candidate_id, 'composition_sha256': composition,
            'history_scope': 'exact_publication_composition_across_snapshots',
            'total': total, 'returned': len(opinions), 'reviews': opinions,
            'identity': 'self_declared_not_authenticated',
            'interpretation': 'История отдельных мнений; консенсус и качество экспертов не установлены.'}
