import copy

import pytest

from saia.triage import build_queue


def card(identifier, status="candidate", failed=(), unknown=(), passed=(), novelty=80,
         momentum=70, confidence=50, slope=0.01, comparable=True):
    gates = ([{"gate": name, "severity": "block", "passed": False} for name in failed]
             + [{"gate": name, "severity": "block", "passed": None} for name in unknown]
             + [{"gate": name, "severity": "block", "passed": True} for name in passed])
    return {
        "candidate_id": identifier,
        "label": identifier,
        "status": status,
        "gates": gates,
        "evidence_confidence": confidence,
        "metrics": {
            "observed": {
                "novelty_percentile": novelty,
                "momentum_percentile": momentum,
            },
            "publication_series": {
                "share_slope_per_window": slope,
                "coverage_comparable": comparable,
            },
        },
    }


def test_queue_is_review_order_not_a_hidden_weighted_score():
    packet = {"mission_id": "m", "score_run_id": 7, "cards": [
        card("many-failures", failed=("G2_novelty", "G3_persistence"),
             passed=("G4_momentum",)),
        card("one-failure", failed=("G3_persistence",),
             passed=("G2_novelty", "G4_momentum", "G4_positive_share_slope",
                     "G_coherence")),
        card("unknown-only", status="watch", unknown=("G0_independent_orgs",),
             passed=("G2_novelty", "G4_momentum")),
    ]}
    before = copy.deepcopy(packet)
    result = build_queue(packet)
    assert packet == before
    assert [item["candidate_id"] for item in result["queue"]] == [
        "unknown-only", "one-failure", "many-failures"
    ]
    assert result["ranking_policy"]["kind"].endswith("not_prospectivity_score")
    assert "автоматически выявленных кандидатов" in result["interpretation"]
    assert result["queue"][0]["card"]["expert_validation"] == {
        "status": "not_requested", "required_for_display": False,
    }


def test_queue_excludes_widespread_but_reports_it():
    result = build_queue({"cards": [
        card("review"), card("already-mass", status="widespread")
    ]})
    assert [item["candidate_id"] for item in result["queue"]] == ["review"]
    assert result["counts"]["excluded_widespread_or_mature"] == 1
    assert result["counts"]["statuses"]["widespread"] == 1


def test_joint_percentile_uses_conservative_minimum_not_sum():
    result = build_queue({"cards": [
        card("balanced", failed=("G3_persistence",), novelty=75, momentum=75),
        card("one-sided", failed=("G3_persistence",), novelty=99, momentum=20),
    ]})
    assert [item["candidate_id"] for item in result["queue"]] == ["balanced", "one-sided"]
    assert result["queue"][0]["joint_novelty_momentum_floor"] == 75


def test_declining_share_is_not_presented_as_observed_growth():
    result = build_queue({"cards": [
        card("declining", failed=("G4_positive_share_slope",), slope=-0.01),
        card("mixed", failed=("G2_novelty",), slope=0.01),
        card("growing", status="watch", unknown=("G0_independent_orgs",), slope=0.02),
    ]})
    assert [item["candidate_id"] for item in result["queue"]] == [
        "growing", "mixed", "declining"
    ]
    assert [item["screening"]["state"] for item in result["queue"]] == [
        "growth_observed", "mixed_evidence", "growth_not_confirmed"
    ]
    assert "не должна трактоваться как растущий слабый сигнал" in result["queue"][2]["why_in_queue"]
    assert result["queue"][2]["screening"]["failed_reasons"] == ["рост доли публикаций"]
    assert result["counts"]["shown"] == 3


def test_unknown_comparability_does_not_become_positive_growth():
    result = build_queue({"cards": [card("unknown", comparable=False)]})
    assert result["queue"][0]["screening"]["state"] == "insufficient_data"
    assert result["queue"][0]["screening"]["publication_growth_comparable"] is False


