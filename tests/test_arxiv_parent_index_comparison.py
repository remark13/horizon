import json
import sqlite3

from saia.arxiv_parent_index_comparison import compare
from saia.controlled_collection import sha256_file


def test_parent_index_comparison_checks_monthly_equality(tmp_path):
    index_dir = tmp_path / "index"
    reports_dir = tmp_path / "reports"
    index_dir.mkdir()
    reports_dir.mkdir()
    db_path = index_dir / "index.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE works(first_submission_date TEXT)")
        connection.executemany("INSERT INTO works VALUES (?)",
                               [("2025-01-03",), ("2025-01-12",), ("2025-02-01",)])
    manifest = {"version": "arxiv-trigram-index-v4",
                "source": {"complete_pinned_inventory_indexed": True,
                           "full_inventory_sha256": "source", "revision": "revision"},
                "counts": {"invalid_v1_dates_excluded": 0},
                "file": {"name": "index.sqlite3", "bytes": db_path.stat().st_size,
                         "sha256": sha256_file(db_path)}}
    (index_dir / "manifest.json").write_text(json.dumps(manifest))
    report = {"version": "arxiv-parent-corpus-audit-0.4.16",
              "input": {"upstream_inventory_sha256": "source",
                        "dataset_revision": "revision", "categories": []},
              "period_from": "2025-01-01", "period_end_exclusive": "2025-03-01",
              "counts": {"parent_native_ids_in_period": 3},
              "series": [{"start": "2025-01-01", "parent_category_works": 2},
                         {"start": "2025-02-01", "parent_category_works": 1}]}
    path = reports_dir / "sample-parent-arxiv.json"
    path.write_text(json.dumps(report))
    result = compare(index_dir=index_dir, reports_dir=reports_dir)
    assert (result["reports_compared"], result["reports_exact"],
            result["months_mismatched"]) == (1, 1, 0)
    report["series"][1]["parent_category_works"] = 2
    path.write_text(json.dumps(report))
    result = compare(index_dir=index_dir, reports_dir=reports_dir)
    assert result["reports_exact"] == 0
    assert result["months_mismatched"] == 1
