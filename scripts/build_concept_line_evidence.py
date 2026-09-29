"""Frozen, auditable arXiv evidence for a mechanism AND task concept line.

The output is a retrieval card and complete metadata inventory, not a weak-
signal verdict.  In particular the current abstract may be newer than v1.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search, supports_concept_plan


VERSION = "concept-line-evidence-plan-v1"
REPORT_VERSION = "concept-line-evidence-card-v1"


def _year_window(first_date: str) -> int:
    return int(first_date[:4]) - (first_date[5:7] < "09")


def _read_rows(connection: sqlite3.Connection, ids: list[str]) -> list[dict]:
    rows: list[dict] = []
    for offset in range(0, len(ids), 400):
        batch = ids[offset:offset + 400]
        marks = ",".join("?" for _ in batch)
        rows.extend(dict(row) for row in connection.execute(
            "SELECT arxiv_id,title,abstract,first_submission_date,"
            "snapshot_update_date,categories,versions_json,doi "
            f"FROM works WHERE arxiv_id IN ({marks})", batch))
    if len(rows) != len(ids):
        raise ValueError("Indexed result IDs could not all be materialized")
    return sorted(rows, key=lambda item: (item["first_submission_date"],
                                          item["arxiv_id"]))


def _annual(connection: sqlite3.Connection, rows: list[dict],
            start: date, cutoff: date, background: str) -> list[dict]:
    year_expr = ("CAST(substr(first_submission_date,1,4) AS INTEGER) "
                 "- CASE WHEN substr(first_submission_date,6,2)<'09' "
                 "THEN 1 ELSE 0 END")
    background_counts = {int(year): count for year, count in connection.execute(
        f"SELECT {year_expr} AS y,count(*) FROM works "
        "WHERE first_submission_date>=? AND first_submission_date<? "
        "AND instr(' ' || coalesce(categories,'') || ' ', ?) > 0 GROUP BY y",
        (start.isoformat(), cutoff.isoformat(), f" {background} "))}
    counts = Counter(_year_window(row["first_submission_date"]) for row in rows)
    background_line = Counter(_year_window(row["first_submission_date"])
                              for row in rows if background in
                              str(row["categories"] or "").split())
    years = []
    for year in range(start.year, cutoff.year):
        numerator = background_line[year]
        denominator = background_counts.get(year, 0)
        years.append({
            "from": f"{year}-09-01", "to_exclusive": f"{year + 1}-09-01",
            "all_exact_matches": counts[year],
            "line_in_background": numerator,
            "background_unique_arxiv_ids": denominator,
            "share_per_10000_background": (
                round(10000 * numerator / denominator, 3) if denominator else None),
        })
    if sum(row["all_exact_matches"] for row in years) != len(rows):
        raise ValueError("Annual counts do not reconcile to evidence inventory")
    return years


def build(config_path: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    start = date.fromisoformat(config["date_from"])
    cutoff = date.fromisoformat(config["as_of_date"])
    if (config.get("version") != VERSION or start >= cutoff
            or (start.month, start.day) != (9, 1)
            or (cutoff.month, cutoff.day) != (9, 1)
            or not 2 <= cutoff.year - start.year <= 15
            or config.get("background_category") != "cs.RO"
            or not 1 <= config.get("max_matches", 0) <= 5000
            or not supports_concept_plan(config)):
        raise ValueError("Unsupported frozen concept-line plan")
    manifest_path = root / config["source"]["index_manifest"]
    if sha256_file(manifest_path) != config["source"]["index_manifest_sha256"]:
        raise ValueError("Pinned arXiv manifest changed")
    index_dir = manifest_path.parent
    found = exact_concept_search(index_dir, config,
                                 max_matches=config["max_matches"])
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                                 uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = _read_rows(connection, found["arxiv_ids"])
        annual = _annual(connection, rows, start, cutoff,
                         config["background_category"])
    finally:
        connection.close()
    works = []
    for row in rows:
        versions = json.loads(row["versions_json"])
        works.append({
            "arxiv_id": row["arxiv_id"],
            "url": f"https://arxiv.org/abs/{row['arxiv_id']}",
            "title": row["title"], "abstract": row["abstract"],
            "first_submission_date": row["first_submission_date"],
            "snapshot_update_date": row["snapshot_update_date"],
            "versions_in_current_snapshot": len(versions),
            "doi": row["doi"], "categories": row["categories"],
            "in_background": config["background_category"] in
            str(row["categories"] or "").split(),
            "article_role": "unreviewed",
            "mechanism_match_review": "unreviewed",
        })
    return {
        "version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "source_manifest_sha256": config["source"]["index_manifest_sha256"],
        "line_id": config["line_id"], "title_ru": config["title_ru"],
        "selection_note": config["selection_note"],
        "search_predicate": {"concept_groups": config["concept_groups"],
                             "exclusions": config["exclusions"],
                             "matching_version": config["matching_version"]},
        "period": {"date_from": config["date_from"],
                   "as_of_date_exclusive": config["as_of_date"],
                   "window": "complete September-August years"},
        "source": "complete pinned local arXiv current metadata",
        "source_audit": found["audit"],
        "all_exact_matches": len(works),
        "background_category": config["background_category"],
        "annual": annual, "works": works,
        "primary_results_verified": False,
        "historical_first_mention_verified": False,
        "independent_team_diffusion_measured": False,
        "weak_signal_confirmed": False,
        "status": "research_line_candidate_needs_evidence_review",
        "limitations": config["limitations"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Evidence report is immutable; choose a new path")
    result = build(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"line_id": result["line_id"],
                      "all_exact_matches": result["all_exact_matches"],
                      "annual": [row["all_exact_matches"] for row in result["annual"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
