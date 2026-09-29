import copy

import pytest

from saia.portfolio import project


def snapshot(slope=None, comparable=False, share=.1):
    return {'snapshot_id': 'frozen', 'provenance': {'normalize_run_id': 1},
            'as_of_date': '2017-01-01', 'period_from': '2010-01-01',
            'period_end_exclusive': '2017-01-01', 'window_step': 'year', 'limitations': [],
            'candidates': [{'candidate_id': 'one', 'label': 'mechanism', 'channels': ['lexical'],
                            'status': 'unassessed_candidate', 'document_support': 2,
                            'first_observed_in_corpus': '2015-01-02', 'unknown_checks': ['independence'],
                            'contexts': [], 'overlap_links': [], 'work_ids': [1, 2],
                            'publication_series': {'share_slope_per_window': slope,
                                'coverage_comparable': comparable, 'window_scale_comparable': True,
                                'points': [{'start': '2016-01-01', 'end': '2017-01-01',
                                            'topic_works': 0 if share == 0 else 1, 'corpus_works': 10,
                                            'share': share, 'share_interval': [.01, .4], 'complete': True}]}}]}


def test_unknown_dynamics_are_not_zero_or_coordinates_and_input_is_unchanged():
    s = snapshot()
    before = copy.deepcopy(s)
    p = project(s)
    assert s == before
    assert p['counts'] == {'candidates': 1, 'map_positioned': 0, 'map_unpositioned': 1}
    row = p['candidates'][0]
    assert row['share'] == .1 and row['map_position'] is None
    assert row['share_slope_per_window'] is None and row['map_missing_reasons']
    assert row['radar_ring'] == 'unassessed'


@pytest.mark.parametrize('slope', [-.2, 0., .2])
def test_valid_growth_preserves_sign_and_zero(slope):
    row = project(snapshot(slope=slope, comparable=True))['candidates'][0]
    assert row['map_position'] == {'x_share': .1, 'y_share_slope': slope}


def test_coverage_unknown_blocks_even_a_supplied_numeric_slope():
    assert project(snapshot(.4, False))['candidates'][0]['map_position'] is None


def test_partial_window_is_not_last_full_window():
    s = snapshot(.01, True)
    s['candidates'][0]['publication_series']['points'].append({
        'start': '2017-01-01', 'end': '2017-03-01', 'complete': False, 'share': .9})
    assert project(s)['candidates'][0]['share'] == .1


def test_latest_unknown_share_does_not_fall_back_to_older_window():
    s = snapshot(.01, True)
    point = copy.deepcopy(s['candidates'][0]['publication_series']['points'][0])
    point.update(start='2017-01-01', end='2018-01-01', share=None, corpus_works=0, topic_works=0)
    s['candidates'][0]['publication_series']['points'].append(point)
    assert project(s)['candidates'][0]['map_position'] is None


@pytest.mark.parametrize('share', [-.1, 1.1, float('nan'), float('inf')])
def test_invalid_share_is_rejected(share):
    with pytest.raises(ValueError, match='доля'):
        project(snapshot(share=share))


def test_zero_share_is_not_unknown():
    assert project(snapshot(0., True, 0.))['candidates'][0]['map_position']['x_share'] == 0.


def test_foreign_status_is_not_inherited():
    s = snapshot()
    s['candidates'][0]['status'] = 'forming'
    with pytest.raises(ValueError, match='неоценённые'):
        project(s)


def test_both_channels_mean_exact_same_composition_not_independent_sources():
    s = snapshot()
    s['candidates'][0]['channels'] = ['lexical', 'semantic']
    assert project(s)['candidates'][0]['radar_sector'] == 'both'


def test_duplicate_candidate_id_is_rejected():
    s = snapshot()
    s['candidates'].append(copy.deepcopy(s['candidates'][0]))
    with pytest.raises(ValueError, match='Повтор'):
        project(s)
