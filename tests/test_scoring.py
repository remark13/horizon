from saia import methodology
from saia.scoring import CandidateMetrics, assess_candidate
import pytest


def complete_metrics(**overrides):
    values = dict(
        doc_count=12,
        independent_orgs=5,
        independent_teams=4,
        single_org_share=0.30,
        maturity_percentile=25,
        novelty_percentile=85,
        windows_present=4,
        momentum_percentile=82,
        primary_sources=4,
        age_years=3,
        share_slope=0.01,
        share_change=0.03,
        consecutive_active_windows=4,
        coverage_comparable=True,
        coherence_calibrated=True,
        embedding_coverage=1.0,
        normalized={
            "novelty": 0.85,
            "momentum": 0.82,
            "persistence": 0.75,
            "independent_diffusion": 0.80,
            "bridge": 0.55,
            "citation_velocity": 0.40,
            "coherence": 0.78,
        },
        confidence_components={
            "data_completeness": 0.90,
            "source_quality": 0.80,
            "confirmation_independence": 0.75,
        },
    )
    values.update(overrides)
    return CandidateMetrics(**values)


@pytest.mark.parametrize('share,rank,passed', [
    (0.0002, 99., True), (0.02, 99., False), (0.01, 99., False),
    (None, 99., None), (0.02, None, None), (0.0002, None, True),
    (None, 25., True), (0.02, 25., True), (None, None, None)])
def test_widespread_requires_high_rank_and_absolute_share_when_requested(share, rank, passed):
    assessed = assess_candidate(complete_metrics(publication_share=share, maturity_percentile=rank),
                                methodology.load_default(), prevalence_share_floor=.01)
    gate = next(g for g in assessed.gates if g.gate == 'G1_publication_prevalence')
    assert gate.passed is passed
    assert (assessed.status == 'widespread') is (passed is False)


def test_legacy_scoring_without_absolute_floor_is_unchanged():
    assessed = assess_candidate(complete_metrics(publication_share=.0002, maturity_percentile=99), methodology.load_default())
    assert assessed.status == 'widespread'


def test_unknown_primary_content_cannot_form_even_if_everything_else_passes():
    assessed = assess_candidate(complete_metrics(primary_sources=None), methodology.load_default())
    assert assessed.status == 'watch' and assessed.emergence_score is None
    assert next(g for g in assessed.gates if g.gate == 'G6_primary_sources').passed is None


@pytest.mark.parametrize('floor', [0, -1, 2, float('nan'), float('inf')])
def test_invalid_absolute_floor_is_rejected(floor):
    with pytest.raises(ValueError):
        assess_candidate(complete_metrics(publication_share=.1), methodology.load_default(), floor)


def test_forming_candidate_gets_two_separate_scores():
    assessment = assess_candidate(complete_metrics(), methodology.load_default())
    assert assessment.status == "forming"
    assert 0 < assessment.emergence_score <= 100
    assert 0 < assessment.evidence_confidence <= 100
    assert assessment.emergence_score != assessment.evidence_confidence


def test_incomplete_embedding_coverage_blocks_confirmation():
    assessment = assess_candidate(complete_metrics(embedding_coverage=0.5), methodology.load_default())
    assert assessment.status != 'forming'
    assert any(g.gate == 'G_embedding_coverage' and g.passed is False for g in assessment.gates)


def test_unknown_persistence_is_not_measured_zero():
    normalized = dict(complete_metrics().normalized, persistence=None)
    assessment = assess_candidate(complete_metrics(windows_present=None,
                                  consecutive_active_windows=None,
                                  coverage_comparable=None, normalized=normalized),
                                  methodology.load_default())
    assert assessment.status != 'forming'
    assert assessment.emergence_score is None
    for name in ('G3_persistence', 'G3_consecutive'):
        gate = next(g for g in assessment.gates if g.gate == name)
        assert gate.passed is None
        assert gate.observed is None


def test_mature_topic_is_excluded_from_ranking():
    assessment = assess_candidate(
        complete_metrics(maturity_percentile=95), methodology.load_default()
    )
    assert assessment.status == "widespread"
    assert assessment.emergence_score is None
    assert any(gate.gate == "G1_publication_prevalence" and gate.passed is False for gate in assessment.gates)


def test_missing_measurement_is_not_zero_and_moves_to_watch():
    assessment = assess_candidate(
        complete_metrics(momentum_percentile=None), methodology.load_default()
    )
    assert assessment.status == "watch"
    gate = next(g for g in assessment.gates if g.gate == "G4_momentum")
    assert gate.passed is None
    assert gate.observed is None


def test_missing_weighted_metric_prevents_score():
    normalized = dict(complete_metrics().normalized)
    normalized["novelty"] = None
    assessment = assess_candidate(
        complete_metrics(normalized=normalized), methodology.load_default()
    )
    assert assessment.status == "watch"
    assert assessment.emergence_score is None
    assert assessment.missing_metrics == ("novelty",)


def test_missing_citations_do_not_block_base_score():
    normalized = dict(complete_metrics().normalized)
    normalized['citation_velocity'] = None
    assert assess_candidate(complete_metrics(normalized=normalized), methodology.load_default()).emergence_score is not None


def test_top_percentile_of_negative_growth_does_not_form_signal():
    result = assess_candidate(complete_metrics(momentum_percentile=100, share_slope=-.01,
                                              share_change=-.03), methodology.load_default())
    assert result.status != 'forming'
    assert any(g.gate == 'G4_positive_share_slope' and g.passed is False for g in result.gates)


def test_low_coherence_blocks_even_with_other_good_features():
    normalized = dict(complete_metrics().normalized)
    normalized['coherence'] = .05
    assert assess_candidate(complete_metrics(normalized=normalized), methodology.load_default()).status != 'forming'


def test_zero_confidence_moves_to_watch():
    result = assess_candidate(complete_metrics(confidence_components={}), methodology.load_default())
    assert result.status == 'watch'
    assert result.evidence_confidence == 0


def test_non_consecutive_presence_blocks_forming():
    result = assess_candidate(complete_metrics(windows_present=4, consecutive_active_windows=1),
                              methodology.load_default())
    assert result.status != 'forming'


def test_uncalibrated_model_is_explicitly_unknown():
    result = assess_candidate(complete_metrics(coherence_calibrated=None), methodology.load_default())
    assert result.status == 'watch'
    assert any(g.gate == 'G_coherence_calibration' and g.passed is None for g in result.gates)


def test_missing_confidence_component_reduces_confidence():
    complete = assess_candidate(complete_metrics(), methodology.load_default())
    incomplete = assess_candidate(
        complete_metrics(confidence_components={
            "data_completeness": 0.90,
            "source_quality": None,
            "confirmation_independence": 0.75,
        }),
        methodology.load_default(),
    )
    assert incomplete.evidence_confidence < complete.evidence_confidence
