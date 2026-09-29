"""Exact-result and latency comparison against the complete pinned mirror."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from saia.arxiv_trigram_index import exact_search
from saia.local_arxiv_search import search
from saia.thematic_arxiv_cache import coverage_for_plan, load_cache


PHRASES = ("small modular reactor", "sovereign cloud")


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * .95
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] if low == high else (
        ordered[low] * (high - position) + ordered[high] * (position - low))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists() or not 2 <= args.repeats <= 5:
        raise ValueError("Use a new output and 2–5 repeats")
    _, anchors = load_cache(args.cache)
    cases = []
    for phrase in PHRASES:
        plan = {"included_terms": [phrase], "exclusions": [],
                "date_from": "2024-09-01", "as_of_date": "2026-09-01"}
        rounds = []
        for number in range(args.repeats):
            started = perf_counter()
            baseline = search(args.mirror, plan, limit=100)
            baseline_seconds = perf_counter() - started
            started = perf_counter()
            indexed = exact_search(args.index, plan)
            index_seconds = perf_counter() - started
            if baseline.audit["eligible_matches"] > 100:
                raise ValueError("Full-scan result was truncated")
            baseline_ids = {work.source_ids[0].rsplit("/", 1)[-1]
                            for work in baseline.works}
            indexed_ids = set(indexed["arxiv_ids"])
            if baseline_ids != indexed_ids:
                raise ValueError(f"Index/full-mirror mismatch for {phrase}")
            rounds.append({"repeat": number + 1, "full_seconds": baseline_seconds,
                           "index_seconds": index_seconds,
                           "exact_unique_ids": len(baseline_ids),
                           "index_coarse_hits": indexed["audit"][
                               "coarse_hits_with_possible_or_repetition"]})
        full_times = [row["full_seconds"] for row in rounds]
        index_times = [row["index_seconds"] for row in rounds]
        cases.append({"phrase": phrase, "existing_thematic_cache_coverage":
                      coverage_for_plan(anchors, plan)["complete"],
                      "plan": plan, "rounds": rounds,
                      "summary": {"same_exact_ids_each_repeat": True,
                                  "full_p50_seconds": median(full_times),
                                  "full_p95_seconds": _p95(full_times),
                                  "index_p50_seconds": median(index_times),
                                  "index_p95_seconds": _p95(index_times)}})
    report = {"version": "arxiv-trigram-full-comparison-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "index_manifest": json.loads((args.index / "manifest.json").read_text())["version"],
              "cases": cases,
              "scope": "arxiv_local_retrieval_only_not_user_end_to_end_or_weak_signal_accuracy",
              "limits": ["First repeat is not guaranteed cold disk.",
                         "Two narrow English exact phrases do not represent arbitrary Russian queries.",
                         "OpenAlex, query planning, clustering, scoring and interface are excluded."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({row["phrase"]: row["summary"] for row in cases}, ensure_ascii=False))


if __name__ == "__main__":
    main()
