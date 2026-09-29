"""Build a sealed-input package from an approved query and a pinned arXiv mirror.

The package is deliberately source-specific.  It does not call a bounded
preview "complete" and it never mutates an older query version or package.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from saia import db
from saia.arxiv_metadata import (first_submission, matches_controlled_plan,
                                  validate_schema)
from saia.local_arxiv_search import _substring_mask, policy, validate_inventory
from saia.query_expansion import digest


CONNECTOR_VERSION = "controlled-local-arxiv-0.4.16"
PROFILE_VERSION = "controlled-source-profile-0.4.16"
MAX_PACKAGE_BYTES = 1024 ** 3


def canonical_json(value: dict) -> str:
    """Use the exact serialization used by query_expansion.digest."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def derive_arxiv_profile(base: dict, query_version_id: str, reviewed_by: str,
                         openalex_observation: dict) -> dict:
    """Create a new immutable execution profile; do not rewrite the base query."""
    if not isinstance(base.get("controlled_search_plan"), dict):
        raise ValueError("Base query has no approved controlled_search_plan")
    mission_id = base.get("mission_id")
    prefix = f"{mission_id}/v"
    if not isinstance(query_version_id, str) or not query_version_id.startswith(prefix):
        raise ValueError("Derived query_version must be a version of the same mission")
    if not reviewed_by.strip() or len(reviewed_by.strip()) > 120:
        raise ValueError("reviewed_by is required and limited to 120 characters")
    if (not isinstance(openalex_observation, dict)
            or not isinstance(openalex_observation.get("source_reported_count"), int)
            or openalex_observation["source_reported_count"] < 0
            or not openalex_observation.get("observed_at")):
        raise ValueError("A dated OpenAlex size observation is required")
    result = copy.deepcopy(base)
    result["query_version"] = query_version_id
    result["sources"] = ["arxiv"]
    result["expansion_source"] = "controlled_source_profile"
    result["collection_profile"] = {
        "version": PROFILE_VERSION,
        "base_query_version_id": base["query_version"],
        "selected_sources": ["arxiv"],
        "reviewed_by": reviewed_by.strip(),
        "decision": "full_local_arxiv_first",
        "openalex_observation": openalex_observation,
        "limitations": [
            "OpenAlex is omitted from this complete collection profile; its bounded preview is not treated as full coverage.",
            "The pinned arXiv mirror contains current metadata and v1 dates, not historically frozen v1 title/abstract text.",
            "Completeness is only relative to the pinned mirror inventory and exact approved phrase predicate.",
        ],
    }
    return result


def inventory_sha256(files: tuple[Path, ...], expected_rows: int, cfg: dict) -> str:
    entries = []
    for path in files:
        stat = path.stat()
        resolved = path.resolve()
        # Hugging Face snapshot files are content-addressed symlinks.  The
        # resolved blob name is the cheap immutable content identity; the
        # package audit must not rely on file names and sizes alone.
        entries.append({"file": path.name, "bytes": stat.st_size,
                        "resolved_blob": resolved.name})
    return digest({"dataset": cfg["dataset"], "revision": cfg["revision"],
                   "expected_rows": expected_rows, "files": entries})


