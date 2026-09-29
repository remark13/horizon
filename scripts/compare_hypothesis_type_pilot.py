"""Compare frozen local-model type drafts with developer-reviewed pilot roles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.controlled_collection import sha256_file
from saia.hypothesis_source_roles import audit


VERSION = "hypothesis-type-model-pilot-comparison-v1"


def compare(report_path: Path, manual_path: Path, gate_path: Path,
            catalog_path: Path, pilot_path: Path) -> dict:
    audit_result = audit(manual_path, catalog_path, pilot_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if (report.get("version") != "hypothesis-type-local-model-draft-v1"
            or report.get("scope") != "pilot16"
            or report.get("catalog_sha256") != audit_result["catalog_sha256"]
            or report.get("pilot_sha256") != audit_result["pilot_sha256"]
            or gate.get("version") != "hypothesis-type-model-gate-v1"
            or gate.get("fixed_pilot_count") != 16
            or len(report.get("rows") or []) != 16):
        raise ValueError("Incompatible fixed model, catalog or gate")
    expected = {row["id"]: row for row in manual["rows"]}
    if len(expected) != 16 or {row["id"] for row in report["rows"]} != set(expected):
        raise ValueError("Pilot IDs differ")
    compared = []
    for row in report["rows"]:
        target = expected[row["id"]]
        proposal = row.get("proposal") or {}
        predicted = proposal.get("primary_type")
        if row["status"] != "parsed_machine_draft":
            predicted = None
        compared.append({
            "id": row["id"], "source_role": row["source_role"],
            "title": row["source_title"],
            "developer_primary_type": target["primary_type"],
            "model_primary_type": predicted,
            "model_needs_review": proposal.get("needs_review"),
            "exact_primary_agreement": predicted == target["primary_type"],
        })
    parsed = sum(item["model_primary_type"] is not None for item in compared)
    exact = sum(item["exact_primary_agreement"] for item in compared)
    critical = [item["id"] for item in compared
                if item["source_role"] == "customer_supplied_signal_example"
                and item["developer_primary_type"] == "product_market"
                and item["model_primary_type"] == "scientific_method_material"]
    passed = (parsed >= gate["minimum_parsed"]
              and exact >= gate["minimum_exact_primary_agreement"]
              and (not gate["forbid_customer_market_to_scientific_method_confusion"]
                   or not critical))
    return {
        "version": VERSION,
        "model_report_sha256": sha256_file(report_path),
        "manual_roles_sha256": sha256_file(manual_path),
        "gate_sha256": sha256_file(gate_path),
        "counts": {"pilot": 16, "parsed": parsed,
                   "exact_primary_agreement": exact,
                   "critical_market_to_science_confusions": len(critical)},
        "gate_passed_for_unreviewed_189_draft_only": passed,
        "critical_ids": critical,
        "rows": compared,
        "limitations": {
            "developer_types_not_independent_gold": True,
            "passing_does_not_authorize_production_routing": True,
            "passing_does_not_prove_weak_signal_accuracy": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manual", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Comparison output is immutable")
    result = compare(args.report, args.manual, args.gate,
                     args.catalog, args.pilot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result["counts"],
                      "gate_passed": result["gate_passed_for_unreviewed_189_draft_only"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
