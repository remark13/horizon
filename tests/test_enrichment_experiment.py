from copy import deepcopy

import pytest

from saia.enrichment_experiment import policy_effect, summary, snapshot_context


def report():
    return {'snapshot_id': 'fixed', 'snapshot_content_sha256': 'sha', 'input_text_hash': 'text',
            'methodology_hash': 'method', 'measurement_policy_hash': 'measure', 'assessment_content_sha256': 'assessment',
            'version': 'v1', 'policy_hash': 'policy', 'provenance': {}, 'counts': {'watch': 1},
            'candidates': [{'candidate_id': 'one', 'work_ids': [1, 2],
                            'assessment': {'status': 'watch', 'gates': [{'gate': 'identity', 'passed': None}]}}]}


def test_policy_comparison_preserves_unknowns_and_does_not_mutate_inputs():
    before = report()
    after = deepcopy(before)
    after['candidates'][0]['assessment']['gates'][0]['passed'] = False
    frozen = deepcopy(before)
    assert policy_effect(before, after) == {'same_exact_compositions': True, 'changed_statuses': {},
                                          'changed_check_outcomes': {'identity': 1}}
    assert before == frozen
    assert summary(before)['unknown_checks'] == {'identity': 1}


@pytest.mark.parametrize('key', ['snapshot_id', 'snapshot_content_sha256', 'input_text_hash', 'methodology_hash', 'measurement_policy_hash'])
def test_cross_corpus_or_method_transitions_forbidden(key):
    before, after = report(), report()
    after[key] = 'other'
    with pytest.raises(ValueError):
        policy_effect(before, after)


def test_same_id_cannot_hide_changed_work_composition():
    before, after = report(), report()
    after['candidates'][0]['work_ids'] = [1, 3]
    with pytest.raises(ValueError):
        policy_effect(before, after)


def test_summary_rejects_fabricated_status_totals():
    source = report()
    source['counts'] = {'forming': 1}
    with pytest.raises(ValueError):
        summary(source)


def snapshot():
    return {'as_of_date': '2017-01-01', 'period_from': '2010-01-01',
            'period_end_exclusive': '2017-01-01', 'window_step': 'year',
            'version': 'v1', 'policy_hash': 'p', 'lexical_policy_hash': 'l',
            'provenance': {'embedding_model': 'fixed', 'quality_methodology_hash': 'm', 'code_version': 'old'},
            'runtime': {}, 'input_text_hash': 'text', 'counts': {'eligible_corpus': 3}}


@pytest.mark.parametrize('key', ['period_from', 'period_end_exclusive', 'window_step', 'policy_hash', 'lexical_policy_hash'])
def test_corpus_comparison_checks_frozen_period_and_generator_passports(key):
    before, after = snapshot(), snapshot()
    after[key] = 'different'
    with pytest.raises(ValueError):
        snapshot_context(before, after)


def test_changed_text_and_code_are_recorded_not_claimed_identical():
    before, after = snapshot(), snapshot()
    after['input_text_hash'] = 'changed'
    after['provenance']['code_version'] = 'new'
    result = snapshot_context(before, after)
    assert result['same_input_text'] is False and result['same_code'] is False
