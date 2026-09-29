"""Frozen, full-index arXiv time-series diagnostic for two narrow BAS lines.

This does not infer weak-signal status or claim historical text-version fidelity.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.arxiv_trigram_index import exact_search
from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search


PLAN_VERSION = "bas-narrow-temporal-diagnostic-v1"
VERSION = "bas-narrow-temporal-diagnostic-v3"


def _window_year(iso_date: str) -> int:
    return int(iso_date[:4]) - (iso_date[5:7] < "09")


def _read_ids(conn: sqlite3.Connection, identifiers: list[str]) -> list[dict]:
    result = []
    for offset in range(0, len(identifiers), 400):
        batch = identifiers[offset:offset + 400]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            "SELECT arxiv_id,first_submission_date,title,categories,"
            "snapshot_update_date,versions_json "
            f"FROM works WHERE arxiv_id IN ({placeholders})", batch)
        result.extend(dict(row) for row in rows)
    if len(result) != len(identifiers):
        raise ValueError("Indexed identifiers disappeared during temporal lookup")
    return result


def _share(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 9) if denominator else None


def _later_snapshot_update(row: dict, window_end: str) -> bool | None:
    update = row.get("snapshot_update_date")
    if not update:
        return None
    return str(update)[:10] >= window_end


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("version") != PLAN_VERSION or len(config.get("lines") or []) != 2:
        raise ValueError("Expected the frozen two-line BAS diagnostic plan")
    start = date.fromisoformat(config["period"]["date_from"])
    cutoff = date.fromisoformat(config["period"]["as_of_date"])
    if (start.month, start.day) != (9, 1) or (cutoff.month, cutoff.day) != (9, 1):
        raise ValueError("Only complete September-August windows are supported")
    if start >= cutoff or not 2 <= cutoff.year - start.year <= 20:
        raise ValueError("Unexpected diagnostic period")
    if config["matching_version"] != ORTHOGRAPHIC_MATCHING_VERSION:
        raise ValueError("Unpinned matching policy")
    root = Path(__file__).resolve().parents[1]
    source = config["source"]
    manifest_path = root / source["index_manifest"]
    mission_path = root / source["bas_parent_mission"]
    if (sha256_file(manifest_path) != source["index_manifest_sha256"]
            or sha256_file(mission_path) != source["bas_parent_mission_sha256"]):
        raise ValueError("Pinned index manifest or BAS parent mission changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mission = json.loads(mission_path.read_text(encoding="utf-8"))
    index_dir = manifest_path.parent
    parent_plan = dict(mission["controlled_search_plan"])
    parent_plan.update({"date_from": start.isoformat(),
                        "as_of_date": cutoff.isoformat()})
    if parent_plan["matching_version"] != config["matching_version"]:
        raise ValueError("Parent predicate and line predicates use different matching")
    parent = exact_search(index_dir, parent_plan)
    parent_ids = set(parent["arxiv_ids"])
    line_results = []
    for line in config["lines"]:
        plan = {"concept_groups": line["concept_groups"],
                "exclusions": line["exclusions"],
                "matching_version": config["matching_version"],
                "date_from": start.isoformat(), "as_of_date": cutoff.isoformat()}
        search = exact_concept_search(index_dir, plan, max_matches=5000)
        line_results.append((line, search))
    connection = sqlite3.connect(
        f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        all_arxiv = {
            int(year): count for year, count in connection.execute(
                "SELECT CAST(substr(first_submission_date,1,4) AS INTEGER) "
                "- CASE WHEN substr(first_submission_date,6,2)<'09' THEN 1 ELSE 0 END "
                "AS window_year, count(*) FROM works "
                "WHERE first_submission_date>=? AND first_submission_date<? "
                "GROUP BY window_year", (start.isoformat(), cutoff.isoformat()))
        }
        parent_rows = _read_ids(connection, sorted(parent_ids))
        parent_by_year = Counter(_window_year(row["first_submission_date"])
                                 for row in parent_rows)
        old_start = mission["controlled_search_plan"]["date_from"]
        old_cutoff = mission["controlled_search_plan"]["as_of_date"]
        package_manifest_path = mission_path.parent / "manifest.json"
        package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
        old_selected = package_manifest["sources"]["arxiv"]["total_records"]
        old_recomputed = sum(old_start <= row["first_submission_date"] < old_cutoff
                             for row in parent_rows)
        if (package_manifest["mission_file_sha256"] != source["bas_parent_mission_sha256"]
                or old_recomputed != old_selected):
            raise ValueError("Full-index BAS parent differs from prior full-mirror cohort")
        windows = []
        for year in range(start.year, cutoff.year):
            denominator = all_arxiv.get(year, 0)
            parent_count = parent_by_year[year]
            if denominator < parent_count:
                raise ValueError("BAS parent count exceeds all-arXiv denominator")
            windows.append({
                "from": f"{year}-09-01", "to_exclusive": f"{year + 1}-09-01",
                "all_pinned_arxiv_unique_ids": denominator,
                "frozen_bas_union_unique_ids": parent_count,
            })
        lines = []
        for line, search in line_results:
            line_ids = set(search["arxiv_ids"])
            rows = _read_ids(connection, sorted(line_ids))
            by_year = Counter(_window_year(row["first_submission_date"])
                              for row in rows)
            in_parent_by_year = Counter(
                _window_year(row["first_submission_date"])
                for row in rows if row["arxiv_id"] in parent_ids)
            annual = []
            for window in windows:
                year = int(window["from"][:4])
                count = by_year[year]
                in_parent = in_parent_by_year[year]
                window_rows = [row for row in rows
                               if _window_year(row["first_submission_date"]) == year]
                later_update = sum(_later_snapshot_update(
                    row, window["to_exclusive"]) is True for row in window_rows)
                unknown_update = sum(_later_snapshot_update(
                    row, window["to_exclusive"]) is None for row in window_rows)
                multiple_versions = sum(len(json.loads(row["versions_json"])) > 1
                                        for row in window_rows)
                annual.append({
                    "from": window["from"], "to_exclusive": window["to_exclusive"],
                    "line_unique_ids": count,
                    "line_in_frozen_bas_union": in_parent,
                    "line_outside_frozen_bas_union": count - in_parent,
                    "share_all_pinned_arxiv": _share(
                        count, window["all_pinned_arxiv_unique_ids"]),
                    "intersection_share_of_frozen_bas_union_not_line_prevalence": _share(
                        in_parent, window["frozen_bas_union_unique_ids"]),
                    "current_metadata_updated_after_window": later_update,
                    "snapshot_update_date_unknown": unknown_update,
                    "multiple_preprint_versions_in_current_snapshot": multiple_versions,
                })
            rows.sort(key=lambda row: (row["first_submission_date"],
                                       row["arxiv_id"]))
            lines.append({
                "line_id": line["line_id"], "label_ru": line["label_ru"],
                "concept_groups": line["concept_groups"],
                "total_unique_ids": len(rows),
                "within_frozen_bas_union": len(line_ids & parent_ids),
                "outside_frozen_bas_union": len(line_ids - parent_ids),
                "frozen_bas_union_valid_as_line_denominator": (
                    len(line_ids - parent_ids) == 0),
                "current_metadata_updated_after_first_submission_window": sum(
                    row["current_metadata_updated_after_window"] for row in annual),
                "snapshot_update_date_unknown": sum(
                    row["snapshot_update_date_unknown"] for row in annual),
                "search_audit": search["audit"], "annual": annual,
                "earliest_papers": [
                    {"arxiv_id": row["arxiv_id"],
                     "url": f"https://arxiv.org/abs/{row['arxiv_id']}",
                     "first_submission_date": row["first_submission_date"],
                     "title": row["title"], "categories": row["categories"]}
                    for row in rows[:10]],
                "latest_papers": [
                    {"arxiv_id": row["arxiv_id"],
                     "url": f"https://arxiv.org/abs/{row['arxiv_id']}",
                     "first_submission_date": row["first_submission_date"],
                     "title": row["title"], "categories": row["categories"]}
                    for row in rows[-10:]],
            })
    finally:
        connection.close()
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "source": {
            "dataset": manifest["source"]["dataset"],
            "revision": manifest["source"]["revision"],
            "inventory_sha256": manifest["source"]["full_inventory_sha256"],
            "index_manifest_sha256": source["index_manifest_sha256"],
            "parent_mission_sha256": source["bas_parent_mission_sha256"],
            "indexed_unique_arxiv_ids": manifest["counts"]["indexed_unique_arxiv_ids"],
            "duplicate_id_rows_excluded": manifest["counts"][
                "duplicate_id_rows_excluded_first_snapshot_row_wins"],
            "source_role": "pinned_arxiv_metadata_snapshot_not_world_science",
        },
        "period": config["period"],
        "parent_predicate": {"included_terms": parent_plan["included_terms"],
                             "exclusions": parent_plan["exclusions"],
                             "matching_version": parent_plan["matching_version"],
                             "relation": "OR"},
        "parent_search_audit": parent["audit"],
        "parent_total_unique_ids": len(parent_ids),
        "prior_full_mirror_reconciliation": {
            "period_from": old_start, "period_to_exclusive": old_cutoff,
            "full_mirror_selected_records": old_selected,
            "full_index_recomputed_records": old_recomputed,
            "package_manifest_sha256": sha256_file(package_manifest_path),
            "exact_count_match": True,
        },
        "windows": windows, "lines": lines,
        "weak_signal_assessment_performed": False,
        "accuracy_evaluated": False,
        "supersedes": "outputs/bas-narrow-temporal-diagnostic-2026-09-26-v3.json",
        "limitations": [
            *config["interpretation_limits"],
            "The frozen BAS union misses line papers; its intersection fraction is a coverage diagnostic, not the prevalence or growth of the narrow line.",
            "Snapshot update timing is a risk indicator, not a historical title/abstract reconstruction; an earlier version may or may not contain the matching phrase.",
        ],
    }
