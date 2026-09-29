"""Self-contained checks for claim-vs-technology routing; no model required."""

import json
from pathlib import Path

import pytest

from scripts.probe_two_axis_hypothesis_type_model import _parse


ROOT = Path(__file__).resolve().parents[1]


def test_model_response_cannot_call_customer_example_not_applicable():
    proposal = {"technical_entity_type": "scientific_method_material",
                "signal_claim_type": "product_market",
                "reason_ru": "Технический метод, но заявлен рыночный раунд.",
                "needs_review": False}
    assert _parse({"response": json.dumps(proposal)},
                  "customer_supplied_signal_example") == proposal
    proposal["signal_claim_type"] = "not_applicable"
    with pytest.raises(ValueError, match="Invalid two-axis"):
        _parse({"response": json.dumps(proposal)},
               "customer_supplied_signal_example")
    assert _parse({"response": json.dumps(proposal)},
                  "search_area_not_signal") == proposal


def test_two_axis_holdout_is_disjoint_from_prompt_tuning_pilot():
    pilot = json.loads((ROOT / "config/goal-pilot-two-axis-hypothesis-types.v1.json")
                       .read_text(encoding="utf-8"))
    holdout = json.loads((ROOT / "config/goal-two-axis-type-holdout.v1.json")
                         .read_text(encoding="utf-8"))
    pilot_ids = {row["id"] for row in pilot["rows"]}
    holdout_ids = {row["id"] for row in holdout["rows"]}
    assert len(pilot_ids) == len(holdout_ids) == 16
    assert not pilot_ids & holdout_ids
    assert holdout["prompt_version_to_test"] == "two-axis-hypothesis-local-model-pilot-v3"
