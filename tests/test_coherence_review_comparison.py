import json

import pytest

from saia.coherence_review import read_packet, validate_submission
from saia.coherence_review_comparison import compare_exports


def export(tmp_path, reviewer, *, disagreement=False):
    """Synthetic parser fixture, not a submitted expert opinion."""
    packet = read_packet()
    submission = {"packet_sha256": packet["packet_sha256"], "reviewer_id": reviewer,
                  "independent_review_declared": True,
                  "answers": [{"case_id": case["case_id"],
                               "same_research_problem": "unclear",
                               "same_technical_mechanism": "unclear",
                               "one_signal_line": "unclear",
                               "evidence_quote_a": case["paper_a"]["title"][:24],
                               "evidence_quote_b": case["paper_b"]["title"][:24],
                               "rationale": "Тест формата: это синтетический ответ, не экспертная оценка."}
                              for case in packet["cases"]]}
    if disagreement:
        submission["answers"][0]["one_signal_line"] = "no"
    value = validate_submission(submission)
    path = tmp_path / (reviewer + ".json")
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_pair_exports_use_correct_three_question_schema(tmp_path):
    left, right = export(tmp_path, "test-left"), export(tmp_path, "test-right", disagreement=True)
    result = compare_exports(left, right)
    assert result["items"] == 30
    metrics = result["agreement_by_question"]
    assert metrics["one_signal_line"]["exact_agreement"] == pytest.approx(29 / 30)
    assert metrics["one_signal_line"]["cohen_kappa"] == 0
    assert metrics["same_technical_mechanism"]["cohen_kappa"] is None
    assert metrics["same_technical_mechanism"]["kappa_undefined_for_single_category"] is True
    assert len(result["disagreements_for_adjudication"]) == 1
    assert result["automatic_consensus"] is None
    assert result["weak_signal_precision"] is None
    assert result["production_changed"] is False
    assert result["problem_vs_line_answer_differences"]["test-right"] == 1


def test_same_file_and_same_normalized_reviewer_are_rejected(tmp_path):
    left = export(tmp_path, "test-reviewer")
    with pytest.raises(ValueError, match="distinct review files"):
        compare_exports(left, left)
    right = export(tmp_path, "TEST-REVIEWER")
    with pytest.raises(ValueError, match="distinct reviewer identifiers"):
        compare_exports(left, right)


def test_tampered_answer_is_rejected_before_comparison(tmp_path):
    left, right = export(tmp_path, "test-left"), export(tmp_path, "test-right")
    payload = json.loads(right.read_text())
    payload["answers"][0]["one_signal_line"] = "yes"
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        compare_exports(left, right)
