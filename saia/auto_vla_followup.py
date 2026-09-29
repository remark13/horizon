"""Check a discovered BAS phrase with complete bounded arXiv concept searches."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sqlite3

from saia.broad_title_phrases import _year_window, collect_parent
from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search, supports_concept_plan


VERSION = "auto-vla-full-arxiv-followup-v1"
ROLES = {"new_phrase_family", "narrow_navigation_candidate",
         "sensitivity_for_abbreviated_VLA", "predecessor_not_new_VLA"}


def _materialize(index_dir: Path, identifiers: set[str]) -> dict[str, dict]:
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                                 uri=True)
    connection.row_factory = sqlite3.Row
    result = {}
    try:
        ordered = sorted(identifiers)
        for offset in range(0, len(ordered), 500):
            chunk = ordered[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            for row in connection.execute(
                    "SELECT arxiv_id,title,abstract,first_submission_date,authors,"
                    "authors_parsed_json,versions_json,snapshot_update_date,doi,categories "
                    f"FROM works WHERE arxiv_id IN ({placeholders})", chunk):
                item = dict(row)
                item["authors_parsed"] = json.loads(item.pop("authors_parsed_json"))
                item["versions"] = json.loads(item.pop("versions_json"))
                item["url"] = f"https://arxiv.org/abs/{item['arxiv_id']}"
                result[item["arxiv_id"]] = item
    finally:
        connection.close()
    if set(result) != identifiers:
        raise ValueError("Index did not materialize every matched arXiv ID")
    return result


def run(*, config: dict, index_dir: Path, parent_config: dict,
        discovery: dict, packet: dict, config_sha256: str,
        discovery_sha256: str, packet_sha256: str,
        parent_config_sha256: str) -> dict:
    if (config["discovery_report_sha256"] != discovery_sha256
            or config["candidate_packet_sha256"] != packet_sha256
            or config["broad_parent_config_sha256"] != parent_config_sha256
            or config["index_manifest_sha256"] != parent_config["index_manifest_sha256"]
            or discovery["config_sha256"] != parent_config_sha256
            or packet["proposals_sha256"] != discovery_sha256
            or config["matching_version"] != "orthographic-separators-v1"):
        raise ValueError("Frozen source and follow-up provenance differs")
    start = date.fromisoformat(config["period"]["date_from"])
    end = date.fromisoformat(config["period"]["as_of_date_exclusive"])
    if (start.isoformat() != parent_config["date_from"]
            or end.isoformat() != parent_config["as_of_date_exclusive"]
            or config["max_matches_per_branch"] > 5000):
        raise ValueError("Follow-up period or per-branch bound differs")
    if sha256_file(index_dir / "manifest.json") != config["index_manifest_sha256"]:
        raise ValueError("Pinned arXiv index changed")
    parent_rows, parent_audit = collect_parent(index_dir, parent_config)
    if parent_audit != discovery["parent_collection"]:
        raise ValueError("Broad parent inventory changed")
    parent_ids = {row["arxiv_id"] for row in parent_rows}
    if len(parent_ids) != len(parent_rows):
        raise ValueError("Broad parent has duplicate IDs")
    original_title_ids = {row["arxiv_id"] for row in packet["works"]}
    if len(original_title_ids) != len(packet["works"]):
        raise ValueError("Title packet has duplicate IDs")
    branch_ids = {}
    branch_audits = {}
    for branch in config["branches"]:
        if branch["id"] in branch_ids or branch["role"] not in ROLES:
            raise ValueError("Duplicate branch or unsupported evidence role")
        plan = {
            "concept_groups": branch["concept_groups"],
            "exclusions": branch["exclusions"],
            "matching_version": config["matching_version"],
            "date_from": start.isoformat(), "as_of_date": end.isoformat(),
        }
        if not supports_concept_plan(plan):
            raise ValueError(f"Unsupported exact search plan: {branch['id']}")
        result = exact_concept_search(index_dir, plan,
                                      max_matches=config["max_matches_per_branch"])
        branch_ids[branch["id"]] = set(result["arxiv_ids"])
        branch_audits[branch["id"]] = result["audit"]
    works = _materialize(index_dir, set().union(*branch_ids.values()))
    years = end.year - start.year
    branches = []
    for branch in config["branches"]:
        identifiers = branch_ids[branch["id"]]
        annual = [0] * years
        within_parent = [0] * years
        updated_after_window = 0
        for identifier in identifiers:
            item = works[identifier]
            window = _year_window(item["first_submission_date"], start, end)
            annual[window] += 1
            if identifier in parent_ids:
                within_parent[window] += 1
            window_end_year = start.year + window + 1
            window_end = date(window_end_year, start.month, start.day)
            update_date = item["snapshot_update_date"]
            if update_date and date.fromisoformat(update_date[:10]) >= window_end:
                updated_after_window += 1
        branches.append({
            "id": branch["id"], "role": branch["role"],
            "concept_groups": branch["concept_groups"],
            "arxiv_ids": sorted(identifiers),
            "count": len(identifiers),
            "annual_current_metadata_matches": annual,
            "annual_inside_fixed_broad_parent": within_parent,
            "outside_fixed_broad_parent": len(identifiers - parent_ids),
            "original_title_ids_recovered": len(identifiers & original_title_ids),
            "original_title_ids_missed": sorted(original_title_ids - identifiers),
            "current_metadata_updated_after_submission_window": updated_after_window,
            "search_audit": branch_audits[branch["id"]],
        })
    by_role = {}
    for role in sorted(ROLES):
        ids = set().union(*(branch_ids[branch["id"]] for branch in config["branches"]
                            if branch["role"] == role))
        annual = [0] * years
        for identifier in ids:
            annual[_year_window(works[identifier]["first_submission_date"], start, end)] += 1
        by_role[role] = {"unique_ids": sorted(ids), "count": len(ids),
                         "annual_current_metadata_matches": annual,
                         "outside_fixed_broad_parent": len(ids - parent_ids),
                         "original_title_ids_recovered": len(ids & original_title_ids)}
    return {
        "version": VERSION, "config_sha256": config_sha256,
        "source_sha256": {"discovery": discovery_sha256,
                           "packet": packet_sha256,
                           "broad_parent_config": parent_config_sha256,
                           "arxiv_index_manifest": config["index_manifest_sha256"]},
        "source_role": "complete_pinned_arxiv_metadata_not_world_science",
        "period": config["period"],
        "broad_parent_count": len(parent_ids),
        "broad_parent_annual_counts": discovery["parent_annual_counts"],
        "original_title_phrase_ids": sorted(original_title_ids),
        "branches": branches, "role_unions": by_role,
        "works": [works[identifier] for identifier in sorted(works)],
        "interpretation_limits": config["interpretation_limits"],
        "relevance_or_primary_role_reviewed": False,
        "weak_signal_confirmed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    paths = {"discovery": root / config["discovery_report"],
             "packet": root / config["candidate_packet"],
             "parent_config": root / config["broad_parent_config"]}
    data = {name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in paths.items()}
    result = run(config=config, index_dir=args.index,
                 parent_config=data["parent_config"], discovery=data["discovery"],
                 packet=data["packet"], config_sha256=sha256_file(args.config),
                 discovery_sha256=sha256_file(paths["discovery"]),
                 packet_sha256=sha256_file(paths["packet"]),
                 parent_config_sha256=sha256_file(paths["parent_config"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({role: info["count"] for role, info
                      in result["role_unions"].items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
