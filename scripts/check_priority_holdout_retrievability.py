"""Check literal arXiv availability for a frozen external list, not discovery."""

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
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Holdout retrievability report is immutable")
    holdout = json.loads(args.holdout.read_text(encoding="utf-8"))
    if holdout.get("version") != "priority-external-reference-holdout-v1":
        raise ValueError("Unknown frozen reference holdout")
    cases = []
    for row in holdout["items"]:
        plan = {"included_terms": [row["title"]], "exclusions": [],
                "date_from": "2000-01-01", "as_of_date": "2022-01-01"}
        if not supports_plan(plan):
            cases.append({"reference_id": row["id"], "title": row["title"],
                          "source_category": row["category"],
                          "scope_control": row["role"] == "out_of_scientific_scope_control",
                          "status": "unsupported_phrase", "count": None})
            continue
        started = perf_counter()
        try:
            found = exact_search(args.index, plan)
        except ValueError as error:
            if str(error) != "Indexed query intersects duplicate source IDs":
                raise
            cases.append({"reference_id": row["id"], "title": row["title"],
                          "source_category": row["category"],
                          "scope_control": row["role"] == "out_of_scientific_scope_control",
                          "status": "requires_full_mirror_due_duplicate", "count": None,
                          "seconds": perf_counter() - started})
            continue
        identifiers = found["arxiv_ids"]
        cases.append({"reference_id": row["id"], "title": row["title"],
                      "source_category": row["category"],
                      "scope_control": row["role"] == "out_of_scientific_scope_control",
                      "status": "literal_search_complete_on_pinned_mirror",
                      "count": len(identifiers),
                      "sample_arxiv_urls": [f"https://arxiv.org/abs/{identifier}"
                                            for identifier in identifiers[:5]],
                      "seconds": perf_counter() - started})
    report = {"version": "priority-holdout-literal-retrievability-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "holdout_sha256": sha256_file(args.holdout),
              "index_manifest_sha256": sha256_file(args.index / "manifest.json"),
              "time_filter": {"first_submission_from": "2000-01-01",
                              "first_submission_before": "2022-01-01"},
              "cases": cases,
              "policy": {"reference_titles_are_seeded_queries": True,
                         "not_independent_detection": True,
                         "literal_match_is_not_relevance": True,
                         "source_text_is_current_snapshot_not_historical_v1": True,
                         "no_match_is_not_evidence_of_no_science": True,
                         "arxiv_only_not_openalex_or_patents": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({row["reference_id"]: row["count"] for row in cases},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
