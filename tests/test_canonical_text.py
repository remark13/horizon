"""Native abstract preference is a source binding rule, not truth detection."""

from copy import deepcopy

import pytest

from saia import canonical_text as module


def candidates():
    return [
        {"raw_record_id": 1, "source": "openalex", "source_record_id": "W1",
         "title_key": "native method", "abstract": "Unrelated aggregate text",
         "arxiv_ids": ["1601.00001"], "metadata": {}},
        {"raw_record_id": 2, "source": "arxiv", "source_record_id": "1601.00001",
         "title_key": "native method", "abstract": "Original method abstract",
         "arxiv_ids": ["1601.00001"],
         "metadata": {"id": "1601.00001", "created": "2016-01-01", "updated": "2016-02-01"}},
    ]


def select(rows, mode=module.VERSION):
    return module.select_abstract(rows, canonical_title_key="native method",
                                  cutoff="2017-01-01", mode=mode)


def test_bound_native_preferred_without_mutating_raw_candidates():
    rows = candidates()
    frozen = deepcopy(rows)
    result = select(rows)
    assert result["abstract"] == "Original method abstract"
    assert result["chosen_raw_record_id"] == 2 and result["chosen_source"] == "arxiv"
    assert result["chosen_historical_text_available"] is True
    assert result["changed_from_original_first_abstract"] is True
    assert result["different_abstract_values"] is True
    assert result["abstract_correctness_verified"] is None
    assert result["abstract_sha256"] == module.text_sha256(rows[1]["abstract"])
    assert rows == frozen


@pytest.mark.parametrize("problem", ["missing_revision", "unknown_revision", "future_revision",
                                     "cutoff_revision", "wrong_title", "wrong_id", "wrong_order"])
def test_native_preference_fails_closed_on_missing_or_conflicting_binding(problem):
    rows = candidates()
    native = rows[1]
    if problem == "missing_revision":
        native["metadata"].pop("updated")
    elif problem == "unknown_revision":
        native["metadata"]["updated"] = "unknown"
    elif problem == "future_revision":
        native["metadata"]["updated"] = "2018-01-01"
    elif problem == "cutoff_revision":
        native["metadata"]["updated"] = "2017-01-01"
    elif problem == "wrong_title":
        native["title_key"] = "different method"
    elif problem == "wrong_id":
        native["metadata"]["id"] = "1601.00002"
    else:
        native["metadata"]["updated"] = "2015-12-31"
    result = select(rows)
    assert result["chosen_raw_record_id"] == 1
    assert result["basis"] == "original_source_order_fallback"
    assert result["changed_from_original_first_abstract"] is False


def test_conflicting_canonical_arxiv_identity_blocks_native_preference():
    rows = candidates()
    rows[0]["arxiv_ids"].append("1601.00002")
    assert select(rows)["chosen_raw_record_id"] == 1


def test_legacy_source_order_and_input_order_do_not_change_choice():
    rows = candidates()
    assert select(rows, module.LEGACY)["chosen_raw_record_id"] == 1
    assert select(list(reversed(rows)), module.LEGACY) == select(rows, module.LEGACY)
    assert select(list(reversed(rows))) == select(rows)


def test_no_abstract_has_no_source_or_checksum():
    rows = candidates()
    for row in rows:
        row["abstract"] = None
    result = select(rows)
    assert result["abstract"] is None and result["abstract_sha256"] is None
    assert result["chosen_raw_record_id"] is None
    assert result["basis"] == "no_available_abstract"


def test_native_version_history_defines_current_text_revision():
    rows = candidates()
    rows[1]["metadata"]["_arxiv_versions"] = [
        {"version": "v1", "created": "Fri, 1 Jan 2016 10:00:00 GMT"},
        {"version": "v2", "created": "Mon, 1 Feb 2016 10:00:00 GMT"},
    ]
    result = select(rows)
    assert result["chosen_raw_record_id"] == 2
    assert result["candidates"][1]["revision_date_basis"] == "native_version_history"
    rows[1]["metadata"]["updated"] = "2016-03-01"
    assert select(rows)["chosen_raw_record_id"] == 1


@pytest.mark.parametrize("bad_history", [[], [{"version": "v2", "created": "Mon, 1 Feb 2016 10:00:00 GMT"}],
                                        [{"version": "v1", "created": "unknown"}]])
def test_invalid_history_cannot_fall_back_to_updated_for_native_preference(bad_history):
    rows = candidates()
    rows[1]["metadata"]["_arxiv_versions"] = bad_history
    assert select(rows)["chosen_raw_record_id"] == 1


