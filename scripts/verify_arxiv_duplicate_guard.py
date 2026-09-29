"""Compare guarded index path with the full mirror for one duplicate hazard."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.local_arxiv_search import search


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Guard verification output exists")
    cases = [
        ("nonduplicate_fast", "sovereign cloud", "2024-09-01"),
        ("older_duplicate_fallback", "Galilea relativity", "2000-01-01"),
    ]
    old = os.environ.get("SAIA_ARXIV_TRIGRAM_INDEX_DIR")
    os.environ["SAIA_ARXIV_TRIGRAM_INDEX_DIR"] = str(args.index.resolve())
    observations = []
    try:
        for role, phrase, start in cases:
            plan = {"included_terms": [phrase], "exclusions": [],
                    "date_from": start, "as_of_date": "2026-09-01"}
            began = perf_counter()
            found = search(args.mirror, plan, limit=100)
            observations.append({"role": role, "phrase": phrase,
                                 "adapter_version": found.audit["adapter_version"],
                                 "scanned_rows": found.audit["scanned_rows"],
                                 "eligible_matches": found.audit["eligible_matches"],
                                 "source_ids": [work.source_ids[0] for work in found.works],
                                 "seconds": perf_counter() - began})
    finally:
        if old is None:
            os.environ.pop("SAIA_ARXIV_TRIGRAM_INDEX_DIR", None)
        else:
            os.environ["SAIA_ARXIV_TRIGRAM_INDEX_DIR"] = old
    if (observations[0]["adapter_version"] != "arxiv-trigram-index-v4"
            or observations[0]["eligible_matches"] != 3
            or observations[1]["adapter_version"] != "local-arxiv-fallback-0.4.15"
            or observations[1]["eligible_matches"] != 1
            or observations[1]["scanned_rows"] != 3_164_528):
        raise ValueError("Guarded index did not preserve expected retrieval paths")
    report = {"version": "arxiv-duplicate-guard-verification-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "index_manifest_sha256": sha256_file(args.index / "manifest.json"),
              "cases": observations,
              "scope": "two narrow phrases only; not exhaustive equivalence for all possible queries"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({item["role"]: {"adapter_version": item["adapter_version"],
                                     "eligible_matches": item["eligible_matches"],
                                     "seconds": item["seconds"]}
                      for item in observations}, ensure_ascii=False))


if __name__ == "__main__":
    main()
