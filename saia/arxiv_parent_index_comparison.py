"""Compare old full-mirror monthly denominators with the guarded arXiv index.

Read-only diagnostic. Agreement does not by itself authorize replacing the
full parent audit: selected-ID and phrase membership checks also matter.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.arxiv_trigram_index import GUARDED_VERSION
from saia.controlled_collection import sha256_file


VERSION = "arxiv-parent-index-comparison-v1"


def compare(*, index_dir: Path, reports_dir: Path) -> dict:
    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != GUARDED_VERSION
            or manifest["source"].get("complete_pinned_inventory_indexed") is not True
            or manifest["counts"].get("invalid_v1_dates_excluded") != 0):
        raise ValueError("Expected complete guarded index")
    index_path = index_dir / manifest["file"]["name"]
    if (index_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(index_path) != manifest["file"]["sha256"]):
        raise ValueError("Guarded index differs from its frozen manifest")
    files = sorted(reports_dir.glob("*-parent-arxiv.json"))
    results = []
    skipped = []
    cache = {}
    connection = sqlite3.connect(f"file:{index_path.resolve()}?mode=ro", uri=True)
    try:
        for path in files:
            report = json.loads(path.read_text(encoding="utf-8"))
            source = report.get("input", {})
            if (report.get("version") != "arxiv-parent-corpus-audit-0.4.16"
                    or source.get("upstream_inventory_sha256") !=
                    manifest["source"]["full_inventory_sha256"]
                    or source.get("dataset_revision") != manifest["source"]["revision"]):
                skipped.append({"report": path.name, "reason": "different_source_or_policy"})
                continue
            if source.get("categories"):
                skipped.append({"report": path.name, "reason": "category_union_not_compared"})
                continue
            key = (report["period_from"], report["period_end_exclusive"])
            if key not in cache:
                cache[key] = dict(connection.execute(
                    "SELECT substr(first_submission_date,1,7),count(*) FROM works "
                    "WHERE first_submission_date>=? AND first_submission_date<? "
                    "GROUP BY substr(first_submission_date,1,7)", key))
            monthly = cache[key]
            mismatches = [{"month": row["start"][:7],
                           "full_mirror": row["parent_category_works"],
                           "guarded_index": monthly.get(row["start"][:7], 0)}
                          for row in report["series"]
                          if row["parent_category_works"] != monthly.get(row["start"][:7], 0)]
            indexed_total = sum(monthly.get(row["start"][:7], 0)
                                for row in report["series"])
            full_total = report["counts"]["parent_native_ids_in_period"]
            results.append({"report": path.name,
                            "report_sha256": sha256_file(path),
                            "months": len(report["series"]),
                            "full_mirror_total": full_total,
                            "guarded_index_total": indexed_total,
                            "mismatches": mismatches,
                            "exact_match": not mismatches and indexed_total == full_total})
    finally:
        connection.close()
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "index_manifest_sha256": sha256_file(manifest_path),
            "index_file_sha256": manifest["file"]["sha256"],
            "reports_found": len(files), "reports_compared": len(results),
            "reports_exact": sum(row["exact_match"] for row in results),
            "months_compared": sum(row["months"] for row in results),
            "months_mismatched": sum(len(row["mismatches"]) for row in results),
            "results": results, "skipped": skipped,
            "limits": {"denominator_only": True,
                       "selected_id_and_phrase_checks_not_replaced": True,
                       "no_production_fast_path_enabled": True,
                       "same_pinned_snapshot_only": True}}
