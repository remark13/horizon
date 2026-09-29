import json
import sqlite3

import pytest

from saia.arxiv_background_counts import build
from saia.arxiv_trigram_index import GUARDED_VERSION
from saia.controlled_collection import sha256_file


def _index(tmp_path):
    index = tmp_path / "index"
    index.mkdir()
    database = index / "index.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE works (first_submission_date TEXT, categories TEXT)")
    connection.executemany("INSERT INTO works VALUES (?,?)", [
        ("2023-05-01", "cs.AI cs.LG"),
        ("2023-06-01", "cs.AI cs.LG cs.AI"),
        ("2024-01-01", "cs.LG cs.AI"),
        ("2024-02-01", ""),
        ("2026-01-01", "cs.AI"),
    ])
    connection.commit()
    connection.close()
    manifest = {
        "version": GUARDED_VERSION,
        "source": {"dataset": "test", "revision": "frozen",
                   "full_inventory_sha256": "inventory",
                   "complete_pinned_inventory_indexed": True},
        "counts": {"indexed_unique_arxiv_ids": 5,
                   "invalid_v1_dates_excluded": 0,
                   "duplicate_id_rows_excluded_first_snapshot_row_wins": 0},
        "file": {"name": database.name, "bytes": database.stat().st_size,
                 "sha256": sha256_file(database)},
    }
    (index / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return index


def test_full_category_year_background_has_distinct_overlapping_counts(tmp_path):
    index = _index(tmp_path)
    output = tmp_path / "background.json"
    report = build(index_dir=index, output_path=output, first_year=2023,
                   last_complete_year=2025)
    assert report["counts"]["unique_works_in_window"] == 4
    assert report["counts"]["missing_categories"] == 1
    assert [(row["year"], row["unique_works"]) for row in report["annual"]] == [
        (2023, 2), (2024, 2), (2025, 0)]
    counts = {(row["category"], row["year"]): row
              for row in report["category_annual"]}
    assert counts["cs.AI", 2023]["first_listed_category_works"] == 2
    assert counts["cs.AI", 2024]["any_listed_category_works"] == 1
    assert counts["cs.LG", 2023]["any_listed_category_works"] == 2
    assert counts["cs.LG", 2024]["first_listed_category_works"] == 1
    assert report["interpretation"]["not_a_weak_signal_or_growth_measure"] is True
    with pytest.raises(FileExistsError):
        build(index_dir=index, output_path=output, first_year=2023,
              last_complete_year=2025)


def test_background_rejects_modified_index_and_incomplete_year(tmp_path):
    index = _index(tmp_path)
    with pytest.raises(ValueError, match="complete pinned years"):
        build(index_dir=index, output_path=tmp_path / "bad.json", first_year=2023,
              last_complete_year=2026)
    database = index / "index.sqlite3"
    with database.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="size differs"):
        build(index_dir=index, output_path=tmp_path / "bad.json", first_year=2023,
              last_complete_year=2025)
