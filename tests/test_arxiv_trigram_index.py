from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from saia.arxiv_trigram_index import (GUARDED_VERSION, VERSION, _create_schema,
                                      exact_search, supports_plan)
from saia.controlled_collection import sha256_file


def _plan(term: str) -> dict:
    return {"included_terms": [term], "exclusions": [],
            "date_from": "2024-01-01", "as_of_date": "2026-01-01"}


def test_index_rejects_unsafe_or_short_phrases() -> None:
    assert supports_plan(_plan("small modular reactor"))
    assert not supports_plan(_plan("ИИ"))
    assert not supports_plan(_plan("ai"))
    assert not supports_plan(_plan("model_%"))
    assert not supports_plan(_plan("small/modular"))
    assert supports_plan(_plan("small modular reactor") | {"matching_version":
                         "orthographic-separators-v1"})
    assert supports_plan(_plan("vision/language/action") | {"matching_version":
                         "orthographic-separators-v1"})
    assert not supports_plan(_plan("AI") | {"matching_version":
                             "orthographic-separators-v1"})


def test_partial_index_is_diagnostic_only_and_exact_predicate_reapplied(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "index.sqlite3")
    _create_schema(db)
    rows = [
        ("2401.00001", "Small modular reactor design", "", "2024-01-01", "A", "[]",
         "[]", None, "physics", None, None, None, None, "part-1.parquet"),
        ("2401.00002", "Small modular reactors", "", "2024-01-02", "B", "[]",
         "[]", None, "physics", None, None, None, None, "part-1.parquet"),
        ("2401.00003", "Unrelated", "small modular reactor\n safety", "2024-01-03",
         "C", "[]", "[]", None, "physics", None, None, None, None, "part-1.parquet"),
        ("2401.00004", "Vision-Language-Action Models", "On robot deployment",
         "2024-01-04", "D", "[]", "[]", None, "cs.AI", None, None, None, None,
         "part-1.parquet"),
    ]
    db.executemany("""INSERT INTO works (arxiv_id,title,abstract,
        first_submission_date,authors,authors_parsed_json,versions_json,doi,categories,
        license,journal_ref,comments,snapshot_update_date,source_shard)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    db.executemany("INSERT INTO work_fts(rowid,text) VALUES (?,?)", [
        (index, (row[1] + " " + row[2]).casefold().replace("\n", " "))
        for index, row in enumerate(rows, 1)
    ])
    db.commit()
    db.close()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": VERSION, "source": {"complete_pinned_inventory_indexed": False},
        "file": {"bytes": (tmp_path / "index.sqlite3").stat().st_size},
    }))
    with pytest.raises(ValueError, match="Partial"):
        exact_search(tmp_path, _plan("small modular reactor"))
    result = exact_search(tmp_path, _plan("small modular reactor"),
                          allow_partial_diagnostic=True)
    assert result["arxiv_ids"] == ["2401.00001", "2401.00003"]
    assert result["audit"]["weak_signal_detection"] is False
    orthographic = _plan("vision language action") | {
        "matching_version": "orthographic-separators-v1"}
    assert exact_search(tmp_path, orthographic, allow_partial_diagnostic=True)[
        "arxiv_ids"] == ["2401.00004"]
    assert exact_search(tmp_path, _plan("vision language action"),
                        allow_partial_diagnostic=True)["arxiv_ids"] == []


def test_guarded_index_refuses_query_matching_a_source_duplicate(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "index.sqlite3")
    _create_schema(db)
    db.execute("""INSERT INTO works (arxiv_id,title,abstract,first_submission_date,
        authors,authors_parsed_json,versions_json,doi,categories,license,journal_ref,
        comments,snapshot_update_date,source_shard) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2401.00001", "Galilean relativity", "", "2024-01-01", "A", "[]", "[]",
         None, "math-ph", None, None, None, None, "part-1.parquet"))
    db.execute("INSERT INTO work_fts(rowid,text) VALUES (?,?)", (1, "galilean relativity "))
    db.commit()
    db.close()
    guard = {"source_inventory_sha256": "sourcehash",
             "base_index_manifest_sha256": "basehash", "duplicate_source_rows": 2,
             "variants": [{"arxiv_id": "2401.00001", "title": "Galilean relativity",
                           "abstract": "", "first_submission_date": "2024-01-01"},
                          {"arxiv_id": "2401.00001", "title": "Galilea relativity",
                           "abstract": "", "first_submission_date": "2024-01-01"}]}
    (tmp_path / "duplicate_guard.json").write_text(json.dumps(guard))
    manifest = {"version": GUARDED_VERSION,
                "source": {"complete_pinned_inventory_indexed": True,
                           "full_inventory_sha256": "sourcehash"},
                "base_index_manifest_sha256": "basehash",
                "file": {"bytes": (tmp_path / "index.sqlite3").stat().st_size},
                "duplicate_guard": {"name": "duplicate_guard.json",
                                    "bytes": (tmp_path / "duplicate_guard.json").stat().st_size,
                                    "sha256": sha256_file(tmp_path / "duplicate_guard.json")}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="intersects duplicate source IDs"):
        exact_search(tmp_path, _plan("Galilea relativity"))
    result = exact_search(tmp_path, _plan("nonexistent phrase"))
    assert result["audit"]["duplicate_source_rows_guarded"] is True
