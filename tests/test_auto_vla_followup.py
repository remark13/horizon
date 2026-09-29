from copy import deepcopy
import json
from pathlib import Path

import pytest

from saia.auto_vla_followup_eval import evaluate
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
FOLLOWUP = ROOT / "outputs/bas-auto-vla-full-arxiv-followup-2026-09-26-v1.json"
REVIEW = ROOT / "evaluation/bas-auto-vla-followup-developer-review-2026-09-26-v1.json"


def _inputs():
    return (json.loads(FOLLOWUP.read_text(encoding="utf-8")),
            json.loads(REVIEW.read_text(encoding="utf-8")),
            sha256_file(FOLLOWUP))


def test_followup_revises_title_only_inventory_without_confirming_signal():
    followup, review, digest = _inputs()
    result = evaluate(followup, review, followup_sha256=digest)
    assert result["case_count"] == 27
    assert result["retrieval_comparison"]["new_phrase_family"]["count"] == 32
    assert result["retrieval_comparison"]["narrow_navigation_candidate"]["count"] == 22
    assert result["retrieval_comparison"]["predecessor_not_new_VLA"]["count"] == 67
    assert result["direct_navigation"]["count"] == 12
    assert result["direct_navigation"]["annual_counts_in_reviewed_union"][-2:] == [2, 10]
    assert result["direct_navigation"]["earliest_in_reviewed_union"]["arxiv_id"] == "2503.02572"
    assert result["direct_navigation"]["previous_title_packet_direct_ids_recovered"] == 7
    assert "2503.02572" in result["direct_navigation"]["additional_direct_ids"]
    assert result["weak_signal_confirmed"] is False
    assert result["first_technology_publication_proven"] is False


def test_frozen_full_arxiv_branches_reconcile_without_double_counting():
    followup, _, _ = _inputs()
    all_work_ids = {row["arxiv_id"] for row in followup["works"]}
    assert len(all_work_ids) == len(followup["works"])
    for branch in followup["branches"]:
        assert len(branch["arxiv_ids"]) == branch["count"]
        assert len(set(branch["arxiv_ids"])) == branch["count"]
        assert sum(branch["annual_current_metadata_matches"]) == branch["count"]
        assert sum(branch["annual_inside_fixed_broad_parent"]) + branch[
            "outside_fixed_broad_parent"] == branch["count"]
        assert set(branch["arxiv_ids"]) <= all_work_ids
    for role, union in followup["role_unions"].items():
        expected = set().union(*(set(branch["arxiv_ids"])
                                 for branch in followup["branches"]
                                 if branch["role"] == role))
        assert set(union["unique_ids"]) == expected
        assert union["count"] == len(expected) == sum(union[
            "annual_current_metadata_matches"])
    assert set(followup["original_title_phrase_ids"]) <= set(
        followup["role_unions"]["new_phrase_family"]["unique_ids"])


def test_followup_review_cannot_silently_drop_or_promote_a_case():
    followup, review, digest = _inputs()
    missing = deepcopy(review)
    missing["rows"].pop()
    with pytest.raises(ValueError, match="exactly"):
        evaluate(followup, missing, followup_sha256=digest)
    promoted = deepcopy(review)
    promoted["independent_expert_review"] = True
    with pytest.raises(ValueError, match="status"):
        evaluate(followup, promoted, followup_sha256=digest)
