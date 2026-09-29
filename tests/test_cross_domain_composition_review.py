"""Developer diagnostics must retain exact evidence and a blind holdout."""

import json

import pytest

from saia.cross_domain_composition_review import validate
from scripts.probe_cross_domain_shared_line_llm import PACKET, REVIEW, parse_response


def test_review_quotes_and_holdout_are_valid() -> None:
    summary = validate(PACKET, REVIEW)
    assert summary["reviewed_cases"] == 12
    assert summary["unreviewed_holdout_cases"] == 20
    assert summary["one_narrow_line"] == {"no": 11, "yes": 1}
    assert summary["baseline_scope_warning_comparison"]["negative_pair_cases_with_scope_warning"] == 7


def test_ungrounded_developer_quote_is_rejected(tmp_path) -> None:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    review["labels"][0]["evidence_quote_a"] = "unsupported invented phrase"
    changed = tmp_path / "changed-review.json"
    changed.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="Quote does not occur"):
        validate(PACKET, changed)


def test_holdout_label_cannot_enter_development_review(tmp_path) -> None:
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    review["labels"].append({"case_id": next(case["case_id"] for case in packet["cases"]
                                            if case["partition"] == "holdout")})
    changed = tmp_path / "leaked-review.json"
    changed.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="only the frozen development"):
        validate(PACKET, changed)


def test_model_paraphrase_is_not_accepted_as_quote() -> None:
    case = {"paper_a": {"abstract": "We built a new optical tactile sensor."},
            "paper_b": {"abstract": "We tested an event-based camera for touch."}}
    response = json.dumps({"one_narrow_line": False, "line_name": "",
                           "quote_a": "a novel optical tactile sensor",
                           "quote_b": "an event-based camera for touch",
                           "reason": "Different technical approaches."})
    with pytest.raises(ValueError, match="Ungrounded quote a"):
        parse_response(response, case)
