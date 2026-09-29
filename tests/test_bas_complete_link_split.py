import numpy as np
import pytest

from scripts.probe_bas_complete_link_split import complete_link


def test_complete_link_does_not_chain_through_bridge():
    similarities = np.array([[1.0, 0.95, 0.82],
                             [0.95, 1.0, 0.95],
                             [0.82, 0.95, 1.0]])
    groups = complete_link([10, 20, 30], similarities, 0.9)
    assert groups == [[10, 20], [30]]
    assert complete_link([10, 20, 30], similarities, 0.99) == [[10], [20], [30]]


def test_complete_link_rejects_bad_matrix():
    with pytest.raises(ValueError, match="Invalid clustering"):
        complete_link([1, 1], np.eye(2), 0.5)
