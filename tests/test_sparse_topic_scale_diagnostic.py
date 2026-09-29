from datetime import date

import pytest

from scripts.diagnose_sparse_topic_scale import family_representative_indices


def test_representative_is_earliest_and_all_source_ids_are_retained_in_audit():
    records = [
        (20, "review v2", "", date(2024, 7, 10)),
        (30, "journal", "", date(2024, 7, 29)),
        (10, "review v1", "", date(2024, 7, 1)),
        (40, "different result", "", date(2024, 8, 1)),
    ]
    indices, audit = family_representative_indices(
        records, [{"work_ids": [20, 30, 10]}])
    assert indices == [2, 3]
    assert audit == [{"representative_work_id": 10,
                      "all_source_work_ids": [10, 20, 30],
                      "omitted_from_sensitivity_only": [20, 30]}]


def test_representative_rejects_inconsistent_families():
    records = [(10, "a", "", date(2024, 1, 1)),
               (20, "b", "", date(2024, 1, 2)),
               (30, "c", "", date(2024, 1, 3))]
    with pytest.raises(ValueError, match="Overlapping"):
        family_representative_indices(records, [
            {"work_ids": [10, 20]}, {"work_ids": [20, 30]}])
    with pytest.raises(ValueError, match="Overlapping"):
        family_representative_indices(records, [
            {"work_ids": [10, 20]}, {"work_ids": [10, 30]}])
    with pytest.raises(ValueError, match="does not match"):
        family_representative_indices(records, [{"work_ids": [10, 99]}])
