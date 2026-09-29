"""Reusable category/year denominator counts from a pinned full arXiv index.

Category populations are scientific background cohorts, not automatically
valid denominators for a particular technology query or weak-signal score.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.arxiv_trigram_index import GUARDED_VERSION
from saia.controlled_collection import sha256_file


VERSION = "arxiv-category-year-background-v1"


def build(*, index_dir: Path, output_path: Path, first_year: int = 2000,
          last_complete_year: int = 2025) -> dict:
    if output_path.exists():
        raise FileExistsError("Background generation is immutable")
    if not 1991 <= first_year <= last_complete_year <= 2025:
        raise ValueError("Only complete pinned years through 2025 are accepted")
    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != GUARDED_VERSION
            or manifest["source"].get("complete_pinned_inventory_indexed") is not True
            or manifest["counts"].get("invalid_v1_dates_excluded") != 0):
        raise ValueError("A complete guarded arXiv index with valid v1 dates is required")
    db_path = index_dir / manifest["file"]["name"]
    if db_path.stat().st_size != manifest["file"]["bytes"]:
        raise ValueError("Index size differs from pinned manifest")
    if sha256_file(db_path) != manifest["file"]["sha256"]:
        raise ValueError("Index checksum differs from pinned manifest")

    unique_by_year: Counter[int] = Counter()
    first_category_by_year: Counter[tuple[int, str]] = Counter()
    any_category_by_year: Counter[tuple[int, str]] = Counter()
    missing_categories_by_year: Counter[int] = Counter()
    start = f"{first_year}-01-01"
    end = f"{last_complete_year + 1}-01-01"
    scanned = 0
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        indexed_count = connection.execute("SELECT count(*) FROM works").fetchone()[0]
        if indexed_count != manifest["counts"]["indexed_unique_arxiv_ids"]:
            raise ValueError("Index row count differs from pinned manifest")
        cursor = connection.execute(
            "SELECT first_submission_date,categories FROM works "
            "WHERE first_submission_date >= ? AND first_submission_date < ?",
            (start, end))
        for date_str, categories_raw in cursor:
            scanned += 1
            year = int(date_str[:4])
            unique_by_year[year] += 1
            categories = list(dict.fromkeys(str(categories_raw or "").split()))
            if not categories:
                missing_categories_by_year[year] += 1
                continue
            first_category_by_year[(year, categories[0])] += 1
            for category in categories:
                any_category_by_year[(year, category)] += 1
    finally:
        connection.close()

    years = list(range(first_year, last_complete_year + 1))
    categories = sorted({category for _, category in any_category_by_year})
    result = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "index_manifest_sha256": sha256_file(manifest_path),
            "dataset": manifest["source"]["dataset"],
            "revision": manifest["source"]["revision"],
            "full_inventory_sha256": manifest["source"]["full_inventory_sha256"],
            "indexed_unique_arxiv_ids": manifest["counts"]["indexed_unique_arxiv_ids"],
            "duplicate_source_rows_excluded_first_snapshot_row_wins":
                manifest["counts"]["duplicate_id_rows_excluded_first_snapshot_row_wins"],
        },
        "window": {"first_year_inclusive": first_year,
                   "last_complete_year_inclusive": last_complete_year,
                   "current_partial_year_excluded": True},
        "counts": {
            "unique_works_in_window": scanned,
            "years": len(years),
            "distinct_categories_any_position": len(categories),
            "missing_categories": sum(missing_categories_by_year.values()),
        },
        "annual": [
            {"year": year, "unique_works": unique_by_year[year],
             "missing_categories": missing_categories_by_year[year]}
            for year in years
        ],
        "category_annual": [
            {"category": category, "year": year,
             "first_listed_category_works": first_category_by_year[year, category],
             "any_listed_category_works": any_category_by_year[year, category]}
            for category in categories for year in years
            if any_category_by_year[year, category]
        ],
        "interpretation": {
            "counts_distinct_work_ids_within_each_category_year": True,
            "categories_overlap_so_counts_must_not_be_summed_across_categories": True,
            "first_listed_category_is_only_metadata_order_not_verified_primary_field": True,
            "all_arxiv_is_not_all_scientific_publications": True,
            "topic_denominator_requires_approved_comparable_field_definition": True,
            "not_a_weak_signal_or_growth_measure": True,
            "stored_current_text_not_historical_v1_text": True,
        },
    }
    if sum(row["unique_works"] for row in result["annual"]) != scanned:
        raise ValueError("Annual denominator count mismatch")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    return result
