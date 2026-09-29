"""Check every duplicate arXiv ID against the indexed first snapshot row."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

from saia.priority_arxiv_pilot import _sha


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Audit report already exists")
    manifest = json.loads((args.index / "manifest.json").read_text())
    if not manifest["source"]["complete_pinned_inventory_indexed"]:
        raise ValueError("Duplicate audit requires the complete pinned index")
    seen = set()
    duplicates = []
    scanned = 0
    connection = sqlite3.connect(f"file:{(args.index / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for name in manifest["source"]["indexed_shards"]:
            for batch in pq.ParquetFile(args.mirror / name).iter_batches(
                    batch_size=8192, columns=["id", "title", "abstract", "update_date"]):
                scanned += batch.num_rows
                for row in batch.to_pylist():
                    identifier = row["id"]
                    if identifier not in seen:
                        seen.add(identifier)
                        continue
                    indexed = connection.execute(
                        "SELECT title,abstract,snapshot_update_date,source_shard "
                        "FROM works WHERE arxiv_id=?", (identifier,)).fetchone()
                    if indexed is None:
                        raise ValueError("Duplicate has no indexed first row")
                    later_update = row["update_date"].isoformat() if row["update_date"] else None
                    prior_update = indexed["snapshot_update_date"]
                    duplicates.append({
                        "arxiv_id": identifier,
                        "indexed_update_date": prior_update,
                        "later_source_update_date": later_update,
                        "indexed_source_shard": indexed["source_shard"],
                        "later_source_shard": name,
                        "content_identical": (indexed["title"] == row["title"]
                                              and indexed["abstract"] == row["abstract"]),
                        "later_row_newer_than_indexed": bool(
                            prior_update and later_update and later_update > prior_update),
                    })
    finally:
        connection.close()
    if scanned != manifest["counts"]["scanned_rows"]:
        raise ValueError("Source row count differs from indexed manifest")
    report = {
        "version": "arxiv-index-duplicate-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "index_manifest_sha256": _sha(args.index / "manifest.json"),
        "scanned_rows": scanned,
        "duplicate_rows": len(duplicates),
        "counts": dict(Counter(
            "identical" if row["content_identical"] else
            "later_newer" if row["later_row_newer_than_indexed"] else
            "later_older_or_same_date_conflict"
            for row in duplicates)),
        "duplicates": duplicates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"scanned_rows": scanned, "duplicate_rows": len(duplicates),
                      "counts": report["counts"]}, ensure_ascii=False))
    if len(duplicates) != manifest["counts"]["duplicate_id_rows_excluded_first_snapshot_row_wins"]:
        raise SystemExit("Duplicate count differs from index manifest")


if __name__ == "__main__":
    main()
