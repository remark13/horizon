"""Evaluate a provisional developer review against the frozen selection manifest."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from saia.controlled_collection import sha256_file


VERSION = "title-anchor-developer-evaluation-v1"
ANSWERS = {"yes", "no", "unclear"}
ROLES = {"primary_result", "review", "application", "unclear"}


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"\s+", " ", value).casefold().split())


def evaluate(blind: dict, manifest: dict, review: dict,
             *, blind_sha256: str, manifest_sha256: str, review_sha256: str) -> dict:
    if (blind.get("version") != "title-anchor-paper-review-v2"
            or manifest.get("version") != "title-anchor-paper-review-v2-selection-manifest"
            or review.get("version") != "title-anchor-developer-review-v1"
            or review.get("blind_packet_sha256") != blind_sha256
            or review.get("independent_expert_review") is not False):
        raise ValueError("Review provenance or role mismatch")
    cases = {row["case_id"]: row for row in blind["cases"]}
    selection = {row["case_id"]: row for row in manifest["rows"]}
    labels = {row["case_id"]: row for row in review["cases"]}
    if (len(cases) != blind["case_count"] or len(selection) != len(cases)
            or len(labels) != len(cases)
            or set(cases) != set(selection) or set(cases) != set(labels)):
        raise ValueError("Cases are missing, duplicated or unaligned")
    if set(review["line_review"]) != {case["proposed_line_en"] for case in cases.values()}:
        raise ValueError("Phrase-level judgements incomplete")
    counts = defaultdict(Counter)
    by_role = defaultdict(Counter)
    for case_id, case in cases.items():
        label = labels[case_id]
        selected = selection[case_id]
        if (label["own_result"] not in ANSWERS
                or label["bas_core"] not in ANSWERS
                or label["role"] not in ROLES
                or selected["phrase_en"] != case["proposed_line_en"]
                or selected["arxiv_id"] != case["paper"]["arxiv_id"]):
            raise ValueError(f"Invalid label or provenance for {case_id}")
        if (not label["quote"] or not label["reason"]
                or _normalize(label["quote"]) not in _normalize(
                    case["paper"]["title"] + " " + case["paper"]["abstract"])):
            raise ValueError(f"Unsupported quote for {case_id}")
        stratum = selected["selection_stratum"]
        if stratum not in {"title", "abstract_only"}:
            raise ValueError("Unexpected review stratum")
        counts[stratum][label["own_result"]] += 1
        by_role[selected["selection_role"]][label["own_result"]] += 1
    return {"version": VERSION,
            "blind_sha256": blind_sha256,
            "selection_manifest_sha256": manifest_sha256,
            "developer_review_sha256": review_sha256,
            "case_count": len(cases),
            "line_specificity_provisional": dict(Counter(
                value["specific_technology"] for value in review["line_review"].values())),
            "own_result_by_selection_stratum": {key: dict(value)
                                                for key, value in counts.items()},
            "own_result_by_selection_role": {key: dict(value)
                                             for key, value in by_role.items()},
            "independent_expert_review": False,
            "population_precision_estimated": False,
            "weak_signal_accuracy_measured": False,
            "production_changed": False,
            "interpretation": "Single-developer labels on a deliberately balanced 28-paper sample diagnose failure modes; they do not estimate real-world precision or confirm weak signals."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    blind = json.loads(args.blind.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    result = evaluate(blind, manifest, review,
                      blind_sha256=sha256_file(args.blind),
                      manifest_sha256=sha256_file(args.manifest),
                      review_sha256=sha256_file(args.review))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_count": result["case_count"],
                      "by_stratum": result["own_result_by_selection_stratum"]}))


if __name__ == "__main__":
    main()
