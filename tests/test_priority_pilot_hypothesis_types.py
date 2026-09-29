from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pilot_type_interpretations_cover_frozen_cases_without_promotion_to_gold() -> None:
    typed = json.loads((ROOT / "config/priority-pilot-hypothesis-types.v1.json").read_text())
    cases = json.loads((ROOT / "config/priority-arxiv-pilot.v2.json").read_text())["cases"]
    assert {item["id"] for item in typed["cases"]} == {item["id"] for item in cases}
    assert len(typed["cases"]) == len(cases) == 16
    assert all(item["primary_type"] in typed["allowed_types"] for item in typed["cases"])
    assert all(set(item["secondary_types"]) <= set(typed["allowed_types"])
               for item in typed["cases"])
    assert all(item["source_priority"] and item["caution"] for item in typed["cases"])
    assert typed["policy"]["national_areas_are_search_scope_not_signals"] is True
    assert typed["policy"]["customer_cases_are_provided_hypotheses_not_gold"] is True
    assert typed["policy"]["used_in_production_scoring"] is False
