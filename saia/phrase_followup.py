"""Bounded post-discovery complete-index phrase and predecessor search."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from saia.arxiv_trigram_index import exact_search
from saia.auto_vla_followup import _materialize
from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search


VERSION = "post-discovery-phrase-followup-v1"


def run(*, config: dict, report: dict, config_sha256: str,
        report_sha256: str, index_dir: Path) -> dict:
    if (config["discovery_report_sha256"] != report_sha256
            or config["index_manifest_sha256"] != sha256_file(index_dir / "manifest.json")
            or report["parent_collection"]["index_manifest_sha256"] != config["index_manifest_sha256"]
            or report.get("version") != "broad-title-phrase-proposals-v3"
            or report["period"]["date_from"] != config["date_from"]
            or report["period"]["as_of_date_exclusive"] != config["as_of_date_exclusive"]
            or not 1 <= config["max_matches_per_branch"] <= 5000):
        raise ValueError("Unpinned or incompatible follow-up input")
    branch_results = []
    all_ids: set[str] = set()
    branch_names: set[str] = set()
    for branch in config["branches"]:
        if branch["id"] in branch_names:
            raise ValueError("Duplicate follow-up branch ID")
        branch_names.add(branch["id"])
        plan = {
            "date_from": config["date_from"],
            "as_of_date": config["as_of_date_exclusive"],
            "matching_version": config["matching_version"],
        }
        if branch["kind"] == "or_phrase":
            plan.update(included_terms=branch["included_terms"], excluded_terms=[])
            found = exact_search(index_dir, plan)
        elif branch["kind"] == "concept_groups":
            plan.update(concept_groups=branch["concept_groups"],
                        exclusions=branch["exclusions"])
            found = exact_concept_search(
                index_dir, plan, max_matches=config["max_matches_per_branch"])
        else:
            raise ValueError("Unknown post-discovery branch kind")
        ids = set(found["arxiv_ids"])
        if len(ids) > config["max_matches_per_branch"]:
            raise ValueError("Branch exceeds frozen safety cap")
        all_ids.update(ids)
        branch_results.append({
            "id": branch["id"], "role": branch["role"],
            "kind": branch["kind"], "query": plan,
            "count": len(ids), "arxiv_ids": sorted(ids),
            "search_audit": found["audit"],
        })
    works = _materialize(index_dir, all_ids)
    for branch in branch_results:
        counts = Counter(works[identifier]["first_submission_date"][:4]
                         for identifier in branch["arxiv_ids"])
        branch["annual_current_metadata_matches"] = dict(sorted(counts.items()))
        branch["first_matching_work"] = (
            min((works[identifier] for identifier in branch["arxiv_ids"]),
                key=lambda row: (row["first_submission_date"], row["arxiv_id"]))["arxiv_id"]
            if branch["arxiv_ids"] else None)
    return {
        "version": VERSION,
        "config_sha256": config_sha256,
        "discovery_report_sha256": report_sha256,
        "index_manifest_sha256": config["index_manifest_sha256"],
        "period": {"date_from": config["date_from"],
                   "as_of_date_exclusive": config["as_of_date_exclusive"]},
        "branches": branch_results,
        "works": [works[identifier] for identifier in sorted(works)],
        "weak_signal_confirmed": False,
        "independent_discovery_benchmark": False,
        "limitations": config["limitations"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = run(config=config, report=report,
                 config_sha256=sha256_file(args.config),
                 report_sha256=sha256_file(args.report), index_dir=args.index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({row["id"]: row["count"] for row in result["branches"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
