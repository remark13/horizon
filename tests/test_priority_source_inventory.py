from __future__ import annotations

import json
from pathlib import Path
import pytest

from saia.priority_source_inventory import VERSION, inventory_one
from saia.priority_arxiv_pilot import _sha


ROOT = Path(__file__).resolve().parents[1]


def test_saved_inventory_is_explicit_about_query_scope() -> None:
    report = json.loads((ROOT / "outputs/priority-existing-openalex-inventory-v2.json").read_text())
    assert report["version"] == VERSION
    assert report["counts"]["snapshots"] == len(report["snapshots"]) == 23
    assert report["counts"]["verified_query_complete"] == 3
    assert report["policy"]["openalex_complete_field_coverage_proven"] is False
    assert report["policy"]["sum_records_as_unique_works"] is False
    by_directory = {Path(row["directory"]).name: row for row in report["snapshots"]}
    for name in ("openalex-small-modular-reactors-2026q3",
                 "openalex-precision-fermentation-2026q3",
                 "openalex-bone-tissue-engineering-2026q3"):
        assert by_directory[name]["reuse_status"] == "verified_query_complete"
        assert by_directory[name]["cursor_audit_complete"] is True


def test_inventory_rejects_path_traversal_in_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({
        "sources": {"openalex": {"files": []}},
        "mission_snapshot_file": "../outside.json",
    }))
    try:
        inventory_one(directory)
    except ValueError as error:
        assert "Unsafe" in str(error)
    else:
        raise AssertionError("Unsafe path was accepted")


@pytest.mark.local_data
def test_inventory_manifest_hash_is_reproducible() -> None:
    directory = ROOT / "data/raw/openalex-small-modular-reactors-2026q3"
    row = inventory_one(directory)
    assert row is not None
    assert row["manifest_sha256"] == _sha(directory / "manifest.json")
    assert row["records_in_saved_pages"] == 1808
