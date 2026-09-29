"""Arithmetic and comparison safeguards for the offline relevance probe."""

from scripts.probe_priority_query_reranker import ndcg_at_k, pairwise


def test_pairwise_counts_only_within_topic_and_uses_strict_order() -> None:
    rows = [
        {"topic": "a", "label": "yes", "score": 3.0},
        {"topic": "a", "label": "partial", "score": 2.0},
        {"topic": "a", "label": "no", "score": 1.0},
        {"topic": "b", "label": "no", "score": 4.0},
    ]
    assert pairwise(rows, "score") == {
        "yes_over_no": {"correct": 1, "pairs": 1},
        "yes_over_partial": {"correct": 1, "pairs": 1},
        "partial_over_no": {"correct": 1, "pairs": 1},
    }


def test_ndcg_drops_for_reversed_ranking() -> None:
    rows = [
        {"label": "yes", "good": 3.0, "bad": 1.0},
        {"label": "no", "good": 1.0, "bad": 3.0},
    ]
    assert ndcg_at_k(rows, "good", 2) == 1.0
    assert ndcg_at_k(rows, "bad", 2) < 1.0


def test_ndcg_is_undefined_without_label_contrast() -> None:
    assert ndcg_at_k([{"label": "partial", "score": 1.0}], "score", 5) is None
