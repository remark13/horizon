from datetime import date

import pytest
from fastapi.testclient import TestClient

from saia.api import app
from saia.measurement import WindowCounts, analyze_series


def annual(counts, denominators):
    return [WindowCounts(date(2012+i, 1, 1), date(2013+i, 1, 1), n, total,
                         coverage_comparable=True)
            for i, (n, total) in enumerate(zip(counts, denominators))]


def test_flat_new_publications_are_not_growth():
    result = analyze_series(annual([10]*4, [100]*4), date(2016, 1, 1))
    assert result['direction'] == 'flat_or_mixed'
    assert result['share_slope_per_window'] == 0
    assert result['consecutive_active_full_windows'] == 4


def test_increasing_field_can_make_flat_topic_decrease():
    result = analyze_series(annual([10]*4, [100, 200, 300, 400]), date(2016, 1, 1))
    assert result['direction'] == 'decreasing'
    assert result['absolute_count_change'] == 0
    assert result['share_change'] < 0


def test_gap_breaks_current_persistence():
    result = analyze_series(annual([2, 0, 0, 2], [100]*4), date(2016, 1, 1))
    assert result['consecutive_active_full_windows'] == 1


def test_absent_denominator_is_not_zero():
    result = analyze_series(annual([1, 2, 3, 4], [100, None, 100, 100]), date(2016, 1, 1))
    assert result['points'][1]['share'] is None
    assert result['direction'] == 'not_established'
    assert result['share_slope_per_window'] is None


def test_coverage_break_prevents_growth_interpretation():
    windows = annual([1, 2, 3, 4], [100]*4)
    windows[-1] = WindowCounts(windows[-1].start, windows[-1].end, 4, 100,
                              coverage_comparable=False)
    result = analyze_series(windows, date(2016, 1, 1))
    assert result['coverage_comparable'] is False
    assert result['direction'] == 'not_established'
    assert result['consecutive_active_full_windows'] is None
    assert result['observed_consecutive_active_full_windows'] == 4


def test_partial_final_window_is_visible_but_not_compared():
    windows = annual([10]*4, [100]*4)
    windows.append(WindowCounts(date(2016, 1, 1), date(2016, 5, 1), 50, 100, complete=False))
    result = analyze_series(windows, date(2016, 5, 1))
    assert result['points'][-1]['share'] == .5
    assert result['direction'] == 'flat_or_mixed'
    assert result['partial_window_excluded_from_comparison'] is True


def test_unknown_older_coverage_does_not_invalidate_streak_after_verified_gap():
    windows = annual([1, 0, 2, 3], [100]*4)
    windows[0] = WindowCounts(windows[0].start, windows[0].end, 1, 100,
                              coverage_comparable=False)
    result = analyze_series(windows, date(2016, 1, 1))
    assert result['consecutive_active_full_windows'] == 2
    assert result['observed_consecutive_active_full_windows'] == 2


def test_only_partial_window_has_no_established_full_window_streak():
    result = analyze_series([WindowCounts(date(2016, 1, 1), date(2016, 5, 1),
                                         4, 100, complete=False)], date(2016, 5, 1))
    assert result['consecutive_active_full_windows'] is None
    assert result['observed_consecutive_active_full_windows'] == 0


def test_small_sample_interval_does_not_prove_growth():
    result = analyze_series(annual([1, 1, 2, 2], [100]*4), date(2016, 1, 1))
    assert result['direction'] == 'increasing'
    assert result['share_change_interval'][0] < 0 < result['share_change_interval'][1]
    assert 'status' not in result


def test_future_window_rejected():
    with pytest.raises(ValueError, match='не позже'):
        analyze_series(annual([10]*4, [100]*4), date(2015, 5, 1))


def test_gaps_must_be_represented_explicitly():
    windows = annual([1]*4, [100]*4)
    with pytest.raises(ValueError, match='подряд'):
        analyze_series([windows[0], windows[2]], date(2016, 1, 1))


def test_api_measurement_returns_version_and_interpretation():
    with TestClient(app) as client:
        response = client.post('/analysis/series', json={
            'as_of_date': '2016-01-01', 'windows': [
                {'start': str(w.start), 'end': str(w.end), 'topic_works': w.topic_works,
                 'corpus_works': w.corpus_works,
                 'coverage_comparable': True} for w in annual([10]*4, [100]*4)]})
        assert response.status_code == 200
        assert response.json()['measurement_version'] == '0.4-measurement-2'
        assert response.json()['direction'] == 'flat_or_mixed'
        assert response.json()['policy_hash']


def test_api_does_not_allow_topic_larger_than_corpus():
    with TestClient(app) as client:
        response = client.post('/analysis/series', json={
            'as_of_date': '2016-01-01', 'windows': [
                {'start': '2015-01-01', 'end': '2016-01-01', 'topic_works': 5,
                 'corpus_works': 4}]})
        assert response.status_code == 422


