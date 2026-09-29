import pytest

from saia.phrase_group_packet import edge_sample


def test_edge_sample_is_deterministic_and_deduplicates_short_groups():
    rows = [{"arxiv_id": str(i), "first_submission_date": f"2025-01-{i:02d}"}
            for i in (4, 1, 3, 2, 5)]
    assert [row["arxiv_id"] for row in edge_sample(rows)] == ["1", "2", "4", "5"]
    assert len(edge_sample(rows[:2])) == 2
    with pytest.raises(ValueError, match="bounded"):
        edge_sample(rows, each_edge=100)
