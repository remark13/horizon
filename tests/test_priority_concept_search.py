import json
import sqlite3

import pytest

from saia.arxiv_trigram_index import GUARDED_VERSION, VERSION, _create_schema
from saia.controlled_collection import sha256_file
from saia.priority_concept_search import (_fts_query, _matches,
                                          exact_concept_search, supports_concept_plan)


def _plan():
    return {"concept_groups": [["quantum-inspired"],
                               ["tensor networks", "tensor-network"],
                               ["compression"]],
            "exclusions": [], "date_from": "2024-01-01",
            "as_of_date": "2026-01-01"}


def _index(tmp_path):
    db = sqlite3.connect(tmp_path / "index.sqlite3")
    _create_schema(db)
    samples = [
        ("2401.00001", "Quantum-inspired tensor networks", "LLM compression"),
        ("2401.00002", "Quantum-inspired CFD", "Tensor networks and compression"),
        ("2401.00003", "Quantum-inspired routing", "No relevant method"),
        ("2401.00004", "Quantum", "Inspired tensor networks compression"),
        ("2401.00005", "Tensor-network compression", "A quantum-inspired method"),
    ]
    for index, (identifier, title, abstract) in enumerate(samples, 1):
        db.execute("""INSERT INTO works (arxiv_id,title,abstract,first_submission_date,
            authors,authors_parsed_json,versions_json,doi,categories,license,journal_ref,
            comments,snapshot_update_date,source_shard)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (identifier, title, abstract, "2024-01-01", "A", "[]", "[]", None,
             "cs.AI", None, None, None, None, "part-1.parquet"))
        db.execute("INSERT INTO work_fts(rowid,text) VALUES (?,?)",
                   (index, (title + " " + abstract).casefold()))
    db.commit()
    db.close()
    guard = {"source_inventory_sha256": "sourcehash",
             "base_index_manifest_sha256": "basehash",
             "duplicate_source_rows": 0, "variants": []}
    (tmp_path / "duplicate_guard.json").write_text(json.dumps(guard), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": GUARDED_VERSION,
        "source": {"complete_pinned_inventory_indexed": True,
                   "full_inventory_sha256": "sourcehash"},
        "base_index_manifest_sha256": "basehash",
        "file": {"bytes": (tmp_path / "index.sqlite3").stat().st_size},
        "duplicate_guard": {"name": "duplicate_guard.json",
                            "bytes": (tmp_path / "duplicate_guard.json").stat().st_size,
                            "sha256": sha256_file(tmp_path / "duplicate_guard.json")},
    }), encoding="utf-8")


def test_concept_groups_are_and_of_or_synonyms_without_changing_legacy(tmp_path):
    _index(tmp_path)
    plan = _plan()
    assert supports_concept_plan(plan)
    assert " OR " in _fts_query(plan) and " AND " in _fts_query(plan)
    result = exact_concept_search(tmp_path, plan)
    assert result["arxiv_ids"] == ["2401.00001", "2401.00002", "2401.00005"]
    assert result["audit"]["legacy_or_semantics_unchanged"] is True
    plan["exclusions"] = ["CFD"]
    assert exact_concept_search(tmp_path, plan)["arxiv_ids"] == [
        "2401.00001", "2401.00005"]


def test_concept_matching_is_field_local_and_plan_is_bounded(tmp_path):
    assert not _matches({"title": "Quantum", "abstract": "inspired tensor networks compression"},
                        _plan())
    with pytest.raises(ValueError, match="safe limit"):
        _index(tmp_path)
        exact_concept_search(tmp_path, _plan(), max_matches=1)
    assert not supports_concept_plan(_plan() | {"concept_groups": [["AI"], ["compression"]]})
    assert not supports_concept_plan(_plan() | {"concept_groups": [["a\" OR b"], ["compression"]]})


def test_unguarded_index_cannot_silently_answer_concept_search(tmp_path):
    _index(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = VERSION
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate-guarded"):
        exact_concept_search(tmp_path, _plan())


def test_concept_matching_normalizes_whitespace_inside_each_field():
    plan = {"concept_groups": [["liveness detection"], ["deepfake"]],
            "exclusions": []}
    assert _matches({"title": "Deepfake defense",
                     "abstract": "Voice liveness\ndetection"}, plan)
    assert not _matches({"title": "Liveness", "abstract": "detection of deepfake"}, plan)
