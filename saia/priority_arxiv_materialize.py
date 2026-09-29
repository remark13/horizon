"""Immutable, bounded metadata corpus for the frozen 16-case retrieval pilot.

No case assignment here is a weak-signal or relevance label.  This is a
reusable diagnostic corpus, not a production search index or field series.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from saia.controlled_collection import inventory_sha256, sha256_file
from saia.local_arxiv_search import policy, validate_inventory
from saia.priority_arxiv_pilot import _boundary_pattern, _sha, _validated_cases
from saia.priority_arxiv_full import MIN_FREE_BYTES


VERSION = "priority-arxiv-pilot-corpus-v1"
MAX_CORPUS_BYTES = 512 * 1024 ** 2


def build(*, catalog_path: Path, cases_path: Path, mirror_dir: Path,
          full_report_path: Path, output_dir: Path) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from saia.arxiv_metadata import first_submission

    if output_dir.exists():
        raise FileExistsError("Pilot corpus is immutable; choose a new output directory")
    if shutil.disk_usage(output_dir.parent).free < MIN_FREE_BYTES:
        raise OSError("Insufficient free disk reserve")
    report = json.loads(full_report_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    config = json.loads(cases_path.read_text(encoding="utf-8"))
    if report["input_fingerprints"]["catalog_sha256"] != _sha(catalog_path) or (
            report["input_fingerprints"]["case_config_sha256"] != _sha(cases_path)):
        raise ValueError("Pilot catalog or exact terms differ from frozen full-scan report")
    if config.get("version") != "priority-arxiv-pilot-v2":
        raise ValueError("Only frozen word-boundary v2 terms are supported")
    cases = _validated_cases(config, catalog)
    cfg = policy()
    files = validate_inventory(str(mirror_dir.resolve()), cfg["expected_files"], cfg["expected_rows"])
    inventory_hash = inventory_sha256(files, cfg["expected_rows"], cfg)
    if inventory_hash != report["input_fingerprints"]["arxiv_inventory_sha256"]:
        raise ValueError("Local arXiv mirror differs from frozen full-scan report")
    cutoff = date.fromisoformat(report["period"]["as_of_exclusive"])
    start = date.fromisoformat(report["period"]["from"])
    expected_counts = {row["catalog_id"]: row["eligible_unique_in_pinned_mirror"]
                       for row in report["cases"]}
    if set(expected_counts) != {case["id"] for case in cases}:
        raise ValueError("Full-scan report case IDs differ from configuration")
    patterns = {case["id"]: [_boundary_pattern(term) for term in case["all_terms"]]
                for case in cases}
    doc_schema = pa.schema([
        ("arxiv_id", pa.string()), ("first_submission_date", pa.string()),
        ("title_current", pa.string()), ("abstract_current", pa.string()),
        ("authors_current", pa.string()), ("doi_current", pa.string()),
        ("categories_current", pa.string()), ("license_current", pa.string()),
        ("snapshot_update_date", pa.string()), ("source_shard", pa.string()),
    ])
    assignment_schema = pa.schema([("arxiv_id", pa.string()), ("catalog_id", pa.string())])
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-", dir=output_dir.parent) as temp_name:
        temp = Path(temp_name)
        doc_path = temp / "documents.parquet"
        assignments_path = temp / "assignments.parquet"
        seen: dict[str, tuple[str, str]] = {}
        assigned: set[tuple[str, str]] = set()
        counts: Counter[str] = Counter()
        scanned = outside_period = invalid_date = duplicate_document_rows = 0
        doc_writer = pq.ParquetWriter(doc_path, doc_schema, compression="zstd")
        assignment_writer = pq.ParquetWriter(assignments_path, assignment_schema,
                                             compression="zstd")
        try:
            columns = ["id", "title", "abstract", "versions", "authors", "doi",
                       "categories", "license", "update_date"]
            for path in files:
                parquet = pq.ParquetFile(path)
                for batch in parquet.iter_batches(batch_size=8192, columns=columns):
                    scanned += batch.num_rows
                    title, abstract = batch.column(1), batch.column(2)
                    masks = {}
                    memberships: dict[int, list[str]] = {}
                    for case in cases:
                        mask = None
                        for pattern in patterns[case["id"]]:
                            if pattern not in masks:
                                masks[pattern] = pc.or_(
                                    pc.fill_null(pc.match_substring_regex(
                                        title, pattern, ignore_case=True), False),
                                    pc.fill_null(pc.match_substring_regex(
                                        abstract, pattern, ignore_case=True), False),
                                )
                            mask = masks[pattern] if mask is None else pc.and_(mask, masks[pattern])
                        for index in pc.indices_nonzero(mask).to_pylist():
                            memberships.setdefault(index, []).append(case["id"])
                    if not memberships:
                        continue
                    docs, assignments = [], []
                    for index in sorted(memberships):
                        row = batch.slice(index, 1).to_pylist()[0]
                        identifier = str(row.get("id") or "")
                        try:
                            first_date = first_submission(row.get("versions"))
                            published = date.fromisoformat(first_date)
                        except (TypeError, ValueError, IndexError):
                            invalid_date += 1
                            continue
                        if not start <= published < cutoff:
                            outside_period += 1
                            continue
                        identity = (str(row.get("title") or ""),
                                    str(row.get("abstract") or ""))
                        if identifier in seen:
                            duplicate_document_rows += 1
                            if seen[identifier] != identity:
                                raise ValueError(f"Conflicting arXiv metadata for {identifier}")
                        else:
                            seen[identifier] = identity
                            update = row.get("update_date")
                            docs.append({
                                "arxiv_id": identifier, "first_submission_date": first_date,
                                "title_current": row.get("title"),
                                "abstract_current": row.get("abstract"),
                                "authors_current": row.get("authors"),
                                "doi_current": row.get("doi"),
                                "categories_current": row.get("categories"),
                                "license_current": row.get("license"),
                                "snapshot_update_date": update.isoformat() if update else None,
                                "source_shard": path.name,
                            })
                        for case_id in memberships[index]:
                            pair = (identifier, case_id)
                            if pair not in assigned:
                                assigned.add(pair)
                                counts[case_id] += 1
                                assignments.append({"arxiv_id": identifier, "catalog_id": case_id})
                    if docs:
                        doc_writer.write_table(pa.Table.from_pylist(docs, schema=doc_schema))
                    if assignments:
                        assignment_writer.write_table(pa.Table.from_pylist(
                            assignments, schema=assignment_schema))
        finally:
            doc_writer.close()
            assignment_writer.close()
        if scanned != cfg["expected_rows"] or dict(counts) != {key: value for key, value in expected_counts.items()
                                                       if value > 0}:
            raise ValueError("Materialized memberships differ from frozen full scan")
        file_metadata = {path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
                         for path in (doc_path, assignments_path)}
        total_bytes = sum(value["bytes"] for value in file_metadata.values())
        if total_bytes > MAX_CORPUS_BYTES or shutil.disk_usage(temp).free < MIN_FREE_BYTES:
            raise OSError("Pilot corpus exceeded size cap or free disk reserve")
        manifest = {
            "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {"dataset": cfg["dataset"], "revision": cfg["revision"],
                       "inventory_sha256": inventory_hash,
                       "full_scan_report_sha256": _sha(full_report_path),
                       "full_scan_report": str(full_report_path)},
            "inputs": {"catalog_sha256": _sha(catalog_path),
                       "case_config_sha256": _sha(cases_path)},
            "period": report["period"], "scanned_mirror_rows": scanned,
            "eligible_unique_documents": len(seen), "case_assignments": len(assigned),
            "assignment_counts": dict(sorted(counts.items())),
            "excluded": {"invalid_date_rows_matching_any_case": invalid_date,
                         "outside_period_rows_matching_any_case": outside_period,
                         "duplicate_document_rows": duplicate_document_rows},
            "files": file_metadata, "max_corpus_bytes": MAX_CORPUS_BYTES,
            "min_free_disk_reserve_bytes": MIN_FREE_BYTES,
            "rights": {"metadata": "arXiv CC0; https://info.arxiv.org/help/license/index.html",
                       "full_text_pdfs_included": False},
            "interpretation": {
                "case_memberships_are_relevance_labels": False,
                "case_memberships_are_weak_signal_labels": False,
                "field_denominator_available": False,
                "historical_title_abstract_frozen_at_first_submission": False,
                "all_arxiv_coverage_proven": False,
                "source_year_counts_are_comparable_to_openalex": False,
            },
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return manifest
