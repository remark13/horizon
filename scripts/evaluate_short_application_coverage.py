"""Diagnostic metadata concept coverage versus the developer review packet."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from saia.compiled_phrase_matching import concept_coverage
from saia.controlled_collection import sha256_file
from scripts.check_short_application_metadata_review import check


VERSION = "free-ru-short-application-coverage-evaluation-v1"


def evaluate(packet_path: Path, review_path: Path, groups_path: Path) -> dict:
    check(packet_path, review_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    config = json.loads(groups_path.read_text(encoding="utf-8"))
    if config.get("version") != "free-ru-short-application-coverage-groups-v1":
        raise ValueError("Unexpected group config")
    cases = {row["case_id"]: row for row in config["cases"]}
    labels = {row["case_id"]: set(row["full_qualifiers_observed_ranks"])
              for row in review["cases"]}
    if set(cases) != set(labels):
        raise ValueError("Group config does not cover all reviewed cases")
    rows = []
    counts = Counter()
    for paper in packet["records"]:
        case_id = paper["case_id"]
        coverage = concept_coverage(paper, cases[case_id]["groups"],
                                    matching_version=config["matching_version"])
        observed = coverage["status"] == "all_groups_observed"
        developer_observed = paper["retrieval_rank"] in labels[case_id]
        counts[(case_id, developer_observed, observed)] += 1
        rows.append({"case_id": case_id, "rank": paper["retrieval_rank"],
                     "openalex_id": paper["openalex_id"],
                     "developer_metadata_observed": developer_observed,
                     "machine_all_groups_observed": observed,
                     "coverage": coverage})
    return {"version": VERSION, "packet_sha256": sha256_file(packet_path),
            "review_sha256": sha256_file(review_path),
            "groups_sha256": sha256_file(groups_path),
            "developer_terms_not_independent": True,
            "not_relevance_or_weak_signal_precision": True,
            "counts": [{"case_id": case, "developer_metadata_observed": developer,
                        "machine_all_groups_observed": machine, "count": number}
                       for (case, developer, machine), number in sorted(counts.items())],
            "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic output is immutable")
    result = evaluate(args.packet, args.review, args.groups)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
