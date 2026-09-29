from datetime import date

import pytest
import yaml

from saia.hybrid import POLICY_PATH, add_series, compare, fuse, lexical_policy, recovery
from saia.terminology import PublicationText, generate


def lex(phrase, ids):
    return {'phrase': phrase, 'work_ids': ids, 'contexts': []}


def sem(topic, ids):
    return {'topic_id': topic, 'label': f'topic {topic}', 'work_ids': ids}


def test_partial_overlap_never_becomes_transitive_union():
    result = fuse([lex('rare topic', [1, 2]), lex('another topic', [2, 3])],
                  [sem(10, [1, 4]), sem(11, [3, 5])], {1, 2, 3, 4, 5})
    assert len(result) == 4
    assert all(len(c['work_ids']) == 2 for c in result)
    assert all(c['status'] == 'unassessed_candidate' and c['emergence_score'] is None for c in result)
    linked = next(c for c in result if c['aliases'] == ['rare topic'])
    assert linked['overlap_links'][0]['shared_work_ids'] == [1]


def test_equal_memberships_merge_aliases_and_channels_only_once():
    result = fuse([lex('rare topic', [1, 2]), lex('new rare topic', [2, 1])],
                  [sem(10, [1, 2])], {1, 2})
    assert len(result) == 1
    assert result[0]['channels'] == ['lexical', 'semantic']
    assert result[0]['label'] == 'new rare topic'
    assert result[0]['semantic_topic_ids'] == [10]


@pytest.mark.parametrize('ids', [[], [1, 1], [1, 99]])
def test_invalid_membership_fails_instead_of_becoming_evidence(ids):
    with pytest.raises(ValueError, match='Состав'):
        fuse([lex('rare topic', ids)], [], {1, 2})


def test_single_channel_can_supply_candidate_without_confirmation_by_other():
    assert fuse([lex('rare topic', [1, 2])], [], {1, 2})[0]['channels'] == ['lexical']


def test_rare_selection_rejects_old_common_abstract_only_and_future_terms():
    cfg = yaml.safe_load(POLICY_PATH.read_text())
    p = lexical_policy(cfg)
    p['max_document_support'] = 2
    docs = [PublicationText(1, date(2010, 1, 1), 'old approach'),
            PublicationText(2, date(2016, 1, 1), 'old approach rare mechanism', 'hidden trick'),
            PublicationText(3, date(2016, 2, 1), 'rare mechanism', 'hidden trick'),
            PublicationText(4, date(2018, 1, 1), 'future surprise rare mechanism')]
    r = generate(docs, date(2017, 1, 1), policy=p, period_from=date(2010, 1, 1))
    phrases = {c['phrase'] for c in r['candidates']}
    assert 'rare mechanism' in phrases
    assert not {'old approach', 'hidden trick', 'future surprise'} & phrases
    assert r['candidates'][0]['title_document_support'] >= 1
    assert r['corpus_works'] == 3


def test_explicit_disk_lexical_policy_keeps_same_selection_rules():
    old = lexical_policy(yaml.safe_load(POLICY_PATH.read_text()))
    new = lexical_policy(yaml.safe_load(POLICY_PATH.with_name('hybrid.v0.4.1.yaml').read_text()))
    assert new['frequency_storage'] == 'sqlite'
    assert new['max_corpus_works'] == 100000
    for k in old:
        if k not in ('version', 'max_corpus_works'):
            assert old[k] == new[k]


def test_lexical_policy_does_not_read_outside_config():
    cfg = yaml.safe_load(POLICY_PATH.read_text())
    cfg['lexical_policy_file'] = '../secrets.yaml'
    with pytest.raises(ValueError, match='именем YAML'):
        lexical_policy(cfg)


def test_rare_recent_quarter_bounds_are_not_year_bounds():
    cfg = yaml.safe_load(POLICY_PATH.read_text())
    cfg['recent_birth_windows'] = 2
    docs = [PublicationText(1, date(2016, 3, 1), 'early mechanism'),
            PublicationText(2, date(2016, 10, 1), 'early mechanism late mechanism'),
            PublicationText(3, date(2016, 11, 1), 'late mechanism')]
    r = generate(docs, date(2017, 1, 1), step='quarter', policy=lexical_policy(cfg))
    assert 'late mechanism' in {c['phrase'] for c in r['candidates']}
    assert 'early mechanism' not in {c['phrase'] for c in r['candidates']}


