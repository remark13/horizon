import pytest

from scripts.probe_free_ru_bge_cross_domain import evaluate_orders


def test_cross_domain_order_checks_require_same_topic():
    rows = [{"item_id": "a", "target_topic": "rare earth extraction", "score": 2.0},
            {"item_id": "b", "target_topic": "rare earth extraction", "score": 1.0}]
    checks = [{"higher": "a", "lower": "b", "reason": "fixed"}]
    assert evaluate_orders(rows, checks)[0]["passed"]
    rows[1]["target_topic"] = "battery anode"
    with pytest.raises(ValueError, match="same original topic"):
        evaluate_orders(rows, checks)
