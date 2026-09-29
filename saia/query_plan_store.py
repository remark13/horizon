"""Append-only storage for explicitly approved transparent query plans."""
from __future__ import annotations

import hashlib
import json
import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.query_planning import preview


VERSION = "approved-transparent-query-plan-0.4.36"


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except ValueError:
        raise ValueError("Ключ подтверждения должен быть UUID.") from None


def validate_approval(
    query: str,
    max_suggestions: int,
    preview_payload_sha256: str,
    selected_branch_ids: list[str],
    approved_by: str,
    plan_id: str,
) -> dict:
    """Rebuild the preview and accept only branches that were actually offered."""
    approved_by = " ".join(approved_by.split())
    if not 1 <= len(approved_by) <= 120:
        raise ValueError("Идентификатор подтверждающего должен содержать от 1 до 120 символов.")
    current = preview(query, max_suggestions)
    if current["plan_payload_sha256"] != preview_payload_sha256:
        raise ValueError(
            "Предпросмотр изменился или контрольная сумма неверна; получите его заново."
        )
    if len(selected_branch_ids) > 20:
        raise ValueError("Можно подтвердить не более 20 предложенных ветвей.")
    if len(selected_branch_ids) != len(set(selected_branch_ids)):
        raise ValueError("Одна и та же ветвь не должна подтверждаться дважды.")
    offered = {item["suggestion_id"]: item for item in current["suggestions"]}
    unknown = [branch_id for branch_id in selected_branch_ids if branch_id not in offered]
    if unknown:
        raise ValueError(
            "Нельзя подтвердить ветвь, которой не было в показанном предпросмотре: "
            + ", ".join(unknown)
        )
    branches = [{
        "branch_id": "original-query",
        "query": current["original_query"],
        "origin": "user_query",
        "mandatory": True,
    }]
    branches.extend({
        "branch_id": branch_id,
        "query": offered[branch_id]["query_en"],
        "label_ru": offered[branch_id]["label_ru"],
        "origin": offered[branch_id]["basis"],
        "mandatory": False,
    } for branch_id in selected_branch_ids)
    payload = {
        "version": VERSION,
        "plan_id": plan_id,
        "original_query": current["original_query"],
        "max_suggestions": max_suggestions,
        "preview_version": current["version"],
        "preview_payload_sha256": preview_payload_sha256,
        "approved_by": approved_by,
        "selected_branch_ids": selected_branch_ids,
        "branches": branches,
        "complete": True,
        "append_only": True,
        "original_query_preserved": True,
        "automatic_execution": False,
        "scientific_result": False,
        "interpretation": (
            "Зафиксирован выбор поисковых ветвей. Это не запуск сбора, не результат "
            "анализа и не подтверждение слабого сигнала."
        ),
    }
    payload["payload_sha256"] = _digest(payload)
    return payload


def verify_payload(payload: dict) -> dict:
    value = dict(payload)
    checksum = value.pop("payload_sha256", None)
    if not isinstance(checksum, str) or checksum != _digest(value):
        raise ValueError("Контрольная сумма сохранённого поискового плана не совпала.")
    if (
        payload.get("version") != VERSION
        or payload.get("complete") is not True
        or payload.get("append_only") is not True
        or payload.get("original_query_preserved") is not True
        or payload.get("automatic_execution") is not False
        or payload.get("scientific_result") is not False
    ):
        raise ValueError("Сохранённый поисковый план не прошёл проверку политики.")
    if not payload.get("branches") or payload["branches"][0].get("branch_id") != "original-query":
        raise ValueError("В сохранённом плане отсутствует исходный запрос пользователя.")
    return payload


def approve(
    query: str,
    max_suggestions: int,
    preview_payload_sha256: str,
    selected_branch_ids: list[str],
    approved_by: str,
    operation_id: str | None = None,
) -> dict:
    plan_id = _uuid(operation_id)
    payload = validate_approval(
        query, max_suggestions, preview_payload_sha256,
        selected_branch_ids, approved_by, plan_id,
    )
    expected = (
        payload["original_query"], payload["max_suggestions"],
        payload["preview_payload_sha256"], payload["approved_by"],
        payload["selected_branch_ids"], payload["payload_sha256"], payload,
    )
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approved_query_plan "
            "(plan_id,original_query,max_suggestions,preview_payload_sha256,approved_by,"
            "selected_branch_ids,payload_sha256,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING created_at",
            (plan_id, *expected[:4], Jsonb(expected[4]), expected[5], Jsonb(expected[6])),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        if inserted:
            created = inserted[0]
        else:
            cur.execute(
                "SELECT original_query,max_suggestions,preview_payload_sha256,approved_by,"
                "selected_branch_ids,payload_sha256,payload,created_at "
                "FROM approved_query_plan WHERE plan_id=%s",
                (plan_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Конфликт подтверждения не удалось сопоставить с планом.")
            if tuple(row[:7]) != expected:
                raise ValueError(
                    "Этот ключ уже использован для другого поискового плана; история не изменена."
                )
            payload, created = verify_payload(row[6]), row[7]
    return {
        "plan_id": plan_id,
        "created_at": created.isoformat(),
        "replayed": replayed,
        "approved_plan": payload,
        "execution_started": False,
    }


def read(plan_id: str) -> dict:
    identifier = _uuid(plan_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT original_query,max_suggestions,preview_payload_sha256,approved_by,"
            "selected_branch_ids,payload_sha256,payload,created_at "
            "FROM approved_query_plan WHERE plan_id=%s",
            (identifier,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("Подтверждённый поисковый план не найден.")
    payload = verify_payload(row[6])
    expected = (
        payload["original_query"], payload["max_suggestions"],
        payload["preview_payload_sha256"], payload["approved_by"],
        payload["selected_branch_ids"], payload["payload_sha256"],
    )
    if tuple(row[:6]) != expected or payload["plan_id"] != identifier:
        raise ValueError("Колонки истории не совпали с проверенным поисковым планом.")
    return {
        "plan_id": identifier,
        "created_at": row[7].isoformat(),
        "approved_plan": payload,
        "execution_started": False,
    }


def history(limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Лимит истории должен быть от 1 до 500.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM approved_query_plan")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT plan_id,original_query,approved_by,selected_branch_ids,"
            "preview_payload_sha256,payload_sha256,created_at FROM approved_query_plan "
            "ORDER BY created_at DESC,plan_id DESC LIMIT %s",
            (limit,),
        )
        rows = [{
            "plan_id": str(plan_id), "original_query": query, "approved_by": approved_by,
            "selected_branch_ids": selected, "preview_payload_sha256": preview_hash,
            "payload_sha256": payload_hash, "created_at": created.isoformat(),
        } for plan_id, query, approved_by, selected, preview_hash, payload_hash, created
            in cur.fetchall()]
    return {
        "total": total,
        "returned": len(rows),
        "plans": rows,
        "identity": "self_declared_not_authenticated",
        "execution_started": False,
    }
