from datetime import date, datetime, timezone

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.historical_background import (
    assess_current_anchors,
    historical_rows,
    topic_anchors,
)


def row(identifier, title, abstract, created):
    stamp = datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc)
    return {
        "id": identifier, "title": title, "abstract": abstract,
        "categories": "cs.LG", "versions": [{"version": "v1", "created":
        stamp.strftime("%a, %d %b %Y %H:%M:%S %z")}],
    }


def test_historical_rows_are_strictly_pre_cutoff_and_not_silently_capped(tmp_path):
    path = tmp_path / "pack.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("a", "Old", "Text", date(2022, 12, 31)),
        row("b", "Boundary", "Text", date(2023, 1, 1)),
        row("c", "Text missing abstract", "", date(2020, 1, 1)),
        row("d", "Older", "Text", date(2019, 1, 1)),
    ]), path)
    plan = {"included_terms": ["text"], "exclusions": []}
    rows, counts = historical_rows(
        path, date(2023, 1, 1), max_records=2, plan=plan
    )
    assert [item["arxiv_id"] for item in rows] == ["d", "a"]
    assert counts["before_cutoff"] == 3
    assert counts["at_or_after_cutoff"] == 1
    assert counts["excluded_missing_title_or_abstract"] == 1
    with pytest.raises(ValueError, match="never silently truncated"):
        historical_rows(path, date(2023, 1, 1), max_records=1, plan=plan)


def test_historical_rows_reapply_relevance_plan(tmp_path):
    path = tmp_path / "pack.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("a", "Tissue engineering", "Relevant", date(2020, 1, 1)),
        row("b", "Regenerative braking", "Unrelated", date(2020, 1, 2)),
    ]), path)
    rows, counts = historical_rows(
        path, date(2023, 1, 1), max_records=10,
        plan={"included_terms": ["tissue engineering"], "exclusions": []},
    )
    assert [item["arxiv_id"] for item in rows] == ["a"]
    assert counts["excluded_by_relevance_plan"] == 1


def test_topic_anchors_exclude_noise_and_use_only_supplied_history(monkeypatch):
    rows = [
        {"published": date(2020, 1, i + 1), "title": f"T{i}", "abstract": "A",
         "arxiv_id": str(i)} for i in range(6)
    ]
    vectors = [[1.0, 0.0], [0.9, 0.1], [1.0, 0.1],
               [0.0, 1.0], [0.1, 0.9], [0.7, 0.7]]

    def fake_cluster(texts, matrix, *_args):
        assert len(texts) == 6 and matrix.shape == (6, 2)
        return np.array([0, 0, 0, 1, 1, -1]), {0: ["alpha"], 1: ["beta"]}

    monkeypatch.setattr("saia.historical_background.cluster_window", fake_cluster)
    anchors, labels = topic_anchors(
        rows, vectors, min_topic_size=2, seed=42, umap={}, hdbscan={},
        small_window_similarity_threshold=.35,
    )
    assert labels[-1] == -1
    assert [item["documents"] for item in anchors] == [3, 2]
    assert anchors[0]["top_terms"] == ["alpha"]


def test_current_novelty_uses_historical_anchors_and_requires_peer_population():
    background = [
        {"background_topic": 0, "top_terms": ["old-a"], "anchor": [1.0, 0.0]},
        {"background_topic": 1, "top_terms": ["old-b"], "anchor": [0.0, 1.0]},
    ]
    current = {f"topic-{i}": [1.0, float(i + 1) / 10] for i in range(5)}
    result = assess_current_anchors(current, background, min_peers=5)
    assert all(item["novelty_percentile"] is not None for item in result.values())
    assert all(item["historical_anchor_topics"] == 2 for item in result.values())
    too_few = assess_current_anchors(dict(list(current.items())[:4]), background, min_peers=5)
    assert all(item["novelty_percentile"] is None for item in too_few.values())
