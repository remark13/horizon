import json

import pytest

from saia.historical_novelty_sensitivity import (
    stable_hash, summarise, validated_score_values,
)
from saia.package_observations import payload_hash


def test_summary_reports_parameter_stability():
    variants = [
        {"topics": {
            "1": {"novelty_percentile": 90.0, "novelty_raw": .2,
                  "nearest_background_topic": 2},
            "2": {"novelty_percentile": 10.0, "novelty_raw": .1,
                  "nearest_background_topic": 1},
        }},
        {"topics": {
            "1": {"novelty_percentile": 70.0, "novelty_raw": .18,
                  "nearest_background_topic": 3},
            "2": {"novelty_percentile": 30.0, "novelty_raw": .12,
                  "nearest_background_topic": 1},
        }},
    ]
    result = summarise(variants)
    assert result[0]["topic_id"] == 1
    assert result[0]["percentile_median"] == 80.0
    assert result[0]["top_rank_count"] == 2
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})


def test_validated_score_values_keep_only_sensitivity_stable_percentiles(tmp_path):
    diagnostic = {
        "status": "diagnostic_not_applied_to_score", "cluster_run_id": 7,
        "provenance": {"embedding_model": "model", "current_membership_subset_of_target_pack": True,
                       "background_manifest_payload_sha256": "background"},
        "interpretation": {"controlled_historical_relevance_predicate_reapplied": True},
        "topics": [
            {"topic_id": 1, "novelty_raw": .2, "novelty_percentile": 90,
             "nearest_background_topic": 3, "nearest_background_similarity": .8,
             "nearest_background_terms": ["a"], "historical_anchor_topics": 4},
            {"topic_id": 2, "novelty_raw": .1, "novelty_percentile": 50,
             "nearest_background_topic": 2, "nearest_background_similarity": .9,
             "nearest_background_terms": ["b"], "historical_anchor_topics": 4},
        ],
    }
    diagnostic["payload_sha256"] = payload_hash(diagnostic)
    sensitivity = {
        "status": "sensitivity_diagnostic_not_applied_to_score", "cluster_run_id": 7,
        "input": {"diagnostic_payload_sha256": diagnostic["payload_sha256"],
                  "variants": 9},
        "summary": [
            {"topic_id": 1, "percentile_min": 90, "percentile_max": 90},
            {"topic_id": 2, "percentile_min": 30, "percentile_max": 50},
        ],
    }
    sensitivity["payload_sha256"] = payload_hash(sensitivity)
    dp, sp = tmp_path / "d.json", tmp_path / "s.json"
    dp.write_text(json.dumps(diagnostic)); sp.write_text(json.dumps(sensitivity))
    values, provenance = validated_score_values(
        dp, sp, cluster_run_id=7, model="model", topic_ids={1, 2}
    )
    assert values[1]["novelty_percentile"] == 90
    assert values[2]["novelty_percentile"] is None
    assert "sensitivity_unresolved" in values[2]["novelty_availability"]
    assert provenance["diagnostic_payload_sha256"] == diagnostic["payload_sha256"]


def test_validated_score_values_refuse_changed_topic_set(tmp_path):
    diagnostic = {"status": "diagnostic_not_applied_to_score", "cluster_run_id": 7,
                  "provenance": {"embedding_model": "model", "current_membership_subset_of_target_pack": True,
                                 "background_manifest_payload_sha256": "background"},
                  "interpretation": {"controlled_historical_relevance_predicate_reapplied": True},
                  "topics": []}
    diagnostic["payload_sha256"] = payload_hash(diagnostic)
    sensitivity = {"status": "sensitivity_diagnostic_not_applied_to_score", "cluster_run_id": 7,
                   "input": {"diagnostic_payload_sha256": diagnostic["payload_sha256"], "variants": 1},
                   "summary": []}
    sensitivity["payload_sha256"] = payload_hash(sensitivity)
    dp, sp = tmp_path / "d.json", tmp_path / "s.json"
    dp.write_text(json.dumps(diagnostic)); sp.write_text(json.dumps(sensitivity))
    with pytest.raises(ValueError, match="topic set"):
        validated_score_values(dp, sp, cluster_run_id=7, model="model", topic_ids={1})