def test_whole_corpus_denominator_and_zero_windows_are_shared():
    docs = [PublicationText(1, date(2014, 1, 1), 'one'),
            PublicationText(2, date(2016, 1, 1), 'two'),
            PublicationText(3, date(2016, 2, 1), 'other')]
    candidates = fuse([lex('rare topic', [1, 2])], [], {1, 2, 3})
    add_series(candidates, docs, date(2017, 1, 1), date(2013, 1, 1), date(2017, 1, 1), 'year', False)
    series = candidates[0]['publication_series']
    assert [p['topic_works'] for p in series['points']] == [0, 1, 0, 1]
    assert [p['corpus_works'] for p in series['points']] == [0, 1, 0, 2]
    assert series['share_slope_per_window'] is None


def test_partial_first_window_is_not_silently_full():
    docs = [PublicationText(1, date(2014, 8, 1), 'one')]
    with pytest.raises(ValueError, match='начало периода'):
        add_series(fuse([], [sem(1, [1])], {1}), docs, date(2015, 1, 1),
                   date(2014, 7, 1), date(2015, 1, 1), 'year', False)


def test_recovery_is_one_candidate_not_any_assignment():
    candidates = fuse([lex('rare topic', [1, 2])], [sem(10, [1, 5]), sem(11, [2, 6])], {1, 2, 5, 6})
    semantic = recovery([c for c in candidates if 'semantic' in c['channels']], {1, 2})
    hybrid = recovery(candidates, {1, 2})
    assert semantic['dominant_target_recall'] == .5
    assert hybrid['dominant_target_recall'] == 1.
    assert hybrid['dominant_target_share'] == 1.
    assert recovery(candidates, set())['dominant_target_recall'] is None


@pytest.mark.parametrize('candidates', [[], [{'candidate_id': 'unrelated', 'work_ids': [8, 9], 'label': 'unrelated'}]])
def test_zero_recovery_never_names_an_unrelated_candidate(candidates):
    result = recovery(candidates, {1, 2})
    assert result['dominant_target_recall'] == 0
    assert result['max_targets_one_candidate'] == 0
    assert result['dominant_candidate_id'] is None and result['dominant_label'] is None
    assert result['dominant_target_share'] is None


def test_api_pins_requested_semantic_run(monkeypatch):
    from fastapi.testclient import TestClient
    from saia.api import app
    from saia import hybrid
    seen = []
    monkeypatch.setattr(hybrid, 'analyze', lambda *args: seen.append(args) or {'candidates': []})
    r = TestClient(app).post('/corpus/demo/hybrid', json={'cluster_run_id': 428})
    assert r.status_code == 200 and seen == [('demo', 428)]


def test_api_read_failure_is_not_empty_success(monkeypatch):
    from fastapi.testclient import TestClient
    from saia.api import app
    from saia import hybrid
    def fail(_):
        raise ValueError('Отпечаток не совпал')
    monkeypatch.setattr(hybrid, 'read', fail)
    r = TestClient(app).get('/hybrid/fixture')
    assert r.status_code == 422 and 'Отпечаток' in r.json()['detail']


def repeat_packet():
    return {'snapshot_id': 'fixture', 'version': 'fixture', 'policy_hash': 'p',
            'lexical_policy_hash': 'l', 'input_text_hash': 'text', 'semantic_membership_hash': 's',
            'runtime': {'python': 'fixture'}, 'as_of_date': '2017-01-01',
            'period_from': '2010-01-01', 'period_end_exclusive': '2017-01-01',
            'window_step': 'year', 'missing_vector_work_ids': [],
            'provenance': {'code_version': 'code1'}, 'candidates': []}


def test_incomparable_repeat_is_unknown_not_success_or_failure():
    a, b = repeat_packet(), repeat_packet()
    b['provenance']['code_version'] = 'code2'
    assert compare(a, b)['repeatability_passed'] is None


def test_repeat_checks_candidate_contents_not_just_counts():
    a, b = repeat_packet(), repeat_packet()
    a['snapshot_id'], b['snapshot_id'] = 'one', 'two'
    assert compare(a, b)['repeatability_passed'] is True
    b['candidates'] = [{'work_ids': [1]}]
    assert compare(a, b)['repeatability_passed'] is False
