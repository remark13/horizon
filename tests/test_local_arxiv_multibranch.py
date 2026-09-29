from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.local_arxiv_multibranch import scan
from saia.local_arxiv_search import validate_inventory
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION


def row(identifier, title, abstract, created):
    stamp = datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc)
    return {
        "id": identifier, "title": title, "abstract": abstract, "categories": "cs.LG",
        "versions": [{"version": "v1", "created": stamp.strftime("%a, %d %b %Y %H:%M:%S %Z")}],
        "authors_parsed": [["Doe", "Jane", ""]], "doi": None,
        "journal-ref": None, "comments": None, "update_date": created,
        "authors": "Doe, Jane", "license": None,
    }


def test_multibranch_scan_reads_inventory_once_and_keeps_independent_series(tmp_path):
    path = tmp_path / "train-00000-of-00001.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("2301.00001", "Tissue engineering scaffold", "Result", date(2023, 1, 1)),
        row("2302.00001", "3D bioprinting", "Tissue engineering", date(2023, 2, 1)),
        row("2401.00001", "Graph neural network", "Machine learning", date(2024, 1, 1)),
        row("2402.00001", "Clinical graph neural network", "Machine learning", date(2024, 2, 1)),
    ]), path)
    validate_inventory.cache_clear()
    result = scan(tmp_path, [
        {"branch_id": "tissue", "included_phrases": ["tissue engineering", "3D bioprinting"], "excluded_phrases": []},
        {"branch_id": "gnn", "included_phrases": ["graph neural network"], "excluded_phrases": ["clinical"]},
    ], date(2023, 1, 1), date(2025, 1, 1), 1, expected_files=1, expected_rows=4)
    assert result["scanned_rows"] == 4
    by_id = {item["branch_id"]: item for item in result["branches"]}
    assert by_id["tissue"]["eligible_matches"] == 2
    assert by_id["tissue"]["works"][0]["source_ids"] == ("https://arxiv.org/abs/2302.00001",)
    assert by_id["gnn"]["eligible_matches"] == 1
    assert by_id["gnn"]["works"][0]["source_ids"] == ("https://arxiv.org/abs/2401.00001",)
    assert result["weak_signal_assessment_performed"] is False


def test_multibranch_expanded_pilot_limit_is_bounded(tmp_path):
    path = tmp_path / "train-00000-of-00001.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("2401.00001", "Robotic manipulation", "Experiment", date(2024, 1, 1)),
    ]), path)
    validate_inventory.cache_clear()
    args = (tmp_path, [{"branch_id": "robotics",
                       "included_phrases": ["robotic manipulation"]}],
            date(2024, 1, 1), date(2025, 1, 1))
    result = scan(*args, 100, expected_files=1, expected_rows=1)
    assert len(result["branches"][0]["works"]) == 1
    with pytest.raises(ValueError, match="100"):
        scan(*args, 101, expected_files=1, expected_rows=1)


def test_multibranch_scan_respects_compiled_orthographic_version(tmp_path):
    path = tmp_path / "train-00000-of-00001.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("2604.24447", "Vision-Language-Action Models", "Robotic deployment",
            date(2026, 4, 27)),
    ]), path)
    validate_inventory.cache_clear()
    result = scan(tmp_path, [{
        "branch_id": "vla", "included_phrases": ["vision language action"],
        "excluded_phrases": [], "matching_version": ORTHOGRAPHIC_MATCHING_VERSION,
    }], date(2026, 1, 1), date(2026, 9, 1), 5,
        expected_files=1, expected_rows=1)
    assert result["branches"][0]["eligible_matches"] == 1
    assert result["branches"][0]["matching_version"] == ORTHOGRAPHIC_MATCHING_VERSION


def test_multibranch_year_balanced_sample_keeps_early_years(tmp_path):
    path = tmp_path / "train-00000-of-00001.parquet"
    rows = [
        row(f"{str(year)[2:]}01.{index:05d}", "Federated learning",
            "Independent experiment", date(year, 1, index + 1))
        for year in (2021, 2022, 2023) for index in range(5)
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
    validate_inventory.cache_clear()
    kwargs = {"expected_files": 1, "expected_rows": len(rows),
              "selection_strategy": "year_balanced_hash_v1"}
    specs = [{"branch_id": "fl", "included_phrases": ["federated learning"],
              "excluded_phrases": []}]
    first = scan(tmp_path, specs, date(2021, 1, 1), date(2024, 1, 1), 6, **kwargs)
    second = scan(tmp_path, specs, date(2021, 1, 1), date(2024, 1, 1), 6, **kwargs)
    branch = first["branches"][0]
    assert branch["year_counts"] == {"2021": 5, "2022": 5, "2023": 5}
    assert branch["selected_year_counts"] == {"2021": 2, "2022": 2, "2023": 2}
    assert [work["source_ids"] for work in branch["works"]] == [
        work["source_ids"] for work in second["branches"][0]["works"]]


def test_multibranch_compound_groups_require_both_concepts_and_preserve_literal_path(tmp_path):
    path = tmp_path / "train-00000-of-00001.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("2401.00001", "Neuromorphic chips", "Inference on edge devices", date(2024, 1, 1)),
        row("2401.00002", "Neuromorphic chips", "Cloud deployment", date(2024, 1, 2)),
        row("2401.00003", "Edge devices", "Conventional processors", date(2024, 1, 3)),
        row("2401.00004", "Узкая русская тема", "Original literal path", date(2024, 1, 4)),
        row("2401.00005", "Neuromorphic chips survey", "Edge devices", date(2024, 1, 5)),
    ]), path)
    validate_inventory.cache_clear()
    spec = {"branch_id": "original-query", "included_phrases": ["узкая русская тема"],
            "concept_groups": [["neuromorphic chips"], ["edge devices"]],
            "excluded_phrases": ["survey"]}
    result = scan(tmp_path, [spec], date(2024, 1, 1), date(2025, 1, 1), 10,
                  expected_files=1, expected_rows=5)
    branch = result["branches"][0]
    assert branch["eligible_matches"] == 2
    assert {work["source_ids"][0] for work in branch["works"]} == {
        "https://arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00004"}
    assert branch["concept_groups"] == spec["concept_groups"]
