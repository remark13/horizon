import copy
from collections import Counter
from pathlib import Path

import pytest

from saia.candidates import composition_sha256, primary_source_observation
from saia.composition_calibration import (
    apply_recheck,
    build_primary_annotation_queue,
    build_report,
    read_json,
    validate_annotations,
)
from saia.hybrid import digest


ROOT = Path(__file__).resolve().parents[1]
ANNOTATIONS = ROOT / "evaluation/current-triage-composition.v0.4.10.json"
EVIDENCE = ROOT / (
    "reports/generated/"
    "ai-data-selection-current-p1-v049-enriched-triage-evidence-r1-2026-09-20.json"
)
CARDS = ROOT / (
    "reports/generated/"
    "ai-data-selection-current-p1-v049-enriched-global-cards-r2-2026-09-20.json"
)
RECHECK = ROOT / "evaluation/current-triage-composition-recheck.v0.4.11.json"


def artifacts():
    return read_json(ANNOTATIONS), read_json(EVIDENCE), read_json(CARDS)


def test_preliminary_labels_bind_every_case_and_preserve_internal_holdout():
    annotations, evidence, _ = artifacts()
    result = validate_annotations(annotations, evidence)
    assert result["candidate_count"] == 15
    assert result["development_count"] == 10
    assert result["internal_holdout_count"] == 5
    assert sum(result["partition_target_counts"].values()) == 15


def test_coherence_calibration_is_refused_when_development_labels_overlap():
    annotations, evidence, cards = artifacts()
    queue = build_primary_annotation_queue(annotations, evidence)
    report = build_report(annotations, evidence, cards, queue)
    diagnostic = report["coherence_diagnostic"]
    assert diagnostic["decision"] == "refused_non_separable_development_labels"
    assert diagnostic["production_threshold"] is None
    assert diagnostic["holdout_evaluation"]["status"] == "not_run_without_development_threshold"
    assert report["detector_or_saved_score_modified"] is False
    expected = report.pop("report_payload_sha256")
    assert digest(report) == expected


def test_primary_queue_is_bounded_unlabelled_and_development_only():
    annotations, evidence, _ = artifacts()
    queue = build_primary_annotation_queue(annotations, evidence)
    development_ids = {
        case["candidate_id"] for case in annotations["cases"]
        if case["partition"] == "development"
    }
    assert queue["item_count"] == 30
    assert queue["labels_completed"] == 0
    assert {item["candidate_id"] for item in queue["items"]} == development_ids
    assert Counter(item["candidate_id"] for item in queue["items"]) == {
        candidate_id: 3 for candidate_id in development_ids
    }
    assert all(item["primary_result_label"] is None for item in queue["items"])


@pytest.mark.parametrize("mutation", ["partition", "foreign_outlier", "gold"])
def test_annotation_binding_and_preliminary_status_cannot_be_silently_changed(mutation):
    annotations, evidence, _ = artifacts()
    changed = copy.deepcopy(annotations)
    if mutation == "partition":
        changed["cases"][0]["partition"] = "internal_holdout"
    elif mutation == "foreign_outlier":
        changed["cases"][0]["outlier_work_ids"].append(999999999)
    else:
        changed["not_gold_standard"] = False
    with pytest.raises(ValueError):
        validate_annotations(changed, evidence)


def test_title_proxy_is_never_counted_as_verified_primary_research():
    observation = primary_source_observation(
        ["A new method", "Systematic Review of methods"],
        ["survey", "systematic review", "literature review"],
    )
    assert observation == {
        "verified_primary_sources": None,
        "non_review_title_candidates": 1,
        "proxy_is_scientific_evidence": False,
    }


def test_composition_identity_is_order_independent_and_rejects_bad_membership():
    assert composition_sha256([3, 1, 2]) == composition_sha256([2, 3, 1])
    with pytest.raises(ValueError):
        composition_sha256([])
    with pytest.raises(ValueError):
        composition_sha256([1, 1])


def test_recheck_overlay_corrects_only_named_compositions_and_preserves_split():
    annotations, evidence, _ = artifacts()
    recheck = read_json(RECHECK)
    merged = apply_recheck(annotations, recheck, evidence)
    original = {case["composition_sha256"]: case for case in annotations["cases"]}
    updated = {case["composition_sha256"]: case for case in merged["cases"]}
    changed = {change["composition_sha256"] for change in recheck["changes"]}
    assert {key for key in updated if updated[key] != original[key]} == changed
    assert [(c["composition_sha256"], c["partition"]) for c in merged["cases"]] == [
        (c["composition_sha256"], c["partition"]) for c in annotations["cases"]
    ]
    assert merged["not_gold_standard"] is True
    assert merged["recheck_overlay_sha256"] == digest(recheck)
    queue = build_primary_annotation_queue(merged, evidence)
    assert queue["version"] == "primary-result-annotation-queue-0.4.11"
    assert queue["annotation_version"] == merged["version"]
    assert queue["annotation_sha256"] == digest(merged)
    assert queue["recheck_overlay_sha256"] == digest(recheck)


def test_recheck_rejects_wrong_base_or_forbidden_partition_change():
    annotations, evidence, _ = artifacts()
    wrong_base = copy.deepcopy(read_json(RECHECK))
    wrong_base["base_annotation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="другой базовой"):
        apply_recheck(annotations, wrong_base, evidence)
    forbidden = copy.deepcopy(read_json(RECHECK))
    forbidden["changes"][0]["set"]["partition"] = "internal_holdout"
    with pytest.raises(ValueError, match="запрещённые"):
        apply_recheck(annotations, forbidden, evidence)
