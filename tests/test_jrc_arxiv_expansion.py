from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.jrc_arxiv_expansion import (
    CONFIG_PATH, load_inputs, ordered_gap_pattern, scan_paths,
)


@pytest.mark.local_data
def test_expansion_config_pins_baseline_and_forbids_manual_aliases():
    config, baseline, signals = load_inputs(CONFIG_PATH)
    assert baseline["version"] == "jrc-arxiv-lexical-observability-0.4.24-r4"
    assert len(signals) == 25
    assert config["expansion"]["manual_aliases"] == []
    assert config["interpretation"]["production_adoption_allowed"] is False
    assert config["interpretation"]["precision_measured"] is False


def test_ordered_gap_pattern_is_generic_and_order_sensitive():
    pattern = ordered_gap_pattern("Self supervised learning CNN", 3)
    assert "self" in pattern and "cnn" in pattern
    assert "{0,3}" in pattern


def test_scan_recomputes_exact_and_collects_unlabelled_expansion_sample(tmp_path):
    rows = [
        {
            "id": "2001.00001",
            "title": "Self supervised learning CNN",
            "abstract": "Exact.",
            "categories": "cs.LG",
            "versions": [{"version": "v1", "created": "Wed, 01 Jan 2020 00:00:00 GMT"}],
        },
        {
            "id": "2001.00002",
            "title": "Self supervised learning for robust CNN models",
            "abstract": "Expanded.",
            "categories": "cs.CV",
            "versions": [{"version": "v1", "created": "Thu, 02 Jan 2020 00:00:00 GMT"}],
        },
        {
            "id": "2001.00003",
            "title": "CNN with supervised self learning",
            "abstract": "Wrong order.",
            "categories": "cs.CV",
            "versions": [{"version": "v1", "created": "Fri, 03 Jan 2020 00:00:00 GMT"}],
        },
    ]
    path = tmp_path / "part.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    signals = [{
        "source_number": 91,
        "signal_original": "Self supervised learning CNN",
        "signal_ru": "Самообучение в CNN",
        "cluster_original": "Artificial Intelligence & Machine Learning",
        "description_present_in_supplied_source": False,
        "baseline_exact_matches": 1,
    }]
    result = scan_paths(
        [path], signals, date(1996, 1, 1), date(2024, 1, 1), 3, 5
    )
    item = result["results"][0]
    assert item["exact_matches"] == 1
    assert item["expanded_matches"] == 2
    assert item["expansion_only_matches"] == 1
    assert item["expansion_only_review_sample"][0]["arxiv_id"] == "2001.00002"
    assert item["expansion_only_review_sample"][0]["review_label"] is None
