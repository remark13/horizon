import copy
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.probe_bas_task_pair_similarity import (
    _source, _works, _validated_pairs, evaluate,
)


CONFIG = Path("config/bas-task-pair-similarity.v1.json")


def _inputs():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    source = _source(config)
    works, card_of = _works(config, source)
    return config, source, works, card_of


def test_frozen_pairs_cover_both_cards_and_both_classes():
    config, _source_packet, works, card_of = _inputs()
    pairs = _validated_pairs(config, works, card_of)
    assert len(works) == 26
    assert len(pairs) == 20
    assert {card_of[row["a"]] for row in pairs} == {1, 2}
    assert sum(row["same_task"] for row in pairs) == 10


def test_duplicate_or_cross_card_pair_is_rejected():
    config, _source_packet, works, card_of = _inputs()
    duplicate = copy.deepcopy(config)
    duplicate["pairs"][1]["a"] = duplicate["pairs"][0]["b"]
    duplicate["pairs"][1]["b"] = duplicate["pairs"][0]["a"]
    with pytest.raises(ValueError, match="duplicate"):
        _validated_pairs(duplicate, works, card_of)
    cross = copy.deepcopy(config)
    cross["pairs"][0]["b"] = 195733
    with pytest.raises(ValueError, match="outside its parent card"):
        _validated_pairs(cross, works, card_of)


def test_evaluation_does_not_proclaim_weak_signal_accuracy():
    config, source, works, _card_of = _inputs()
    ids = sorted(works)
    vectors = {identifier: np.eye(len(ids))[n] for n, identifier in enumerate(ids)}
    report = evaluate(config, source, vectors)
    assert report["pair_count"] == 20
    assert report["production_changed"] is False
    assert report["weak_signal_accuracy_measured"] is False
    assert report["selected_feature"] in config["features"]
    with pytest.raises(ValueError, match="one vector for every work"):
        evaluate(config, source, {key: value for key, value in vectors.items()
                                  if key != ids[0]})
