"""Extract a frozen mission subset from an existing arXiv mirror, offline.

No changes to the source cache, no model computation, and no database writes.
The output is a normal verified SAIA collection package for saia.ingest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from saia.arxiv_metadata import (ADAPTER_VERSION, matches_text_scope, selected, selection,
                                 text_scope, to_record, validate_schema)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def inventory(source: Path, mission: dict) -> list[dict]:
    import pyarrow.parquet as pq

    policy = mission["query"]["arxiv_local_snapshot"]
    if not re.fullmatch(r"[0-9a-f]{40}", str(policy.get("revision", ""))):
        raise ValueError("A pinned upstream revision is required")
    if source.name != "data" or source.parent.name != policy["revision"]:
        raise ValueError("Source path does not match the pinned snapshot revision/data")
    files = sorted(source.glob("*.parquet"))
    count = policy.get("expected_files")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0 or len(files) != count:
        raise ValueError("Incomplete or unexpected local snapshot file set")
    expected = [f"train-{index:05d}-of-{count:05d}.parquet" for index in range(count)]
    if [path.name for path in files] != expected:
        raise ValueError("Missing or unexpected snapshot shards")
    result = []
    for path in files:
        parquet = pq.ParquetFile(path)
        validate_schema(parquet.schema_arrow.names)
        result.append({"file": path.name, "rows": parquet.metadata.num_rows,
                       "bytes": path.stat().st_size, "sha256": digest(path)})
    rows = policy.get("expected_rows")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0 or sum(item["rows"] for item in result) != rows:
        raise ValueError("Snapshot row count differs from the pinned inventory")
    return result


def validate_quarantine(root: Path, manifest: dict, mission: dict) -> None:
    """Verify excluded evidence before ingest; never import it as publications."""
    block = manifest["sources"]["arxiv"]
    audit = block.get("record_quarantine")
    policy = mission.get("query", {}).get("arxiv_local_snapshot", {})
    if policy.get("record_error_policy") != "quarantine" or not isinstance(audit, dict):
        raise ValueError("Record quarantine does not match the frozen mission")
    if (audit.get("schema") != "arxiv-record-quarantine-0.4.4"
            or audit.get("record_error_policy") != "quarantine"
            or audit.get("max_quarantined_records") != policy.get("max_quarantined_records", 100)):
        raise ValueError("Invalid record quarantine policy/schema")
    if audit.get("file") != "record-quarantine.jsonl":
        raise ValueError("Invalid record quarantine path")
    path = root / audit["file"]
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or digest(path) != audit.get("sha256"):
        raise ValueError("Record quarantine missing, outside package, or checksum mismatch")
    upstream = {item["file"]: item for item in block.get("upstream_inventory", [])}
    seen = set()
    total = known = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            rejected = json.loads(line)
            location = (rejected.get("upstream_file"), rejected.get("upstream_row_zero_based"))
            shard = upstream.get(location[0])
            if (not shard or not isinstance(location[1], int) or isinstance(location[1], bool)
                    or not 0 <= location[1] < shard["rows"] or location in seen
                    or rejected.get("upstream_sha256") != shard["sha256"]
                    or rejected.get("dataset_revision") != policy.get("revision")):
                raise ValueError("Invalid record quarantine provenance")
            seen.add(location)
            raw = rejected.get("raw_metadata")
            if not isinstance(raw, dict):
                raise ValueError("Missing quarantined raw metadata")
            raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            if hashlib.sha256(raw_json.encode()).hexdigest() != rejected.get("raw_metadata_sha256"):
                raise ValueError("Quarantined metadata checksum mismatch")
            if rejected.get("arxiv_id") != raw.get("id"):
                raise ValueError("Quarantined identifier mismatch")
            membership = None
            try:
                membership = selected(raw, mission)
                if membership:
                    to_record(raw, filename=location[0], revision=policy["revision"])
            except ValueError as error:
                expected_scope = "selected" if membership is True else "unknown_period"
                if rejected.get("scope_membership") != expected_scope or rejected.get("reason") != str(error):
                    raise ValueError("Quarantine reason/scope mismatch") from error
            else:
                raise ValueError("Valid or out-of-scope record cannot be quarantined")
            total += 1
            known += int(membership is True)
            if total > policy.get("max_quarantined_records", 100):
                raise ValueError("Record quarantine limit exceeded")
    counts = {"records": total, "known_selected_records": known, "unknown_period_records": total - known}
    if any(type(audit.get(key)) is not int or audit[key] != value for key, value in counts.items()):
        raise ValueError("Record quarantine counters mismatch")
    local = manifest.get("local_audit", {})
    keys = ("examined_category_records", "selected_records", "excluded_by_period_records", "quarantined_records")
    scope = text_scope(mission)
    if scope is not None:
        if block.get("selection", {}).get("text_scope") != scope:
            raise ValueError("Text scope differs from the frozen mission")
        keys += ("excluded_by_text_records",)
    if any(type(local.get(key)) is not int or local[key] < 0 for key in keys):
        raise ValueError("Invalid local record audit counters")
    if (local["quarantined_records"] != total
            or local["selected_records"] != block.get("total_records")
            or local["selected_records"] != sum(item["records"] for item in block["files"])
            or local["examined_category_records"] != sum(local[key] for key in keys[1:])):
        raise ValueError("Local record partition mismatch")
    if total and "record_validation_quarantine; scientific scope coverage not established" not in manifest.get("incomplete", {}).get("arxiv", []):
        raise ValueError("Quarantined selection cannot claim complete coverage")


def extract(source: Path, mission_path: Path, output: Path, *, max_records: int | None = None,
            min_free_gib: float = 10, verbose: bool = True) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if max_records is not None and (not isinstance(max_records, int) or isinstance(max_records, bool) or max_records <= 0):
        raise ValueError("max_records must be a positive integer")
    if not math.isfinite(min_free_gib) or min_free_gib < 0:
        raise ValueError("Negative disk reserve")
    mission_bytes = mission_path.read_bytes()
    mission = json.loads(mission_bytes)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", mission.get("mission_id", "")):
        raise ValueError("Invalid mission identity")
    if mission.get("sources") != ["arxiv"]:
        raise ValueError("Local extraction requires an arXiv-only mission")
    if mission.get("query_version") != mission["mission_id"] + "/v1":
        raise ValueError("Use a new mission/v1 for each local extraction")
    policy = mission["query"]["arxiv_local_snapshot"]
    error_policy = policy.get("record_error_policy", "abort")
    if error_policy not in ("abort", "quarantine"):
        raise ValueError("record_error_policy must be abort or quarantine")
    quarantine_limit = policy.get("max_quarantined_records", 100)
    if not isinstance(quarantine_limit, int) or isinstance(quarantine_limit, bool) or quarantine_limit <= 0:
        raise ValueError("max_quarantined_records must be a positive integer")
    wanted, start, end = selection(mission)
    scope = text_scope(mission)  # Validate configuration before creating any output.
    if not wanted or not start or not end:
        raise ValueError("Explicit categories and a bounded period are required")
    cutoff = datetime.strptime(mission["as_of_date"], "%Y-%m-%d").date().isoformat()
    if end >= cutoff:
        raise ValueError("The inclusive period end must precede as_of_date")
    source = source.resolve()
    # Resolve only the directory, not each cache symlink: the blobs are
    # external read-only inputs and are never accepted as output package links.
    output = output.resolve()
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Source and output must not overlap")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output is not empty; preserved without overwriting")
    parent = output.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < min_free_gib * 1024 ** 3:
        raise ValueError("Insufficient disk reserve for local extraction")
    frozen_inventory = inventory(source, mission)
    output.mkdir(parents=True, exist_ok=True)
    out_source = output / "arxiv"
    out_source.mkdir()
    (output / "mission.json").write_bytes(mission_bytes)
    started = datetime.now(timezone.utc).isoformat()
    total = 0
    scanned = 0
    selected_ids = set()
    entries = []
    capped = False
    after_cutoff = 0
    unresolved_authors = 0
    examined = excluded = quarantined = known_selected_errors = 0
    excluded_text = 0
    quarantine_path = output / "record-quarantine.jsonl"
    if error_policy == "quarantine":
        quarantine_path.touch(exist_ok=False)
    categories_pattern = r"(^| )(?:" + "|".join(re.escape(item) for item in sorted(wanted)) + r")( |$)"
    for item in frozen_inventory:
        path = source / item["file"]
        parquet = pq.ParquetFile(path)
        writer = None
        written = 0
        row_offset = 0
        destination = out_source / path.name
        try:
            for batch in parquet.iter_batches(batch_size=4096):
                scanned += batch.num_rows
                table = pa.Table.from_batches([batch])
                mask = pc.match_substring_regex(pc.fill_null(table["categories"], ""), categories_pattern)
                candidate_table = table.filter(mask)
                source_offsets = pc.indices_nonzero(mask).to_pylist()
                keep = []
                for index, row in enumerate(candidate_table.to_pylist()):
                    examined += 1
                    if not matches_text_scope(row, mission):
                        excluded_text += 1
                        continue
                    in_period = None
                    try:
                        in_period = selected(row, mission)
                        if in_period:
                            identifier, record = to_record(row, filename=path.name,
                                                          revision=policy["revision"])
                    except ValueError as error:
                        if error_policy == "abort":
                            raise
                        # Only record validation errors are recoverable. Inventory,
                        # duplicate identity, I/O and disk failures remain fatal.
                        if quarantined >= quarantine_limit:
                            raise ValueError("Record quarantine limit exceeded; no completed manifest") from error
                        if shutil.disk_usage(output).free < min_free_gib * 1024 ** 3:
                            raise ValueError("Disk reserve exhausted; no completed manifest") from error
                        raw_json = json.dumps(row, ensure_ascii=False, sort_keys=True,
                                              default=lambda value: value.isoformat())
                        rejected = {"arxiv_id": row.get("id"), "upstream_file": path.name,
                                    "upstream_row_zero_based": row_offset + source_offsets[index],
                                    "upstream_sha256": item["sha256"], "dataset_revision": policy["revision"],
                                    "scope_membership": "selected" if in_period is True else "unknown_period",
                                    "reason": str(error), "raw_metadata": json.loads(raw_json),
                                    "raw_metadata_sha256": hashlib.sha256(raw_json.encode()).hexdigest()}
                        with quarantine_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(rejected, ensure_ascii=False, sort_keys=True) + "\n")
                        quarantined += 1
                        known_selected_errors += int(in_period is True)
                        continue
                    if not in_period:
                        excluded += 1
                        continue
                    if identifier in selected_ids:
                        raise ValueError("Duplicate selected arXiv ID: " + identifier)
                    selected_ids.add(identifier)
                    keep.append(index)
                    total += 1
                    after_cutoff += int(record["updated"] >= cutoff and record["updated"] != record["created"])
                    unresolved_authors += int(record["_author_parse_status"] == "unresolved")
                    if max_records is not None and total >= max_records:
                        capped = True
                        break
                if keep:
                    if shutil.disk_usage(output).free < min_free_gib * 1024 ** 3:
                        raise ValueError("Disk reserve exhausted; no completed manifest will be published")
                    chosen = candidate_table.take(pa.array(keep, type=pa.int64()))
                    if writer is None:
                        writer = pq.ParquetWriter(destination, chosen.schema, compression="zstd")
                    writer.write_table(chosen)
                    written += len(keep)
                if capped:
                    break
                row_offset += batch.num_rows
        finally:
            if writer is not None:
                writer.close()
        if digest(path) != item["sha256"]:
            raise ValueError("Upstream shard changed while extracting; no completed manifest")
        if written:
            entries.append({"file": path.name, "records": written, "sha256": digest(destination),
                            "url": "https://huggingface.co/datasets/" + mission["query"]["arxiv_local_snapshot"]["dataset"]
                            + "/resolve/" + mission["query"]["arxiv_local_snapshot"]["revision"] + "/data/" + path.name,
                            "http_status": None, "upstream_sha256": item["sha256"], "upstream_rows": item["rows"]})
        if verbose:
            print(f"{path.name}: selected {written}, total {total}", flush=True)
        if capped:
            break
    if not total:
        raise ValueError("Empty selection; no completed manifest")
    # Bind the whole initial inventory, including shards outside the selected
    # period. Do not declare completion if an unvisited shard was changed.
    for item in frozen_inventory:
        if digest(source / item["file"]) != item["sha256"]:
            raise ValueError("Upstream inventory changed; no completed manifest")
    incomplete_reasons = []
    if capped:
        incomplete_reasons.append("local_record_cap_reached; adapter smoke subset, not complete corpus")
    if quarantined:
        incomplete_reasons.append("record_validation_quarantine; scientific scope coverage not established")
    quarantine = ({"file": quarantine_path.name, "sha256": digest(quarantine_path),
                   "records": quarantined, "known_selected_records": known_selected_errors,
                   "unknown_period_records": quarantined - known_selected_errors,
                   "record_error_policy": error_policy, "max_quarantined_records": quarantine_limit,
                   "schema": "arxiv-record-quarantine-0.4.4"} if error_policy == "quarantine" else None)
    manifest = {
        "mission_id": mission["mission_id"], "query_version": mission["query_version"],
        "connector_version": ("arxiv-local-export-0.4.6" if scope is not None else
                              "arxiv-local-export-0.4.4" if quarantine is not None else ADAPTER_VERSION),
        "fetch_started_utc": started,
        "mission_snapshot_file": "mission.json",
        "mission_file_sha256": hashlib.sha256(mission_bytes).hexdigest(),
        "incomplete": {"arxiv": incomplete_reasons} if incomplete_reasons else {},
        "sources": {"arxiv": {
            "api": "local_cached_huggingface_snapshot", "access_mode": "local-arxiv-metadata-parquet",
            "independent_discovery": True, "category_scope": "all_category_tokens_in_mirror_rows",
            "crosslist_completeness": "not_proven", "field_coverage": "unknown",
            "historical_text_scope": "current_metadata_not_recovered_versions",
            "dataset_revision": policy["revision"], "adapter_schema": "arxiv-metadata-oai",
            "total_records": total, "files": entries,
            **({"record_quarantine": quarantine} if quarantine is not None else {}),
            "upstream_inventory": frozen_inventory,
            "upstream_inventory_sha256": hashlib.sha256(json.dumps(frozen_inventory, sort_keys=True).encode()).hexdigest(),
            "selection": {"categories": sorted(wanted), "period": mission["period"], "date_field": "versions.v1.created_utc",
                          **({"text_scope": scope} if scope is not None else {})},
        }},
        "local_audit": {"selected_records": total, "unique_selected_ids": len(selected_ids),
                        "scanned_records": scanned, "cap_reached": capped,
                        "examined_category_records": examined, "excluded_by_period_records": excluded,
                        "quarantined_records": quarantined,
                        **({"excluded_by_text_records": excluded_text} if scope is not None else {}),
                        "examined_partition": ("selected + excluded_by_period + quarantined + excluded_by_text" if scope is not None else
                                               "selected + excluded_by_period + quarantined"),
                        "inventory_traversal_complete": not capped,
                        "selected_revised_after_cutoff": after_cutoff,
                        "selected_unresolved_authors": unresolved_authors,
                        "weak_signal_precision": None, "detection_evaluated": False,
                        "purpose": mission.get("collection_stage"),
                        "metadata_license": "CC0 according to the cached dataset card; article rights are separate"},
    }
    if quarantine is not None:
        validate_quarantine(output, manifest, mission)
    # Manifest is the completion marker. Failure before here leaves an
    # unimportable directory and never overwrites an existing completed package.
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline extraction of a frozen local arXiv mission")
    parser.add_argument("--source", type=Path, required=True, help="snapshot revision/data directory")
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--min-free-gib", type=float, default=10)
    args = parser.parse_args()
    manifest = extract(args.source, args.mission, args.out, max_records=args.max_records, min_free_gib=args.min_free_gib)
    print(json.dumps(manifest["local_audit"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
