"""Append-only PostgreSQL storage for bounded patent and news observations."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.hybrid import digest
from saia.news_publishers import publishers


SOURCES = {
    "gdelt_doc_2_0": "news_attention_only",
    "epo_ops": "patent_landscape_only",
    "nih_reporter": "research_funding_only",
    "deps_dev": "software_diffusion_only",
    "semantic_scholar": "bibliographic_enrichment_only",
    "ukri_gtr": "research_funding_only",
    "eu_funding_tenders": "research_programme_only",
    "huggingface_hub": "ai_artifact_diffusion_only",
    "clinicaltrials_gov": "clinical_translation_only",
    "europe_pmc": "bibliographic_enrichment_only",
    "nasa_ntrs": "bibliographic_enrichment_only",
    "usaspending": "public_procurement_only",
    "mit_news_rss": "news_attention_only",
    "nasa_news_rss": "news_attention_only",
    "jpl_news_rss": "news_attention_only",
    "nsf_awards": "research_funding_only",
    "datacite": "research_artifact_only",
    "openaire_projects": "research_funding_only",
    "osti_gov": "bibliographic_enrichment_only",
    "nist_news_rss": "news_attention_only",
    "aist_press_rss": "news_attention_only",
    "dealroom_marketmaps": "company_landscape_only",
    "dealroom_public_rounds": "investment_lead_only",
    "event_registry": "news_attention_only",
    "mediacloud_news": "news_attention_only",
    "lens_patents": "patent_landscape_only",
    "google_news_rss": "news_attention_only",
}
SOURCES.update({identifier: "news_attention_only" for identifier in publishers()})


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except ValueError:
        raise ValueError("Ключ сохранения должен быть UUID.") from None


def verify(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("External evidence payload must be an object.")
    source, role = payload.get("source"), payload.get("role")
    if source not in SOURCES or role != SOURCES[source]:
        raise ValueError("Неизвестная пара source/role внешнего доказательства.")
    if not isinstance(payload.get("topic_id"), str) or not payload["topic_id"].strip():
        raise ValueError("У внешнего доказательства нет topic ID.")
    if payload.get("scientific_score_modified") is not False:
        raise ValueError("Внешнее доказательство не может менять scientific score.")
    if payload.get("missing_is_zero") is not False:
        raise ValueError("Неизвестность нельзя сохранять как ноль.")
    if source in publishers() and (
            payload.get("publisher_filter_domain") != publishers()[source]["domain"]
            or payload.get("publisher_backend") != "google_news_rss"
            or payload.get("publisher_directory_version") != "news-publisher-directory-v1"
            or payload.get("usage_scope") != "personal_research"
            or any(payload.get(key) is not False for key in (
                "public_republication_allowed", "model_input_allowed", "model_training_allowed", "bulk_reuse_approved", "stores_full_text", "stores_images"))):
        raise ValueError("Новостной издатель подключён только для ограниченного просмотра метаданных.")
    if source == "osti_gov" and any(payload.get(key) is not False for key in (
            "model_input_allowed", "model_training_allowed", "bulk_reuse_approved")):
        raise ValueError("OSTI не допущен как корпус, вход модели или обучающие данные.")
    if source == "aist_press_rss" and (payload.get("usage_scope") != "personal_research" or any(
            payload.get(key) is not False for key in (
                "public_republication_allowed", "model_input_allowed", "model_training_allowed", "bulk_reuse_approved", "model_inputs_modified"))):
        raise ValueError("AIST подключён только как личный локальный RSS-читатель.")
    checksum = payload.get("report_payload_sha256")
    canonical = dict(payload)
    canonical.pop("report_payload_sha256", None)
    if not isinstance(checksum, str) or checksum != digest(canonical):
        raise ValueError("Контрольная сумма внешнего доказательства не совпала.")
    if not isinstance(payload.get("status"), str) or not payload["status"]:
        raise ValueError("У внешнего доказательства нет статуса.")
    return payload


def record(payload: dict, operation_id: str | None = None) -> dict:
    value = verify(payload)
    identifier = _uuid(operation_id)
    expected = (value["source"], value["role"], value["topic_id"], value["status"],
                value["report_payload_sha256"], value)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO external_evidence_observation "
            "(observation_id,source,role,topic_id,status,report_payload_sha256,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING created_at",
            (identifier, *expected[:5], Jsonb(value)),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        deduplicated_by_content = False
        if inserted:
            created = inserted[0]
        else:
            cur.execute(
                "SELECT observation_id,source,role,topic_id,status,report_payload_sha256,payload,created_at "
                "FROM external_evidence_observation WHERE observation_id=%s", (identifier,),
            )
            old = cur.fetchone()
            if old is None:
                cur.execute(
                    "SELECT observation_id,source,role,topic_id,status,report_payload_sha256,payload,created_at "
                    "FROM external_evidence_observation WHERE source=%s AND report_payload_sha256=%s",
                    (value["source"], value["report_payload_sha256"]),
                )
                old = cur.fetchone()
                deduplicated_by_content = old is not None
            if old is None:
                raise ValueError("Конфликт внешнего доказательства не удалось сопоставить.")
            if str(old[0]) == identifier and tuple(old[1:7]) != expected:
                raise ValueError("Этот ключ уже использован для другого внешнего доказательства.")
            identifier, created, value = str(old[0]), old[7], verify(old[6])
    return {"observation_id": identifier, "source": value["source"],
            "topic_id": value["topic_id"], "status": value["status"],
            "report_payload_sha256": value["report_payload_sha256"],
            "created_at": created.isoformat(), "replayed": replayed,
            "deduplicated_by_content": deduplicated_by_content,
            "scientific_score_modified": False}


def history(topic_id: str | None = None, source: str | None = None,
            limit: int = 100) -> dict:
    if source is not None and source not in SOURCES:
        raise ValueError("Неизвестный внешний источник.")
    if not 1 <= limit <= 500:
        raise ValueError("Лимит истории должен быть от 1 до 500.")
    clauses, params = [], []
    if topic_id:
        clauses.append("topic_id=%s")
        params.append(topic_id)
    if source:
        clauses.append("source=%s")
        params.append(source)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM external_evidence_observation" + where, params)
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT observation_id,source,role,topic_id,status,report_payload_sha256,created_at "
            "FROM external_evidence_observation" + where +
            " ORDER BY created_at DESC,observation_id DESC LIMIT %s", (*params, limit),
        )
        rows = [{"observation_id": str(identifier), "source": row_source, "role": role,
                 "topic_id": row_topic, "status": status,
                 "report_payload_sha256": checksum, "created_at": created.isoformat()}
                for identifier, row_source, role, row_topic, status, checksum, created in cur.fetchall()]
    return {"total": total, "returned": len(rows), "observations": rows,
            "scientific_score_modified": False}


def read(observation_id: str) -> dict:
    identifier = _uuid(observation_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source,role,topic_id,status,report_payload_sha256,payload,created_at "
            "FROM external_evidence_observation WHERE observation_id=%s", (identifier,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("Сохранённое внешнее доказательство не найдено.")
    payload = verify(row[5])
    expected = (payload["source"], payload["role"], payload["topic_id"],
                payload["status"], payload["report_payload_sha256"])
    if tuple(row[:5]) != expected:
        raise ValueError("Колонки внешнего доказательства не совпали с payload.")
    return {"observation_id": identifier, "source": row[0], "role": row[1],
            "topic_id": row[2], "status": row[3],
            "report_payload_sha256": row[4], "created_at": row[6].isoformat(),
            "payload": payload, "scientific_score_modified": False}
