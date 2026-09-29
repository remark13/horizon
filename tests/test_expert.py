import pytest

from saia.expert import _candidate, validate


def test_expert_opinion_is_trimmed_and_sources_deduplicated():
    assert validate('needs_review', ' Analyst ', ' Needs an independent follow-up study. ',
                    ['https://arxiv.org/abs/1601.00001', 'https://arxiv.org/abs/1601.00001']) == (
        'Analyst', 'Needs an independent follow-up study.', ['https://arxiv.org/abs/1601.00001'])


@pytest.mark.parametrize('decision,actor,reason', [
    ('forming', 'analyst', 'Needs an independent follow-up study.'),
    ('noise', ' ', 'Needs an independent follow-up study.'),
    ('noise', 'analyst', 'short')])
def test_invalid_opinion_is_rejected(decision, actor, reason):
    with pytest.raises(ValueError): validate(decision, actor, reason, [])


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/source',
                                'https://user:password@example.org', 'https://example.org/a b', 'https:///missing'])
def test_invalid_expert_source_is_rejected(url):
    with pytest.raises(ValueError):
        validate('needs_review', 'analyst', 'Needs an independent follow-up study.', [url])


def test_exact_publication_composition_is_stable_across_candidate_ids():
    first = {'candidates': [{'candidate_id': 'run-a', 'work_ids': [3, 1, 2]}]}
    second = {'candidates': [{'candidate_id': 'run-b', 'work_ids': [2, 3, 1]}]}
    assert _candidate(first, 'run-a')[1] == _candidate(second, 'run-b')[1]
    assert len(_candidate(first, 'run-a')[1]) == 64


def test_invalid_or_duplicate_composition_is_rejected():
    with pytest.raises(ValueError, match='корректного состава'):
        _candidate({'candidates': [{'candidate_id': 'bad', 'work_ids': [1, 1]}]}, 'bad')
    with pytest.raises(ValueError, match='не соответствует'):
        _candidate({'candidates': [{'candidate_id': 'bad', 'work_ids': [1],
                                    'composition_sha256': '0' * 64}]}, 'bad')
