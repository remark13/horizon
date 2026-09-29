import json
from collections import Counter
from pathlib import Path

import numpy as np

from scripts.build_bas_pair_review_packet import build_packets, select_pairs
from scripts.probe_bas_task_pair_similarity import _source, _works


CONFIG = Path("config/bas-task-pair-similarity.v1.json")


def _inputs():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    source = _source(config)
    works, _ = _works(config, source)
    ids = sorted(works)
    rng = np.random.default_rng(410)
    vectors = {identifier: raw / np.linalg.norm(raw)
               for identifier in ids
               for raw in [rng.normal(size=32)]}
    return config, source, vectors


def test_blind_packet_has_unseen_balanced_strata_and_no_scores_or_labels():
    config, source, vectors = _inputs()
    selected = select_pairs(config, source, vectors)
    forbidden = {tuple(sorted((row["a"], row["b"]))) for row in config["pairs"]}
    assert not forbidden & {(row["a"], row["b"]) for row in selected}
    assert Counter((row["card_rank"], row["stratum"]) for row in selected) == {
        (rank, stratum): 5 for rank in (1, 2)
        for stratum in ("low", "middle", "high")
    }
    blind, manifest = build_packets(config, source, vectors)
    assert len(blind["cases"]) == len(manifest["rows"]) == 30
    assert blind["cases"] == build_packets(config, source, vectors)[0]["cases"]
    assert all(set(case["review_fields"].values()) == {None}
               for case in blind["cases"])
    assert "specter_cosine" not in json.dumps(blind)
    assert "same_task" not in json.dumps(blind)
    assert all(case["paper_a"]["source_url"].startswith("https://arxiv.org/abs/")
               and case["paper_b"]["source_url"].startswith("https://arxiv.org/abs/")
               for case in blind["cases"])
