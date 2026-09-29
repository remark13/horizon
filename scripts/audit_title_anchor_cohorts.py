"""Audit title evidence and display duplicates in a frozen title-anchor pilot.

This is a diagnostic, not a paper relevance classifier or signal scorer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
VERSION = "title-anchor-cohort-audit-v1"
RECENT = "2024-09-01"
PRIOR = "2021-09-01"


def _load_works(index_path: Path, ids: set[str]) -> dict[str, dict]:
    connection = sqlite3.connect(f"file:{index_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    works = {}
    try:
        ordered = sorted(ids)
        for offset in range(0, len(ordered), 400):
            chunk = ordered[offset:offset + 400]
            marks = ",".join("?" for _ in chunk)
            for row in connection.execute(
                    f"SELECT arxiv_id,title,abstract,first_submission_date FROM works "
                    f"WHERE arxiv_id IN ({marks})", chunk):
                works[row["arxiv_id"]] = dict(row)
    finally:
        connection.close()
    if set(works) != ids:
        raise ValueError("Expanded IDs are missing from pinned index")
    return works


def _nested(a: str, b: str) -> bool:
    left, right = a.split(), b.split()
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return any(long[pos:pos + len(short)] == short
               for pos in range(len(long) - len(short) + 1))


def display_groups(rows: list[dict], *, threshold: float = 0.8) -> list[dict]:
    """Group near-identical nested phrases for display, never scientific identity."""
    if not 0.5 <= threshold <= 1.0:
        raise ValueError("Invalid display overlap")
    groups = []
    for rank, row in enumerate(rows, 1):
        phrase = row["phrase_en"]
        ids = set(row["expanded_ids"])
        selected = None
        for group in groups:
            representative = group["representative_phrase_en"]
            if not _nested(phrase, representative):
                continue
            representative_ids = group["_representative_ids"]
            overlap = len(ids & representative_ids) / len(ids | representative_ids)
            if overlap >= threshold:
                selected = group
                break
        if selected is None:
            selected = {"display_rank": len(groups) + 1,
                        "representative_phrase_en": phrase,
                        "representative_source_rank": rank,
                        "member_phrases": [phrase],
                        "_representative_ids": ids}
            groups.append(selected)
        else:
            selected["member_phrases"].append(phrase)
        row["display_group_rank"] = selected["display_rank"]
    for group in groups:
        del group["_representative_ids"]
    return groups


def audit(expansion: dict, works: dict[str, dict], *, expansion_sha256: str) -> dict:
    if (expansion.get("version") != "title-anchor-expansion-pilot-v1"
            or expansion.get("complete_candidate_scan") is not True
            or expansion.get("production_changed") is not False):
        raise ValueError("Expected complete frozen diagnostic expansion")
    rows = []
    for source in expansion["rows"]:
        phrase = source["phrase_en"]
        ids = source["expanded_ids"]
        if len(ids) != source["expanded_unique_arxiv_ids"] or len(set(ids)) != len(ids):
            raise ValueError("Expanded cohort count or uniqueness mismatch")
        title_ids = [identifier for identifier in ids if _matches_phrase(
            works[identifier]["title"] or "", phrase, ORTHOGRAPHIC_MATCHING_VERSION)]
        title_recent = sum(works[identifier]["first_submission_date"] >= RECENT
                           for identifier in title_ids)
        title_prior = sum(PRIOR <= works[identifier]["first_submission_date"] < RECENT
                          for identifier in title_ids)
        rows.append({**source, "title_anchored_ids": title_ids,
                     "title_anchored_count": len(title_ids),
                     "abstract_only_count": len(ids) - len(title_ids),
                     "title_recent_two_year_count": title_recent,
                     "title_prior_three_year_count": title_prior,
                     "title_evidence_is_primary_result": None})
    groups = display_groups(rows)
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "expansion_sha256": expansion_sha256,
            "index_manifest_sha256": expansion["index_manifest_sha256"],
            "phrase_count": len(rows), "display_group_count": len(groups),
            "top15_raw_distinct_display_groups": len({row["display_group_rank"]
                                                       for row in rows[:15]}),
            "top15_display_groups": groups[:15],
            "rows": rows,
            "production_changed": False,
            "paper_relevance_measured": False,
            "weak_signal_accuracy_measured": False,
            "limitations": [
                "Title alignment is stronger lexical evidence, not proof of a paper's own result.",
                "Abstract-only matches may be relevant; they are not deleted or counted as false.",
                "Display grouping only merges nested phrases with at least 0.8 cohort Jaccard; it is not semantic clustering.",
                "The review order is inherited from the unvalidated expansion heuristic, not a signal score.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expansion", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    expansion = json.loads(args.expansion.read_text(encoding="utf-8"))
    if sha256_file(args.index / "manifest.json") != expansion["index_manifest_sha256"]:
        raise ValueError("Pinned index manifest changed")
    ids = {identifier for row in expansion["rows"] for identifier in row["expanded_ids"]}
    result = audit(expansion, _load_works(args.index / "index.sqlite3", ids),
                   expansion_sha256=sha256_file(args.expansion))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "phrase_count", "display_group_count", "top15_raw_distinct_display_groups")}))


if __name__ == "__main__":
    main()
