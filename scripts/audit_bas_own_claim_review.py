"""Coverage audit of a frozen contribution-cue index on reviewed papers."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.controlled_collection import sha256_file


VERSION = "bas-own-claim-review-coverage-v1"


def audit(blind: dict, review: dict, connection: sqlite3.Connection,
          *, index_manifest_sha: str, blind_sha: str, review_sha: str) -> dict:
    if (blind.get("version") != "title-anchor-paper-review-v2"
            or review.get("version") != "title-anchor-developer-review-v1"
            or review.get("blind_packet_sha256") != blind_sha):
        raise ValueError("Frozen packet/review mismatch")
    labels = {row["case_id"]: row for row in review["cases"]}
    if len(labels) != len(blind["cases"]):
        raise ValueError("Review case count mismatch")
    rows = []
    for case in blind["cases"]:
        identifier = case["paper"]["arxiv_id"]
        found = connection.execute(
            "SELECT extraction_reason FROM works WHERE arxiv_id=?", (identifier,)).fetchone()
        claims = connection.execute(
            "SELECT span_id,text,format_hint FROM claims WHERE arxiv_id=? ORDER BY span_id",
            (identifier,)).fetchall()
        rows.append({"case_id": case["case_id"], "arxiv_id": identifier,
                     "developer_own_result": labels[case["case_id"]]["own_result"],
                     "in_broad_parent": found is not None,
                     "extraction_reason": found[0] if found else "outside_parent",
                     "claim_count": len(claims),
                     "claims": [{"span_id": row[0], "text": row[1],
                                 "format_hint": row[2]} for row in claims]})
    by_label = defaultdict(Counter)
    for row in rows:
        label = row["developer_own_result"]
        by_label[label]["total"] += 1
        by_label[label]["in_parent"] += row["in_broad_parent"]
        by_label[label]["at_least_one_cue"] += row["claim_count"] > 0
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "index_manifest_sha256": index_manifest_sha,
            "blind_packet_sha256": blind_sha,
            "developer_review_sha256": review_sha,
            "case_count": len(rows),
            "by_developer_label": {key: dict(value) for key, value in by_label.items()},
            "rows": rows, "production_changed": False,
            "cue_is_relevance_label": False,
            "independent_accuracy_measured": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--blind", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = json.loads((args.index_dir / "manifest.json").read_text(encoding="utf-8"))
    db_path = args.index_dir / manifest["file"]["name"]
    if (manifest.get("version") != "bas-own-claim-index-pilot-v1"
            or db_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(db_path) != manifest["file"]["sha256"]):
        raise ValueError("Claim index differs from manifest")
    blind = json.loads(args.blind.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        result = audit(blind, review, connection,
                       index_manifest_sha=sha256_file(args.index_dir / "manifest.json"),
                       blind_sha=sha256_file(args.blind),
                       review_sha=sha256_file(args.review))
    finally:
        connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["by_developer_label"], sort_keys=True))


if __name__ == "__main__":
    main()
