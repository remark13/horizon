from __future__ import annotations

import json
from pathlib import Path

from saia.priority_arxiv_full import _existing_parent, _retain, VERSION
from saia.priority_arxiv_pilot import _sha


ROOT = Path(__file__).resolve().parents[1]


def test_example_retention_is_bounded_and_ordered() -> None:
    state = {"earliest": [], "latest": []}
    for number in (3, 1, 5, 2, 4):
        _retain(state, {"arxiv_id": str(number),
                        "first_submission": f"2020-01-0{number}"}, 2, 2)
    assert [row["arxiv_id"] for row in state["earliest"]] == ["1", "2"]
    assert [row["arxiv_id"] for row in state["latest"]] == ["5", "4"]


def test_existing_parent_does_not_create_output_directory(tmp_path: Path) -> None:
    destination = tmp_path / "not-created" / "deep" / "report.json"
    assert _existing_parent(destination) == tmp_path
    assert not destination.parent.exists()


def test_saved_full_mirror_pilot_is_auditable_retrieval_only() -> None:
    report = json.loads((ROOT / "data/processed/priority-arxiv-full-v1/report.json").read_text())
    config_path = ROOT / "config/priority-arxiv-pilot.v2.json"
    catalog_path = ROOT / "data/reference/priority_catalog/v1/catalog.json"
    assert report["version"] == VERSION
    assert report["source"]["inventory_files"] == 10
    assert report["source"]["inventory_rows"] == report["scanned_rows"] == 3_164_528
    assert report["input_fingerprints"]["catalog_sha256"] == _sha(catalog_path)
    assert report["input_fingerprints"]["case_config_sha256"] == _sha(config_path)
    assert len(report["cases"]) == 16
    assert sum(row["catalog_id"].startswith("national-area-") for row in report["cases"]) == 10
    assert sum(row["catalog_id"].startswith("customer-signal-") for row in report["cases"]) == 6
    assert report["coverage"]["full_pinned_mirror_inventory_scanned"] is True
    assert report["coverage"]["weak_signal_detected"] is False
    assert report["coverage"]["technology_field_denominator_available"] is False
    assert report["coverage"]["historical_title_abstract_frozen_at_first_submission"] is False
    for row in report["cases"]:
        assert sum(row["year_counts"].values()) == row["eligible_unique_in_pinned_mirror"]
        assert row["interpretation"] == "seeded literal retrieval only; not signal detection"
        assert len(row["example_references"]) <= 20
        assert all(reference["url"] == f"https://arxiv.org/abs/{reference['arxiv_id']}"
                   for reference in row["example_references"])
