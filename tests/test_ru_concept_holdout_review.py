import hashlib
import json
from pathlib import Path

from saia.ru_concept_revalidate import revalidate


ROOT = Path(__file__).resolve().parents[1]


def test_developer_holdout_review_is_complete_and_bound_to_frozen_proposals():
    proposal_path = ROOT / "outputs/ru-concept-holdout16-qwen35-v3-2026-09-26-v1.json"
    proposals = json.loads(proposal_path.read_text(encoding="utf-8"))
    review = json.loads((ROOT / "evaluation/ru-concept-holdout16-developer-review-v1.json")
                        .read_text(encoding="utf-8"))
    assert review["proposal_sha256"] == hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    assert review["independent_expert_review"] is False
    assert len(review["rows"]) == len(proposals["rows"]) == 16
    assert {row["case_id"] for row in review["rows"]} == {
        row["case_id"] for row in proposals["rows"]
    }
    assert {row["case_id"] for row in review["rows"]
            if row["judgement"] == "critical_loss"} == {
                "national-area-030", "customer-signal-016",
                "customer-signal-009", "customer-signal-012",
            }


def test_revalidation_does_not_change_frozen_model_text_or_invoke_model():
    source = ROOT / "outputs/ru-concept-holdout16-qwen35-v3-2026-09-26-v1.json"
    original = json.loads(source.read_text(encoding="utf-8"))
    checked = revalidate(source)
    assert checked["model_reinvoked"] is False
    assert checked["revalidated_from_report_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert [(row["case_id"], row.get("proposal_unreviewed")) for row in checked["rows"]] == [
        (row["case_id"], row.get("proposal_unreviewed")) for row in original["rows"]
    ]
    rejected = {row["case_id"]: row["structural_validation"]["issues"]
                for row in checked["rows"]}
    assert "negative_scope_requires_explicit_exclusion" in rejected["customer-signal-016"]
