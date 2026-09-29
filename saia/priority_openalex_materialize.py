"""Materialize only integrity-checked, query-complete saved OpenAlex pages.

These query cohorts are reusable metadata, not whole-field denominators or
weak-signal labels. Source pages stay untouched and are recorded by hash.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from saia.controlled_collection import sha256_file
from saia.normalize import clean_doi, restore_abstract
from saia.priority_arxiv_full import MIN_FREE_BYTES
from saia.priority_source_inventory import VERSION as INVENTORY_VERSION, inventory_one


VERSION = "priority-openalex-complete-cohorts-v2"
MAX_OUTPUT_BYTES = 512 * 1024 * 1024


def _work(raw: dict, mission_id: str, page: str, page_hash: str) -> dict:
    identifier = raw.get("id")
    if not isinstance(identifier, str) or not identifier.startswith("https://openalex.org/W"):
        raise ValueError("Invalid OpenAlex work ID")
    published = raw.get("publication_date")
    if not isinstance(published, str):
        raise ValueError("Missing OpenAlex publication_date")
    date.fromisoformat(published)
    authorships = raw.get("authorships") or []
    authors = [item["author"]["display_name"] for item in authorships
               if isinstance(item, dict) and isinstance(item.get("author"), dict)
               and item["author"].get("display_name")]
    institutions = sorted({institution["id"] for item in authorships
                           if isinstance(item, dict)
                           for institution in (item.get("institutions") or [])
                           if isinstance(institution, dict) and institution.get("id")})
    primary_topic = raw.get("primary_topic") or {}
    return {
        "openalex_id": identifier.rsplit("/", 1)[-1],
        "openalex_url": identifier,
        "doi": clean_doi(raw.get("doi")),
        "title": raw.get("title") or raw.get("display_name") or "",
        "abstract": restore_abstract(raw.get("abstract_inverted_index")),
        "publication_date": published,
        "openalex_created_date": raw.get("created_date"),
        "openalex_updated_date": raw.get("updated_date"),
        "language": raw.get("language"),
        "document_type": raw.get("type"),
        "authors": authors,
        "institution_ids": institutions,
        "primary_topic_id": primary_topic.get("id") if isinstance(primary_topic, dict) else None,
        "cited_by_count_at_snapshot": raw.get("cited_by_count"),
        "is_retracted": raw.get("is_retracted"),
        "source_mission_id": mission_id,
        "source_mission_ids": [mission_id],
        "source_page": page,
        "source_page_sha256": page_hash,
    }


def materialize(*, inventory_path: Path, raw_root: Path, output_dir: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if output_dir.exists():
        raise FileExistsError("OpenAlex materialization output is immutable")
    if shutil.disk_usage(output_dir.parent).free < MIN_FREE_BYTES:
        raise OSError("Insufficient free disk reserve")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != INVENTORY_VERSION:
        raise ValueError("Unknown source inventory version")
    verified = [row for row in inventory["snapshots"]
                if row["reuse_status"] == "verified_query_complete"]
    if not verified:
        raise ValueError("No query-complete OpenAlex snapshots")
    documents: dict[str, dict] = {}
    cohorts = []
    failures = []
    for row in verified:
        source = Path(row["directory"]).resolve()
        if not source.is_relative_to(raw_root.resolve()):
            raise ValueError("Inventory points outside the allowed raw root")
        fresh = inventory_one(source)
        if (fresh is None or fresh["reuse_status"] != "verified_query_complete"
                or fresh["manifest_sha256"] != row["manifest_sha256"]
                or fresh["records_in_saved_pages"] != row["records_in_saved_pages"]):
            raise ValueError("Saved OpenAlex snapshot no longer matches audited inventory")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["sources"]["openalex"]["files"]
        count = 0
        duplicate_ids = 0
        for entry in entries:
            page_name = entry["file"]
            payload = json.loads((source / "openalex" / page_name).read_text(encoding="utf-8"))
            results = payload.get("results")
            if not isinstance(results, list) or len(results) != entry["records"]:
                raise ValueError("Saved OpenAlex page count differs from manifest")
            for raw in results:
                count += 1
                try:
                    work = _work(raw, row["mission_id"], page_name, entry["sha256"])
                except (TypeError, ValueError) as error:
                    failures.append({"mission_id": row["mission_id"], "page": page_name,
                                     "openalex_id": raw.get("id") if isinstance(raw, dict) else None,
                                     "reason": str(error)})
                    continue
                identifier = work["openalex_id"]
                if identifier in documents:
                    duplicate_ids += 1
                    memberships = documents[identifier]["source_mission_ids"]
                    if row["mission_id"] not in memberships:
                        memberships.append(row["mission_id"])
                    continue
                documents[identifier] = work
        if count != row["records_in_saved_pages"]:
            raise ValueError("Complete cohort row count mismatch")
        cohorts.append({"mission_id": row["mission_id"],
                        "priority_catalog_area_id": row.get("priority_catalog_area_id"),
                        "query_terms": row["query_terms"], "period": row["period"],
                        "source_manifest_sha256": row["manifest_sha256"],
                        "source_record_count": count, "duplicate_openalex_ids": duplicate_ids,
                        "source_reported_total_records": row["source_reported_total_records"],
                        "fetch_finished_utc": row["fetch_finished_utc"],
                        "search_scope": "OpenAlex query-specific cursor-complete; not field-wide",
                        "source_api": row["source_api"],
                        "source_access_mode": row["access_mode"],
                        "source_errors": row["source_errors"],
                        "excluded_invalid_rows": 0})
    if failures:
        raise ValueError(f"Invalid saved OpenAlex works; first examples: {failures[:3]}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-", dir=output_dir.parent) as name:
        temp = Path(name)
        path = temp / "works.parquet"
        pq.write_table(pa.Table.from_pylist([documents[key] for key in sorted(documents)]),
                       path, compression="zstd")
        if path.stat().st_size > MAX_OUTPUT_BYTES or shutil.disk_usage(temp).free < MIN_FREE_BYTES:
            raise OSError("OpenAlex materialization exceeded size cap or reserve")
        table = pq.read_table(path, columns=["openalex_id", "source_mission_id"])
        if table.num_rows != len(documents):
            raise ValueError("OpenAlex output row count mismatch")
        stats = Counter(work["source_mission_id"] for work in documents.values())
        memberships = Counter(mission for work in documents.values()
                              for mission in work["source_mission_ids"])
        result = {
            "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "input_inventory_sha256": sha256_file(inventory_path),
            "cohorts": cohorts,
            "counts": {"source_records": sum(row["source_record_count"] for row in cohorts),
                       "unique_openalex_works": len(documents),
                       "duplicate_openalex_id_rows": sum(row["duplicate_openalex_ids"]
                                                          for row in cohorts),
                       "excluded_invalid_rows": 0,
                       "with_abstract": sum(bool(work["abstract"]) for work in documents.values()),
                       "by_source_mission": dict(sorted(stats.items())),
                       "by_source_mission_memberships": dict(sorted(memberships.items()))},
            "file": {"name": path.name, "bytes": path.stat().st_size,
                     "sha256": sha256_file(path)},
            "policy": {"weak_signal_labels": False, "relevance_labels": False,
                       "denominator_for_technology_area": False,
                       "citation_count_is_current_snapshot_not_historical_series": True,
                       "only_verified_query_complete_saved_pages": True,
                       "full_text_included": False},
            "rights": {
                "openalex_metadata": "CC0; https://help.openalex.org/data/how-its-built/",
                "linked_pdfs": "not included; their original copyright remains with rights holders",
                "fulltext_reference": "https://help.openalex.org/access/fulltext/",
            },
            "limits": {"max_output_bytes": MAX_OUTPUT_BYTES,
                       "min_free_disk_reserve_bytes": MIN_FREE_BYTES,
                       "historical_text": "current OpenAlex metadata, not text as at publication date"},
        }
        (temp / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return result
