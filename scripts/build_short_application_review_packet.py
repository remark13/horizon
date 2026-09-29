"""Freeze the first 15 distinct papers per new query for source-role review."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "free-ru-short-application-first15-review-v1"
CASES = ("national-area-032", "national-area-039", "customer-signal-017")


def build(source_path: Path) -> dict:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source.get("version") != "free-ru-short-application-openalex-v2":
        raise ValueError("Expected exact-date frozen OpenAlex probe")
    result = []
    for case_id in CASES:
        branches = [row for row in source["rows"] if row["case_id"] == case_id]
        if len(branches) != 2 or any(row["status"] != "succeeded" for row in branches):
            raise ValueError(f"Incomplete source branches for {case_id}")
        by_id = {}
        for branch_no, branch in enumerate(branches, 1):
            for paper in branch["results"]:
                if not paper["within_exact_period"]:
                    raise ValueError("Source result outside the exact date window")
                key = paper["openalex_id"]
                if not key:
                    raise ValueError("OpenAlex paper missing ID")
                if key not in by_id:
                    by_id[key] = {"paper": paper, "first_branch": branch_no,
                                  "first_rank": paper["rank_in_first_page"],
                                  "branches": []}
                by_id[key]["branches"].append({"search_query": branch["search_query"],
                                                "first_page_rank": paper["rank_in_first_page"]})
        ordered = sorted(by_id.values(), key=lambda x: (x["first_rank"], x["first_branch"],
                                                     x["paper"]["openalex_id"]))
        for ordinal, item in enumerate(ordered[:15], 1):
            paper = item["paper"]
            result.append({"case_id": case_id, "query_ru": branches[0]["query_ru"],
                           "retrieval_rank": ordinal,
                           "openalex_id": paper["openalex_id"], "doi": paper["doi"],
                           "title": paper["title"], "abstract": paper["abstract"],
                           "openalex_type": paper["type"],
                           "publication_date": paper["publication_date"],
                           "branch_appearances": item["branches"]})
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": sha256_file(source_path), "records": result,
            "ranking_rule": "minimum first-page rank; tie: earlier frozen branch; tie: OpenAlex ID",
            "not_product_top15": True,
            "review_fields": ["query_fit", "record_role", "all_required_qualifiers_observed",
                              "basis", "uncertainty"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Review packet is immutable")
    report = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(report["records"]),
                      "cases": len({x["case_id"] for x in report["records"]})}))


if __name__ == "__main__":
    main()
