from copy import deepcopy
import json
from pathlib import Path

import pytest

from saia.auto_phrase_card import build_card
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "proposals": ROOT / "outputs/broad-title-phrase-bas-2026-09-26-v1.json",
    "packet": ROOT / "outputs/bas-auto-vla-papers-2026-09-26-v1.json",
    "review": ROOT / "evaluation/bas-auto-vla-developer-review-2026-09-26-v1.json",
}


def _inputs():
    data = {name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in PATHS.items()}
    return data | {"packet_sha256": sha256_file(PATHS["packet"]),
                   "input_sha256": {name: sha256_file(path) for name, path in PATHS.items()}}


def test_auto_phrase_is_split_before_any_weak_signal_claim():
    result = build_card(**_inputs())
    assert result["source_phrase"] == "vision language action"
    assert result["source_phrase_rank"] == 11
    assert result["phrase_family"]["arxiv_title_matches"] == 16
    assert result["phrase_family"]["annual_title_matches"][-2:] == [1, 15]
    assert result["phrase_family"]["developer_roles"] == {
        "model_or_system": 12, "benchmark_or_dataset": 3, "review_or_survey": 1,
    }
    assert result["phrase_family"]["one_narrow_mechanism_proven"] is False
    assert result["leading_narrow_task"]["task"] == "navigation_control"
    assert result["leading_narrow_task"]["developer_primary_system_count"] == 7
    assert result["leading_narrow_task"]["title_phrase_series_complete_for_task"] is False
    assert result["confidence_percent"] is None
    assert result["relative_growth_of_narrow_task_proven"] is False
    assert result["other_source_blocks"]["patents"] == "not_collected_for_this_candidate"


def test_card_rejects_partial_review_and_claim_of_independent_validation():
    inputs = _inputs()
    inputs["review"] = deepcopy(inputs["review"])
    inputs["review"]["rows"].pop()
    with pytest.raises(ValueError, match="Complete one-to-one"):
        build_card(**inputs)
    inputs = _inputs()
    inputs["review"] = deepcopy(inputs["review"])
    inputs["review"]["independent_expert_review"] = True
    with pytest.raises(ValueError, match="does not match"):
        build_card(**inputs)
