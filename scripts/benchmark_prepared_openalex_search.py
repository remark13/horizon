"""Reproducible component-only benchmark of pinned OpenAlex cohort lookup."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
from time import perf_counter

from saia.prepared_openalex_search import scan


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def benchmark(config_path: Path) -> dict:
    raw = config_path.read_bytes()
    config = json.loads(raw)
    if config.get("version") != "prepared-openalex-search-benchmark-v1":
        raise ValueError("Unknown benchmark configuration")
    count = config["repetitions"]
    if not isinstance(count, int) or not 3 <= count <= 20:
        raise ValueError("Benchmark repetitions must be between 3 and 20")
    project_root = Path(__file__).resolve().parents[1]
    directory = project_root / config["cohort_dir"]
    start = date.fromisoformat(config["date_from"])
    cutoff = date.fromisoformat(config["as_of_date"])
    samples = []
    result = None
    for _ in range(count):
        started = perf_counter()
        result = scan(directory, config["branch_specs"], start, cutoff,
                      config["limit_per_branch"])
        samples.append(round(perf_counter() - started, 6))
    assert result is not None
    return {
        "version": "prepared-openalex-component-benchmark-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest_sha256": result["manifest_sha256"],
        "source_parquet_sha256": result["parquet_sha256"],
        "source_unique_ids": result["available_unique_ids"],
        "component": "exact lookup after phrase compilation only",
        "not_measured": ["RU query compilation", "live retrieval", "candidate grouping",
                         "weak-signal quality", "end-to-end response time"],
        "seconds": {"samples": samples,
                    "median": round(statistics.median(samples), 6),
                    "p95_nearest_rank": round(_p95(samples), 6)},
        "branches": [{
            "branch_id": branch["branch_id"],
            "eligible_matches_in_prepared_query_cohorts": branch[
                "eligible_matches_in_prepared_query_cohorts"],
            "selected": len(branch["works"]),
            "source_mission_ids": branch["selected_source_mission_ids"],
            "year_counts": branch["year_counts"],
        } for branch in result["branches"]],
        "administrative_rows_excluded": result["administrative_rows_excluded"],
        "variant_rows_collapsed": result["variant_rows_collapsed"],
        "complete_for_arbitrary_query": result["complete_for_arbitrary_query"],
        "coverage_comparable": result["coverage_comparable"],
        "limitations": result["limitations"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path,
                        default=Path("config/prepared-openalex-search-benchmark-v1.json"))
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    report = benchmark(options.config)
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if options.output:
        if options.output.exists():
            raise FileExistsError(options.output)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
