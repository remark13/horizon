"""Append-only analyst opinions on immutable score candidates."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.candidates import export_cards
from saia.expert import validate
from saia.hybrid import digest


def _card(mission_id: str, candidate_id: int,
          score_run_id: int | None = None) -> tuple[dict, dict]:
    packet = export_cards(mission_id, score_run_id)
    matches = [card for card in packet["cards"] if card["candidate_id"] == candidate_id]
    if len(matches) != 1:
        raise ValueError("Кандидат отсутствует в выбранном завершённом score-прогоне.")
    return packet, matches[0]


def record(mission_id: str, candidate_id: int, decision: str, reviewed_by: str,
           rationale: str, sources: list[str], score_run_id: int | None = None,
           operation_id: str | None = None) -> dict:
    actor, reason, links = validate(decision, reviewed_by, rationale, sources)
    try:
        review_id = str(uuid.UUID(str(operation_id))) if operation_id else str(uuid.uuid4())
    except ValueError:
        raise ValueError("Ключ повторной отправки должен быть UUID.") from None
    packet, card = _card(mission_id, candidate_id, score_run_id)
    run_id = packet["score_run_id"]
    card_hash = digest(card)
    composition = card.get("composition_sha256")
    if not isinstance(composition, str) or len(composition) != 64:
        raise ValueError("У score-кандидата нет стабильной идентичности состава.")
    expected = (run_id, candidate_id, composition, card_hash,
                actor, decision, reason, links)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO score_candidate_review "
            "(review_id,score_run_id,candidate_id,composition_sha256,"
            "candidate_content_sha256,reviewed_by,decision,rationale,sources) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (review_id) DO NOTHING RETURNING created_at",
            (review_id, run_id, candidate_id, composition, card_hash,
             actor, decision, reason, Jsonb(links)),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        if replayed:
            cur.execute(
                "SELECT score_run_id,candidate_id,composition_sha256,candidate_content_sha256,"
                "reviewed_by,decision,rationale,sources,created_at "
                "FROM score_candidate_review WHERE review_id=%s", (review_id,),
            )
            old = cur.fetchone()
            if old is None or (*old[:7], old[7]) != expected:
                raise ValueError("Этот ключ уже использован для другого мнения; прежняя запись не изменена.")
            created = old[8]
        else:
            created = inserted[0]
    return {
        "review_id": review_id,
        "mission_id": mission_id,
        "score_run_id": run_id,
        "candidate_id": candidate_id,
        "composition_sha256": composition,
        "candidate_content_sha256": card_hash,
        "created_at": created.isoformat(),
        "decision": decision,
        "replayed": replayed,
        "review_policy_version": "score-candidate-review-0.4.28-composition",
        "interpretation": "Сохранено отдельное мнение; автоматический статус и метрики не изменены.",
        "identity": "self_declared_not_authenticated",
    }


def history(mission_id: str, candidate_id: int, score_run_id: int | None = None,
            limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Лимит истории должен быть от 1 до 500.")
    packet, card = _card(mission_id, candidate_id, score_run_id)
    run_id, card_hash = packet["score_run_id"], digest(card)
    composition = card.get("composition_sha256")
    if not isinstance(composition, str) or len(composition) != 64:
        raise ValueError("У score-кандидата нет стабильной идентичности состава.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM score_candidate_review WHERE composition_sha256=%s",
            (composition,),
        )
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT review_id,score_run_id,candidate_id,reviewed_by,decision,rationale,"
            "sources,created_at,candidate_content_sha256 "
            "FROM score_candidate_review WHERE composition_sha256=%s "
            "ORDER BY created_at DESC,review_id DESC LIMIT %s", (composition, limit),
        )
        reviews = [{
            "review_id": str(identifier), "source_score_run_id": source_run,
            "source_candidate_id": source_candidate,
            "reviewed_by": actor, "decision": decision,
            "rationale": reason, "sources": sources, "created_at": created.isoformat(),
            "candidate_content_sha256": saved_hash,
            "matches_current_card": saved_hash == card_hash,
            "matches_current_composition": True,
        } for (identifier, source_run, source_candidate, actor, decision, reason,
               sources, created, saved_hash) in cur.fetchall()]
    return {
        "mission_id": mission_id,
        "score_run_id": run_id,
        "candidate_id": candidate_id,
        "composition_sha256": composition,
        "candidate_content_sha256": card_hash,
        "history_scope": "exact_publication_composition_across_score_runs",
        "total": total,
        "returned": len(reviews),
        "reviews": reviews,
        "identity": "self_declared_not_authenticated",
        "interpretation": "История отдельных мнений; консенсус и квалификация экспертов не установлены.",
    }
