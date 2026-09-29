import copy

import pytest

from saia.hybrid import digest
from saia.primary_calibration import evidence_fragments, validate_and_enrich


def queue():
    value = {
        "evidence_report_payload_sha256": "evidence",
        "annotation_sha256": "annotation",
        "recheck_overlay_sha256": "recheck",
        "items": [
            {"candidate_id": 1, "work_id": 11, "title": "A method",
             "abstract": "We propose a method. Experiments demonstrate gains.",
             "primary_result_label": None},
            {"candidate_id": 1, "work_id": 12, "title": "A framework",
             "abstract": "This paper introduces a framework. We argue it is useful.",
             "primary_result_label": None},
        ],
    }
    return value


def decisions(source):
    return {
        "not_gold_standard": True,
        "base_queue_sha256": digest(source),
        "labels": [
            {"work_id": 11, "label": "primary_original_research",
             "reason_code": "original_method_with_evaluation"},
            {"work_id": 12, "label": "non_research_position_or_framework",
             "reason_code": "conceptual_framework_without_reported_evaluation"},
        ],
        "limitations": ["test"],
    }


def test_primary_calibration_is_evidence_bound_and_never_production_rule():
    source = queue()
    report = validate_and_enrich(source, decisions(source), ["survey", "review"])
    assert report["counts"]["items"] == 2
    assert report["counts"]["labels"] == {
        "non_research_position_or_framework": 1,
        "primary_original_research": 1,
    }
    assert report["title_proxy_comparison"]["false_positive"] == 1
    assert report["title_proxy_comparison"]["production_rule_allowed"] is False
    assert report["decision"]["g6_production_rule"] is None
    assert report["decision"]["holdout_evaluated"] is False
    claimed = report.pop("report_payload_sha256")
    assert digest(report) == claimed


def test_evidence_fragments_are_exact_sentences():
    abstract = "Background only. We introduce NewThing. Results demonstrate gains."
    fragments, fallback = evidence_fragments(abstract)
    assert fragments == ["We introduce NewThing.", "Results demonstrate gains."]
    assert fallback is False
    assert all(fragment in abstract for fragment in fragments)


def test_primary_calibration_rejects_wrong_binding_or_missing_item():
    source = queue()
    wrong = decisions(source)
    wrong["base_queue_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="другой очереди"):
        validate_and_enrich(source, wrong, ["review"])
    missing = decisions(source)
    missing["labels"].pop()
    with pytest.raises(ValueError, match="ровно один раз"):
        validate_and_enrich(source, missing, ["review"])


def test_primary_calibration_rejects_unbound_evidence():
    source = queue()
    changed = copy.deepcopy(source)
    changed["items"][0]["abstract"] = ""
    changed_decisions = decisions(changed)
    with pytest.raises(ValueError, match="непустой abstract"):
        validate_and_enrich(changed, changed_decisions, ["review"])
