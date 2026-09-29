from datetime import date

import pytest

from saia.candidates import composition_sha256
from scripts.build_scout_composition_review import assemble_packet


def _queue():
    return {
        "mission_id": "sample", "score_run_id": 7,
        "queue": [
            {"rank": 1, "candidate_id": 3, "topic_id": 10,
             "composition_sha256": composition_sha256([1, 2]),
             "card": {"label": "A", "status": "watch"},
             "screening": {"state": "insufficient_data"}},
            {"rank": 2, "candidate_id": 4, "topic_id": 11,
             "composition_sha256": composition_sha256([2]),
             "card": {"label": "B", "status": "candidate"},
             "screening": {"state": "mixed_evidence"}},
        ],
    }


def test_shared_work_identifiers_are_kept_on_both_cards():
    works = [
        (10, 1, "First", "abstract", date(2024, 1, 1), "article", "en"),
        (10, 2, "Shared", None, date(2024, 2, 1), "preprint", "en"),
        (11, 2, "Shared", None, date(2024, 2, 1), "preprint", "en"),
    ]
    packet = assemble_packet(_queue(), works, [(2, "arxiv", "2402.00001")])
    assert packet["total_memberships"] == 3
    assert packet["cards"][0]["works"][1]["identifiers"] == [
        {"kind": "arxiv", "value": "2402.00001"}]
    assert packet["cards"][1]["works"][0]["identifiers"] == [
        {"kind": "arxiv", "value": "2402.00001"}]


def test_changed_composition_fails_closed():
    works = [
        (10, 1, "First", None, date(2024, 1, 1), "article", "en"),
        (11, 2, "Shared", None, date(2024, 2, 1), "preprint", "en"),
    ]
    with pytest.raises(ValueError, match="Composition changed"):
        assemble_packet(_queue(), works, [])


def test_repeated_membership_fails_closed():
    works = [(10, 1, "First", None, None, None, None)] * 2
    with pytest.raises(ValueError, match="repeated"):
        assemble_packet(_queue(), works, [])
