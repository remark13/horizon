"""Compare additive concept-group index results with the frozen full scan."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search, supports_concept_plan


def main() -> None:
    import pyarrow.parquet as pq

    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--case-config", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Verification report is immutable")
    pilot = json.loads(args.pilot_report.read_text(encoding="utf-8"))
    config = json.loads(args.case_config.read_text(encoding="utf-8"))
    index_manifest = json.loads((args.index / "manifest.json").read_text(encoding="utf-8"))
    corpus_manifest = json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))
    if (pilot.get("version") != "priority-arxiv-full-pilot-v1"
            or config.get("version") != "priority-arxiv-pilot-v2"
            or pilot["input_fingerprints"]["case_config_sha256"] != sha256_file(args.case_config)
            or corpus_manifest["source"]["full_scan_report_sha256"] != sha256_file(args.pilot_report)
            or index_manifest["source"]["full_inventory_sha256"] != pilot[
                "input_fingerprints"]["arxiv_inventory_sha256"]):
        raise ValueError("Pilot, corpus and index do not share pinned inputs")
    assignments = pq.read_table(args.corpus / "assignments.parquet",
                                columns=["catalog_id", "arxiv_id"]).to_pylist()
    by_case = {}
    for row in assignments:
        by_case.setdefault(row["catalog_id"], set()).add(row["arxiv_id"])
    pilot_counts = {row["catalog_id"]: row["eligible_unique_in_pinned_mirror"]
                    for row in pilot["cases"]}
    cases = []
    for case in config["cases"]:
        identifier = case["id"]
        expected = by_case.get(identifier, set())
        if len(expected) != pilot_counts[identifier]:
            raise ValueError("Saved corpus does not match full-scan count")
        terms = case["all_terms"]
        if len(terms) < 2:
            cases.append({"case_id": identifier, "status": "single_term_no_concept_comparison",
                          "full_scan_ids": len(expected)})
            continue
        plan = {"concept_groups": [[term] for term in terms], "exclusions": [],
                "date_from": pilot["period"]["from"],
                "as_of_date": pilot["period"]["as_of_exclusive"]}
        if not supports_concept_plan(plan):
            cases.append({"case_id": identifier, "status": "unsupported_terms",
                          "full_scan_ids": len(expected)})
            continue
        started = perf_counter()
        try:
            result = exact_concept_search(args.index, plan)
        except ValueError as error:
            if str(error) != "Concept query intersects duplicate source IDs":
                raise
            cases.append({"case_id": identifier, "status": "duplicate_guard_full_scan_required",
                          "full_scan_ids": len(expected),
                          "seconds": perf_counter() - started})
            continue
        found = set(result["arxiv_ids"])
        missing = sorted(expected - found)
        extra = sorted(found - expected)
        cases.append({"case_id": identifier,
                      "status": "exact_id_equivalent" if not (missing or extra)
                      else "policy_non_equivalent",
                      "full_scan_ids": len(expected), "index_ids": len(found),
                      "full_scan_only_count": len(missing),
                      "concept_index_only_count": len(extra),
                      "full_scan_only_examples": missing[:20],
                      "concept_index_only_examples": extra[:20],
                      "coarse_candidates": result["audit"]["coarse_candidates"],
                      "seconds": perf_counter() - started})
    report = {"version": "priority-concept-index-equivalence-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "pilot_report_sha256": sha256_file(args.pilot_report),
              "case_config_sha256": sha256_file(args.case_config),
              "corpus_manifest_sha256": sha256_file(args.corpus / "manifest.json"),
              "index_manifest_sha256": sha256_file(args.index / "manifest.json"),
              "counts": {status: sum(row["status"] == status for row in cases)
                         for status in sorted({row["status"] for row in cases})},
              "cases": cases,
              "limits": {"pilot_terms_are_seeded_not_user_queries": True,
                         "literal_retrieval_not_relevance_or_signal_detection": True,
                         "single_term_cases_not_compared": True,
                         "duplicate_guard_requires_full_scan": True,
                         "index_and_full_scan_policies_may_normalize_whitespace_differently": True,
                         "current_snapshot_text_not_historical_v1": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
