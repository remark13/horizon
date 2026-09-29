"""Evaluate frozen model query proposals against a fixed literal-search pilot.

Matched ID overlap is not relevance, recall, or weak-signal accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

from saia.controlled_collection import sha256_file


def _by_id(rows: list[dict]) -> dict[str, dict]:
    indexed = {row["case_id"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate case ID in comparison")
    return indexed


def evaluate(*, gate: dict, proposals: dict, probe: dict,
             baseline: dict, review: dict,
             proposals_sha256: str, probe_sha256: str) -> dict:
    if (proposals["cases_sha256"] != gate["source_cases_sha256"]
            or baseline["config_sha256"] != gate["source_cases_sha256"]
            or baseline["index_manifest_sha256"] != gate["arxiv_index_manifest_sha256"]
            or probe["index_manifest_sha256"] != gate["arxiv_index_manifest_sha256"]
            or probe["proposals_sha256"] != proposals_sha256
            or review["proposal_sha256"] != proposals_sha256
            or proposals["model"] != gate["model"]
            or probe["period"]["from"] != gate["source_period"]["date_from"]
            or probe["period"]["as_of_exclusive"] != gate["source_period"]["as_of_date_exclusive"]
            or baseline["period"]["from"] != gate["source_period"]["date_from"]
            or baseline["period"]["as_of_exclusive"] != gate["source_period"]["as_of_date_exclusive"]):
        raise ValueError("Frozen inputs, source period or model do not match")
    proposed = _by_id(proposals["rows"])
    probed = _by_id(probe["rows"])
    old = _by_id(baseline["rows"])
    reviewed = _by_id(review["rows"])
    if (not len(proposed) == gate["case_count"]
            or set(proposed) != set(probed) or set(proposed) != set(old)
            or set(proposed) != set(reviewed)
            or review.get("independent_expert_review") is not False):
        raise ValueError("Incomplete or non-development case review")
    checks = gate["decision_gate_for_optional_product_suggestion"]
    parsed = sum(row["status"] == "parsed" for row in proposed.values())
    spans = sum(row["status"] == "parsed" and
                row["structural_validation"]["source_span_check_passed"]
                for row in proposed.values())
    clean = sum(row["status"] == "parsed" and
                not row["structural_validation"]["issues"]
                for row in proposed.values())
    critical = sum(row["judgement"] == "critical_loss" for row in reviewed.values())
    uncertain = sum(row["judgement"] == "uncertain" for row in reviewed.values())
    negative_nonzero = sum(
        row["role"] == "negative_control" and
        (probed[case_id].get("arxiv_ids") or 0) > 0
        for case_id, row in proposed.items()
    )
    comparisons = []
    totals = Counter()
    for case_id, row in proposed.items():
        current, previous = probed[case_id], old[case_id]
        same_complete = (current["status"] == "exact_index_probe_complete"
                         and previous["status"] == "exact_index_probe_complete")
        comparison = {
            "case_id": case_id,
            "role": row["role"],
            "model_status": current["status"],
            "baseline_status": previous["status"],
            "developer_semantic_judgement": reviewed[case_id]["judgement"],
            "same_complete_literal_inventory": same_complete,
        }
        if same_complete:
            baseline_ids = previous.get("arxiv_ids") or []
            model_ids = current.get("matched_arxiv_ids")
            if (model_ids is None or len(model_ids) != current["arxiv_ids"]
                    or len(set(model_ids)) != len(model_ids)
                    or len(set(baseline_ids)) != len(baseline_ids)):
                raise ValueError("Matched ID sets are incomplete or duplicated")
            intersection = len(set(baseline_ids) & set(model_ids))
            comparison.update({
                "baseline_literal_matches": len(baseline_ids),
                "model_literal_matches": len(model_ids),
                "shared_literal_matches": intersection,
                "baseline_only_literal_matches": len(baseline_ids) - intersection,
                "model_only_literal_matches": len(model_ids) - intersection,
            })
            totals["comparable_cases"] += 1
            totals["baseline_match_assignments"] += len(baseline_ids)
            totals["model_match_assignments"] += len(model_ids)
            totals["shared_match_assignments"] += intersection
            if baseline_ids and not model_ids:
                totals["baseline_positive_model_zero_cases"] += 1
        comparisons.append(comparison)
    timings = sorted(row["seconds"] for row in proposed.values())
    def quantile(q: float) -> float:
        pos = (len(timings) - 1) * q
        low, high = int(pos), min(int(pos) + 1, len(timings) - 1)
        return timings[low] + (timings[high] - timings[low]) * (pos - low)
    criteria = {
        "parsed": parsed >= checks["parsed_minimum"],
        "source_spans": spans >= checks["source_span_exact_minimum"],
        "structural_plan": clean >= checks["structurally_index_executable_minimum"],
        "negative_controls": negative_nonzero <= checks["negative_controls_with_nonzero_arxiv_matches_maximum"],
        "critical_semantics": critical <= checks["critical_semantic_errors_maximum"],
        "all_semantically_reviewed": uncertain == 0,
    }
    return {
        "version": "ru-concept-cross-domain-evaluation-v1",
        "gate_version": gate["version"],
        "proposal_sha256": proposals_sha256,
        "probe_sha256": probe_sha256,
        "model_digest": proposals["model_digest"],
        "counts": {
            "cases": len(proposed), "parsed": parsed, "exact_source_spans": spans,
            "clean_structural_plans": clean, "critical_semantic_errors": critical,
            "uncertain_semantic_reviews": uncertain,
            "negative_controls_with_nonzero_matches": negative_nonzero,
            **dict(totals),
        },
        "model_planning_seconds": {
            "median": round(statistics.median(timings), 3),
            "p95": round(quantile(0.95), 3),
            "maximum": round(timings[-1], 3),
        },
        "gate_checks": criteria,
        "optional_product_suggestion_gate_passed": all(criteria.values()),
        "comparison_scope": "literal arXiv ID overlap on identical pinned index and dates, not relevant-work recall or weak-signal accuracy",
        "independent_expert_labels": False,
        "rows": comparisons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gate", "proposals", "probe", "baseline", "review", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    proposal_sha = sha256_file(args.proposals)
    result = evaluate(
        gate=json.loads(args.gate.read_text(encoding="utf-8")),
        proposals=json.loads(args.proposals.read_text(encoding="utf-8")),
        probe=json.loads(args.probe.read_text(encoding="utf-8")),
        baseline=json.loads(args.baseline.read_text(encoding="utf-8")),
        review=json.loads(args.review.read_text(encoding="utf-8")),
        proposals_sha256=proposal_sha,
        probe_sha256=sha256_file(args.probe),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"gate_passed": result["optional_product_suggestion_gate_passed"],
                      "counts": result["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
