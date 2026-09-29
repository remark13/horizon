"""Reproducible multi-case concept-group probe on the pinned local arXiv index.

This is a retrieval diagnostic, never an estimate of weak-signal precision.
The input plan must be fixed before inspecting its output; the report is immutable.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search, supports_concept_plan


VERSION = "fixed-compound-arxiv-probe-v1"
GOAL_CONFIG_VERSION = "goal-cross-domain-compound-pilot-v1"
GOAL_REPORT_VERSION = "goal-cross-domain-compound-arxiv-probe-v1"


def _validate_goal_cases(config: dict, catalog_path: Path | None) -> str:
    if catalog_path is None:
        raise ValueError("Goal pilot requires the versioned source catalog")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("version") != "priority-catalog-v1":
        raise ValueError("Unexpected priority catalog version")
    national = {row["id"]: row for row in catalog["national_search_areas"]}
    customer = {row["id"]: row for row in catalog["customer_examples"]}
    counts = {role: 0 for role in ("national_search_area", "customer_supplied_example",
                                   "mature_control", "negative_control",
                                   "outside_priority_map")}
    directions = set()
    customer_areas = set()
    for case in config["cases"]:
        role = case.get("role")
        if role not in counts:
            raise ValueError(f"Unexpected goal pilot role: {role}")
        counts[role] += 1
        if role == "national_search_area":
            source = national.get(case["case_id"])
            if source is None or source["direction_id"] != case.get("direction_id"):
                raise ValueError(f"National area mapping mismatch: {case['case_id']}")
            directions.add(case["direction_id"])
        elif role == "customer_supplied_example":
            source = customer.get(case["case_id"])
            if source is None or source["client_area_id"] != case.get("client_area_id"):
                raise ValueError(f"Customer area mapping mismatch: {case['case_id']}")
            customer_areas.add(case["client_area_id"])
    if (counts != {"national_search_area": 10, "customer_supplied_example": 6,
                   "mature_control": 2, "negative_control": 2,
                   "outside_priority_map": 2}
            or directions != {row["id"] for row in catalog["directions"]}
            or customer_areas != {row["id"] for row in catalog["client_search_areas"]}):
        raise ValueError("Goal pilot does not cover ten directions and six customer areas")
    if not 1 <= config.get("max_matches_per_case", 0) <= 1000:
        raise ValueError("Goal pilot match cap missing or unsafe")
    return sha256_file(catalog_path)


def run(config_path: Path, index_dir: Path,
        catalog_path: Path | None = None) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_version = config.get("version")
    if config_version not in {"ru-compound-multicase-pilot-v1", GOAL_CONFIG_VERSION}:
        raise ValueError("Unknown fixed case list")
    cases = config.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Missing fixed cases")
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate case ID")
    catalog_sha256 = (_validate_goal_cases(config, catalog_path)
                      if config_version == GOAL_CONFIG_VERSION else None)
    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != "arxiv-trigram-index-v4":
        raise ValueError("A complete guarded index v4 is required")
    rows = []
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                                 uri=True)
    try:
        for case in cases:
            plan = {"concept_groups": case["concept_groups"], "exclusions": [],
                    "date_from": config["date_from"],
                    "as_of_date": config["as_of_date"]}
            if not supports_concept_plan(plan):
                raise ValueError(f"Unsupported fixed plan: {case['case_id']}")
            started = perf_counter()
            try:
                result = exact_concept_search(
                    index_dir, plan,
                    max_matches=config.get("max_matches_per_case", 300))
            except ValueError as exc:
                if str(exc) == "Concept cohort exceeds safe limit; refine the plan":
                    rows.append({"case_id": case["case_id"], "role": case["role"],
                                 "direction_id": case.get("direction_id"),
                                 "client_area_id": case.get("client_area_id"),
                                 "status": "too_broad", "seconds": perf_counter() - started,
                                 "reason": str(exc)})
                    continue
                if str(exc) == "Concept query intersects duplicate source IDs":
                    rows.append({"case_id": case["case_id"], "role": case["role"],
                                 "direction_id": case.get("direction_id"),
                                 "client_area_id": case.get("client_area_id"),
                                 "status": "duplicate_guard_requires_full_scan",
                                 "seconds": perf_counter() - started,
                                 "reason": str(exc)})
                    continue
                raise
            ids = result["arxiv_ids"]
            samples = []
            for arxiv_id in ids[:20]:
                work = connection.execute(
                    "SELECT title,abstract,first_submission_date,categories FROM works "
                    "WHERE arxiv_id=?", (arxiv_id,)).fetchone()
                if work is None:
                    raise ValueError("Index match disappeared during probe")
                samples.append({"arxiv_id": arxiv_id,
                                "url": f"https://arxiv.org/abs/{arxiv_id}",
                                "title": work[0], "abstract": work[1],
                                "first_submission_date": work[2],
                                "categories": work[3]})
            rows.append({"case_id": case["case_id"], "role": case["role"],
                         "direction_id": case.get("direction_id"),
                         "client_area_id": case.get("client_area_id"),
                         "status": "exact_index_probe_complete",
                         "seconds": perf_counter() - started,
                         "arxiv_ids": ids, "match_count": len(ids),
                         "sample_works": samples, "audit": result["audit"]})
    finally:
        connection.close()
    return {"version": GOAL_REPORT_VERSION if catalog_sha256 else VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "catalog_sha256": catalog_sha256,
            "index_manifest_sha256": sha256_file(manifest_path),
            "source_revision": manifest["source"]["revision"],
            "period": {"from": config["date_from"],
                       "as_of_exclusive": config["as_of_date"]},
            "rows": rows,
            "policy": {"developer_reviewed_terms_not_independent_gold": True,
                       "not_executed_in_user_route": True,
                       "matches_are_not_weak_signals": True,
                       "zero_is_not_absence_of_science": True,
                       "current_metadata_text_not_historical_v1": True,
                       "source_only_not_end_to_end_latency": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--catalog", type=Path,
                        default=Path("data/reference/priority_catalog/v1/catalog.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe output is immutable")
    report = run(args.config, args.index, args.catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({row["case_id"]: row.get("match_count", row["status"])
                      for row in report["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
