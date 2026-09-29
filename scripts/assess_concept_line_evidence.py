"""Join a frozen full-index concept line to complete developer paper-role review."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "concept-line-developer-assessment-v1"
ROLES = {"direct_technical_claim", "evaluation_or_benchmark",
         "adjacent_technical_claim", "unclear"}


def assess(inventory: dict, review: dict, inventory_sha256: str) -> dict:
    if (inventory.get("version") != "concept-line-evidence-card-v1"
            or review.get("version") != "concept-line-developer-review-v1"
            or review.get("inventory_sha256") != inventory_sha256
            or review.get("reviewer_role") != "developer"
            or review.get("independent_expert_review") is not False):
        raise ValueError("Evidence or review provenance differs")
    works = {item["arxiv_id"]: item for item in inventory["works"]}
    labels = {item["arxiv_id"]: item for item in review["rows"]}
    if (len(works) != len(inventory["works"])
            or len(labels) != len(review["rows"])
            or set(works) != set(labels)
            or any(item.get("role") not in ROLES or not item.get("reason")
                   for item in review["rows"])):
        raise ValueError("One complete developer review per indexed work required")
    by_role = Counter(item["role"] for item in review["rows"])
    by_year = Counter((int(work["first_submission_date"][:4]) -
                       (work["first_submission_date"][5:7] < "09"),
                       labels[identifier]["role"])
                      for identifier, work in works.items())
    annual = []
    for window in inventory["annual"]:
        year = int(window["from"][:4])
        direct = by_year[(year, "direct_technical_claim")]
        background = window["background_unique_arxiv_ids"]
        annual.append({
            **window, "developer_direct_technical_claims": direct,
            "developer_direct_in_background": sum(
                work["in_background"] for identifier, work in works.items()
                if labels[identifier]["role"] == "direct_technical_claim"
                and int(work["first_submission_date"][:4]) -
                (work["first_submission_date"][5:7] < "09") == year),
        })
        annual[-1]["developer_direct_per_10000_background"] = (
            round(10000 * annual[-1]["developer_direct_in_background"] / background, 3)
            if background else None)
    if sum(item["developer_direct_technical_claims"] for item in annual) != by_role[
            "direct_technical_claim"]:
        raise ValueError("Developer-direct annual counts do not reconcile")
    return {
        "version": VERSION,
        "inventory_sha256": inventory_sha256,
        "line_id": inventory["line_id"], "title_ru": inventory["title_ru"],
        "all_exact_matches": inventory["all_exact_matches"],
        "developer_role_counts": dict(by_role),
        "annual": annual,
        "reviewed_works": [{"arxiv_id": item["arxiv_id"],
                            "url": works[item["arxiv_id"]]["url"],
                            "title": works[item["arxiv_id"]]["title"],
                            "first_submission_date": works[item["arxiv_id"]][
                                "first_submission_date"],
                            "role": item["role"], "reason": item["reason"]}
                           for item in review["rows"]],
        "weak_signal_confirmed": False,
        "independent_accuracy_evaluated": False,
        "relative_growth_observation": "descriptive_in_pinned_arxiv_and_developer_review_only",
        "limitations": [
            "Developer classified current titles and abstracts, not full texts or independent expert labels.",
            "Technical claims are not verified research contributions or independent organizations.",
            "The line predicate was fixed after viewing broad robotics results, not discovered independently.",
            "Current metadata may contain phrases added after a paper's v1 submission.",
            "The cs.RO background is not a complete worldwide scientific denominator.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Assessment is immutable; choose a new output path")
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    result = assess(inventory, review, sha256_file(args.inventory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"all": result["all_exact_matches"],
                      "roles": result["developer_role_counts"],
                      "direct_annual": [row["developer_direct_technical_claims"]
                                        for row in result["annual"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
