"""Read-only trace of the customer's 19 cited arXiv papers through saved SAIA jobs.

This is an in-sample retrieval diagnostic, not weak-signal accuracy or an
independent benchmark. The customer examples informed the current vocabulary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from saia.arxiv_metadata import first_submission, matches_controlled_plan


JOBS = {
    "Индустриальный ИИ": ("940d5a1b-776e-402b-832f-ac107183e959", "6938e8ae-f0f9-4df8-9f25-7158d8ad08a6"),
    "Роботы": ("373a4346-85ac-4e9f-96fb-a47183f5d739", "02992203-dedd-4fe9-b0a8-c689e0157984"),
    "Финтех": ("e3af779f-3a51-4950-be32-fff90745bd68", "92331c22-1a75-43bc-9cde-fe88f2da732c"),
    "Защита ИИ": ("7f143336-0ecb-4602-846d-fecf39f539e4", "82f8eb26-16a1-45ab-bc8d-4e067a047be0"),
    "Edge": ("94c9638b-a4ad-4aff-a88b-35d517142c5d", "60bbc7d9-0027-495b-9cd2-27c39207d97c"),
}
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")


def _arxiv_ids(works: list[dict]) -> set[str]:
    found = set()
    for work in works:
        for value in [*(work.get("urls") or []), *(work.get("source_ids") or [])]:
            match = ARXIV_URL.search(str(value))
            if match:
                found.add(match.group(1))
    return found


def _snapshot_rows(directory: Path, targets: set[str]) -> dict[str, dict]:
    lookup = pa.array(sorted(targets))
    rows: dict[str, dict] = {}
    for path in sorted(directory.glob("*.parquet")):
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(
            batch_size=65536, columns=["id", "title", "abstract", "versions", "categories"]
        ):
            mask = pc.is_in(batch.column(0), value_set=lookup)
            if pc.any(mask).as_py():
                for row in pa.Table.from_batches([batch]).filter(mask).to_pylist():
                    rows[row["id"]] = row
    return rows


def _selected_ids(path: Path) -> set[str]:
    return set(pq.read_table(path, columns=["id"]).column("id").to_pylist())


def _stage(conn: psycopg.Connection, mission: str, arxiv_id: str,
           quality_generation: int, cluster_run: int, score_run: int,
           embedding_model: str, shown_ids: list[int]) -> dict:
    record = conn.execute(
        "SELECT w.work_id,w.canonical_title FROM identifier i "
        "JOIN work w USING(work_id) WHERE i.mission_id=%s AND i.kind='arxiv' "
        "AND i.value=%s", (mission, arxiv_id),
    ).fetchone()
    if record is None:
        return {"ingested": False}
    work_id, work_title = record
    quality = conn.execute(
        "SELECT decision,relevance_score,matched_terms,reasons FROM quality_snapshot "
        "WHERE generation_id=%s AND work_id=%s", (quality_generation, work_id),
    ).fetchone()
    embedded = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM work_embedding WHERE work_id=%s AND model=%s)",
        (work_id, embedding_model),
    ).fetchone()[0]
    topic_rows = conn.execute(
        "SELECT DISTINCT t.topic_id,t.label,s.candidate_id,s.label,s.status "
        "FROM topic_membership tm JOIN topic t ON t.topic_id=tm.topic_id "
        "LEFT JOIN signal_candidate s ON s.topic_id=t.topic_id AND s.run_id=%s "
        "WHERE tm.work_id=%s AND t.run_id=%s ORDER BY t.topic_id",
        (score_run, work_id, cluster_run),
    ).fetchall()
    topics = [{
        "topic_id": topic_id, "topic_label": topic_label,
        "candidate_id": candidate_id, "candidate_label": candidate_label,
        "candidate_status": candidate_status,
        "shown_rank": shown_ids.index(candidate_id) + 1 if candidate_id in shown_ids else None,
    } for topic_id, topic_label, candidate_id, candidate_label, candidate_status in topic_rows]
    evidence = conn.execute(
        "SELECT DISTINCT e.candidate_id FROM evidence_item e "
        "JOIN signal_candidate s USING(candidate_id) "
        "WHERE e.work_id=%s AND s.run_id=%s ORDER BY e.candidate_id",
        (work_id, score_run),
    ).fetchall()
    return {
        "ingested": True, "work_id": work_id, "work_title": work_title,
        "quality": ({"decision": quality[0], "relevance_score": quality[1],
                     "matched_terms": quality[2], "reasons": quality[3]}
                    if quality else None),
        "embedded": embedded, "topics": topics,
        "shown_as_evidence_in_candidate_ids": [item[0] for item in evidence],
    }


def _cause(item: dict) -> str:
    if not item["snapshot_present"]:
        return "absent_from_snapshot"
    if not item["in_complete_month_period"]:
        return "outside_full_analysis_period"
    if not item["full_selected"]:
        return "no_selected_exact_phrase_match" if not item["matched_full_phrases"] else "collection_mismatch"
    stage = item["stage"]
    if not stage.get("ingested"):
        return "selected_but_not_ingested"
    if not stage.get("quality"):
        return "missing_quality_snapshot"
    if stage["quality"]["decision"] != "include":
        return "excluded_or_quarantined_by_quality"
    if not stage["embedded"]:
        return "missing_embedding"
    if not stage["topics"]:
        return "cluster_noise_or_no_current_topic"
    if not any(topic["candidate_id"] is not None for topic in stage["topics"]):
        return "topic_without_candidate"
    if not any(topic["shown_rank"] is not None for topic in stage["topics"]):
        return "candidate_not_shown"
    if not stage["shown_as_evidence_in_candidate_ids"]:
        return "shown_topic_but_paper_not_in_evidence_excerpt"
    return "shown_topic_and_paper_in_evidence_excerpt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-overlap", type=Path, required=True)
    parser.add_argument("--arxiv-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dsn = os.environ.get("SAIA_DATABASE_URL")
    if not dsn:
        raise SystemExit("SAIA_DATABASE_URL is required for read-only diagnosis")
    source = json.loads(args.source_overlap.read_text(encoding="utf-8"))
    cited = [(row, identifier.removeprefix("arxiv:"))
             for row in source["rows"] for identifier in row.get("cited_arxiv_ids", [])]
    if len(cited) != 19 or len({identifier for _, identifier in cited}) != 19:
        raise SystemExit("Expected exactly 19 distinct cited arXiv identifiers")
    snapshot = _snapshot_rows(args.arxiv_dir, {identifier for _, identifier in cited})
    by_area: dict[str, dict] = {}
    with psycopg.connect(dsn) as conn:
        for area, (discovery_id, full_id) in JOBS.items():
            discovery = conn.execute(
                "SELECT status,payload,result FROM analysis_job WHERE job_id=%s", (discovery_id,),
            ).fetchone()
            full = conn.execute(
                "SELECT status,mission_id,result FROM analysis_job WHERE job_id=%s", (full_id,),
            ).fetchone()
            if not discovery or not full or discovery[0] != "succeeded" or full[0] != "succeeded":
                raise SystemExit(f"Missing completed jobs for {area}")
            package = full[2]["package"]
            if not package.startswith("/app/"):
                raise SystemExit("Unexpected package path")
            selected_path = Path(__file__).resolve().parents[1] / package.removeprefix("/app/") / "arxiv" / "selected.parquet"
            specs = discovery[1]["compiled_branch_specs"]
            if len(specs) > 1:
                full_specs = [spec for spec in specs if spec["branch_id"] != "original-query"]
            else:
                full_specs = specs
            branches = discovery[2]["branches"]
            branch_preview = {
                branch["branch_id"]: _arxiv_ids((branch.get("local_arxiv") or {}).get("works") or [])
                for branch in branches
            }
            by_area[area] = {
                "discovery_job_id": discovery_id, "full_job_id": full_id,
                "mission_id": full[1], "runs": full[2]["runs"],
                "branch_specs": full_specs,
                "branch_preview": branch_preview,
                "merged_preview": _arxiv_ids(discovery[2]["merge"]["results"]),
                "full_selected_ids": _selected_ids(selected_path),
                "shown_ids": full[2]["cards"]["candidate_ids"],
                "period_from": "2021-10-01", "period_to": "2026-08-31",
            }
        items = []
        for row, identifier in cited:
            area = row["area"]
            context = by_area[area]
            metadata = snapshot.get(identifier)
            first_date = first_submission(metadata.get("versions")) if metadata else None
            in_period = bool(first_date and context["period_from"] <= first_date <= context["period_to"])
            phrase_hits = []
            branch_matches = []
            if metadata:
                for spec in context["branch_specs"]:
                    for phrase in spec.get("included_phrases") or []:
                        if matches_controlled_plan(
                            metadata, {"included_terms": [phrase],
                                       "exclusions": spec.get("excluded_phrases") or []}
                        ):
                            phrase_hits.append(phrase)
                            branch_matches.append(spec["branch_id"])
            selected = identifier in context["full_selected_ids"]
            stage = (_stage(conn, context["mission_id"], identifier,
                            context["runs"]["quality_generation"],
                            context["runs"]["cluster"], context["runs"]["score"],
                            context["runs"]["embedding_model"], context["shown_ids"])
                     if selected else None)
            item = {
                "excel_row": row["excel_row"], "area": area,
                "customer_signal": row["title"], "arxiv_id": identifier,
                "arxiv_url": f"https://arxiv.org/abs/{identifier}",
                "article_title": metadata.get("title") if metadata else None,
                "first_submission": first_date,
                "snapshot_present": metadata is not None,
                "in_complete_month_period": in_period,
                "matched_full_phrases": sorted(set(phrase_hits)),
                "matched_full_branches": sorted(set(branch_matches)),
                "local_branch_preview": sorted(branch_id for branch_id, ids in context["branch_preview"].items() if identifier in ids),
                "merged_preview": identifier in context["merged_preview"],
                "full_selected": selected, "stage": stage,
            }
            item["cause"] = _cause(item)
            items.append(item)
    items.sort(key=lambda item: (item["area"], item["excel_row"], item["arxiv_id"]))
    funnel = {
        "in_local_snapshot": sum(item["snapshot_present"] for item in items),
        "in_complete_month_period": sum(item["in_complete_month_period"] for item in items),
        "in_merged_preview": sum(item["merged_preview"] for item in items),
        "in_full_arxiv_corpus": sum(item["full_selected"] for item in items),
        "ingested": sum(bool((item["stage"] or {}).get("ingested")) for item in items),
        "quality_included": sum(((item["stage"] or {}).get("quality") or {}).get("decision") == "include"
                                for item in items),
        "embedded": sum(bool((item["stage"] or {}).get("embedded")) for item in items),
        "assigned_to_current_topic": sum(bool((item["stage"] or {}).get("topics")) for item in items),
        "assigned_to_shown_card": sum(any(topic["shown_rank"] is not None
                                          for topic in (item["stage"] or {}).get("topics", []))
                                      for item in items),
        "listed_in_card_evidence_excerpt": sum(bool((item["stage"] or {}).get(
            "shown_as_evidence_in_candidate_ids")) for item in items),
    }
    result = {
        "method": "Exact arXiv ID trace through immutable September 2026 jobs; no semantic signal-accuracy claim",
        "count": len(items), "funnel": funnel,
        "cause_counts": dict(Counter(item["cause"] for item in items)),
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["funnel"])
    print(result["cause_counts"])
    for item in items:
        print(item["area"], item["arxiv_id"], item["cause"],
              "preview", item["merged_preview"], "corpus", item["full_selected"])


if __name__ == "__main__":
    main()
