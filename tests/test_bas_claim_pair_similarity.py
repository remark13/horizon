import numpy as np
import pytest

from scripts.probe_bas_claim_bge_pairs import _unit_vectors


def test_embedding_vectors_are_normalized_and_zero_rejected():
    result = _unit_vectors([[3.0, 4.0], [1.0, 0.0]])
    assert np.allclose(np.linalg.norm(result, axis=1), 1.0)
    with pytest.raises(ValueError, match="Invalid embedding"):
        _unit_vectors([[0.0, 0.0]])
