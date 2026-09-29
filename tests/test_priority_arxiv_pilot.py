from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from saia.priority_arxiv_pilot import _boundary_pattern, _validated_cases


ROOT = Path(__file__).resolve().parents[1]


def test_pilot_covers_all_ten_directions_and_six_client_areas() -> None:
    catalog = json.loads((ROOT / "data/reference/priority_catalog/v1/catalog.json").read_text())
    for version in ("v1", "v2"):
        config = json.loads((ROOT / f"config/priority-arxiv-pilot.{version}.json").read_text())
        cases = _validated_cases(config, catalog)
        national = {item["id"]: item["direction_id"] for item in catalog["national_search_areas"]}
        customer = {item["id"]: item["client_area_id"] for item in catalog["customer_examples"]}
        assert {national[case["id"]] for case in cases if case["id"] in national} == {
            item["id"] for item in catalog["directions"]
        }
        assert {customer[case["id"]] for case in cases if case["id"] in customer} == {
            item["id"] for item in catalog["client_search_areas"]
        }


def test_word_boundary_removes_car_to_cloud_false_positive() -> None:
    candidates = pa.array(["CAR-T cell therapy", "Car-to-Cloud Communication", "Eta Carinae"])
    assert pc.match_substring_regex(candidates, _boundary_pattern("car-t"), ignore_case=True).to_pylist() == [
        True, False, False,
    ]


def test_saved_pilots_are_retrieval_diagnostics_not_signal_labels() -> None:
    for version in ("v1", "v2"):
        report = json.loads((ROOT / f"data/processed/priority-arxiv-pilot-{version}/report.json").read_text())
        assert report["version"] == f"priority-arxiv-pilot-{version}"
        assert report["scanned_cached_documents"] == 1_335_551
        assert len(report["pilot_cases"]) == 16
        assert report["limits"]["weak_signal_detected"] is False
        assert report["limits"]["field_denominator_available"] is False
        assert report["limits"]["full_arxiv_coverage"] is False
        assert all(case["interpretation"] == "seeded literal retrieval only; not signal detection"
                   for case in report["pilot_cases"])
