"""Make a bounded title/abstract review packet from frozen phrase groups.

This is a diagnostic packet.  It never labels papers or weak signals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from saia.controlled_collection import sha256_file


VERSION = "phrase-group-content-review-packet-v1"


def edge_sample(rows: list[dict], *, each_edge: int = 2) -> list[dict]:
    if not 1 <= each_edge <= 5:
        raise ValueError("Sample must remain bounded")
    ordered = sorted(rows, key=lambda row: (row["first_submission_date"], row["arxiv_id"]))
    chosen = {row["arxiv_id"]: row for row in ordered[:each_edge] + ordered[-each_edge:]}
    return sorted(chosen.values(), key=lambda row: (row["first_submission_date"], row["arxiv_id"]))


def build_packet(report: dict, report_hash: str, index_dir: Path,
                 *, group_limit: int = 15) -> dict:
    if (report.get("version") != "broad-title-phrase-proposals-v3"
            or not 1 <= group_limit <= 30
            or sha256_file(index_dir / "manifest.json") != report["parent_collection"]["index_manifest_sha256"]):
        raise ValueError("Unpinned or incompatible source")
    proposals = {row["phrase_en"]: row for row in report["proposals"]}
    groups = report["display_groups"][:group_limit]
    if groups != report["top_15_distinct_display_groups"] and group_limit == 15:
        raise ValueError("Top groups do not match frozen display list")
    ids = set()
    for group in groups:
        for item in group["members"]:
            if (item["phrase_en"] not in proposals
                    or proposals[item["phrase_en"]]["display_group_id"] != group["group_id"]):
                raise ValueError("Display group references an inconsistent phrase")
            ids.update(proposals[item["phrase_en"]]["title_work_ids"])
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        found = {}
        for identifier in sorted(ids):
            row = connection.execute(
                "SELECT arxiv_id,title,abstract,first_submission_date,authors,"
                "versions_json,doi,categories,snapshot_update_date "
                "FROM works WHERE arxiv_id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise ValueError("Frozen group work is missing from pinned index")
            found[identifier] = dict(row)
    finally:
        connection.close()
    packet_groups = []
    for group in groups:
        group_ids = set()
        for item in group["members"]:
            group_ids.update(proposals[item["phrase_en"]]["title_work_ids"])
        rows = edge_sample([found[identifier] for identifier in group_ids])
        packet_groups.append({
            "group_id": group["group_id"],
            "display_rank": group["display_rank"],
            "display_label_en": group["display_label_en"],
            "member_phrases": group["members"],
            "unique_title_work_count": len(group_ids),
            "sampling_rule": "first_two_and_last_two_by_first_submission_date_then_id",
            "sampled_works": [{
                "arxiv_id": row["arxiv_id"],
                "url": f"https://arxiv.org/abs/{row['arxiv_id']}",
                "title": row["title"], "abstract": row["abstract"],
                "first_submission_date": row["first_submission_date"],
                "authors": row["authors"],
                "versions": json.loads(row["versions_json"]),
                "doi": row["doi"], "categories": row["categories"],
                "snapshot_update_date": row["snapshot_update_date"],
                "article_role": "not_reviewed",
            } for row in rows],
        })
    return {
        "version": VERSION,
        "input_report_sha256": report_hash,
        "index_manifest_sha256": report["parent_collection"]["index_manifest_sha256"],
        "group_limit": group_limit,
        "groups": packet_groups,
        "unique_sampled_arxiv_ids": len({row["arxiv_id"] for group in packet_groups
                                        for row in group["sampled_works"]}),
        "limitations": [
            "Edge samples are not random or representative of every group member.",
            "Title and abstract are current snapshot metadata, not verified original v1 text.",
            "No article role, technology identity or weak-signal status is inferred.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-report-sha256", required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report_hash = sha256_file(args.report)
    if report_hash != args.expected_report_sha256:
        raise ValueError("Frozen report hash differs")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = build_packet(report, report_hash, args.index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"groups": len(result["groups"]),
                      "unique_sampled_works": result["unique_sampled_arxiv_ids"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
