from pathlib import Path

from scripts.benchmark_article_role_holdout import _inputs


ROOT = Path(__file__).resolve().parents[1]


def test_holdout_exactly_matches_first_twelve_frozen_packet_items():
    pairs = _inputs(
        ROOT / "evaluation/goal-cross-domain-relevance-v2.packet.json",
        ROOT / "config/role-verifier-holdout12-developer-labels.v1.json",
    )
    assert len(pairs) == 12
    assert pairs[0][0]["item_id"] == "review-781f8df61381f12129dbf72a"
    assert pairs[-1][0]["item_id"] == "review-0706e304ead95201ba3356de"
