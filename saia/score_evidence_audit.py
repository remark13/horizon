"""Read-only full-composition export for saved score candidates.

The export supports content review of a current-period triage queue.  It does
not assign a scientific label, alter a score run, or claim that a cluster is a
single technology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

from saia import db
from saia.candidates import export_cards
from saia.hybrid import digest
from saia.triage import build_queue


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def build_packet(cards_packet: dict, candidate_ids: list[int], rows: list[dict],
                 triage_packet: dict | None = None) -> dict:
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Нужно явно выбрать разные идентификаторы кандидатов.")
    by_card = {card["candidate_id"]: card for card in cards_packet["cards"]}
    if not set(candidate_ids) <= set(by_card):
        raise ValueError("Кандидат не принадлежит выбранному score-прогону.")
    by_candidate: dict[int, list[dict]] = {candidate_id: [] for candidate_id in candidate_ids}
    seen_pairs = set()
    for row in rows:
        candidate_id = row["candidate_id"]
        pair = (candidate_id, row["work_id"])
        if candidate_id not in by_candidate or pair in seen_pairs:
            raise ValueError("Состав содержит лишнюю или повторённую работу.")
        seen_pairs.add(pair)
        by_candidate[candidate_id].append({k: _json_value(v) for k, v in row.items()
                                           if k != "candidate_id"})
    if any(not by_candidate[candidate_id] for candidate_id in candidate_ids):
        raise ValueError("Для одного из кандидатов не найден состав публикаций.")

    triage_by_id = ({item["candidate_id"]: item for item in triage_packet["queue"]}
                    if triage_packet else {})
    cases = []
    for candidate_id in candidate_ids:
        card = by_card[candidate_id]
        works = sorted(by_candidate[candidate_id],
                       key=lambda row: (row["effective_date"], row["work_id"]))
        cases.append({
            "candidate_id": candidate_id,
            "topic_id": card.get("topic_id"),
            "composition_sha256": card.get("composition_sha256"),
            "label": card["label"],
            "status": card["status"],
            "triage": ({k: v for k, v in triage_by_id[candidate_id].items() if k != "card"}
                       if candidate_id in triage_by_id else None),
            "document_support": len(works),
            "works": works,
            "composition_review": {
                "reviewed": False,
                "single_coherent_technology": None,
                "proposed_human_title": None,
                "outlier_work_ids": [],
                "notes": None,
            },
            "limitations": [
                "Кластерная принадлежность не доказывает, что все работы описывают одну технологию.",
                "Автоматическая метка состоит из ключевых фраз и не является готовым названием тренда.",
                "Тексты отражают текущий снимок метаданных, а не обязательно версию на дату публикации.",
            ],
        })
    return {
        "version": "score-candidate-evidence-audit-0.4.9",
        "mission_id": cards_packet["mission_id"],
        "score_run_id": cards_packet["score_run_id"],
        "score_packet_sha256": digest(cards_packet),
        "triage_packet_sha256": digest(triage_packet) if triage_packet else None,
        "case_count": len(cases),
        "unique_works": len({row["work_id"] for row in rows}),
        "cases": cases,
        "scientific_accuracy_evaluated": False,
        "detector_modified_by_this_export": False,
    }


def export(mission_id: str, score_run_id: int | None = None,
           triage_limit: int = 15) -> dict:
    cards_packet = export_cards(mission_id, score_run_id)
    triage_packet = build_queue(cards_packet, triage_limit)
    candidate_ids = [item["candidate_id"] for item in triage_packet["queue"]]
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT sc.candidate_id, sc.topic_id
                   FROM signal_candidate sc
                   WHERE sc.run_id=%s AND sc.candidate_id=ANY(%s)
                   ORDER BY sc.candidate_id""",
                (cards_packet["score_run_id"], candidate_ids),
            )
            topic_by_candidate = {row["candidate_id"]: row["topic_id"] for row in cur.fetchall()}
            if set(topic_by_candidate) != set(candidate_ids):
                raise ValueError("Score-прогон изменился или содержит неполный состав кандидатов.")
            rows = []
            for candidate_id in candidate_ids:
                cur.execute(
                    """SELECT DISTINCT w.work_id, w.canonical_title AS title, w.abstract,
                              w.effective_date, w.publication_date, w.type, w.language
                       FROM topic_membership tm JOIN work w USING (work_id)
                       WHERE tm.topic_id=%s
                       ORDER BY w.effective_date, w.work_id""",
                    (topic_by_candidate[candidate_id],),
                )
                works = cur.fetchall()
                work_ids = [row["work_id"] for row in works]
                identifiers = {work_id: [] for work_id in work_ids}
                cur.execute(
                    "SELECT work_id, kind, value FROM identifier WHERE work_id=ANY(%s) "
                    "ORDER BY work_id, kind, value", (work_ids,),
                )
                for identifier in cur.fetchall():
                    identifiers[identifier["work_id"]].append({
                        "kind": identifier["kind"], "value": identifier["value"]})
                for work in works:
                    work["candidate_id"] = candidate_id
                    work["identifiers"] = identifiers[work["work_id"]]
                    work["text_sha256"] = hashlib.sha256(
                        (work["title"] + "\n" + (work["abstract"] or "")).encode()
                    ).hexdigest()
                    rows.append(work)
    result = build_packet(cards_packet, candidate_ids, rows, triage_packet)
    result["exported_at"] = datetime.now(timezone.utc).isoformat()
    result["exporter_code_bytes_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["report_payload_sha256"] = digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mission_id")
    parser.add_argument("--score-run-id", type=int)
    parser.add_argument("--triage-limit", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Файл уже существует; выберите новый версионированный путь.")
    result = export(args.mission_id, args.score_run_id, args.triage_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "cases": result["case_count"],
        "unique_works": result["unique_works"],
        "report_payload_sha256": result["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
