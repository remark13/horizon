"""Portable checks for the source-derived catalog; no Downloads files needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from saia.priority_catalog import (
    CUSTOMER_DIRECTION_HINTS, DIRECTION_IDS_BY_NAME, VERSION, _links,
    write_snapshot,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data/reference/priority_catalog/v1/catalog.json"


def test_source_snapshot_keeps_roles_and_all_ids_distinct() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert catalog["version"] == VERSION
    assert catalog["counts"]["directions"] == 10
    assert catalog["counts"]["national_search_areas"] == 89
    assert catalog["counts"]["client_search_areas"] == 6
    assert catalog["counts"]["customer_examples"] == 100
    assert {item["name_ru"] for item in catalog["directions"]} == set(DIRECTION_IDS_BY_NAME)
    assert {item["id"] for item in catalog["directions"]} == set(DIRECTION_IDS_BY_NAME.values())
    assert {item["id"] for item in catalog["national_search_areas"]} == {
        f"national-area-{number:03d}" for number in range(1, 90)
    }
    assert {item["id"] for item in catalog["customer_examples"]} == {
        f"customer-signal-{number:03d}" for number in range(1, 101)
    }
    assert all(item["role"] == "search_area_not_signal" for item in catalog["national_search_areas"])
    assert all(item["role"] == "customer_supplied_signal_example" for item in catalog["customer_examples"])
    assert all(item["independent_gold_label"] is None for item in catalog["customer_examples"])
    assert all(item["query_draft"]["status"] == "unreviewed_source_draft"
               for item in catalog["national_search_areas"] + catalog["customer_examples"])
    assert catalog["policy"]["unknown_query_uses_general_route"] is True
    assert catalog["policy"]["draft_queries_may_be_executed_without_review"] is False


def test_customer_area_hints_are_explicit_and_nonexclusive() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    areas = catalog["client_search_areas"]
    assert {area["name_ru"] for area in areas} == set(CUSTOMER_DIRECTION_HINTS)
    assert all(area["mapping_status"] == "draft_area_level_navigation_not_relevance_label" for area in areas)
    valid = {direction["id"] for direction in catalog["directions"]}
    assert all(set(area["direction_hints"]) <= valid for area in areas)
    area_ids = {area["id"] for area in areas}
    assert all(example["client_area_id"] in area_ids for example in catalog["customer_examples"])


def test_snapshot_write_refuses_changed_content(tmp_path: Path) -> None:
    path = tmp_path / "v1" / "catalog.json"
    assert write_snapshot({"version": VERSION}, path) == path
    assert write_snapshot({"version": VERSION}, path) == path
    with pytest.raises(FileExistsError):
        write_snapshot({"version": "changed"}, path)


def test_links_keep_original_cell_as_authority() -> None:
    assert _links("[A](https://example.org/a), [A](https://example.org/a), https://other.test/b") == [
        "https://example.org/a", "https://other.test/b",
    ]
