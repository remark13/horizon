"""Materialize every work behind one frozen, automatically proposed title phrase."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sqlite3

from saia.broad_title_phrases import _WORDS, _year_window, collect_parent
from saia.controlled_collection import sha256_file


VERSION = "title-phrase-full-review-packet-v1"


def materialize(*, config: dict, proposals: dict, parent_rows: list[dict],
                index_dir: Path, phrase: str, config_sha256: str,
                proposals_sha256: str) -> dict:
    if (proposals.get("config_sha256") != config_sha256
            or proposals["parent_collection"]["selected_unique_ids"] != len(parent_rows)
            or proposals["parent_collection"]["index_manifest_sha256"]
            != config["index_manifest_sha256"]):
        raise ValueError("Proposals do not match the frozen parent")
    hits = [(rank, item) for rank, item in enumerate(proposals["proposals"], 1)
            if item["phrase_en"] == phrase]
    if len(hits) != 1:
        raise ValueError("Phrase is not an approved frozen proposal")
    rank, proposal = hits[0]
    tokens = phrase.split()
    matches = []
    for row in parent_rows:
        title = _WORDS.findall((row["title"] or "").casefold().replace("-", " "))
        if any(title[offset:offset + len(tokens)] == tokens
               for offset in range(len(title) - len(tokens) + 1)):
            matches.append(row)
    start = date.fromisoformat(config["date_from"])
    end = date.fromisoformat(config["as_of_date_exclusive"])
    annual = [0] * (end.year - start.year)
    for row in matches:
        annual[_year_window(row["first_submission_date"], start, end)] += 1
    if (len(matches) != proposal["title_work_count"]
            or annual != proposal["annual_title_counts"]):
        raise ValueError("Phrase work inventory does not reproduce proposal counts")
    db_path = index_dir / "index.sqlite3"
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    identifiers = [row["arxiv_id"] for row in matches]
    details = {}
    try:
        for offset in range(0, len(identifiers), 500):
            chunk = identifiers[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            for row in connection.execute(
                    "SELECT arxiv_id,authors,authors_parsed_json,versions_json,"
                    "snapshot_update_date,categories,doi FROM works "
                    f"WHERE arxiv_id IN ({placeholders})", chunk):
                details[row["arxiv_id"]] = dict(row)
    finally:
        connection.close()
    if len(details) != len(matches):
        raise ValueError("Not every source work could be materialized")
    works = []
    for row in sorted(matches, key=lambda item: (item["first_submission_date"],
                                                     item["arxiv_id"])):
        identifier = row["arxiv_id"]
        detail = details[identifier]
        works.append({
            "arxiv_id": identifier,
            "url": f"https://arxiv.org/abs/{identifier}",
            "title": row["title"], "abstract": row["abstract"],
            "first_submission_date": row["first_submission_date"],
            "snapshot_update_date": detail["snapshot_update_date"],
            "versions": json.loads(detail["versions_json"]),
            "authors": detail["authors"],
            "authors_parsed": json.loads(detail["authors_parsed_json"]),
            "categories": detail["categories"], "doi": detail["doi"],
            "developer_review": {"bas_relevance": None,
                                 "own_technical_result": None,
                                 "technical_mechanism": None,
                                 "reason": None},
        })
    return {
        "version": VERSION, "config_sha256": config_sha256,
        "proposals_sha256": proposals_sha256,
        "phrase_en": phrase, "proposal_rank": rank,
        "selected_posthoc_for_developer_review": True,
        "independent_discovery_benchmark": False,
        "parent_works": len(parent_rows),
        "annual_title_counts": annual,
        "works": works,
        "weak_signal_verified": False,
        "limitations": [
            "The phrase emerged without line-name seeding but was selected for review after inspecting proposals.",
            "Current arXiv metadata are not the original title and abstract at first submission.",
            "A title phrase and a broad BAS parent hit do not prove technical relevance or primary contribution.",
            "Authors in arXiv metadata do not establish independent organizations.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--phrase", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    proposals = json.loads(args.proposals.read_text(encoding="utf-8"))
    parent, audit = collect_parent(args.index, config)
    if audit != proposals["parent_collection"]:
        raise ValueError("Parent collection changed since phrase proposal")
    packet = materialize(config=config, proposals=proposals, parent_rows=parent,
                         index_dir=args.index, phrase=args.phrase,
                         config_sha256=sha256_file(args.config),
                         proposals_sha256=sha256_file(args.proposals))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"phrase": packet["phrase_en"], "rank": packet["proposal_rank"],
                      "works": len(packet["works"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
