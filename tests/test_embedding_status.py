import pytest

from saia import embedding_status


@pytest.mark.parametrize('total,completed,invalid,dims,state', [
    (0, 0, 0, [], 'empty'), (3, 0, 0, [], 'pending'),
    (3, 1, 0, [768], 'partial'), (3, 3, 0, [768], 'complete'),
    (3, 3, 1, [768], 'invalid'), (3, 3, 0, [2, 768], 'invalid'),
    (3, 3, 0, [], 'invalid'), (3, 1, 0, [0], 'invalid'),
])
def test_readiness_never_uses_a_log_or_partial_count_as_complete(total, completed, invalid, dims, state):
    result = embedding_status.summarize(total, completed, invalid, dims)
    assert result['state'] == state
    assert result['ready_for_strict_clustering'] is (state == 'complete')


@pytest.mark.parametrize('total,completed,invalid', [(1, 2, 0), (-1, 0, 0), (3, 1, 2)])
def test_impossible_counts_rejected(total, completed, invalid):
    with pytest.raises(ValueError):
        embedding_status.summarize(total, completed, invalid, [])


@pytest.mark.parametrize('generation,model', [(None, 'model'), (True, 'model'), (0, 'model'), (761, ''), (761, None)])
def test_no_implicit_generation_or_model_before_db(monkeypatch, generation, model):
    monkeypatch.setattr(embedding_status.db, 'connect', lambda: pytest.fail('database opened'))
    with pytest.raises(ValueError):
        embedding_status.read('corpus', generation, model)
