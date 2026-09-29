import json

import pyarrow as pa
import pyarrow.parquet as pq

from saia.controlled_collection import sha256_file
from scripts.audit_nested_openalex_cohorts import build


def test_nested_cohorts_report_both_directions_of_snapshot_drift(tmp_path):
    path = tmp_path / "works.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"openalex_id": "W1", "title": "Both", "publication_date": "2025-01-01",
         "source_mission_ids": ["inner", "outer"]},
        {"openalex_id": "W2", "title": "Old only", "publication_date": "2025-02-01",
         "source_mission_ids": ["inner"]},
        {"openalex_id": "W3", "title": "New only", "publication_date": "2025-03-01",
         "source_mission_ids": ["outer"]},
        {"openalex_id": "W4", "title": "Outside inner period",
         "publication_date": "2020-01-01", "source_mission_ids": ["outer"]},
    ]), path)
    manifest = {"file": {"name": path.name, "bytes": path.stat().st_size,
                         "sha256": sha256_file(path)},
                "cohorts": [{"mission_id": "inner", "query_terms": ["topic"],
                             "period": {"from": "2024-09-01", "to": "2026-08-31"},
                             "source_errors": [], "excluded_invalid_rows": 0,
                             "source_record_count": 2, "source_manifest_sha256": "hash-a",
                             "fetch_finished_utc": "2026-09-22T00:00:00Z"},
                            {"mission_id": "outer", "query_terms": ["topic"],
                             "period": {"from": "2016-09-01", "to": "2026-08-31"},
                             "source_errors": [], "excluded_invalid_rows": 0,
                             "source_record_count": 3, "source_manifest_sha256": "hash-b",
                             "fetch_finished_utc": "2026-09-26T00:00:00Z"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = build(tmp_path, "inner", "outer")
    assert result["overlap_openalex_ids"] == 1
    assert result["inner_only_ids"] == ["W2"]
    assert result["outer_only_ids"] == ["W3"]
    assert result["identical_result_sets"] is False
