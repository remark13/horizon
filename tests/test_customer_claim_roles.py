import json

import pytest

from saia.customer_claim_roles import parse


SOURCE = {"rationale_original": "Компания закрыла раунд на $5 млн.",
          "stage_original": "Раннее внедрение",
          "mention_trend_original": "Платформа запущена в 2026 году."}


def test_exact_customer_quote_is_trace_not_verification():
    raw = {"response": json.dumps({"claims": [{
        "kind": "investment_deal", "source_field": "rationale_original",
        "quote": "закрыла раунд на $5 млн"}], "needs_review": False})}
    assert parse(raw, SOURCE)["claims"][0]["kind"] == "investment_deal"


def test_paraphrased_or_wrong_field_quote_is_rejected():
    value = {"claims": [{"kind": "investment_deal", "source_field": "stage_original",
                         "quote": "закрыла раунд на $5 млн"}], "needs_review": False}
    with pytest.raises(ValueError):
        parse({"response": json.dumps(value)}, SOURCE)
