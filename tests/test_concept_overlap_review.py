import pytest

from saia.concept_overlap_review import deterministic_sample


def test_review_sample_is_stable_independent_of_set_order():
    ids = {"1", "2", "3", "4", "5"}
    assert deterministic_sample(ids, "case", 3) == deterministic_sample(
        set(reversed(sorted(ids))), "case", 3)
    assert len(deterministic_sample(ids, "case", 3)) == 3
    assert deterministic_sample(ids, "other", 3) != deterministic_sample(ids, "case", 3)


def test_review_sample_rejects_unbounded_cap():
    with pytest.raises(ValueError):
        deterministic_sample({"a"}, "case", 31)