@pytest.mark.parametrize('state', [None, False, True])
def test_coverage_contract_preserves_unknown_failed_and_passed(state):
    windows = [WindowCounts(w.start, w.end, w.topic_works, w.corpus_works,
                            coverage_comparable=state)
               for w in annual([1, 2, 3, 4], [100]*4)]
    result = analyze_series(windows, date(2016, 1, 1))
    assert result['coverage_comparable'] is state
    assert all(p['coverage_comparable'] is state for p in result['points'])
    assert result['direction'] == ('increasing' if state is True else 'not_established')


def test_omitted_coverage_is_unknown_in_api_and_dataclass():
    assert WindowCounts(date(2015, 1, 1), date(2016, 1, 1), 1, 100).coverage_comparable is None
    with TestClient(app) as client:
        result = client.post('/analysis/series', json={
            'as_of_date': '2016-01-01', 'windows': [
                {'start': str(w.start), 'end': str(w.end), 'topic_works': w.topic_works,
                 'corpus_works': w.corpus_works} for w in annual([1, 2, 3, 4], [100]*4)]}).json()
    assert result['coverage_comparable'] is None
    assert result['direction'] == 'not_established'
    assert result['observed_consecutive_active_full_windows'] == 4
    assert result['consecutive_active_full_windows'] is None


def test_failed_coverage_dominates_unknown_without_erasing_points():
    windows = annual([1, 2, 3, 4], [100]*4)
    windows[0] = WindowCounts(windows[0].start, windows[0].end, 1, 100)
    windows[-1] = WindowCounts(windows[-1].start, windows[-1].end, 4, 100,
                              coverage_comparable=False)
    result = analyze_series(windows, date(2016, 1, 1))
    assert result['coverage_comparable'] is False
    assert result['points'][0]['coverage_comparable'] is None


@pytest.mark.parametrize('coverage', [None, False, True])
def test_observed_count_growth_does_not_need_denominators(coverage):
    windows = [WindowCounts(w.start, w.end, w.topic_works, None, coverage_comparable=coverage)
               for w in annual([1, 2, 3, 4], [100]*4)]
    result = analyze_series(windows, date(2016, 1, 1))
    obs = result['observed_sample']
    assert obs['count']['change'] == 3 and obs['count']['direction'] == 'increasing'
    assert not obs['share']['available']
    assert result['absolute_count_change'] is None and result['direction'] == 'not_established'
    assert obs['scientific_signal_confirmed'] is None
    assert len(obs['policy_sha256']) == 64
    assert obs['policy']['required_history_windows'] == 4
    assert obs['policy']['max_calendar_duration_difference_days'] == 3


def test_observed_count_and_share_can_move_oppositely_with_unknown_coverage():
    windows = [WindowCounts(w.start, w.end, w.topic_works, w.corpus_works)
               for w in annual([1, 2, 3, 4], [10, 40, 90, 160])]
    obs = analyze_series(windows, date(2016, 1, 1))['observed_sample']
    assert obs['count']['direction'] == 'increasing'
    assert obs['share']['direction'] == 'decreasing'
    assert obs['scope'] == 'retrieved_sample_only'


def test_observed_zero_baseline_never_becomes_infinite_growth():
    obs = analyze_series(annual([0, 1, 2, 3], [100]*4), date(2016, 1, 1))['observed_sample']
    assert obs['count']['change'] == 3
    assert obs['count']['relative_change'] is None
    assert obs['count']['relative_change_note'] == 'zero_baseline'


def test_observed_comparison_requires_history_and_same_calendar_scale():
    result = analyze_series(annual([1, 2], [100]*2), date(2016, 1, 1))
    assert result['observed_sample']['count']['reason'] == 'insufficient_full_windows'
    windows = [WindowCounts(date(2015, 1, 1), date(2015, 2, 1), 1, None),
               WindowCounts(date(2015, 2, 1), date(2015, 3, 1), 2, None),
               WindowCounts(date(2015, 3, 1), date(2015, 4, 1), 3, None),
               WindowCounts(date(2015, 4, 1), date(2016, 1, 1), 4, None)]
    assert analyze_series(windows, date(2016, 1, 1))['observed_sample']['count']['reason'] == 'incomparable_window_durations'


def test_observed_monthly_series_excludes_partial_last_window():
    windows = [WindowCounts(date(2026, m, 1), date(2026, m+1, 1), m, None) for m in range(1, 5)]
    windows.append(WindowCounts(date(2026, 5, 1), date(2026, 5, 10), 99, None, complete=False))
    result = analyze_series(windows, date(2026, 5, 10))
    assert result['observed_sample']['count']['change'] == 3
    assert result['observed_sample']['count']['direction'] == 'increasing'
