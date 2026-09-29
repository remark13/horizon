from copy import deepcopy
import json
from pathlib import Path

import pytest

from saia.controlled_collection import sha256_file
from saia.line_evidence_pilot import build_cards


ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "temporal": ROOT / "outputs/bas-narrow-temporal-diagnostic-2026-09-26-v4.json",
    "packet": ROOT / "evaluation/bas-narrow-directness-review-2026-09-26-v1.json",
    "selection": ROOT / "outputs/bas-narrow-directness-selection-2026-09-26-v1.json",
    "review": ROOT / "evaluation/bas-narrow-developer-directness-2026-09-26-v1.json",
}


def _inputs():
    data = {name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in PATHS.items()}
    return data | {
        "packet_sha256": sha256_file(PATHS["packet"]),
        "input_sha256": {name: sha256_file(path) for name, path in PATHS.items()},
    }


def test_two_cards_show_evidence_without_claiming_weak_signal_or_accuracy():
    result = build_cards(**_inputs())
    assert len(result["cards"]) == 2
    assert result["accuracy_or_weak_signal_confirmation_claimed"] is False
    by_id = {card["line_id"]: card for card in result["cards"]}
    visual = by_id["bas-visual-inertial-odometry"]
    tasks = by_id["bas-multi-uav-task-allocation"]
    assert (visual["literal_match_total"], tasks["literal_match_total"]) == (54, 31)
    assert (visual["outside_prior_broad_bas_union"],
            tasks["outside_prior_broad_bas_union"]) == (50, 27)
    assert (visual["developer_title_abstract_sample"]["direct_primary_result"],
            tasks["developer_title_abstract_sample"]["direct_primary_result"]) == (7, 8)
    for card in result["cards"]:
        assert card["generated_from_broad_user_query"] is False
        assert card["relative_growth_in_bas_area"] == "unknown"
        assert card["first_mention_in_original_version"] == "unknown"
        assert card["independent_expert_validation"] == "not_done"
        assert len(card["sample_direct_source_examples"]) == 5
        assert all(source["url"].startswith("https://arxiv.org/abs/")
                   for source in card["sample_direct_source_examples"])


def test_review_must_cover_exact_packet_without_silent_drops():
    inputs = _inputs()
    inputs["review"] = deepcopy(inputs["review"])
    inputs["review"]["rows"].pop()
    with pytest.raises(ValueError, match="exact selected packet"):
        build_cards(**inputs)


def test_no_expert_status_or_other_source_can_be_forged():
    inputs = _inputs()
    inputs["review"] = deepcopy(inputs["review"])
    inputs["review"]["independent_expert_review"] = True
    with pytest.raises(ValueError, match="provenance"):
        build_cards(**inputs)
    inputs = _inputs()
    inputs["temporal"] = deepcopy(inputs["temporal"])
    inputs["temporal"]["source"]["source_role"] = "world_science"
    with pytest.raises(ValueError, match="evidentiary status"):
        build_cards(**inputs)
