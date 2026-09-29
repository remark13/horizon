import copy

import pytest

from saia.portfolio import present_gate


@pytest.mark.parametrize('passed', [True, False, None])
def test_rule_and_outcome_preserved_without_mutation(passed):
    gate = {'gate': 'G0_volume', 'passed': passed, 'observed': 5 if passed is not None else None,
            'threshold': 5, 'reason': 'Недостаточно канонических публикаций.', 'severity': 'block'}
    before = copy.deepcopy(gate)
    shown = present_gate(gate)
    assert gate == before
    assert all(shown[k] == v for k, v in gate.items())
    assert shown['display_name'] == 'Количество публикаций'
    if passed is False:
        assert gate['reason'] in shown['display_explanation']
    else:
        assert gate['reason'] not in shown['display_explanation']
    if passed is None:
        assert 'не означает' in shown['display_explanation']


def test_passed_prevalence_does_not_claim_widespread_or_market():
    shown = present_gate({'gate': 'G1_publication_prevalence', 'passed': True,
                         'reason': 'Высокий ранг; доля ниже порога.'})
    assert 'а не рынок' in shown['display_explanation']
    assert 'не подтверждение сигнала' in shown['display_explanation']


def test_unknown_future_gate_stays_unknown_not_false():
    shown = present_gate({'gate': 'future-check', 'passed': None, 'reason': 'Failure rule'})
    assert shown['passed'] is None
    assert shown['display_name'] == 'future-check'
    assert 'Недостаточно данных' in shown['display_explanation']


def test_prevalence_shows_both_criteria_not_a_misleading_single_threshold():
    gate = {'gate': 'G1_publication_prevalence', 'passed': True, 'observed': 99.78,
            'threshold': 80, 'reason': 'Saved unchanged compound rule'}
    shown = present_gate(gate, {'publication_share': .0008},
                         {'min_last_full_window_share_for_widespread': .01})
    assert 'оба условия' in shown['display_explanation']
    assert '1%' in shown['display_explanation'] and '0.080%' in shown['display_explanation']
    assert shown['passed'] is True and shown['observed'] == 99.78


def test_prevalence_does_not_invent_unknown_share():
    shown = present_gate({'gate': 'G1_publication_prevalence', 'passed': None,
                          'observed': 99., 'threshold': 80, 'reason': 'rule'}, {},
                         {'min_last_full_window_share_for_widespread': .01})
    assert 'доля неизвестна' in shown['display_explanation']
