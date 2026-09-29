import copy

import pytest

from saia.retrieval_relevance_review import (
    build_packet,
    compare_submissions,
    load_policy,
    validate_submission,
)


def completed(template, reviewer, relevance="relevant", role="central"):
    value = copy.deepcopy(template)
    value["reviewer_id"] = reviewer
    value["independent_review_declared"] = True
    value["hidden_fields_not_seen_declared"] = True
    for row in value["annotations"]:
        row["answers"] = {
            "topical_relevance": relevance,
            "evidence_role": role,
        }
        row["rationale"] = "Independent review of the linked primary arXiv record."
        row["sources"] = ["https://arxiv.org/abs/2209.01275"]
    return value


def test_packet_is_deterministic_complete_and_blinded():
    first = build_packet()
    second = build_packet()
    assert first == second
    packet, key, template = first
    assert len(packet["items"]) == 99
    assert packet["selection_summary"] == {
        "algorithm": "all-frozen-expansion-only-samples-v1",
        "assignments": 99,
        "unique_documents": 99,
        "signals": 23,
        "all_stored_samples_included": True,
        "uniform_over_documents": False,
        "micro_precision_supported": False,
    }
    assert packet["gold_standard"] is False
    assert packet["calibration_allowed"] is False
    assert packet["production_change_allowed"] is False
    serialized = str(packet["items"])
    for hidden in (
        "source_number", "exact_matches", "expanded_matches",
        "expansion_only_matches", "newly_observed_by_expansion",
    ):
        assert hidden not in serialized
    assert all(item["target_topic"] and item["document"]["url"]
               for item in packet["items"])
    assert len(key["mapping"]) == len(template["annotations"]) == 99
    assert key["contains_expected_labels"] is False


def test_policy_prevents_micro_precision_and_production_use():
    policy = load_policy()
    assert policy["selection"]["sample_supports_micro_precision"] is False
    assert policy["comparison"]["precision_requires_adjudicated_binary_labels"] is True
    assert policy["interpretation"]["production_change_allowed"] is False


def test_submission_validation_requires_complete_independent_answers():
    packet, _, template = build_packet()
    valid = validate_submission(packet, completed(template, "Reviewer A"))
    assert valid["complete"] is True
    assert valid["precision_available"] is False
    partial = completed(template, "Reviewer A")
    partial["annotations"].pop()
    checked = validate_submission(packet, partial)
    assert checked["complete"] is False and len(checked["missing_item_ids"]) == 1
    invalid = completed(template, "Reviewer A")
    invalid["annotations"][0]["answers"]["topical_relevance"] = "yes"
    with pytest.raises(ValueError, match="Недопустимый"):
        validate_submission(packet, invalid)


def test_two_reviews_yield_agreement_not_precision_or_consensus():
    packet, _, template = build_packet()
    left = completed(template, "Reviewer A")
    right = completed(template, "Reviewer B")
    right["annotations"][0]["answers"]["topical_relevance"] = "not_relevant"
    result = compare_submissions(packet, left, right)
    assert result["axes"]["topical_relevance"]["raw_agreement"] == 98 / 99
    assert len(result["disagreements"]) == 1
    assert result["consensus_created"] is False
    assert result["precision_available"] is False
    assert result["production_change_allowed"] is False
    with pytest.raises(ValueError, match="два разных"):
        compare_submissions(packet, left, completed(template, "reviewer a"))
