import pytest
import json
from pathlib import Path

from saia.openalex_boolean_query import concept_expression
from scripts.probe_fixed_compound_openalex_live import run


def test_boolean_query_preserves_required_groups_and_synonym_alternatives():
    assert concept_expression([["neuromorphic chips", "neuromorphic processors"],
                               ["edge devices"]]) == (
        '("neuromorphic chips" OR "neuromorphic processors") AND ("edge devices")'
    )


def test_boolean_query_rejects_unexpected_syntax_in_phrase():
    with pytest.raises(ValueError, match="Unsafe"):
        concept_expression([["safe", 'bad"phrase'], ["edge devices"]])


def test_boolean_query_rejects_overlong_encoded_url():
    with pytest.raises(ValueError, match="URL budget"):
        concept_expression([["длинная фраза " * 15] * 3,
                            ["ещё одна фраза " * 15] * 3])


def test_goal_followup_is_subset_of_frozen_plan_and_boolean_only(tmp_path):
    plan = Path("config/goal-cross-domain-compound-pilot.v1.json")
    config = Path("config/goal-cross-domain-openalex-followup.v1.json")
    with pytest.raises(ValueError, match="Boolean mode"):
        run(plan, config, mode="plain")
    changed = json.loads(config.read_text(encoding="utf-8"))
    changed["cases"][0]["case_id"] = "unknown-case"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differ"):
        run(plan, path, mode="boolean")
