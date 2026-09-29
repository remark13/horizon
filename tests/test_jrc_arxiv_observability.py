from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.jrc_arxiv_observability import (
    CONFIG_PATH, exact_phrase_pattern, load_inputs, normalize_phrase, scan_paths,
)


@pytest.mark.local_data
def test_official_jrc_pdf_and_derived_catalog_are_pinned():
    config, catalog, signals = load_inputs(CONFIG_PATH)
    assert config["official_report"]["official_signal_count"] == 221
    assert config["official_report"]["released_at"] == "2025-02-17"
    assert catalog["counts"]["rows"] == 219
    assert len(signals) == 25
    assert all(not item["description_present_in_supplied_source"] for item in signals)
    assert config["benchmark"]["current_snapshot_text_used"] is True
    assert config["benchmark"]["historical_text_reconstructed"] is False
    assert config["benchmark"]["diagnostic_activeness_comparable_to_jrc"] is False
    assert config["benchmark"]["diagnostic_low_support_is_production_gate"] is False
    assert config["benchmark"]["true_retrieval_recall_claimed"] is False


def test_phrase_normalization_handles_case_and_hyphenation():
    assert normalize_phrase("Privacy-preserving Machine Learning") == (
        "privacy preserving machine learning"
    )
    pattern = exact_phrase_pattern("Privacy-preserving machine learning")
    assert pattern == (
        r"(?:^| )privacy +preserving +machine +learning(?: |$)"
    )


def test_scan_is_strict_date_bounded_observability_not_recall(tmp_path):
    rows = [
        {
            "id": "2001.00001",
            "title": "Privacy-preserving machine learning in hospitals",
            "abstract": "A result.",
            "categories": "cs.LG cs.CR",
            "versions": [{"version": "v1", "created": "Wed, 01 Jan 2020 00:00:00 GMT"}],
        },
        {
            "id": "2001.00002",
            "title": "A different title",
            "abstract": "We study privacy preserving machine learning methods.",
            "categories": "cs.LG",
            "versions": [{"version": "v1", "created": "Thu, 02 Jan 2020 00:00:00 GMT"}],
        },
        {
            "id": "2401.00001",
            "title": "Privacy preserving machine learning after cutoff",
            "abstract": "Late.",
            "categories": "cs.LG",
            "versions": [{"version": "v1", "created": "Mon, 01 Jan 2024 00:00:00 GMT"}],
        },
    ]
    path = tmp_path / "part.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    signals = [{
        "source_number": 89,
        "signal_original": "Privacy-preserving machine learning",
        "signal_ru": "Приватность-сохраняющее машинное обучение",
        "cluster_original": "Artificial Intelligence & Machine Learning",
        "description_present_in_supplied_source": False,
    }]
    result = scan_paths([path], signals, date(1996, 1, 1), date(2024, 1, 1))
    item = result["results"][0]
    assert result["scanned_rows"] == 3
    assert item["title_abstract_matches"] == 2
    assert item["title_matches"] == 1
    assert item["outside_period_matches"] == 1
    assert item["earliest_first_submission_date"] == "2020-01-01"
    assert item["diagnostic_recent_documents"] == 0
    assert item["diagnostic_activeness"] == 0.0
    assert item["diagnostic_activeness_support_documents"] == 2
    assert item["diagnostic_activeness_low_support"] is True
    assert [row["arxiv_id"] for row in item["earliest_examples"]] == [
        "2001.00001", "2001.00002"
    ]
