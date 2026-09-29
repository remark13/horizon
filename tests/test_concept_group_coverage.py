import pytest

from saia.concept_group_coverage import overlap_counts


def test_overlap_counts_distinguish_union_and_required_intersection():
    counts = overlap_counts([{"a", "b", "c"}, {"b", "c", "d"}, {"c", "e"}])
    assert counts == {"any_group_candidate_ids": 5,
                      "two_or_more_group_ids": 2,
                      "all_group_literal_ids": 1}


def test_overlap_counts_reject_empty_plan():
    with pytest.raises(ValueError):
        overlap_counts([])
