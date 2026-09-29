from copy import deepcopy
from datetime import date

import pytest

from saia import assessment_store as store, composition_assessment as ca, hybrid
from saia.terminology import PublicationText


def fixture():
    docs = [PublicationText(i, date(2010 + i, 2, 1), f'Result {i}', 'A research abstract') for i in range(1, 4)]
    candidates = hybrid.fuse([], [{'topic_id': 1, 'label': 'A line', 'work_ids': [1, 2, 3]}], {1, 2, 3})
    hybrid.add_series(candidates, docs, date(2017, 1, 1), date(2010, 1, 1), date(2017, 1, 1), 'year', False)
    snapshot = {'snapshot_id': 'fixture', 'as_of_date': '2017-01-01', 'period_from': '2010-01-01',
                'period_end_exclusive': '2017-01-01', 'window_step': 'year',
                'eligible_work_ids': [1, 2, 3], 'input_text_hash': ca.text_hash(docs),
                'provenance': {'embedding_model': 'fixture'}, 'candidates': candidates, 'limitations': []}
    evidence = {i: {'vector': [1., i / 10], 'source_quality': .9, 'authors': [], 'organisations': []} for i in [1, 2, 3]}
    return snapshot, ca.assess(snapshot, docs, evidence)


def reseal(report):
    report['assessment_content_sha256'] = hybrid.digest({k: v for k, v in report.items() if k != 'assessment_content_sha256'})


def test_real_generated_report_validates_without_mutation():
    snapshot, report = fixture()
    frozen = deepcopy(report)
    store.validate_report(snapshot, report)
    assert frozen == report


@pytest.mark.parametrize('change', ['hash', 'snapshot', 'source', 'date', 'policy', 'methodology', 'candidate', 'duplicate', 'membership', 'channel', 'composition', 'status', 'counts'])
def test_changed_report_is_not_legitimized_by_a_new_report_hash(change):
    snapshot, report = fixture()
    if change == 'hash': report['assessment_content_sha256'] = 'changed'
    if change == 'snapshot': report['snapshot_id'] = 'other'
    if change == 'source': report['provenance'] = {}
    if change == 'date': report['as_of_date'] = '2020-01-01'
    if change == 'policy': report['effective_policy']['min_last_full_window_share_for_widespread'] = .05
    if change == 'methodology': report['methodology_hash'] = 'changed'
    if change == 'candidate': report['candidates'] = []
    if change == 'duplicate': report['candidates'].append(deepcopy(report['candidates'][0]))
    if change == 'membership': report['candidates'][0]['work_ids'] = [1, 2]
    if change == 'channel': report['candidates'][0]['channels'] = ['lexical']
    if change == 'composition': report['candidates'][0]['composition_sha256'] = 'changed'
    if change == 'status': report['candidates'][0]['assessment']['status'] = 'market_leader'
    if change == 'counts': report['counts'] = {'forming': 1}
    if change != 'hash': reseal(report)
    with pytest.raises(ValueError): store.validate_report(snapshot, report)


def test_failed_scientific_replay_cannot_open_write_connection(monkeypatch):
    snapshot, report = fixture()
    monkeypatch.setattr(store.hybrid, 'read', lambda _: snapshot)
    def reject(_): raise ValueError('Scientific content changed')
    monkeypatch.setattr(store.composition_assessment, 'replay', reject)
    def forbidden(): pytest.fail('Replay failure must precede DB writes')
    monkeypatch.setattr(store.db, 'connect', forbidden)
    with pytest.raises(ValueError, match='Scientific'): store.save(report)


def test_portfolio_overlay_uses_exact_saved_composition_and_preserves_snapshot():
    from saia.portfolio import project
    snapshot, report = fixture()
    frozen = deepcopy(snapshot)
    saved = {'assessment_id': 'saved-fixture', 'created_at': 'today', 'report': report}
    result = project(snapshot, saved)
    assert snapshot == frozen
    assert result['saved_assessment']['policy_hash'] == report['policy_hash']
    row = result['candidates'][0]
    assert row['assessment'] == report['candidates'][0]['assessment']
    assert row['status'] == row['radar_ring'] == row['assessment']['status']
    assert row['map_position'] is None
    assert project(snapshot)['candidates'][0]['status'] == 'unassessed_candidate'
