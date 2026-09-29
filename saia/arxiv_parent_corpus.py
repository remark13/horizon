"""Audit a count-only arXiv parent corpus for a frozen phrase-scoped mission."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from saia.arxiv_local import digest as file_digest, inventory
from saia.arxiv_metadata import (IDENTIFIER, controlled_plan, first_submission,
                                  matches_controlled_plan, matches_text_scope,
                                  selection, text_scope)
from saia.controlled_collection import inventory_sha256, sha256_file
from saia.local_arxiv_search import policy as local_policy, validate_inventory
from saia.measurement import WindowCounts, analyze_series
from saia.package_observations import monthly_bounds, payload_hash
from saia.query_expansion import digest as payload_digest


def month_key(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year:04d}-{parsed.month:02d}"


def series(
    parent_counts: Counter,
    phrase_counts: Counter,
    start: date,
    end: date,
) -> list[dict]:
    points = []
    for left, right in monthly_bounds(start, end):
        key = month_key(left.isoformat())
        parent = parent_counts[key]
        phrase = phrase_counts[key]
        points.append(
            {
                "start": left.isoformat(),
                "end": right.isoformat(),
                "phrase_scope_works": phrase,
                "parent_category_works": parent,
                "share_within_frozen_category_snapshot": (
                    phrase / parent if parent else None
                ),
            }
        )
    return points


def linear_slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean_x = (len(values) - 1) / 2
    mean_y = sum(values) / len(values)
    denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
    return sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(values)
    ) / denominator


def audit(source: Path, mission_path: Path, package_manifest_path: Path) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    source = source.resolve()
    mission_path = mission_path.resolve()
    package_manifest_path = package_manifest_path.resolve()
    mission_bytes = mission_path.read_bytes()
    manifest_bytes = package_manifest_path.read_bytes()
    mission = json.loads(mission_bytes)
    package_manifest = json.loads(manifest_bytes)
    scope = text_scope(mission)
    plan = controlled_plan(mission)
    if scope is None and plan is None:
        raise ValueError("Parent audit requires an explicit phrase scope or controlled plan")
    wanted, start_text, end_inclusive_text = selection(mission)
    if not start_text or not end_inclusive_text:
        raise ValueError("Parent audit requires a bounded period")
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_inclusive_text) + timedelta(days=1)
    cutoff = date.fromisoformat(mission["as_of_date"])
    if end > cutoff:
        raise ValueError("Parent period extends beyond cutoff")
    block = package_manifest.get("sources", {}).get("arxiv", {})
    if (package_manifest.get("mission_id") != mission.get("mission_id")
            or package_manifest.get("mission_file_sha256")
            != hashlib.sha256(mission_bytes).hexdigest()):
        raise ValueError("Mission and selected package manifest do not match")
    controlled_profile = plan is not None and scope is None
    if controlled_profile:
        cfg = local_policy()
        if block.get("dataset_revision") != cfg["revision"]:
            raise ValueError("Mission package uses another local arXiv revision")
        paths = validate_inventory(str(source), cfg["expected_files"], cfg["expected_rows"])
        if inventory_sha256(paths, cfg["expected_rows"], cfg) != block.get(
                "upstream_inventory_sha256"):
            raise ValueError("Local mirror inventory differs from the selected package")
        frozen_inventory = [{"file": path.name} for path in paths]
        terms = list(plan["included_terms"])
        matches_scope = lambda row: matches_controlled_plan(row, plan)
        # The sealed package already contains the complete phrase-selected ID
        # cohort.  Checking every phrase against every parent row is O(parent ×
        # phrases) and gives the same numerator after package/parent ID equality
        # is verified below.  Read the small sealed ID set first, then evaluate
        # phrase membership only for those native IDs during the parent walk.
        selected_ids: set[str] = set()
        selected_files = block.get("files") or []
        if not selected_files:
            raise ValueError("Controlled package has no selected arXiv files")
        for selected_file in selected_files:
            filename = selected_file["file"]
            if Path(filename).name != filename:
                raise ValueError("Selected arXiv package filename is not a basename")
            selected_path = package_manifest_path.parent / "arxiv" / filename
            if sha256_file(selected_path) != selected_file["sha256"]:
                raise ValueError("Selected arXiv package file hash mismatch")
            parquet = pq.ParquetFile(selected_path)
            for selected_batch in parquet.iter_batches(batch_size=8192, columns=["id"]):
                for raw_id in selected_batch.column(0).to_pylist():
                    identifier = str(raw_id or "").strip()
                    if not IDENTIFIER.fullmatch(identifier) or identifier in selected_ids:
                        raise ValueError("Selected arXiv package has invalid or duplicate IDs")
                    selected_ids.add(identifier)
        if len(selected_ids) != block.get("total_records"):
            raise ValueError("Selected arXiv ID count differs from sealed manifest")
    else:
        frozen_inventory = inventory(source, mission)
        if frozen_inventory != block.get("upstream_inventory"):
            raise ValueError("Local mirror inventory differs from the selected package")
        terms = list(scope["phrases"])
        matches_scope = lambda row: matches_text_scope(row, mission)

    categories_pattern = (
        r"(^| )(?:(?:" + "|".join(re.escape(item) for item in sorted(wanted)) + r"))( |$)"
    )
    parent_counts: Counter = Counter()
    phrase_counts: Counter = Counter()
    phrase_member_ids: set[str] = set()
    parent_ids: set[str] = set()
    scanned = 0
    category_records = 0
    outside_period = 0
    invalid_v1_or_id = 0
    invalid_examples: list[dict] = []
    phrase_by_term = {phrase: Counter() for phrase in terms}

    for item in frozen_inventory:
        path = source / item["file"]
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(
                batch_size=8192,
                columns=["id", "title", "abstract", "categories", "versions"]):
            scanned += batch.num_rows
            table = pa.Table.from_batches([batch])
            selected_table = table
            if wanted:
                mask = pc.match_substring_regex(
                    pc.fill_null(table["categories"], ""), categories_pattern
                )
                selected_table = table.filter(mask)
            for row in selected_table.to_pylist():
                category_records += 1
                identifier = str(row.get("id") or "").strip()
                try:
                    if not IDENTIFIER.fullmatch(identifier):
                        raise ValueError("Invalid arXiv ID")
                    created = first_submission(row.get("versions"))
                except ValueError as error:
                    invalid_v1_or_id += 1
                    if len(invalid_examples) < 20:
                        invalid_examples.append(
                            {"source_record_id": identifier or None, "reason": str(error)}
                        )
                    continue
                if not start_text <= created < end.isoformat():
                    outside_period += 1
                    continue
                if identifier in parent_ids:
                    raise ValueError("Duplicate native arXiv ID in parent corpus")
                parent_ids.add(identifier)
                key = month_key(created)
                parent_counts[key] += 1
                if (not controlled_profile or identifier in selected_ids) and matches_scope(row):
                    phrase_counts[key] += 1
                    phrase_member_ids.add(identifier)
                    for phrase in terms:
                        if controlled_profile:
                            one_phrase = {"included_terms": [phrase],
                                          "exclusions": plan.get("exclusions", []),
                                          "matching_version": plan.get("matching_version", "literal-phrase-0.4.6")}
                            matched = matches_controlled_plan(row, one_phrase)
                        else:
                            one_phrase = {"query": {"arxiv_local_text_scope": {
                                "mode": "any_exact_phrase", "fields": ["title", "abstract"],
                                "phrases": [phrase]}}}
                            matched = matches_text_scope(row, one_phrase)
                        if matched:
                            phrase_by_term[phrase][key] += 1
        if not controlled_profile and file_digest(path) != item["sha256"]:
            raise ValueError("Mirror shard changed during parent audit")

    # The selected package total is a structural cross-check. Exact IDs are
    # verified by the observations report in the caller workflow.
    if (len(phrase_member_ids) != block.get("total_records")
            or (controlled_profile and phrase_member_ids != selected_ids)):
        raise ValueError("Parent phrase membership differs from the sealed selected package")

    points = series(parent_counts, phrase_counts, start, end)
    publication_series = analyze_series(
        [
            WindowCounts(
                left,
                right,
                phrase_counts[month_key(left.isoformat())],
                parent_counts[month_key(left.isoformat())],
                coverage_comparable=True,
            )
            for left, right in monthly_bounds(start, end)
        ],
        cutoff,
    )
    report = {
        "version": "arxiv-parent-corpus-audit-0.4.16",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mission_id": mission["mission_id"],
        "as_of_date": cutoff.isoformat(),
        "period_from": start.isoformat(),
        "period_end_exclusive": end.isoformat(),
        "input": {
            "source": str(source),
            "dataset_revision": block["dataset_revision"],
            "upstream_inventory_sha256": block["upstream_inventory_sha256"],
            "mission_bytes_sha256": hashlib.sha256(mission_bytes).hexdigest(),
            "selected_package_manifest_bytes_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "categories": sorted(wanted),
            "text_scope": scope,
            "controlled_search_plan_sha256": payload_digest(plan) if plan else None,
        },
        "counts": {
            "scanned_metadata_rows": scanned,
            "category_records_all_dates": category_records,
            "category_records_outside_period": outside_period,
            "invalid_v1_or_id_unknown_period": invalid_v1_or_id,
            "parent_native_ids_in_period": len(parent_ids),
            "phrase_scope_native_ids_in_period": len(phrase_member_ids),
        },
        "series": points,
        "publication_series": publication_series,
        "full_period_descriptive": {
            "window_count": len(points),
            "phrase_count_slope_per_month": linear_slope(
                [point["phrase_scope_works"] for point in points]
            ),
            "parent_count_slope_per_month": linear_slope(
                [point["parent_category_works"] for point in points]
            ),
            "share_slope_per_month": linear_slope(
                [point["share_within_frozen_category_snapshot"] for point in points]
            ),
            "phrase_count_first_to_last_change": (
                points[-1]["phrase_scope_works"] - points[0]["phrase_scope_works"]
            ),
            "parent_count_first_to_last_change": (
                points[-1]["parent_category_works"] - points[0]["parent_category_works"]
            ),
            "share_first_to_last_change": (
                points[-1]["share_within_frozen_category_snapshot"]
                - points[0]["share_within_frozen_category_snapshot"]
            ),
            "role": "descriptive_all_24_complete_months_not_screen_decision",
        },
        "phrase_series": [
            {
                "phrase": phrase,
                "points": [
                    {
                        "start": left.isoformat(),
                        "end": right.isoformat(),
                        "works": counts[month_key(left.isoformat())],
                    }
                    for left, right in monthly_bounds(start, end)
                ],
            }
            for phrase, counts in phrase_by_term.items()
        ],
        "coverage": {
            "inventory_traversal_complete": (
                scanned == (local_policy()["expected_rows"] if controlled_profile
                            else sum(row["rows"] for row in frozen_inventory))
            ),
            "within_frozen_snapshot_same_query_window_comparable": invalid_v1_or_id == 0,
            "world_science_coverage_complete": None,
            "crosslist_completeness": block.get("crosslist_completeness"),
            "field_coverage": block.get("field_coverage"),
            "unit": "unique_native_arxiv_id_not_independent_research",
            "invalid_examples": invalid_examples,
        },
        "accuracy_evaluated": False,
        "models_used": False,
        "database_written": False,
        "confirmed_weak_signals": None,
        "limitations": [
            ("The denominator is the entire pinned arXiv metadata snapshot in the period, not all science."
             if controlled_profile else
             "The denominator is the selected-category frozen arXiv mirror scope, not all AI or all science."),
            "A stable ratio inside one snapshot does not prove stable real-world coverage over publication time.",
            "Native arXiv IDs are not adjudicated independent studies.",
            "Current title and abstract text are used; historical text versions are not reconstructed.",
        ],
    }
    report["report_payload_sha256"] = payload_hash(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--package-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output exists; choose a new immutable report path")
    report = audit(args.source, args.mission, args.package_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "version": report["version"],
                "counts": report["counts"],
                "coverage": report["coverage"],
                "report_payload_sha256": report["report_payload_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
