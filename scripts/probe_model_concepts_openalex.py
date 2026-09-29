"""Read-only bounded OpenAlex probe of frozen, unreviewed local model plans.

The output is a retrieval diagnostic, never an automatic query approval,
topic relevance judgement, historical corpus, or weak-signal result.
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


VERSION = "unreviewed-model-concepts-openalex-probe-v1"


def run(proposals_path: Path, *, date_from: date, as_of_date: date,
        limit: int = 25, discover=discover_openalex_only) -> dict:
    if date_from >= as_of_date or limit != 25:
        raise ValueError("Frozen probe requires a valid period and first page of 25")
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    if proposals.get("version") != "ru-concept-plan-diagnostic-v2":
        raise ValueError("Expected frozen local model proposal v2")
    rows = proposals.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
        raise ValueError("Unsafe model proposal count")
    seen = set()
    results = []
    for row in rows:
        identifier = row["case_id"]
        if identifier in seen:
            raise ValueError("Duplicate model case")
        seen.add(identifier)
        item = {"case_id": identifier, "role": row["role"],
                "query_ru": row["query_ru"]}
        proposal = row.get("proposal_unreviewed") or {}
        validation = row.get("structural_validation") or {}
        if (row.get("status") != "parsed" or validation.get("issues")
                or not validation.get("index_plan_shape_supported")
                or proposal.get("needs_human_review")
                or proposal.get("ambiguities_ru")):
            item.update({"status": "proposal_not_executable",
                         "structural_issues": validation.get("issues") or []})
            results.append(item)
            continue
        groups = [group["alternatives_en"] for group in proposal["concept_groups"]]
        expression = concept_expression(groups)
        started = perf_counter()
        source = discover(expression, date_from, as_of_date, limit)
        spec = {"included_phrases": [], "concept_groups": groups,
                "excluded_phrases": []}
        works = []
        for publication in source.works:
            work = asdict(publication)
            work["literal_concept_match"] = matches_spec(work, spec)
            works.append(work)
        item.update({
            "status": "source_error" if source.errors else "first_page_observed",
            "english_query": expression,
            "seconds": perf_counter() - started,
            "fetched_at": source.fetched_at,
            "returned_first_page": len(works),
            "literal_concept_matches_first_page": sum(
                work["literal_concept_match"] for work in works),
            "source_errors": source.errors,
            "works": works,
        })
        results.append(item)
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "proposals_sha256": sha256_file(proposals_path),
        "period": {"from": date_from.isoformat(),
                   "as_of_exclusive": as_of_date.isoformat()},
        "rows": results,
        "limitations": {
            "model_plans_are_unreviewed": True,
            "first_page_only_not_recall_or_temporal_series": True,
            "literal_match_is_not_semantic_relevance": True,
            "no_automatic_production_approval": True,
            "results_are_not_weak_signals": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe output is immutable")
    result = run(args.proposals, date_from=args.date_from,
                 as_of_date=args.as_of_date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({row["case_id"]: {
        "status": row["status"],
        "returned": row.get("returned_first_page"),
        "literal": row.get("literal_concept_matches_first_page"),
    } for row in result["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
