"""Rebuildable OpenAlex venue metadata joined to the saved priority corpus.

Venue fields describe provenance, not scientific primary-result status. In
particular, OpenAlex ``type=article`` and ``source.type=journal`` can include
trade-magazine pieces; ``is_core=False`` can also include valid preprints.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from saia.controlled_collection import sha256_file
from saia.priority_arxiv_full import MIN_FREE_BYTES


VERSION = "priority-openalex-venue-metadata-v1"
INPUT_VERSION = "priority-openalex-complete-cohorts-v2"
MAX_OUTPUT_BYTES = 128 * 1024 * 1024


def _source_row(raw: dict, *, mission_id: str, page: str, page_hash: str) -> dict:
    location = raw.get("primary_location") or {}
    source = location.get("source") or {}
    return {
        "openalex_id": raw["id"].rsplit("/", 1)[-1],
        "source_mission_id": mission_id,
        "source_page": page,
        "source_page_sha256": page_hash,
        "work_type": raw.get("type"),
        "work_type_crossref": raw.get("type_crossref"),
        "primary_source_id": source.get("id"),
        "primary_source_name": source.get("display_name"),
        "primary_source_type": source.get("type"),
        "primary_source_is_core": source.get("is_core"),
        "primary_source_is_in_doaj": source.get("is_in_doaj"),
        "primary_location_raw_source_name": location.get("raw_source_name"),
        "primary_location_raw_type": location.get("raw_type"),
        "primary_location_landing_page_url": location.get("landing_page_url"),
        "scientific_primary_result_verified": None,
    }


def materialize(*, corpus_dir: Path, raw_root: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError("Venue-metadata output is immutable")
    if shutil.disk_usage(output_dir.parent).free < MIN_FREE_BYTES:
        raise OSError("Insufficient free disk reserve")
    manifest_path = corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    works_path = corpus_dir / manifest["file"]["name"]
    if (manifest.get("version") != INPUT_VERSION
            or works_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(works_path) != manifest["file"]["sha256"]):
        raise ValueError("Saved OpenAlex priority corpus differs from manifest")
    table = pq.read_table(works_path, columns=["openalex_id", "source_mission_id",
                                               "source_page", "source_page_sha256"])
    if table.num_rows != manifest["counts"]["unique_openalex_works"]:
        raise ValueError("OpenAlex work count differs from manifest")
    expected = table.to_pylist()
    pages: dict[tuple[str, str], dict[str, dict]] = {}
    page_hashes = {}
    rows = []
    for work in expected:
        mission, page = work["source_mission_id"], work["source_page"]
        path = raw_root / mission / "openalex" / page
        if not path.resolve().is_relative_to(raw_root.resolve()):
            raise ValueError("Source page escapes allowed raw root")
        key = mission, page
        if key not in pages:
            digest = sha256_file(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_rows = payload.get("results")
            if not isinstance(raw_rows, list):
                raise ValueError("Source page has no result list")
            lookup = {raw.get("id", "").rsplit("/", 1)[-1]: raw for raw in raw_rows}
            if len(lookup) != len(raw_rows):
                raise ValueError("Duplicate OpenAlex IDs in source page")
            pages[key], page_hashes[key] = lookup, digest
        if page_hashes[key] != work["source_page_sha256"]:
            raise ValueError("Source page checksum differs from saved corpus")
        raw = pages[key].get(work["openalex_id"])
        if raw is None:
            raise ValueError("Saved OpenAlex ID missing from its source page")
        rows.append(_source_row(raw, mission_id=mission, page=page,
                                page_hash=page_hashes[key]))
    if len({row["openalex_id"] for row in rows}) != len(rows):
        raise ValueError("Venue join repeated an OpenAlex ID")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-",
                                     dir=output_dir.parent) as name:
        temp = Path(name)
        file = temp / "venues.parquet"
        pq.write_table(pa.Table.from_pylist(rows), file, compression="zstd")
        if (file.stat().st_size > MAX_OUTPUT_BYTES
                or shutil.disk_usage(temp).free < MIN_FREE_BYTES):
            raise OSError("Venue-metadata output exceeded cap or disk reserve")
        verified = pq.read_table(file, columns=["openalex_id"])
        if verified.num_rows != len(rows):
            raise ValueError("Venue-metadata output row count differs")
        source_types = Counter(str(row["primary_source_type"] or "unknown") for row in rows)
        source_core = Counter(str(row["primary_source_is_core"]).lower() for row in rows)
        result = {
            "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_corpus_manifest_sha256": sha256_file(manifest_path),
            "input_corpus_file_sha256": manifest["file"]["sha256"],
            "source_pages_verified": len(pages),
            "counts": {"rows": len(rows),
                       "primary_source_type": dict(sorted(source_types.items())),
                       "primary_source_is_core": dict(sorted(source_core.items())),
                       "without_primary_source": sum(row["primary_source_id"] is None
                                                     for row in rows)},
            "file": {"name": file.name, "bytes": file.stat().st_size,
                     "sha256": sha256_file(file)},
            "policy": {"venue_metadata_only": True,
                       "no_records_excluded": True,
                       "not_a_primary_result_classifier": True,
                       "not_used_in_scoring": True,
                       "no_new_api_calls": True,
                       "max_output_bytes": MAX_OUTPUT_BYTES,
                       "min_free_disk_reserve_bytes": MIN_FREE_BYTES},
        }
        (temp / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return result
