import pytest

from saia.card_subline_proposals import propose
from scripts.propose_card_sublines import run


def _work(identifier, title):
    return {"work_id": identifier, "title": title,
            "identifiers": [{"kind": "arxiv", "value": f"2609.{identifier:05d}"}]}


def test_narrow_title_phrase_is_a_proposal_not_a_signal():
    cards = [{"candidate_id": 1, "label": "Mixed UAV", "works": [
        _work(1, "Vision language navigation for aerial search"),
        _work(2, "Vision language navigation in urban airspace"),
        _work(3, "Vision language navigation for drones"),
        _work(4, "Fluid antennas for aerial communication"),
    ]}]
    report = propose(cards)
    assert report["cards_with_subline_proposals"] == 1
    assert report["weak_signal_accuracy_measured"] is False
    assert any(item["phrase"] == "vision language navigation"
               and item["title_anchored_work_count_in_card"] == 3
               and item["one_technical_line_verified"] is False
               and item["source_works"][0]["url"].startswith("https://arxiv.org/abs/")
               for item in report["cards"][0]["subline_proposals"])
    assert 4 in report["cards"][0]["unassigned_work_ids"]


def test_no_shared_phrase_abstains_instead_of_inventing_line():
    cards = [{"candidate_id": 1, "works": [
        _work(1, "Optical navigation for aircraft"),
        _work(2, "Fluid antennas in wireless networks"),
        _work(3, "Quantum sensors and inertial systems"),
    ]}]
    report = propose(cards)
    assert report["proposed_sublines"] == 0
    assert report["cards"][0]["unassigned_work_ids"] == [1, 2, 3]


def test_duplicate_work_with_conflicting_title_is_rejected():
    cards = [{"candidate_id": 1, "works": [_work(1, "Alpha beta"),
                                            _work(2, "Gamma delta")]},
             {"candidate_id": 2, "works": [_work(1, "Different title"),
                                            _work(3, "Other title")]}]
    with pytest.raises(ValueError, match="Conflicting"):
        propose(cards)


def test_frozen_bas_pilot_has_no_production_side_effect():
    report = run(__import__("pathlib").Path("config/card-sublines-bas-pilot-v1.json"))
    assert report["cards_processed"] == 15
    assert report["production_changed"] is False
    assert report["source_sha256"] == "5eadfb9154deac9d69dedbafa6ff29540bb885e9e045caf551b51807201d6fe3"
    assert all(work["url"].startswith("https://arxiv.org/abs/")
               for card in report["cards"] for line in card["subline_proposals"]
               for work in line["source_works"])
