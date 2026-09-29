"""Read-only check of the opportunistic OpenAlex cache for a frozen pilot."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import sha256_file
from saia.openalex_cache_search import scan


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("version") != "ru-compound-multicase-pilot-v1":
        raise ValueError("Unknown fixed case list")
    specs = [{"branch_id": case["case_id"], "included_phrases": [],
              "concept_groups": case["concept_groups"], "excluded_phrases": []}
             for case in config["cases"]]
    source = scan(specs, date.fromisoformat(config["date_from"]),
                  date.fromisoformat(config["as_of_date"]), 25)
    return {
        "version": "fixed-compound-openalex-cache-probe-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "source_version": source["version"],
        "available_unique_openalex_ids_in_period": source["available_unique_ids_in_period"],
        "latest_cache_ingested_at": source["latest_cache_ingested_at"],
        "period": {"from": config["date_from"],
                   "as_of_exclusive": config["as_of_date"]},
        "rows": [{"case_id": row["branch_id"],
                  "eligible_matches": row["eligible_matches"],
                  "selected_count": len(row["works"]),
                  "selected_year_counts": row["selected_year_counts"],
                  "works": [{"title": work["title"], "published_at": work["published_at"],
                             "urls": work["urls"], "abstract": work["abstract"]}
                            for work in row["works"]]}
                 for row in source["branches"]],
        "policy": {"cache_not_complete_openalex": True,
                   "concept_only_no_russian_literal_route": True,
                   "zero_not_absence_of_science": True,
                   "matches_not_relevance_labels_or_weak_signals": True,
                   "no_live_api_calls": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe output is immutable")
    report = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({row["case_id"]: row["eligible_matches"]
                      for row in report["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
