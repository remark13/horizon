"""Bounded live OpenAlex preview for frozen compound cases, with local postfilter.

Search returns at most the first page of 25 OpenAlex results. A zero after
postfilter says nothing about source-wide recall or absence of research.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.compiled_phrase_matching import matches_spec
from saia.controlled_collection import sha256_file
from saia.discovery import discover_openalex_only
from saia.openalex_boolean_query import concept_expression


def run(plan_path: Path, live_config_path: Path, *, mode: str = "plain") -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    config = json.loads(live_config_path.read_text(encoding="utf-8"))
    goal_followup = plan.get("version") == "goal-cross-domain-compound-pilot-v1"
    if plan.get("version") not in {"ru-compound-multicase-pilot-v1",
                                   "goal-cross-domain-compound-pilot-v1"}:
        raise ValueError("Unknown compound case plan")
    expected_config = ("goal-cross-domain-openalex-followup-v1" if goal_followup
                       else "ru-compound-openalex-live-pilot-v1")
    if config.get("version") != expected_config:
        raise ValueError("Unknown live probe plan")
    if mode not in {"plain", "boolean"}:
        raise ValueError("Unknown live query mode")
    if goal_followup and mode != "boolean":
        raise ValueError("Goal follow-up accepts only the frozen Boolean mode")
    if sha256_file(plan_path) != config["frozen_compound_plan_sha256"]:
        raise ValueError("Compound case plan changed after live probe was fixed")
    if (config["date_from"] != plan["date_from"]
            or config["as_of_date"] != plan["as_of_date"]
            or config["limit_per_query"] != 25):
        raise ValueError("Live pilot period or limit changed")
    by_id = {case["case_id"]: case for case in plan["cases"]}
    if len(by_id) != len(plan["cases"]):
        raise ValueError("Duplicate case ID")
    live_ids = [case["case_id"] for case in config["cases"]]
    if (len(live_ids) != len(set(live_ids)) or not live_ids
            or (goal_followup and not set(live_ids) <= set(by_id))
            or (not goal_followup and set(live_ids) != set(by_id))):
        raise ValueError("Live and pinned cases differ")
    rows = []
    for case in config["cases"]:
        query = (case["live_query_en"] if mode == "plain" else
                 concept_expression(by_id[case["case_id"]]["concept_groups"]))
        started = perf_counter()
        source = discover_openalex_only(
            query, date.fromisoformat(config["date_from"]),
            date.fromisoformat(config["as_of_date"]), config["limit_per_query"])
        spec = {"included_phrases": [],
                "concept_groups": by_id[case["case_id"]]["concept_groups"],
                "excluded_phrases": []}
        works = []
        for item in source.works:
            work = asdict(item)
            work["strict_compound_match"] = matches_spec(work, spec)
            works.append(work)
        rows.append({"case_id": case["case_id"], "query_en": query,
                     "fetched_at": source.fetched_at, "seconds": perf_counter() - started,
                     "returned_count": len(works),
                     "strict_compound_count": sum(w["strict_compound_match"] for w in works),
                     "works": works, "errors": source.errors,
                     "collection_policy": source.collection_policy})
    return {"version": ("goal-cross-domain-openalex-followup-v1" if goal_followup
                        else "ru-compound-openalex-live-probe-v1"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "case_plan_sha256": sha256_file(plan_path),
            "live_config_sha256": sha256_file(live_config_path),
            "period": {"from": config["date_from"],
                       "as_of_exclusive": config["as_of_date"]},
            "query_mode": mode,
            "rows": rows,
            "policy": {"one_bounded_page_per_case": True,
                       "live_recall_proven": False,
                       "strict_match_is_not_topical_relevance": True,
                       "zero_not_absence_of_science": True,
                       "selected_after_arxiv_probe": goal_followup,
                       "boundary_query_previously_previewed": not goal_followup,
                       "not_independent_quality_benchmark": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--live-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["plain", "boolean"], default="plain")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Live probe output is immutable")
    report = run(args.plan, args.live_config, mode=args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({row["case_id"]: {"returned": row["returned_count"],
                                        "strict": row["strict_compound_count"],
                                        "errors": row["errors"]}
                      for row in report["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
