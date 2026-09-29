from datetime import date

import numpy as np

from saia.temporal_neighborhood import (
    component_summary,
    evaluate_cases,
    filtered_edges,
    temporal_neighbors,
)


def test_temporal_neighbors_never_link_to_future_and_obey_gap():
    work_ids = [30, 10, 20, 40]
    days = [date(2020, 1, 3), date(2020, 1, 1),
            date(2020, 1, 2), date(2021, 1, 1)]
    vectors = np.array([[1, 0], [1, 0], [1, 0], [1, 0]], dtype=np.float32)
    result = temporal_neighbors(work_ids, days, vectors, max_neighbors=3,
                                max_gap_days=10, block_size=2)
    assert result[1] == []
    assert [idx for idx, _score in result[2]] == [1]
    assert set(idx for idx, _score in result[0]) == {1, 2}
    assert result[3] == []


def test_temporal_neighbors_same_day_uses_work_id_order():
    work_ids = [20, 10]
    days = [date(2020, 1, 1), date(2020, 1, 1)]
    vectors = np.array([[1, 0], [1, 0]], dtype=np.float32)
    result = temporal_neighbors(work_ids, days, vectors, max_neighbors=1,
                                max_gap_days=0)
    assert result[1] == []
    assert result[0][0][0] == 1


def test_temporal_neighbors_clamps_float_roundoff_to_cosine_range():
    work_ids = [1, 2]
    days = [date(2020, 1, 1), date(2020, 1, 2)]
    vectors = np.array([[1.0000001, 0], [1.0000001, 0]], dtype=np.float32)
    result = temporal_neighbors(work_ids, days, vectors, max_neighbors=1,
                                max_gap_days=10)
    assert result[1][0][1] <= 1.0


def test_edges_components_and_posthoc_case_recovery():
    neighbors = [[], [(0, .92)], [(1, .81)], [(0, .96)]]
    edges = filtered_edges(neighbors, k=1, threshold=.90)
    summary, roots = component_summary(4, edges)
    assert summary["largest_component"] == 3
    cases = [{"case_id": "x", "family_id": "f", "title": "F",
              "kind": "ambiguous_line", "arxiv_ids": ["a", "b"]}]
    rows = [
        {"title": "A", "arxiv_id": "a", "effective_date": date(2020, 1, 1)},
        {"title": "B", "arxiv_id": "b", "effective_date": date(2020, 2, 1)},
        {"title": "C", "arxiv_id": "c", "effective_date": date(2020, 3, 1)},
        {"title": "D", "arxiv_id": "d", "effective_date": date(2020, 4, 1)},
    ]
    result = evaluate_cases(cases, {"a": 0, "b": 1}, rows, neighbors,
                            edges, roots, k=1)[0]
    assert result["direct_pair_recovered"] is True
    assert result["same_component_recovered"] is True
    assert result["publications"][1]["nearest_prior"][0]["same_catalog_family"] is True
