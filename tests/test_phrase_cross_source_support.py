import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import sha256_file
from scripts.phrase_cross_source_support import _matches_group, build


def test_matching_unions_group_spellings_without_counting_two_hits_twice():
    row = {"title": "Solid-state electrolytes for batteries",
           "abstract": "A solid electrolyte material"}
    assert _matches_group(row, ["solid electrolytes", "solid electrolyte"])
    assert not _matches_group(row, ["hard carbon"])


def test_cross_source_report_keeps_populations_separate_and_checks_hashes(tmp_path):
    cohort = tmp_path / "cohort"
    cohort.mkdir()
    works = cohort / "works.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"openalex_id": "W1", "openalex_url": "https://openalex.org/W1",
         "title": "Solid electrolytes for batteries", "abstract": "solid electrolyte",
         "publication_date": "2025-01-01", "source_mission_ids": ["battery"]},
        {"openalex_id": "W2", "openalex_url": "https://openalex.org/W2",
         "title": "Unrelated finding", "abstract": None,
         "publication_date": "2026-01-01", "source_mission_ids": ["battery"]},
    ]), works)
    manifest = {
        "file": {"name": works.name, "sha256": sha256_file(works), "bytes": works.stat().st_size},
        "cohorts": [{"mission_id": "battery", "period": {"from": "2024-09-01",
                   "to": "2026-08-31"}, "source_errors": [], "excluded_invalid_rows": 0,
                   "source_record_count": 2, "query_terms": ["battery materials"],
                   "priority_catalog_area_id": "national-area-005"}],
    }
    (cohort / "manifest.json").write_text(json.dumps(manifest))
    followup = tmp_path / "followup.json"
    followup.write_text(json.dumps({
        "version": "auto-broad-phrase-full-index-followup-v2",
        "groups_processed_without_manual_selection": 15,
        "period": {"as_of_date_exclusive": "2026-09-01"},
        "groups": [{"rank": rank, "phrase_en": "solid electrolytes",
                    "member_phrases": ["solid electrolytes", "solid electrolyte"],
                    "in_parent_unique_ids": 3, "full_index_exact_ids": 7}
                   for rank in range(1, 16)],
    }))
    result = build(followup_path=followup, openalex_dir=cohort, mission_id="battery")
    assert result["openalex_query_complete_records"] == 2
    assert result["groups"][0]["openalex_query_cohort_exact_mentions"] == 1
    assert result["groups"][0]["openalex_two_complete_windows"] == [1, 0]
    assert result["groups"][0]["openalex_title_anchored_count"] == 1
    assert result["groups"][0]["arxiv_full_index_current_text_mentions"] == 7
    assert result["weak_signal_detection_performed"] is False
    works.write_bytes(works.read_bytes() + b"bad")
    with pytest.raises(ValueError, match="differs"):
        build(followup_path=followup, openalex_dir=cohort, mission_id="battery")
