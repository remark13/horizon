from datetime import date

import pytest

from saia.composition_projection import (
    _collapse_families,
    _counts_by_window,
    _series,
)


POINTS = [
    {"start": "2025-01-01", "end": "2025-02-01", "topic_works": 1,
     "corpus_works": 10, "complete": True, "coverage_comparable": True},
    {"start": "2025-02-01", "end": "2025-03-01", "topic_works": 0,
     "corpus_works": 10, "complete": True, "coverage_comparable": True},
    {"start": "2025-03-01", "end": "2025-04-01", "topic_works": 1,
     "corpus_works": 10, "complete": True, "coverage_comparable": True},
    {"start": "2025-04-01", "end": "2025-05-01", "topic_works": 1,
     "corpus_works": 10, "complete": True, "coverage_comparable": True},
]


def work(work_id, value):
    return {"work_id": work_id, "effective_date": value}


def test_counts_and_series_use_exact_saved_calendar_windows():
    works = [work(1, "2025-01-15"), work(2, "2025-03-02"), work(3, "2025-04-30")]
    assert _counts_by_window(POINTS, works) == [1, 0, 1, 1]
    result = _series(POINTS, works, date(2025, 5, 1))
    assert result["absolute_count_change"] == 0
    assert result["observed_sample"]["scientific_signal_confirmed"] is None


def test_projection_rejects_work_outside_saved_windows():
    with pytest.raises(ValueError, match="выходят"):
        _counts_by_window(POINTS, [work(1, "2024-12-31")])


def test_declared_family_counts_once_at_earliest_observation():
    works = [work(1, "2025-04-01"), work(2, "2025-03-01"), work(3, "2025-04-20")]
    collapsed, actions = _collapse_families(works, [[1, 2]])
    assert [item["work_id"] for item in collapsed] == [2, 3]
    assert actions == [{
        "work_ids": [1, 2],
        "counted_once_at": "2025-03-01",
        "representative_work_id": 2,
    }]


def test_missing_family_members_do_not_create_a_merge():
    works = [work(1, "2025-04-01"), work(3, "2025-04-20")]
    collapsed, actions = _collapse_families(works, [[1, 2]])
    assert [item["work_id"] for item in collapsed] == [1, 3]
    assert actions == []
