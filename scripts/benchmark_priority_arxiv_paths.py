"""Bounded repeat benchmark of existing local arXiv retrieval paths only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from saia.local_arxiv_search import search
from saia.thematic_arxiv_cache import select_from_target_packs


def _percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phrase", default="small modular reactor")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists() or not 2 <= args.repeats <= 5:
        raise ValueError("New output path and 2–5 repeats required")
    plan = {"included_terms": [args.phrase], "exclusions": [],
            "date_from": "2024-09-01", "as_of_date": "2026-09-01"}
    rounds = []
    for number in range(args.repeats):
        started = perf_counter()
        baseline = search(args.mirror, plan, limit=100)
        full_seconds = perf_counter() - started
        started = perf_counter()
        packed, audit = select_from_target_packs(args.cache, args.packs, plan)
        pack_seconds = perf_counter() - started
        baseline_ids = {item.source_ids[0].rsplit("/", 1)[-1] for item in baseline.works}
        packed_ids = set(packed["id"].to_pylist()) if packed.num_rows else set()
        if baseline.audit["eligible_matches"] > 100:
            raise ValueError("Cannot compare a truncated baseline result")
        if baseline_ids != packed_ids:
            raise ValueError("Current cache changed the exact result set")
        rounds.append({"repeat": number + 1, "full_mirror_seconds": full_seconds,
                       "target_pack_seconds": pack_seconds,
                       "matched_unique_arxiv_ids": len(baseline_ids),
                       "full_mirror_rows_scanned": baseline.audit["scanned_rows"],
                       "target_pack_rows_scanned": audit["target_pack_rows_scanned"]})
    full = [row["full_mirror_seconds"] for row in rounds]
    pack = [row["target_pack_seconds"] for row in rounds]
    report = {"version": "priority-arxiv-path-baseline-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "scope": "local_arxiv_retrieval_only_not_user_end_to_end_or_new_priority_corpus",
              "plan": plan, "repeats": rounds,
              "summary": {"full_mirror_p50_seconds": median(full),
                          "full_mirror_p95_seconds": _percentile(full, .95),
                          "target_pack_p50_seconds": median(pack),
                          "target_pack_p95_seconds": _percentile(pack, .95),
                          "same_result_set_each_repeat": True},
              "limitations": ["First repeat is not a guaranteed cold-disk measurement.",
                              "OpenAlex, query planning, candidate scoring and UI are excluded.",
                              "One phrase cannot establish speed for arbitrary Russian queries."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
