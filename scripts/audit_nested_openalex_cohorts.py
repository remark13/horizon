"""Compare two saved OpenAlex query cohorts with nested publication periods.

Even cursor-complete cohorts can differ after OpenAlex reindexes records. This
audit describes overlap by source ID; it does not infer which snapshot is true.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "nested-openalex-cohort-overlap-v1"


def build(directory: Path, inner_mission: str, outer_mission: str) -> dict:
    import pyarrow.parquet as pq

    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parquet_path = directory / manifest["file"]["name"]
    if (parquet_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(parquet_path) != manifest["file"]["sha256"]):
        raise ValueError("OpenAlex materialization differs from manifest")
    cohorts = {item["mission_id"]: item for item in manifest["cohorts"]}
    inner, outer = cohorts[inner_mission], cohorts[outer_mission]
    if (inner["query_terms"] != outer["query_terms"]
            or not outer["period"]["from"] <= inner["period"]["from"]
            or not inner["period"]["to"] <= outer["period"]["to"]
            or inner["source_errors"] or outer["source_errors"]
            or inner["excluded_invalid_rows"] or outer["excluded_invalid_rows"]):
        raise ValueError("Queries, nested periods or source integrity differ")
    rows = pq.read_table(parquet_path, columns=[
        "openalex_id", "title", "publication_date", "source_mission_ids",
    ]).to_pylist()
    inner_rows = {row["openalex_id"]: row for row in rows
                  if inner_mission in (row["source_mission_ids"] or [])}
    outer_rows = {row["openalex_id"]: row for row in rows
                  if (outer_mission in (row["source_mission_ids"] or [])
                      and inner["period"]["from"] <= row["publication_date"]
                      <= inner["period"]["to"])}
    if len(inner_rows) != inner["source_record_count"]:
        raise ValueError("Inner cohort membership count differs")
    old_only = sorted(inner_rows.keys() - outer_rows.keys())
    new_only = sorted(outer_rows.keys() - inner_rows.keys())
    def samples(ids: list[str], source: dict[str, dict]) -> list[dict]:
        return [{"openalex_id": identifier,
                 "url": f"https://openalex.org/{identifier}",
                 "title": source[identifier]["title"],
                 "publication_date": source[identifier]["publication_date"]}
                for identifier in ids[:20]]
    return {
        "version": VERSION,
        "materialized_manifest_sha256": sha256_file(manifest_path),
        "materialized_parquet_sha256": manifest["file"]["sha256"],
        "query_terms": inner["query_terms"],
        "compared_publication_period": inner["period"],
        "inner_mission": {"id": inner_mission,
                          "fetch_finished_utc": inner["fetch_finished_utc"],
                          "source_manifest_sha256": inner["source_manifest_sha256"],
                          "record_count": len(inner_rows)},
        "outer_mission": {"id": outer_mission,
                          "fetch_finished_utc": outer["fetch_finished_utc"],
                          "source_manifest_sha256": outer["source_manifest_sha256"],
                          "record_count_in_compared_period": len(outer_rows)},
        "overlap_openalex_ids": len(inner_rows.keys() & outer_rows.keys()),
        "inner_only_ids": old_only,
        "outer_only_ids": new_only,
        "inner_only_examples": samples(old_only, inner_rows),
        "outer_only_examples": samples(new_only, outer_rows),
        "identical_result_sets": not old_only and not new_only,
        "interpretation": (
            "Different saved snapshots of the same expression are not interchangeable;"
            " do not infer scientific growth from the set difference."),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-dir", type=Path, required=True)
    parser.add_argument("--inner-mission", required=True)
    parser.add_argument("--outer-mission", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new immutable output path")
    result = build(args.cohort_dir, args.inner_mission, args.outer_mission)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"overlap": result["overlap_openalex_ids"],
                      "inner_only": len(result["inner_only_ids"]),
                      "outer_only": len(result["outer_only_ids"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
