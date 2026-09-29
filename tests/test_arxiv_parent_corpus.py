from collections import Counter
from datetime import date

from saia.arxiv_parent_corpus import linear_slope, month_key, series


def test_parent_series_keeps_zero_months_and_explicit_denominator():
    points = series(
        Counter({'2024-09': 100, '2024-10': 200}),
        Counter({'2024-09': 2}),
        date(2024, 9, 1),
        date(2024, 11, 1),
    )
    assert points == [
        {
            'start': '2024-09-01',
            'end': '2024-10-01',
            'phrase_scope_works': 2,
            'parent_category_works': 100,
            'share_within_frozen_category_snapshot': .02,
        },
        {
            'start': '2024-10-01',
            'end': '2024-11-01',
            'phrase_scope_works': 0,
            'parent_category_works': 200,
            'share_within_frozen_category_snapshot': 0,
        },
    ]


def test_month_key_is_calendar_month_not_string_slice():
    assert month_key('2024-02-29') == '2024-02'


def test_full_period_slope_is_descriptive_and_deterministic():
    assert linear_slope([1, 2, 3, 4]) == 1
    assert linear_slope([4, 3, 2, 1]) == -1
    assert linear_slope([1]) is None
