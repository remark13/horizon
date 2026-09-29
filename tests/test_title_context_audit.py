import json

from saia.title_context_audit import audit_packet


def test_audit_is_title_only_and_counts_memberships(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps({
        "version": "synthetic-packet-v1",
        "items": [{
            "item_id": "one", "direction": "food", "generated_label": "Precision fermentation",
            "works": [
                {"title": "Consumer acceptance of precision fermentation cheese"},
                {"title": "Precision fermentation of whey protein in yeast"},
                {"title": "Consumer valuation of precision fermentation milk"},
            ],
        }],
    }), encoding="utf-8")
    result = audit_packet(packet)
    assert result["counts"] == {
        "cards": 1, "work_memberships": 3,
        "flagged_title_memberships": 2, "cards_with_majority_flagged": 1,
    }
    assert result["cards"][0]["flagged_title_positions"] == [1, 3]
    assert "not a holdout" in result["limitations"][0]
