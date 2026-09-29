import hashlib
import json
from pathlib import Path
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia import arxiv_parent_index_audit as fast
from saia.controlled_collection import sha256_file
from saia.coverage_passport import verify


def test_fast_parent_eligibility_excludes_category_and_duplicate_period():
    mission = {"period": {"from": "2025-01-01", "to": "2025-02-28"},
               "query": {"arxiv_categories": []},
               "controlled_search_plan": {"included_terms": ["neural chip"],
                                          "exclusions": []}}
    index = {"version": "arxiv-trigram-index-v4",
             "source": {"complete_pinned_inventory_indexed": True},
             "counts": {"invalid_v1_dates_excluded": 0}}
    guard = {"version": "arxiv-duplicate-text-guard-v1",
             "variants": [{"first_submission_date": "2007-03-30"}]}
    assert fast.eligible(mission=mission, index_manifest=index,
                         duplicate_guard=guard) is True
    mission["query"]["arxiv_categories"] = ["cs.AI"]
    assert fast.eligible(mission=mission, index_manifest=index,
                         duplicate_guard=guard) is False
    mission["query"]["arxiv_categories"] = []
    mission["period"]["from"] = "2006-01-01"
    assert fast.eligible(mission=mission, index_manifest=index,
                         duplicate_guard=guard) is False


def test_fast_parent_audit_rechecks_selected_and_counts(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "fake.parquet").write_bytes(b"pinned")
    monkeypatch.setattr(fast, "local_policy", lambda: {
        "expected_files": 1, "expected_rows": 3, "revision": "revision"})
    monkeypatch.setattr(fast, "validate_inventory", lambda *_args: (
        mirror / "fake.parquet",))
    monkeypatch.setattr(fast, "inventory_sha256", lambda *_args: "source")
    package = tmp_path / "package"
    (package / "arxiv").mkdir(parents=True)
    mission = {"mission_id": "example", "as_of_date": "2025-03-01",
               "period": {"from": "2025-01-01", "to": "2025-02-28"},
               "query": {"arxiv_categories": []},
               "controlled_search_plan": {
                   "included_terms": ["neural chip"], "exclusions": [],
                   "matching_version": "literal-phrase-0.4.6"}}
    mission_path = package / "mission.json"
    mission_path.write_text(json.dumps(mission))
    row = {"id": "2501.00001", "title": "Neural chip for sensors",
           "abstract": "A research device.", "categories": "cs.ET",
           "versions": [{"version": "v1", "created": "Fri, 03 Jan 2025 00:00:00 GMT"}]}
    selected = package / "arxiv" / "selected.parquet"
    pq.write_table(pa.Table.from_pylist([row]), selected)
    package_manifest = {"mission_id": "example",
                        "mission_file_sha256": sha256_file(mission_path),
                        "sources": {"arxiv": {
                            "dataset_revision": "revision",
                            "upstream_inventory_sha256": "source",
                            "total_records": 1,
                            "files": [{"file": "selected.parquet", "records": 1,
                                       "sha256": sha256_file(selected)}]}}}
    manifest_path = package / "manifest.json"
    manifest_path.write_text(json.dumps(package_manifest))
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE works(arxiv_id TEXT,title TEXT,abstract TEXT,"
                           "categories TEXT,first_submission_date TEXT)")
        connection.executemany("INSERT INTO works VALUES (?,?,?,?,?)", [
            ("2501.00001", row["title"], row["abstract"], "cs.ET", "2025-01-03"),
            ("2501.00002", "Other", "Work", "cs.AI", "2025-01-10"),
            ("2502.00001", "Another", "Work", "cs.AI", "2025-02-01")])
    guard = {"version": "arxiv-duplicate-text-guard-v1",
             "source_inventory_sha256": "source",
             "base_index_manifest_sha256": "base", "variants": []}
    guard_path = index_dir / "duplicate_guard.json"
    guard_path.write_text(json.dumps(guard))
    index_manifest = {
        "version": "arxiv-trigram-index-v4",
        "base_index_manifest_sha256": "base",
        "source": {"complete_pinned_inventory_indexed": True,
                   "revision": "revision", "full_inventory_sha256": "source"},
        "counts": {"invalid_v1_dates_excluded": 0, "scanned_rows": 3,
                   "indexed_unique_arxiv_ids": 3,
                   "duplicate_id_rows_excluded_first_snapshot_row_wins": 0},
        "file": {"name": "index.sqlite3", "bytes": db_path.stat().st_size,
                 "sha256": sha256_file(db_path)},
        "duplicate_guard": {"name": "duplicate_guard.json",
                            "sha256": sha256_file(guard_path)}}
    (index_dir / "manifest.json").write_text(json.dumps(index_manifest))
    report = fast.audit(mirror_dir=mirror, mission_path=mission_path,
                        package_manifest_path=manifest_path, index_dir=index_dir)
    assert report["counts"]["parent_native_ids_in_period"] == 3
    assert report["counts"]["phrase_scope_native_ids_in_period"] == 1
    assert [point["parent_category_works"] for point in report["series"]] == [2, 1]
    assert report["coverage"]["selected_work_predicate_and_metadata_rechecked"] is True
    evidence_path = tmp_path / "fast-report.json"
    evidence_path.write_text(json.dumps(report))
    assert verify(package, evidence_path)[2]["version"] == fast.VERSION
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE works SET title='wrong' WHERE arxiv_id='2501.00001'")
    index_manifest["file"] = {"name": "index.sqlite3", "bytes": db_path.stat().st_size,
                              "sha256": sha256_file(db_path)}
    (index_dir / "manifest.json").write_text(json.dumps(index_manifest))
    with pytest.raises(ValueError, match="differs from guarded index"):
        fast.audit(mirror_dir=mirror, mission_path=mission_path,
                   package_manifest_path=manifest_path, index_dir=index_dir)
