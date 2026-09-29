import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import sha256_file
from saia.openalex_title_phrases import collect_parent


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/openalex-title-phrase-pilot-smr-development-v3.json"


def test_openalex_parent_filters_nonworks_and_collapses_only_exact_variants(tmp_path):
    cohort = tmp_path / "cohort"
    cohort.mkdir()
    path = cohort / "works.parquet"
    common = {"title": "A symmetric thermal architecture for small modular reactors",
              "abstract": "A model", "authors": ["Same Author"],
              "document_type": "preprint", "source_mission_ids": ["smr"]}
    data = [
        {**common, "openalex_id": "W1", "openalex_url": "https://openalex.org/W1",
         "publication_date": "2025-01-01"},
        {**common, "openalex_id": "W2", "openalex_url": "https://openalex.org/W2",
         "publication_date": "2025-03-01"},
        {**common, "openalex_id": "W3", "openalex_url": "https://openalex.org/W3",
         "publication_date": "2025-04-01", "document_type": "peer-review"},
        {**common, "openalex_id": "W4", "openalex_url": "https://openalex.org/W4",
         "publication_date": "2022-01-01", "authors": ["Other Author"]},
        {**common, "openalex_id": "W5", "openalex_url": "https://openalex.org/W5",
         "publication_date": "2025-05-01", "title": "Unrelated title", "abstract": None},
    ]
    pq.write_table(pa.Table.from_pylist(data), path)
    manifest = {
        "file": {"name": path.name, "bytes": path.stat().st_size,
                 "sha256": sha256_file(path)},
        "cohorts": [{"mission_id": "smr", "period": {"from": "2016-09-01",
                     "to": "2026-08-31"}, "priority_catalog_area_id": "national-area-020",
                     "source_errors": [], "excluded_invalid_rows": 0,
                     "query_terms": ["small modular reactor"],
                     "source_record_count": 5, "source_manifest_sha256": "raw-hash"}],
    }
    manifest_path = cohort / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    config = json.loads(CONFIG.read_text())
    config["openalex_mission_id"] = "smr"
    config["openalex_manifest_sha256"] = sha256_file(manifest_path)
    rows, audit = collect_parent(cohort, config)
    assert [row["openalex_id"] for row in rows] == ["W4", "W1"]
    assert audit["query_cohort_records"] == 5
    assert audit["excluded_document_types"] == {"peer-review": 1}
    assert audit["excluded_current_title_abstract_not_matching_parent"] == 1
    assert audit["exact_parent_records_before_variant_collapse"] == 3
    assert audit["selected_unique_ids"] == 2
    assert audit["variant_family_audit"]["variant_rows_collapsed"] == 1
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="Parquet differs"):
        collect_parent(cohort, config)
