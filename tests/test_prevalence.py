from saia.candidates import prevalence_ranks


def test_dead_lines_do_not_make_rare_current_topic_look_widespread():
    active = {i: (i+1)/100 for i in range(10)}
    a, count = prevalence_ranks(active, 5, 'midrank', 'active_in_last_full_window')
    extended = dict(active)
    extended.update({i: 0.0 for i in range(10, 1000)})
    b, other_count = prevalence_ranks(extended, 5, 'midrank', 'active_in_last_full_window')
    assert count == other_count == 10
    assert a[0] == b[0] == 5.0
    assert b[100] == 0.0


def test_all_zero_shares_do_not_provide_percentile_peers():
    ranks, count = prevalence_ranks({i: 0.0 for i in range(10)}, 5, 'midrank', 'active_in_last_full_window')
    assert count == 0
    assert all(v is None for v in ranks.values())


def test_one_active_line_is_insufficient_despite_many_historical_lines():
    shares = {i: 0.0 for i in range(100)}
    shares[0] = 0.02
    ranks, count = prevalence_ranks(shares, 5, 'midrank', 'active_in_last_full_window')
    assert count == 1 and ranks[0] is None
