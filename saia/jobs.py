"""Durable jobs for bounded collection from an approved query.

The result is deliberately labelled as a candidate publication corpus.  This
module does not skip normalization, clustering, temporal measurements, gates,
or expert review and therefore cannot produce a weak-signal verdict.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import date
from typing import Callable

from psycopg.types.json import Jsonb

from saia import db
from saia.discovery import discover


class JobConflict(ValueError):
    """The same idempotency key was reused for a different request."""


JOB_COLUMNS = (
    "job_id", "operation_id", "mission_id", "query_version_id",
    "approved_query_plan_id", "retry_of_job_id", "job_kind", "status", "requested_by", "payload",
    "input_sha256", "result_role", "result", "result_sha256", "error",
    "attempt_count", "max_attempts", "lease_owner", "lease_expires_at",
    "heartbeat_at", "cancel_requested_at", "created_at", "started_at",
    "finished_at",
)
JOB_SELECT = ", ".join(JOB_COLUMNS)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _uuid(value: str | uuid.UUID | None, label: str, *, generate: bool = False) -> str:
    if value is None and generate:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"Некорректный {label}.") from None


def _actor(value: str, label: str = "исполнитель") -> str:
    value = " ".join((value or "").split())
    if not 1 <= len(value) <= 120:
        raise ValueError(f"Укажите {label} (не более 120 символов).")
    return value


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _job(row) -> dict:
    if not row:
        raise ValueError("Задача не найдена.")
    result = {key: _json_value(value) for key, value in zip(JOB_COLUMNS, row)}
    result["job_id"] = str(result["job_id"])
    result["operation_id"] = str(result["operation_id"])
    if result["retry_of_job_id"] is not None:
        result["retry_of_job_id"] = str(result["retry_of_job_id"])
    if result["approved_query_plan_id"] is not None:
        result["approved_query_plan_id"] = str(result["approved_query_plan_id"])
    source_errors = (result.get("result") or {}).get("errors") or {}
    if result["status"] == "succeeded":
        result["result_completeness"] = "partial" if source_errors else "complete"
    else:
        result["result_completeness"] = "not_finished"
    result["interpretation"] = (
        "Результат полного анализа — автоматически выявленные кандидаты слабых "
        "сигналов. Они показываются без обязательной экспертизы, но не называются "
        "экспертно подтверждёнными и не являются прогнозом рынка."
        if result["job_kind"] == "controlled_full_analysis" else
        "Результат содержит сбалансированный по подтверждённым ветвям "
        "корпус-кандидат. Он не является научным score или списком слабых сигналов."
        if result["job_kind"] == "approved_balanced_discovery" else
        "Результат содержит только публикации-кандидаты. Он не является "
        "списком слабых сигналов без последующих этапов анализа."
    )
    return result


def _read_locked(cur, job_id: str) -> dict:
    cur.execute(f"SELECT {JOB_SELECT} FROM analysis_job WHERE job_id=%s FOR UPDATE", (job_id,))
    return _job(cur.fetchone())


def _event(cur, job_id: str, event_type: str, actor: str, details: dict | None = None) -> None:
    cur.execute(
        "INSERT INTO analysis_job_event (job_id,event_type,actor,details) VALUES (%s,%s,%s,%s)",
        (job_id, event_type, actor, Jsonb(details or {})),
    )


def apply_local_arxiv_fallback(result: dict, payload: dict, directory: str,
                               searcher=None) -> dict:
    """Replace only an unavailable arXiv API channel with the pinned mirror."""
    from saia.discovery import Publication, deduplicate
    from saia.local_arxiv_search import search

    if not (result.get("errors") or {}).get("arxiv"):
        return result
    searcher = searcher or search
    local = searcher(directory, payload["search_plan"], payload["limit_per_source"])
    existing = [Publication(
        canonical_key=item["canonical_key"], title=item["title"],
        abstract=item.get("abstract"), published_at=item["published_at"],
        sources=tuple(item.get("sources") or ()),
        source_ids=tuple(item.get("source_ids") or ()),
        urls=tuple(item.get("urls") or ()), doi=item.get("doi"),
        authors=tuple(item.get("authors") or ()),
        openalex_type=item.get("openalex_type"),
    ) for item in result.get("works", [])]
    merged = deduplicate([*existing, *local.works])
    result = {**result, "works": [asdict(work) for work in merged]}
    result["source_counts"] = {**(result.get("source_counts") or {}),
                               "arxiv": len(local.works)}
    result["errors"] = dict(result.get("errors") or {})
    result["errors"].pop("arxiv", None)
    if local.audit["invalid_selected_records"]:
        result["errors"]["arxiv_local_records"] = (
            f"В локальном снимке исключено некорректных подходящих записей: "
            f"{local.audit['invalid_selected_records']}."
        )
    result["fallbacks"] = {**(result.get("fallbacks") or {}), "arxiv": local.audit}
    result["source_modes"] = {**(result.get("source_modes") or {}),
                              "openalex": "live_api_current_metadata",
                              "arxiv": "pinned_local_metadata_snapshot"}
    result["coverage_comparable"] = None
    result["collection_policy"] = "controlled-query-local-arxiv-fallback-v1"
    result["limitations"] = list(result.get("limitations") or []) + list(local.audit["limitations"])
    result["query_hash"] = _digest({
        "original_query_hash": result.get("query_hash"),
        "local_arxiv_revision": local.audit["revision"],
        "adapter_version": local.audit["adapter_version"],
    })[:16]
    return result


def enqueue_controlled_discovery(mission_id: str, query_version_id: str,
                                 requested_by: str, limit_per_source: int = 50,
                                 operation_id: str | uuid.UUID | None = None,
                                 max_attempts: int = 3) -> dict:
    """Persist an approved controlled query without executing it inline."""
    requested_by = _actor(requested_by, "автора запроса")
    operation_id = _uuid(operation_id, "operation_id", generate=True)
    if not 1 <= limit_per_source <= 100:
        raise ValueError("Лимит должен быть от 1 до 100 публикаций на источник.")
    if not 1 <= max_attempts <= 10:
        raise ValueError("Число попыток должно быть от 1 до 10.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT q.mission_id,q.payload->'controlled_search_plan' "
            "FROM query_version q JOIN query_expansion_decision d USING(query_version_id) "
            "WHERE q.query_version_id=%s",
            (query_version_id,),
        )
        row = cur.fetchone()
        if not row or not row[1]:
            raise ValueError("Нужна подтверждённая управляемая версия запроса.")
        if row[0] != mission_id:
            raise ValueError("Версия запроса не принадлежит указанной миссии.")
        payload = {
            "contract_version": "controlled-discovery-job-v1",
            "mission_id": mission_id,
            "query_version_id": query_version_id,
            "limit_per_source": limit_per_source,
            "search_plan": row[1],
        }
        input_sha256 = _digest(payload)
        cur.execute(f"SELECT {JOB_SELECT} FROM analysis_job WHERE operation_id=%s FOR UPDATE",
                    (operation_id,))
        existing = cur.fetchone()
        if existing:
            found = _job(existing)
            if found["input_sha256"] != input_sha256:
                raise JobConflict(
                    "operation_id уже использован для другого входа; создайте новый идентификатор."
                )
            return found
        job_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO analysis_job (job_id,operation_id,mission_id,query_version_id,"
            "job_kind,status,requested_by,payload,input_sha256,max_attempts) "
            "VALUES (%s,%s,%s,%s,'controlled_discovery','queued',%s,%s,%s,%s)",
            (job_id, operation_id, mission_id, query_version_id, requested_by,
             Jsonb(payload), input_sha256, max_attempts),
        )
        _event(cur, job_id, "queued", requested_by,
               {"input_sha256": input_sha256, "result_role": "corpus_candidate_not_signals"})
        return _read_locked(cur, job_id)


def enqueue_balanced_discovery(
    approved_query_plan_id: str,
    requested_by: str,
    date_from: date,
    as_of_date: date,
    *,
    limit_per_source: int = 10,
    max_results: int = 15,
    compiled_query_plan_id: str | None = None,
    openalex_collection_mode: str = "live",
    operation_id: str | uuid.UUID | None = None,
    max_attempts: int = 3,
) -> dict:
    """Queue collection only from an immutable, explicitly approved plan."""
    from saia.multi_branch_discovery import RESULT_ROLE, VERSION
    from saia.query_plan_store import verify_payload
    from saia.compiled_query_plan_store import verify_payload as verify_compiled

    plan_id = _uuid(approved_query_plan_id, "approved_query_plan_id")
    requested_by = _actor(requested_by, "автора запуска")
    operation_id = _uuid(operation_id, "operation_id", generate=True)
    if date_from >= as_of_date:
        raise ValueError("Начало периода должно быть раньше даты среза.")
    if not 1 <= limit_per_source <= 100:
        raise ValueError("Лимит должен быть от 1 до 100 публикаций на источник и ветвь.")
    if not 1 <= max_results <= 300:
        raise ValueError("Итоговый лимит должен быть от 1 до 300 публикаций.")
    if not 1 <= max_attempts <= 10:
        raise ValueError("Число попыток должно быть от 1 до 10.")
    if openalex_collection_mode not in ("live", "cache_year_spread",
                                        "live_with_cache_fallback", "compound_boolean_live",
                                        "live_with_prepared_supplement"):
        raise ValueError("Неизвестный режим сбора OpenAlex.")
    if openalex_collection_mode in {"cache_year_spread", "live_with_cache_fallback",
                                       "compound_boolean_live", "live_with_prepared_supplement"} and compiled_query_plan_id is None:
        raise ValueError("Для выбранного режима OpenAlex нужен скомпилированный план точных фраз.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM approved_query_plan WHERE plan_id=%s",
            (plan_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Подтверждённый поисковый план не найден.")
        approved = verify_payload(row[0])
        if len(approved["branches"]) > 12:
            raise ValueError(
                "В плане больше 12 ветвей; разделите его на несколько подтверждённых запусков."
            )
        payload = {
            "contract_version": VERSION,
            "approved_query_plan_id": plan_id,
            "approved_plan_payload_sha256": approved["payload_sha256"],
            "date_from": date_from.isoformat(),
            "as_of_date": as_of_date.isoformat(),
            "limit_per_source": limit_per_source,
            "max_results": max_results,
            "openalex_collection_mode": openalex_collection_mode,
            "branches": approved["branches"],
        }
        if compiled_query_plan_id is not None:
            compilation_id = _uuid(compiled_query_plan_id, "compiled_query_plan_id")
            cur.execute(
                "SELECT payload FROM compiled_query_plan WHERE compilation_id=%s",
                (compilation_id,),
            )
            compiled_row = cur.fetchone()
            if not compiled_row:
                raise ValueError("Скомпилированный поисковый план не найден.")
            compiled = verify_compiled(compiled_row[0])
            if (
                compiled["approved_query_plan_id"] != plan_id
                or compiled["approved_plan_payload_sha256"] != approved["payload_sha256"]
            ):
                raise ValueError("Компиляция относится к другой версии подтверждённого плана.")
            if (openalex_collection_mode == "compound_boolean_live"
                    and not any(spec.get("concept_groups")
                                for spec in compiled["branch_specs"])):
                raise ValueError("Для логического поиска OpenAlex нужен составной план понятий.")
            payload.update({
                "compiled_query_plan_id": compilation_id,
                "compiled_plan_payload_sha256": compiled["payload_sha256"],
                "compiled_branch_specs": compiled["branch_specs"],
            })
        input_sha256 = _digest(payload)
        cur.execute(
            f"SELECT {JOB_SELECT} FROM analysis_job WHERE operation_id=%s FOR UPDATE",
            (operation_id,),
        )
        existing = cur.fetchone()
        if existing:
            found = _job(existing)
            if found["input_sha256"] != input_sha256:
                raise JobConflict(
                    "operation_id уже использован для другого входа; создайте новый идентификатор."
                )
            return found
        cur.execute(
            "SELECT job_id FROM analysis_job WHERE approved_query_plan_id=%s "
            "AND job_kind='approved_balanced_discovery' "
            "AND status IN ('queued','running','cancel_requested') FOR UPDATE",
            (plan_id,),
        )
        if cur.fetchone():
            raise JobConflict("Для этого плана уже выполняется сбор; откройте его историю.")
        job_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO analysis_job "
            "(job_id,operation_id,approved_query_plan_id,job_kind,status,requested_by,"
            "payload,input_sha256,result_role,max_attempts) "
            "VALUES (%s,%s,%s,'approved_balanced_discovery','queued',%s,%s,%s,%s,%s)",
            (job_id, operation_id, plan_id, requested_by, Jsonb(payload),
             input_sha256, RESULT_ROLE, max_attempts),
        )
        _event(cur, job_id, "queued", requested_by, {
            "input_sha256": input_sha256, "result_role": RESULT_ROLE,
            "approved_query_plan_id": plan_id,
            "branch_count": len(approved["branches"]),
            "compiled_query_plan_id": payload.get("compiled_query_plan_id"),
        })
        return _read_locked(cur, job_id)


def enqueue_full_analysis(mission_id: str, query_version_id: str,
                          requested_by: str, *, max_records: int = 10_000,
                          top_n: int = 15,
                          acknowledge_source_scope: bool = False,
                          operation_id: str | uuid.UUID | None = None,
                          max_attempts: int = 3) -> dict:
    """Queue an approved, frozen scientific cohort for candidate generation."""
    from saia.full_analysis import (RESULT_ROLE, ensure_arxiv_source_profile,
                                    job_payload)

    requested_by = _actor(requested_by, "автора запроса")
    operation_id = _uuid(operation_id, "operation_id", generate=True)
    if acknowledge_source_scope is not True:
        raise ValueError(
            "Подтвердите область источников и ограничения покрытия этого запуска."
        )
    if not 1 <= max_attempts <= 10:
        raise ValueError("Число попыток должно быть от 1 до 10.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {JOB_SELECT} FROM analysis_job WHERE operation_id=%s FOR UPDATE",
                    (operation_id,))
        existing = cur.fetchone()
        if existing:
            found = _job(existing)
            expected = {
                "mission_id": mission_id,
                "base_query_version_id": query_version_id,
                "max_records": max_records,
                "top_n": top_n,
            }
            observed = {
                "mission_id": found["mission_id"],
                "base_query_version_id": (found["payload"] or {}).get(
                    "base_query_version_id"
                ),
                "max_records": (found["payload"] or {}).get("max_records"),
                "top_n": (found["payload"] or {}).get("top_n"),
            }
            if found["job_kind"] != "controlled_full_analysis" or observed != expected:
                raise JobConflict(
                    "operation_id уже использован для другого входа; создайте новый идентификатор."
                )
            return found

        cur.execute(
            "SELECT job_id FROM analysis_job WHERE mission_id=%s "
            "AND job_kind='controlled_full_analysis' "
            "AND status IN ('queued','running','cancel_requested') FOR UPDATE",
            (mission_id,),
        )
        if cur.fetchone():
            raise JobConflict(
                "Для этой миссии уже выполняется полный анализ; откройте его историю."
            )
        execution_id, profile, _reused = ensure_arxiv_source_profile(
            cur, mission_id, query_version_id, requested_by
        )
        payload = job_payload(
            mission_id, query_version_id, execution_id, profile,
            max_records, top_n,
        )
        input_sha256 = _digest(payload)
        job_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO analysis_job (job_id,operation_id,mission_id,query_version_id,"
            "job_kind,status,requested_by,payload,input_sha256,result_role,max_attempts) "
            "VALUES (%s,%s,%s,%s,'controlled_full_analysis','queued',%s,%s,%s,%s,%s)",
            (job_id, operation_id, mission_id, execution_id, requested_by,
             Jsonb(payload), input_sha256, RESULT_ROLE, max_attempts),
        )
        _event(cur, job_id, "queued", requested_by, {
            "input_sha256": input_sha256,
            "result_role": RESULT_ROLE,
            "source_scope": payload["source_scope"],
            "openalex_role": payload["openalex_role"],
            "max_records": max_records,
            "top_n": top_n,
        })
        return _read_locked(cur, job_id)


def read(job_id: str | uuid.UUID) -> dict:
    job_id = _uuid(job_id, "job_id")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {JOB_SELECT} FROM analysis_job WHERE job_id=%s", (job_id,))
        result = _job(cur.fetchone())
        cur.execute(
            "SELECT event_id,event_type,actor,details,created_at "
            "FROM analysis_job_event WHERE job_id=%s ORDER BY event_id", (job_id,)
        )
        result["events"] = [
            {"event_id": row[0], "event_type": row[1], "actor": row[2],
             "details": row[3], "created_at": row[4].isoformat()}
            for row in cur.fetchall()
        ]
    return result


def history(mission_id: str, limit: int = 50) -> dict:
    if not 1 <= limit <= 200:
        raise ValueError("Лимит истории должен быть от 1 до 200.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {JOB_SELECT} FROM analysis_job WHERE mission_id=%s "
                    "ORDER BY created_at DESC,job_id DESC LIMIT %s", (mission_id, limit))
        jobs = [_job(row) for row in cur.fetchall()]
    return {"mission_id": mission_id, "jobs": jobs}


def query_plan_history(approved_query_plan_id: str, limit: int = 50) -> dict:
    plan_id = _uuid(approved_query_plan_id, "approved_query_plan_id")
    if not 1 <= limit <= 200:
        raise ValueError("Лимит истории должен быть от 1 до 200.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {JOB_SELECT} FROM analysis_job WHERE approved_query_plan_id=%s "
            "ORDER BY created_at DESC,job_id DESC LIMIT %s", (plan_id, limit),
        )
        values = [_job(row) for row in cur.fetchall()]
    return {"approved_query_plan_id": plan_id, "jobs": values}


def _recover_expired(cur, actor: str) -> None:
    cur.execute(
        "SELECT job_id,status,attempt_count,max_attempts FROM analysis_job "
        "WHERE status IN ('running','cancel_requested') AND lease_expires_at < now() "
        "ORDER BY lease_expires_at FOR UPDATE SKIP LOCKED"
    )
    for job_id, status, attempts, maximum in cur.fetchall():
        job_id = str(job_id)
        if status == "cancel_requested":
            cur.execute(
                "UPDATE analysis_job SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL,"
                "heartbeat_at=NULL,finished_at=now(),error=NULL WHERE job_id=%s", (job_id,)
            )
            _event(cur, job_id, "cancelled", actor, {"reason": "lease_expired_after_cancel"})
        elif attempts < maximum:
            cur.execute(
                "UPDATE analysis_job SET status='queued',lease_owner=NULL,lease_expires_at=NULL,"
                "heartbeat_at=NULL,error=%s WHERE job_id=%s",
                ("Предыдущий воркер потерял аренду; задача возвращена в очередь.", job_id),
            )
            _event(cur, job_id, "lease_expired_requeued", actor, {"attempt_count": attempts})
        else:
            cur.execute(
                "UPDATE analysis_job SET status='failed',lease_owner=NULL,lease_expires_at=NULL,"
                "heartbeat_at=NULL,finished_at=now(),error=%s WHERE job_id=%s",
                ("Исчерпаны попытки после потери аренды воркером.", job_id),
            )
            _event(cur, job_id, "lease_expired_failed", actor, {"attempt_count": attempts})


def claim(worker_id: str, lease_seconds: int = 300) -> dict | None:
    worker_id = _actor(worker_id, "worker_id")
    if not 10 <= lease_seconds <= 3600:
        raise ValueError("Аренда должна быть от 10 до 3600 секунд.")
    with db.connect() as conn, conn.cursor() as cur:
        _recover_expired(cur, worker_id)
        cur.execute(
            "SELECT job_id FROM analysis_job WHERE status='queued' "
            "ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        job_id = str(row[0])
        cur.execute(
            "UPDATE analysis_job SET status='running',attempt_count=attempt_count+1,"
            "lease_owner=%s,lease_expires_at=now()+(%s * interval '1 second'),"
            "heartbeat_at=now(),started_at=COALESCE(started_at,now()),error=NULL "
            "WHERE job_id=%s", (worker_id, lease_seconds, job_id)
        )
        _event(cur, job_id, "claimed", worker_id, {"lease_seconds": lease_seconds})
        return _read_locked(cur, job_id)


def heartbeat(job_id: str | uuid.UUID, worker_id: str, lease_seconds: int = 300) -> dict:
    job_id = _uuid(job_id, "job_id")
    worker_id = _actor(worker_id, "worker_id")
    if not 10 <= lease_seconds <= 3600:
        raise ValueError("Аренда должна быть от 10 до 3600 секунд.")
    with db.connect() as conn, conn.cursor() as cur:
        current = _read_locked(cur, job_id)
        if current["status"] not in ("running", "cancel_requested") or current["lease_owner"] != worker_id:
            raise JobConflict("Задача не арендована этим воркером.")
        cur.execute(
            "UPDATE analysis_job SET heartbeat_at=now(),"
            "lease_expires_at=now()+(%s * interval '1 second') WHERE job_id=%s",
            (lease_seconds, job_id),
        )
        _event(cur, job_id, "heartbeat", worker_id, {"lease_seconds": lease_seconds})
        return _read_locked(cur, job_id)


def cancel(job_id: str | uuid.UUID, requested_by: str) -> dict:
    job_id = _uuid(job_id, "job_id")
    requested_by = _actor(requested_by, "автора отмены")
    with db.connect() as conn, conn.cursor() as cur:
        current = _read_locked(cur, job_id)
        if current["status"] in ("succeeded", "failed", "cancelled"):
            return current
        if current["status"] == "queued":
            cur.execute("UPDATE analysis_job SET status='cancelled',cancel_requested_at=now(),"
                        "finished_at=now() WHERE job_id=%s", (job_id,))
            _event(cur, job_id, "cancelled", requested_by, {"from": "queued"})
        elif current["status"] == "running":
            cur.execute("UPDATE analysis_job SET status='cancel_requested',"
                        "cancel_requested_at=now() WHERE job_id=%s", (job_id,))
            _event(cur, job_id, "cancel_requested", requested_by)
        return _read_locked(cur, job_id)


def retry(job_id: str | uuid.UUID, requested_by: str,
          operation_id: str | uuid.UUID | None = None) -> dict:
    job_id = _uuid(job_id, "job_id")
    requested_by = _actor(requested_by, "автора повтора")
    operation_id = _uuid(operation_id, "operation_id", generate=True)
    with db.connect() as conn, conn.cursor() as cur:
        old = _read_locked(cur, job_id)
        if old["status"] not in ("failed", "cancelled"):
            raise ValueError("Повтор создаётся только для завершившейся ошибкой или отменённой задачи.")
        cur.execute(f"SELECT {JOB_SELECT} FROM analysis_job WHERE operation_id=%s FOR UPDATE",
                    (operation_id,))
        existing = cur.fetchone()
        if existing:
            found = _job(existing)
            if found["retry_of_job_id"] != job_id:
                raise JobConflict("operation_id уже относится к другой операции.")
            return found
        new_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO analysis_job "
            "(job_id,operation_id,mission_id,query_version_id,approved_query_plan_id,"
            "retry_of_job_id,job_kind,status,requested_by,payload,input_sha256,result_role,"
            "max_attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s)",
            (new_id, operation_id, old["mission_id"], old["query_version_id"],
             old["approved_query_plan_id"], job_id, old["job_kind"], requested_by,
             Jsonb(old["payload"]), old["input_sha256"], old["result_role"],
             old["max_attempts"]),
        )
        _event(cur, new_id, "queued", requested_by,
               {"retry_of_job_id": job_id, "input_sha256": old["input_sha256"]})
        _event(cur, new_id, "retry_created", requested_by, {"retry_of_job_id": job_id})
        return _read_locked(cur, new_id)


def _renew_lease(job_id: str, worker_id: str, lease_seconds: int) -> bool:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE analysis_job SET heartbeat_at=now(),"
            "lease_expires_at=now()+(%s * interval '1 second') "
            "WHERE job_id=%s AND lease_owner=%s "
            "AND status IN ('running','cancel_requested')",
            (lease_seconds, job_id, worker_id),
        )
        changed = cur.rowcount == 1
    return changed


def _full_job_execute(current: dict, worker_id: str, lease_seconds: int,
                      full_runner: Callable | None = None) -> dict:
    from saia.full_analysis import FullAnalysisCancelled, RESULT_ROLE, run_job

    job_id = current["job_id"]
    runner = full_runner or run_job
    stop = threading.Event()
    interval = max(5.0, min(60.0, lease_seconds / 3))

    def keep_alive() -> None:
        while not stop.wait(interval):
            try:
                if not _renew_lease(job_id, worker_id, lease_seconds):
                    return
            except Exception:
                # The main thread verifies lease ownership before committing.
                # A transient heartbeat error must not manufacture a result.
                continue

    thread = threading.Thread(target=keep_alive, name=f"saia-lease-{job_id[:8]}",
                              daemon=True)
    thread.start()

    def cancelled() -> bool:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status,lease_owner FROM analysis_job WHERE job_id=%s",
                        (job_id,))
            row = cur.fetchone()
        return not row or row[0] == "cancel_requested" or row[1] != worker_id

    def progress(stage: str, state: str, details: dict) -> None:
        if state not in {"started", "succeeded", "reused", "skipped"}:
            raise ValueError("Неизвестное состояние стадии полного анализа.")
        event_type = f"stage_{state}"
        with db.connect() as conn, conn.cursor() as cur:
            locked = _read_locked(cur, job_id)
            if locked["status"] not in ("running", "cancel_requested"):
                raise JobConflict("Задача больше не выполняется.")
            cur.execute(
                "UPDATE analysis_job SET heartbeat_at=now(),"
                "lease_expires_at=now()+(%s * interval '1 second') WHERE job_id=%s",
                (lease_seconds, job_id),
            )
            _event(cur, job_id, event_type, worker_id,
                   {"stage": stage, **(details or {})})

    try:
        result = runner(current["payload"], progress=progress,
                        cancel_check=cancelled)
        if not isinstance(result, dict) or result.get("result_role") != RESULT_ROLE:
            raise ValueError("Полный анализ вернул результат неизвестной роли.")
        result_hash = _digest(result)
        with db.connect() as conn, conn.cursor() as cur:
            locked = _read_locked(cur, job_id)
            if locked["status"] == "cancel_requested":
                cur.execute(
                    "UPDATE analysis_job SET status='cancelled',lease_owner=NULL,"
                    "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() "
                    "WHERE job_id=%s", (job_id,),
                )
                _event(cur, job_id, "cancelled", worker_id,
                       {"after_stage": True})
            elif locked["status"] == "running" and locked["lease_owner"] == worker_id:
                cur.execute(
                    "UPDATE analysis_job SET status='succeeded',result=%s,result_sha256=%s,"
                    "lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() "
                    "WHERE job_id=%s",
                    (Jsonb(result), result_hash, job_id),
                )
                _event(cur, job_id, "succeeded", worker_id, {
                    "result_sha256": result_hash,
                    "score_run_id": (result.get("runs") or {}).get("score"),
                    "cards_shown": (result.get("cards") or {}).get("shown"),
                })
            else:
                raise JobConflict(
                    "Аренда истекла или перешла другому воркеру; результат не записан."
                )
            return _read_locked(cur, job_id)
    except FullAnalysisCancelled:
        with db.connect() as conn, conn.cursor() as cur:
            locked = _read_locked(cur, job_id)
            if (locked["status"] in ("running", "cancel_requested")
                    and locked["lease_owner"] == worker_id):
                cur.execute(
                    "UPDATE analysis_job SET status='cancelled',lease_owner=NULL,"
                    "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now(),error=NULL "
                    "WHERE job_id=%s", (job_id,),
                )
                _event(cur, job_id, "cancelled", worker_id,
                       {"between_stages": True})
                return _read_locked(cur, job_id)
        raise
    finally:
        stop.set()
        thread.join(timeout=2)


def execute(job_id: str | uuid.UUID, worker_id: str,
            collector: Callable = discover, full_runner: Callable | None = None,
            lease_seconds: int = 300) -> dict:
    job_id = _uuid(job_id, "job_id")
    worker_id = _actor(worker_id, "worker_id")
    with db.connect() as conn, conn.cursor() as cur:
        current = _read_locked(cur, job_id)
        if current["status"] == "cancel_requested":
            cur.execute("UPDATE analysis_job SET status='cancelled',lease_owner=NULL,"
                        "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() WHERE job_id=%s",
                        (job_id,))
            _event(cur, job_id, "cancelled", worker_id, {"before_collection": True})
            return _read_locked(cur, job_id)
        if current["status"] != "running" or current["lease_owner"] != worker_id:
            raise JobConflict("Задача не арендована этим воркером.")
        payload = current["payload"]
    if current["job_kind"] == "controlled_full_analysis":
        try:
            return _full_job_execute(current, worker_id, lease_seconds, full_runner)
        except Exception as error:
            with db.connect() as conn, conn.cursor() as cur:
                locked = _read_locked(cur, job_id)
                if (locked["status"] in ("running", "cancel_requested")
                        and locked["lease_owner"] == worker_id):
                    cur.execute(
                        "UPDATE analysis_job SET status='failed',error=%s,lease_owner=NULL,"
                        "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() WHERE job_id=%s",
                        ((f"{type(error).__name__}: {error}")[:4000], job_id),
                    )
                    _event(cur, job_id, "failed", worker_id,
                           {"error_type": type(error).__name__})
                    return _read_locked(cur, job_id)
            raise
    if current["job_kind"] == "approved_balanced_discovery":
        from saia.multi_branch_discovery import RESULT_ROLE, run
        stop = threading.Event()
        interval = max(5.0, min(60.0, lease_seconds / 3))

        def keep_alive() -> None:
            while not stop.wait(interval):
                try:
                    if not _renew_lease(job_id, worker_id, lease_seconds):
                        return
                except Exception:
                    # Commit still verifies ownership after collection.
                    continue

        renewer = threading.Thread(target=keep_alive,
                                   name=f"saia-discovery-lease-{job_id[:8]}", daemon=True)
        renewer.start()
        try:
            result = run(payload, collector)
            if result.get("result_role") != RESULT_ROLE:
                raise ValueError("Многоветочный сбор вернул результат неизвестной роли.")
            result_hash = _digest(result)
            with db.connect() as conn, conn.cursor() as cur:
                locked = _read_locked(cur, job_id)
                if locked["status"] == "cancel_requested":
                    cur.execute(
                        "UPDATE analysis_job SET status='cancelled',lease_owner=NULL,"
                        "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() "
                        "WHERE job_id=%s", (job_id,),
                    )
                    _event(cur, job_id, "cancelled", worker_id, {"after_collection": True})
                elif locked["status"] == "running" and locked["lease_owner"] == worker_id:
                    cur.execute(
                        "UPDATE analysis_job SET status='succeeded',result=%s,result_sha256=%s,"
                        "lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() "
                        "WHERE job_id=%s", (Jsonb(result), result_hash, job_id),
                    )
                    _event(cur, job_id, "succeeded", worker_id, {
                        "result_sha256": result_hash,
                        "works": len(result.get("works") or []),
                        "represented_branches": len(
                            (result.get("merge") or {}).get("represented_branches") or []
                        ),
                    })
                else:
                    raise JobConflict(
                        "Аренда истекла или перешла другому воркеру; результат не записан."
                    )
                return _read_locked(cur, job_id)
        except Exception as error:
            with db.connect() as conn, conn.cursor() as cur:
                locked = _read_locked(cur, job_id)
                if (locked["status"] in ("running", "cancel_requested")
                        and locked["lease_owner"] == worker_id):
                    cur.execute(
                        "UPDATE analysis_job SET status='failed',error=%s,lease_owner=NULL,"
                        "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() WHERE job_id=%s",
                        ((f"{type(error).__name__}: {error}")[:4000], job_id),
                    )
                    _event(cur, job_id, "failed", worker_id,
                           {"error_type": type(error).__name__})
                    return _read_locked(cur, job_id)
            raise
        finally:
            stop.set()
            renewer.join(timeout=1.0)
    plan = payload["search_plan"]
    try:
        collected = collector(
            plan["original_query"], date.fromisoformat(plan["date_from"]),
            date.fromisoformat(plan["as_of_date"]), payload["limit_per_source"],
            search_plan=plan,
        )
        result = collected.to_dict() if hasattr(collected, "to_dict") else collected
        if not isinstance(result, dict):
            raise ValueError("Коллектор вернул результат неизвестного формата.")
        mirror = os.environ.get("SAIA_ARXIV_MIRROR_DIR")
        if mirror and (result.get("errors") or {}).get("arxiv"):
            try:
                result = apply_local_arxiv_fallback(result, payload, mirror)
            except Exception as fallback_error:
                result["errors"] = {**(result.get("errors") or {}),
                                    "arxiv_local_fallback":
                                    f"{type(fallback_error).__name__}: {fallback_error}"[:1000]}
        result = {**result, "result_role": "corpus_candidate_not_signals"}
        result_hash = _digest(result)
        with db.connect() as conn, conn.cursor() as cur:
            current = _read_locked(cur, job_id)
            if current["status"] == "cancel_requested":
                cur.execute("UPDATE analysis_job SET status='cancelled',lease_owner=NULL,"
                            "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() WHERE job_id=%s",
                            (job_id,))
                _event(cur, job_id, "cancelled", worker_id, {"after_collection": True})
            elif current["status"] == "running" and current["lease_owner"] == worker_id:
                cur.execute("UPDATE analysis_job SET status='succeeded',result=%s,result_sha256=%s,"
                            "lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() "
                            "WHERE job_id=%s", (Jsonb(result), result_hash, job_id))
                _event(cur, job_id, "succeeded", worker_id,
                       {"result_sha256": result_hash, "works": len(result.get("works", []))})
            else:
                raise JobConflict("Аренда истекла или перешла другому воркеру; результат не записан.")
            return _read_locked(cur, job_id)
    except Exception as error:
        with db.connect() as conn, conn.cursor() as cur:
            current = _read_locked(cur, job_id)
            if current["status"] in ("running", "cancel_requested") and current["lease_owner"] == worker_id:
                cur.execute("UPDATE analysis_job SET status='failed',error=%s,lease_owner=NULL,"
                            "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now() WHERE job_id=%s",
                            ((f"{type(error).__name__}: {error}")[:4000], job_id))
                _event(cur, job_id, "failed", worker_id, {"error_type": type(error).__name__})
                return _read_locked(cur, job_id)
        raise


def run_once(worker_id: str, lease_seconds: int = 300,
             collector: Callable = discover,
             full_runner: Callable | None = None) -> dict | None:
    job = claim(worker_id, lease_seconds)
    return execute(job["job_id"], worker_id, collector, full_runner,
                   lease_seconds) if job else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Воркер сохранённых задач Horizon")
    parser.add_argument("command", choices=("run-once", "worker"), default="run-once", nargs="?")
    parser.add_argument("--worker-id", default=f"worker-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    db.migrate()
    if args.command == "run-once":
        result = run_once(args.worker_id, args.lease_seconds)
        print(json.dumps(result, ensure_ascii=False, default=str) if result else "Очередь пуста.")
        return 0
    if not 0.2 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds должен быть от 0.2 до 60")
    while True:
        if run_once(args.worker_id, args.lease_seconds) is None:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
