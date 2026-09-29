"""Append-only links from a frozen signal candidate to one observed external record.

An analyst's relevance choice is not independent expert validation and never
changes the publication-based score. The saved external observation is the
source of truth for the linked record, not its free-text topic_id.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from saia import candidates, db, external_evidence_store


ASSESSMENTS = {"relevant_to_topic", "background_only"}


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except (TypeError, ValueError):
        raise ValueError("Некорректный ключ связи с источником.") from None


def _candidate(mission_id: str, score_run_id: int, candidate_id: int) -> dict:
    if not isinstance(score_run_id, int) or isinstance(score_run_id, bool) or score_run_id <= 0:
        raise ValueError("Укажите завершённый прогон оценки.")
    if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
        raise ValueError("Укажите карточку сигнала.")
    packet = candidates.export_cards(mission_id, score_run_id)
    match = next((card for card in packet["cards"]
                  if card["candidate_id"] == candidate_id), None)
    if match is None:
        raise ValueError("Карточка отсутствует в выбранном прогоне.")
    return match


def prepare(mission_id: str, score_run_id: int, candidate_id: int,
            observation_id: str, record_url: str, assessment: str,
            linked_by: str, rationale: str) -> dict:
    """Resolve an exact saved record and exact candidate without writing."""
    card = _candidate(mission_id, score_run_id, candidate_id)
    if assessment not in ASSESSMENTS:
        raise ValueError("Укажите оценку связи материала с темой.")
    actor, reason = linked_by.strip(), rationale.strip()
    if not 1 <= len(actor) <= 120 or not 20 <= len(reason) <= 2000:
        raise ValueError("Укажите аналитика и обоснование связи (20–2000 символов).")
    if not isinstance(record_url, str) or not 10 <= len(record_url) <= 2048:
        raise ValueError("Укажите ссылку на отдельную запись источника.")
    parsed = urlsplit(record_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("Ссылка должна вести на отдельную запись источника.")
    observation = external_evidence_store.read(_uuid(observation_id))
    if observation["status"] != "complete":
        raise ValueError("Связывать можно только записи из успешно полученного ответа источника.")
    records = observation["payload"].get("observations")
    if not isinstance(records, list):
        raise ValueError("В сохранённом ответе нет отдельных записей.")
    matches = [record for record in records
               if isinstance(record, dict) and record.get("url") == record_url]
    if len(matches) != 1:
        raise ValueError("Ссылка не совпала с отдельной записью сохранённого источника.")
    if matches[0].get("original_record_url_available") is False:
        raise ValueError("Источник не предоставил отдельную ссылку на запись. Сообщение нельзя привязать как первичный материал.")
    return {
        "mission_id": mission_id, "score_run_id": score_run_id,
        "candidate_id": candidate_id,
        "composition_sha256": card["composition_sha256"],
        "observation_id": observation["observation_id"],
        "observation_sha256": observation["report_payload_sha256"],
        "source": observation["source"], "role": observation["role"],
        "record_url": record_url, "record_snapshot": matches[0],
        "assessment": assessment, "linked_by": actor, "rationale": reason,
        "scientific_score_modified": False,
    }


def link(mission_id: str, score_run_id: int, candidate_id: int,
         observation_id: str, record_url: str, assessment: str,
         linked_by: str, rationale: str, operation_id: str | None = None) -> dict:
    entry = prepare(mission_id, score_run_id, candidate_id, observation_id,
                    record_url, assessment, linked_by, rationale)
    link_id = _uuid(operation_id)
    fields = ("mission_id", "score_run_id", "candidate_id", "composition_sha256",
              "observation_id", "observation_sha256", "source", "role",
              "record_url", "record_snapshot", "assessment", "linked_by",
              "rationale", "scientific_score_modified")
    expected = tuple(entry[field] for field in fields)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO candidate_external_evidence_link "
            "(link_id,mission_id,score_run_id,candidate_id,composition_sha256,"
            "observation_id,observation_sha256,source,role,record_url,record_snapshot,"
            "assessment,linked_by,rationale,scientific_score_modified) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING created_at",
            (link_id, *expected[:9], Jsonb(entry["record_snapshot"]), *expected[10:]),
        )
        inserted = cur.fetchone()
        deduplicated_by_record = False
        if inserted:
            created = inserted[0]
        else:
            selected = ",".join(fields) + ",created_at,link_id"
            cur.execute(f"SELECT {selected} FROM candidate_external_evidence_link "
                        "WHERE link_id=%s", (link_id,))
            old = cur.fetchone()
            if old is None:
                cur.execute(f"SELECT {selected} FROM candidate_external_evidence_link "
                            "WHERE candidate_id=%s AND record_url=%s",
                            (candidate_id, record_url))
                old = cur.fetchone()
                deduplicated_by_record = old is not None
            actual = list(old[:len(fields)]) if old is not None else []
            if actual:
                actual[4] = str(actual[4])
            if not actual or tuple(actual) != expected:
                raise ValueError("Этот материал уже связан иначе либо ключ использован повторно.")
            created, link_id = old[-2], str(old[-1])
    return {"link_id": link_id, **entry, "created_at": created.isoformat(),
            "replayed": inserted is None,
            "deduplicated_by_record": deduplicated_by_record,
            "validation_status": "analyst_selected_not_expert_validated"}


def for_candidate(mission_id: str, score_run_id: int, candidate_id: int, *, card: dict | None = None) -> dict:
    card = card or _candidate(mission_id, score_run_id, candidate_id)
    if card.get("candidate_id", candidate_id) != candidate_id:
        raise ValueError("Карточка не соответствует идентификатору связи.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT link_id,composition_sha256,observation_id,source,role,"
            "record_url,record_snapshot,assessment,linked_by,rationale,created_at "
            "FROM candidate_external_evidence_link "
            "WHERE mission_id=%s AND score_run_id=%s AND candidate_id=%s "
            "ORDER BY created_at DESC,link_id DESC",
            (mission_id, score_run_id, candidate_id),
        )
        rows = cur.fetchall()
    if any(row[1] != card["composition_sha256"] for row in rows):
        raise ValueError("Состав карточки изменился; внешние материалы требуют повторной привязки.")
    links = [{"link_id": str(row[0]), "observation_id": str(row[2]),
              "source": row[3], "role": row[4], "record_url": row[5],
              "record_snapshot": row[6], "assessment": row[7],
              "linked_by": row[8], "rationale": row[9],
              "created_at": row[10].isoformat(),
              "validation_status": "analyst_selected_not_expert_validated"}
             for row in rows]
    return {"mission_id": mission_id, "score_run_id": score_run_id,
            "candidate_id": candidate_id,
            "composition_sha256": card["composition_sha256"],
            "links": links, "count": len(links),
            "scientific_score_modified": False}
