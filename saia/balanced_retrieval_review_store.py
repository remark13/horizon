"""Append-only storage for reviews of one immutable balanced discovery job."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.balanced_retrieval_review import from_job_id
from saia.query_expansion import digest
from saia.retrieval_relevance_review import (
    compare_validated_submissions,
    validate_submission,
    verify_validated_submission,
)


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Ключ retrieval-операции должен быть UUID.") from None


def record(job_id: str, submission: dict, operation_id: str | None = None) -> dict:
    packet, _template = from_job_id(job_id)
    validated = validate_submission(packet, submission)
    if not validated["complete"]:
        raise ValueError("В историю сохраняются только полные retrieval-анкеты.")
    submission_id = _uuid(operation_id)
    expected = (
        packet["package_id"], packet["packet_payload_sha256"],
        validated["reviewer_id"], validated["submission_payload_sha256"], validated,
    )
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_review_submission "
            "(submission_id,package_id,packet_payload_sha256,reviewer_id,"
            "submission_payload_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING created_at",
            (submission_id, *expected[:4], Jsonb(validated)),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        deduplicated_by_content = False
        if inserted:
            created = inserted[0]
        else:
            cur.execute(
                "SELECT submission_id,package_id::text,packet_payload_sha256,reviewer_id,"
                "submission_payload_sha256,payload,created_at "
                "FROM retrieval_review_submission WHERE submission_id=%s",
                (submission_id,),
            )
            old = cur.fetchone()
            if old is None:
                cur.execute(
                    "SELECT submission_id,package_id::text,packet_payload_sha256,reviewer_id,"
                    "submission_payload_sha256,payload,created_at "
                    "FROM retrieval_review_submission "
                    "WHERE package_id=%s AND lower(btrim(reviewer_id))=lower(btrim(%s)) "
                    "AND submission_payload_sha256=%s",
                    (packet["package_id"], validated["reviewer_id"],
                     validated["submission_payload_sha256"]),
                )
                old = cur.fetchone()
                deduplicated_by_content = old is not None
            if old is None:
                raise ValueError("Конфликт retrieval-отправки не удалось сопоставить.")
            if str(old[0]) == submission_id and tuple(old[1:6]) != expected:
                raise ValueError("Этот ключ уже использован для другой retrieval-анкеты.")
            submission_id, created = str(old[0]), old[6]
            validated = verify_validated_submission(packet, old[5])
    return {
        "submission_id": submission_id,
        "source_job_id": str(job_id),
        "package_id": packet["package_id"],
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewer_id": validated["reviewer_id"],
        "submission_payload_sha256": validated["submission_payload_sha256"],
        "created_at": created.isoformat(),
        "replayed": replayed,
        "deduplicated_by_content": deduplicated_by_content,
        "validated_submission": validated,
        "individual_opinion_not_gold": True,
        "precision_available": False,
        "production_change_allowed": False,
    }


def read(job_id: str, submission_id: str) -> dict:
    identifier = _uuid(submission_id)
    packet, _template = from_job_id(job_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT package_id::text,packet_payload_sha256,reviewer_id,"
            "submission_payload_sha256,payload,created_at "
            "FROM retrieval_review_submission WHERE submission_id=%s",
            (identifier,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("Сохранённая retrieval-анкета не найдена.")
    if row[0] != packet["package_id"] or row[1] != packet["packet_payload_sha256"]:
        raise ValueError("Retrieval-анкета относится к другой задаче или версии результата.")
    payload = verify_validated_submission(packet, row[4])
    if (row[2], row[3]) != (payload["reviewer_id"], payload["submission_payload_sha256"]):
        raise ValueError("Колонки retrieval-истории не совпали с payload.")
    return {
        "submission_id": identifier,
        "source_job_id": str(job_id),
        "package_id": row[0],
        "packet_payload_sha256": row[1],
        "reviewer_id": row[2],
        "submission_payload_sha256": row[3],
        "created_at": row[5].isoformat(),
        "validated_submission": payload,
        "individual_opinion_not_gold": True,
        "precision_available": False,
    }


def history(job_id: str, limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Лимит retrieval-истории должен быть от 1 до 500.")
    packet, _template = from_job_id(job_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM retrieval_review_submission WHERE package_id=%s",
            (packet["package_id"],),
        )
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT submission_id,reviewer_id,submission_payload_sha256,created_at "
            "FROM retrieval_review_submission WHERE package_id=%s "
            "ORDER BY created_at DESC,submission_id DESC LIMIT %s",
            (packet["package_id"], limit),
        )
        rows = [{
            "submission_id": str(identifier), "reviewer_id": reviewer,
            "submission_payload_sha256": checksum, "created_at": created.isoformat(),
        } for identifier, reviewer, checksum, created in cur.fetchall()]
    return {
        "source_job_id": str(job_id),
        "package_id": packet["package_id"],
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "total": total, "returned": len(rows), "submissions": rows,
        "identity": "self_declared_not_authenticated",
        "precision_available": False,
    }


def compare(job_id: str, left_submission_id: str, right_submission_id: str) -> dict:
    if left_submission_id == right_submission_id:
        raise ValueError("Для сравнения нужны две разные retrieval-анкеты.")
    packet, _template = from_job_id(job_id)
    left = read(job_id, left_submission_id)
    right = read(job_id, right_submission_id)
    result = compare_validated_submissions(
        packet, left["validated_submission"], right["validated_submission"]
    )
    result.pop("comparison_payload_sha256")
    result["source_job_id"] = str(job_id)
    result["stored_submission_ids"] = [left["submission_id"], right["submission_id"]]
    result["comparison_payload_sha256"] = digest(result)
    return result
