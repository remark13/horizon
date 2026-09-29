import json

import pytest

from scripts.probe_hypothesis_type_model import _parse


def test_hypothesis_type_draft_rejects_unknown_or_repeated_types():
    proposal = {"primary_type": "product_market",
                "secondary_types": ["technical_application"],
                "reason_ru": "Название задаёт продуктовый рынок.",
                "needs_review": True}
    assert _parse({"response": json.dumps(proposal)}) == proposal
    proposal["secondary_types"] = ["product_market"]
    with pytest.raises(ValueError, match="Invalid hypothesis"):
        _parse({"response": json.dumps(proposal)})


def test_hypothesis_type_accepts_explanatory_rationale_within_safe_limit():
    proposal = {"primary_type": "technical_application",
                "secondary_types": [],
                "reason_ru": "Техническое применение. " * 30,
                "needs_review": False}
    assert len(proposal["reason_ru"]) > 500
    assert _parse({"response": json.dumps(proposal)}) == proposal
