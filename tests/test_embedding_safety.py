import pytest
from saia import embed


def test_refresh_cannot_delete_old_inputs_or_initialize_model(monkeypatch):
    def fail():
        pytest.fail('Проверка должна сработать до подключения модели и базы.')
    monkeypatch.setattr(embed, 'make_embedder', fail)
    with pytest.raises(embed.EmbeddingError, match='старые векторы сохраняются'):
        embed.embed_mission('not-needed', refresh=True)


@pytest.mark.parametrize('vectors,count', [([],1), ([[0.,0.]],1), ([[float('nan'),1.]],1),
                                          ([[1.],[1.,2.]],2), ([[True,1.]],1), ([[None,1.]],1)])
def test_invalid_batch_cannot_be_committed(vectors, count):
    with pytest.raises(embed.EmbeddingError): embed.validate_vector_batch(vectors, count)


def test_vector_dimension_is_checked_across_batches():
    assert embed.validate_vector_batch([[1.,2.]],1,2) == 2
    with pytest.raises(embed.EmbeddingError, match='между пакетами'):
        embed.validate_vector_batch([[1.,2.,3.]],1,2)
