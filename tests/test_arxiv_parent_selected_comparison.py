import hashlib
import json
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq

from saia.arxiv_parent_selected_comparison import _selected_check
from saia.controlled_collection import sha256_file


def test_selected_comparison_checks_binding_text_and_phrase(tmp_path):
    package = tmp_path / "package"
    (package / "arxiv").mkdir(parents=True)
    mission = {"mission_id": "example", "controlled_search_plan": {
        "included_terms": ["neural chip"], "exclusions": [],
        "matching_version": "literal-phrase-0.4.6"}}
    mission_bytes = json.dumps(mission).encode()
    (package / "mission.json").write_bytes(mission_bytes)
    row = {"id": "2501.00001", "title": "Neural chip for sensors",
           "abstract": "A research device.", "categories": "cs.ET",
           "versions": [{"version": "v1", "created": "Fri, 03 Jan 2025 00:00:00 GMT"}]}
    selected = package / "arxiv" / "selected.parquet"
    pq.write_table(pa.Table.from_pylist([row]), selected)
    manifest = {"mission_id": "example",
                "mission_file_sha256": hashlib.sha256(mission_bytes).hexdigest(),
                "sources": {"arxiv": {"upstream_inventory_sha256": "source",
                                      "total_records": 1,
                                      "files": [{"file": "selected.parquet", "records": 1,
                                                 "sha256": sha256_file(selected)}]}}}
    manifest_bytes = json.dumps(manifest).encode()
    (package / "manifest.json").write_bytes(manifest_bytes)
    report = {"mission_id": "example", "period_from": "2025-01-01",
              "period_end_exclusive": "2025-02-01",
              "input": {"selected_package_manifest_bytes_sha256":
                            hashlib.sha256(manifest_bytes).hexdigest(),
                        "mission_bytes_sha256": hashlib.sha256(mission_bytes).hexdigest(),
                        "upstream_inventory_sha256": "source",
                        "categories": [], "text_scope": None},
              "counts": {"phrase_scope_native_ids_in_period": 1},
              "series": [{"start": "2025-01-01", "phrase_scope_works": 1}],
              "phrase_series": [{"phrase": "neural chip", "points": [
                  {"start": "2025-01-01", "works": 1}]}]}
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE works(arxiv_id TEXT,title TEXT,abstract TEXT,"
                       "categories TEXT,first_submission_date TEXT)")
    connection.execute("INSERT INTO works VALUES (?,?,?,?,?)",
                       ("2501.00001", row["title"], row["abstract"], row["categories"],
                        "2025-01-03"))
    result = _selected_check(package=package, report=report, connection=connection)
    assert result["exact_match"] is True
    connection.execute("UPDATE works SET title='Different' WHERE arxiv_id='2501.00001'")
    result = _selected_check(package=package, report=report, connection=connection)
    assert result["exact_match"] is False
    assert result["content_differences"] == [
        {"arxiv_id": "2501.00001", "reason": "indexed_metadata_differs"}]
    connection.close()
