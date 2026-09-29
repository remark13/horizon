"""Title-phrase proposals from one pinned, query-complete OpenAlex cohort.

This supplies a source-aware fallback when arXiv coverage is sparse. It does
not infer scientific novelty, first mention, one mechanism or a weak signal.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path
import re

from saia.broad_title_phrases import (
    _matches_parent, _parent_regex, _validate_config, propose,
)
from saia.controlled_collection import sha256_file
from saia.openalex_work_variants import collapse


def collect_parent(cohort_dir: Path, config: dict) -> tuple[list[dict], dict]:
    import pyarrow.parquet as pq

    _validate_config(config)
    if config.get("source_type") != "openalex":
        raise ValueError("OpenAlex source type is required")
    excluded_types = config.get("excluded_document_types", [])
    if (not isinstance(excluded_types, list) or len(excluded_types) != len(set(excluded_types))
            or any(not isinstance(value, str)
                   or not re.fullmatch(r"[a-z][a-z-]{0,39}", value)
                   for value in excluded_types)):
        raise ValueError("Invalid excluded_document_types")
    deduplicate_variants = config.get("collapse_exact_title_author_variants", False)
    if not isinstance(deduplicate_variants, bool):
        raise ValueError("Invalid collapse_exact_title_author_variants")
    manifest_path = cohort_dir / "manifest.json"
    if sha256_file(manifest_path) != config["openalex_manifest_sha256"]:
        raise ValueError("Pinned OpenAlex manifest differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_info = manifest["file"]
    path = cohort_dir / file_info["name"]
    if (path.stat().st_size != file_info["bytes"]
            or sha256_file(path) != file_info["sha256"]):
        raise ValueError("Pinned OpenAlex Parquet differs")
    mission_id = config["openalex_mission_id"]
    cohort = next((item for item in manifest["cohorts"]
                   if item["mission_id"] == mission_id), None)
    end = date.fromisoformat(config["as_of_date_exclusive"])
    expected_period = {"from": config["date_from"],
                       "to": (end - timedelta(days=1)).isoformat()}
    if (cohort is None or cohort["period"] != expected_period
            or cohort.get("priority_catalog_area_id") != config.get(
                "priority_catalog_area_id")
            or cohort["source_errors"] or cohort["excluded_invalid_rows"]
            or not set(cohort["query_terms"]) <= set(config["parent_terms"])):
        raise ValueError("OpenAlex query cohort does not match pinned area and period")
    parent = _parent_regex(config["parent_terms"])
    context = (_parent_regex(config["required_context_terms"])
               if config.get("required_context_terms") else None)
    selected = []
    in_cohort = 0
    missing_abstract = 0
    excluded_by_type: Counter[str] = Counter()
    excluded_nonmatching = 0
    for row in pq.read_table(path, columns=[
        "openalex_id", "openalex_url", "title", "abstract",
        "publication_date", "document_type", "authors",
        "source_mission_ids"]).to_pylist():
        if mission_id not in (row["source_mission_ids"] or []):
            continue
        in_cohort += 1
        if not row["abstract"]:
            missing_abstract += 1
        if not config["date_from"] <= row["publication_date"] < config[
                "as_of_date_exclusive"]:
            raise ValueError("OpenAlex work outside frozen publication period")
        if row["document_type"] in excluded_types:
            excluded_by_type[row["document_type"]] += 1
            continue
        if not _matches_parent(f"{row['title'] or ''} {row['abstract'] or ''}",
                               parent, context):
            excluded_nonmatching += 1
            continue
        selected.append(row)
        if len(selected) > config["max_parent_works"]:
            raise ValueError("OpenAlex exact parent exceeds frozen safety cap")
    if in_cohort != cohort["source_record_count"]:
        raise ValueError("OpenAlex source membership count differs")
    exact_parent_records = len(selected)
    variant_audit = None
    if deduplicate_variants:
        selected, variant_audit = collapse(selected)
    return selected, {
        "query_cohort_records": in_cohort,
        "selected_unique_ids": len(selected),
        "exact_parent_records_before_variant_collapse": exact_parent_records,
        "variant_family_audit": variant_audit,
        "excluded_document_types": dict(sorted(excluded_by_type.items())),
        "excluded_document_type_count": sum(excluded_by_type.values()),
        "excluded_current_title_abstract_not_matching_parent": excluded_nonmatching,
        "cohort_records_without_abstract": missing_abstract,
        "parent_predicate": "whole-term in current title or abstract",
        "source_scope": "one complete saved OpenAlex query cohort, not whole field",
        "source_mission_id": mission_id,
        "source_manifest_sha256": cohort["source_manifest_sha256"],
        "materialized_manifest_sha256": config["openalex_manifest_sha256"],
        "materialized_parquet_sha256": file_info["sha256"],
        "priority_catalog_area_id": cohort.get("priority_catalog_area_id"),
        "query_terms": cohort["query_terms"],
    }


def build(cohort_dir: Path, config: dict) -> dict:
    rows, audit = collect_parent(cohort_dir, config)
    result = propose(rows, config, collection_audit=audit)
    result["limitations"].extend([
        "OpenAlex search can match fields absent from materialized title/abstract; excluded records are not proven irrelevant.",
        "The query cohort changed between source snapshots; annual counts are descriptive for this fixed snapshot only.",
        "A document may belong to several saved cohorts but is counted once in this cohort by OpenAlex ID.",
    ])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cohort-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new immutable output path")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    report = build(args.cohort_dir, config)
    report["config_sha256"] = sha256_file(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"parent_works": report["parent_collection"]["selected_unique_ids"],
                      "eligible_phrases": report["phrases_above_eligibility_rules"],
                      "display_groups": len(report["display_groups"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
