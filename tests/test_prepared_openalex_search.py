import json
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import sha256_file
from saia.prepared_openalex_search import scan


def test_prepared_search_is_supplemental_exact_and_variant_aware(tmp_path):
    path = tmp_path / "works.parquet"
    title = "A novel cooling mechanism for small modular reactors"
    pq.write_table(pa.Table.from_pylist([
        {"openalex_id": "W1", "openalex_url": "https://openalex.org/W1",
         "doi": "10.1000/one", "title": title, "abstract": "Primary research",
         "publication_date": "2025-01-01", "document_type": "article",
         "authors": ["Researcher"], "source_mission_ids": ["old"]},
        {"openalex_id": "W2", "openalex_url": "https://openalex.org/W2",
         "doi": "10.1000/two", "title": title, "abstract": "Updated research",
         "publication_date": "2025-02-01", "document_type": "preprint",
         "authors": ["Researcher"], "source_mission_ids": ["new"]},
        {"openalex_id": "W3", "openalex_url": "https://openalex.org/W3",
         "doi": None, "title": "Review: small modular reactors", "abstract": None,
         "publication_date": "2025-03-01", "document_type": "peer-review",
         "authors": ["Reviewer"], "source_mission_ids": ["new"]},
        {"openalex_id": "W4", "openalex_url": "https://openalex.org/W4",
         "doi": None, "title": "Unrelated battery chemistry", "abstract": None,
         "publication_date": "2025-04-01", "document_type": "article",
         "authors": ["Other"], "source_mission_ids": ["new"]},
    ]), path)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": "priority-openalex-complete-cohorts-v2",
        "policy": {"only_verified_query_complete_saved_pages": True},
        "file": {"name": path.name, "sha256": sha256_file(path),
                 "bytes": path.stat().st_size},
        "cohorts": [{"mission_id": "old", "fetch_finished_utc": "2026-09-22T00:00:00Z", "source_errors": []},
                    {"mission_id": "new", "fetch_finished_utc": "2026-09-26T00:00:00Z", "source_errors": []}],
    }))
    specs = [{"branch_id": "smr", "included_phrases": ["small modular reactors"],
              "excluded_phrases": [], "matching_version": "orthographic-separators-v1"},
             {"branch_id": "late_abstract", "included_phrases": ["updated research"],
              "excluded_phrases": []},
             {"branch_id": "outside", "included_phrases": ["quantum algorithm"],
              "excluded_phrases": []}]
    result = scan(tmp_path, specs, date(2024, 9, 1), date(2026, 9, 1), 25)
    assert result["administrative_rows_excluded"] == 1
    assert result["variant_rows_collapsed"] == 1
    assert result["branches"][0]["eligible_matches_in_prepared_query_cohorts"] == 1
    assert result["branches"][0]["works"][0]["prepared_openalex_variant_ids"] == ["W1", "W2"]
    assert result["branches"][0]["selected_source_mission_ids"] == ["new", "old"]
    assert result["branches"][1]["eligible_matches_in_prepared_query_cohorts"] == 1
    assert result["branches"][1]["works"][0]["published_at"] == "2025-02-01"
    assert result["branches"][1]["works"][0]["prepared_family_earliest_publication_date"] == "2025-01-01"
    assert result["branches"][2]["works"] == []
    assert result["complete_for_arbitrary_query"] is False
    assert result["coverage_comparable"] is None


def test_prepared_search_rejects_unverified_or_escaping_manifest(tmp_path):
    manifest = {
        "version": "priority-openalex-complete-cohorts-v2",
        "policy": {"only_verified_query_complete_saved_pages": False},
        "file": {"name": "works.parquet", "bytes": 0, "sha256": "0" * 64},
        "cohorts": [{"mission_id": "m", "source_errors": []}],
    }
    path = tmp_path / "manifest.json"
    specs = [{"branch_id": "x", "included_phrases": ["signal"],
              "excluded_phrases": []}]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="verified complete"):
        scan(tmp_path, specs, date(2024, 1, 1), date(2025, 1, 1), 1)
    manifest["policy"]["only_verified_query_complete_saved_pages"] = True
    manifest["file"]["name"] = "../other.parquet"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inside its cohort"):
        scan(tmp_path, specs, date(2024, 1, 1), date(2025, 1, 1), 1)
