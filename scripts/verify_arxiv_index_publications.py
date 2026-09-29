"""Compare full Publication records from the index and mirror oracle."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.arxiv_trigram_index import search_publications
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.local_arxiv_search import search
from saia.priority_arxiv_pilot import _sha


CASES = (
    {"phrase": "small modular reactor", "date_from": "2024-09-01", "as_of_date": "2026-09-01"},
    {"phrase": "sovereign cloud", "date_from": "2024-09-01", "as_of_date": "2026-09-01"},
    {"phrase": "vision-language-action", "date_from": "2024-01-01", "as_of_date": "2024-04-01",
     "matching_version": ORTHOGRAPHIC_MATCHING_VERSION},
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Verification output exists")
    rows = []
    for case in CASES:
        phrase = case["phrase"]
        plan = {"included_terms": [phrase], "exclusions": [],
                "date_from": case["date_from"], "as_of_date": case["as_of_date"]}
        if "matching_version" in case:
            plan["matching_version"] = case["matching_version"]
        started = perf_counter()
        full = search(args.mirror, plan, limit=100)
        full_seconds = perf_counter() - started
        started = perf_counter()
        indexed = search_publications(args.index, plan, limit=100)
        index_seconds = perf_counter() - started
        full_by_id = {work.source_ids[0]: asdict(work) for work in full.works}
        index_by_id = {work.source_ids[0]: asdict(work) for work in indexed.works}
        different = sorted(identifier for identifier in full_by_id.keys() | index_by_id.keys()
                           if full_by_id.get(identifier) != index_by_id.get(identifier))
        rows.append({"phrase": phrase, "matching_version": plan.get("matching_version", "literal-phrase-0.4.6"),
                     "date_from": plan["date_from"], "as_of_date": plan["as_of_date"],
                     "full_records": len(full.works),
                     "index_records": len(indexed.works),
                     "all_publication_fields_equal": not different,
                     "different_ids": different,
                     "full_seconds": full_seconds, "index_seconds": index_seconds})
    report = {"version": "arxiv-index-publication-equivalence-v2",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "index_manifest_sha256": _sha(args.index / "manifest.json"),
              "cases": rows,
              "all_equal": all(row["all_publication_fields_equal"] for row in rows),
              "scope": "two_narrow_english_literal_and_one_orthographic_query_not_all_user_requests"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"all_equal": report["all_equal"], "cases": rows}, ensure_ascii=False))
    if not report["all_equal"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
