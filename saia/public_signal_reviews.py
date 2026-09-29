"""Optional opinions on dispatched public records, never detector labels."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.expert import validate
from saia.scout_public_signals import parse_ids
from saia.hybrid import digest

CHOICES = {"needs_review", "signal_supported", "noise", "possible_duplicate", "insufficient_evidence"}


def request_snapshot(request_id: str, public_signal_id: str) -> dict:
    parse_ids(public_signal_id, maximum=1)
    try:
        identifier = str(uuid.UUID(str(request_id)))
    except ValueError:
        raise ValueError("Некорректная экспертная заявка.") from None
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT candidate_snapshot FROM expert_validation_request WHERE request_id=%s", (identifier,))
        row = cur.fetchone()
    match = next((item for item in (row[0] if row else []) if item.get("public_signal_id") == public_signal_id), None)
    if match is None:
        raise ValueError("Опубликованный сигнал отсутствует в выбранной экспертной заявке.")
    reference = match.get("public_reference") or {}
    members = [{key: value for key, value in member.items() if key != "catalog_version"}
               for member in reference.get("references") or []]
    checksum = digest({"catalog_version": reference.get("catalog_version"), "records": sorted(members, key=lambda item: item["id"])})
    if not members or checksum != match.get("reference_content_sha256") or reference.get("reference_content_sha256") != checksum:
        raise ValueError("Снимок опубликованного сигнала в заявке повреждён.")
    return match


def record(request_id: str, public_signal_id: str, decision: str, reviewed_by: str,
           rationale: str, sources: list[str], operation_id: str | None = None) -> dict:
    actor, reason, links = validate(decision, reviewed_by, rationale, sources, choices=CHOICES)
    snapshot = request_snapshot(request_id, public_signal_id)
    try:
        review_id = str(uuid.UUID(str(operation_id))) if operation_id else str(uuid.uuid4())
    except ValueError:
        raise ValueError("Ключ повторной отправки должен быть UUID.") from None
    request_id = str(uuid.UUID(str(request_id)))
    reference_hash = snapshot["reference_content_sha256"]
    version = snapshot["public_reference"]["catalog_version"]
    expected = (request_id, public_signal_id, version, reference_hash, actor, decision, reason, links)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO public_signal_review "
                    "(review_id,request_id,public_signal_id,catalog_version,reference_content_sha256,reviewed_by,decision,rationale,sources) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (review_id) DO NOTHING RETURNING created_at",
                    (review_id, *expected[:-1], Jsonb(links)))
        inserted = cur.fetchone()
        if inserted is None:
            cur.execute("SELECT request_id::text,public_signal_id,catalog_version,reference_content_sha256,reviewed_by,decision,rationale,sources,created_at "
                        "FROM public_signal_review WHERE review_id=%s", (review_id,))
            old = cur.fetchone()
            if old is None or tuple(old[:8]) != expected:
                raise ValueError("Этот ключ уже использован для другого мнения.")
            created = old[8]
        else:
            created = inserted[0]
    return {"review_id": review_id, "request_id": request_id, "public_signal_id": public_signal_id,
            "reference_content_sha256": reference_hash, "decision": decision,
            "created_at": created.isoformat(), "replayed": inserted is None,
            "scientific_score_modified": False, "identity": "self_declared_not_authenticated",
            "interpretation": "Отдельное мнение о публичной записи; автоматическая оценка и происхождение не изменены."}


def history_for_reference(reference_hash: str, limit: int = 100, *, request_id: str | None = None) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Лимит истории должен быть от 1 до 500.")
    scope_clause, scope_args = "", []
    if request_id is not None:
        try:
            request_id = str(uuid.UUID(str(request_id)))
        except ValueError:
            raise ValueError("Некорректная экспертная заявка.") from None
        scope_clause, scope_args = "AND request_id=%s ", [request_id]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT review_id,request_id,public_signal_id,catalog_version,reviewed_by,decision,rationale,sources,created_at "
                    "FROM public_signal_review WHERE reference_content_sha256=%s "
                    + scope_clause + "ORDER BY created_at DESC,review_id DESC LIMIT %s", (reference_hash, *scope_args, limit))
        rows = cur.fetchall()
    return {"reviews": [{"review_id": str(row[0]), "request_id": str(row[1]), "public_signal_id": row[2],
                         "catalog_version": row[3], "reviewed_by": row[4], "decision": row[5],
                         "rationale": row[6], "sources": row[7], "created_at": row[8].isoformat()}
                        for row in rows], "history_scope": ("exact_pinned_public_reference_and_request" if request_id
                                                              else "exact_pinned_public_reference_across_requests"),
            "request_id": request_id,
            "identity": "self_declared_not_authenticated", "scientific_score_modified": False}
