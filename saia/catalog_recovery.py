"""Post-hoc collection and topic recovery for development catalog families.

The catalog is joined only to completed runs.  It never modifies collection,
embeddings, clustering, or scores.  Reserved cases are excluded.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from saia import db, evaluation_catalog, runs
from saia.benchmark import identifier_url


REPORT_VERSION = "catalog-topic-recovery-0.4.18"


def summarize_case(case: dict, rows: list[dict], topic_sizes: dict[int, int]) -> dict:
    by_id = {row["arxiv_id"]: row for row in rows}
    ordered = [by_id.get(identifier, {
        "arxiv_id": identifier,
        "found_in_corpus": False,
        "source_url": identifier_url("arxiv", identifier),
    }) for identifier in case["arxiv_ids"]]
    found = [row for row in ordered if row.get("found_in_corpus")]
    included = [row for row in found if row.get("quality_decision") == "include"]
    assigned = [row for row in included if row.get("topic_id") is not None]
    counts = Counter(row["topic_id"] for row in assigned)
    dominant_topic, dominant_count = (counts.most_common(1)[0] if counts else (None, 0))
    dominant_row = next((row for row in assigned if row["topic_id"] == dominant_topic), None)
    target_count = len(ordered)
    dominant_size = topic_sizes.get(dominant_topic) if dominant_topic is not None else None
    return {
        "case_id": case["case_id"],
        "family_id": case["family_id"],
        "title": case["title"],
        "kind": case["kind"],
        "target_references": target_count,
        "found_in_corpus": len(found),
        "included_after_quality": len(included),
        "assigned_to_topic": len(assigned),
        "collection_recall": round(len(found) / target_count, 4),
        "quality_survival": round(len(included) / target_count, 4),
        "topic_assignment_recall": round(len(assigned) / target_count, 4),
        "max_in_one_topic": dominant_count,
        "dominant_topic_id": dominant_topic,
        "dominant_topic_label": dominant_row.get("topic_label") if dominant_row else None,
        "dominant_candidate_status": dominant_row.get("candidate_status") if dominant_row else None,
        "dominant_recall": round(dominant_count / target_count, 4),
        "dominant_target_share": (
            round(dominant_count / dominant_size, 4) if dominant_size else None
        ),
        "multi_reference_topic_recovered": (
            dominant_count >= 2 if target_count >= 2 else None
        ),
        "publications": ordered,
    }


def evaluate(mission_id: str, score_run_id: int, catalog_path: Path,
             as_of_date: str) -> dict:
    catalog = evaluation_catalog.load(catalog_path)
    cases, _identifiers = evaluation_catalog.summary(catalog), None
    selected_cases = [item for item in catalog["cases"]
                      if item["split"] == "development"
                      and item["as_of_date"] == as_of_date
                      and item["kind"] in {
                          "research_line_positive_proposal", "ambiguous_line"}]
    if not selected_cases:
        raise ValueError("Нет development-семейств для указанной даты.")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT upstream_run_id,query_version_id,as_of_date FROM analysis_run "
            "WHERE run_id=%s AND mission_id=%s AND kind='score' AND status='done'",
            (score_run_id, mission_id),
        )
        score = cur.fetchone()
        if not score or score[2].isoformat() != as_of_date:
            raise ValueError("Score отсутствует или имеет другую дату среза.")
        cluster_run_id, query_version_id, _ = score
        cur.execute(
            "SELECT upstream_run_id,embedding_model,notes FROM analysis_run "
            "WHERE run_id=%s AND mission_id=%s AND kind='cluster' AND status='done'",
            (cluster_run_id, mission_id),
        )
        cluster = cur.fetchone()
        if not cluster:
            raise ValueError("Кластерный вход score не найден.")
        normalize_run_id, model, cluster_notes = cluster
        generation_id = (cluster_notes or {}).get("quality_generation_id")
        if not generation_id:
            raise ValueError("Кластер не закрепил поколение качества.")
        cur.execute(
            "SELECT t.topic_id,count(*) FROM topic t JOIN topic_membership tm USING(topic_id) "
            "WHERE t.run_id=%s GROUP BY t.topic_id",
            (cluster_run_id,),
        )
        topic_sizes = {topic_id: count for topic_id, count in cur.fetchall()}
        rows_by_id = {}
        for identifier in sorted({identifier for case in selected_cases
                                  for identifier in case["arxiv_ids"]}):
            cur.execute(
                """
                SELECT w.work_id,w.canonical_title,w.effective_date,q.decision,q.flags,
                       topic.topic_id,topic.label,sc.status
                FROM identifier i JOIN work w USING(work_id)
                LEFT JOIN quality_snapshot q
                  ON q.work_id=w.work_id AND q.generation_id=%s
                LEFT JOIN LATERAL (
                    SELECT t.topic_id,t.label
                    FROM topic_membership tm JOIN topic t USING(topic_id)
                    WHERE tm.work_id=w.work_id AND t.run_id=%s
                    ORDER BY tm.window_key DESC LIMIT 1
                ) topic ON true
                LEFT JOIN signal_candidate sc
                  ON sc.topic_id=topic.topic_id AND sc.run_id=%s
                WHERE i.run_id=%s AND i.kind='arxiv' AND lower(i.value)=%s
                LIMIT 1
                """,
                (generation_id, cluster_run_id, score_run_id,
                 normalize_run_id, identifier.casefold()),
            )
            row = cur.fetchone()
            if not row:
                rows_by_id[identifier] = {
                    "arxiv_id": identifier, "found_in_corpus": False,
                    "source_url": identifier_url("arxiv", identifier),
                }
                continue
            work_id, title, effective, decision, flags, topic_id, label, status = row
            rows_by_id[identifier] = {
                "arxiv_id": identifier, "found_in_corpus": True,
                "work_id": work_id, "title": title,
                "effective_date": effective.isoformat(),
                "quality_decision": decision,
                "quality_flags": flags or {},
                "topic_id": topic_id, "topic_label": label,
                "candidate_status": status,
                "source_url": identifier_url("arxiv", identifier),
            }
    case_results = [summarize_case(
        case, [rows_by_id[identifier] for identifier in case["arxiv_ids"]],
        topic_sizes,
    ) for case in selected_cases]
    multi = [item for item in case_results if item["target_references"] >= 2]
    return {
        "version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mission_id": mission_id,
        "query_version_id": query_version_id,
        "as_of_date": as_of_date,
        "runs": {"normalize": normalize_run_id, "quality_generation": generation_id,
                 "cluster": cluster_run_id, "score": score_run_id,
                 "embedding_model": model},
        "catalog": cases,
        "evaluated_split": "development",
        "reserved_evaluated": False,
        "cases": case_results,
        "summary": {
            "cases": len(case_results),
            "unique_references": len(rows_by_id),
            "references_found": sum(item["found_in_corpus"] for item in case_results),
            "references_included": sum(item["included_after_quality"] for item in case_results),
            "references_assigned": sum(item["assigned_to_topic"] for item in case_results),
            "multi_reference_cases": len(multi),
            "multi_reference_topics_recovered": sum(
                item["multi_reference_topic_recovered"] is True for item in multi),
            "all_cases_are_ground_truth": False,
            "precision_measured": False,
            "weak_signal_detection_measured": False,
        },
        "limitations": [
            "Кейсы являются development proposals, а не экспертной gold-разметкой.",
            "Попадание двух ссылок в одну тему проверяет recovery, но не статус слабого сигнала.",
            "Reserved-кейсы не оценивались; существующий каталог не является независимо ослеплённым holdout.",
            "Результат зависит от current-metadata reconstruction и политики карантина поздних редакций.",
        ],
        "code_version": runs.code_version(),
        "runtime": runs.runtime_snapshot(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-hoc recovery development-каталога")
    parser.add_argument("mission_id")
    parser.add_argument("--score-run", required=True, type=int)
    parser.add_argument("--catalog", type=Path, default=evaluation_catalog.CATALOG_PATH)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--export", required=True, type=Path)
    args = parser.parse_args()
    if args.export.exists():
        raise ValueError("Отчёт уже существует и не перезаписывается.")
    report = evaluate(args.mission_id, args.score_run, args.catalog.resolve(), args.as_of)
    args.export.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
