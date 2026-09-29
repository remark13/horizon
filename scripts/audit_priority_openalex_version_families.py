"""Read-only possible-version audit of the saved OpenAlex priority corpus."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pyarrow.parquet as pq

from saia.controlled_collection import sha256_file
from saia.research_families import audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Version-family audit output is immutable")
    manifest_path = args.corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file = args.corpus / manifest["file"]["name"]
    if (manifest.get("version") != "priority-openalex-complete-cohorts-v2"
            or file.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(file) != manifest["file"]["sha256"]):
        raise ValueError("Unknown or modified OpenAlex priority corpus")
    table = pq.read_table(file, columns=["openalex_id", "openalex_url", "doi",
                                         "title", "abstract", "publication_date",
                                         "authors", "source_mission_id"])
    if table.num_rows != manifest["counts"]["unique_openalex_works"]:
        raise ValueError("OpenAlex row count differs from manifest")
    source_rows = table.to_pylist()
    works = [{"title": row["title"], "abstract": row["abstract"],
              "authors": row["authors"], "published_at": row["publication_date"],
              "doi": row["doi"], "source_ids": [row["openalex_url"]]}
             for row in source_rows]
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "corpus_manifest_sha256": sha256_file(manifest_path),
              "corpus_file_sha256": manifest["file"]["sha256"],
              "scope": "three saved query-complete OpenAlex cohorts, not the full field",
              **audit(works)}
    by_id = {row["openalex_url"]: row["source_mission_id"] for row in source_rows}
    report["candidate_pair_missions"] = [
        {"left": by_id[pair["left_source_ids"][0]],
         "right": by_id[pair["right_source_ids"][0]]}
        for pair in report["candidate_pairs"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("record_count", "same_title_group_count",
                       "records_in_same_title_groups", "candidate_pair_count")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
