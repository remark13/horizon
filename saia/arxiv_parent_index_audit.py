"""Guarded-index parent audit for current, all-category arXiv windows.

Unlike a bare cached denominator, this revalidates the mirror identity,
every selected work and every approved phrase. It remains opt-in until its
coverage-passport and end-to-end behavior are checked separately.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from saia.arxiv_metadata import (IDENTIFIER, controlled_plan, first_submission,
                                  matches_controlled_plan, selection, text_scope)
from saia.arxiv_parent_corpus import linear_slope, month_key, series
from saia.arxiv_trigram_index import GUARDED_VERSION
from saia.controlled_collection import inventory_sha256, sha256_file
from saia.local_arxiv_search import policy as local_policy, validate_inventory
from saia.measurement import WindowCounts, analyze_series
from saia.package_observations import monthly_bounds, payload_hash
from saia.query_expansion import digest as payload_digest


VERSION = "arxiv-parent-corpus-audit-guarded-index-v1"


class IndexAuditIneligible(ValueError):
    """The old full-mirror audit remains required for this scope."""


def eligible(*, mission: dict, index_manifest: dict, duplicate_guard: dict) -> bool:
    wanted, start_text, _ = selection(mission)
    if wanted or text_scope(mission) is not None or controlled_plan(mission) is None:
        return False
    if (index_manifest.get("version") != GUARDED_VERSION
            or index_manifest.get("source", {}).get("complete_pinned_inventory_indexed") is not True
            or index_manifest.get("counts", {}).get("invalid_v1_dates_excluded") != 0):
        return False
    if not start_text or duplicate_guard.get("version") != "arxiv-duplicate-text-guard-v1":
        return False
    return all(variant["first_submission_date"] < start_text
               for variant in duplicate_guard["variants"])


def audit(*, mirror_dir: Path, mission_path: Path, package_manifest_path: Path,
          index_dir: Path) -> dict:
    import pyarrow.parquet as pq

    mission_bytes = mission_path.read_bytes()
    package_bytes = package_manifest_path.read_bytes()
    mission = json.loads(mission_bytes)
    package = json.loads(package_bytes)
    index_manifest_path = index_dir / "manifest.json"
    index_manifest = json.loads(index_manifest_path.read_text(encoding="utf-8"))
    guard_path = index_dir / index_manifest["duplicate_guard"]["name"]
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    if not eligible(mission=mission, index_manifest=index_manifest,
                    duplicate_guard=guard):
        raise IndexAuditIneligible("Index parent audit requires all categories, "
                                   "approved plan and period after duplicate records")
    wanted, start_text, end_inclusive = selection(mission)
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_inclusive) + timedelta(days=1)
    cutoff = date.fromisoformat(mission["as_of_date"])
    if end > cutoff:
        raise ValueError("Parent period extends beyond cutoff")
    block = package.get("sources", {}).get("arxiv", {})
    cfg = local_policy()
    paths = validate_inventory(str(mirror_dir.resolve()), cfg["expected_files"],
                               cfg["expected_rows"])
    source_hash = inventory_sha256(paths, cfg["expected_rows"], cfg)
    if (package.get("mission_id") != mission.get("mission_id")
            or package.get("mission_file_sha256") != hashlib.sha256(mission_bytes).hexdigest()
            or block.get("dataset_revision") != cfg["revision"]
            or block.get("upstream_inventory_sha256") != source_hash
            or index_manifest["source"].get("revision") != cfg["revision"]
            or index_manifest["source"].get("full_inventory_sha256") != source_hash
            or guard.get("source_inventory_sha256") != source_hash
            or guard.get("base_index_manifest_sha256") !=
            index_manifest.get("base_index_manifest_sha256")):
        raise ValueError("Mirror, sealed package and guarded index differ")
    index_path = index_dir / index_manifest["file"]["name"]
    if (index_path.stat().st_size != index_manifest["file"]["bytes"]
            or sha256_file(index_path) != index_manifest["file"]["sha256"]
            or sha256_file(guard_path) != index_manifest["duplicate_guard"]["sha256"]):
        raise ValueError("Guarded index file or duplicate guard changed")
    if (index_manifest["counts"]["scanned_rows"] != cfg["expected_rows"]
            or index_manifest["counts"]["indexed_unique_arxiv_ids"] +
            index_manifest["counts"]["duplicate_id_rows_excluded_first_snapshot_row_wins"]
            != cfg["expected_rows"]):
        raise ValueError("Index inventory count mismatch")

    plan = controlled_plan(mission)
    parent_counts: Counter[str] = Counter()
    phrase_counts: Counter[str] = Counter()
    phrase_by_term = {term: Counter() for term in plan["included_terms"]}
    selected_ids: set[str] = set()
    connection = sqlite3.connect(f"file:{index_path.resolve()}?mode=ro", uri=True)
    try:
        indexed_rows = connection.execute("SELECT count(*) FROM works").fetchone()[0]
        if indexed_rows != index_manifest["counts"]["indexed_unique_arxiv_ids"]:
            raise ValueError("Index rows differ from manifest")
        for month, count in connection.execute(
                "SELECT substr(first_submission_date,1,7),count(*) FROM works "
                "WHERE first_submission_date>=? AND first_submission_date<? "
                "GROUP BY substr(first_submission_date,1,7)",
                (start.isoformat(), end.isoformat())):
            parent_counts[month] = count
        for selected_file in block.get("files") or []:
            filename = selected_file["file"]
            if Path(filename).name != filename:
                raise ValueError("Selected arXiv filename is not a basename")
            selected_path = package_manifest_path.parent / "arxiv" / filename
            if sha256_file(selected_path) != selected_file["sha256"]:
                raise ValueError("Sealed selected file checksum mismatch")
            parquet = pq.ParquetFile(selected_path)
            if parquet.metadata.num_rows != selected_file["records"]:
                raise ValueError("Sealed selected file row count mismatch")
            for batch in parquet.iter_batches(batch_size=2048,
                                              columns=["id", "title", "abstract",
                                                       "categories", "versions"]):
                for row in batch.to_pylist():
                    identifier = str(row.get("id") or "").strip()
                    if not IDENTIFIER.fullmatch(identifier) or identifier in selected_ids:
                        raise ValueError("Invalid or duplicate selected arXiv ID")
                    selected_ids.add(identifier)
                    indexed = connection.execute(
                        "SELECT title,abstract,categories,first_submission_date "
                        "FROM works WHERE arxiv_id=?", (identifier,)).fetchone()
                    created = first_submission(row.get("versions"))
                    if indexed != (row.get("title"), row.get("abstract"),
                                   row.get("categories"), created):
                        raise ValueError("Selected work differs from guarded index")
                    if not start_text <= created < end.isoformat():
                        raise ValueError("Selected work lies outside approved window")
                    if not matches_controlled_plan(row, plan):
                        raise ValueError("Selected work fails approved predicate")
                    month = month_key(created)
                    phrase_counts[month] += 1
                    for term in plan["included_terms"]:
                        one = {"included_terms": [term],
                               "exclusions": plan.get("exclusions", []),
                               "matching_version": plan.get("matching_version", "literal-phrase-0.4.6")}
                        if matches_controlled_plan(row, one):
                            phrase_by_term[term][month] += 1
    finally:
        connection.close()
    if not block.get("files") or len(selected_ids) != block.get("total_records"):
        raise ValueError("Sealed selected cohort is incomplete")
    parent_total = sum(parent_counts.values())
    scanned = index_manifest["counts"]["scanned_rows"]
    points = series(parent_counts, phrase_counts, start, end)
    publication = analyze_series([
        WindowCounts(left, right, phrase_counts[month_key(left.isoformat())],
                     parent_counts[month_key(left.isoformat())],
                     coverage_comparable=True)
        for left, right in monthly_bounds(start, end)], cutoff)
    report = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mission_id": mission["mission_id"], "as_of_date": cutoff.isoformat(),
        "period_from": start.isoformat(), "period_end_exclusive": end.isoformat(),
        "input": {"source": str(mirror_dir.resolve()),
                  "dataset_revision": block["dataset_revision"],
                  "upstream_inventory_sha256": source_hash,
                  "mission_bytes_sha256": hashlib.sha256(mission_bytes).hexdigest(),
                  "selected_package_manifest_bytes_sha256": hashlib.sha256(package_bytes).hexdigest(),
                  "categories": sorted(wanted), "text_scope": None,
                  "controlled_search_plan_sha256": payload_digest(plan),
                  "index_manifest_sha256": sha256_file(index_manifest_path),
                  "index_file_sha256": index_manifest["file"]["sha256"],
                  "duplicate_guard_sha256": index_manifest["duplicate_guard"]["sha256"]},
        "counts": {"scanned_metadata_rows": scanned,
                   "category_records_all_dates": scanned,
                   "category_records_outside_period": scanned - parent_total,
                   "invalid_v1_or_id_unknown_period": 0,
                   "parent_native_ids_in_period": parent_total,
                   "phrase_scope_native_ids_in_period": len(selected_ids)},
        "series": points, "publication_series": publication,
        "full_period_descriptive": {
            "window_count": len(points),
            "phrase_count_slope_per_month": linear_slope(
                [point["phrase_scope_works"] for point in points]),
            "parent_count_slope_per_month": linear_slope(
                [point["parent_category_works"] for point in points]),
            "share_slope_per_month": linear_slope(
                [point["share_within_frozen_category_snapshot"] for point in points]),
            "phrase_count_first_to_last_change":
                points[-1]["phrase_scope_works"] - points[0]["phrase_scope_works"],
            "parent_count_first_to_last_change":
                points[-1]["parent_category_works"] - points[0]["parent_category_works"],
            "share_first_to_last_change":
                points[-1]["share_within_frozen_category_snapshot"] -
                points[0]["share_within_frozen_category_snapshot"],
            "role": "descriptive_all_24_complete_months_not_screen_decision"},
        "phrase_series": [
            {"phrase": term, "points": [
                {"start": left.isoformat(), "end": right.isoformat(),
                 "works": counts[month_key(left.isoformat())]}
                for left, right in monthly_bounds(start, end)]}
            for term, counts in phrase_by_term.items()],
        "coverage": {
            "inventory_traversal_complete": True,
            "inventory_traversal_method": "guarded_index_built_from_complete_pinned_mirror",
            "same_source_index_checksum_verified": True,
            "selected_work_predicate_and_metadata_rechecked": True,
            "within_frozen_snapshot_same_query_window_comparable": True,
            "world_science_coverage_complete": None,
            "crosslist_completeness": block.get("crosslist_completeness"),
            "field_coverage": block.get("field_coverage"),
            "unit": "unique_native_arxiv_id_not_independent_research",
            "invalid_examples": []},
        "accuracy_evaluated": False, "models_used": False,
        "database_written": False, "confirmed_weak_signals": None,
        "limitations": [
            "The denominator is the entire pinned arXiv metadata snapshot in the period, not all science.",
            "Index-based traversal was built earlier from the complete pinned mirror and verified by checksum; the full mirror was not reread for this request.",
            "A stable ratio inside one snapshot does not prove stable real-world coverage over publication time.",
            "Native arXiv IDs are not adjudicated independent studies.",
            "Current title and abstract text are used; historical text versions are not reconstructed.",
        ],
    }
    report["report_payload_sha256"] = payload_hash(report)
    return report
