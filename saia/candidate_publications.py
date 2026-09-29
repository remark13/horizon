"""Read-only, paginated metadata for the exact composition of a saved card."""
from __future__ import annotations

from psycopg.rows import dict_row

from saia import db
from saia.candidates import composition_sha256, source_links


def _positive_int(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} должен быть положительным целым числом.")


def validate_page(limit: int, offset: int) -> None:
    _positive_int(limit, "Лимит")
    if limit > 100:
        raise ValueError("Лимит не должен превышать 100 публикаций.")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("Смещение должно быть неотрицательным целым числом.")


def build_page(*, mission_id: str, score_run_id: int, candidate_id: int,
               work_ids: list[int], rows: list[dict], limit: int, offset: int) -> dict:
    validate_page(limit, offset)
    total = len(work_ids)
    if len(set(work_ids)) != total or not total:
        raise ValueError("Состав карточки пуст или содержит повторённые работы.")
    row_ids = [row["work_id"] for row in rows]
    if len(set(row_ids)) != len(row_ids) or not set(row_ids) <= set(work_ids):
        raise ValueError("Страница содержит повторённую или постороннюю работу.")
    if len(rows) != min(limit, max(0, total - offset)):
        raise ValueError("Число работ на странице не соответствует составу карточки.")
    works = []
    for row in rows:
        effective = row["effective_date"]
        publication = row.get("publication_date")
        works.append({
            "work_id": row["work_id"], "title": row["title"],
            "published_at": effective.isoformat() if hasattr(effective, "isoformat") else effective,
            "publication_date": publication.isoformat() if hasattr(publication, "isoformat") else publication,
            "record_type": row.get("type"), "language": row.get("language"),
            "sources": source_links([(item["kind"], item["value"])
                                      for item in row.get("identifiers", [])]),
            "primary_result_verified": None,
        })
    return {
        "version": "candidate-publication-metadata-v1",
        "mission_id": mission_id, "score_run_id": score_run_id,
        "candidate_id": candidate_id,
        "composition_sha256": composition_sha256(work_ids),
        "total": total, "limit": limit, "offset": offset,
        "next_offset": offset + len(works) if offset + len(works) < total else None,
        "works": works, "ranking_changed": False,
        "single_technology_verified": None,
    }


def for_candidate(mission_id: str, score_run_id: int, candidate_id: int,
                  limit: int = 20, offset: int = 0) -> dict:
    _positive_int(score_run_id, "Прогон")
    _positive_int(candidate_id, "Карточка")
    validate_page(limit, offset)
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT sc.topic_id FROM signal_candidate sc
                   JOIN analysis_run r ON r.run_id=sc.run_id
                   WHERE sc.candidate_id=%s AND sc.run_id=%s
                     AND r.mission_id=%s AND r.kind='score' AND r.status='done'""",
                (candidate_id, score_run_id, mission_id),
            )
            candidate = cur.fetchone()
            if candidate is None:
                raise ValueError("Карточка отсутствует в выбранном завершённом прогоне.")
            topic_id = candidate["topic_id"]
            cur.execute("SELECT DISTINCT work_id FROM topic_membership WHERE topic_id=%s ORDER BY work_id",
                        (topic_id,))
            work_ids = [row["work_id"] for row in cur.fetchall()]
            cur.execute(
                """SELECT w.work_id, w.canonical_title AS title, w.effective_date,
                          w.publication_date, w.type, w.language
                   FROM work w WHERE w.work_id=ANY(%s)
                   ORDER BY w.effective_date, w.work_id LIMIT %s OFFSET %s""",
                (work_ids, limit, offset),
            )
            rows = cur.fetchall()
            identifiers = {row["work_id"]: [] for row in rows}
            if identifiers:
                cur.execute("SELECT work_id, kind, value FROM identifier WHERE work_id=ANY(%s) "
                            "ORDER BY work_id,kind,value", (list(identifiers),))
                for item in cur.fetchall():
                    identifiers[item["work_id"]].append({"kind": item["kind"], "value": item["value"]})
            for row in rows:
                row["identifiers"] = identifiers[row["work_id"]]
    return build_page(mission_id=mission_id, score_run_id=score_run_id,
                      candidate_id=candidate_id, work_ids=work_ids, rows=rows,
                      limit=limit, offset=offset)
