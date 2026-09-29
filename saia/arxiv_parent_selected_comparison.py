"""Read-only check of sealed selected works against the guarded full index.

This extends denominator equivalence evidence without changing the live
coverage-passport policy or claiming that a fast parent audit is deployed.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from saia.arxiv_metadata import controlled_plan, first_submission, matches_controlled_plan
from saia.arxiv_parent_corpus import month_key
from saia.arxiv_trigram_index import GUARDED_VERSION
from saia.controlled_collection import sha256_file


VERSION = "arxiv-parent-selected-index-comparison-v1"


def _selected_check(*, package: Path, report: dict, connection: sqlite3.Connection) -> dict:
    import pyarrow.parquet as pq

    manifest_path = package / "manifest.json"
    mission_path = package / "mission.json"
    manifest_bytes = manifest_path.read_bytes()
    mission_bytes = mission_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    mission = json.loads(mission_bytes)
    source = report["input"]
    block = manifest["sources"]["arxiv"]
    if (source["selected_package_manifest_bytes_sha256"] !=
            hashlib.sha256(manifest_bytes).hexdigest()
            or source["mission_bytes_sha256"] != hashlib.sha256(mission_bytes).hexdigest()
            or manifest["mission_file_sha256"] != hashlib.sha256(mission_bytes).hexdigest()
            or manifest["mission_id"] != report["mission_id"]
            or mission["mission_id"] != report["mission_id"]
            or block["upstream_inventory_sha256"] != source["upstream_inventory_sha256"]):
        raise ValueError("Sealed package is not bound to full-mirror report")
    plan = controlled_plan(mission)
    if plan is None or source["categories"] or source["text_scope"] is not None:
        raise ValueError("Expected a controlled, uncategorized phrase plan")
    seen: set[str] = set()
    phrase_counts: Counter[str] = Counter()
    by_term = {term: Counter() for term in plan["included_terms"]}
    content_differences = []
    predicate_failures = []
    outside_window = []
    for selected_file in block["files"]:
        filename = selected_file["file"]
        if Path(filename).name != filename:
            raise ValueError("Invalid selected package basename")
        path = package / "arxiv" / filename
        if sha256_file(path) != selected_file["sha256"]:
            raise ValueError("Selected package file checksum mismatch")
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != selected_file["records"]:
            raise ValueError("Selected package row count mismatch")
        for batch in parquet.iter_batches(batch_size=2048,
                                          columns=["id", "title", "abstract",
                                                   "categories", "versions"]):
            for row in batch.to_pylist():
                identifier = str(row["id"] or "")
                if identifier in seen or not identifier:
                    raise ValueError("Duplicate or missing selected arXiv ID")
                seen.add(identifier)
                indexed = connection.execute(
                    "SELECT title,abstract,categories,first_submission_date "
                    "FROM works WHERE arxiv_id=?", (identifier,)).fetchone()
                if indexed is None:
                    content_differences.append({"arxiv_id": identifier, "reason": "missing_index_id"})
                    continue
                created = first_submission(row["versions"])
                if ((row["title"], row["abstract"], row["categories"], created)
                        != indexed):
                    content_differences.append({"arxiv_id": identifier,
                                                "reason": "indexed_metadata_differs"})
                if not report["period_from"] <= created < report["period_end_exclusive"]:
                    outside_window.append(identifier)
                if not matches_controlled_plan(row, plan):
                    predicate_failures.append(identifier)
                key = month_key(created)
                phrase_counts[key] += 1
                for term in plan["included_terms"]:
                    one = {"included_terms": [term],
                           "exclusions": plan.get("exclusions", []),
                           "matching_version": plan.get("matching_version", "literal-phrase-0.4.6")}
                    if matches_controlled_plan(row, one):
                        by_term[term][key] += 1
    if len(seen) != block["total_records"]:
        raise ValueError("Selected package total differs from unique IDs")
    monthly_mismatches = [{"month": point["start"][:7], "field": "phrase_scope_works"}
                          for point in report["series"]
                          if phrase_counts[point["start"][:7]] != point["phrase_scope_works"]]
    prior_terms = {row["phrase"]: row["points"] for row in report["phrase_series"]}
    if set(prior_terms) != set(by_term):
        raise ValueError("Selected package phrase list differs from report")
    for term, points in prior_terms.items():
        monthly_mismatches.extend(
            {"month": point["start"][:7], "field": "term:" + term}
            for point in points
            if by_term[term][point["start"][:7]] != point["works"])
    exact = (not content_differences and not predicate_failures and not outside_window
             and not monthly_mismatches
             and len(seen) == report["counts"]["phrase_scope_native_ids_in_period"])
    return {"selected_ids": len(seen), "content_differences": content_differences,
            "predicate_failures": predicate_failures,
            "outside_window": outside_window,
            "monthly_mismatches": monthly_mismatches, "exact_match": exact}


def compare(*, index_dir: Path, reports_dir: Path, packages_dir: Path) -> dict:
    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != GUARDED_VERSION
            or manifest["source"].get("complete_pinned_inventory_indexed") is not True
            or manifest["counts"].get("invalid_v1_dates_excluded") != 0):
        raise ValueError("Expected complete guarded index")
    index_path = index_dir / manifest["file"]["name"]
    if (index_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(index_path) != manifest["file"]["sha256"]):
        raise ValueError("Guarded index checksum mismatch")
    results = []
    skipped = []
    connection = sqlite3.connect(f"file:{index_path.resolve()}?mode=ro", uri=True)
    try:
        for path in sorted(reports_dir.glob("*-parent-arxiv.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            source = report.get("input", {})
            if (report.get("version") != "arxiv-parent-corpus-audit-0.4.16"
                    or source.get("dataset_revision") != manifest["source"]["revision"]
                    or source.get("upstream_inventory_sha256") !=
                    manifest["source"]["full_inventory_sha256"]
                    or source.get("categories") or source.get("text_scope") is not None):
                skipped.append({"report": path.name, "reason": "incompatible_scope_or_source"})
                continue
            package = packages_dir / path.name.removesuffix("-parent-arxiv.json")
            if not (package / "manifest.json").exists():
                skipped.append({"report": path.name, "reason": "sealed_package_not_found"})
                continue
            try:
                result = _selected_check(package=package, report=report,
                                         connection=connection)
            except ValueError as error:
                result = {"selected_ids": 0, "exact_match": False,
                          "error": str(error)}
            results.append({"report": path.name, "report_sha256": sha256_file(path), **result})
    finally:
        connection.close()
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "index_manifest_sha256": sha256_file(manifest_path),
            "index_file_sha256": manifest["file"]["sha256"],
            "reports_compared": len(results),
            "reports_exact": sum(row["exact_match"] for row in results),
            "selected_id_assignments_checked": sum(row["selected_ids"] for row in results),
            "results": results, "skipped": skipped,
            "limits": {"selected_work_checks_only": True,
                       "same_pinned_snapshot_only": True,
                       "independent_studies_not_adjudicated": True,
                       "no_production_fast_path_enabled": True}}
