import pyarrow as pa
import pyarrow.parquet as pq

from saia.negative_primary_queue import collect_candidates, sampling_stratum


def write_rows(path, rows):
    schema = pa.schema([
        ("id", pa.string()), ("title", pa.string()), ("abstract", pa.string()),
        ("categories", pa.string()),
        ("versions", pa.list_(pa.struct([("version", pa.string()), ("created", pa.string())]))),
        ("license", pa.string()),
    ])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def row(identifier, title):
    return {
        "id": identifier, "title": title, "abstract": "An abstract with evidence.",
        "categories": "cs.AI cs.LG",
        "versions": [{"version": "v1", "created": "Mon, 01 Jan 2024 00:00:00 GMT"}],
        "license": "http://creativecommons.org/licenses/by/4.0/",
    }


def test_patterns_are_sampling_strata_not_labels():
    assert sampling_stratum("A comprehensive review") == "review_or_survey_candidate"
    assert sampling_stratum("A new framework") == "framework_or_perspective_candidate"
    assert sampling_stratum("A benchmark") == "benchmark_or_taxonomy_candidate"
    assert sampling_stratum("A primary experiment") is None


def test_queue_is_deterministic_preliminary_and_source_bound(tmp_path):
    path = tmp_path / "part.parquet"
    write_rows(path, [
        row("2401.00001", "A review"),
        row("2401.00002", "A framework"),
        row("2401.00003", "A benchmark"),
        row("2401.00004", "An experiment"),
    ])
    first = collect_candidates([path], "revision", 1)
    second = collect_candidates([path], "revision", 1)
    assert first == second
    assert len(first["items"]) == 3
    assert first["not_gold_standard"] is True
    assert first["production_g6_calibration_allowed"] is False
    assert all(item["primary_result_label"] is None for item in first["items"])
    assert first["source"]["file_inventory"][0]["bytes_sha256"]


def test_queue_caps_each_stratum(tmp_path):
    path = tmp_path / "part.parquet"
    write_rows(path, [row(f"2401.{index:05d}", "A review") for index in range(4)])
    report = collect_candidates([path], "revision", 2)
    assert report["sampling"]["available_by_stratum"]["review_or_survey_candidate"] == 4
    assert report["sampling"]["selected_by_stratum"]["review_or_survey_candidate"] == 2
