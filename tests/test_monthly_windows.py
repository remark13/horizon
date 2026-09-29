from datetime import date

import pytest
import yaml

from saia.cluster import window_of, window_bounds, next_window, contiguous_windows
from saia.terminology import PublicationText, generate
from pathlib import Path


def test_month_grid_across_year_and_leap_year():
    assert window_of(date(2024, 2, 29), 'month') == '2024-02'
    assert window_bounds('2024-02', 'month') == (date(2024, 2, 1), date(2024, 2, 29))
    assert next_window('2025-12', 'month') == '2026-01'
    assert contiguous_windows('2025-11', '2026-02', 'month') == ['2025-11', '2025-12', '2026-01', '2026-02']
    with pytest.raises(ValueError):
        contiguous_windows('2026-02', '2025-11', 'month')


@pytest.mark.parametrize('key', ['2026-13', '2026-00', '2026-1', '2026Q1'])
def test_invalid_month_key_refused(key):
    with pytest.raises(ValueError):
        window_bounds(key, 'month')


def test_monthly_lexical_series_preserves_empty_start_and_unknown_coverage():
    policy = yaml.safe_load((Path(__file__).resolve().parents[1]/'config/terminology.monthly-pilot.v0.4.7.yaml').read_text())
    docs = [PublicationText(i, date(2026, i+2, 2), 'Sparse annotation budget', 'Useful sparse annotation budget')
            for i in range(1, 4)]
    result = generate(docs, date(2026, 6, 1), step='month', coverage_comparable=None, policy=policy,
                      period_from=date(2026, 1, 1), period_end=date(2026, 6, 1))
    card = next(c for c in result['candidates'] if c['phrase'] == 'annotation budget')
    points = card['publication_series']['points']
    assert [p['topic_works'] for p in points] == [0, 0, 1, 1, 1]
    assert card['publication_series']['coverage_comparable'] is None
    assert card['publication_series']['observed_sample']['count']['direction'] == 'increasing'
    assert card['publication_series']['direction'] == 'not_established'
    assert result['window_step'] == 'month'
    for context in card['contexts']:
        doc = next(d for d in docs if d.work_id == context['work_id'])
        assert getattr(doc, context['field'])[context['start']:context['end']] == context['matched_text']
