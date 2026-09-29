"""Evaluate exact arXiv follow-up coverage without treating developer labels as gold."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

from saia.broad_title_phrases import _year_window
from saia.controlled_collection import sha256_file


VERSION = "auto-vla-followup-developer-evaluation-v1"
DIRECT = "direct_vla_navigation_result"


def evaluate(followup: dict, review: dict, *, followup_sha256: str) -> dict:
    if (review.get("followup_sha256") != followup_sha256
            or review.get("independent_expert_review") is not False
            or review.get("reviewer_role") != "developer"
            or followup.get("weak_signal_confirmed") is not False):
        raise ValueError("Follow-up or review evidence status differs")
    eligible = (set(followup["role_unions"]["narrow_navigation_candidate"]["unique_ids"])
                | set(followup["role_unions"]["sensitivity_for_abbreviated_VLA"]["unique_ids"]))
    labelled = {row["arxiv_id"]: row for row in review["rows"]}
    works = {row["arxiv_id"]: row for row in followup["works"]}
    if (len(labelled) != len(review["rows"]) or len(works) != len(followup["works"])
            or set(labelled) != eligible or not eligible <= set(works)):
        raise ValueError("Review must cover exactly both navigation-search unions")
    choices = set(review["choices"])
    if any(row["judgement"] not in choices or not row.get("reason")
           for row in review["rows"]):
        raise ValueError("Incomplete developer judgement")
    start = date.fromisoformat(followup["period"]["date_from"])
    end = date.fromisoformat(followup["period"]["as_of_date_exclusive"])
    annual = [0] * (end.year - start.year)
    direct_ids = sorted(identifier for identifier, row in labelled.items()
                        if row["judgement"] == DIRECT)
    for identifier in direct_ids:
        annual[_year_window(works[identifier]["first_submission_date"], start, end)] += 1
    direct_works = sorted((works[identifier] for identifier in direct_ids),
                          key=lambda row: (row["first_submission_date"], row["arxiv_id"]))
    old_title_ids = set(followup["original_title_phrase_ids"])
    return {
        "version": VERSION,
        "followup_sha256": followup_sha256,
        "reviewer_role": "developer",
        "independent_expert_review": False,
        "case_count": len(eligible),
        "developer_judgement_counts": dict(Counter(
            row["judgement"] for row in labelled.values())),
        "direct_navigation": {
            "count": len(direct_ids),
            "arxiv_ids": direct_ids,
            "annual_counts_in_reviewed_union": annual,
            "earliest_in_reviewed_union": {
                "arxiv_id": direct_works[0]["arxiv_id"],
                "first_submission_date": direct_works[0]["first_submission_date"],
                "title": direct_works[0]["title"],
                "url": direct_works[0]["url"],
            } if direct_works else None,
            "previous_title_packet_direct_ids_recovered": len(set(direct_ids) & old_title_ids),
            "additional_direct_ids": sorted(set(direct_ids) - old_title_ids),
        },
        "retrieval_comparison": {
            role: {"count": followup["role_unions"][role]["count"],
                   "annual": followup["role_unions"][role]["annual_current_metadata_matches"],
                   "outside_broad_parent": followup["role_unions"][role]["outside_fixed_broad_parent"]}
            for role in ("new_phrase_family", "narrow_navigation_candidate",
                         "sensitivity_for_abbreviated_VLA", "predecessor_not_new_VLA")
        },
        "limitations": [
            "Developer judgement used current title/abstract only; it is not independent precision or a full-text primary-result verdict.",
            "Two developer-composed post-discovery predicates are not a proven complete numerator for all VLA UAV navigation work.",
            "The 2023 vision-language navigation predecessor series prevents identifying a technology birth from the newer VLA phrase alone.",
            "The fixed lexical BAS parent is not all BAS publications or world science; outside-parent hits further limit prevalence comparison.",
            "Original-version text timing, organizational diffusion, patent and market evidence remain unverified.",
        ],
        "relative_growth_of_technology_proven": False,
        "first_technology_publication_proven": False,
        "weak_signal_confirmed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("followup", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(json.loads(args.followup.read_text(encoding="utf-8")),
                      json.loads(args.review.read_text(encoding="utf-8")),
                      followup_sha256=sha256_file(args.followup))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"reviewed": report["case_count"],
                      "developer_direct": report["direct_navigation"]["count"],
                      "annual_direct": report["direct_navigation"][
                          "annual_counts_in_reviewed_union"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
