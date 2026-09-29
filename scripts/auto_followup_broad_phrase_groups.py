"""Follow up every displayed broad-query phrase on a pinned full arXiv index.

There is no hand-picked phrase input: all top display groups are processed in
rank order.  The result is a proposal inventory, never a weak-signal verdict.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

from saia.arxiv_trigram_index import exact_search
from saia.arxiv_metadata import _matches_phrase
from saia.broad_title_phrases import collect_parent
from saia.controlled_collection import sha256_file


VERSION = "auto-broad-phrase-full-index-followup-v2"
MAX_STORED_IDS_PER_GROUP = 5000


def _window(first_date: str) -> int:
    return int(first_date[:4]) - (first_date[5:7] < "09")


def _sample_rows(connection: sqlite3.Connection, ids: set[str], limit: int = 3,
                 title_phrases: list[str] | None = None) -> list[dict]:
    rows = []
    ordered_ids = sorted(ids)
    for offset in range(0, len(ordered_ids), 400):
        batch = ordered_ids[offset:offset + 400]
        marks = ",".join("?" for _ in batch)
        rows.extend(dict(row) for row in connection.execute(
            "SELECT arxiv_id,title,first_submission_date FROM works "
            f"WHERE arxiv_id IN ({marks})", batch))
    if len(rows) != len(ids):
        raise ValueError("Follow-up identifiers missing from pinned index")
    ordered = sorted(rows, key=lambda item: (item["first_submission_date"],
                                            item["arxiv_id"]))
    if title_phrases is not None:
        ordered = [row for row in ordered if any(_matches_phrase(
            row["title"] or "", phrase, "orthographic-separators-v1")
            for phrase in title_phrases)]
    return [{**row, "url": f"https://arxiv.org/abs/{row['arxiv_id']}"}
            for row in ordered[:limit]]


def _group_hits(group: dict, index_dir: Path, date_from: str,
                as_of_date: str, cache: dict[str, dict]) -> tuple[set[str], list[dict]]:
    phrases = [member["phrase_en"] for member in group["members"]]
    if not phrases or len(phrases) != len(set(phrases)):
        raise ValueError("Display group has missing or repeated member phrases")
    ids: set[str] = set()
    audits = []
    for phrase in phrases:
        if phrase not in cache:
            cache[phrase] = exact_search(index_dir, {
                "date_from": date_from, "as_of_date": as_of_date,
                "matching_version": "orthographic-separators-v1",
                "included_terms": [phrase], "exclusions": [],
            })
        result = cache[phrase]
        ids.update(result["arxiv_ids"])
        audits.append({"phrase_en": phrase,
                       "exact_unique_ids": len(result["arxiv_ids"]),
                       "search_audit": result["audit"]})
    return ids, audits


def build(*, report_path: Path, parent_plan_path: Path,
          index_dir: Path, group_limit: int = 15) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parent_plan = json.loads(parent_plan_path.read_text(encoding="utf-8"))
    manifest_sha = sha256_file(index_dir / "manifest.json")
    if (report.get("version") not in (
            "broad-title-phrase-proposals-v3", "broad-title-phrase-proposals-v4",
            "broad-title-phrase-proposals-v5")
            or report.get("source_type", "arxiv") != "arxiv"
            or not 1 <= group_limit <= 15
            or report["parent_collection"]["index_manifest_sha256"] != manifest_sha
            or parent_plan["index_manifest_sha256"] != manifest_sha
            or report["period"]["date_from"] != parent_plan["date_from"]
            or report["period"]["as_of_date_exclusive"] != parent_plan[
                "as_of_date_exclusive"]
            or len(report["parent_annual_counts"]) != report["period"][
                "complete_year_windows"]):
        raise ValueError("Broad report, parent plan or index differs")
    parent_rows, parent_audit = collect_parent(index_dir, parent_plan)
    if (len(parent_rows) != report["parent_collection"]["selected_unique_ids"]
            or parent_audit["selected_unique_ids"] != len(parent_rows)):
        raise ValueError("Parent cohort no longer reproduces frozen report")
    parent_ids = {row["arxiv_id"] for row in parent_rows}
    parent_dates = {row["arxiv_id"]: row["first_submission_date"]
                    for row in parent_rows}
    first_year = int(parent_plan["date_from"][:4])
    last_year = int(parent_plan["as_of_date_exclusive"][:4])
    parent_annual = Counter(_window(value) for value in parent_dates.values())
    if ([parent_annual[year] for year in range(first_year, last_year)]
            != report["parent_annual_counts"]):
        raise ValueError("Parent annual counts no longer reproduce frozen report")
    groups = report["top_15_distinct_display_groups"][:group_limit]
    if len(groups) != group_limit or len({g["display_label_en"] for g in groups}) != len(groups):
        raise ValueError("Displayed phrase groups are missing or duplicate")
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                                 uri=True)
    connection.row_factory = sqlite3.Row
    try:
        results = []
        search_cache: dict[str, dict] = {}
        for group in groups:
            phrase = group["display_label_en"]
            member_phrases = [m["phrase_en"] for m in group["members"]]
            all_ids, member_audits = _group_hits(
                group, index_dir, parent_plan["date_from"],
                parent_plan["as_of_date_exclusive"], search_cache)
            in_parent = all_ids & parent_ids
            by_window = Counter(_window(parent_dates[identifier])
                                for identifier in in_parent)
            annual = [by_window.get(year, 0) for year in range(first_year, last_year)]
            if sum(annual) != len(in_parent):
                raise ValueError("Annual phrase counts do not reconcile")
            bounded = len(all_ids) <= MAX_STORED_IDS_PER_GROUP
            results.append({
                "rank": group["display_rank"], "group_id": group["group_id"],
                "phrase_en": phrase,
                "member_phrases": member_phrases,
                "discovered_from_parent_titles": True,
                "full_index_exact_ids": len(all_ids),
                "in_parent_unique_ids": len(in_parent),
                "outside_parent_unique_ids": len(all_ids - parent_ids),
                "annual_in_parent_current_text_mentions": annual,
                "parent_annual_counts": report["parent_annual_counts"],
                "all_arxiv_ids": sorted(all_ids) if bounded else None,
                "all_arxiv_ids_complete": bounded,
                "all_arxiv_id_omission_reason": (
                    None if bounded else "full_index_phrase_exceeds_5000_id_output_cap"),
                "in_parent_arxiv_ids": sorted(in_parent),
                "first_in_parent_examples": _sample_rows(connection, in_parent),
                "first_title_anchored_in_parent_examples": _sample_rows(
                    connection, in_parent, title_phrases=member_phrases),
                "first_anywhere_examples": (
                    _sample_rows(connection, all_ids) if bounded else []),
                "first_title_anchored_anywhere_examples": (
                    _sample_rows(connection, all_ids, title_phrases=member_phrases)
                    if bounded else []),
                "member_search_audits": member_audits,
                "paper_role_verified": False,
                "single_mechanism_verified": False,
                "weak_signal_confirmed": False,
            })
    finally:
        connection.close()
    return {
        "version": VERSION,
        "report_sha256": sha256_file(report_path),
        "parent_plan_sha256": sha256_file(parent_plan_path),
        "index_manifest_sha256": manifest_sha,
        "period": report["period"],
        "parent_audit": parent_audit,
        "groups_processed_without_manual_selection": len(results),
        "groups": results,
        "limitations": [
            "Every displayed top phrase is followed up, including irrelevant or generic phrases.",
            "The user query to English parent terms was fixed earlier, not generated by this script.",
            "A phrase in current title or abstract is not a primary result or one mechanism.",
            "Current metadata can leak words added after a paper's first submission.",
            "The broad lexical parent omits papers that use different task language.",
            "No OpenAlex, patent, market or independent-team evidence is measured here.",
        ],
        "weak_signal_detection_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--parent-plan", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group-limit", type=int, default=15)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Follow-up output is immutable")
    result = build(report_path=args.report, parent_plan_path=args.parent_plan,
                   index_dir=args.index, group_limit=args.group_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps([{"rank": g["rank"], "phrase": g["phrase_en"],
                       "in_parent": g["in_parent_unique_ids"],
                       "full_index": g["full_index_exact_ids"]}
                      for g in result["groups"]], ensure_ascii=False))


if __name__ == "__main__":
    main()