def test_known_failed_novelty_is_not_ranked_ahead_of_unknown_evidence():
    packet = {"cards": [
        card("mixed", failed=("G2_novelty",), comparable=False, confidence=90),
        card("unknown", unknown=("G_coverage",), comparable=False, confidence=20),
    ]}
    result = build_queue(packet)
    assert [item["candidate_id"] for item in result["queue"]] == ["unknown", "mixed"]
    assert [item["screening"]["state"] for item in result["queue"]] == [
        "insufficient_data", "mixed_evidence",
    ]
    assert result["counts"]["shown"] == 2


def test_broad_topic_name_is_disclosed_without_claiming_articles_are_irrelevant():
    broad = card("broad", comparable=False)
    broad["metrics"]["observed"]["paper_title_diagnostics"] = {
        "total_works": 8,
        "title_matches": {"label_too_broad_for_title_check": 8},
    }
    result = build_queue({"cards": [broad]})
    screening = result["queue"][0]["screening"]
    assert "нельзя проверить" in screening["explanation"]
    assert screening["scope_warning"] is not None
    assert screening["state"] == "insufficient_data"


def test_positive_share_of_broad_cluster_is_not_ranked_as_weak_signal_growth():
    broad = card("broad-growing", slope=0.03)
    broad["metrics"]["observed"]["paper_title_diagnostics"] = {
        "total_works": 8,
        "title_matches": {"label_too_broad_for_title_check": 8},
    }
    narrow = card("narrow-growing", slope=0.01)
    result = build_queue({"cards": [broad, narrow]})
    assert [row["candidate_id"] for row in result["queue"]] == [
        "narrow-growing", "broad-growing",
    ]
    screening = result["queue"][1]["screening"]
    assert screening["state"] == "insufficient_data"
    assert screening["publication_growth_comparable"] is True
    assert "нельзя выдавать за рост слабого сигнала" in screening["explanation"]


def test_majority_acceptance_titles_get_role_warning_without_unproved_ranking_penalty():
    contextual = card("acceptance-heavy", slope=0.03)
    contextual["metrics"]["observed"]["paper_title_diagnostics"] = {
        "total_works": 5,
        "context_hints": {"acceptance_or_market_context": 3, "unclassified": 2},
    }
    result = build_queue({"cards": [contextual]})
    screening = result["queue"][0]["screening"]
    assert screening["state"] == "growth_observed"
    assert screening["context_warning"] is not None
    assert "их роль как технических исследований не установлена" in screening["explanation"]


def test_single_acceptance_title_does_not_override_observed_growth():
    mixed = card("mixed-line", slope=0.02)
    mixed["metrics"]["observed"]["paper_title_diagnostics"] = {
        "total_works": 5,
        "context_hints": {"acceptance_or_market_context": 1, "unclassified": 4},
    }
    result = build_queue({"cards": [mixed]})
    assert result["queue"][0]["screening"]["state"] == "growth_observed"


def test_uncalibrated_coherence_does_not_boost_ranking():
    packet = {"cards": [
        card("uncalibrated", comparable=False, passed=("G_coherence",),
             unknown=("G_coherence_calibration",), confidence=80),
        card("other", comparable=False, passed=("G2_novelty",),
             unknown=("G_coherence_calibration",), confidence=20),
    ]}
    result = build_queue(packet)
    assert [item["candidate_id"] for item in result["queue"]] == ["other", "uncalibrated"]
    assert result["queue"][1]["passed_core_checks"] == 0


def test_calibrated_coherence_can_count_as_core_pass():
    result = build_queue({"cards": [
        card("calibrated", comparable=False,
             passed=("G_coherence", "G_coherence_calibration"))
    ]})
    assert result["queue"][0]["passed_core_checks"] == 1


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_limit_is_bounded_and_not_coerced(limit):
    with pytest.raises(ValueError):
        build_queue({"cards": []}, limit)


def test_duplicate_candidate_id_is_rejected():
    with pytest.raises(ValueError, match="уникальны"):
        build_queue({"cards": [card("same"), card("same")]})
