"""Deduplicate frozen OpenAlex branch pages and count literal concept coverage.

This is an audit of mentions, never a relevance/primary-research classifier.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase
from saia.controlled_collection import sha256_file
from scripts.probe_frozen_openalex_synonym_branches import SOURCE_VERSION, VERSION_WITH_ABSTRACTS


VERSION = "frozen-openalex-branch-pool-audit-v1"


def audit(source_path: Path, config_path: Path) -> dict:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (source.get("version") != VERSION_WITH_ABSTRACTS
            or source.get("limits", {}).get("abstracts_archived") is not True
            or config.get("version") != SOURCE_VERSION
            or source.get("source_config_sha256") != sha256_file(config_path)):
        raise ValueError("Source snapshot and frozen query config do not match")
    cases = {case["case_id"]: case for case in config["cases"]}
    grouped = defaultdict(list)
    for branch in source["rows"]:
        if branch["status"] != "succeeded" or branch["case_id"] not in cases:
            raise ValueError("Incomplete or unexpected branch")
        grouped[branch["case_id"]].append(branch)
    results = []
    for case_id, branches in grouped.items():
        concepts = cases[case_id]["concept_groups"]
        unique = {}
        raw_in_period = 0
        for branch in branches:
            for item in branch["results"]:
                if not item["within_exact_period"]:
                    continue
                raw_in_period += 1
                identifier = item["openalex_id"]
                if not identifier:
                    raise ValueError("Missing OpenAlex ID")
                if identifier not in unique or (not unique[identifier].get("abstract")
                                                and item.get("abstract")):
                    unique[identifier] = item
        matches = []
        title_matches = 0
        missing_abstract = 0
        for identifier, item in unique.items():
            title = item.get("title") or ""
            abstract = item.get("abstract") or ""
            missing_abstract += not bool(abstract)
            title_all = all(any(_matches_phrase(title, term, ORTHOGRAPHIC_MATCHING_VERSION)
                                for term in alternatives) for alternatives in concepts)
            title_matches += title_all
            matched = [[term for term in alternatives
                        if _matches_phrase(title + " " + abstract, term,
                                           ORTHOGRAPHIC_MATCHING_VERSION)]
                       for alternatives in concepts]
            if all(matched):
                matches.append({"openalex_id": identifier, "title": title,
                                "doi": item.get("doi"),
                                "matched_terms_by_group": matched,
                                "abstract_available": bool(abstract)})
        results.append({"case_id": case_id, "branch_count": len(branches),
                        "raw_in_period": raw_in_period, "unique_openalex_ids": len(unique),
                        "repeated_placements": raw_in_period - len(unique),
                        "missing_abstract_count": missing_abstract,
                        "all_groups_in_title_count": title_matches,
                        "all_groups_in_title_or_abstract_count": len(matches),
                        "all_groups_in_title_or_abstract": sorted(
                            matches, key=lambda row: row["openalex_id"])})
    return {"version": VERSION, "source_sha256": sha256_file(source_path),
            "config_sha256": sha256_file(config_path),
            "matching_version": ORTHOGRAPHIC_MATCHING_VERSION,
            "cases": sorted(results, key=lambda row: row["case_id"]),
            "limits": {"developer_selected_concept_groups": True,
                       "text_cooccurrence_not_relevance_or_primary_role": True,
                       "missing_abstract_not_negative_evidence": True,
                       "bounded_first_pages_not_temporal_denominator": True,
                       "not_independent_accuracy_or_signal_growth": True,
                       "not_production_route_change": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Audit output is immutable")
    report = audit(args.source, args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({row["case_id"]: row["all_groups_in_title_or_abstract_count"]
                      for row in report["cases"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
