"""Compare Claude's bounded HF JSONL with current local arXiv inventories."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

from saia.hybrid import digest
from saia.source_registry import file_sha256


VERSION = "hf-snapshot-comparison-0.4.13"
TEXT_AUDIT_VERSION = "hf-snapshot-comparison-0.4.23-text-audit"


def normalize_arxiv_id(value: str) -> str:
    clean = value.strip().lower()
    if clean.startswith("arxiv:"):
        clean = clean[6:]
    if "v" in clean and clean.rsplit("v", 1)[1].isdigit():
        clean = clean.rsplit("v", 1)[0]
    if not clean or any(char.isspace() for char in clean):
        raise ValueError("Invalid arXiv ID.")
    return clean


def version_created_day(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid arXiv versions.created value.") from exc


def normalized_text_sha256(title: object, abstract: object) -> str:
    normalized_title = " ".join(str(title or "").split())
    normalized_abstract = " ".join(str(abstract or "").split())
    return hashlib.sha256(
        f"{normalized_title}\n{normalized_abstract}".encode("utf-8")
    ).hexdigest()


def read_hf_jsonl(path: Path) -> tuple[dict[str, dict], dict]:
    records = {}
    duplicate_ids = []
    categories = Counter()
    created_dates = []
    updated_dates = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
                identifier = normalize_arxiv_id(str(row["id"]))
                created = date.fromisoformat(row["created"])
                updated = date.fromisoformat(row["updated"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid HF JSONL row {line_number}.") from exc
            if identifier in records:
                duplicate_ids.append(identifier)
                continue
            row_categories = row.get("categories") or []
            if not isinstance(row_categories, list):
                raise ValueError(f"Invalid categories in HF JSONL row {line_number}.")
            categories.update(str(value) for value in row_categories)
            created_dates.append(created)
            updated_dates.append(updated)
            records[identifier] = {
                "created": created.isoformat(),
                "updated": updated.isoformat(),
                "categories": sorted(str(value) for value in row_categories),
                "text_sha256": normalized_text_sha256(
                    row.get("title"), row.get("summary")
                ),
            }
    inventory = {
        "file_name": path.name,
        "file_bytes_sha256": file_sha256(path),
        "valid_rows": len(records),
        "duplicate_ids": sorted(set(duplicate_ids)),
        "created_range": [min(created_dates).isoformat(), max(created_dates).isoformat()] if created_dates else None,
        "updated_range": [min(updated_dates).isoformat(), max(updated_dates).isoformat()] if updated_dates else None,
        "category_counts": dict(sorted(categories.items())),
    }
    return records, inventory


def parquet_id_inventory(paths: list[Path], target_ids: set[str],
                         include_dates: bool = False,
                         include_text: bool = False) -> dict[str, dict]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - optional bulk extra
        raise RuntimeError("pyarrow is required for local parquet comparison.") from exc
    found = {}
    columns = ["id"]
    if include_dates:
        columns.append("versions")
    if include_text:
        columns.extend(["title", "abstract"])
    for path in paths:
        parquet_file = parquet.ParquetFile(path)
        for batch in parquet_file.iter_batches(columns=columns, batch_size=32768):
            values = batch.to_pylist()
            for row in values:
                identifier = normalize_arxiv_id(row["id"])
                if identifier not in target_ids:
                    continue
                item = {"file": path.name}
                if include_dates:
                    versions = row.get("versions") or []
                    item["first_version_date"] = (
                        version_created_day(versions[0].get("created")) if versions else None
                    )
                    item["last_version_date"] = (
                        version_created_day(versions[-1].get("created")) if versions else None
                    )
                if include_text:
                    item["text_sha256"] = normalized_text_sha256(
                        row.get("title"), row.get("abstract")
                    )
                found[identifier] = item
    return found


def compare(hf_records: dict[str, dict], hf_inventory: dict,
            full_mirror_found: dict[str, dict], selected_found: dict[str, dict],
            mirror_revision: str, selected_collection: str,
            compare_text: bool = False) -> dict:
    ids = set(hf_records)
    full_ids, selected_ids = set(full_mirror_found), set(selected_found)
    changed_dates = []
    for identifier in sorted(ids & full_ids):
        current = full_mirror_found[identifier]
        first_day = current.get("first_version_date")
        last_day = current.get("last_version_date")
        if (first_day and first_day != hf_records[identifier]["created"]) or (
                last_day and last_day != hf_records[identifier]["updated"]):
            changed_dates.append(identifier)
    new_vs_mirror = sorted(ids - full_ids)
    text_changed = []
    text_unavailable = []
    if compare_text:
        for identifier in sorted(ids & full_ids):
            mirror_hash = full_mirror_found[identifier].get("text_sha256")
            hf_hash = hf_records[identifier].get("text_sha256")
            if not mirror_hash or not hf_hash:
                text_unavailable.append(identifier)
            elif mirror_hash != hf_hash:
                text_changed.append(identifier)
    result = {
        "version": TEXT_AUDIT_VERSION if compare_text else VERSION,
        "hf_inventory": hf_inventory,
        "comparison": {
            "full_mirror_revision": mirror_revision,
            "full_mirror_matches": len(ids & full_ids),
            "new_vs_full_mirror": new_vs_mirror,
            "selected_collection": selected_collection,
            "selected_collection_matches": len(ids & selected_ids),
            "outside_selected_collection": len(ids - selected_ids),
            "date_or_version_metadata_changed": changed_dates,
        },
        "potential_coverage": {
            "new_records_vs_full_mirror": len(new_vs_mirror),
            "outside_active_selected_collection": len(ids - selected_ids),
            "outside_selection_is_incremental_corpus_evidence": False,
            "reason": (
                "The active collection has different period and topic constraints; "
                "absence from it does not make an ID new to the local mirror."
            ),
        },
        "decision": {
            "records_imported": 0,
            "full_revectorization_started": False,
            "incremental_value_proven": bool(new_vs_mirror),
            "recommendation": (
                "review_missing_ids_before_any_import" if new_vs_mirror
                else "do_not_import_duplicate_snapshot"
            ),
        },
        "limitations": [
            "Absence from the active selected collection is expected when periods or topic scopes differ.",
            "The comparison does not measure weak-signal detection quality.",
        ],
    }
    if compare_text:
        result["comparison"]["title_abstract_text_changed"] = text_changed
        result["comparison"]["title_abstract_text_unavailable"] = text_unavailable
        result["comparison"]["text_normalization"] = (
            "collapse_whitespace_then_sha256_utf8_title_newline_abstract"
        )
        result["limitations"].append(
            "Text equality covers the current title and abstract snapshot, not historical v1 text."
        )
    else:
        result["limitations"].insert(
            0, "Date comparison is metadata-level and does not prove text-version equality."
        )
    result["report_payload_sha256"] = digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hf-jsonl", type=Path, required=True)
    parser.add_argument("--mirror-dir", type=Path, required=True)
    parser.add_argument("--mirror-revision", required=True)
    parser.add_argument("--selected-dir", type=Path, required=True)
    parser.add_argument("--selected-collection", required=True)
    parser.add_argument("--compare-text", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Comparison report immutable: use a new output path.")
    hf_records, inventory = read_hf_jsonl(args.hf_jsonl)
    target_ids = set(hf_records)
    mirror_paths = sorted(args.mirror_dir.glob("*.parquet"))
    selected_paths = sorted(args.selected_dir.glob("*.parquet"))
    if not mirror_paths or not selected_paths:
        parser.error("Both full mirror and selected parquet inventories are required.")
    full = parquet_id_inventory(
        mirror_paths,
        target_ids,
        include_dates=True,
        include_text=args.compare_text,
    )
    selected = parquet_id_inventory(selected_paths, target_ids)
    result = compare(hf_inventory=inventory, hf_records=hf_records,
                     full_mirror_found=full, selected_found=selected,
                     mirror_revision=args.mirror_revision,
                     selected_collection=args.selected_collection,
                     compare_text=args.compare_text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    comparison_summary = {
        "full_mirror_revision": result["comparison"]["full_mirror_revision"],
        "full_mirror_matches": result["comparison"]["full_mirror_matches"],
        "new_vs_full_mirror_count": len(result["comparison"]["new_vs_full_mirror"]),
        "selected_collection_matches": result["comparison"]["selected_collection_matches"],
        "date_or_version_metadata_changed_count": len(
            result["comparison"]["date_or_version_metadata_changed"]
        ),
    }
    if args.compare_text:
        comparison_summary["title_abstract_text_changed_count"] = len(
            result["comparison"]["title_abstract_text_changed"]
        )
        comparison_summary["title_abstract_text_unavailable_count"] = len(
            result["comparison"]["title_abstract_text_unavailable"]
        )
    print(json.dumps({"output": str(args.output), "comparison": comparison_summary,
                      "decision": result["decision"],
                      "sha256": result["report_payload_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
