from __future__ import annotations

import json
from pathlib import Path
import shutil

import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import sha256_file
from saia.priority_openalex_materialize import _work, materialize
from saia.priority_source_inventory import VERSION as INVENTORY_VERSION, inventory_one


def _saved_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "raw" / "sample-openalex"
    page_dir = source / "openalex"
    page_dir.mkdir(parents=True)
    work = {"id": "https://openalex.org/W123", "title": "Example research work",
            "doi": "https://doi.org/10.1000/TEST", "publication_date": "2025-01-02",
            "abstract_inverted_index": {"A": [0], "result": [1]},
            "authorships": [{"author": {"display_name": "Researcher"},
                             "institutions": [{"id": "https://openalex.org/I1"}]}],
            "cited_by_count": 7}
    page = page_dir / "page_0001.json"
    page.write_text(json.dumps({"results": [work]}))
    mission = source / "mission.json"
    mission.write_text(json.dumps({"period": {"from": "2025-01-01", "to": "2025-12-31"},
                                  "query": {"terms": ["example research"]},
                                  "protocol": {"priority_catalog_area_id": "national-area-005"}}))
    manifest = {"mission_id": "sample", "query_version": "sample-v1",
                "mission_snapshot_file": "mission.json",
                "mission_file_sha256": sha256_file(mission),
                "incomplete": {}, "source_errors": [],
                "sources": {"openalex": {"access_mode": "cursor-paged-query",
                                        "total_records": 1,
                                        "files": [{"file": page.name, "records": 1,
                                                   "sha256": sha256_file(page),
                                                   "cursor_audit_complete": True,
                                                   "cursor_next": None}]}}}
    (source / "manifest.json").write_text(json.dumps(manifest))
    audited = inventory_one(source)
    assert audited and audited["reuse_status"] == "verified_query_complete"
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"version": INVENTORY_VERSION, "snapshots": [audited]}))
    return source, inventory


def test_openalex_work_restores_abstract_and_source_provenance() -> None:
    row = _work({"id": "https://openalex.org/W42", "title": "A title",
                 "publication_date": "2025-01-02",
                 "abstract_inverted_index": {"a": [0], "result": [1]}},
                "sample", "page_0001.json", "abc")
    assert row["openalex_id"] == "W42"
    assert row["abstract"] == "a result"
    assert row["source_page_sha256"] == "abc"
    assert row["source_mission_ids"] == ["sample"]


def test_only_verified_complete_snapshot_can_be_materialized(tmp_path: Path) -> None:
    source, inventory = _saved_snapshot(tmp_path)
    output = tmp_path / "complete-cohort"
    manifest = materialize(inventory_path=inventory, raw_root=source.parent, output_dir=output)
    assert manifest["counts"]["unique_openalex_works"] == 1
    assert manifest["counts"]["with_abstract"] == 1
    assert manifest["policy"]["denominator_for_technology_area"] is False
    assert manifest["cohorts"][0]["priority_catalog_area_id"] == "national-area-005"
    assert manifest["counts"]["by_source_mission_memberships"] == {"sample": 1}
    table = pq.read_table(output / "works.parquet")
    assert table.column("doi").to_pylist() == ["10.1000/test"]
    assert table.column("authors").to_pylist() == [["Researcher"]]
    assert table.column("source_mission_ids").to_pylist() == [["sample"]]
    assert sha256_file(output / "works.parquet") == manifest["file"]["sha256"]
    with pytest.raises(FileExistsError):
        materialize(inventory_path=inventory, raw_root=source.parent, output_dir=output)
    (source / "openalex" / "page_0001.json").write_text('{"results": []}')
    with pytest.raises(ValueError, match="no longer matches"):
        materialize(inventory_path=inventory, raw_root=source.parent,
                    output_dir=tmp_path / "corrupt-cohort")


def test_same_openalex_work_keeps_both_cohort_memberships_without_double_count(tmp_path: Path) -> None:
    source, inventory = _saved_snapshot(tmp_path)
    second = source.parent / "second-openalex"
    shutil.copytree(source, second)
    mission_path = second / "mission.json"
    mission = json.loads(mission_path.read_text())
    mission["protocol"]["priority_catalog_area_id"] = "national-area-006"
    mission_path.write_text(json.dumps(mission))
    manifest_path = second / "manifest.json"
    second_manifest = json.loads(manifest_path.read_text())
    second_manifest["mission_id"] = "second"
    second_manifest["mission_file_sha256"] = sha256_file(mission_path)
    manifest_path.write_text(json.dumps(second_manifest))
    rows = [inventory_one(source), inventory_one(second)]
    assert all(row and row["reuse_status"] == "verified_query_complete" for row in rows)
    inventory.write_text(json.dumps({"version": INVENTORY_VERSION, "snapshots": rows}))
    result = materialize(inventory_path=inventory, raw_root=source.parent,
                         output_dir=tmp_path / "overlap-cohort")
    assert result["counts"]["source_records"] == 2
    assert result["counts"]["unique_openalex_works"] == 1
    assert result["counts"]["duplicate_openalex_id_rows"] == 1
    assert result["counts"]["by_source_mission_memberships"] == {
        "sample": 1, "second": 1,
    }
    table = pq.read_table(tmp_path / "overlap-cohort" / "works.parquet")
    assert table.column("source_mission_ids").to_pylist() == [["sample", "second"]]
