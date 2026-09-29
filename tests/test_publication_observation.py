from copy import deepcopy
from datetime import date

import pytest

from saia import composition_assessment as ca, hybrid
from saia.measurement import WindowCounts, analyze_series
from saia.publication_observation import load_policy, observe
from saia.terminology import PublicationText


def candidate(counts=(5, 6, 10, 20), totals=(10000,) * 4, coverage=False):
    windows = [WindowCounts(date(2013+i, 1, 1), date(2014+i, 1, 1), n, total,
                            coverage_comparable=coverage) for i, (n, total) in enumerate(zip(counts, totals))]
    return {'document_support': sum(counts), 'publication_series': analyze_series(windows, date(2017, 1, 1))}


def portfolio_fixture():
    docs = [
        PublicationText(
            i,
            date(2010 + i, 2, 1),
            f'Result {i}',
            'A research abstract',
        )
        for i in range(1, 4)
    ]
    candidates = hybrid.fuse(
        [],
        [{'topic_id': 1, 'label': 'A line', 'work_ids': [1, 2, 3]}],
        {1, 2, 3},
    )
    hybrid.add_series(
        candidates,
        docs,
        date(2017, 1, 1),
        date(2010, 1, 1),
        date(2017, 1, 1),
        'year',
        False,
    )
    snapshot = {
        'snapshot_id': 'publication-observation-fixture',
        'as_of_date': '2017-01-01',
        'period_from': '2010-01-01',
        'period_end_exclusive': '2017-01-01',
        'window_step': 'year',
        'eligible_work_ids': [1, 2, 3],
        'input_text_hash': ca.text_hash(docs),
        'provenance': {'embedding_model': 'fixture'},
        'candidates': candidates,
        'limitations': [],
    }
    evidence = {
        i: {
            'vector': [1.0, i / 10],
            'source_quality': 0.9,
            'authors': [],
            'organisations': [],
        }
        for i in [1, 2, 3]
    }
    return snapshot, ca.assess(snapshot, docs, evidence)


def test_unknown_validation_does_not_erase_observed_growth_or_promote_confirmation():
    c = candidate(); before = deepcopy(c)
    assert c['publication_series']['share_slope_per_window'] is None
    result = observe(c, load_policy())
    assert result['stage'] == 'publication_signal_candidate'
    assert result['observed']['share_slope_per_window'] > 0
    assert result['observed']['count_change'] == 15
    assert result['map_position'] is not None and not result['coverage_verified']
    assert result['validation']['confirmed_weak_signal'] is None
    assert result['diffusion']['distinct_author_groups_proxy'] is None
    assert c == before


def test_flat_activity_not_a_signal():
    result = observe(candidate((5, 5, 5, 5)), load_policy())
    assert result['stage'] == 'stable_or_mixed'
    assert result['observed']['count_slope_per_window'] == 0
    assert result['observed']['share_slope_per_window'] == 0


@pytest.mark.parametrize('counts', [(0, 7, 13, 8), (0, 37, 43, 8)])
def test_zero_baseline_can_pass_old_screen_but_recent_decline_is_explicit(counts):
    result = observe(candidate(counts), load_policy())
    # Preserve the historical screen decision, but never hide the contradiction.
    assert result['stage'] == 'publication_signal_candidate'
    t = result['recent_trajectory']
    assert t['last_count_change'] < 0 and t['last_share_change'] < 0
    assert t['positive_long_window_but_recent_count_decline'] is True
    assert t['positive_long_window_but_recent_share_decline'] is True
    assert t['baseline_window_has_zero_topic_works'] is True
    assert any('Недавний спад' in reason for reason in result['limitations'])


def test_recent_trajectory_unknown_is_not_flat_or_zero():
    result = observe(candidate(totals=(None,)*4), load_policy())
    assert result['recent_trajectory']['last_count_change'] is None
    assert result['recent_trajectory']['baseline_window_has_zero_topic_works'] is None


def test_recent_trajectory_is_descriptive_and_never_promotes_validation():
    result = observe(candidate(), load_policy())
    t = result['recent_trajectory']
    assert t['last_count_change'] == 10
    assert t['count_drawdown_from_peak'] == 0
    assert t['baseline_window_has_zero_topic_works'] is False
    assert result['validation']['confirmed_weak_signal'] is None


def test_growth_of_whole_field_is_not_a_signal():
    result = observe(candidate((5, 6, 10, 20), (5000, 6000, 10000, 20000)), load_policy())
    assert result['stage'] == 'stable_or_mixed'
    assert result['observed']['count_change'] > 0 and result['observed']['share_change'] == 0


