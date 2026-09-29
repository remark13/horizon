"""Compare legacy OR controlled-plan semantics with seeded pilot AND terms."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.arxiv_trigram_index import exact_search, supports_plan
from saia.controlled_collection import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Semantics audit is immutable")
    pilot = json.loads(args.pilot_report.read_text(encoding="utf-8"))
    if pilot.get("version") != "priority-arxiv-full-pilot-v1":
        raise ValueError("Unexpected full mirror pilot version")
    cases = [case for case in pilot["cases"] if case["catalog_id"] == args.case_id]
    if len(cases) != 1:
        raise ValueError("Expected exactly one pilot case")
    case = cases[0]
    if len(case["terms_and"]) < 2:
        raise ValueError("Semantics audit needs at least two terms")
    plan = {"included_terms": case["terms_and"], "exclusions": [],
            "date_from": pilot["period"]["from"],
            "as_of_date": pilot["period"]["as_of_exclusive"]}
    if not supports_plan(plan):
        raise ValueError("Index does not support literal pilot terms")
    started = perf_counter()
    found = exact_search(args.index, plan)
    elapsed = perf_counter() - started
    report = {"version": "priority-query-semantics-audit-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "pilot_report_sha256": sha256_file(args.pilot_report),
              "index_manifest_sha256": sha256_file(args.index / "manifest.json"),
              "case_id": args.case_id, "period": pilot["period"],
              "terms": case["terms_and"],
              "pilot_all_terms_and_count": case["eligible_unique_in_pinned_mirror"],
              "controlled_plan_any_term_or_count": len(found["arxiv_ids"]),
              "controlled_plan_or_coarse_hits": found["audit"][
                  "coarse_hits_with_possible_or_repetition"],
              "controlled_plan_or_seconds": elapsed,
              "counts_are_not_directly_comparable_as_relevance_or_precision": True,
              "both_are_seeded_literal_retrieval_not_weak_signal_detection": True,
              "warning": "The pilot all_terms are conjunctions; production included_terms are alternatives. Never treat the pilot cohort as a quality estimate of the production free-query route."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"and": report["pilot_all_terms_and_count"],
                      "or": report["controlled_plan_any_term_or_count"]}))


if __name__ == "__main__":
    main()
