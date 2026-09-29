"""Independent row-by-row oracle for a partial trigram-index pilot."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
from time import perf_counter

import pyarrow.parquet as pq

from saia.arxiv_metadata import first_submission, matches_controlled_plan
from saia.arxiv_trigram_index import exact_search
from saia.priority_arxiv_pilot import _sha


PHRASES = ("language model", "neural network", "CAR-T", "satellite constellation")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Verification report exists")
    manifest = json.loads((args.index / "manifest.json").read_text())
    shards = manifest["source"]["indexed_shards"]
    if len(shards) != 1 or manifest["source"]["complete_pinned_inventory_indexed"]:
        raise ValueError("This verifier expects exactly one partial shard")
    plans = {phrase: {"included_terms": [phrase], "exclusions": [],
                      "date_from": "2024-01-01", "as_of_date": "2026-01-01"}
             for phrase in PHRASES}
    index_results = {}
    for phrase, plan in plans.items():
        start = perf_counter()
        result = exact_search(args.index, plan, allow_partial_diagnostic=True)
        index_results[phrase] = {"ids": result["arxiv_ids"],
                                 "seconds": perf_counter() - start,
                                 "coarse_hits": result["audit"]["coarse_hits_with_possible_or_repetition"]}
    oracle = {phrase: set() for phrase in PHRASES}
    start_time = perf_counter()
    path = args.mirror / shards[0]
    scanned = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=8192,
                                                    columns=["id", "title", "abstract", "versions"]):
        scanned += batch.num_rows
        for row in batch.to_pylist():
            matching = [phrase for phrase, plan in plans.items()
                        if matches_controlled_plan(row, plan)]
            if not matching:
                continue
            try:
                first_date = date.fromisoformat(first_submission(row["versions"]))
            except (TypeError, ValueError, IndexError):
                continue
            if date(2024, 1, 1) <= first_date < date(2026, 1, 1):
                for phrase in matching:
                    oracle[phrase].add(row["id"])
    scan_seconds = perf_counter() - start_time
    rows = []
    for phrase in PHRASES:
        indexed = set(index_results[phrase]["ids"])
        actual = oracle[phrase]
        rows.append({"phrase": phrase, "oracle_unique_ids": len(actual),
                     "index_unique_ids": len(indexed),
                     "false_negatives": sorted(actual - indexed),
                     "false_positives_after_exact_filter": sorted(indexed - actual),
                     "index_seconds": index_results[phrase]["seconds"],
                     "index_coarse_hits": index_results[phrase]["coarse_hits"]})
    report = {"version": "arxiv-trigram-index-one-shard-verification-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "index_manifest_sha256": _sha(args.index / "manifest.json"),
              "source_shard": shards[0], "source_rows_scanned": scanned,
              "oracle_single_pass_seconds": scan_seconds,
              "plans": rows,
              "all_exact_sets_equal": all(not row["false_negatives"]
                                          and not row["false_positives_after_exact_filter"]
                                          for row in rows),
              "scope": "one_shard_diagnostic_not_full_inventory_or_user_end_to_end"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"all_exact_sets_equal": report["all_exact_sets_equal"],
                      "source_rows_scanned": scanned,
                      "counts": {row["phrase"]: row["oracle_unique_ids"] for row in rows}},
                     ensure_ascii=False))
    if not report["all_exact_sets_equal"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