def test_declining_counts_and_share_are_observed_without_declaring_failed_technology():
    result = observe(candidate((20, 10, 6, 5)), load_policy())
    assert result['stage'] == 'declining_activity'
    assert result['observed']['share_slope_per_window'] < 0
    assert result['validation']['confirmed_weak_signal'] is None


def test_single_spike_does_not_become_a_signal():
    result = observe(candidate((0, 0, 0, 20)), load_policy())
    assert result['stage'] == 'growth_watch'
    assert result['observed']['consecutive_active_windows'] == 1


def test_small_sample_does_not_become_a_signal():
    assert observe(candidate((0, 1, 1, 2)), load_policy())['stage'] == 'growth_watch'


def test_known_single_author_group_is_not_automatic_diffusion():
    result = observe(candidate(), load_policy(), {'independent_teams_proxy': 1})
    assert result['stage'] == 'single_group_growth'
    assert result['diffusion']['temporal_diffusion_direction'] is None


def test_multiple_groups_are_proxy_not_temporal_diffusion():
    result = observe(candidate(), load_policy(), {'independent_teams_proxy': 4, 'independent_orgs_proxy': 3})
    assert result['stage'] == 'publication_signal_candidate'
    assert result['diffusion']['distinct_author_groups_proxy'] == 4
    assert result['diffusion']['temporal_diffusion_direction'] is None


def test_widespread_topic_not_small_early_signal():
    assert observe(candidate(totals=(1000,) * 4), load_policy())['stage'] == 'widespread_topic'


def test_no_denominator_never_invent_zero_or_growth():
    result = observe(candidate(totals=(None,) * 4), load_policy())
    assert result['stage'] == 'insufficient_history'
    assert result['map_position'] is None and result['observed']['share_change'] is None


def test_no_fallback_from_last_empty_corpus_window():
    result = observe(candidate((5, 6, 10, 0), (10000, 10000, 10000, 0)), load_policy())
    assert result['stage'] == 'insufficient_history'
    assert result['observed']['last_window_share'] is None


def test_partial_final_window_excluded():
    c = candidate()
    c['publication_series']['points'].append({'start': '2017-01-01', 'end': '2017-03-01', 'complete': False})
    result = observe(c, load_policy())
    assert result['stage'] == 'publication_signal_candidate'
    assert result['observed']['last_window_works'] == 20


def test_zero_last_count_is_observed_decline_not_unknown():
    result = observe(candidate((20, 10, 5, 0)), load_policy())
    assert result['stage'] == 'declining_activity' and result['observed']['last_window_share'] == 0


@pytest.mark.parametrize('change', ['negative_count', 'denominator', 'share', 'gap', 'internal_partial'])
def test_invalid_frozen_counts_do_not_generate_signals(change):
    c = candidate(); p = c['publication_series']['points'][1]
    if change == 'negative_count': p['topic_works'] = -1
    if change == 'denominator': p['corpus_works'] = 1
    if change == 'share': p['share'] = .5
    if change == 'gap': p['start'] = '2014-02-01'
    if change == 'internal_partial': p['complete'] = False
    with pytest.raises(ValueError): observe(c, load_policy())


def test_observation_policy_hash_changes_without_changing_old_series():
    c = candidate(); p = load_policy(); a = observe(c, p)
    p['max_small_topic_share'] = .001
    b = observe(c, p)
    assert a['policy_hash'] != b['policy_hash']
    assert a['stage'] == 'publication_signal_candidate' and b['stage'] == 'widespread_topic'


def test_portfolio_keeps_strict_status_and_map_separate():
    from saia.portfolio import project
    s, report = portfolio_fixture(); original = deepcopy(report)
    result = project(s, {'report': report, 'assessment_id': 'saved', 'created_at': 'today'})
    assert result['candidates'][0]['assessment'] == original['candidates'][0]['assessment']
    assert report == original
    assert 'publication_observations' in result


def test_portfolio_summary_does_not_hide_recent_declining_candidates():
    from saia.portfolio import project
    snapshot, _ = portfolio_fixture()
    c = snapshot['candidates'][0]
    c.update(candidate((0, 7, 13, 8)))
    c['work_ids'] = list(range(28))
    result = project(snapshot)
    summary = result['publication_observations']
    assert summary['long_window_candidates_with_recent_decline'] == 1
    assert summary['stages']['publication_signal_candidate'] == 1
    assert summary['screening_policy_binding'] == 'live_policy_projection_not_saved_screen'
