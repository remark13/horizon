"""Frozen full-index time series for three post-discovery robotics line probes.

Counts are literal mentions in a pinned arXiv snapshot, not confirmed signals.
The parent predicate and exact source hashes are checked before any analysis.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase
from saia.arxiv_trigram_index import exact_search
from saia.broad_title_phrases import collect_parent
from saia.controlled_collection import sha256_file


PLAN_VERSION = "robotic-narrow-line-temporal-plan-v1"
REPORT_VERSION = "robotic-narrow-line-temporal-diagnostic-v1"


def _window_year(value: str) -> int:
    return int(value[:4]) - (value[5:7] < "09")


def _share(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 9) if denominator else None


def _fetch_rows(conn: sqlite3.Connection, identifiers: set[str]) -> list[dict]:
    rows = []
    ordered = sorted(identifiers)
    for offset in range(0, len(ordered), 400):
        batch = ordered[offset:offset + 400]
        placeholders = ",".join("?" for _ in batch)
        for item in conn.execute(
                "SELECT arxiv_id,first_submission_date,title,abstract,categories,"
                "snapshot_update_date,versions_json FROM works "
                f"WHERE arxiv_id IN ({placeholders})", batch):
            rows.append(dict(item))
    if len(rows) != len(identifiers):
        raise ValueError("Indexed IDs disappeared during temporal lookup")
    return rows


def _paper(row: dict) -> dict:
    return {
        "arxiv_id": row["arxiv_id"],
        "url": f"https://arxiv.org/abs/{row['arxiv_id']}",
        "first_submission_date": row["first_submission_date"],
        "title": row["title"], "categories": row["categories"],
    }


def build(config_path: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    period = config.get("period") or {}
    start = date.fromisoformat(period["date_from"])
    cutoff = date.fromisoformat(period["as_of_date"])
    if (config.get("version") != PLAN_VERSION
            or config.get("matching_version") != ORTHOGRAPHIC_MATCHING_VERSION
            or config.get("alternative_background_category") != "cs.RO"
            or len(config.get("lines") or []) != 3
            or start >= cutoff
            or (start.month, start.day) != (9, 1)
            or (cutoff.month, cutoff.day) != (9, 1)
            or not 2 <= cutoff.year - start.year <= 15):
        raise ValueError("Unexpected frozen robotics line plan")
    source = config["source"]
    manifest_path = root / source["index_manifest"]
    prior_plan_path = root / source["prior_parent_plan"]
    prior_path = root / source["prior_parent_report"]
    if (sha256_file(manifest_path) != source["index_manifest_sha256"]
            or sha256_file(prior_plan_path) != source["prior_parent_plan_sha256"]
            or sha256_file(prior_path) != source["prior_parent_report_sha256"]):
        raise ValueError("Pinned index or parent report changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prior_plan = json.loads(prior_plan_path.read_text(encoding="utf-8"))
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    if (prior_plan["parent_terms"] != config["parent_terms"]
            or prior_plan["date_from"] != start.isoformat()
            or prior_plan["as_of_date_exclusive"] != cutoff.isoformat()
            or prior["config_sha256"] != source["prior_parent_plan_sha256"]):
        raise ValueError("Parent predicate differs from prior robotics pilot")
    index_dir = manifest_path.parent
    common = {"matching_version": config["matching_version"],
              "date_from": start.isoformat(), "as_of_date": cutoff.isoformat(),
              "exclusions": []}
    parent_rows, parent_audit = collect_parent(index_dir, prior_plan)
    parent_ids = {row["arxiv_id"] for row in parent_rows}
    if len(parent_ids) != prior["parent_collection"]["selected_unique_ids"]:
        raise ValueError("Full-index parent does not reproduce prior robotics pilot")
    line_searches = []
    ids_needed = set(parent_ids)
    for line in config["lines"]:
        search = exact_search(index_dir, {**common, "included_terms": line["terms"]})
        line_ids = set(search["arxiv_ids"])
        line_searches.append((line, search["audit"], line_ids))
        ids_needed.update(line_ids)
    conn = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                           uri=True)
    conn.row_factory = sqlite3.Row
    try:
        records = {row["arxiv_id"]: row for row in _fetch_rows(conn, ids_needed)}
        window_expr = ("CAST(substr(first_submission_date,1,4) AS INTEGER) "
                       "- CASE WHEN substr(first_submission_date,6,2)<'09' "
                       "THEN 1 ELSE 0 END")
        cs_ro_by_window = {
            int(year): count for year, count in conn.execute(
                f"SELECT {window_expr} AS window_year,count(*) FROM works "
                "WHERE first_submission_date>=? AND first_submission_date<? "
                "AND instr(' ' || coalesce(categories,'') || ' ', ' cs.RO ')>0 "
                "GROUP BY window_year", (start.isoformat(), cutoff.isoformat()))
        }
        parent_by_window = Counter(
            _window_year(records[identifier]["first_submission_date"])
            for identifier in parent_ids)
        windows = [{
            "from": f"{year}-09-01", "to_exclusive": f"{year + 1}-09-01",
            "parent_unique_arxiv_ids": parent_by_window[year],
            "cs_ro_unique_arxiv_ids": cs_ro_by_window.get(year, 0),
        } for year in range(start.year, cutoff.year)]
        if [row["parent_unique_arxiv_ids"] for row in windows] != prior[
                "parent_annual_counts"]:
            raise ValueError("Annual parent differs from prior robotics pilot")
        lines = []
        for line, audit, all_ids in line_searches:
            in_parent = all_ids & parent_ids
            in_cs_ro = {identifier for identifier in all_ids
                        if "cs.RO" in str(records[identifier]["categories"] or "").split()}
            selected = sorted((records[identifier] for identifier in in_parent),
                              key=lambda row: (row["first_submission_date"],
                                               row["arxiv_id"]))
            cs_ro_selected = sorted((records[identifier] for identifier in in_cs_ro),
                                    key=lambda row: (row["first_submission_date"],
                                                     row["arxiv_id"]))
            cs_ro_title_anchored = [
                row for row in cs_ro_selected
                if any(_matches_phrase(row["title"] or "", term,
                                       config["matching_version"])
                       for term in line["terms"])]
            by_window = Counter(_window_year(row["first_submission_date"])
                                for row in selected)
            cs_ro_line_by_window = Counter(_window_year(row["first_submission_date"])
                                           for row in cs_ro_selected)
            annual = []
            for window in windows:
                year = int(window["from"][:4])
                count = by_window[year]
                annual.append({
                    "from": window["from"], "to_exclusive": window["to_exclusive"],
                    "line_in_parent_unique_arxiv_ids": count,
                    "parent_unique_arxiv_ids": window["parent_unique_arxiv_ids"],
                    "share_of_parent": _share(count, window["parent_unique_arxiv_ids"]),
                    "share_per_10000_parent": (
                        round(10000 * count / window["parent_unique_arxiv_ids"], 3)
                        if window["parent_unique_arxiv_ids"] else None),
                    "line_in_cs_ro_unique_arxiv_ids": cs_ro_line_by_window[year],
                    "line_in_cs_ro_with_term_in_title": sum(
                        _window_year(row["first_submission_date"]) == year
                        for row in cs_ro_title_anchored),
                    "cs_ro_unique_arxiv_ids": window["cs_ro_unique_arxiv_ids"],
                    "share_per_10000_cs_ro": (
                        round(10000 * cs_ro_line_by_window[year] /
                              window["cs_ro_unique_arxiv_ids"], 3)
                        if window["cs_ro_unique_arxiv_ids"] else None),
                    "current_metadata_updated_after_window": sum(
                        bool(row["snapshot_update_date"] and
                             row["snapshot_update_date"] >= window["to_exclusive"])
                        for row in selected
                        if _window_year(row["first_submission_date"]) == year),
                    "multiple_preprint_versions_in_current_snapshot": sum(
                        len(json.loads(row["versions_json"])) > 1
                        for row in selected
                        if _window_year(row["first_submission_date"]) == year),
                    "cs_ro_current_metadata_updated_after_window": sum(
                        bool(row["snapshot_update_date"] and
                             row["snapshot_update_date"] >= window["to_exclusive"])
                        for row in cs_ro_selected
                        if _window_year(row["first_submission_date"]) == year),
                    "cs_ro_multiple_preprint_versions_in_current_snapshot": sum(
                        len(json.loads(row["versions_json"])) > 1
                        for row in cs_ro_selected
                        if _window_year(row["first_submission_date"]) == year),
                })
            lines.append({
                "id": line["id"], "name_ru": line["name_ru"],
                "role": line["role"], "terms": line["terms"],
                "all_arxiv_phrase_mentions": len(all_ids),
                "line_in_parent_unique_arxiv_ids": len(in_parent),
                "line_outside_parent_unique_arxiv_ids": len(all_ids - parent_ids),
                "line_in_cs_ro_unique_arxiv_ids": len(in_cs_ro),
                "line_in_cs_ro_with_term_in_title": len(cs_ro_title_anchored),
                "line_in_cs_ro_outside_phrase_parent": len(in_cs_ro - parent_ids),
                "search_audit": audit, "annual": annual,
                "first_parent_papers": [_paper(row) for row in selected[:10]],
                "latest_parent_papers": [_paper(row) for row in selected[-10:]],
                "first_cs_ro_papers": [_paper(row) for row in cs_ro_selected[:10]],
                "first_cs_ro_title_anchored_papers": [
                    _paper(row) for row in cs_ro_title_anchored[:10]],
                "latest_cs_ro_papers": [_paper(row) for row in cs_ro_selected[-10:]],
                "primary_result_verified": False,
                "independent_team_diffusion_measured": False,
                "weak_signal_confirmed": False,
            })
    finally:
        conn.close()
    return {
        "version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "source": {
            "index_manifest_sha256": source["index_manifest_sha256"],
            "prior_parent_report_sha256": source["prior_parent_report_sha256"],
            "snapshot_revision": manifest["source"]["revision"],
            "indexed_unique_arxiv_ids": manifest["counts"]["indexed_unique_arxiv_ids"],
            "role": "complete_pinned_arxiv_metadata_not_world_science",
        },
        "period": period, "matching_version": config["matching_version"],
        "alternative_background_category": "cs.RO",
        "parent_terms": config["parent_terms"],
        "parent_unique_arxiv_ids": len(parent_ids),
        "parent_search_audit": parent_audit,
        "windows": windows, "lines": lines,
        "independent_discovery_benchmark": False,
        "weak_signal_assessment_performed": False,
        "limitations": config["limitations"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Temporal diagnostic output is immutable")
    report = build(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                       indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"parent_unique_arxiv_ids": report["parent_unique_arxiv_ids"],
                      "line_counts": {line["id"]: line[
                          "line_in_parent_unique_arxiv_ids"] for line in report["lines"]}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
