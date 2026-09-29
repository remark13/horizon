"""Optional read-only venue context for saved card presentation.

This never changes saved cards, evidence counts, screening or ranking. A venue
name is not a scientific-primary-result classification.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pyarrow.parquet as pq

from saia.controlled_collection import sha256_file
from saia.priority_openalex_venues import VERSION


DEFAULT_DIR = (Path(__file__).resolve().parents[1] / "data" / "processed"
               / "priority-openalex-venue-metadata-v1")


def load_index(directory: Path = DEFAULT_DIR) -> dict[str, dict]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    file = directory / manifest["file"]["name"]
    if (manifest.get("version") != VERSION
            or file.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(file) != manifest["file"]["sha256"]):
        raise ValueError("OpenAlex venue layer differs from manifest")
    rows = pq.read_table(file, columns=["openalex_id", "primary_source_name",
                                        "primary_source_type", "primary_source_is_core",
                                        "scientific_primary_result_verified"]).to_pylist()
    if len(rows) != manifest["counts"]["rows"]:
        raise ValueError("OpenAlex venue layer row count differs")
    index = {row["openalex_id"]: row for row in rows}
    if len(index) != len(rows):
        raise ValueError("OpenAlex venue layer has repeated IDs")
    return index


def enrich_triage(packet: dict, index: dict[str, dict]) -> dict:
    result = deepcopy(packet)
    count = 0
    for item in result.get("queue") or []:
        for evidence in (item.get("card") or {}).get("evidence") or []:
            venues = []
            seen = set()
            for source in evidence.get("sources") or []:
                url = str(source.get("url") or "")
                if not url.startswith("https://openalex.org/W"):
                    continue
                row = index.get(url.rsplit("/", 1)[-1])
                if row is None:
                    continue
                key = (row["primary_source_name"], row["primary_source_type"])
                if key in seen:
                    continue
                seen.add(key)
                venues.append({"name": row["primary_source_name"],
                               "source_type": row["primary_source_type"],
                               "is_core": row["primary_source_is_core"],
                               "primary_result_verified": None})
            if venues:
                evidence["venue_context"] = venues
                count += 1
    result["venue_context"] = {
        "status": "available", "evidence_items_with_context": count,
        "scope": "saved_three_cohorts_only",
        "scientific_primary_result_verified": False,
        "ranking_changed": False,
    }
    return result
