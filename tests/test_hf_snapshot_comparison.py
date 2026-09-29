import json

import pytest

from saia.hf_snapshot_comparison import (
    compare, normalize_arxiv_id, normalized_text_sha256, read_hf_jsonl,
    version_created_day,
)


def test_hf_jsonl_inventory_and_version_normalization(tmp_path):
    path = tmp_path / "matched.jsonl"
    path.write_text(json.dumps({
        "id": "arXiv:1601.00001v2", "created": "2016-01-01",
        "updated": "2017-01-01", "title": "Title", "summary": "Abstract",
        "categories": ["cs.LG", "stat.ML"],
    }) + "\n", encoding="utf-8")
    records, inventory = read_hf_jsonl(path)
    assert set(records) == {"1601.00001"}
    assert inventory["valid_rows"] == 1
    assert inventory["created_range"] == ["2016-01-01", "2016-01-01"]
    assert inventory["category_counts"] == {"cs.LG": 1, "stat.ML": 1}


def test_hf_comparison_imports_nothing_when_full_mirror_has_all_ids():
    records = {"1601.00001": {"created": "2016-01-01", "updated": "2017-01-01"}}
    inventory = {"valid_rows": 1}
    full = {"1601.00001": {"first_version_date": "2016-01-01",
                            "last_version_date": "2017-01-01"}}
    result = compare(records, inventory, full, {}, "revision", "selection")
    assert result["comparison"]["full_mirror_matches"] == 1
    assert result["decision"]["records_imported"] == 0
    assert result["decision"]["incremental_value_proven"] is False
    assert result["decision"]["recommendation"] == "do_not_import_duplicate_snapshot"
    assert result["potential_coverage"]["new_records_vs_full_mirror"] == 0
    assert result["potential_coverage"]["outside_selection_is_incremental_corpus_evidence"] is False


def test_hf_comparison_exposes_missing_id_without_auto_import():
    records = {"1601.00001": {"created": "2016-01-01", "updated": "2017-01-01"}}
    result = compare(records, {"valid_rows": 1}, {}, {}, "revision", "selection")
    assert result["comparison"]["new_vs_full_mirror"] == ["1601.00001"]
    assert result["decision"]["records_imported"] == 0
    assert result["decision"]["recommendation"] == "review_missing_ids_before_any_import"


def test_hf_text_audit_is_optional_and_does_not_authorize_import():
    text_hash = normalized_text_sha256("A  title", "An\nabstract")
    records = {
        "1601.00001": {
            "created": "2016-01-01",
            "updated": "2017-01-01",
            "text_sha256": text_hash,
        },
        "1601.00002": {
            "created": "2016-01-02",
            "updated": "2017-01-02",
            "text_sha256": text_hash,
        },
    }
    full = {
        "1601.00001": {
            "first_version_date": "2016-01-01",
            "last_version_date": "2017-01-01",
            "text_sha256": normalized_text_sha256("A title", "An abstract"),
        },
        "1601.00002": {
            "first_version_date": "2016-01-02",
            "last_version_date": "2017-01-02",
            "text_sha256": normalized_text_sha256("Different", "text"),
        },
    }
    result = compare(
        records, {"valid_rows": 2}, full, {}, "revision", "selection",
        compare_text=True,
    )
    assert result["version"] == "hf-snapshot-comparison-0.4.23-text-audit"
    assert result["comparison"]["title_abstract_text_changed"] == ["1601.00002"]
    assert result["comparison"]["title_abstract_text_unavailable"] == []
    assert result["decision"]["records_imported"] == 0
    assert result["decision"]["full_revectorization_started"] is False


def test_rfc_arxiv_version_date_is_compared_to_hf_day():
    assert version_created_day("Fri, 29 Jan 2016 11:13:46 GMT") == "2016-01-29"
    with pytest.raises(ValueError, match="versions.created"):
        version_created_day("not a date")


@pytest.mark.parametrize("value", ["", "bad id", "arXiv: "])
def test_invalid_arxiv_id_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_arxiv_id(value)
