"""Read-only arXiv count probe for unreviewed local RU→EN concept proposals."""

from __future__ import annotations

import argparse
from datetime import date
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.arxiv_trigram_index import exact_search, supports_plan
from saia.priority_concept_search import exact_concept_search


def single_group_legacy_allowed(groups: list[list[str]], issues: list[str],
                                enabled: bool) -> bool:
    return (enabled and len(groups) == 1
            and issues == ["concept_search_shape_unsupported_or_single_group"]
            and supports_plan({"included_terms": groups[0]}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date-from", default="2000-01-01")
    parser.add_argument("--as-of-date", default="2026-09-01")
    parser.add_argument("--allow-single-group-legacy", action="store_true",
                        help="Diagnostic only: use existing OR index for one concept group")
    parser.add_argument("--include-ids", action="store_true",
                        help="Store the complete matched ID set for bounded overlap checks")
    args = parser.parse_args()
    start, cutoff = date.fromisoformat(args.date_from), date.fromisoformat(args.as_of_date)
    if start >= cutoff:
        raise ValueError("Concept probe period must be non-empty")
    if args.output.exists():
        raise FileExistsError("Concept probe report is immutable")
    proposals = json.loads(args.proposals.read_text(encoding="utf-8"))
    if proposals.get("version") not in {"ru-concept-plan-diagnostic-v1",
                                         "ru-concept-plan-diagnostic-v2",
                                         "ru-concept-plan-diagnostic-v3",
                                         "ru-concept-plan-diagnostic-v4"}:
        raise ValueError("Unknown frozen concept proposal format")
    rows = []
    for item in proposals["rows"]:
        row = {"case_id": item["case_id"], "query_ru": item["query_ru"],
               "role": item["role"]}
        if item["status"] != "parsed":
            row.update({"status": "model_parse_failed", "arxiv_ids": None})
            rows.append(row)
            continue
        validation = item["structural_validation"]
        groups = [group["alternatives_en"] for group in
                  item["proposal_unreviewed"]["concept_groups"]]
        single_group = single_group_legacy_allowed(
            groups, validation["issues"], args.allow_single_group_legacy)
        if not single_group and (validation["issues"] or not
                                 validation["index_plan_shape_supported"]):
            row.update({"status": "unreviewed_plan_not_index_executable",
                        "structural_issues": validation["issues"], "arxiv_ids": None})
            rows.append(row)
            continue
        plan = ({"included_terms": groups[0], "exclusions": [],
                 "date_from": start.isoformat(), "as_of_date": cutoff.isoformat()}
                if single_group else
                {"concept_groups": groups, "exclusions": [],
                 "date_from": start.isoformat(), "as_of_date": cutoff.isoformat()})
        started = perf_counter()
        try:
            result = (exact_search(args.index, plan) if single_group else
                      exact_concept_search(args.index, plan))
        except ValueError as error:
            if str(error) in {"Concept query intersects duplicate source IDs",
                              "Indexed query intersects duplicate source IDs"}:
                row.update({"status": "duplicate_guard_requires_full_scan",
                            "arxiv_ids": None})
            elif str(error) == "Concept cohort exceeds safe limit; refine the plan":
                row.update({"status": "too_broad_for_safe_probe",
                            "arxiv_ids": None})
            else:
                raise
        else:
            row.update({"status": "exact_index_probe_complete",
                        "search_policy": "single_group_legacy_or" if single_group else
                                         "required_concept_groups",
                        "arxiv_ids": len(result["arxiv_ids"]),
                        "coarse_candidates": result["audit"].get("coarse_candidates",
                            result["audit"].get("coarse_hits_with_possible_or_repetition")),
                        "sample_arxiv_urls": [f"https://arxiv.org/abs/{identifier}"
                                              for identifier in result["arxiv_ids"][:5]]})
            if args.include_ids:
                row["matched_arxiv_ids"] = result["arxiv_ids"]
                row["matched_id_set_complete"] = True
        row["seconds"] = perf_counter() - started
        rows.append(row)
    report = {"version": "ru-concept-plan-arxiv-probe-v3" if args.include_ids else
              "ru-concept-plan-arxiv-probe-v2" if args.allow_single_group_legacy else
              "ru-concept-plan-arxiv-probe-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "proposals_sha256": sha256_file(args.proposals),
              "index_manifest_sha256": sha256_file(args.index / "manifest.json"),
              "period": {"from": start.isoformat(), "as_of_exclusive": cutoff.isoformat()},
              "rows": rows,
              "status_counts": {status: sum(row["status"] == status for row in rows)
                                for status in sorted({row["status"] for row in rows})},
              "policy": {"model_proposals_unreviewed": True,
                         "not_executed_in_user_route": True,
                         "no_match_not_no_science": True,
                         "literal_index_probe_not_relevance_or_weak_signal_detection": True,
                         "single_group_legacy_or_opt_in": args.allow_single_group_legacy,
                         "matched_ids_included": args.include_ids,
                         "source_text_is_current_snapshot_not_historical_v1": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["status_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