def test_same_text_changes_origin_without_claiming_quality_improvement():
    rows = candidates()
    rows[1]["abstract"] = rows[0]["abstract"]
    result = select(rows)
    assert result["chosen_raw_record_id"] == 2
    assert result["changed_from_original_first_abstract"] is False
    assert result["different_abstract_values"] is False
    assert result["abstract_correctness_verified"] is None


def test_native_only_unknown_revision_is_retained_but_not_historically_verified():
    rows = candidates()[1:]
    rows[0]["metadata"].pop("updated")
    result = select(rows)
    assert result["abstract"] == "Original method abstract"
    assert result["chosen_historical_text_available"] is None
    assert result["basis"] == "original_source_order_fallback"


def test_native_only_future_text_remains_explicitly_unavailable_before_cutoff():
    rows = candidates()[1:]
    rows[0]["metadata"]["updated"] = "2018-01-01"
    result = select(rows)
    assert result["abstract"] == "Original method abstract"
    assert result["chosen_historical_text_available"] is False
    assert result["basis"] == "original_source_order_fallback"


@pytest.mark.parametrize("value,expected", [
    ("https://arxiv.org/abs/1601.00001v2", "1601.00001"),
    ("https://export.arxiv.org/pdf/1601.00001v2.pdf", "1601.00001"),
    ("cs/0601001v3", "cs/0601001"),
    ("https://other.example/abs/1601.00001", None),
])
def test_native_id_is_strict_and_preserves_legacy_ids(value, expected):
    assert module.native_id(value) == expected


def test_most_recent_eligible_native_text_with_same_identity_is_selected():
    rows = candidates()
    later = deepcopy(rows[1])
    later.update(raw_record_id=3, abstract="Later native revision")
    later["metadata"]["updated"] = "2016-03-01"
    rows.append(later)
    assert select(rows)["chosen_raw_record_id"] == 3
    later["metadata"]["updated"] = "2017-03-01"
    assert select(rows)["chosen_raw_record_id"] == 2


def test_legacy_mirror_inferred_updated_does_not_prove_historical_text():
    rows = candidates()
    rows[1]["metadata"].update(_source_format="hf-arxiv-parquet-snapshot",
                               _submission_date_raw="1 Jan 2016", updated="2016-01-01")
    rows[1]["observed_at"] = "2026-09-14T10:00:00+00:00"
    result = select(rows)
    assert result["chosen_raw_record_id"] == 1
    assert result["candidates"][1]["revision_date_basis"] == "legacy_snapshot_revision_not_verified"
    assert result["candidates"][1]["historical_text_available"] is False


def test_record_captured_before_cutoff_dates_text_without_inventing_revision():
    rows = candidates()
    rows[1]["metadata"].pop("updated")
    rows[1]["observed_at"] = "2016-02-01T12:00:00+00:00"
    result = select(rows)
    assert result["chosen_raw_record_id"] == 2
    assert result["candidates"][1]["text_revision_date"] is None
    assert result["candidates"][1]["historical_text_date_basis"] == "source_capture"


def test_future_revision_cannot_be_hidden_by_earlier_capture():
    rows = candidates()
    rows[1]["metadata"]["updated"] = "2018-01-01"
    rows[1]["observed_at"] = "2016-02-01T12:00:00+00:00"
    assert select(rows)["chosen_raw_record_id"] == 1


def test_conflicting_dates_cannot_use_source_capture_to_gain_native_preference():
    rows = candidates()
    rows[1]["metadata"]["_arxiv_versions"] = [
        {"version": "v1", "created": "Fri, 1 Jan 2016 10:00:00 GMT"},
    ]
    rows[1]["metadata"]["updated"] = "2018-01-01"
    rows[1]["observed_at"] = "2016-02-01T12:00:00+00:00"
    result = select(rows)
    assert result["chosen_raw_record_id"] == 1
    assert result["candidates"][1]["historical_text_available"] is None
    assert result["candidates"][1]["historical_text_date_basis"] == "conflicting_metadata"


def test_invalid_policy_duplicate_candidate_or_nontext_abstract_rejected():
    with pytest.raises(ValueError, match="policy"):
        select(candidates(), "unknown")
    rows = candidates()
    with pytest.raises(ValueError, match="Duplicate"):
        select([rows[0], rows[0]])
    rows[1]["abstract"] = 0
    with pytest.raises(ValueError, match="text or null"):
        select(rows)