def export_selected_arxiv(mirror_dir: str | Path, destination: Path, mission: dict,
                          *, expected_files: int | None = None,
                          expected_rows: int | None = None,
                          max_records: int | None = None,
                          max_package_bytes: int = MAX_PACKAGE_BYTES,
                          thematic_cache_dir: str | Path | None = None,
                          thematic_packs_dir: str | Path | None = None) -> dict:
    """Write every matching raw mirror row and prove the selected cohort count."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    plan = mission.get("controlled_search_plan")
    if not isinstance(plan, dict):
        raise ValueError("Mission has no controlled_search_plan")
    start = date.fromisoformat(plan["date_from"])
    cutoff = date.fromisoformat(plan["as_of_date"])
    if start >= cutoff:
        raise ValueError("Invalid controlled search period")
    included = list(plan.get("included_terms") or [])
    if not included:
        raise ValueError("Controlled plan has no included terms")
    if (max_records is not None
            and (not isinstance(max_records, int) or isinstance(max_records, bool)
                 or max_records < 1)):
        raise ValueError("max_records must be a positive integer")
    cfg = policy()
    expected_files = cfg["expected_files"] if expected_files is None else expected_files
    expected_rows = cfg["expected_rows"] if expected_rows is None else expected_rows
    files = validate_inventory(str(Path(mirror_dir).resolve()), expected_files, expected_rows)
    source_inventory_sha256 = inventory_sha256(files, expected_rows, cfg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Destination already exists; immutable packages are never overwritten")

    explicit_thematic_cache = thematic_cache_dir is not None or thematic_packs_dir is not None
    if explicit_thematic_cache and not (thematic_cache_dir and thematic_packs_dir):
        raise ValueError("Both thematic cache and target-pack directories are required")
    if not explicit_thematic_cache:
        # Container defaults belong to the canonical pinned mirror.  A unit
        # fixture or another explicitly sized inventory must not accidentally
        # inherit those paths from the worker environment.
        if (expected_files, expected_rows) == (cfg["expected_files"], cfg["expected_rows"]):
            thematic_cache_dir = os.environ.get("SAIA_THEMATIC_ARXIV_CACHE_DIR")
            thematic_packs_dir = os.environ.get("SAIA_THEMATIC_ARXIV_PACKS_DIR")
        else:
            thematic_cache_dir = thematic_packs_dir = None
    cache_not_used_reason = (
        "noncanonical_inventory_does_not_inherit_environment_cache"
        if not explicit_thematic_cache
        and (expected_files, expected_rows) != (cfg["expected_files"], cfg["expected_rows"])
        else None
    )
    if thematic_cache_dir and thematic_packs_dir:
        from saia.thematic_arxiv_cache import (
            coverage_for_plan, load_cache, select_from_target_packs,
        )
        cache_manifest, cache_anchors = load_cache(thematic_cache_dir)
        cache_source = cache_manifest.get("source") or {}
        if (
            cache_source.get("revision") != cfg["revision"]
            or cache_source.get("inventory_rows") != expected_rows
            or cache_source.get("inventory_sha256") != source_inventory_sha256
        ):
            raise ValueError(
                "Configured thematic arXiv cache belongs to another pinned mirror"
            )
        cache_coverage = coverage_for_plan(cache_anchors, plan)
        cache_start = date.fromisoformat(cache_manifest["period"]["from"])
        cache_cutoff = date.fromisoformat(cache_manifest["period"]["as_of_exclusive"])
        if not cache_coverage["complete"]:
            cache_not_used_reason = (
                "orthographic_matching_not_covered_by_literal_cache"
                if plan.get("matching_version", "literal-phrase-0.4.6")
                != "literal-phrase-0.4.6"
                else "approved_include_phrases_not_fully_indexed"
            )
        elif start < cache_start or cutoff > cache_cutoff:
            cache_not_used_reason = "approved_period_outside_cache"
        else:
            selected_table, cache_audit = select_from_target_packs(
                thematic_cache_dir, thematic_packs_dir, plan,
                max_records=max_records,
            )
            if selected_table.num_rows == 0:
                raise ValueError("Controlled local arXiv cohort is empty")
            pq.write_table(selected_table, destination, compression="zstd")
            if destination.stat().st_size > max_package_bytes:
                destination.unlink(missing_ok=True)
                raise ValueError("Selected arXiv package exceeded the 1 GiB stage limit")
            return {
                "adapter_version": cfg["version"],
                "dataset": cfg["dataset"], "revision": cfg["revision"],
                "inventory_files": len(files), "inventory_rows": expected_rows,
                "inventory_sha256": source_inventory_sha256,
                "scanned_rows": cache_audit["target_pack_rows_scanned"],
                "source_inventory_rows_not_rescanned": expected_rows,
                "coarse_text_matches": cache_audit["cache_coarse_rows"],
                "exact_text_matches_all_dates": None,
                "selected_records": selected_table.num_rows,
                "unique_arxiv_ids": selected_table.num_rows,
                "invalid_selected_records": 0, "invalid_examples": [],
                "date_semantics": "v1_created_utc; start_inclusive_as_of_exclusive",
                "text_semantics": {**cfg["matching"], "matching_version": plan.get(
                    "matching_version", "literal-phrase-0.4.6")},
                "selection_engine": "verified_thematic_target_pack",
                "cache_manifest_sha256": cache_audit["cache_manifest_sha256"],
                "target_pack_manifest_sha256": cache_audit[
                    "target_pack_manifest_sha256"
                ],
                "selected_target_packs": cache_audit["selected_target_packs"],
                "selection_predicate_reapplied": True,
                "output_bytes": destination.stat().st_size,
                "output_sha256": sha256_file(destination),
            }

    writer = None
    scanned = coarse = exact = selected = invalid = 0
    identifiers: set[str] = set()
    invalid_examples: list[dict] = []
    try:
        for path in files:
            parquet = pq.ParquetFile(path)
            validate_schema(parquet.schema_arrow.names)
            for batch in parquet.iter_batches(batch_size=8192):
                scanned += batch.num_rows
                table = pa.Table.from_batches([batch])
                coarse_table = table.filter(pc.fill_null(_substring_mask(table, included), False))
                coarse += coarse_table.num_rows
                rows = coarse_table.to_pylist()
                keep = []
                for index, row in enumerate(rows):
                    if not matches_controlled_plan(row, plan):
                        continue
                    exact += 1
                    try:
                        created = date.fromisoformat(first_submission(row.get("versions")))
                        identifier = str(row.get("id") or "").strip()
                        if not identifier:
                            raise ValueError("empty arXiv id")
                    except ValueError as error:
                        invalid += 1
                        if len(invalid_examples) < 20:
                            invalid_examples.append({"arxiv_id": row.get("id"),
                                                     "reason": str(error)})
                        continue
                    if not start <= created < cutoff:
                        continue
                    if identifier in identifiers:
                        raise ValueError(f"Duplicate selected arXiv id: {identifier}")
                    identifiers.add(identifier)
                    keep.append(index)
                if keep:
                    if max_records is not None and selected + len(keep) > max_records:
                        raise ValueError(
                            f"Selected arXiv cohort exceeds the approved limit of "
                            f"{max_records} records; narrow or split the query"
                        )
                    chosen = coarse_table.take(pa.array(keep, type=pa.int64()))
                    if writer is None:
                        writer = pq.ParquetWriter(destination, chosen.schema,
                                                  compression="zstd")
                    elif not chosen.schema.equals(writer.schema):
                        raise ValueError("Pinned arXiv shards do not share one schema")
                    writer.write_table(chosen)
                    selected += chosen.num_rows
                    if destination.stat().st_size > max_package_bytes:
                        raise ValueError("Selected arXiv package exceeded the 1 GiB stage limit")
        if invalid:
            raise ValueError(
                f"Selected predicate encountered {invalid} invalid records; complete status refused"
            )
        if writer is None or selected == 0:
            raise ValueError("Controlled local arXiv cohort is empty")
    except Exception:
        if writer is not None:
            writer.close()
            writer = None
        destination.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()
    if destination.stat().st_size > max_package_bytes:
        raise ValueError("Selected arXiv package exceeded the 1 GiB stage limit")
    return {
        "adapter_version": cfg["version"],
        "dataset": cfg["dataset"],
        "revision": cfg["revision"],
        "inventory_files": len(files),
        "inventory_rows": expected_rows,
        "inventory_sha256": source_inventory_sha256,
        "scanned_rows": scanned,
        "coarse_text_matches": coarse,
        "exact_text_matches_all_dates": exact,
        "selected_records": selected,
        "unique_arxiv_ids": len(identifiers),
        "invalid_selected_records": invalid,
        "invalid_examples": invalid_examples,
        "date_semantics": "v1_created_utc; start_inclusive_as_of_exclusive",
        "text_semantics": {**cfg["matching"], "matching_version": plan.get(
            "matching_version", "literal-phrase-0.4.6")},
        "output_bytes": destination.stat().st_size,
        "output_sha256": sha256_file(destination),
        "selection_engine": "full_pinned_mirror_scan",
        "thematic_cache_not_used_reason": cache_not_used_reason,
    }


def build_package(mission_path: str | Path, mirror_dir: str | Path,
                  output: str | Path, *, expected_files: int | None = None,
                  expected_rows: int | None = None,
                  max_records: int | None = None) -> dict:
    mission_path = Path(mission_path).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Output package already exists; choose a new path")
    raw_text = mission_path.read_text(encoding="utf-8")
    mission = json.loads(raw_text)
    if raw_text != canonical_json(mission):
        raise ValueError("Mission snapshot must use canonical query-version serialization")
    if sha256_bytes(raw_text.encode()) != digest(mission):
        raise ValueError("Mission snapshot hash does not match its canonical payload")
    if mission.get("sources") != ["arxiv"]:
        raise ValueError("This builder requires the explicit arXiv-only source profile")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    try:
        (temp / "arxiv").mkdir()
        (temp / "mission.json").write_text(raw_text, encoding="utf-8")
        selected_path = temp / "arxiv" / "selected.parquet"
        audit = export_selected_arxiv(
            mirror_dir, selected_path, mission,
            expected_files=expected_files, expected_rows=expected_rows,
            max_records=max_records,
        )
        audit_text = canonical_json(audit)
        (temp / "collection_audit.json").write_text(audit_text, encoding="utf-8")
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        plan_hash = digest(mission["controlled_search_plan"])
        manifest = {
            "connector_version": CONNECTOR_VERSION,
            "mission_id": mission["mission_id"],
            "query_version": mission["query_version"],
            "mission_snapshot_file": "mission.json",
            "mission_file_sha256": sha256_bytes(raw_text.encode()),
            "fetch_started_utc": fetched_at,
            "fetch_finished_utc": fetched_at,
            "audit_file": "collection_audit.json",
            "audit_sha256": sha256_bytes(audit_text.encode()),
            "incomplete": {},
            "source_errors": {},
            "sources": {
                "arxiv": {
                    "access_mode": "local-arxiv-metadata-parquet",
                    "independent_discovery": True,
                    "total_records": audit["selected_records"],
                    "records_are_selected_cohort": True,
                    "category_scope": "all categories; exact controlled title/abstract predicate",
                    "crosslist_completeness": "not_applicable_to_text_selected_cohort",
                    "field_coverage": "complete_within_pinned_inventory_and_exact_predicate",
                    "historical_text_scope": "current_metadata_not_recovered_versions",
                    "dataset_revision": audit["revision"],
                    "upstream_inventory_sha256": audit["inventory_sha256"],
                    "adapter_schema": "librarian-bots-arxiv-metadata-snapshot",
                    "selection": {
                        "controlled_search_plan_sha256": plan_hash,
                        "included_terms": mission["controlled_search_plan"]["included_terms"],
                        "exclusions": mission["controlled_search_plan"].get("exclusions", []),
                        "matching_version": mission["controlled_search_plan"].get(
                            "matching_version", "literal-phrase-0.4.6"),
                        "date_from_inclusive": mission["controlled_search_plan"]["date_from"],
                        "as_of_date_exclusive": mission["controlled_search_plan"]["as_of_date"],
                        "selection_engine": audit["selection_engine"],
                        "thematic_cache_not_used_reason": audit.get(
                            "thematic_cache_not_used_reason"
                        ),
                        "cache_manifest_sha256": audit.get("cache_manifest_sha256"),
                        "target_pack_manifest_sha256": audit.get(
                            "target_pack_manifest_sha256"
                        ),
                        "selected_target_packs": audit.get(
                            "selected_target_packs", []
                        ),
                        "selection_predicate_reapplied": audit.get(
                            "selection_predicate_reapplied", False
                        ),
                    },
                    "files": [{
                        "file": selected_path.name,
                        "records": audit["selected_records"],
                        "sha256": audit["output_sha256"],
                        "http_status": None,
                        "url": None,
                    }],
                }
            },
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, output)
        return {"package": str(output), "manifest": manifest, "audit": audit}
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def read_query_payload(query_version_id: str) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT payload FROM query_version WHERE query_version_id=%s",
                    (query_version_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError("Query version not found")
    return row[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a complete pinned local-arXiv collection")
    sub = parser.add_subparsers(dest="command", required=True)
    profile = sub.add_parser("profile")
    profile.add_argument("--base", required=True)
    profile.add_argument("--query-version", required=True)
    profile.add_argument("--reviewed-by", required=True)
    profile.add_argument("--openalex-count", required=True, type=int)
    profile.add_argument("--observed-at", required=True)
    profile.add_argument("--output", required=True, type=Path)
    build = sub.add_parser("build")
    build.add_argument("mission", type=Path)
    build.add_argument("mirror", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--max-records", type=int)
    args = parser.parse_args()
    if args.command == "profile":
        derived = derive_arxiv_profile(
            read_query_payload(args.base), args.query_version, args.reviewed_by,
            {"source_reported_count": args.openalex_count,
             "observed_at": args.observed_at,
             "query_role": "size_estimate_only_no_collection",
             "reason_omitted": "full raw collection would exceed the approved 1 GiB stage budget; no API key configured"},
        )
        if args.output.exists():
            raise ValueError("Output mission exists; immutable version not overwritten")
        args.output.write_text(canonical_json(derived), encoding="utf-8")
        print(json.dumps({"output": str(args.output), "sha256": digest(derived)},
                         ensure_ascii=False))
        return 0
    result = build_package(
        args.mission, args.mirror, args.output, max_records=args.max_records
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
