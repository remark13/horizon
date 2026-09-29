"""Read-only inventory of reusable local OpenAlex snapshots.

An old query-specific cache is neither a field denominator nor a proof of
coverage of any of the 89 search areas or 100 customer examples.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from saia.priority_arxiv_pilot import _sha


VERSION = "priority-existing-openalex-inventory-v2"


def inventory_one(directory: Path) -> dict | None:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = (manifest.get("sources") or {}).get("openalex")
    if not isinstance(source, dict):
        return None
    pages = source.get("files") or []
    source_root = directory / "openalex"
    checked = []
    for page in pages:
        name = page.get("file")
        safe_name = isinstance(name, str) and Path(name).name == name and name not in {".", ".."}
        path = source_root / name if safe_name else source_root
        recorded_hash = page.get("sha256")
        checked.append({"file": name, "exists": path.is_file(),
                        "sha256_matches_manifest": bool(
                            path.is_file() and recorded_hash and _sha(path) == recorded_hash),
                        "records": page.get("records"),
                        "bytes": path.stat().st_size if path.is_file() else None})
    mission_name = str(manifest.get("mission_snapshot_file") or "mission.json")
    if Path(mission_name).name != mission_name or mission_name in {".", ".."}:
        raise ValueError("Unsafe mission snapshot filename in manifest")
    mission_path = directory / mission_name
    mission = json.loads(mission_path.read_text(encoding="utf-8")) if mission_path.is_file() else {}
    protocol = mission.get("protocol") or {}
    area_id = protocol.get("priority_catalog_area_id") if isinstance(protocol, dict) else None
    if area_id is not None and (not isinstance(area_id, str)
                                or not re.fullmatch(r"national-area-\d{3}", area_id)):
        raise ValueError("Invalid priority catalog area ID in mission")
    mission_hash_matches = bool(mission_path.is_file() and manifest.get("mission_file_sha256")
                                and _sha(mission_path) == manifest["mission_file_sha256"])
    records_from_pages = sum(page["records"] for page in checked
                             if isinstance(page["records"], int))
    total_records = source.get("total_records")
    page_count_complete = len(checked) == len(pages) and bool(checked)
    integrity_ok = (page_count_complete and all(page["sha256_matches_manifest"] for page in checked)
                    and mission_hash_matches)
    count_reconciled = isinstance(total_records, int) and total_records == records_from_pages
    errors = manifest.get("source_errors") or []
    last_page = pages[-1] if pages else {}
    cursor_complete = (source.get("access_mode") == "cursor-paged-query"
                       and last_page.get("cursor_audit_complete") is True
                       and last_page.get("cursor_next") is None)
    declared_not_incomplete = manifest.get("incomplete") in (None, False, {})
    complete = (integrity_ok and count_reconciled and declared_not_incomplete
                and cursor_complete and not errors)
    reuse_status = ("verified_query_complete" if complete else
                    "verified_saved_pages_scope_unproven" if integrity_ok else
                    "needs_integrity_review")
    return {
        "directory": str(directory), "mission_id": manifest.get("mission_id"),
        "query_version": manifest.get("query_version"),
        "manifest_sha256": _sha(manifest_path),
        "fetch_started_utc": manifest.get("fetch_started_utc"),
        "fetch_finished_utc": manifest.get("fetch_finished_utc"),
        "source_api": source.get("api"), "access_mode": source.get("access_mode"),
        "record_shape": source.get("record_shape"),
        "source_reported_total_records": total_records,
        "pages": len(checked), "records_in_saved_pages": records_from_pages,
        "bytes_in_saved_pages": sum(page["bytes"] or 0 for page in checked),
        "period": mission.get("period"),
        "priority_catalog_area_id": area_id,
        "query_terms": (mission.get("query") or {}).get("terms"),
        "mission_file_sha256_matches": mission_hash_matches,
        "page_hashes_match": all(page["sha256_matches_manifest"] for page in checked),
        "saved_page_counts_reconcile": count_reconciled,
        "cursor_audit_complete": cursor_complete,
        "manifest_incomplete": manifest.get("incomplete"),
        "source_errors": errors,
        "reuse_status": reuse_status,
        "scope_warning": "Query-specific saved pages; not a full OpenAlex field or denominator.",
        "customer_signal_label": False,
    }


def build(root: Path) -> dict:
    rows = [result for directory in sorted(root.iterdir()) if directory.is_dir()
            if (result := inventory_one(directory)) is not None]
    return {
        "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root), "snapshots": rows,
        "counts": {"snapshots": len(rows),
                   "verified_query_complete": sum(
                       row["reuse_status"] == "verified_query_complete" for row in rows),
                   "verified_pages_scope_unproven": sum(
                       row["reuse_status"] == "verified_saved_pages_scope_unproven" for row in rows),
                   "saved_page_bytes_not_unique_corpus_bytes": sum(
                       row["bytes_in_saved_pages"] for row in rows)},
        "policy": {"automatically_map_to_priority_areas": False,
                   "automatically_label_weak_signals": False,
                   "sum_records_as_unique_works": False,
                   "openalex_complete_field_coverage_proven": False},
    }
