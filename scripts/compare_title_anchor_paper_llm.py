"""Reconcile a frozen LLM article probe with provisional developer labels."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "title-anchor-paper-llm-comparison-v1"


def compare(probe: dict, review: dict, *, probe_sha: str, review_sha: str) -> dict:
    if (probe.get("version") != "title-anchor-paper-llm-probe-v1"
            or review.get("version") != "title-anchor-developer-review-v1"
            or probe.get("complete") is not True
            or probe.get("blind_packet_sha256") != review.get("blind_packet_sha256")
            or review.get("independent_expert_review") is not False):
        raise ValueError("Frozen probe or review provenance mismatch")
    labels = {row["case_id"]: row for row in review["cases"]}
    predictions = {row["case_id"]: row for row in probe["rows"]}
    if (len(labels) != len(review["cases"])
            or len(predictions) != len(probe["rows"])
            or set(labels) != set(predictions)):
        raise ValueError("Missing or repeated cases")
    table = defaultdict(Counter)
    false_yes = []
    for case_id, row in predictions.items():
        observed = labels[case_id]["own_result"]
        predicted = (row["prediction"]["own_result"]
                     if row["status"] == "valid_grounded" else "invalid")
        table[observed][predicted] += 1
        if observed == "no" and predicted == "yes":
            false_yes.append({"case_id": case_id,
                              "proposed_line_en": row["proposed_line_en"]})
    return {"version": VERSION,
            "probe_sha256": probe_sha, "developer_review_sha256": review_sha,
            "case_count": len(predictions),
            "valid_grounded": sum(row["status"] == "valid_grounded"
                                  for row in predictions.values()),
            "by_developer_label": {key: dict(value) for key, value in table.items()},
            "developer_negative_model_positive_cases": false_yes,
            "production_recommendation": "reject_current_model_probe",
            "independent_accuracy_measured": False,
            "interpretation": "On a deliberately balanced one-developer sample, model yes on 11 of 13 developer negatives; quote grounding is not semantic correctness. Not a population precision estimate."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    probe = json.loads(args.probe.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    result = compare(probe, review, probe_sha=sha256_file(args.probe),
                     review_sha=sha256_file(args.review))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid_grounded": result["valid_grounded"],
                      "by_developer_label": result["by_developer_label"]}))


if __name__ == "__main__":
    main()
