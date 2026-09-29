"""Evaluate a frozen two-axis local-model pilot against developer labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "two-axis-hypothesis-type-pilot-comparison-v1"


def compare(report_path: Path, reference_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if (report.get("version") not in {
            "two-axis-hypothesis-local-model-pilot-v1",
            "two-axis-hypothesis-local-model-pilot-v2",
            "two-axis-hypothesis-local-model-pilot-v3"}
            or reference.get("version") not in {
                "goal-pilot-two-axis-hypothesis-types-v1",
                "goal-two-axis-type-holdout-v1"}
            or report.get("reference_sha256") != sha256_file(reference_path)
            or len(report.get("rows") or []) != 16):
        raise ValueError("Incompatible or incomplete frozen two-axis pilot")
    if (reference["version"] == "goal-two-axis-type-holdout-v1"
            and report["version"] != reference.get("prompt_version_to_test")):
        raise ValueError("Holdout prompt version differs")
    expected = {row["id"]: row for row in reference["rows"]}
    if len(expected) != 16 or {row["id"] for row in report["rows"]} != set(expected):
        raise ValueError("Pilot IDs differ")
    rows = []
    for row in report["rows"]:
        target = expected[row["id"]]
        proposal = row.get("proposal") or {}
        technical = proposal.get("technical_entity_type")
        claim = proposal.get("signal_claim_type")
        customer = row["source_role"] == "customer_supplied_signal_example"
        rows.append({
            "id": row["id"], "source_role": row["source_role"],
            "title": row["source_title"], "status": row["status"],
            "expected_technical": target["technical_entity_type"],
            "proposed_technical": technical,
            "technical_match": technical == target["technical_entity_type"],
            "expected_claim": target["signal_claim_type"],
            "proposed_claim": claim,
            "claim_match": claim == target["signal_claim_type"],
            "customer": customer,
            "needs_review": proposal.get("needs_review"),
        })
    gate = reference["acceptance_gate"]
    parsed = sum(row["status"] == "parsed_machine_draft" for row in rows)
    technical_matches = sum(row["technical_match"] for row in rows)
    customer_claim_matches = sum(row["customer"] and row["claim_match"]
                                 for row in rows)
    critical = [row["id"] for row in rows
                if row["customer"] and row["expected_claim"] == "product_market"
                and row["proposed_claim"] == "scientific_method_material"]
    passed = (parsed == gate["parsed_rows"]
              and technical_matches >= gate["min_technical_matches"]
              and customer_claim_matches >= gate["min_customer_claim_matches"]
              and len(critical) <= gate["max_product_market_claim_as_scientific_method"])
    return {
        "version": VERSION,
        "report_sha256": sha256_file(report_path),
        "reference_sha256": sha256_file(reference_path),
        "counts": {"pilot": 16, "parsed": parsed,
                   "technical_matches": technical_matches,
                   "customer_claim_matches": customer_claim_matches,
                   "customer_claim_total": 6,
                   "critical_market_claim_as_science": len(critical)},
        "critical_ids": critical,
        "gate_passed_for_draft_expansion_only": passed,
        "rows": rows,
        "limitations": {"developer_labels_not_independent_gold": True,
                        "model_never_authorizes_production_routing": True,
                        "not_weak_signal_accuracy": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Comparison output is immutable")
    result = compare(args.report, args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                        indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result["counts"],
                      "gate_passed": result["gate_passed_for_draft_expansion_only"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
